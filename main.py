"""FastAPI 入口：建表、注册路由、托管静态文件。"""
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import models  # noqa: F401  确保模型注册到 Base.metadata
from app.db import Base, engine
from app.routers import employees, import_routes, orgchart, positions

# 启动时建表（幂等）
Base.metadata.create_all(bind=engine)

app = FastAPI(title="轻量级 HR 管理系统", version="1.0.0")

app.include_router(positions.router)
app.include_router(employees.router)
app.include_router(orgchart.router)
app.include_router(import_routes.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "HR Management"}


@app.get("/", include_in_schema=False)
def index():
    """根路径返回前端单页。"""
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html"))


# 托管前端静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")
