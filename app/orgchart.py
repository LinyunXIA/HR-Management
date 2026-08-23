"""组织架构图数据构建（汇报线树）。

- 节点 = 岗位编号。
- solid_edges：直线汇报（父→子），依据 solid_line_manager_id。
- dotted_edges：虚线汇报（岗位 → 虚线经理）。
- roots：直线经理为空的岗位。
"""
from sqlalchemy.orm import Session

from app.models import Employee, PositionNumber, PositionNumberDottedLine, PositionStatus


def build_orgchart(db: Session) -> dict:
    pns = db.query(PositionNumber).all()
    nodes = []
    solid_edges = []
    dotted_edges = []
    roots = []
    has_solid_line = set()

    for pn in pns:
        node = {
            "id": pn.id,
            "number": pn.number,
            "display": pn.org_chart_display or (pn.position.name if pn.position else pn.number),
            "position_name": pn.position.name if pn.position else None,
            "company": pn.company.name if pn.company else None,
            "level": pn.level,
            "scope": pn.scope.value if pn.scope else None,
            "country": pn.country.name if pn.country else None,
            "status": pn.status.value if pn.status else None,
            "closed": pn.status == PositionStatus.CLOSED,
            "incumbent": None,
            "incumbent_id": None,
        }
        inc = db.query(Employee).filter(Employee.position_number_id == pn.id).first()
        if inc:
            node["incumbent"] = inc.name
            node["incumbent_id"] = inc.id
        nodes.append(node)

        if pn.solid_line_manager_id:
            m = db.get(PositionNumber, pn.solid_line_manager_id)
            if m:
                solid_edges.append({"from": m.number, "to": pn.number})
                has_solid_line.add(pn.number)
        else:
            roots.append(pn.number)

        rows = db.query(PositionNumberDottedLine.dotted_manager_id, PositionNumberDottedLine.label).filter(
            PositionNumberDottedLine.position_number_id == pn.id
        ).all()
        for mid, label in rows:
            m = db.get(PositionNumber, mid)
            if m:
                dotted_edges.append({"from": pn.number, "to": m.number, "label": label or "虚线汇报"})

    return {
        "nodes": nodes,
        "solid_edges": solid_edges,
        "dotted_edges": dotted_edges,
        "roots": roots,
    }
