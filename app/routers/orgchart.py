"""组织架构图资源：GET /org-charts。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.export_md import export_md
from app.orgchart import build_orgchart

router = APIRouter(prefix="/api/v1", tags=["orgchart"])


@router.get("/org-charts")
def get_org_charts(request: Request, report: str | None = None, db: Session = Depends(get_db)):
    """获取组织架构：JSON 或 Markdown（通过 Accept: text/markdown 协商）。

    - Accept: application/json（默认）→ {nodes, solid_edges, dotted_edges, roots}
    - Accept: text/markdown → Markdown 文本，需 report=org|solid|dotted
    """
    accept = request.headers.get("accept", "")
    wants_md = "text/markdown" in accept.lower()
    # 兼容旧 query ?format=md
    fmt_param = request.query_params.get("format")
    if fmt_param == "md":
        wants_md = True

    if wants_md:
        fmt = report or "org"
        try:
            md = export_md(db, fmt)
            return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")
        except ValueError as e:
            raise HTTPException(400, str(e))

    # 若显式 report 且未要求 md，但通过 query 指定 report，仍按 JSON 返回完整数据
    # 为支持导出场景，前端可通过 Accept 头请求 MD
    if report and wants_md:
        try:
            md = export_md(db, report)
            return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")
        except ValueError as e:
            raise HTTPException(400, str(e))

    return build_orgchart(db)
