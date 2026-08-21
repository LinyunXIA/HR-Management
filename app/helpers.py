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

# P{序号}-{范围}，范围可为 1/2/3 或 4-{国家编号}
NUMBER_RE = re.compile(r"^P(\d{3,})-(\d+)(?:-(\d+))?$")

SCOPE_SUFFIX = {Scope.FAMILY: "1", Scope.GLOBAL: "2", Scope.REGIONAL: "3"}


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


def validate_number_format(number: str, scope: Scope, country_code: str | None):
    """校验岗位编号与 scope/country 一致性。"""
    m = NUMBER_RE.match(number)
    if not m:
        raise HTTPException(400, f"岗位编号格式非法: {number}（应为 P{{序号}}-{{范围}}）")
    scope_part = m.group(2)
    if scope == Scope.COUNTRY:
        if country_code is None:
            raise HTTPException(400, "Country 范围缺少国家/地区")
        expected = country_code  # country.code 形如 '4-5'
        if f"{scope_part}-{m.group(3)}" != expected:
            raise HTTPException(400, f"Country 范围岗位编号后缀应为 {expected}，而非 -{scope_part}-{m.group(3)}")
    else:
        expected = SCOPE_SUFFIX[scope]
        if scope_part != expected:
            raise HTTPException(400, f"{scope.value} 范围岗位编号后缀应为 -{expected}，而非 -{scope_part}")


def generate_number(db: Session, scope: Scope, country_code: str | None) -> str:
    """按规则自动生成岗位编号：下一可用 P 序号 + 范围后缀。"""
    seqs = []
    for (num,) in db.query(PositionNumber.number).all():
        m = NUMBER_RE.match(num)
        if m:
            seqs.append(int(m.group(1)))
    seq = (max(seqs) + 1) if seqs else 1
    if scope == Scope.COUNTRY:
        return f"P{seq:03d}-{country_code}"
    return f"P{seq:03d}-{SCOPE_SUFFIX[scope]}"


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


def set_dotted_lines(db: Session, position_number_id: int, manager_ids: list[int]):
    """重设岗位的虚线经理列表。"""
    db.query(PositionNumberDottedLine).filter(
        PositionNumberDottedLine.position_number_id == position_number_id
    ).delete()
    for mid in dict.fromkeys(manager_ids):  # 去重保序
        if mid:
            get_or_404(db, PositionNumber, mid, f"虚线经理岗位不存在 (id={mid})")
            db.add(PositionNumberDottedLine(position_number_id=position_number_id, dotted_manager_id=mid))


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


def serialize_position(db: Session, pn: PositionNumber) -> dict:
    pos = pn.position
    company = pn.company
    country = pn.country
    incumbent = (
        db.query(Employee).filter(Employee.position_number_id == pn.id).first()
    )
    dotted_ids_list = dotted_ids(db, pn.id)
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
        "dotted_manager_numbers": dotted_nums,
        "org_chart_display": pn.org_chart_display,
        "prev_position_id": pn.prev_position_id,
        "prev_position_number": prev_p.number if prev_p else None,
        "prev_company_id": pn.prev_company_id,
        "prev_company_name": prev_c.name if prev_c else None,
        "remark": pn.remark,
        "status": pn.status.value if pn.status else None,
        "cost_mode": pn.cost_mode.value if pn.cost_mode else None,
        "salary_before_tax": float(pn.salary_before_tax) if pn.salary_before_tax is not None else None,
        "company_share": float(pn.company_share) if pn.company_share is not None else None,
        "labor_cost": float(pn.labor_cost) if pn.labor_cost is not None else None,
        "incumbent_id": incumbent.id if incumbent else None,
        "incumbent_name": incumbent.name if incumbent else None,
        "version": pn.version,
        "created_at": pn.created_at,
        "updated_at": pn.updated_at,
    }
