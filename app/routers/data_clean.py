"""数据清洗作业资源：POST /data-clean-jobs。"""
import io
import csv as csv_mod
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from app.auth import get_current_user
from app.data_clean import run_clean
from app.db import SessionLocal
from app.import_csv import import_csv

router = APIRouter(prefix="/api/v1", tags=["data-clean"])

RAW_DIR = Path(__file__).resolve().parent.parent.parent / "testingdata" / "原始文件"

# 内存作业存储（进程内，无持久化）
_JOBS: dict[str, dict] = {}


class ParseRequest(BaseModel):
    orgchart_path: str | None = None
    rules_path: str | None = None


@router.get("/data-clean-jobs")
def list_data_clean_jobs(_user=Depends(get_current_user)):
    """作业列表。"""
    return {"total": len(_JOBS), "items": list(_JOBS.values())}


@router.get("/data-clean-jobs/{job_id}")
def get_data_clean_job(job_id: str, _user=Depends(get_current_user)):
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "清洗作业不存在")
    return job


@router.get("/data-clean-jobs/files/list")
def list_raw_files(_user=Depends(get_current_user)):
    """列出原始文件（兼容保留）。"""
    files = []
    if RAW_DIR.exists():
        for f in sorted(RAW_DIR.iterdir()):
            if f.is_file():
                files.append({"name": f.name, "size": f.stat().st_size, "path": str(f)})
    return {"directory": str(RAW_DIR), "files": files}


@router.post("/data-clean-jobs", status_code=201)
async def create_data_clean_job(
    orgchart: UploadFile | None = File(None),
    source_file: str | None = None,
    _user=Depends(get_current_user),
):
    """创建清洗作业：上传 Org-Chart.md，或用 ?source_file= 指定服务器原始文件（仅限 testingdata/原始文件/ 下的 .md）。

    未指定时默认使用服务器 Org-Chart.md。
    """
    rules_path = RAW_DIR / "Position.md"
    if not rules_path.exists():
        raise HTTPException(500, "规则文件 Position.md 不存在")
    rules_text = rules_path.read_text(encoding="utf-8")

    if orgchart is not None:
        org_text = (await orgchart.read()).decode("utf-8")
    else:
        filename = "Org-Chart.md"
        if source_file:
            # 防路径穿越：仅取文件名，且必须是 RAW_DIR 下已存在的 .md
            filename = Path(source_file).name
            if not filename.endswith(".md"):
                raise HTTPException(400, "source_file 仅支持 .md 文件")
        org_path = RAW_DIR / filename
        if not org_path.exists():
            available = [f.name for f in RAW_DIR.glob("*.md")]
            raise HTTPException(400, f"原始文件不存在: {filename}（可用: {available}）")
        org_text = org_path.read_text(encoding="utf-8")

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
    """将清洗作业的 CSV 导入系统（幂等 upsert，非破坏性；v2.4.1 起各环境均允许，
    破坏性操作仍由 assert_writable 拦截）。"""
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "清洗作业不存在")
    csv_text = job["csv_text"]
    reader = csv_mod.DictReader(io.StringIO(csv_text))
    db = SessionLocal()
    try:
        import_report = import_csv(db, reader, strict_legal=True)
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
