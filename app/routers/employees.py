"""员工路由：CRUD + 入职/调岗/离职（联动岗位状态 Filled↔Vacant）。"""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import lifecycle
from app.db import get_db
from app.helpers import dotted_ids, get_or_404
from app.models import (
    Employee,
    EmploymentStatus,
    PositionNumber,
    PositionStatus,
)
from app.schemas import EmployeeCreate, EmployeeUpdate, TransferRequest

router = APIRouter(prefix="/api/v1", tags=["employees"])


def serialize_employee(db: Session, emp: Employee) -> dict:
    pn = db.get(PositionNumber, emp.position_number_id) if emp.position_number_id else None
    sl = db.get(PositionNumber, pn.solid_line_manager_id) if pn and pn.solid_line_manager_id else None
    dotted = dotted_ids(db, pn.id) if pn else []
    dotted_nums = []
    for did in dotted:
        dm = db.get(PositionNumber, did)
        dotted_nums.append(dm.number if dm else str(did))
    return {
        "id": emp.id,
        "employee_no": emp.employee_no,
        "name": emp.name,
        "gender": emp.gender.value if emp.gender else None,
        "birth_date": emp.birth_date,
        "phone": emp.phone,
        "email": emp.email,
        "hire_date": emp.hire_date,
        "employee_type": emp.employee_type.value if emp.employee_type else None,
        "employment_status": emp.employment_status.value if emp.employment_status else None,
        "position_number_id": emp.position_number_id,
        "position_number": pn.number if pn else None,
        "position_name": pn.position.name if pn else None,
        "company_id": pn.company_id if pn else None,
        "company_name": pn.company.name if pn else None,
        "solid_line_manager_id": pn.solid_line_manager_id if pn else None,
        "solid_line_number": sl.number if sl else None,
        "solid_line_manager_name": sl.position.name if sl else None,
        "dotted_manager_ids": dotted,
        "dotted_manager_numbers": dotted_nums,
        "remark": emp.remark,
        "created_at": emp.created_at,
        "updated_at": emp.updated_at,
    }


def _assert_attachable(db: Session, pn: PositionNumber):
    if pn.status not in (PositionStatus.OPEN, PositionStatus.VACANT, PositionStatus.OFFERED):
        raise HTTPException(400, f"岗位状态为 {pn.status.value}，仅 Open/Vacant/Offered 可挂编")
    if db.query(Employee).filter(Employee.position_number_id == pn.id).first():
        raise HTTPException(400, "岗位已被其他员工占用")


@router.get("/employees")
def list_employees(
    company_id: int | None = None,
    employee_type: str | None = None,
    employment_status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(Employee)
    if company_id:
        q = q.join(PositionNumber, Employee.position_number_id == PositionNumber.id).filter(
            PositionNumber.company_id == company_id
        )
    if employment_status:
        q = q.filter(Employee.employment_status == employment_status)
    if employee_type:
        q = q.filter(Employee.employee_type == employee_type)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter((Employee.name.like(like)) | (Employee.employee_no.like(like)))
    total = q.count()
    items = q.order_by(Employee.employee_no).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [serialize_employee(db, e) for e in items]}


@router.post("/employees", status_code=201)
def create_employee(payload: EmployeeCreate, response: Response, db: Session = Depends(get_db)):
    if db.query(Employee).filter(Employee.employee_no == payload.employee_no).first():
        raise HTTPException(400, f"工号已存在: {payload.employee_no}")
    pn = get_or_404(db, PositionNumber, payload.position_number_id, "岗位不存在")
    _assert_attachable(db, pn)
    emp = Employee(
        employee_no=payload.employee_no,
        name=payload.name,
        gender=payload.gender,
        birth_date=payload.birth_date,
        phone=payload.phone,
        email=payload.email,
        hire_date=payload.hire_date,
        employee_type=payload.employee_type,
        employment_status=payload.employment_status,
        position_number_id=pn.id,
        remark=payload.remark,
    )
    db.add(emp)
    db.flush()
    lifecycle.transition(db, pn, PositionStatus.FILLED, note=f"员工 {payload.name} 入职挂编",
                         employee_id=emp.id, system=True)
    db.commit()
    response.headers["Location"] = f"/api/v1/employees/{emp.id}"
    return serialize_employee(db, emp)


@router.get("/employees/{eid}")
def get_employee(eid: int, db: Session = Depends(get_db)):
    emp = get_or_404(db, Employee, eid, "员工不存在")
    return serialize_employee(db, emp)


@router.patch("/employees/{eid}")
def update_employee(eid: int, payload: EmployeeUpdate, db: Session = Depends(get_db)):
    emp = get_or_404(db, Employee, eid, "员工不存在")
    for field in ("name", "gender", "birth_date", "phone", "email",
                  "hire_date", "employee_type", "remark"):
        val = getattr(payload, field)
        if val is not None:
            setattr(emp, field, val)
    if payload.employment_status is not None:
        if (payload.employment_status == EmploymentStatus.TERMINATED
                and emp.employment_status != EmploymentStatus.TERMINATED):
            _vacate(db, emp, f"员工 {emp.name} 离职")
        emp.employment_status = payload.employment_status
    db.commit()
    return serialize_employee(db, emp)


@router.delete("/employees/{eid}")
def delete_employee(eid: int, db: Session = Depends(get_db)):
    emp = get_or_404(db, Employee, eid, "员工不存在")
    if emp.position_number_id is not None or emp.employment_status != EmploymentStatus.TERMINATED:
        raise HTTPException(400, "仅可删除已离职且已解绑岗位的员工档案")
    db.delete(emp)
    db.commit()
    return {"ok": True, "id": eid}


def _vacate(db: Session, emp: Employee, note: str):
    """解绑员工岗位，岗位转 Vacant。"""
    pn = db.get(PositionNumber, emp.position_number_id) if emp.position_number_id else None
    if pn and pn.status == PositionStatus.FILLED:
        lifecycle.transition(db, pn, PositionStatus.VACANT, note=note, employee_id=emp.id, system=True)
    emp.position_number_id = None
