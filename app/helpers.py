"""共享业务助手：岗位解析、编号规则、序列化、环检测。"""
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (
    Company,
    Employee,
    Position,
    PositionNumber,
    PositionNumberDottedLine,
    Scope,
)

# 编号规则（系统强制分配，源文件编号一律忽视）：
#   正式岗（Employee / Consultant）→ P{seq}，如 P1、P2…
#   外包岗（External Employee）   → PA{seq}，如 PA1、PA2…
NUMBER_RE_P = re.compile(r"^P(\d+)$")
NUMBER_RE_PA = re.compile(r"^PA(\d+)$")

OUTSOURCED_TYPE = "External Employee"


def get_or_404(db: Session, model, obj_id: int, detail=None):
    obj = db.get(model, obj_id)
    if not obj:
        raise HTTPException(404, detail or f"记录不存在 (id={obj_id})")
    return obj


def resolve_position(db: Session, position_id: int = None, position_name: str = None) -> Position:
    """解析职位职能：按 id 或名称（不存在则自动创建，未 commit）。"""
    if position_id:
        return get_or_404(db, Position, position_id, "职位职能不存在")
    if position_name:
        pos = db.query(Position).filter(Position.name == position_name).first()
        if not pos:
            pos = Position(name=position_name)
            db.add(pos)
            db.flush()
        return pos
    raise HTTPException(400, "必须提供 position_id 或 position_name")


def validate_number_format(number: str, scope: Scope = None, country_code: str | None = None):
    """校验岗位编号格式（P{seq} 或 PA{seq}）。scope/country 参数保留兼容旧调用，不再参与校验。"""
    if not (NUMBER_RE_P.match(number) or NUMBER_RE_PA.match(number)):
        raise HTTPException(400, f"岗位编号格式非法: {number}（应为 P{{序号}} 或 PA{{序号}}）")


def number_series(position_type: str | None) -> str:
    """按职位类型返回编号系列前缀：外包岗 PA，其余 P。"""
    return "PA" if (position_type or "").strip() == OUTSOURCED_TYPE else "P"


def next_sequence(db: Session, prefix: str) -> int:
    """取库内指定编号系列的下一个序号（当前最大值 +1；空库从 1 起）。

    可移植实现（v2.5 SQLite 切换）：LIKE 前缀过滤 + Python 侧正则解析取 max，
    不依赖 PG 专属 regexp_replace / ~ 操作符；岗位量级小（百级），无性能顾虑。
    """
    rows = db.query(PositionNumber.number).filter(
        PositionNumber.number.like(f"{prefix}%")
    ).all()
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    max_seq = max((int(m.group(1)) for (n,) in rows if (m := pat.match(n))), default=0)
    return max_seq + 1


def generate_number(db: Session, position_type: str | None = None, *args) -> str:
    """自动生成岗位编号：正式岗 P{seq}、外包岗 PA{seq}（纯序号，无范围后缀）。

    scope/country 等位置参数保留兼容旧签名，已不参与编号。
    """
    return f"{number_series(position_type)}{next_sequence(db, number_series(position_type))}"


# v2.4.2：员工工号自动生成系列（key = EmployeeType.name，DB 存储亦为枚举名）
EMPLOYEE_NO_SERIES = {
    "REGULAR": "G",     # 正式工 G00001 起
    "INTERN": "V",      # 实习 V00001 起
    "LABOR": "V",       # 劳务与实习同系列
    "OUTSOURCED": "O",  # 外包 O00001 起（可虚拟建档不挂岗）
}


def generate_employee_no(db: Session, employee_type) -> str:
    """自动生成员工工号：正式 G{seq:05d}、实习/劳务 V{seq:05d}、外包 O{seq:05d}。

    可移植实现（v2.5 SQLite 切换）：LIKE 前缀过滤 + Python 侧正则解析取 max。
    """
    key = employee_type.name if hasattr(employee_type, "name") else str(employee_type)
    prefix = EMPLOYEE_NO_SERIES.get(key, "G")
    rows = db.query(Employee.employee_no).filter(
        Employee.employee_no.like(f"{prefix}%")
    ).all()
    pat = re.compile(rf"^{re.escape(prefix)}(\d{{5}})$")
    max_seq = max((int(m.group(1)) for (n,) in rows if (m := pat.match(n))), default=0)
    return f"{prefix}{max_seq + 1:05d}"


def check_cycle(db: Session, position_id: int, manager_id: int):
    """沿上级链上溯，若回到 position_id 或成环则拦截。"""
    if manager_id == position_id:
        raise HTTPException(400, "直线经理不能是自身")
    seen = set()
    cur = manager_id
    while cur is not None:
        if cur == position_id:
            raise HTTPException(400, "汇报关系形成环路，已拦截（A→B→A）")
        if cur in seen:
            raise HTTPException(400, "汇报关系存在环路")
        seen.add(cur)
        m = db.get(PositionNumber, cur)
        cur = m.solid_line_manager_id if m else None


def set_dotted_lines(db: Session, position_number_id: int, manager_ids: list[int] | list[dict]):
    """重设岗位的虚线经理列表。支持传入 [{id, label}] 或 [id] 两种格式。"""
    db.query(PositionNumberDottedLine).filter(
        PositionNumberDottedLine.position_number_id == position_number_id
    ).delete()
    for item in dict.fromkeys([m['id'] if isinstance(m, dict) else m for m in manager_ids]):
        if item:
            get_or_404(db, PositionNumber, item, f"虚线经理岗位不存在 (id={item})")
            # 查找对应的 label
            label = None
            for m in manager_ids:
                if isinstance(m, dict) and m.get('id') == item:
                    label = m.get('label')
                    break
            db.add(PositionNumberDottedLine(position_number_id=position_number_id, dotted_manager_id=item, label=label))


def dotted_ids(db: Session, position_number_id: int) -> list[int]:
    rows = db.query(PositionNumberDottedLine.dotted_manager_id).filter(
        PositionNumberDottedLine.position_number_id == position_number_id
    ).all()
    return [r[0] for r in rows]


def assert_version(obj, client_version: int | None, label: str = "数据"):
    """乐观锁校验（PRD §7C）：携带 version 时必须一致，否则 409。"""
    if client_version is None:
        return
    current = getattr(obj, "version", None)
    if current is None:
        return
    if client_version != current:
        raise HTTPException(
            409,
            f"{label}已被他人修改，请刷新后重试（当前版本 {current}，提交版本 {client_version}）",
        )


# ---------------------------------------------------------------- 行级权限隔离（v2.3 PRD §7B.3）
ALL_COMPANIES = "ALL"  # admin 哨兵：全司可读写


def get_operable_company_ids(db: Session, user) -> set[int] | str:
    """返回用户可写操作的公司 id 集合。

    - admin → ALL_COMPANIES（全司）
    - hr    → 其 user_companies 绑定的实体集合（空集 = 无任何可管实体）
    """
    from app.models import UserCompany
    if user.role == "admin":
        return ALL_COMPANIES
    rows = db.query(UserCompany.company_id).filter(UserCompany.user_id == user.id).all()
    return {r[0] for r in rows}


def assert_can_write_company(db: Session, user, company_id: int | None,
                             label: str = "该公司"):
    """写操作按实体隔离：hr 仅能修改其可管实体下的员工/成本（PRD §7B.3）。

    未授权 → 403；company_id 为空视为不涉及实体归属（如已离职员工），放行。
    """
    allowed = get_operable_company_ids(db, user)
    if allowed == ALL_COMPANIES:
        return
    if company_id is not None and company_id not in allowed:
        raise HTTPException(403, f"无权操作{label}（未分配该法人实体）")


# ---------------------------------------------------------------- 成本引擎（v2.6 R1：成本键 = 公司所绑税区）
# 原 resolve_tax_zone（按岗位工作地点 → 国家 → 税区 的解析链）已退役：
# v2.6 R1 起全部成本场景统一取 Company.tax_zone_id，不再经工作地点绕行。
# calc_cost_by_zone(db, zone, salary, ...) 本体不变，由调用方传入公司所绑税区。


def calc_cost_by_zone(db: Session, zone, salary, fixed_bonus=0.0, floating_bonus=0.0) -> dict:
    """按税区计算成本分项与用工成本（v2.6 六栏口径）。

    - 强制扣税 = 税前 × Σ(rate 科目税率%)
    - 强制定额扣费 = Σ(fixed 科目金额)
    - 用工成本 = 税前 + 强制扣税 + 定额扣费 + 固定奖金 + 浮动奖金
    zone=None → configured=False（未配置，不猜测）。
    zone 由调用方传入（v2.6 R1：取 Company.tax_zone）。
    """
    from app.models import EmploymentTaxItem
    salary = float(salary or 0)
    result = {
        "configured": False,
        "tax_zone_id": zone.id if zone else None,
        "salary_before_tax": salary,
        "tax_rate_total": 0.0,
        "fixed_fee_total": 0.0,
        "tax_items": [],
        "mandatory_tax": None,
        "mandatory_fixed_fee": None,
        "labor_cost": None,
        "message": "该地区税率未配置，无法自动计算（请配置税区或改用手动输入）" if zone is None else None,
    }
    if zone is None:
        return result
    items = (
        db.query(EmploymentTaxItem)
        .filter(EmploymentTaxItem.tax_zone_id == zone.id,
                EmploymentTaxItem.is_active.is_(True))
        .all()
    )
    rate_items = [it for it in items if (it.item_kind or "rate") == "rate"]
    fixed_items = [it for it in items if it.item_kind == "fixed"]
    rate_pct = sum(float(it.tax_rate or 0) for it in rate_items)
    mandatory_tax = round(salary * rate_pct / 100.0, 2)
    fixed_fee = round(sum(float(it.fixed_amount or 0) for it in fixed_items), 2)
    labor_cost = round(
        salary + mandatory_tax + fixed_fee + float(fixed_bonus or 0) + float(floating_bonus or 0), 2
    )
    result.update({
        "configured": True,
        "tax_rate_total": round(rate_pct, 4),
        "fixed_fee_total": fixed_fee,
        "tax_items": [
            {"item_name": it.item_name, "item_kind": it.item_kind or "rate",
             "tax_rate": float(it.tax_rate or 0), "fixed_amount":
                 (float(it.fixed_amount) if it.fixed_amount is not None else None)}
            for it in items
        ],
        "mandatory_tax": mandatory_tax,
        "mandatory_fixed_fee": fixed_fee,
        "labor_cost": labor_cost,
        "message": None if items else "税区下暂无有效税务科目",
    })
    return result


def serialize_position(db: Session, pn: PositionNumber) -> dict:
    pos = pn.position
    company = pn.company
    country = pn.country
    incumbent = (
        db.query(Employee).filter(Employee.position_number_id == pn.id).first()
    )
    dotted_lines = db.query(PositionNumberDottedLine).filter(
        PositionNumberDottedLine.position_number_id == pn.id
    ).all()
    dotted_ids_list = [d.dotted_manager_id for d in dotted_lines]
    dotted_labels = [d.label for d in dotted_lines]
    dotted_nums = []
    for did in dotted_ids_list:
        dm = db.get(PositionNumber, did)
        dotted_nums.append(dm.number if dm else str(did))
    sl = db.get(PositionNumber, pn.solid_line_manager_id) if pn.solid_line_manager_id else None
    prev_p = db.get(PositionNumber, pn.prev_position_id) if pn.prev_position_id else None
    prev_c = db.get(Company, pn.prev_company_id) if pn.prev_company_id else None
    return {
        "id": pn.id,
        "number": pn.number,
        "position_id": pn.position_id,
        "position_name": pos.name if pos else None,
        "company_id": pn.company_id,
        "company_name": company.name if company else None,
        "level": pn.level,
        "scope": pn.scope.value if pn.scope else None,
        "country_id": pn.country_id,
        "country_name": country.name if country else None,
        "position_type": pn.position_type,
        "opening_date": pn.opening_date,
        "closing_date": pn.closing_date,
        "work_location": pn.work_location,
        "job_responsibility": pn.job_responsibility,
        "legal_category": pn.legal_category if pn.legal_category else None,
        "solid_line_manager_id": pn.solid_line_manager_id,
        "solid_line_number": sl.number if sl else None,
        "solid_line_manager_name": sl.position.name if sl else None,
        "dotted_manager_ids": dotted_ids_list,
        "dotted_manager_labels": dotted_labels,
        "dotted_manager_numbers": dotted_nums,
        "org_chart_display": pn.org_chart_display,
        "prev_position_id": pn.prev_position_id,
        "prev_position_number": prev_p.number if prev_p else None,
        "prev_company_id": pn.prev_company_id,
        "prev_company_name": prev_c.name if prev_c else None,
        "remark": pn.remark,
        "status": pn.status.value if pn.status else None,
        "cost_mode": pn.cost_mode.value if pn.cost_mode else None,
        # ---- 预算成本六栏（v2.6）----
        "salary_before_tax": float(pn.salary_before_tax) if pn.salary_before_tax is not None else None,
        "mandatory_tax": float(pn.mandatory_tax) if pn.mandatory_tax is not None else None,
        "mandatory_fixed_fee": float(pn.mandatory_fixed_fee) if pn.mandatory_fixed_fee is not None else None,
        "fixed_bonus": float(pn.fixed_bonus) if pn.fixed_bonus is not None else None,
        "floating_bonus": float(pn.floating_bonus) if pn.floating_bonus is not None else None,
        "labor_cost": float(pn.labor_cost) if pn.labor_cost is not None else None,
        "incumbent_id": incumbent.id if incumbent else None,
        "incumbent_name": incumbent.name if incumbent else None,
        # ---- 实际成本层（v2.3 双口径：Filled 对照、跟人走；空岗为 None；v2.6 六栏）----
        "actual_cost_mode": (incumbent.actual_cost_mode.value if incumbent and incumbent.actual_cost_mode else None),
        "actual_salary_before_tax": (float(incumbent.actual_salary_before_tax) if incumbent and incumbent.actual_salary_before_tax is not None else None),
        "actual_mandatory_tax": (float(incumbent.actual_mandatory_tax) if incumbent and incumbent.actual_mandatory_tax is not None else None),
        "actual_mandatory_fixed_fee": (float(incumbent.actual_mandatory_fixed_fee) if incumbent and incumbent.actual_mandatory_fixed_fee is not None else None),
        "actual_fixed_bonus": (float(incumbent.actual_fixed_bonus) if incumbent and incumbent.actual_fixed_bonus is not None else None),
        "actual_floating_bonus": (float(incumbent.actual_floating_bonus) if incumbent and incumbent.actual_floating_bonus is not None else None),
        "actual_labor_cost": (float(incumbent.actual_labor_cost) if incumbent and incumbent.actual_labor_cost is not None else None),
        "version": pn.version,
        "created_at": pn.created_at,
        "updated_at": pn.updated_at,
    }
