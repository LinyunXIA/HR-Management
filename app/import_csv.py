"""Position.csv 导入：解析/校验/幂等入库。

分三趟：
  1) 建公司/职位/国家基础字典；
  2) upsert 岗位编号（含开启/关闭日 → 状态映射）；
  3) 解析直线/虚线经理、之前的职位/公司为外键，并做环检测。
"""
import csv
import re
from datetime import date, datetime, time

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
    "职位", "岗位编号", "隶属公司", "级别", "国家或地区", "职位开启日", "职位关闭日",
    "工作地点", "工作职责描述", "直线经理", "虚线经理", "法律强制/可选",
    "Org-Chart中的显示", "之前的职位", "之前的公司", "备注",
]

# 国家名称 → 编号（-4-{编号}）
COUNTRY_CODES = {
    "比利时": "4-1", "丹麦": "4-2", "瑞典": "4-3", "荷兰": "4-4",
    "卢森堡": "4-5", "英国": "4-6", "美国": "4-7", "中国香港": "4-8", "中国上海": "4-9",
}

SCOPE_PARSE = {"Family": Scope.FAMILY, "Global": Scope.GLOBAL, "Regional": Scope.REGIONAL}

YEAR_RE = re.compile(r"^\s*(\d{4})\s*$")
NUMBER_RE = re.compile(r"P\d{3,}(?:-\d+)*")


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


def extract_numbers(value):
    """从 'Name (P001-2)' 中提取 P 编号列表。"""
    if not value:
        return []
    return NUMBER_RE.findall(str(value))


def import_csv(db, rows):
    """rows: 可迭代的 dict（由 csv.DictReader 或上传解析产生）。"""
    report = {"total": 0, "imported": 0, "updated": 0, "errors": [], "warnings": []}

    companies = {c.name: c for c in db.query(Company).all()}
    positions = {p.name: p for p in db.query(Position).all()}
    countries = {c.name: c for c in db.query(Country).all()}
    existing = {pn.number: pn for pn in db.query(PositionNumber).all()}

    parsed = []  # (number, data, solid_numbers, dotted_numbers, prev_number, prev_company_name)
    seen_numbers = set()

    # ---- 第 1 趟：读取并构建基础数据 ----
    for raw in rows:
        if raw is None or all((v is None or str(v).strip() == "") for v in raw.values()):
            continue
        number = (raw.get("岗位编号") or "").strip()
        report["total"] += 1
        if not number:
            report["errors"].append("某行缺少岗位编号")
            continue
        if not NUMBER_RE.fullmatch(number):
            report["errors"].append(f"{number}: 岗位编号格式非法")
            continue
        if number in seen_numbers:
            report["errors"].append(f"{number}: 岗位编号在文件中重复，该行不导入")
            continue
        seen_numbers.add(number)

        scope, cname, ccode = parse_scope_country(raw.get("国家或地区"))
        country = None
        if scope == Scope.COUNTRY:
            if not cname or not ccode:
                report["errors"].append(f"{number}: 国家或地区不可识别（{raw.get('国家或地区')}）")
                continue
            if cname not in countries:
                countries[cname] = Country(name=cname, code=ccode)
                db.add(countries[cname])
                db.flush()
            country = countries[cname]

        company_name = (raw.get("隶属公司") or "").strip()
        if not company_name:
            report["errors"].append(f"{number}: 缺隶属公司")
            continue
        if company_name not in companies:
            companies[company_name] = Company(name=company_name)
            db.add(companies[company_name])
            db.flush()

        function_name = (raw.get("职位") or "").strip()
        if not function_name:
            report["errors"].append(f"{number}: 缺职位")
            continue
        if function_name not in positions:
            positions[function_name] = Position(name=function_name)
            db.add(positions[function_name])
            db.flush()

        opening = parse_date(raw.get("职位开启日"))
        closing = parse_date(raw.get("职位关闭日"))
        status = PositionStatus.CLOSED if closing else PositionStatus.OPEN

        legal = None
        lc = (raw.get("法律强制/可选") or "").strip()
        if lc:
            # 校验 against LegalCategoryDef 字典，兼容旧 enum 值
            try:
                legal = LegalCategory(lc).value
            except ValueError:
                # 非 enum 值：检查字典表中是否存在，存在则按字符串入库
                exists = db.query(LegalCategoryDef).filter(LegalCategoryDef.name == lc).first()  # type: ignore  # noqa: F821
                if exists:
                    legal = lc
                else:
                    report["warnings"].append(f"{number}: 未知法律分类「{lc}」")
                    legal = lc  # 仍入库为字符串，允许运行时通过字典扩展

        # 职位类型（Position.md §9：Consultant / Employee / External Employee）
        position_type = (raw.get("职位类型") or "").strip() or None

        data = {
            "position_id": positions[function_name].id,
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
        parsed.append((
            number, data,
            extract_numbers(raw.get("直线经理")),
            extract_numbers(raw.get("虚线经理")),
            extract_numbers(raw.get("之前的职位")),
            prev_company_name,
        ))

    db.flush()

    # ---- 第 2 趟：upsert 岗位编号 ----
    for number, data, solid, dotted, prev_num, prev_company_name in parsed:
        pn = existing.get(number)
        if pn is None:
            pn = PositionNumber(number=number, **data)
            db.add(pn)
            db.flush()
            existing[number] = pn
            report["imported"] += 1
            record_event(db, pn.id, None, data["status"].value, note="CSV 导入建档")
            if data["status"] == PositionStatus.CLOSED and data["closing_date"]:
                ev = record_event(db, pn.id, PositionStatus.OPEN.value, PositionStatus.CLOSED.value,
                                  note="CSV 导入（历史关闭）")
                ev.changed_at = datetime.combine(data["closing_date"], time.min)
        else:
            for k, v in data.items():
                setattr(pn, k, v)
            report["updated"] += 1

    db.flush()

    # ---- 第 3 趟：解析直线/虚线经理、之前的职位/公司 ----
    for number, data, solid, dotted, prev_num, prev_company_name in parsed:
        pn = existing[number]
        if solid:
            m = existing.get(solid[0])
            if m:
                if m.id == pn.id:
                    report["errors"].append(f"{number}: 直线经理为自身")
                else:
                    pn.solid_line_manager_id = m.id
            else:
                report["warnings"].append(f"{number}: 直线经理 {solid[0]} 不存在")
        db.query(PositionNumberDottedLine).filter(
            PositionNumberDottedLine.position_number_id == pn.id
        ).delete()
        for dn in dotted:
            m = existing.get(dn)
            if m:
                if m.id != pn.id:
                    db.add(PositionNumberDottedLine(position_number_id=pn.id, dotted_manager_id=m.id))
                else:
                    report["warnings"].append(f"{number}: 虚线经理为自身，跳过")
            else:
                report["warnings"].append(f"{number}: 虚线经理 {dn} 不存在")
        if prev_num:
            m = existing.get(prev_num[0])
            if m:
                pn.prev_position_id = m.id
        if prev_company_name:
            if prev_company_name not in companies:
                companies[prev_company_name] = Company(name=prev_company_name)
                db.add(companies[prev_company_name])
                db.flush()
            pn.prev_company_id = companies[prev_company_name].id

    # ---- 环检测 ----
    report["warnings"] += _detect_cycles(existing)

    db.commit()
    return report


def _detect_cycles(existing: dict) -> list[str]:
    """沿直线经理链上溯，检测环路。"""
    warnings = []
    by_id = {pn.id: pn for pn in existing.values()}
    for pn in existing.values():
        seen = set()
        cur = pn
        while cur is not None:
            if cur.id in seen:
                warnings.append(f"{pn.number}: 汇报关系存在环路")
                break
            seen.add(cur.id)
            cur = by_id.get(cur.solid_line_manager_id)
    return warnings
