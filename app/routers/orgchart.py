"""组织架构图路由。

- GET /orgchart                      → JSON（汇报线树数据）
- GET /orgchart?format=md&report=…   → Markdown（org=公司+岗位 / solid=直线汇报线 / dotted=虚线汇报线）
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.export_md import export_md
from app.orgchart import build_orgchart

router = APIRouter(prefix="/api", tags=["orgchart"])


@router.get("/orgchart")
def get_orgchart(format: str | None = None, report: str | None = None, db: Session = Depends(get_db)):
    if format == "md":
        fmt = report or "org"
        try:
            return PlainTextResponse(export_md(db, fmt), media_type="text/markdown; charset=utf-8")
        except ValueError as e:
            raise HTTPException(400, str(e))
    return build_orgchart(db)
