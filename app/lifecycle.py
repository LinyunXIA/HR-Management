"""岗位生命周期状态机与事件记录。"""
from datetime import date

from app.models import PositionEvent, PositionStatus

# 手动流转（岗位管理界面可操作）
ALLOWED_MANUAL = {
    PositionStatus.PLANNED: {PositionStatus.OPEN, PositionStatus.CLOSED, PositionStatus.FROZEN},
    PositionStatus.OPEN: {PositionStatus.OFFERED, PositionStatus.CLOSED},
    PositionStatus.OFFERED: {PositionStatus.OPEN},
    PositionStatus.VACANT: {PositionStatus.OPEN, PositionStatus.CLOSED, PositionStatus.FROZEN},
    PositionStatus.FROZEN: {PositionStatus.PLANNED, PositionStatus.OPEN},
    PositionStatus.CLOSED: set(),  # 终态
}

# 员工动作触发的系统自动流转（入职/离职/调岗/转调认领）
ALLOWED_EMPLOYEE = {
    PositionStatus.PLANNED: {PositionStatus.FILLED},   # v2.3 转调认领可分配 Planned 空闲编制
    PositionStatus.OPEN: {PositionStatus.FILLED},
    PositionStatus.VACANT: {PositionStatus.FILLED},
    PositionStatus.OFFERED: {PositionStatus.FILLED},
    PositionStatus.FILLED: {PositionStatus.VACANT},
}


class LifecycleError(Exception):
    """状态流转非法。"""


def transition(db, position, to_status: PositionStatus, note=None, employee_id=None,
               *, system: bool = False) -> PositionEvent:
    """校验并执行状态流转，写入一条生命周期事件。调用方负责 commit。

    system=True 表示由员工入职/离职/调岗自动触发（允许 filled↔open/vacant 等）。
    联动规则：
    - 关闭（→closed）：若未设关闭日则写入当前日期。
    - 重新激活（→open/offered/filled/vacant/planned）：清空关闭日。
    """
    from_status = position.status
    if from_status == to_status:
        raise LifecycleError(f"岗位已是 {to_status.value}，无需流转")
    allowed = set(ALLOWED_MANUAL.get(from_status, set()))
    if system:
        allowed |= set(ALLOWED_EMPLOYEE.get(from_status, set()))
    if to_status not in allowed:
        raise LifecycleError(
            f"不允许从 {from_status.value} 流转到 {to_status.value}"
            f"（当前仅允许: " + ", ".join(s.value for s in sorted(allowed, key=lambda x: x.value)) + "）"
        )
    if to_status == PositionStatus.CLOSED and position.closing_date is None:
        position.closing_date = date.today()
    elif to_status in (
        PositionStatus.OPEN,
        PositionStatus.OFFERED,
        PositionStatus.FILLED,
        PositionStatus.VACANT,
        PositionStatus.PLANNED,
    ) and position.closing_date is not None:
        position.closing_date = None

    old = from_status.value if from_status else None
    position.status = to_status
    event = PositionEvent(
        position_number_id=position.id,
        from_status=old,
        to_status=to_status.value,
        note=note,
        employee_id=employee_id,
    )
    db.add(event)
    return event


def record_event(db, position_id, from_status, to_status, note=None, employee_id=None) -> PositionEvent:
    """直接写入一条事件（不改变状态），用于导入等场景。"""
    event = PositionEvent(
        position_number_id=position_id,
        from_status=from_status,
        to_status=to_status,
        note=note,
        employee_id=employee_id,
    )
    db.add(event)
    return event
