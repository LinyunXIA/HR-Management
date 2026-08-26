"""Position.csv 上传导入路由。"""
import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import get_db
from app.import_csv import import_csv

router = APIRouter(prefix="/api/v1", tags=["import"])


@router.post("/imports", status_code=201)
async def create_import(file: UploadFile = File(...), _admin=Depends(require_admin),
                        db: Session = Depends(get_db)):
    """创建导入作业：上传 Position.csv 并幂等入库（非破坏性 upsert；v2.4.1 起各环境均允许，
    破坏性操作仍由 assert_writable 拦截）。

    权限（issue #120 裁决 A）：与 data-clean 作业导入对齐，仅 admin——PRD §7B.3
    「主数据字典/导入：admin 维护，hr 只读」。"""
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    report = import_csv(db, reader, strict_legal=True)
    # 作业资源化：返回 201 时附带 Location，实际报告即资源表述
    return report


@router.get("/imports")
def list_imports(_user=Depends(get_current_user)):
    """导入作业列表（当前为无状态，返回空）。"""
    return {"total": 0, "items": []}


@router.get("/imports/{import_id}")
def get_import(import_id: int, _user=Depends(get_current_user)):
    """单条导入作业（无持久化，返回 404）。"""
    from fastapi import HTTPException
    raise HTTPException(404, "导入作业无持久化，请通过 POST /imports 创建")
