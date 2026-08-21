"""岗位/职位/公司/国家 路由。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app import lifecycle
from app.db import get_db
from app.helpers import (
    assert_version,
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
    CostMode,
    Country,
    Employee,
    EmploymentTaxItem,
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

router = APIRouter(prefix="/api/v1", tags=["positions"])


def _assert_management(db: Session, mgr: PositionNumber, role: str):
    """直线/虚线经理必须是管理岗（级别 M 开头）。"""
    if not mgr.level or not mgr.level.upper().startswith("M"):
        raise HTTPException(
            400, f"{role}必须是管理岗（级别以 M 开头），岗位 {mgr.number}（级别 {mgr.level or '未设置'}）不可作为经理"
        )


# ---------------------------------------------------------------- 基础字典
@router.get("/position-functions", response_model=list[PositionFunctionOut])
def list_functions(db: Session = Depends(get_db)):
    return db.query(Position).order_by(Position.name).all()


@router.post("/position-functions", response_model=PositionFunctionOut, status_code=201)
def create_function(payload: PositionFunctionCreate, response: Response, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "职位名称不能为空")
    if db.query(Position).filter(Position.name == name).first():
        raise HTTPException(400, f"职位已存在: {name}")
    pos = Position(name=name)
    db.add(pos)
    db.commit()
    response.headers["Location"] = f"/api/v1/position-functions/{pos.id}"
    return pos


# ---------------------------------------------------------------- 岗位编号
@router.get("/positions")
def list_positions(
    company_id: int | None = None,
    scope: Scope | None = None,
    status: PositionStatus | None = None,
    search: str | None = None,
    role: str | None = None,
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
    if role == "manager":
        q = q.filter(PositionNumber.level.like("M%"))
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


@router.post("/positions", status_code=201)
def create_position(payload: PositionNumberCreate, response: Response, db: Session = Depends(get_db)):
    position = resolve_position(db, payload.position_id, payload.position_name)
    company = get_or_404(db, Company, payload.company_id, "隶属公司不存在")
    country = None
    if payload.scope == Scope.COUNTRY:
        if not payload.country_id:
            raise HTTPException(400, "Country 范围必须选择国家/地区")
        country = get_or_404(db, Country, payload.country_id, "国家/地区不存在")

    number = generate_number(db, payload.scope, country.code if country else None)
    validate_number_format(number, payload.scope, country.code if country else None)
    if db.query(PositionNumber).filter(PositionNumber.number == number).first():
        raise HTTPException(400, f"岗位编号已存在: {number}")

    if payload.solid_line_manager_id:
        mgr = get_or_404(db, PositionNumber, payload.solid_line_manager_id, "直线经理岗位不存在")
        _assert_management(db, mgr, "直线经理")
    for mid in dict.fromkeys(payload.dotted_manager_ids):
        mgr = get_or_404(db, PositionNumber, mid, f"虚线经理岗位不存在 (id={mid})")
        _assert_management(db, mgr, "虚线经理")

    pn = PositionNumber(
        number=number,
        position_id=position.id,
        company_id=company.id,
        level=payload.level,
        scope=payload.scope,
        country_id=country.id if country else None,
        position_type=payload.position_type,
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
        cost_mode=payload.cost_mode or CostMode.MANUAL,
        salary_before_tax=payload.salary_before_tax,
        company_share=payload.company_share,
        labor_cost=payload.labor_cost,
    )
    db.add(pn)
    db.flush()
    if payload.solid_line_manager_id:
        check_cycle(db, pn.id, payload.solid_line_manager_id)
    set_dotted_lines(db, pn.id, payload.dotted_manager_ids)
    db.add(PositionEvent(position_number_id=pn.id, from_status=None,
                         to_status=PositionStatus.PLANNED.value, note="岗位建档"))
    db.commit()
    response.headers["Location"] = f"/api/v1/positions/{pn.id}"
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


@router.patch("/positions/{pid}")
def update_position(pid: int, payload: PositionNumberUpdate, db: Session = Depends(get_db)):
    pn = get_or_404(db, PositionNumber, pid, "岗位不存在")
    assert_version(pn, payload.version, "岗位")
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
                  "prev_position_id", "prev_company_id", "remark", "position_type"):
        val = getattr(payload, field)
        if val is not None:
            setattr(pn, field, val)

    for field in ("cost_mode", "salary_before_tax", "company_share", "labor_cost"):
        val = getattr(payload, field)
        if val is not None:
            setattr(pn, field, val)

    if payload.solid_line_manager_id is not None:
        if payload.solid_line_manager_id:
            mgr = get_or_404(db, PositionNumber, payload.solid_line_manager_id, "直线经理岗位不存在")
            _assert_management(db, mgr, "直线经理")
            check_cycle(db, pn.id, payload.solid_line_manager_id)
        pn.solid_line_manager_id = payload.solid_line_manager_id

    if payload.dotted_manager_ids is not None:
        for mid in payload.dotted_manager_ids:
            if mid:
                _assert_management(db, get_or_404(db, PositionNumber, mid, f"虚线经理岗位不存在 (id={mid})"), "虚线经理")
        set_dotted_lines(db, pn.id, payload.dotted_manager_ids)

    pn.version = (pn.version or 1) + 1
    db.commit()
    return serialize_position(db, pn)


@router.post("/positions/{pid}/transitions", status_code=201)
def transition_position(pid: int, payload: TransitionRequest, response: Response, db: Session = Depends(get_db)):
    """创建一条生命周期流转事件（会同步变更岗位状态）。"""
    pn = get_or_404(db, PositionNumber, pid, "岗位不存在")
    try:
        event = lifecycle.transition(db, pn, payload.to_status, note=payload.note)
        db.commit()
    except lifecycle.LifecycleError as e:
        raise HTTPException(422, str(e))
    response.headers["Location"] = f"/api/v1/positions/{pid}/transitions/{event.id}"
    return {"id": event.id, "position_number_id": pid, "from_status": event.from_status,
            "to_status": event.to_status, "changed_at": event.changed_at, "note": event.note}


@router.get("/positions/{pid}/transitions")
def list_position_transitions(pid: int, db: Session = Depends(get_db)):
    """列出岗位的生命周期流转事件。"""
    get_or_404(db, PositionNumber, pid, "岗位不存在")
    events = (
        db.query(PositionEvent)
        .filter(PositionEvent.position_number_id == pid)
        .order_by(PositionEvent.changed_at.desc(), PositionEvent.id.desc())
        .all()
    )
    return [
        {"id": e.id, "position_number_id": e.position_number_id, "from_status": e.from_status,
         "to_status": e.to_status, "changed_at": e.changed_at, "note": e.note, "employee_id": e.employee_id}
        for e in events
    ]


@router.get("/transitions")
def list_transitions(position_id: int | None = None, db: Session = Depends(get_db)):
    """全局流转事件列表（可按岗位过滤）。"""
    q = db.query(PositionEvent)
    if position_id:
        q = q.filter(PositionEvent.position_number_id == position_id)
    events = q.order_by(PositionEvent.changed_at.desc(), PositionEvent.id.desc()).all()
    return [
        {"id": e.id, "position_number_id": e.position_number_id, "from_status": e.from_status,
         "to_status": e.to_status, "changed_at": e.changed_at, "note": e.note, "employee_id": e.employee_id}
        for e in events
    ]


@router.get("/positions/{pid}/cost-calculation")
def get_position_cost(pid: int, db: Session = Depends(get_db)):
    """按国家用工税额计算公司份额/用工成本（只读，不落库）。

    公司份额 = 税前薪资 × Σ(有效科目税率)；用工成本 = 税前薪资 + 公司份额。
    """
    pn = get_or_404(db, PositionNumber, pid, "岗位不存在")
    if pn.cost_mode != CostMode.AUTO:
        raise HTTPException(400, "仅「自动计算」模式可重算（当前为手动输入）")
    if pn.salary_before_tax is None:
        raise HTTPException(400, "请先填写税前薪资（人工）")
    if not pn.country_id:
        raise HTTPException(400, "该岗位无国家/地区，无法按国家税率计算（请切换为手动输入）")
    items = db.query(EmploymentTaxItem).filter(
        EmploymentTaxItem.country_id == pn.country_id, EmploymentTaxItem.is_active.is_(True)
    ).all()
    rate = sum(float(it.tax_rate or 0) for it in items) / 100.0
    share = round(float(pn.salary_before_tax) * rate, 2)
    return {
        "position_id": pn.id,
        "salary_before_tax": float(pn.salary_before_tax),
        "tax_rate_total": round(rate * 100, 2),
        "tax_items": [{"item_name": it.item_name, "tax_rate": float(it.tax_rate or 0)} for it in items],
        "company_share": share,
        "labor_cost": round(float(pn.salary_before_tax) + share, 2),
    }


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
