"""调岗资源：顶级 REST 资源 POST /transfers。"""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import lifecycle
from app.db import get_db
from app.helpers import get_or_404
from app.models import Employee, EmploymentStatus, PositionNumber, PositionStatus
from app.routers.employees import _assert_attachable, serialize_employee
from app.schemas import TransferCreate

router = APIRouter(prefix="/api/v1", tags=["transfers"])


@router.post("/transfers", status_code=201)
def create_transfer(payload: TransferCreate, response: Response, db: Session = Depends(get_db)):
    """创建调岗：旧岗→Vacant，新岗→Filled。"""
    emp = get_or_404(db, Employee, payload.employee_id, "员工不存在")
    if emp.employment_status == EmploymentStatus.TERMINATED:
        raise HTTPException(400, "离职员工不可调岗")
    new_pn = get_or_404(db, PositionNumber, payload.to_position_id, "目标岗位不存在")
    _assert_attachable(db, new_pn)
    old_pn = db.get(PositionNumber, emp.position_number_id) if emp.position_number_id else None
    if old_pn and old_pn.id != new_pn.id:
        if old_pn.status == PositionStatus.FILLED:
            lifecycle.transition(db, old_pn, PositionStatus.VACANT, note=f"员工 {emp.name} 转岗",
                                 employee_id=emp.id, system=True)
        emp.position_number_id = new_pn.id
        lifecycle.transition(db, new_pn, PositionStatus.FILLED, note=f"员工 {emp.name} 入职挂编",
                             employee_id=emp.id, system=True)
    db.commit()
    response.headers["Location"] = f"/api/v1/employees/{emp.id}"
    return serialize_employee(db, emp)


@router.get("/transfers")
def list_transfers(employee_id: int | None = None, db: Session = Depends(get_db)):
    """调岗记录通过 PositionEvent 查询（过渡实现）。"""
    from app.models import PositionEvent
    q = db.query(PositionEvent).filter(PositionEvent.note.like("%转岗%"))
    if employee_id:
        q = q.filter(PositionEvent.employee_id == employee_id)
    events = q.order_by(PositionEvent.changed_at.desc()).all()
    return [
        {"id": e.id, "position_number_id": e.position_number_id, "from_status": e.from_status,
         "to_status": e.to_status, "changed_at": e.changed_at, "note": e.note, "employee_id": e.employee_id}
        for e in events
    ]
