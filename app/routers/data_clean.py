"""数据清洗路由：解析原始文件 → 预览 → 确认导入。"""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from app.data_clean import run_clean
from app.db import SessionLocal
from app.import_csv import import_csv

router = APIRouter(prefix="/api/data-clean", tags=["data-clean"])

# 原始文件目录
RAW_DIR = Path(__file__).resolve().parent.parent.parent / "testingdata" / "原始文件"


class ParseRequest(BaseModel):
    """指定原始文件路径进行解析（可选，也可上传文件）。"""
    orgchart_path: str | None = None
    rules_path: str | None = None


class ParseResponse(BaseModel):
    total_positions: int
    report: dict
    csv_text: str
    cleaned: list


@router.get("/files")
def list_raw_files():
    """列出 testingdata/原始文件/ 下的文件。"""
    files = []
    if RAW_DIR.exists():
        for f in sorted(RAW_DIR.iterdir()):
            if f.is_file():
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "path": str(f),
                })
    return {"directory": str(RAW_DIR), "files": files}


@router.post("/parse", response_model=ParseResponse)
def parse_raw_files(req: ParseRequest | None = None):
    """解析原始文件（Org-Chart.md + Position.md），执行数据清洗，返回报告+预览。

    默认从 testingdata/原始文件/ 读取；也可通过 req 指定路径。
    """
    org_path = Path(req.orgchart_path) if req and req.orgchart_path else RAW_DIR / "Org-Chart.md"
    rules_path = Path(req.rules_path) if req and req.rules_path else RAW_DIR / "Position.md"

    if not org_path.exists():
        raise HTTPException(400, f"Org-Chart.md 不存在：{org_path}")
    if not rules_path.exists():
        raise HTTPException(400, f"Position.md 不存在：{rules_path}")

    org_text = org_path.read_text(encoding="utf-8")
    rules_text = rules_path.read_text(encoding="utf-8")

    result = run_clean(org_text, rules_text)

    return ParseResponse(
        total_positions=result["report"]["total_positions"],
        report=result["report"],
        csv_text=result["csv_text"],
        cleaned=result["cleaned"],
    )


@router.post("/import")
def import_cleaned_data():
    """确认导入：解析并执行 CSV 导入（幂等 upsert）。"""
    org_path = RAW_DIR / "Org-Chart.md"
    rules_path = RAW_DIR / "Position.md"

    if not org_path.exists() or not rules_path.exists():
        raise HTTPException(400, "原始文件不存在")

    org_text = org_path.read_text(encoding="utf-8")
    rules_text = rules_path.read_text(encoding="utf-8")

    result = run_clean(org_text, rules_text)
    csv_text = result["csv_text"]

    # 使用现有 import_csv 导入
    import io
    import csv as csv_mod
    reader = csv_mod.DictReader(io.StringIO(csv_text))

    db = SessionLocal()
    try:
        import_report = import_csv(db, reader)
        return {
            "clean_report": result["report"],
            "import_report": import_report,
        }
    finally:
        db.close()


@router.post("/upload-parse")
async def upload_orgchart(orgchart: UploadFile = File(...)):
    """上传 Org-Chart.md 并解析（规则文件使用固定模版 Position.md）。

    返回清洗后的 CSV 预览（格式与 Position.csv 模版对齐）。
    """
    org_text = (await orgchart.read()).decode("utf-8")

    # 规则文件使用固定的 Position.md
    rules_path = RAW_DIR / "Position.md"
    if not rules_path.exists():
        raise HTTPException(500, "规则文件 Position.md 不存在")
    rules_text = rules_path.read_text(encoding="utf-8")

    result = run_clean(org_text, rules_text)

    return {
        "total_positions": result["report"]["total_positions"],
        "report": result["report"],
        "csv_text": result["csv_text"],
        "cleaned": result["cleaned"],
        "template": "Position.csv",
    }
