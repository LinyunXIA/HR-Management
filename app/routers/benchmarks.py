"""外部用工成本基准对接（v2.6，PRD §4 F6）。

POST /benchmarks              推送整年快照（方案甲：同步 L1~L4 校验，任一不过整批 400）
GET  /benchmarks/reports/{year}  拉取预估报告（pending/ready/failed）

鉴权：scope=`benchmarks`（admin 天然全量；UI 用户无 API 权限一律 403）。
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_api_scope
from app.benchmark import active_positions_in_year, compute_report, coverage_missing, run_report_task
from app.db import get_db
from app.limiter import limiter
from app.models import (
    BenchmarkReport,
    Company,
    Country,
    LaborBenchmark,
    Level,
    WorkLocation,
)

router = APIRouter(prefix="/api/v1", tags=["benchmarks"])


class BenchmarkItemIn(BaseModel):
    company_id: int
    level: str = Field(min_length=1)
    country_id: int
    work_location: str = Field(min_length=1)
    salary_before_tax: float = Field(ge=0)
    tax_rate: float = Field(default=0, ge=0, le=100)
    mandatory_fixed_fee: float = Field(default=0, ge=0)


class BenchmarkPushIn(BaseModel):
    year: int = Field(ge=1900, le=2999)
    items: list[BenchmarkItemIn] = Field(min_length=1)


def _reject(stage: str, errors: list[dict]):
    raise HTTPException(400, {"stage": stage, "errors": errors})


@router.post("/benchmarks", status_code=202)
def push_benchmarks(payload: BenchmarkPushIn, request: Request,
                    background: BackgroundTasks,
                    _user=Depends(require_api_scope("benchmarks")),
                    db: Session = Depends(get_db)):
    """推送整年基准快照。L1 格式（pydantic）→ L2 引用 → L3 查重 → L4 覆盖，全过才落库。

    整年替换语义：同 (year) 最后一次成功提交为准。
    """
    items = payload.items
    year = payload.year

    # ---- L2 引用校验（字典必须存在）----
    ref_errors: list[dict] = []
    company_ids = {i.company_id for i in items}
    valid_companies = {c.id for c in db.query(Company).filter(Company.id.in_(company_ids)).all()}
    valid_levels = {lv.code for lv in db.query(Level).all()}
    valid_countries = {c.id for c in db.query(Country).all()}
    valid_locations = {w.name for w in db.query(WorkLocation).all()}
    for idx, it in enumerate(items):
        if it.company_id not in valid_companies:
            ref_errors.append({"index": idx, "reason": f"company_id={it.company_id} 不存在"})
        if it.level not in valid_levels:
            ref_errors.append({"index": idx, "reason": f"level={it.level!r} 不在级别字典"})
        if it.country_id not in valid_countries:
            ref_errors.append({"index": idx, "reason": f"country_id={it.country_id} 不存在"})
        if it.work_location not in valid_locations:
            ref_errors.append({"index": idx, "reason": f"work_location={it.work_location!r} 不在工作地点字典"})
    if ref_errors:
        _reject("reference", ref_errors)

    # ---- L3 包内查重 ----
    seen: dict[tuple, int] = {}
    dup_errors: list[dict] = []
    for idx, it in enumerate(items):
        key = (it.company_id, it.level, it.country_id, it.work_location)
        if key in seen:
            dup_errors.append({"index": idx, "reason":
                               f"包内重复键 {key}（与第 {seen[key]} 行重复）"})
        else:
            seen[key] = idx
    if dup_errors:
        _reject("duplicate", dup_errors)

    # ---- L4 覆盖校验（方案甲：该年在岗岗位必须全部命中本包，缺一即拒收）----
    incoming_lookup = {(it.company_id, it.level, it.country_id, it.work_location): it for it in items}
    total_positions = len(active_positions_in_year(db, year))
    missing = coverage_missing(db, year, incoming_lookup)
    if missing:
        _reject("coverage", {
            "message": f"{len(missing)}/{total_positions} 个在岗岗位未命中基准行，整批拒收",
            "missing": missing,
        })

    # ---- 原子替换整年快照 ----
    db.query(LaborBenchmark).filter(LaborBenchmark.year == year).delete()
    for it in items:
        db.add(LaborBenchmark(
            year=year, company_id=it.company_id, level=it.level,
            country_id=it.country_id, work_location=it.work_location,
            salary_before_tax=it.salary_before_tax, tax_rate=it.tax_rate,
            mandatory_fixed_fee=it.mandatory_fixed_fee,
        ))
    rep = db.query(BenchmarkReport).filter(BenchmarkReport.year == year).first()
    if rep is None:
        rep = BenchmarkReport(year=year)
        db.add(rep)
    rep.status = "pending"
    rep.payload = None
    rep.error_count = 0
    db.commit()

    background.add_task(run_report_task, year)
    return {
        "status": "accepted",
        "year": year,
        "items": len(items),
        "report_status": "computing",
        "coverage": {"positions": total_positions, "matched": total_positions},
    }


@router.get("/benchmarks/reports/{year}")
def get_benchmark_report(year: int, request: Request,
                         _user=Depends(require_api_scope("benchmarks")),
                         db: Session = Depends(get_db)):
    """拉取年度预估报告：ready 返回完整 JSON；计算中返回 pending；从未推送 → 404。

    自愈：有基准数据但报告行缺失（如服务重启丢失后台任务）→ 同步重算后返回。
    """
    has_data = db.query(LaborBenchmark).filter(LaborBenchmark.year == year).count() > 0
    rep = db.query(BenchmarkReport).filter(BenchmarkReport.year == year).first()
    if not has_data and rep is None:
        raise HTTPException(404, f"{year} 年无基准数据")
    if rep is None or rep.status == "pending":
        if rep is None and has_data:
            # 重启丢任务自愈：同步重算
            try:
                result = compute_report(db, year)
                result["generated_at"] = datetime.now(timezone.utc).isoformat()
                rep = BenchmarkReport(year=year, status="ready",
                                      payload=json.dumps(result, ensure_ascii=False),
                                      error_count=len(result.get("unmatched", [])),
                                      generated_at=datetime.now(timezone.utc))
                db.add(rep)
                db.commit()
                return {"year": year, "status": "ready", "report": result}
            except Exception as e:  # noqa: BLE001
                raise HTTPException(500, f"报告生成失败：{e}")
        return {"year": year, "status": "pending"}
    if rep.status == "failed":
        detail = json.loads(rep.payload) if rep.payload else {}
        return {"year": year, "status": "failed", "error": detail.get("error")}
    return {
        "year": year,
        "status": "ready",
        "generated_at": (rep.generated_at.isoformat() if rep.generated_at else None),
        "error_count": rep.error_count,
        "report": (json.loads(rep.payload) if rep.payload else None),
    }
