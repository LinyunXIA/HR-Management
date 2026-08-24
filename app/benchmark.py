"""年度用工成本预估引擎（v2.6，PRD §4 F6）。

外部基准包（LaborBenchmark，整年快照）× 系统内在岗岗位 → 每公司年度用工成本预估。

规则（2026-08-24 grilling 定稿）：
- 计入判定按日期交集：opening ≤ Y-12-31 且（closing 空 或 ≥ Y-01-01），
  **不看 lifecycle 当前状态**（历史年回溯时状态已失真）；opening_date 为空视为未生效不计入
- 月折算系数 = 自然月数(含首尾) / 12
- 匹配键 = (company_id, level, country_id, work_location) 全等值精确匹配，
  **任何维度不回退**（方案甲/D3：缺即报错进缺失清单）
- 单岗位年度用工成本 = (税前 + 税前×强制税率% + 强制定额扣费 + 固定奖金 + 浮动奖金) × factor
- 奖金取自岗位自身字段（外部不知道我方奖金），空按 0
"""
import json
from datetime import date

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import BenchmarkReport, PositionNumber


def year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def active_positions_in_year(db: Session, year: int) -> list[PositionNumber]:
    """该年「在岗」的岗位：日期交集判定（D4），不看状态；opening_date 为空不计入。"""
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
    """年内自然月数(含首尾)/12；opening/closing 截断到年界。"""
    ys, ye = year_bounds(year)
    start: date = max(pn.opening_date or ys, ys)
    end: date = min(pn.closing_date or ye, ye)
    if end < start:
        return 0.0
    months = (end.year - start.year) * 12 + (end.month - start.month) + 1
    return min(months, 12) / 12.0


def _match_reason(pn: PositionNumber) -> str | None:
    """岗位无法参与基准匹配的结构性原因；None = 结构完整可尝试匹配。"""
    if not pn.level:
        return "岗位级别为空"
    if not pn.country_id:
        return "岗位未定位国家/地区"
    if not pn.work_location:
        return "岗位工作地点为空"
    return None


def coverage_missing(db: Session, year: int, lookup: dict) -> list[dict]:
    """L4 覆盖校验（推送时调用）：返回该年在岗岗位中无法命中基准行的清单。

    lookup 键 = (company_id, level, country_id, work_location)。
    """
    missing = []
    for pn in active_positions_in_year(db, year):
        reason = _match_reason(pn)
        key = (pn.company_id, pn.level or "", pn.country_id, pn.work_location or "")
        if reason is None and key in lookup:
            continue
        company = pn.company
        missing.append({
            "position_id": pn.id,
            "number": pn.number,
            "position_name": (pn.position.name if pn.position else None),
            "company_id": pn.company_id,
            "company_name": company.name if company else None,
            "reason": reason or "无对应基准行（该 年份+公司+级别+国家+地点 组合未推送）",
        })
    return missing


def compute_report(db: Session, year: int) -> dict:
    """生成报告 payload（不入库）。返回 {totals, companies[], unmatched[]}。"""
    from app.models import LaborBenchmark

    rows = db.query(LaborBenchmark).filter(LaborBenchmark.year == year).all()
    if not rows:
        raise ValueError(f"{year} 年无基准数据")
    lookup = {
        (r.company_id, r.level, r.country_id, r.work_location): r
        for r in rows
    }

    companies: dict[int, dict] = {}
    unmatched: list[dict] = []
    matched = 0
    for pn in active_positions_in_year(db, year):
        company = pn.company
        comp_entry = companies.setdefault(pn.company_id, {
            "company_id": pn.company_id,
            "company_name": company.name if company else None,
            "annual_labor_cost": 0.0,
            "positions": [],
            "unmatched_count": 0,
        })
        reason = _match_reason(pn)
        row = lookup.get((pn.company_id, pn.level or "", pn.country_id, pn.work_location or ""))
        if reason is not None or row is None:
            unmatched.append({
                "position_id": pn.id,
                "number": pn.number,
                "position_name": (pn.position.name if pn.position else None),
                "company_id": pn.company_id,
                "company_name": company.name if company else None,
                "reason": reason or "无对应基准行（该组合未推送）",
            })
            comp_entry["unmatched_count"] += 1
            continue
        matched += 1
        factor = months_factor(pn, year)
        salary = float(row.salary_before_tax)
        tax_amt = round(salary * float(row.tax_rate) / 100.0, 2)
        fee = float(row.mandatory_fixed_fee)
        bonus = float(pn.fixed_bonus or 0) + float(pn.floating_bonus or 0)
        unit_cost = round(salary + tax_amt + fee + bonus, 2)
        annual = round(unit_cost * factor, 2)
        comp_entry["annual_labor_cost"] = round(comp_entry["annual_labor_cost"] + annual, 2)
        comp_entry["positions"].append({
            "position_id": pn.id,
            "number": pn.number,
            "position_name": (pn.position.name if pn.position else None),
            "level": pn.level,
            "country_id": pn.country_id,
            "work_location": pn.work_location,
            "months_factor": round(factor, 4),
            "salary_before_tax": salary,
            "tax_rate_pct": float(row.tax_rate),
            "mandatory_tax_amount": tax_amt,
            "mandatory_fixed_fee": fee,
            "bonus": round(bonus, 2),
            "unit_annual_cost": unit_cost,
            "annual_labor_cost": annual,
        })

    return {
        "year": year,
        "generated_at": None,  # 由调用方填 generated_at
        "totals": {"benchmark_rows": len(rows), "matched_positions": matched,
                   "unmatched_positions": len(unmatched)},
        "companies": sorted(companies.values(), key=lambda c: c["company_name"] or ""),
        "unmatched": unmatched,
    }


def run_report_task(year: int):
    """后台任务：计算并落库报告（自带会话；失败记 status=failed）。"""
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        rep = db.query(BenchmarkReport).filter(BenchmarkReport.year == year).first()
        if rep is None:
            rep = BenchmarkReport(year=year, status="pending")
            db.add(rep)
            db.flush()
        try:
            payload = compute_report(db, year)
            payload["generated_at"] = datetime.now(timezone.utc).isoformat()
            rep.status = "ready"
            rep.payload = json.dumps(payload, ensure_ascii=False)
            rep.error_count = len(payload.get("unmatched", []))
        except Exception as e:  # noqa: BLE001 —— 任务内自兜底，状态可见
            rep.status = "failed"
            rep.payload = json.dumps({"error": str(e)}, ensure_ascii=False)
        rep.generated_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
