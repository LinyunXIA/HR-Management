"""FastAPI 入口：建表、注册路由、托管静态文件。"""
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import models  # noqa: F401  确保模型注册到 Base.metadata
from app.db import Base, SessionLocal, engine
from app.seed import seed_master_data
from app.routers import data_clean, employees, import_routes, master_data, orgchart, positions, transfers

# 启动时建表（幂等）+ 初始化主数据字典
Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    seed_master_data(db)

app = FastAPI(title="轻量级 HR 管理系统", version="1.0.0")

app.include_router(master_data.router)
app.include_router(data_clean.router)
app.include_router(positions.router)
app.include_router(employees.router)
app.include_router(orgchart.router)
app.include_router(import_routes.router)
app.include_router(transfers.router)


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "app": "HR Management"}


@app.get("/", include_in_schema=False)
def index():
    """根路径返回前端单页。"""
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html"))


# 托管前端静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")
