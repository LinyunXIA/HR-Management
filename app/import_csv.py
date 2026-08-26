"""Position.csv 导入：解析/校验/幂等入库。

编号策略（v3）：**源文件「岗位编号」列一律忽视**，由系统在导入时自动分配：
  正式岗（Employee / Consultant）→ P{seq}；外包岗（External Employee）→ PA{seq}。
迭代识别（DESIGN §8 两段式，#49 定稿）：
  可选「岗位ID」列 → 带 ID 且库内存在按正式 ID 认老更新（updated_by_id）；
  无 ID / 未携带 → 回退幂等键（职位名, 隶属公司, 国家或地区, 开启日）认老（updated_by_key）；
  均未命中 → 新建分配编号。带 ID 不存在 → 报错该行不导入。

分三趟：
  1) 解析校验行数据（级别须在 levels 字典、strict 模式法律分类须在字典），
     建公司/职位/国家基础字典；
  2) 按 ID 优先、幂等键回退 upsert 岗位（新行自动分配编号、写生命周期事件）；
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
    Level,
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
    "岗位ID",  # 可选列（#49 迭代导入按正式 ID 认老）；不计入缺列告警
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


def opening_key(v) -> str:
    """开启日归一化为幂等键分量：年份精度（MM-DD==01-01）存 YYYY，否则完整 ISO。

    文件侧字符串（"1982" / "1982-03-01"）与库内侧 date 统一走此函数，
    保证两侧键表示一致。
    """
    if v is None or v == "":
        return ""
    if isinstance(v, str):
        v = parse_date(v)
        if v is None:
            return ""
    if isinstance(v, date):
        return str(v.year) if (v.month, v.day) == (1, 1) else v.isoformat()
    return str(v)


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


def scope_key_raw(scope: Scope, cname: str | None) -> str:
    """文件侧幂等键的「国家或地区」分量：由**归一化后**的 scope 还原（issue #148-2）。

    此前键分量用原始字符串（`scope_raw or "Global"`），小写/笔误入库为 Global
    但键仍存原串 → 迭代再导同一文件无法认老、重复建档。现两侧统一走规范值。
    """
    if scope == Scope.COUNTRY:
        return f"Country·{cname}" if cname else ""
    return SCOPE_RAW_BY_VALUE.get(scope.value, "")


def position_anchor_key(pn: PositionNumber) -> tuple:
    """库侧岗位的幂等键四元组（职位名, 公司名, 国家或地区, 开启日）。

    与文件侧 item["key"] 同构（issue #137 锚一致性比对 / existing_by_key 构建共用）。
    """
    pos_name = pn.position.name if pn.position else ""
    comp_name = pn.company.name if pn.company else ""
    return (pos_name, comp_name, scope_raw_value(pn), opening_key(pn.opening_date))


def extract_refs(value):
    """从直线/虚线经理或之前职位单元格提取**职位名**引用列表。

    兼容遗留格式 'Name (P001-2)'（括号内编号剥离忽视）；'N/A'/空 → []。
    """
    if not value:
        return []
    text = LEGACY_NUM_IN_REF_RE.sub("", str(value))
    refs = [s.strip() for s in REF_SPLIT_RE.split(text)]
    return [r for r in refs if r and r.upper() != "N/A"]


def import_csv(db, rows, *, strict_legal: bool = True):
    """rows: 可迭代的 dict（由 csv.DictReader 或上传解析产生）。

    strict_legal=True（默认，#51）：法律分类不在 legal_categories 字典（且非历史枚举值）时
    记入 errors 且该行不导入；False 仅限历史数据一次性迁移显式传入，告警后照常入库。
    """
    report = {"total": 0, "imported": 0, "updated": 0, "updated_by_id": 0,
              "updated_by_key": 0, "errors": [], "warnings": []}

    # ---- 表头比对（#117）：EXPECTED_HEADERS 此前定义未使用，列名笔误会让可空列
    # 静默取 None 并随 upsert 覆盖库内原值。必填列缺失 → 整批拒绝；可空列缺失/
    # 未知多余列 → warnings 明细提示。
    rows = list(rows)
    actual_headers = set()
    for r in rows:
        if r:
            actual_headers |= {k for k in (r.keys() or []) if k}
    missing_required = [h for h in ("职位", "隶属公司", "职位开启日") if h not in actual_headers]
    if missing_required:
        report["errors"].append(
            f"CSV 缺少必填列：{'、'.join(missing_required)}（表头漂移会静默清空数据，整批拒绝）")
        return report
    # 「岗位ID」为可选增强列：缺失属正常形态，不计入缺列告警（issue #148-1，
    # 修复注释与实现矛盾——此前标准 17 列每次导入必得一条缺列 warning）
    optional_ignore_missing = {"岗位ID"}
    missing_optional = [h for h in EXPECTED_HEADERS
                        if h not in actual_headers and h not in optional_ignore_missing]
    extra_cols = [h for h in sorted(actual_headers) if h not in EXPECTED_HEADERS]
    if missing_optional:
        report["warnings"].append(f"CSV 缺少可空列（该列将按空值处理，注意迭代导入会覆盖库内原值）：{'、'.join(missing_optional)}")
    if extra_cols:
        report["warnings"].append(f"CSV 含未知列（已忽视）：{'、'.join(extra_cols)}")

    companies = {c.name: c for c in db.query(Company).all()}
    functions = {p.name: p for p in db.query(Position).all()}
    countries = {c.name: c for c in db.query(Country).all()}
    valid_levels = {code for (code,) in db.query(Level.code).all()}

    # ---- 幂等键索引：(职位名, 公司名, 国家或地区, 开启日) → 岗位 ----
    all_pns = db.query(PositionNumber).all()
    existing_by_key: dict[tuple, PositionNumber] = {}
    db_dup_keys: set[tuple] = set()   # #98：库内同幂等键多编制（迭代导入须报错由用户区分）
    db_dup_counts: dict[tuple, int] = {}
    db_refs_by_name: dict[str, list] = {}  # 职位名 → [岗位]（文件外引用兜底）
    for pn in all_pns:
        key4 = position_anchor_key(pn)
        if key4 in existing_by_key:
            db_dup_keys.add(key4)
            db_dup_counts[key4] = db_dup_counts.get(key4, 1) + 1
        existing_by_key[key4] = pn
        db_refs_by_name.setdefault(key4[0], []).append(pn)

    # ---- 编号系列计数器（正式 P / 外包 PA），取库内当前最大序号 +1 起 ----
    seq_counters = {"P": next_sequence(db, "P"), "PA": next_sequence(db, "PA")}

    parsed = []  # {key, label, data, solid_refs, dotted_refs, prev_refs, prev_company_name, ref_id}
    seen_keys = set()
    seen_ref_ids = set()  # 同文件两行引用同一岗位ID → 报错（#49）

    # ---- 第 1 趟：读取并构建基础数据（编号列一律忽视）----
    for raw in rows:
        if raw is None or all((v is None or str(v).strip() == "") for v in raw.values()):
            continue
        report["total"] += 1
        source_number = (raw.get("岗位编号") or "").strip()  # 仅记录用，不参与任何逻辑
        label = source_number or (raw.get("职位") or "").strip() or f"第{report['total']}行"

        scope, cname, ccode = parse_scope_country(raw.get("国家或地区"))
        raw_scope = (raw.get("国家或地区") or "").strip()
        # issue #148-2：不可识别范围静默降级 Global 无告警 → 补告警（仍按 Global 入库）
        if (raw_scope and raw_scope not in SCOPE_PARSE
                and not raw_scope.startswith("Country·") and scope == Scope.GLOBAL):
            report["warnings"].append(
                f"{label}: 国家或地区「{raw_scope}」不可识别，按 Global 处理")
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

        # 级别须在 levels 字典（PRD §3.6 / §4 F0；空值允许留空）
        level_val = (raw.get("级别") or "").strip() or None
        if level_val and level_val not in valid_levels:
            report["errors"].append(
                f"{label}: 级别「{level_val}」不在级别字典，该行不导入（请先在主数据维护）")
            continue

        opening = parse_date(raw.get("职位开启日"))
        closing = parse_date(raw.get("职位关闭日"))
        if opening is None:
            # #97 口径 C：开启日必填——F6.2 在岗判定与幂等键均以其为依据，缺失行不导入
            report["errors"].append(
                f"{label}: 职位开启日缺失或不可识别（{raw.get('职位开启日')}），该行不导入")
            continue
        # 关闭日早于开启日 → 报错该行不导入（issue #138，PRD F4 两段共用校验规则）
        if closing is not None and closing < opening:
            report["errors"].append(
                f"{label}: 职位关闭日（{closing}）早于开启日（{opening}），该行不导入")
            continue
        status = PositionStatus.CLOSED if closing else PositionStatus.OPEN

        legal = None
        lc = (raw.get("法律强制/可选") or "").strip()
        if lc:
            exists = db.query(LegalCategoryDef).filter(LegalCategoryDef.name == lc).first()
            if exists:
                legal = lc
            elif strict_legal:
                report["errors"].append(
                    f"{label}: 法律分类「{lc}」不在主数据字典，strict 模式下该行不导入")
                continue
            else:
                try:
                    legal = LegalCategory(lc).value
                except ValueError:
                    report["warnings"].append(f"{label}: 未知法律分类「{lc}」")
                    legal = lc  # 仍入库为字符串，允许运行时通过字典扩展

        # 职位类型（Consultant / Employee / External Employee）——issue #148-3：
        # 此前无字典校验，脏值（如 "In-house Full-time - Employee"）原样入库并
        # 破坏 number_series 的 PA 分流判定（仅精确匹配 "External Employee"）
        position_type = (raw.get("职位类型") or "").strip() or None
        if position_type and position_type not in ("Consultant", "Employee", "External Employee"):
            if strict_legal:
                report["errors"].append(
                    f"{label}: 职位类型「{position_type}」不在三类规范值"
                    f"（Consultant / Employee / External Employee），该行不导入")
                continue
            report["warnings"].append(
                f"{label}: 职位类型「{position_type}」非规范值，原样入库（历史迁移模式）")

        # 幂等键（PRD §3.1 v2.3，4 列）：职位名+公司+国家或地区+开启日
        # 「国家或地区」分量用归一化规范值（issue #148-2，与库侧 scope_raw_value 同构）
        key = (function_name, company_name, scope_key_raw(scope, cname),
               opening_key(raw.get("职位开启日")))
        if key in seen_keys:
            report["errors"].append(
                f"{label}: 文件内重复（职位+公司+国家或地区+开启日），该行不导入：{key}")
            continue
        seen_keys.add(key)

        # 可选「岗位ID」列（#49）：迭代导入按正式 ID 优先认老；非法/重复引用报错该行
        ref_id = None
        raw_id = (raw.get("岗位ID") or "").strip()
        if raw_id and raw_id.upper() != "N/A":
            if not raw_id.isdigit():
                report["errors"].append(f"{label}: 岗位ID「{raw_id}」非法（须为正整数），该行不导入")
                continue
            ref_id = int(raw_id)
            if ref_id in seen_ref_ids:
                report["errors"].append(
                    f"{label}: 岗位ID {ref_id} 被文件内多行引用，该行不导入")
                continue
            seen_ref_ids.add(ref_id)

        data = {
            "position_id": functions[function_name].id,
            "company_id": companies[company_name].id,
            "level": level_val,
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
            "ref_id": ref_id,
        })

    db.flush()

    # ---- 第 2 趟：按 ID 优先、幂等键回退 upsert（新行由系统分配编号，#49）----
    # issue #133：第 2 趟拒绝的行（_skipped 标记）第 3 趟必须整体跳过——
    # 此前被拒行仍留在 parsed 中参与汇报关系解析并随 commit 落库，
    # 「该行不导入」契约被静默突破（双键行的经理会写到其中一个重复编制上）
    file_refs_by_name: dict[str, list] = {}  # 本文件职位名 → [岗位]（同文件经理引用优先）
    for item in parsed:
        key = item["key"]
        data = item["data"]
        label = item["label"]
        pn = None
        matched_via = None
        if item["ref_id"] is not None:
            pn = db.get(PositionNumber, item["ref_id"])
            if pn is None:
                report["errors"].append(
                    f"{label}: 岗位ID {item['ref_id']} 库内不存在，该行不导入")
                item["_skipped"] = True
                continue
            matched_via = "id"
            # 锚一致性校验前移（issue #137 完整化）：带 ID 认老时先比对四锚字段，
            # 不一致 → 该行报错不落库。此前 setattr 已把锚字段覆盖、直到第 3 趟才
            # 补报错——「识别锚请在系统内维护」防护形同虚设（改动已随 commit 生效）。
            if position_anchor_key(pn) != key:
                report["errors"].append(
                    f"{label}: 携带岗位ID={item['ref_id']} 但识别锚（职位/公司/国家或地区/开启日）"
                    f"与库内不一致，该行不导入——识别锚字段请在系统内维护而非重导 CSV")
                item["_skipped"] = True
                continue
        else:
            # #98：库内同幂等键命中多编制 → 无法唯一识别，报错该行不导入（PRD F4：由用户区分）
            if key in db_dup_keys:
                report["errors"].append(
                    f"{label}: 库内存在 {db_dup_counts[key]} 个同幂等键岗位"
                    f"（职位+公司+国家或地区+开启日），无法唯一识别，该行不导入——"
                    f"请先在系统内处理重复编制")
                item["_skipped"] = True
                continue
            pn = existing_by_key.get(key)
            matched_via = "key" if pn is not None else None

        if pn is None:
            prefix = number_series(data["position_type"])
            number = f"{prefix}{seq_counters[prefix]}"
            seq_counters[prefix] += 1
            pn = PositionNumber(number=number, **data)
            db.add(pn)
            db.flush()
            existing_by_key[key] = pn
            report["imported"] += 1
            report.setdefault("assigned_numbers", []).append({"label": key[0], "number": number, "action": "imported"})
            record_event(db, pn.id, None, data["status"].value, note="导入建档（系统分配编号）")
            if data["status"] == PositionStatus.CLOSED and data["closing_date"]:
                ev = record_event(db, pn.id, PositionStatus.OPEN.value, PositionStatus.CLOSED.value,
                                  note="导入（历史关闭）")
                ev.changed_at = datetime.combine(data["closing_date"], time.min)
        else:
            for k, v in data.items():
                setattr(pn, k, v)
            report["updated"] += 1
            action = "updated_by_id" if matched_via == "id" else "updated_by_key"
            report.setdefault("assigned_numbers", []).append({"label": key[0], "number": pn.number, "action": action})
            if matched_via == "id":
                report["updated_by_id"] += 1
            else:
                report["updated_by_key"] += 1
        file_refs_by_name.setdefault(item["label"], []).append(pn)

    db.flush()

    def _resolve_ref(name: str):
        """按职位名解析引用岗位：本文件优先（首个命中），否则库内兜底。同名多个告警。"""
        candidates = file_refs_by_name.get(name) or db_refs_by_name.get(name) or []
        if len(candidates) > 1:
            report["warnings"].append(f"{name}: 同名岗位 {len(candidates)} 个，采用首个")
        return candidates[0] if candidates else None

    # ---- 第 3 趟：解析直线/虚线经理、之前的职位/公司（按职位名）----
    for item in parsed:
        if item.get("_skipped"):
            continue  # issue #133：第 2 趟已拒绝的行不参与任何关系落库
        pn = existing_by_key.get(item["key"])
        if pn is None:
            # 带 ID 认老但识别锚与库内不一致——锚一致性已在第 2 趟前移校验（#137），
            # 此分支仅为兜底（不应触达）
            report["errors"].append(
                f"{item['label']}: 携带岗位ID={item['ref_id']} 但识别锚与库内不一致，"
                f"该行汇报关系未解析——识别锚字段请在系统内维护而非重导 CSV")
            continue
        solid, dotted, prev_refs = item["solid_refs"], item["dotted_refs"], item["prev_refs"]

        if solid:
            if len(solid) > 1:
                report["warnings"].append(
                    f"{item['label']}: 直线经理列含 {len(solid)} 个值（直线经理唯一），仅采用首个「{solid[0]}」")
            m = _resolve_ref(solid[0])
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
            m = _resolve_ref(ref)
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
            if m and m.id == pn.id:
                report["warnings"].append(f"{item['label']}: 之前的职位为自身，跳过")
            elif m:
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
