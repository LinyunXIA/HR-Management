"""在岗岗位判定助手（v2.6 R2：对外岗位数据导出 GET /public/positions 复用）。

规则（D4 定稿，2026-08-24 第二轮修订沿用）：
- 计入判定按日期交集：opening ≤ Y-12-31 且（closing 空 或 ≥ Y-01-01），
  **不看 lifecycle 当前状态**（历史年回溯时状态已失真）；
  opening_date 为空视为未生效不计入。
- 与年份至少有一天交集即计入（含年中开启/年中关闭）。

注：原「基准包推送 + 报告计算」链路已整体退役——计算权移交第三方，
我方仅提供岗位数据（见 routers/master_data.py::public_positions）；
months_factor 保留供将来内部报表复用。
"""
from datetime import date

from sqlalchemy.orm import Session

from app.models import PositionNumber


def year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def active_positions_in_year(db: Session, year: int) -> list[PositionNumber]:
    """该年「在岗」的岗位：日期交集判定（至少一天落在 Y 内）。"""
    ys, ye = year_bounds(year)
    return (
        db.query(PositionNumber)
        .filter(
            PositionNumber.opening_date.isnot(None),
            PositionNumber.opening_date <= ye,
            (PositionNumber.closing_date.is_(None)) | (PositionNumber.closing_date >= ys),
        )
        .all()
    )


def months_factor(pn: PositionNumber, year: int) -> float:
    """年内自然月数(含首尾)/12；opening/closing 截断到年界。（预留：内部报表用）"""
    ys, ye = year_bounds(year)
    start: date = max(pn.opening_date or ys, ys)
    end: date = min(pn.closing_date or ye, ye)
    if end < start:
        return 0.0
    months = (end.year - start.year) * 12 + (end.month - start.month) + 1
    return min(months, 12) / 12.0
