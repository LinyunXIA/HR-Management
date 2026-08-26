"""在岗岗位判定助手（v2.6 R2：对外岗位数据导出 GET /public/positions 复用）。

规则（D4 定稿，2026-08-24 第二轮修订沿用）：
- 计入判定按日期交集：opening ≤ Y-12-31 且（closing 空 或 ≥ Y-01-01），
  **不看 lifecycle 当前状态**（历史年回溯时状态已失真）；
  opening_date 为空视为未生效不计入。
- 与年份至少有一天交集即计入（含年中开启/年中关闭）。

注：原「基准包推送 + 报告计算」链路已整体退役——计算权移交第三方，
我方仅提供岗位数据（见 routers/master_data.py::public_positions）；
months_factor 月折算助手随 R2 一并移除（折算依据 = 开启/关闭日原始值，
由第三方自行折算，issue #149 清理死代码）。
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
