"""岗位/职位/公司/国家 路由。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import lifecycle
from app.db import get_db
from app.helpers import (
    check_cycle,
    dotted_ids,
    generate_number,
    get_or_404,
    resolve_position,
    serialize_position,
    set_dotted_lines,
    validate_number_format,
)
from app.models import (
    Company,
    Country,
    Employee,
    Position,
    PositionEvent,
    PositionNumber,
    PositionStatus,
    Scope,
)
from app.schemas import (
    CompanyOut,
    CountryOut,
    PositionFunctionCreate,
    PositionFunctionOut,
    PositionNumberCreate,
    PositionNumberUpdate,
    TransitionRequest,
)

router = APIRouter(prefix="/api", tags=["positions"])


# ---------------------------------------------------------------- 基础字典
@router.get("/companies", response_model=list[CompanyOut])
def list_companies(db: Session = Depends(get_db)):
    return db.query(Company).order_by(Company.name).all()


@router.get("/countries", response_model=list[CountryOut])
def list_countries(db: Session = Depends(get_db)):
    return db.query(Country).order_by(Country.code).all()


@router.get("/position-functions", response_model=list[PositionFunctionOut])
def list_functions(db: Session = Depends(get_db)):
    return db.query(Position).order_by(Position.name).all()


@router.post("/position-functions", response_model=PositionFunctionOut)
def create_function(payload: PositionFunctionCreate, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "职位名称不能为空")
    if db.query(Position).filter(Position.name == name).first():
        raise HTTPException(400, f"职位已存在: {name}")
    pos = Position(name=name)
    db.add(pos)
    db.commit()
    return pos


# ---------------------------------------------------------------- 岗位编号
@router.get("/positions")
def list_positions(
    company_id: int | None = None,
    scope: Scope | None = None,
    status: PositionStatus | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(PositionNumber)
    if company_id:
        q = q.filter(PositionNumber.company_id == company_id)
    if scope:
        q = q.filter(PositionNumber.scope == scope)
    if status:
        q = q.filter(PositionNumber.status == status)
    if search:
        like = f"%{search.strip()}%"
        q = q.join(Position).filter(
            (PositionNumber.number.like(like))
            | (Position.name.like(like))
            | (PositionNumber.org_chart_display.like(like))
        )
    total = q.count()
    items = (
        q.order_by(PositionNumber.number)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"total": total, "page": page, "page_size": page_size,
            "items": [serialize_position(db, pn) for pn in items]}


@router.post("/positions")
def create_position(payload: PositionNumberCreate, db: Session = Depends(get_db)):
    position = resolve_position(db, payload.position_id, payload.position_name)
    company = get_or_404(db, Company, payload.company_id, "隶属公司不存在")
    country = None
    if payload.scope == Scope.COUNTRY:
        if not payload.country_id:
            raise HTTPException(400, "Country 范围必须选择国家/地区")
        country = get_or_404(db, Country, payload.country_id, "国家/地区不存在")

    number = payload.number.strip() if payload.number else generate_number(
        db, payload.scope, country.code if country else None
    )
    validate_number_format(number, payload.scope, country.code if country else None)
    if db.query(PositionNumber).filter(PositionNumber.number == number).first():
        raise HTTPException(400, f"岗位编号已存在: {number}")

    if payload.solid_line_manager_id:
        get_or_404(db, PositionNumber, payload.solid_line_manager_id, "直线经理岗位不存在")
    for mid in dict.fromkeys(payload.dotted_manager_ids):
        get_or_404(db, PositionNumber, mid, f"虚线经理岗位不存在 (id={mid})")

    pn = PositionNumber(
        number=number,
        position_id=position.id,
        company_id=company.id,
        level=payload.level,
        scope=payload.scope,
        country_id=country.id if country else None,
        opening_date=payload.opening_date,
        closing_date=payload.closing_date,
        work_location=payload.work_location,
        job_responsibility=payload.job_responsibility,
        legal_category=payload.legal_category,
        solid_line_manager_id=payload.solid_line_manager_id,
        org_chart_display=payload.org_chart_display,
        prev_position_id=payload.prev_position_id,
        prev_company_id=payload.prev_company_id,
        remark=payload.remark,
        status=PositionStatus.PLANNED,
    )
    db.add(pn)
    db.flush()
    if payload.solid_line_manager_id:
        check_cycle(db, pn.id, payload.solid_line_manager_id)
    set_dotted_lines(db, pn.id, payload.dotted_manager_ids)
    db.add(PositionEvent(position_number_id=pn.id, from_status=None,
                         to_status=PositionStatus.PLANNED.value, note="岗位建档"))
    db.commit()
    return serialize_position(db, pn)


@router.get("/positions/{pid}")
def get_position(pid: int, db: Session = Depends(get_db)):
    pn = get_or_404(db, PositionNumber, pid, "岗位不存在")
    events = (
        db.query(PositionEvent)
        .filter(PositionEvent.position_number_id == pid)
        .order_by(PositionEvent.changed_at.desc(), PositionEvent.id.desc())
        .all()
    )
    data = serialize_position(db, pn)
    data["events"] = [
        {
            "id": e.id, "from_status": e.from_status, "to_status": e.to_status,
            "changed_at": e.changed_at, "note": e.note, "employee_id": e.employee_id,
        }
        for e in events
    ]
    return data


@router.put("/positions/{pid}")
def update_position(pid: int, payload: PositionNumberUpdate, db: Session = Depends(get_db)):
    pn = get_or_404(db, PositionNumber, pid, "岗位不存在")
    if payload.position_id is not None:
        pn.position_id = payload.position_id
    if payload.company_id is not None:
        get_or_404(db, Company, payload.company_id, "隶属公司不存在")
        pn.company_id = payload.company_id

    # scope / country 变化需保持编号一致
    new_scope = payload.scope if payload.scope is not None else pn.scope
    new_country = pn.country
    if payload.country_id is not None:
        new_country = get_or_404(db, Country, payload.country_id, "国家/地区不存在")
    if payload.scope is not None or payload.country_id is not None:
        if new_scope == Scope.COUNTRY and new_country is None:
            raise HTTPException(400, "Country 范围必须选择国家/地区")
        validate_number_format(pn.number, new_scope, new_country.code if new_country else None)
        pn.scope = new_scope
        pn.country_id = new_country.id if new_country else None

    for field in ("level", "opening_date", "closing_date", "work_location",
                  "job_responsibility", "legal_category", "org_chart_display",
                  "prev_position_id", "prev_company_id", "remark"):
        val = getattr(payload, field)
        if val is not None:
            setattr(pn, field, val)

    if payload.solid_line_manager_id is not None:
        if payload.solid_line_manager_id:
            get_or_404(db, PositionNumber, payload.solid_line_manager_id, "直线经理岗位不存在")
            check_cycle(db, pn.id, payload.solid_line_manager_id)
        pn.solid_line_manager_id = payload.solid_line_manager_id

    if payload.dotted_manager_ids is not None:
        set_dotted_lines(db, pn.id, payload.dotted_manager_ids)

    db.commit()
    return serialize_position(db, pn)


@router.post("/positions/{pid}/transition")
def transition_position(pid: int, payload: TransitionRequest, db: Session = Depends(get_db)):
    pn = get_or_404(db, PositionNumber, pid, "岗位不存在")
    try:
        lifecycle.transition(db, pn, payload.to_status, note=payload.note)
        db.commit()
    except lifecycle.LifecycleError as e:
        raise HTTPException(422, str(e))
    return serialize_position(db, pn)


@router.delete("/positions/{pid}")
def delete_position(pid: int, db: Session = Depends(get_db)):
    pn = get_or_404(db, PositionNumber, pid, "岗位不存在")
    if db.query(Employee).filter(Employee.position_number_id == pid).first():
        raise HTTPException(400, "岗位有在职员工，禁止删除（请先离职/调岗）")
    if db.query(PositionEvent).filter(PositionEvent.position_number_id == pid).first():
        raise HTTPException(400, "岗位已有生命周期事件，禁止删除（可关闭岗位）")
    db.delete(pn)
    db.commit()
    return {"ok": True, "id": pid}
