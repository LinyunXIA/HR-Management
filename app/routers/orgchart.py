"""组织架构图路由。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.orgchart import build_orgchart

router = APIRouter(prefix="/api", tags=["orgchart"])


@router.get("/orgchart")
def get_orgchart(db: Session = Depends(get_db)):
    return build_orgchart(db)
