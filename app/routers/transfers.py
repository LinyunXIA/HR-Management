"""转调资源（v2.3 F1.5b，人永不脱岗）：

- POST /transfers/initiate      原 HR 把人转出到目标公司（人仍挂原岗、原岗锁定）
- GET  /transfers               记录列表；?pool=1 查看待认领池（按目标公司过滤）
- POST /transfers/{id}/claim    目标公司 HR 认领 + 分配空闲目标岗（单事务）
- POST /transfers/{id}/reject   目标公司 HR 拒绝 → 退回原公司、原岗继续
- POST /transfers               兼容旧直调接口（同公司调岗）

事务原子性：认领/拒绝等一次动作改变多状态的操作全部包进单个 commit，
冲突由乐观锁/行锁保证，不产生「一人双岗」脏窗口（DESIGN §5）。
"""
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app import lifecycle
from app.auth import get_current_user
from app.db import get_db
from app.helpers import (
    ALL_COMPANIES,
    assert_can_write_company,
    get_operable_company_ids,
    get_or_404,
)
from app.models import (
    Company,
    Employee,
    EmploymentStatus,
    PositionNumber,
    PositionStatus,
    Transfer,
)
from app.routers.employees import _assert_attachable, _assert_type_match, serialize_employee
from app.schemas import TransferClaim, TransferCreate, TransferInitiate

router = APIRouter(prefix="/api/v1", tags=["transfers"])


def serialize_transfer(db: Session, t: Transfer) -> dict:
    emp = db.get(Employee, t.employee_id)
    fp = db.get(PositionNumber, t.from_position_id) if t.from_position_id else None
    tp = db.get(PositionNumber, t.to_position_id) if t.to_position_id else None
    tc = db.get(Company, t.target_company_id)
    return {
        "id": t.id,
        "employee_id": t.employee_id,
        "employee_name": emp.name if emp else None,
        "kind": t.kind,
        "status": t.status,
        "timing": t.timing,
        "from_position_id": t.from_position_id,
        "from_position_number": fp.number if fp else None,
        "from_company_id": fp.company_id if fp else None,
        "to_position_id": t.to_position_id,
        "to_position_number": tp.number if tp else None,
        "target_company_id": t.target_company_id,
        "target_company_name": tc.name if tc else None,
        "initiated_by": t.initiated_by,
        "claimed_by": t.claimed_by,
        "note": t.note,
        "created_at": t.created_at,
        "claimed_at": t.claimed_at,
    }


def _lock_employee(db: Session, employee_id: int) -> Employee:
    """行锁加载员工（防并发抢人）。"""
    emp = (
        db.query(Employee)
        .filter(Employee.id == employee_id)
        .with_for_update()
        .first()
    )
    if not emp:
        raise HTTPException(404, f"员工不存在 (id={employee_id})")
    return emp


def _load_transfer_for_update(db: Session, transfer_id: int) -> Transfer:
    t = db.query(Transfer).filter(Transfer.id == transfer_id).with_for_update().first()
    if not t:
        raise HTTPException(404, f"转调记录不存在 (id={transfer_id})")
    return t


@router.post("/transfers/initiate", status_code=201)
def initiate_transfer(payload: TransferInitiate, response: Response,
                      user=Depends(get_current_user), db: Session = Depends(get_db)):
    """转调发起：员工标记「转调中」+ target_company_id；原岗保持 Filled 锁定不释放。"""
    emp = _lock_employee(db, payload.employee_id)
    if emp.employment_status == EmploymentStatus.TERMINATED:
        raise HTTPException(400, "离职员工不可发起转调")
    if emp.employment_status == EmploymentStatus.TRANSFERRING:
        raise HTTPException(400, "该员工已在转调流程中")
    if not emp.position_number_id:
        raise HTTPException(400, "员工未挂岗，无法发起转调")
    target = get_or_404(db, Company, payload.target_company_id, "目标公司不存在")

    from_pn = db.get(PositionNumber, emp.position_number_id)
    # 写权限：仅原公司可管 HR 可发起（读可跨司、写按实体）
    assert_can_write_company(db, user, from_pn.company_id, label="该员工的所属公司")
    if from_pn.status != PositionStatus.FILLED:
        raise HTTPException(400, f"原岗位状态为 {from_pn.status.value}，仅在职（Filled）可转调")
    if not target.is_active:
        raise HTTPException(400, "目标公司已停用")

    emp.employment_status = EmploymentStatus.TRANSFERRING
    emp.target_company_id = target.id
    emp.version = (emp.version or 1) + 1
    t = Transfer(
        employee_id=emp.id,
        from_position_id=from_pn.id,
        target_company_id=target.id,
        status="initiated",
        kind="transfer",
        initiated_by=user.username,
        note=payload.note,
    )
    db.add(t)
    db.flush()
    lifecycle.record_event(db, from_pn.id, PositionStatus.FILLED.value, PositionStatus.FILLED.value,
                           note=f"转调发起 → {target.name}（原岗锁定，等待对方 HR 认领）",
                           employee_id=emp.id)
    db.commit()
    response.headers["Location"] = f"/api/v1/transfers/{t.id}"
    return serialize_transfer(db, t)


@router.get("/transfers/pending")
def pending_pool(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """我的待认领池：hr 仅可见 target_company 落在其可管实体内的记录。"""
    allowed = get_operable_company_ids(db, user)
    q = db.query(Transfer).filter(Transfer.status == "initiated", Transfer.kind == "transfer")
    if allowed != ALL_COMPANIES:
        q = q.filter(Transfer.target_company_id.in_(allowed or {-1}))
    return {"total": q.count(), "items": [serialize_transfer(db, t) for t in
                                          q.order_by(Transfer.created_at.desc()).all()]}


@router.post("/transfers/{transfer_id}/claim", status_code=200)
def claim_transfer(transfer_id: int, payload: TransferClaim,
                   user=Depends(get_current_user), db: Session = Depends(get_db)):
    """转调认领（单事务）：目标岗 Filled + 原岗 Vacant + 人挂新岗 + prev_*。

    仅目标公司的可管 HR 可认领；行锁防并发抢同岗。
    """
    t = _load_transfer_for_update(db, transfer_id)
    if t.status != "initiated" or t.kind != "transfer":
        raise HTTPException(400, f"转调记录状态为 {t.status}，不可认领")
    # 权限：仅目标公司可管 HR
    allowed = get_operable_company_ids(db, user)
    if allowed != ALL_COMPANIES and t.target_company_id not in allowed:
        raise HTTPException(403, "仅目标公司的 HR 可认领此转调")

    emp = _lock_employee(db, t.employee_id)
    if emp.employment_status != EmploymentStatus.TRANSFERRING \
            or emp.target_company_id != t.target_company_id:
        raise HTTPException(409, "员工转调状态已变化，请刷新后重试")
    new_pn = (
        db.query(PositionNumber)
        .filter(PositionNumber.id == payload.to_position_id)
        .with_for_update()
        .first()
    )
    if not new_pn:
        raise HTTPException(404, "目标岗位不存在")
    if new_pn.company_id != t.target_company_id:
        raise HTTPException(400, "目标岗位不属于转调目标公司")
    # 空闲目标岗：Open / Vacant / Offered / Planned 均可分配（PRD F1.5b）
    if new_pn.status not in (PositionStatus.OPEN, PositionStatus.VACANT,
                             PositionStatus.OFFERED, PositionStatus.PLANNED):
        raise HTTPException(400, f"目标岗位状态为 {new_pn.status.value}，非空闲编制")
    if db.query(Employee).filter(Employee.position_number_id == new_pn.id).first():
        raise HTTPException(400, "目标岗位已被其他员工占用")
    # 挂编联动（#50）：目标岗类型须匹配员工合同属性（触发器兜底前先给 400）
    _assert_type_match(new_pn, emp.employee_type)

    old_pn = db.get(PositionNumber, emp.position_number_id)

    # ---- 单事务成对流转：任一步失败整体回滚 ----
    old_note = f"员工 {emp.name} 转调出岗 → {t.target_company.name}"
    if old_pn and old_pn.id != new_pn.id and old_pn.status == PositionStatus.FILLED:
        lifecycle.transition(db, old_pn, PositionStatus.VACANT, note=old_note,
                             employee_id=emp.id, system=True)
    new_note = f"员工 {emp.name} 转调入岗（来自 {old_pn.number if old_pn else '外部'}）"
    lifecycle.transition(db, new_pn, PositionStatus.FILLED, note=new_note,
                         employee_id=emp.id, system=True)
    # prev_* 记来源（识别锚在系统内维护）；工龄 hire_date 不动
    new_pn.prev_position_id = old_pn.id if old_pn else None
    new_pn.prev_company_id = old_pn.company_id if old_pn else None
    emp.position_number_id = new_pn.id
    emp.employment_status = EmploymentStatus.ACTIVE
    emp.target_company_id = None
    emp.version = (emp.version or 1) + 1

    t.status = "claimed"
    t.to_position_id = new_pn.id
    t.claimed_by = user.username
    from datetime import datetime as _dt
    t.claimed_at = _dt.now(timezone.utc)

    db.commit()
    return {"ok": True, "transfer": serialize_transfer(db, t),
            "employee": serialize_employee(db, emp)}


@router.post("/transfers/{transfer_id}/reject")
def reject_transfer(transfer_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """转调退回（仅目标公司 HR）：退回原公司、原岗继续（人不脱岗）。"""
    t = _load_transfer_for_update(db, transfer_id)
    if t.status != "initiated":
        raise HTTPException(400, f"转调记录状态为 {t.status}，无需退回")
    allowed = get_operable_company_ids(db, user)
    is_target_hr = allowed == ALL_COMPANIES or t.target_company_id in allowed
    is_initiator = t.initiated_by == user.username
    if not (is_target_hr or is_initiator):
        raise HTTPException(403, "仅目标公司 HR 或发起人可退回")

    emp = _lock_employee(db, t.employee_id)
    if emp.employment_status == EmploymentStatus.TRANSFERRING:
        emp.employment_status = EmploymentStatus.ACTIVE
        emp.target_company_id = None
        emp.version = (emp.version or 1) + 1
    t.status = "rejected"
    t.claimed_by = user.username
    from datetime import datetime as _dt
    t.claimed_at = _dt.now(timezone.utc)
    from_pn = db.get(PositionNumber, t.from_position_id) if t.from_position_id else None
    if from_pn:
        lifecycle.record_event(db, from_pn.id, from_pn.status.value if from_pn.status else None,
                               from_pn.status.value if from_pn.status else "filled",
                               note=f"转调被退回（{user.username}），员工继续留任",
                               employee_id=emp.id)
    db.commit()
    return {"ok": True, "transfer": serialize_transfer(db, t)}


@router.post("/transfers", status_code=201)
def create_transfer(payload: TransferCreate, response: Response,
                    user=Depends(get_current_user), db: Session = Depends(get_db)):
    """调岗（同公司或跨公司直接调）：旧岗→Vacant，新岗→Filled。

    issue #140：外包虚拟建档员工（无在挂岗位）允许经本接口**首次挂编**——
    此前主体逻辑包在 `if old_pn and old_pn.id != new_pn.id` 内，
    无岗员工静默 no-op 却返回成功（目标岗不转 Filled、不写 transfers 记录）。
    现任何成功的调岗均写一条 kind='transfer' 结构化留痕。
    """
    emp = _lock_employee(db, payload.employee_id)
    if emp.employment_status == EmploymentStatus.TERMINATED:
        raise HTTPException(400, "离职员工不可调岗")
    if emp.employment_status == EmploymentStatus.TRANSFERRING:
        raise HTTPException(400, "转调中员工请先完成认领或退回")
    old_pn = db.get(PositionNumber, emp.position_number_id) if emp.position_number_id else None
    if old_pn:
        assert_can_write_company(db, user, old_pn.company_id, label="该员工的所属公司")
    new_pn = (
        db.query(PositionNumber)
        .filter(PositionNumber.id == payload.to_position_id)
        .with_for_update()
        .first()
    )
    if not new_pn:
        raise HTTPException(404, "目标岗位不存在")
    # 行级隔离补口（issue #147）：目标岗位公司的 status/事件写入须可管
    assert_can_write_company(db, user, new_pn.company_id, label="目标岗位所属公司")
    if old_pn and old_pn.id == new_pn.id:
        raise HTTPException(400, "调岗目标与当前岗位相同")
    _assert_attachable(db, new_pn)
    # 挂编联动（#50）：目标岗类型须匹配员工合同属性
    _assert_type_match(new_pn, emp.employee_type)

    from datetime import datetime as _dt
    if old_pn and old_pn.status == PositionStatus.FILLED:
        lifecycle.transition(db, old_pn, PositionStatus.VACANT,
                             note=f"员工 {emp.name} 调岗",
                             employee_id=emp.id, system=True)
    emp.position_number_id = new_pn.id
    emp.version = (emp.version or 1) + 1
    lifecycle.transition(db, new_pn, PositionStatus.FILLED,
                         note=f"员工 {emp.name} 调岗挂编" + ("（首次挂编）" if not old_pn else ""),
                         employee_id=emp.id, system=True)
    db.add(Transfer(
        employee_id=emp.id,
        from_position_id=old_pn.id if old_pn else None,
        target_company_id=new_pn.company_id,
        to_position_id=new_pn.id,
        status="claimed",
        kind="transfer",
        initiated_by=user.username,
        claimed_by=user.username,
        claimed_at=_dt.now(timezone.utc),
    ))
    db.commit()
    response.headers["Location"] = f"/api/v1/employees/{emp.id}"
    return serialize_employee(db, emp)


@router.get("/transfers")
def list_transfers(employee_id: int | None = None,
                   status_filter: str | None = Query(None, alias="status"),
                   user=Depends(get_current_user), db: Session = Depends(get_db)):
    """转调记录列表（可按员工/状态过滤；hr 仅见与其可管实体相关的记录）。"""
    allowed = get_operable_company_ids(db, user)
    q = db.query(Transfer)
    if employee_id:
        q = q.filter(Transfer.employee_id == employee_id)
    if status_filter:
        q = q.filter(Transfer.status == status_filter)
    if allowed != ALL_COMPANIES:
        visible_companies = allowed or {-1}
        q = q.join(PositionNumber, Transfer.from_position_id == PositionNumber.id, isouter=True).filter(
            (Transfer.target_company_id.in_(visible_companies))
            | (PositionNumber.company_id.in_(visible_companies))
        )
    items = q.order_by(Transfer.created_at.desc()).limit(500).all()
    return {"total": len(items), "items": [serialize_transfer(db, t) for t in items]}
