"""Position.csv 导入：解析/校验/幂等入库。

编号策略（v3）：**源文件「岗位编号」列一律忽视**，由系统在导入时自动分配：
  正式岗（Employee / Consultant）→ P{seq}；外包岗（External Employee）→ PA{seq}。
幂等键 = （职位名, 隶属公司, 国家或地区）：与库内已有岗位匹配则更新，否则建档分配新号。

分三趟：
  1) 解析校验行数据，建公司/职位/国家基础字典；
  2) 按幂等键 upsert 岗位（新行自动分配编号、写生命周期事件）；
  3) 直线/虚线经理、之前的职位按**职位名**解析为外键，并做环检测。
"""
import csv
import re
from datetime import date, datetime, time

from app.helpers import next_sequence, number_series
from app.lifecycle import record_event
from app.models import (
    Company,
    Country,
    LegalCategory,
    LegalCategoryDef,
    Position,
    PositionNumber,
    PositionNumberDottedLine,
    PositionStatus,
    Scope,
)

EXPECTED_HEADERS = [
    "职位", "职位类型", "岗位编号", "隶属公司", "级别", "国家或地区", "职位开启日", "职位关闭日",
    "工作地点", "工作职责描述", "直线经理", "虚线经理", "法律强制/可选",
    "Org-Chart中的显示", "之前的职位", "之前的公司", "备注",
]

# 国家名称 → 编号（-4-{编号}，仅作 Country 字典 code 参考，不再参与岗位编号）
COUNTRY_CODES = {
    "比利时": "4-1", "丹麦": "4-2", "瑞典": "4-3", "荷兰": "4-4",
    "卢森堡": "4-5", "英国": "4-6", "美国": "4-7", "中国香港": "4-8", "中国上海": "4-9",
}

SCOPE_PARSE = {"Family": Scope.FAMILY, "Global": Scope.GLOBAL, "Regional": Scope.REGIONAL}
SCOPE_RAW_BY_VALUE = {"family": "Family", "global": "Global", "regional": "Regional"}

YEAR_RE = re.compile(r"^\s*(\d{4})\s*$")
NUMBER_TOKEN_RE = re.compile(r"^P(A?)(\d+)$")

# 引用解析：按职位名。分割符兼容 ; ； 、 , ，；兼容遗留 "Name (Pxxx)" 尾巴
REF_SPLIT_RE = re.compile(r"[;；、,，]")
LEGACY_NUM_IN_REF_RE = re.compile(r"[(（]P[^)）]*[)）]")


def parse_date(raw):
    """年份/日期字符串 → date；空/N/A → None。"""
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw or raw.upper() == "N/A":
        return None
    m = YEAR_RE.match(raw)
    if m:
        return date(int(m.group(1)), 1, 1)
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_scope_country(raw):
    """'国家或地区' → (scope, country_name, country_code)。"""
    raw = (raw or "").strip()
    if raw.startswith("Country·"):
        cname = raw[len("Country·"):].strip()
        return Scope.COUNTRY, cname, COUNTRY_CODES.get(cname)
    return SCOPE_PARSE.get(raw, Scope.GLOBAL), None, None


def scope_raw_value(pn: PositionNumber) -> str:
    """还原岗位的「国家或地区」原始值（用于幂等键）。"""
    if pn.scope is None:
        return ""
    if pn.scope == Scope.COUNTRY:
        return f"Country·{pn.country.name}" if pn.country else ""
    return SCOPE_RAW_BY_VALUE.get(pn.scope.value, "")


def extract_refs(value):
    """从直线/虚线经理或之前职位单元格提取**职位名**引用列表。

    兼容遗留格式 'Name (P001-2)'（括号内编号剥离忽视）；'N/A'/空 → []。
    """
    if not value:
        return []
    text = LEGACY_NUM_IN_REF_RE.sub("", str(value))
    refs = [s.strip() for s in REF_SPLIT_RE.split(text)]
    return [r for r in refs if r and r.upper() != "N/A"]


def import_csv(db, rows):
    """rows: 可迭代的 dict（由 csv.DictReader 或上传解析产生）。"""
    report = {"total": 0, "imported": 0, "updated": 0, "errors": [], "warnings": []}

    companies = {c.name: c for c in db.query(Company).all()}
    functions = {p.name: p for p in db.query(Position).all()}
    countries = {c.name: c for c in db.query(Country).all()}

    # ---- 幂等键索引：(职位名, 公司名, 国家或地区) → 岗位 ----
    all_pns = db.query(PositionNumber).all()
    existing_by_key: dict[tuple, PositionNumber] = {}
    db_refs_by_name: dict[str, list] = {}  # 职位名 → [岗位]（文件外引用兜底）
    for pn in all_pns:
        pos_name = pn.position.name if pn.position else ""
        comp_name = pn.company.name if pn.company else ""
        existing_by_key[(pos_name, comp_name, scope_raw_value(pn))] = pn
        db_refs_by_name.setdefault(pos_name, []).append(pn)

    # ---- 编号系列计数器（正式 P / 外包 PA），取库内当前最大序号 +1 起 ----
    seq_counters = {"P": next_sequence(db, "P"), "PA": next_sequence(db, "PA")}

    parsed = []  # {key, label, data, solid_refs, dotted_refs, prev_refs, prev_company_name}
    seen_keys = set()

    # ---- 第 1 趟：读取并构建基础数据（编号列一律忽视）----
    for raw in rows:
        if raw is None or all((v is None or str(v).strip() == "") for v in raw.values()):
            continue
        report["total"] += 1
        source_number = (raw.get("岗位编号") or "").strip()  # 仅记录用，不参与任何逻辑
        label = source_number or (raw.get("职位") or "").strip() or f"第{report['total']}行"

        scope, cname, ccode = parse_scope_country(raw.get("国家或地区"))
        country = None
        if scope == Scope.COUNTRY:
            if not cname or not ccode:
                report["errors"].append(f"{label}: 国家或地区不可识别（{raw.get('国家或地区')}）")
                continue
            if cname not in countries:
                countries[cname] = Country(name=cname, code=ccode)
                db.add(countries[cname])
                db.flush()
            country = countries[cname]
        scope_raw = (raw.get("国家或地区") or "").strip()

        company_name = (raw.get("隶属公司") or "").strip()
        if not company_name:
            report["errors"].append(f"{label}: 缺隶属公司")
            continue
        if company_name not in companies:
            companies[company_name] = Company(name=company_name)
            db.add(companies[company_name])
            db.flush()

        function_name = (raw.get("职位") or "").strip()
        if not function_name:
            report["errors"].append(f"{label}: 缺职位")
            continue
        if function_name not in functions:
            functions[function_name] = Position(name=function_name)
            db.add(functions[function_name])
            db.flush()

        key = (function_name, company_name, scope_raw or "Global")
        if key in seen_keys:
            report["errors"].append(
                f"{label}: 文件内重复（职位+公司+国家或地区），该行不导入：{key}")
            continue
        seen_keys.add(key)

        opening = parse_date(raw.get("职位开启日"))
        closing = parse_date(raw.get("职位关闭日"))
        status = PositionStatus.CLOSED if closing else PositionStatus.OPEN

        legal = None
        lc = (raw.get("法律强制/可选") or "").strip()
        if lc:
            exists = db.query(LegalCategoryDef).filter(LegalCategoryDef.name == lc).first()
            if exists:
                legal = lc
            else:
                try:
                    legal = LegalCategory(lc).value
                except ValueError:
                    report["warnings"].append(f"{label}: 未知法律分类「{lc}」")
                    legal = lc  # 仍入库为字符串，允许运行时通过字典扩展

        # 职位类型（Consultant / Employee / External Employee）
        position_type = (raw.get("职位类型") or "").strip() or None

        data = {
            "position_id": functions[function_name].id,
            "company_id": companies[company_name].id,
            "level": (raw.get("级别") or "").strip() or None,
            "scope": scope,
            "country_id": country.id if country else None,
            "position_type": position_type,
            "opening_date": opening,
            "closing_date": closing,
            "work_location": (raw.get("工作地点") or "").strip() or None,
            "job_responsibility": (raw.get("工作职责描述") or "").strip() or None,
            "legal_category": legal,
            "org_chart_display": (raw.get("Org-Chart中的显示") or "").strip() or None,
            "remark": (raw.get("备注") or "").strip() or None,
            "status": status,
        }
        prev_company_name = (raw.get("之前的公司") or "").strip()
        prev_company_name = None if prev_company_name.upper() == "N/A" or not prev_company_name else prev_company_name
        parsed.append({
            "key": key,
            "label": function_name,
            "data": data,
            "solid_refs": extract_refs(raw.get("直线经理")),
            "dotted_refs": extract_refs(raw.get("虚线经理")),
            "prev_refs": extract_refs(raw.get("之前的职位")),
            "prev_company_name": prev_company_name,
        })

    db.flush()

    # ---- 第 2 趟：按幂等键 upsert（新行由系统分配编号）----
    file_refs_by_name: dict[str, list] = {}  # 本文件职位名 → [岗位]（同文件经理引用优先）
    for item in parsed:
        key = item["key"]
        data = item["data"]
        pn = existing_by_key.get(key)
        if pn is None:
            prefix = number_series(data["position_type"])
            number = f"{prefix}{seq_counters[prefix]}"
            seq_counters[prefix] += 1
            pn = PositionNumber(number=number, **data)
            db.add(pn)
            db.flush()
            existing_by_key[key] = pn
            report["imported"] += 1
            report.setdefault("assigned_numbers", []).append({"label": key[0], "number": number})
            record_event(db, pn.id, None, data["status"].value, note="导入建档（系统分配编号）")
            if data["status"] == PositionStatus.CLOSED and data["closing_date"]:
                ev = record_event(db, pn.id, PositionStatus.OPEN.value, PositionStatus.CLOSED.value,
                                  note="导入（历史关闭）")
                ev.changed_at = datetime.combine(data["closing_date"], time.min)
        else:
            for k, v in data.items():
                setattr(pn, k, v)
            report["updated"] += 1
        file_refs_by_name.setdefault(item["label"], []).append(pn)

    db.flush()

    def _resolve_ref(name: str, exclude_id=None):
        """按职位名解析引用岗位：本文件优先（首个命中），否则库内兜底。同名多个告警。"""
        candidates = file_refs_by_name.get(name) or db_refs_by_name.get(name) or []
        if len(candidates) > 1:
            report["warnings"].append(f"{name}: 同名岗位 {len(candidates)} 个，采用首个")
        return candidates[0] if candidates else None

    # ---- 第 3 趟：解析直线/虚线经理、之前的职位/公司（按职位名）----
    for item in parsed:
        pn = existing_by_key[item["key"]]
        solid, dotted, prev_refs = item["solid_refs"], item["dotted_refs"], item["prev_refs"]

        if solid:
            m = _resolve_ref(solid[0], exclude_id=pn.id)
            if m is None:
                report["warnings"].append(f"{item['label']}: 直线经理「{solid[0]}」不存在")
            elif m.id == pn.id:
                report["errors"].append(f"{item['label']}: 直线经理为自身")
            else:
                pn.solid_line_manager_id = m.id

        db.query(PositionNumberDottedLine).filter(
            PositionNumberDottedLine.position_number_id == pn.id
        ).delete()
        seen_dotted = set()
        for ref in dotted:
            m = _resolve_ref(ref, exclude_id=pn.id)
            if m is None:
                report["warnings"].append(f"{item['label']}: 虚线经理「{ref}」不存在")
            elif m.id == pn.id:
                report["warnings"].append(f"{item['label']}: 虚线经理为自身，跳过")
            elif m.id in seen_dotted:
                continue
            else:
                seen_dotted.add(m.id)
                db.add(PositionNumberDottedLine(position_number_id=pn.id, dotted_manager_id=m.id))

        if prev_refs:
            m = _resolve_ref(prev_refs[0])
            if m:
                pn.prev_position_id = m.id

        if item["prev_company_name"]:
            cn = item["prev_company_name"]
            if cn not in companies:
                companies[cn] = Company(name=cn)
                db.add(companies[cn])
                db.flush()
            pn.prev_company_id = companies[cn].id

    # ---- 环检测 ----
    report["warnings"] += _detect_cycles(db)

    db.commit()
    return report


def _detect_cycles(db) -> list[str]:
    """沿直线经理链上溯，检测环路（全库范围）。"""
    warnings = []
    by_id = {pn.id: pn for pn in db.query(PositionNumber).all()}
    for pn in by_id.values():
        seen = set()
        cur = pn
        while cur is not None:
            if cur.id in seen:
                warnings.append(f"{pn.number}: 汇报关系存在环路")
                break
            seen.add(cur.id)
            cur = by_id.get(cur.solid_line_manager_id)
    return warnings
