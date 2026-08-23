"""岗位/职位/公司/国家 路由。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app import lifecycle
from app.auth import get_current_user
from app.db import get_db
from app.helpers import (
    assert_can_write_company,
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
    LegalCategoryDef,
    Level,
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


def _assert_level(db: Session, level: str | None):
    """岗位级别须在 levels 字典（PRD §3.6 / §4 F0）；空值允许留空。"""
    if level and not db.query(Level).filter(Level.code == level).first():
        raise HTTPException(400, f"级别「{level}」不存在，请先在主数据中维护")


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
def create_position(payload: PositionNumberCreate, response: Response,
                    user=Depends(get_current_user), db: Session = Depends(get_db)):
    position = resolve_position(db, payload.position_id, payload.position_name)
    company = get_or_404(db, Company, payload.company_id, "隶属公司不存在")
    level_val = (payload.level or "").strip() or None
    _assert_level(db, level_val)
    country = None
    if payload.scope == Scope.COUNTRY:
        if not payload.country_id:
            raise HTTPException(400, "Country 范围必须选择国家/地区")
        country = get_or_404(db, Country, payload.country_id, "国家/地区不存在")

    number = generate_number(db, payload.position_type)
    validate_number_format(number)
    if db.query(PositionNumber).filter(PositionNumber.number == number).first():
        raise HTTPException(400, f"岗位编号已存在: {number}")

    if payload.solid_line_manager_id:
        mgr = get_or_404(db, PositionNumber, payload.solid_line_manager_id, "直线经理岗位不存在")
        _assert_management(db, mgr, "直线经理")
    for mid in dict.fromkeys(payload.dotted_manager_ids):
        mgr = get_or_404(db, PositionNumber, mid, f"虚线经理岗位不存在 (id={mid})")
        _assert_management(db, mgr, "虚线经理")

    # DESIGN §4.1：legal_category 为字符串，校验来自 LegalCategoryDef 字典（issue #2）
    if payload.legal_category:
        if not db.query(LegalCategoryDef).filter(LegalCategoryDef.name == payload.legal_category).first():
            raise HTTPException(400, f"法律强制/可选「{payload.legal_category}」不存在，请先在主数据中添加")

    pn = PositionNumber(
        number=number,
        position_id=position.id,
        company_id=company.id,
        level=level_val,
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
    dotted_list = [
        {"id": mid, "label": payload.dotted_manager_labels[i] if i < len(payload.dotted_manager_labels) else None}
        for i, mid in enumerate(payload.dotted_manager_ids)
    ]
    set_dotted_lines(db, pn.id, dotted_list)
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
def update_position(pid: int, payload: PositionNumberUpdate,
                    user=Depends(get_current_user), db: Session = Depends(get_db)):
    pn = get_or_404(db, PositionNumber, pid, "岗位不存在")
    assert_version(pn, payload.version, "岗位")
    # 行级隔离（PRD §7B.3）：岗位全局可读、hr 可维护；但成本字段按实体隔离
    cost_fields_touched = any(
        getattr(payload, f) is not None
        for f in ("cost_mode", "salary_before_tax", "company_share", "labor_cost")
    )
    if cost_fields_touched:
        assert_can_write_company(db, user, pn.company_id, label="该岗位的成本字段")
    if payload.position_id is not None:
        pn.position_id = payload.position_id
    if payload.company_id is not None:
        get_or_404(db, Company, payload.company_id, "隶属公司不存在")
        pn.company_id = payload.company_id

    # scope / country 变化（编号为系统分配的纯序号，与范围解耦）
    new_scope = payload.scope if payload.scope is not None else pn.scope
    new_country = pn.country
    if payload.country_id is not None:
        new_country = get_or_404(db, Country, payload.country_id, "国家/地区不存在")
    if payload.scope is not None or payload.country_id is not None:
        if new_scope == Scope.COUNTRY and new_country is None:
            raise HTTPException(400, "Country 范围必须选择国家/地区")
        pn.scope = new_scope
        pn.country_id = new_country.id if new_country else None

    # 校验 legal_category 字典存在性（issue #2）
    if payload.legal_category is not None and payload.legal_category:
        if not db.query(LegalCategoryDef).filter(LegalCategoryDef.name == payload.legal_category).first():
            raise HTTPException(400, f"法律强制/可选「{payload.legal_category}」不存在，请先在主数据中添加")

    old_position_type = pn.position_type
    if payload.level is not None:
        level_upd = payload.level.strip()
        _assert_level(db, level_upd)
        pn.level = level_upd or None
    for field in ("opening_date", "closing_date", "work_location",
                  "job_responsibility", "legal_category", "org_chart_display",
                  "prev_position_id", "prev_company_id", "remark", "position_type"):
        val = getattr(payload, field)
        if val is not None:
            setattr(pn, field, val)

    # 挂编联动（PRD F1.5）：岗位类型变更时校验当前占用员工合同属性，防「四不像」数据
    if payload.position_type is not None and payload.position_type != (old_position_type or "").strip():
        from app.routers.employees import ATTACH_TYPE_MAP, _assert_type_match
        incumbent = db.query(Employee).filter(Employee.position_number_id == pn.id).first()
        if incumbent and (pn.position_type or "").strip() in ATTACH_TYPE_MAP:
            _assert_type_match(pn, incumbent.employee_type)

    for field in ("cost_mode", "salary_before_tax", "company_share", "labor_cost"):
        val = getattr(payload, field)
        if val is not None:
            setattr(pn, field, val)

    if payload.solid_line_manager_id is not None:
        if payload.solid_line_manager_id:
            mgr = get_or_404(db, PositionNumber, payload.solid_line_manager_id, "直线经理岗位不存在")
            _assert_management(db, mgr, "直线经理")
            check_cycle(db, pn.id, payload.solid_line_manager_id)
            # 汇报接线权限（v2.3）：由被汇报目标岗位的操作者维护
            assert_can_write_company(db, user, mgr.company_id, label="被汇报目标岗位所属公司")
        pn.solid_line_manager_id = payload.solid_line_manager_id

    if payload.dotted_manager_ids is not None:
        for mid in payload.dotted_manager_ids:
            if mid:
                mgr = get_or_404(db, PositionNumber, mid, f"虚线经理岗位不存在 (id={mid})")
                _assert_management(db, mgr, "虚线经理")
                assert_can_write_company(db, user, mgr.company_id, label="被汇报目标岗位所属公司")
        dotted_list = [
            {"id": mid, "label": payload.dotted_manager_labels[i] if payload.dotted_manager_labels and i < len(payload.dotted_manager_labels) else None}
            for i, mid in enumerate(payload.dotted_manager_ids)
        ]
        set_dotted_lines(db, pn.id, dotted_list)

    pn.version = (pn.version or 1) + 1
    db.commit()
    return serialize_position(db, pn)


@router.post("/positions/{pid}/transitions", status_code=201)
def transition_position(pid: int, payload: TransitionRequest, response: Response,
                        user=Depends(get_current_user), db: Session = Depends(get_db)):
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
def get_position_cost(pid: int, salary_before_tax: float | None = None,
                      scope: str = "budget", db: Session = Depends(get_db)):
    """成本测算（v2.3 双口径，只读不落库）。

    - scope=budget（默认）：按**岗位税区**（工作地点）计算预算成本，空岗可用；
    - scope=actual：按当前占用员工的**归属税区**计算实际成本（跟人走），需 Filled；
    - 未配置税率 → configured=false +「未配置」提示，不猜测估值。
    """
    from app.helpers import calc_cost_by_zone, resolve_tax_zone
    pn = get_or_404(db, PositionNumber, pid, "岗位不存在")
    if scope not in ("budget", "actual"):
        raise HTTPException(400, "scope 仅支持 budget / actual")

    if scope == "actual":
        emp = db.query(Employee).filter(Employee.position_number_id == pn.id).first()
        if not emp:
            raise HTTPException(400, "该岗位无在职员工，实际成本不可用（请用 scope=budget 查看预算口径）")
        # 人的归属税区 = 其当前所挂岗位的工作地点
        salary = salary_before_tax if salary_before_tax is not None else emp.actual_salary_before_tax
        zone = resolve_tax_zone(db, pn.work_location)
        result = calc_cost_by_zone(db, zone, salary)
        result.update({"position_id": pn.id, "scope": "actual", "employee_id": emp.id})
        return result

    # budget：岗位预算口径（空岗可录、不随人走）
    salary = salary_before_tax if salary_before_tax is not None else pn.salary_before_tax
    if salary is None:
        raise HTTPException(400, "请先填写税前薪资（人工）")
    zone = resolve_tax_zone(db, pn.work_location)
    result = calc_cost_by_zone(db, zone, salary)
    result.update({"position_id": pn.id, "scope": "budget"})
    return result


@router.delete("/positions/{pid}")
def delete_position(pid: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    pn = get_or_404(db, PositionNumber, pid, "岗位不存在")
    if db.query(Employee).filter(Employee.position_number_id == pid).first():
        raise HTTPException(400, "岗位有在职员工，禁止删除（请先离职/调岗）")
    if db.query(PositionEvent).filter(PositionEvent.position_number_id == pid).first():
        raise HTTPException(400, "岗位已有生命周期事件，禁止删除（可关闭岗位）")
    db.delete(pn)
    db.commit()
    return {"ok": True, "id": pid}
