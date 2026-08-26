"""员工路由：CRUD + 入职/调岗/离职/升职（联动岗位状态 Filled↔Vacant）。

v2.3：
- 挂编联动：岗位 position_type ↔ 员工 employee_type 强制匹配（数据层兜底）
- 行级隔离：写操作按可管实体隔离（读可跨司，PRD §7B.3）
- 升职：POST /employees/{id}/promote，单事务成对流转
"""
from datetime import date as _date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import lifecycle
from app.auth import get_current_user
from app.db import get_db
from app.helpers import (
    ALL_COMPANIES,
    assert_can_write_company,
    assert_version,
    calc_cost_by_zone,
    dotted_ids,
    generate_employee_no,
    get_operable_company_ids,
    get_or_404,
)
from app.models import (
    Company,
    CostMode,
    Employee,
    EmployeeType,
    EmploymentStatus,
    PositionNumber,
    PositionNumberDottedLine,
    PositionStatus,
    Transfer,
)
from app.schemas import EmployeeCreate, EmployeeUpdate, PromoteRequest

router = APIRouter(prefix="/api/v1", tags=["employees"])

# 挂编联动映射（PRD §4 F1.5）：岗位 position_type → 允许的员工 employee_type 集合
ATTACH_TYPE_MAP = {
    "Consultant": {"正式"},
    "External Employee": {"外包"},
    "Employee": {"正式", "实习", "劳务"},
}


def _assert_type_match(pn: PositionNumber, employee_type) -> None:
    """岗位类型 ↔ 员工合同属性强制联动；不匹配挂载拒绝（防「四不像」数据）。"""
    allowed = ATTACH_TYPE_MAP.get((pn.position_type or "").strip())
    if allowed is None:
        return  # 岗位未设类型 → 不约束（导入历史数据兼容）
    et = employee_type.value if hasattr(employee_type, "value") else str(employee_type)
    if et not in allowed:
        type_cn = {"Consultant": "顾问编制", "External Employee": "外包编制",
                   "Employee": "正式编制"}.get(pn.position_type, pn.position_type)
        raise HTTPException(
            400, f"挂编联动校验失败：岗位为{type_cn}（{pn.position_type}），"
                 f"仅允许 {'/'.join(sorted(allowed))} 员工，不接受「{et}」")


# 跨司只读例外（PRD §7B.3 + issue #121 裁决 A）：组织图/岗位展示可见他司员工「姓名」；
# 列表/详情对他司行脱敏——联系方式（出生日期/手机/邮箱）与实际成本字段置 null，
# 姓名及岗位/公司归属保留；admin 全量。
REDACTED_EMPLOYEE_FIELDS = (
    "birth_date", "phone", "email",
    "actual_cost_mode",
    "actual_salary_before_tax", "actual_mandatory_tax", "actual_mandatory_fixed_fee",
    "actual_fixed_bonus", "actual_floating_bonus", "actual_labor_cost",
)


def serialize_employee(db: Session, emp: Employee, redact: bool = False) -> dict:
    pn = db.get(PositionNumber, emp.position_number_id) if emp.position_number_id else None
    sl = db.get(PositionNumber, pn.solid_line_manager_id) if pn and pn.solid_line_manager_id else None
    dotted = dotted_ids(db, pn.id) if pn else []
    dotted_nums = []
    for did in dotted:
        dm = db.get(PositionNumber, did)
        dotted_nums.append(dm.number if dm else str(did))
    tc = db.get(Company, emp.target_company_id) if emp.target_company_id else None
    data = {
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
        "target_company_id": emp.target_company_id,
        "target_company_name": tc.name if tc else None,
        # ---- 实际成本六栏（跟人走，v2.6）----
        "actual_cost_mode": emp.actual_cost_mode.value if emp.actual_cost_mode else None,
        "actual_salary_before_tax": float(emp.actual_salary_before_tax) if emp.actual_salary_before_tax is not None else None,
        "actual_mandatory_tax": float(emp.actual_mandatory_tax) if emp.actual_mandatory_tax is not None else None,
        "actual_mandatory_fixed_fee": float(emp.actual_mandatory_fixed_fee) if emp.actual_mandatory_fixed_fee is not None else None,
        "actual_fixed_bonus": float(emp.actual_fixed_bonus) if emp.actual_fixed_bonus is not None else None,
        "actual_floating_bonus": float(emp.actual_floating_bonus) if emp.actual_floating_bonus is not None else None,
        "actual_labor_cost": float(emp.actual_labor_cost) if emp.actual_labor_cost is not None else None,
        "solid_line_manager_id": pn.solid_line_manager_id if pn else None,
        "solid_line_number": sl.number if sl else None,
        "solid_line_manager_name": sl.position.name if sl else None,
        "dotted_manager_ids": dotted,
        "dotted_manager_numbers": dotted_nums,
        "remark": emp.remark,
        "version": emp.version,
        "created_at": emp.created_at,
        "updated_at": emp.updated_at,
    }
    if redact:
        for f in REDACTED_EMPLOYEE_FIELDS:
            data[f] = None
    return data


def _redact_for(db: Session, user, emp: Employee, data: dict) -> dict:
    """按操作者可管实体判定是否脱敏（#121 裁决 A）：admin 全量；hr 对非本实体行脱敏。"""
    allowed = get_operable_company_ids(db, user)
    if allowed == ALL_COMPANIES:
        return data
    pn = db.get(PositionNumber, emp.position_number_id) if emp.position_number_id else None
    company_id = pn.company_id if pn else None
    # 未挂岗（外包虚拟建档/已解绑）无实体归属 → 仅 admin 可见全量
    if company_id is None or company_id not in allowed:
        for f in REDACTED_EMPLOYEE_FIELDS:
            data[f] = None
    return data


def _assert_attachable(db: Session, pn: PositionNumber):
    if pn.status not in (PositionStatus.OPEN, PositionStatus.VACANT, PositionStatus.OFFERED):
        raise HTTPException(400, f"岗位状态为 {pn.status.value}，仅 Open/Vacant/Offered 可挂编")
    if db.query(Employee).filter(Employee.position_number_id == pn.id).first():
        raise HTTPException(400, "岗位已被其他员工占用")


@router.get("/employees")
def list_employees(
    company_id: int | None = None,
    # 枚举类型参数：FastAPI 按枚举「值」（中文）解析，SQLAlchemy 按存储「名」比较（与 positions 路由一致）
    employee_type: EmployeeType | None = None,
    employment_status: EmploymentStatus | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    _user=Depends(get_current_user),
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
            "items": [_redact_for(db, _user, e, serialize_employee(db, e)) for e in items]}


@router.post("/employees", status_code=201)
def create_employee(payload: EmployeeCreate, response: Response,
                    user=Depends(get_current_user), db: Session = Depends(get_db)):
    # 工号自动生成（v2.4.2）：正式 G00001 起、实习/劳务 V00001 起、外包 O00001 起；
    # 显式传入仍接受（API 兼容/数据迁移场景），重复时 400
    emp_no = (payload.employee_no or "").strip() or generate_employee_no(db, payload.employee_type)
    if db.query(Employee).filter(Employee.employee_no == emp_no).first():
        raise HTTPException(400, f"工号已存在: {emp_no}")
    # 转调旁路一致性（#119-3）：建档即「转调中」无 Transfer 记录，禁止；
    # 建档即「离职」又挂岗属矛盾组合（离职建档仅允许外包虚拟名单场景）
    if payload.employment_status == EmploymentStatus.TRANSFERRING:
        raise HTTPException(400, "「转调中」不可直接建档：请先建档后经 POST /transfers/initiate 发起")
    if payload.employment_status == EmploymentStatus.TERMINATED and payload.position_number_id is not None:
        raise HTTPException(400, "建档状态为「离职」时不可同时挂岗（离职档案应解绑）")
    # 挂岗规则（v2.4.2）：外包人员可不挂岗（虚拟建档，由外包公司管理）；其余类型必须挂岗
    pn = None
    if payload.position_number_id is not None:
        pn = get_or_404(db, PositionNumber, payload.position_number_id, "岗位不存在")
        _assert_attachable(db, pn)
        _assert_type_match(pn, payload.employee_type)
        # 行级隔离：入职挂编写入目标岗位所属实体，需可管
        assert_can_write_company(db, user, pn.company_id, label="目标岗位所属公司")
    elif payload.employee_type != EmployeeType.OUTSOURCED:
        raise HTTPException(400, "该员工类型必须挂编岗位（仅外包人员可虚拟建档不挂岗）")
    emp = Employee(
        employee_no=emp_no,
        name=payload.name,
        gender=payload.gender,
        birth_date=payload.birth_date,
        phone=payload.phone,
        email=payload.email,
        hire_date=payload.hire_date,
        employee_type=payload.employee_type,
        employment_status=payload.employment_status,
        position_number_id=pn.id if pn else None,
        remark=payload.remark,
    )
    db.add(emp)
    db.flush()
    if pn:
        lifecycle.transition(db, pn, PositionStatus.FILLED, note=f"员工 {payload.name} 入职挂编",
                             employee_id=emp.id, system=True)
    db.commit()
    response.headers["Location"] = f"/api/v1/employees/{emp.id}"
    return serialize_employee(db, emp)


@router.get("/employees/{eid}")
def get_employee(eid: int, _user=Depends(get_current_user), db: Session = Depends(get_db)):
    emp = get_or_404(db, Employee, eid, "员工不存在")
    return _redact_for(db, _user, emp, serialize_employee(db, emp))


@router.get("/employees/{eid}/cost-calculation")
def employee_cost_calculation(eid: int,
                              salary_before_tax: float | None = None,
                              fixed_bonus: float | None = None,
                              floating_bonus: float | None = None,
                              _user=Depends(get_current_user),
                              db: Session = Depends(get_db)):
    """员工实际成本测算（API.md §6.2，issue #111 补实现）。

    - 税区来源（v2.6 R1）= 员工当前所挂岗位所属公司的绑定税区（跟人走）；
    - salary_before_tax / fixed_bonus / floating_bonus 可传表单未落库值试算；
    - 未挂岗（外包虚拟建档/已解绑）→ 400；税前缺失 → 400（#115 口径对称，不按 0 静默计算）。
    """
    emp = get_or_404(db, Employee, eid, "员工不存在")
    pn = db.get(PositionNumber, emp.position_number_id) if emp.position_number_id else None
    if not pn:
        raise HTTPException(400, "员工未挂岗，无法按公司税区计算实际成本（外包虚拟建档请先挂编 External Employee 岗位）")
    # 行级隔离（issue #132，#121 脱敏第二旁路封堵）：实际成本按实体隔离——
    # hr 仅可测算其可管实体员工；无实体归属（虚拟建档/已解绑）仅 admin
    emp_company = pn.company_id
    if get_operable_company_ids(db, _user) != ALL_COMPANIES:
        if emp_company is None or emp_company not in get_operable_company_ids(db, _user):
            raise HTTPException(403, "无权查看该员工的实际成本（未分配其所属法人实体）")
    zone = pn.company.tax_zone if pn.company else None
    salary = salary_before_tax if salary_before_tax is not None else emp.actual_salary_before_tax
    if salary is None:
        raise HTTPException(400, "请先填写税前薪资（实际口径缺失，#115：不按 0 静默计算）")
    fb = fixed_bonus if fixed_bonus is not None else (emp.actual_fixed_bonus or 0)
    vb = floating_bonus if floating_bonus is not None else (emp.actual_floating_bonus or 0)
    result = calc_cost_by_zone(db, zone, salary, fixed_bonus=fb, floating_bonus=vb)
    result.update({"employee_id": eid, "scope": "actual"})
    return result


@router.patch("/employees/{eid}")
def update_employee(eid: int, payload: EmployeeUpdate,
                    user=Depends(get_current_user), db: Session = Depends(get_db)):
    emp = get_or_404(db, Employee, eid, "员工不存在")
    assert_version(emp, payload.version, "员工")
    # 行级隔离：员工修改按其实体隔离（转调中仍归属原岗公司）；
    # 无实体归属（虚拟建档/已解绑）仅 admin 可改（issue #147，读写对称）
    cur_pn = db.get(PositionNumber, emp.position_number_id) if emp.position_number_id else None
    if cur_pn is None:
        if get_operable_company_ids(db, user) != ALL_COMPANIES:
            raise HTTPException(403, "无实体归属的员工档案（虚拟建档/已解绑）仅 admin 可修改")
    else:
        assert_can_write_company(db, user, cur_pn.company_id, label="该员工")
    # 必填字段保持非空守卫；可空字段按 model_fields_set 区分「未提供」与「显式 null=清空」
    old_type = emp.employee_type
    for field in ("name", "gender", "employee_type"):
        val = getattr(payload, field)
        if val is not None:
            setattr(emp, field, val)
    # 挂编联动（PRD F1.5）：类型变更且已挂岗时校验与岗位编制匹配，友好 400（DB 触发器兜底）
    if ("employee_type" in payload.model_fields_set and payload.employee_type != old_type
            and emp.position_number_id):
        pn = db.get(PositionNumber, emp.position_number_id)
        if pn:
            _assert_type_match(pn, emp.employee_type)
    for field in ("birth_date", "phone", "email", "hire_date", "remark"):
        if field in payload.model_fields_set:
            setattr(emp, field, getattr(payload, field))
    # 实际成本字段（v2.3：跟人走）；手动模式清空输入框 → 显式 null 清空
    old_actual_mode = emp.actual_cost_mode
    if "actual_cost_mode" in payload.model_fields_set:
        try:
            emp.actual_cost_mode = CostMode(payload.actual_cost_mode or CostMode.MANUAL.value)
        except ValueError:
            raise HTTPException(400, "actual_cost_mode 仅支持 auto / manual")
    for field in ("actual_salary_before_tax", "actual_mandatory_tax", "actual_mandatory_fixed_fee",
                  "actual_fixed_bonus", "actual_floating_bonus", "actual_labor_cost"):
        if field in payload.model_fields_set:
            setattr(emp, field, getattr(payload, field))
    # 成本模式互斥服务端兜底（issue #141，PRD F1.6）：最终模式为 AUTO 时派生三栏
    # 须与引擎计算一致（重算保存路径原样通过、手填残留清空），与岗位侧同构
    if emp.actual_cost_mode == CostMode.AUTO and cur_pn is not None:
        from app.helpers import calc_cost_by_zone
        three = ("actual_mandatory_tax", "actual_mandatory_fixed_fee", "actual_labor_cost")
        zone = cur_pn.company.tax_zone if cur_pn.company else None
        if emp.actual_salary_before_tax is None:
            expected = {k: None for k in three}
        else:
            r = calc_cost_by_zone(db, zone, emp.actual_salary_before_tax,
                                  fixed_bonus=emp.actual_fixed_bonus or 0,
                                  floating_bonus=emp.actual_floating_bonus or 0)
            expected = ({k: None for k in three} if not r.get("configured")
                        else {"actual_mandatory_tax": r["mandatory_tax"],
                              "actual_mandatory_fixed_fee": r["mandatory_fixed_fee"],
                              "actual_labor_cost": r["labor_cost"]})
        flipped = old_actual_mode != CostMode.AUTO
        for f in three:
            touched = f in payload.model_fields_set
            if not (touched or flipped):
                continue
            eff = getattr(emp, f)
            exp = expected[f]
            setattr(emp, f, (eff if (eff is not None and exp is not None
                                     and abs(float(eff) - float(exp)) <= 0.05) else None))
    # 转调目标公司（v2.3）：仅可经 /transfers/initiate|claim 流转（#119-1 旁路封堵）——
    # 直改会造成「挂目标公司但非转调中」的无 Transfer 记录不一致态
    if "target_company_id" in payload.model_fields_set and payload.target_company_id != emp.target_company_id:
        raise HTTPException(400, "target_company_id 仅可经 POST /transfers/initiate 发起、认领/退回时自动清空，不可直接修改")
    if payload.employment_status is not None:
        # 「转调中」状态只能经 initiate 进入；离开该状态仅允许离职（认领/退回走 /transfers/*）
        if (payload.employment_status == EmploymentStatus.TRANSFERRING
                and emp.employment_status != EmploymentStatus.TRANSFERRING):
            raise HTTPException(400, "「转调中」仅可经 POST /transfers/initiate 发起，不可直接修改")
        if (emp.employment_status == EmploymentStatus.TRANSFERRING
                and payload.employment_status not in
                (EmploymentStatus.TRANSFERRING, EmploymentStatus.TERMINATED)):
            raise HTTPException(400, "转调中员工仅可认领/退回（POST /transfers/{id}/claim|reject）或办理离职")
        if (payload.employment_status == EmploymentStatus.TERMINATED
                and emp.employment_status != EmploymentStatus.TERMINATED):
            _vacate(db, emp, f"员工 {emp.name} 离职")
            # 离职自动退回未决转调（#119-2）：避免 initiated 记录滞留目标公司待认领池
            _reject_pending_transfers(db, emp, actor=user.username)
        emp.employment_status = payload.employment_status
    emp.version = (emp.version or 1) + 1
    db.commit()
    return serialize_employee(db, emp)


@router.delete("/employees/{eid}")
def delete_employee(eid: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    emp = get_or_404(db, Employee, eid, "员工不存在")
    if emp.position_number_id is not None or emp.employment_status != EmploymentStatus.TERMINATED:
        raise HTTPException(400, "仅可删除已离职且已解绑岗位的员工档案")
    # 无实体归属（issue #147）：已解绑档案任何公司都不可写，仅 admin 可删
    if get_operable_company_ids(db, user) != ALL_COMPANIES:
        raise HTTPException(403, "无实体归属的员工档案（虚拟建档/已解绑）仅 admin 可删除")
    db.delete(emp)
    db.commit()
    return {"ok": True, "id": eid}


# ---------------------------------------------------------------- 升职（v2.3 F1.5b）
@router.post("/employees/{eid}/promote")
def promote_employee(eid: int, payload: PromoteRequest,
                     user=Depends(get_current_user), db: Session = Depends(get_db)):
    """升职：Filled 新岗、老岗默认 Vacant（可后续手动 Closed）、prev_* 记来源、工龄照人。

    时节 timing=immediate|month_end 仅记入事件供财务月边界归属；
    本系统无调度器，动作即时生效并在事件中保留时节标记。
    单事务成对流转：老岗 Vacant + 新岗 Filled + 人移动 + prev_* 全部生效或整体回滚。
    """
    if payload.timing not in ("immediate", "month_end"):
        raise HTTPException(400, "timing 仅支持 immediate / month_end")
    emp = (
        db.query(Employee).filter(Employee.id == eid).with_for_update().first()
    )
    if not emp:
        raise HTTPException(404, "员工不存在")
    if emp.employment_status == EmploymentStatus.TERMINATED:
        raise HTTPException(400, "离职员工不可升职")
    if emp.employment_status == EmploymentStatus.TRANSFERRING:
        raise HTTPException(400, "转调中员工请先完成认领或退回再升职")
    old_pn = db.get(PositionNumber, emp.position_number_id) if emp.position_number_id else None
    if old_pn:
        assert_can_write_company(db, user, old_pn.company_id, label="该员工的所属公司")
    else:
        # issue #140：外包虚拟建档员工无在挂岗位，promote 语义不成立——
        # 静默 no-op 会「返回成功却不留痕」，显式 400 引导先经调岗挂编
        raise HTTPException(400, "员工未挂岗（虚拟建档），请先经 POST /transfers 挂编后再升职")
    new_pn = (
        db.query(PositionNumber)
        .filter(PositionNumber.id == payload.to_position_id)
        .with_for_update()
        .first()
    )
    if not new_pn:
        raise HTTPException(404, "目标岗位不存在")
    # 行级隔离补口（issue #147）：升职会写入目标岗位公司的 status/prev_*，
    # 目标公司须在操作者可管实体集内（与 claim 目标侧校验同口径）
    assert_can_write_company(db, user, new_pn.company_id, label="目标岗位所属公司")
    # 空闲目标岗白名单与 claim 对称（#124 复测发现）：Open/Vacant/Offered/Planned 均可升入
    # （月末升职常先立 planned 编制、生效日转 Filled；ALLOWED_EMPLOYEE 含 planned→filled），
    # occupied/closed/frozen 仍拒绝
    if new_pn.status not in (PositionStatus.OPEN, PositionStatus.VACANT,
                             PositionStatus.OFFERED, PositionStatus.PLANNED):
        raise HTTPException(400, f"目标岗位状态为 {new_pn.status.value}，非空闲编制（occupied/closed/frozen 不可升入）")
    if db.query(Employee).filter(Employee.position_number_id == new_pn.id).first():
        raise HTTPException(400, "目标岗位已被其他员工占用")
    # 挂编联动（#50）：新岗类型须匹配员工合同属性（DB 触发器兜底前先给 400）
    _assert_type_match(new_pn, emp.employee_type)

    timing_cn = "月末升职" if payload.timing == "month_end" else "即时升职"
    if old_pn.id == new_pn.id:
        raise HTTPException(400, "升职目标岗位与当前岗位相同，无意义操作")
    if old_pn.status == PositionStatus.FILLED:
        lifecycle.transition(db, old_pn, PositionStatus.VACANT,
                             note=f"员工 {emp.name} {timing_cn}移出（如需关闭编制请手动 Closed）",
                             employee_id=emp.id, system=True)
    lifecycle.transition(db, new_pn, PositionStatus.FILLED,
                         note=f"员工 {emp.name} {timing_cn}入岗" + (f"（{payload.note}）" if payload.note else ""),
                         employee_id=emp.id, system=True)
    new_pn.prev_position_id = old_pn.id
    new_pn.prev_company_id = old_pn.company_id
    emp.position_number_id = new_pn.id
    emp.version = (emp.version or 1) + 1
    # 升职结构化留痕（#113）：transfers 表 kind='promotion'，支撑 F1.5b 财务月边界归属审计
    from datetime import datetime as _dt
    from datetime import timezone as _tz
    db.add(Transfer(
        employee_id=emp.id,
        from_position_id=old_pn.id,
        target_company_id=new_pn.company_id,
        to_position_id=new_pn.id,
        status="claimed",
        kind="promotion",
        timing=payload.timing,
        initiated_by=user.username,
        claimed_by=user.username,
        claimed_at=_dt.now(_tz.utc),
        note=payload.note,
    ))
    db.commit()
    return {"ok": True, "employee": serialize_employee(db, emp)}


def _vacate(db: Session, emp: Employee, note: str):
    """解绑员工岗位，岗位转 Vacant。"""
    pn = db.get(PositionNumber, emp.position_number_id) if emp.position_number_id else None
    if pn and pn.status == PositionStatus.FILLED:
        lifecycle.transition(db, pn, PositionStatus.VACANT, note=note, employee_id=emp.id, system=True)
    emp.position_number_id = None


def _reject_pending_transfers(db: Session, emp: Employee, actor: str):
    """员工离职时自动退回其未决转调（#119-2），避免 initiated 记录滞留待认领池。"""
    from datetime import datetime as _dt
    from datetime import timezone as _tz
    pend = (
        db.query(Transfer)
        .filter(Transfer.employee_id == emp.id,
                Transfer.status == "initiated",
                Transfer.kind == "transfer")
        .with_for_update()
        .all()
    )
    for t in pend:
        t.status = "rejected"
        t.claimed_by = actor
        t.claimed_at = _dt.now(_tz.utc)
