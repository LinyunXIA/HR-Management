"""数据清洗作业资源：POST /data-clean-jobs。"""
import io
import csv as csv_mod
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from app.data_clean import run_clean
from app.db import APP_ENV, SessionLocal
from app.import_csv import import_csv

router = APIRouter(prefix="/api/v1", tags=["data-clean"])

RAW_DIR = Path(__file__).resolve().parent.parent.parent / "testingdata" / "原始文件"

# 内存作业存储（进程内，无持久化）
_JOBS: dict[str, dict] = {}


class ParseRequest(BaseModel):
    orgchart_path: str | None = None
    rules_path: str | None = None


@router.get("/data-clean-jobs")
def list_data_clean_jobs():
    """作业列表。"""
    return {"total": len(_JOBS), "items": list(_JOBS.values())}


@router.get("/data-clean-jobs/{job_id}")
def get_data_clean_job(job_id: str):
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "清洗作业不存在")
    return job


@router.get("/data-clean-jobs/files/list")
def list_raw_files():
    """列出原始文件（兼容保留）。"""
    files = []
    if RAW_DIR.exists():
        for f in sorted(RAW_DIR.iterdir()):
            if f.is_file():
                files.append({"name": f.name, "size": f.stat().st_size, "path": str(f)})
    return {"directory": str(RAW_DIR), "files": files}


@router.post("/data-clean-jobs", status_code=201)
async def create_data_clean_job(orgchart: UploadFile | None = File(None)):
    """创建清洗作业：上传 Org-Chart.md 或使用服务器原始文件。"""
    if orgchart is not None:
        org_text = (await orgchart.read()).decode("utf-8")
        rules_path = RAW_DIR / "Position.md"
        if not rules_path.exists():
            raise HTTPException(500, "规则文件 Position.md 不存在")
        rules_text = rules_path.read_text(encoding="utf-8")
    else:
        org_path = RAW_DIR / "Org-Chart.md"
        rules_path = RAW_DIR / "Position.md"
        if not org_path.exists() or not rules_path.exists():
            raise HTTPException(400, "原始文件不存在")
        org_text = org_path.read_text(encoding="utf-8")
        rules_text = rules_path.read_text(encoding="utf-8")

    result = run_clean(org_text, rules_text)
    job_id = uuid.uuid4().hex[:8]
    job = {
        "id": job_id,
        "total_positions": result["report"]["total_positions"],
        "report": result["report"],
        "csv_text": result["csv_text"],
        "cleaned": result["cleaned"],
        "template": "Position.csv",
    }
    _JOBS[job_id] = job
    return job


@router.post("/data-clean-jobs/{job_id}/imports", status_code=201)
def import_data_clean_job(job_id: str):
    """将清洗作业的 CSV 导入系统（幂等 upsert）。prod 环境需走受控迁移。"""
    if APP_ENV == "prod":
        raise HTTPException(400, "生产环境禁止通过清洗作业直接导入，请走受控迁移（pg_dump 后手工操作）")
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "清洗作业不存在")
    csv_text = job["csv_text"]
    reader = csv_mod.DictReader(io.StringIO(csv_text))
    db = SessionLocal()
    try:
        import_report = import_csv(db, reader)
        return {"clean_report": job["report"], "import_report": import_report, "job_id": job_id}
    finally:
        db.close()


# 兼容旧端点（301 迁移提示，实际已替换为 /data-clean-jobs）
@router.post("/data-clean/parse")
def legacy_parse(req: ParseRequest | None = None):
    raise HTTPException(410, "已迁移至 POST /api/v1/data-clean-jobs")

@router.post("/data-clean/upload-parse")
async def legacy_upload_parse(orgchart: UploadFile = File(...)):
    raise HTTPException(410, "已迁移至 POST /api/v1/data-clean-jobs")

@router.post("/data-clean/import")
def legacy_import():
    raise HTTPException(410, "已迁移至 POST /api/v1/data-clean-jobs/{job_id}/imports")

@router.get("/data-clean/files")
def legacy_files():
    raise HTTPException(410, "已迁移至 GET /api/v1/data-clean-jobs/files/list")
