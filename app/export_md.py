"""组织架构导出 MD（3 格式）。

- `org`   ：公司 + 岗位（不含汇报线，按公司分组，含占用员工）
- `solid` ：直线汇报线（按 solid_line_manager_id 层级树，含占用员工）
- `dotted`：虚线汇报线（岗位 → 虚线经理，含占用员工）
"""
from sqlalchemy.orm import Session

from app.models import Company, Employee, PositionNumber, PositionNumberDottedLine


def _incumbent_map(db: Session) -> dict[int, str]:
    """当前占用员工映射（在职员工 position_number_id 唯一，离职已解绑为 NULL）。"""
    rows = (
        db.query(Employee.position_number_id, Employee.name)
        .filter(Employee.position_number_id.isnot(None))
        .all()
    )
    return {pid: name for pid, name in rows}


def _display(pn: PositionNumber, incumbents: dict[int, str]) -> str:
    name = pn.org_chart_display or (pn.position.name if pn.position else pn.number)
    inc = incumbents.get(pn.id)
    if inc:
        return f"{name} ({pn.number}) · 👤 {inc}"
    return f"{name} ({pn.number})"


def export_org(db: Session) -> str:
    incumbents = _incumbent_map(db)
    lines = ["## 组织架构（公司 + 岗位）"]
    for company in db.query(Company).order_by(Company.name):
        pns = (
            db.query(PositionNumber)
            .filter(PositionNumber.company_id == company.id)
            .order_by(PositionNumber.number)
            .all()
        )
        if not pns:
            continue
        lines.append(f"\n### {company.name}")
        for pn in pns:
            lines.append(f"- {_display(pn, incumbents)}【{pn.level or ''} · {pn.status.value}】")
    return "\n".join(lines)


def export_solid(db: Session) -> str:
    incumbents = _incumbent_map(db)
    pns = db.query(PositionNumber).all()
    children: dict[int, list[PositionNumber]] = {}
    by_id = {pn.id: pn for pn in pns}
    has_mgr = set()
    for pn in pns:
        if pn.solid_line_manager_id and pn.solid_line_manager_id in by_id:
            children.setdefault(pn.solid_line_manager_id, []).append(pn)
            has_mgr.add(pn.id)
    roots = [pn for pn in pns if pn.id not in has_mgr]
    lines = ["## 直线汇报线"]

    def walk(pn: PositionNumber, depth: int):
        lines.append("    " * depth + f"- {_display(pn, incumbents)}")
        for child in sorted(children.get(pn.id, []), key=lambda c: c.number):
            walk(child, depth + 1)

    for root in sorted(roots, key=lambda r: r.number):
        walk(root, 0)
    return "\n".join(lines)


def export_dotted(db: Session) -> str:
    incumbents = _incumbent_map(db)
    lines = ["## 虚线汇报线"]
    rows = (
        db.query(PositionNumber, PositionNumberDottedLine)
        .join(PositionNumberDottedLine, PositionNumberDottedLine.position_number_id == PositionNumber.id)
        .all()
    )
    for pn, link in rows:
        mgr = db.get(PositionNumber, link.dotted_manager_id)
        if mgr:
            lines.append(f"- {_display(pn, incumbents)} → {_display(mgr, incumbents)}")
    return "\n".join(lines)


def export_md(db: Session, fmt: str) -> str:
    if fmt == "org":
        return export_org(db)
    if fmt == "solid":
        return export_solid(db)
    if fmt == "dotted":
        return export_dotted(db)
    raise ValueError(f"未知导出格式: {fmt}（支持 org / solid / dotted）")
