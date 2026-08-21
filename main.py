"""FastAPI 入口：建表、注册路由、托管静态文件。"""
import os
import sys

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import models  # noqa: F401  确保模型注册到 Base.metadata
from app.db import APP_ENV, Base, SessionLocal, engine, startup_banner
from app.seed import seed_master_data
from app.routers import auth, data_clean, employees, import_routes, master_data, orgchart, positions, transfers

# 启动时打印三环境自检日志（PRD §7D.2）
print(startup_banner(), file=sys.stderr, flush=True)

# 启动时建表（幂等）+ 初始化主数据字典
# create_all 本身不会删除已有表（非破坏性），无需按 env 区分
Base.metadata.create_all(bind=engine)

# 轻量迁移：为已存在表补 version 列（PRD §7C 乐观锁，存量库兼容）
def _ensure_version_columns():
    try:
        with engine.begin() as conn:
            for tbl in ("position_numbers", "employees"):
                try:
                    conn.execute(text(
                        f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1"
                    ))
                    # 存量 NULL 回填
                    conn.execute(text(f"UPDATE {tbl} SET version=1 WHERE version IS NULL"))
                except Exception as e:
                    print(f"[migrate] {tbl}.version skipped: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[migrate] version columns ensure failed: {e}", file=sys.stderr)

_ensure_version_columns()

with SessionLocal() as db:
    seed_master_data(db)

app = FastAPI(title="轻量级 HR 管理系统", version="1.0.0")

app.include_router(auth.router)
app.include_router(master_data.router)
app.include_router(data_clean.router)
app.include_router(positions.router)
app.include_router(employees.router)
app.include_router(orgchart.router)
app.include_router(import_routes.router)
app.include_router(transfers.router)


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "app": "HR Management", "env": APP_ENV}


@app.get("/", include_in_schema=False)
def index():
    """根路径返回前端单页。"""
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html"))


# 托管前端静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")
