"""Position.csv 上传导入路由。"""
import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import APP_ENV, get_db
from app.import_csv import import_csv

router = APIRouter(prefix="/api/v1", tags=["import"])


@router.post("/imports", status_code=201)
async def create_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """创建导入作业：上传 Position.csv 并幂等入库。prod 环境需走受控迁移。"""
    if APP_ENV == "prod":
        raise HTTPException(400, "生产环境禁止通过 Web 直接导入，请走受控迁移（pg_dump 后手工操作）")
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    report = import_csv(db, reader)
    # 作业资源化：返回 201 时附带 Location，实际报告即资源表述
    return report


@router.get("/imports")
def list_imports():
    """导入作业列表（当前为无状态，返回空）。"""
    return {"total": 0, "items": []}


@router.get("/imports/{import_id}")
def get_import(import_id: int):
    """单条导入作业（无持久化，返回 404）。"""
    from fastapi import HTTPException
    raise HTTPException(404, "导入作业无持久化，请通过 POST /imports 创建")
