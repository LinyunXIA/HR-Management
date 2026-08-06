# 轻量级 HR 管理系统

覆盖岗位全生命周期、人员信息、组织架构图（汇报线树）三大能力的轻量 HR 工具。
数据权威为 `Position.csv`（见 [docs/PRD.md](docs/PRD.md)）。

## 启动

```bash
# 1. 安装依赖（Python 3.14）
.venv/bin/pip install -r requirements.txt

# 2. 配置 PostgreSQL 连接（全部新建，无历史迁移）
cp .env.example .env
# 编辑 .env 设置 DATABASE_URL，例如：
#   DATABASE_URL=postgresql://user:password@localhost:5432/hr_db

# 3. 启动（自动建表到 PostgreSQL）
.venv/bin/uvicorn main:app --reload
```

打开 http://127.0.0.1:8000

## 数据导入

```bash
# 命令行导入（幂等；--reset 先清空全部数据）
.venv/bin/python -m scripts.import_csv testingdata/Position.csv --reset

# 或网页「数据导入」页面上传 Position.csv
```

需要先在 PostgreSQL 中创建数据库 `hr_db`。数据从 `testingdata/Position.csv` 全部新建导入。

## 功能

- **主数据配置**：隶属公司 / 级别（按 Position.md 初始化） / 工作地点 / 工作范围 / 国家 / 员工用工税额（按国家维护科目+税率）统一维护。
- **岗位管理**：16 字段岗位档案、岗位编号规则自动生成（不可手填）、生命周期 7 态流转
  （Planned→Open→Offered→Filled→Vacant→Frozen/Closed）、生命周期时间线、直线/虚线经理维护（**仅管理岗**）+ 环检测、
  **人工成本字段**（税前薪资/公司份额/用工成本，自动按国家税率计算 或 手动输入，两种模式互斥）。
- **员工管理**：档案、必须挂岗、入职/调岗/离职自动联动岗位状态（Filled↔Vacant）。
- **组织架构图**：SVG 汇报线树，实线=直线汇报、虚线=虚线汇报，虚拟根「家族自然人」、
  含已关闭岗位开关、**公司聚焦视图**、缩放平移、悬浮详情、**导出 MD（3 种格式：公司+岗位 / 直线汇报线 / 虚线汇报线）**。
- **数据导入**：`Position.csv` 全字段校验导入（**重复编号报错**）、幂等 upsert，返回错误/警告明细。

## 文档

- [docs/PRD.md](docs/PRD.md) — 产品需求文档（术语、规则、状态机、数据权威决策）
- [docs/DESIGN.md](docs/DESIGN.md) — 技术设计文档（数据库、API、前端、实施步骤）
- [docs/API.md](docs/API.md) — 系统 REST API 文档（端点、请求/响应、状态码）
- [docs/API_PUBLIC.md](docs/API_PUBLIC.md) — 对外接口文档（只读，无认证）

## 项目结构

```
main.py                  FastAPI 入口（建表到 PostgreSQL、路由、静态托管）
app/                    后端：models / lifecycle 状态机 / orgchart / import_csv / routers
static/                 前端：原生 JS 单页（岗位/员工/组织图/导入）
scripts/import_csv.py    CLI 导入脚本
testingdata/            源数据：Position.csv / Position.md / Org-Chart.md
.env.example            PostgreSQL 连接配置示例
```
