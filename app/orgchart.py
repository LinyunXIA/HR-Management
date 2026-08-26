"""组织架构图数据构建（汇报线树）。

- 节点 = 岗位编号。
- solid_edges：直线汇报（父→子），依据 solid_line_manager_id。
- dotted_edges：虚线汇报（岗位 → 虚线经理）。
- roots：直线经理为空的岗位。
- anomalies：从任一根不可达的岗位（孤立 / 成环 / 经理引用失效，PRD F3.3 底部异常列表）。
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
    number_to_id = {}

    for pn in pns:
        number_to_id[pn.number] = pn.id
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

    # 异常岗位（#124，PRD F3.3）：从任一根沿直线边可达性遍历，
    # 不可达者 = 孤立（经理引用失效）或处于汇报环中——底部单独列表提示
    children: dict[str, list[str]] = {}
    for e in solid_edges:
        children.setdefault(e["from"], []).append(e["to"])
    reachable = set()
    stack = [r for r in roots if r in number_to_id]
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        stack.extend(children.get(cur, []))
    anomalies = [
        {"number": n["number"], "display": n["display"], "company": n["company"],
         "status": n["status"], "reason": "成环或直线经理引用失效，未挂入组织树"}
        for n in nodes if n["number"] not in reachable and n["number"] not in roots
    ]

    return {
        "nodes": nodes,
        "solid_edges": solid_edges,
        "dotted_edges": dotted_edges,
        "roots": roots,
        "anomalies": anomalies,
    }
