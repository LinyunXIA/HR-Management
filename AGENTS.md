# HR_Management — AGENTS 指南（OpenCode 接管）

> 本文件为 **OpenCode** 项目级指令**，替代原 `CLAUDE.md`（已移除，原文件与 `.claude/`、`.mcp.json` 已于 a4a7d1f 清理）。
> 启动时自动加载，优先级：AGENTS.md > docs/PRD.md > docs/DESIGN.md。

## 1. 项目概览

轻量级 HR 管理系统（单用户本地工具），三大能力：**岗位全生命周期**、**员工档案**、**组织架构图（汇报线树）**。
技术栈：**FastAPI + PostgreSQL + SQLAlchemy + Pydantic v2**，前端**原生 JS（零依赖、无 npm/构建）**。
需求与设计见 `docs/PRD.md`、`docs/DESIGN.md`、`docs/API.md`。

## 2. 常用命令

```bash
# 安装依赖（Python 3.14 venv）
.venv/bin/pip install -r requirements.txt

# 三环境配置（PRD §7D，单文件 .env 合并版）
cp .env.example .env            # 单文件内含 dev/test/prod 三段，通过 APP_ENV 切换
# .env 内示例：
#   DATABASE_URL_dev=postgresql://.../hr_db_dev
#   DATABASE_URL_test=postgresql://.../hr_db_test
#   DATABASE_URL_prod=postgresql://.../hr_db_prod  # 含 ${POSTGRES_PASSKEY}
# 库名强制 hr_db_{env}，不一致则拒启；旧的 .env.test/.env.prod 仍兼容

# 启动（dev 默认；自动建表 + 种子数据）
.venv/bin/uvicorn main:app --reload --port 7273   # http://127.0.0.1:7273

# 切换环境（dev / test / prod）
APP_ENV=dev  .venv/bin/uvicorn main:app --reload --port 7273
APP_ENV=test .venv/bin/uvicorn main:app --reload --port 7274   # hr_db_test
APP_ENV=prod .venv/bin/uvicorn main:app --reload --port 7275   # hr_db_prod

# 数据导入（幂等 upsert；dev/test 允许 --reset；prod 禁止）
APP_ENV=dev  .venv/bin/python -m scripts.import_csv testingdata/原始文件/Position.csv --reset
APP_ENV=test .venv/bin/python -m scripts.import_csv testingdata/原始文件/Position.csv
APP_ENV=prod .venv/bin/python -m scripts.import_csv testingdata/原始文件/Position.csv

# 或在网页「数据导入」Tab 上传 Position.csv
```

**生产护栏**：`APP_ENV=prod` 时 `--reset` / `Base.metadata.drop_all` 一律被 `app.db.assert_writable()` 拦截并退出码 1。生产重置必须走 `pg_dump hr_db_prod` + 受控迁移，不经本系统。

**无测试/静态检查配置**。验证方式：`curl /api/*` 或无头浏览器 `--headless --dump-dom http://127.0.0.1:7273/#orgchart` 检查 DOM。

## 3. 领域模型（必读）

**职位(Position) ≠ 岗位编号(Position Number)**：
- `positions`（职位/职能）= 干什么活；一个职位可对应多个岗位编号。
- `position_numbers`（岗位编号）= 编制名额（slot），业务主键 `number` 格式 `P{seq}-{scope}`，Country 级 `P{seq}-4-{国家编号}`。生命周期与员工挂编以它为主体。
- 汇报关系**岗位→岗位**：`position_numbers.solid_line_manager_id` 自引用（直线，0..1）、`position_number_dotted_lines`（虚线，0..N）。

**数据权威（PRD §3.7）**：`testingdata/原始文件/Position.csv`（16 列）为唯一权威；`Org-Chart.md / Org-Chart2.md` 仅展示参考，系统不直接读取。两者不一致时以 CSV 为准。

**员工必须挂岗**：在职员工 `position_number_id` 必填且 `unique`（一对一）；离职经 offboard 解绑置 NULL。挂编条件 = 岗位状态 `open / vacant / offered`。

**管理岗判定**：`levels.is_management = code startswith 'M'`；直线/虚线经理下拉仅限管理岗。

## 4. 生命周期状态机（app/lifecycle.py:1）

7 态：`planned → open → offered → filled → vacant → frozen → closed`

- `ALLOWED_MANUAL`（app/lifecycle.py:7）：UI 手动流转 `POST /positions/{id}/transition`（`system=False`）
- `ALLOWED_EMPLOYEE`（app/lifecycle.py:17）：员工动作系统流转 `system=True`：`open/vacant/offered→filled`, `filled→vacant`
- **`filled↔vacant` 只能由员工入职/调岗/离职触发，不能手动流转**
- 前端 `static/js/positions.js` 的 `TRANSITIONS` 必须与 `ALLOWED_MANUAL` 同步
- 每次流转写一条 `position_events`；`→closed` 写 `closing_date`，重激活清空

## 5. 后端结构（app/）

- `main.py:1`：入口；`Base.metadata.create_all` + `seed_master_data`；注册 6 个 router；`/` 返回 `static/index.html`；挂载 `/static`
- `app/db.py:1`：三环境引擎（`DATABASE_URL_{env}` + `APP_ENV` 分流 + 库名强制 `hr_db_{env}` + 读写护栏 + `limiter`）；单文件 `.env` 合并版
- `app/auth.py`：JWT 签发/校验（`Authorization: Bearer`）、bcrypt；`app/limiter.py` 全局限流（`120/min`，登录 `10/min`）
- `app/models.py:1`：14 张表（含 `users`）+ `position_numbers/employees.version` 乐观锁
- `app/helpers.py:1`：`validate_number_format` / `generate_number` / `check_cycle` / `set_dotted_lines` / `serialize_position`
- `app/lifecycle.py:1`：状态机 + 事件记录
- `app/orgchart.py`：`build_orgchart(db)` → `{nodes, solid_edges, dotted_edges, roots}`（含虚拟根“家族自然人”）
- `app/import_csv.py`：三趟导入（建字典→upsert→解析经理外键+环检测）
- `app/export_md.py`：组织图导出 MD（3 格式：org/solid/dotted）
- `app/data_clean.py`：Org-Chart.md 解析清洗
- `app/seed.py:1`：主数据初始化（从 Position.md 级别对照表解析，fallback 到内置 19 级）
- `app/routers/`：`auth.py` / `master_data.py` / `data_clean.py` / `positions.py` / `employees.py` / `orgchart.py` / `import_routes.py`
- `app/schemas.py`：Pydantic 校验

## 6. 关键实现约束

- `PositionNumber.company` 必须 `foreign_keys=[company_id]`（`app/models.py:213`），否则 AmbiguousForeignKeys（存在 `company_id` + `prev_company_id` 双外键）
- `solid_line_manager_id` 未声明 ORM relationship，一律 `db.get(PositionNumber, id)` 查询
- 全局用 `_now()`（timezone-aware，`app/models.py:23`），禁用 `datetime.utcnow`
- 直线经理变更必须 `check_cycle`（`app/helpers.py:74`）环检测
- 岗位有在职员工或已有 `position_events` 时禁止 DELETE（`app/routers/positions.py`）
- 年份精度：`Position.csv` 年份如 `1982` → 存储 `1982-01-01`（Date）

## 7. 前端结构（static/）

单页 hash 路由（`#data_clean` / `#master` / `#positions` / `#employees` / `#orgchart` / `#import`）：
- `static/index.html:1`：单页骨架（6 个 Tab）
- `static/js/api.js`：fetch 封装 + `esc` / `statusBadge` / `openModal`
- `static/js/app.js`：Tab 切换、顶部统计、字典预加载
- `static/js/positions.js`：列表/新建/详情/编辑；含成本字段（auto/manual 互斥）与 `TRANSITIONS`
- `static/js/employees.js`：入职（选 Open/Vacant/Offered）/调岗/离职
- `static/js/orgchart.js`：SVG 汇报线树（`computeLayout()`，`H_SPACE=230`/`V_SPACE=132`）、虚拟根、筛选、折叠、缩放拖拽平移
- `static/js/import.js` / `data_clean.js` / `master_data.js`

## 8. 数据与目录

- `testingdata/原始文件/`：`Position.csv`（权威数据源）/ `Position.md`（规则）/ `Org-Chart.md` / `Org-Chart2.md`（参考）
- `data/`（gitignored）：运行时文件存储（当前主用 PostgreSQL）
- `docs/`：`PRD.md` / `DESIGN.md` / `API.md` / `API_PUBLIC.md` / `UI_MOCKUP.html`
- `scripts/import_csv.py`：CLI 导入脚本
- `.env`（gitignored, 合并版）：`DATABASE_URL_{dev,test,prod}` 三段 + `APP_ENV` 切换；`.env.example` 为模版（含 JWT/limiter 示例）

## 9. Opencode 工作约定

- **沟通**：简洁、客观、基于事实；引用代码用 `file:line` 格式
- **校验**：改动后必须通过执行验证（`curl /api/*`、启动 `uvicorn`、导入 CSV 等）
- **文件操作**：优先 `read`/`edit`，避免不必要的 `bash` 替代；新建文件仅在必要时
- **Git**：未明确要求时不自动 commit/push；commit 前检查 `git status` / `diff`
- **配置变更**：修改 `opencode.json` / `.opencode/**` 后需重启 opencode 生效

## 10. 当前运行状态（2026-08-22 扫描）

- PostgreSQL 18.4（Postgres.app）已连通：`hr_db_dev` / `hr_db_test` / `hr_db_prod` 三库并存（prod 含 `${POSTGRES_PASSKEY}` 展开）
- **三环境 DB 隔离（PRD §7D 合并版）已落地**：单文件 `.env` 内 `DATABASE_URL_{dev,test,prod}` + `APP_ENV` 切换；库名一致性校验 + prod 护栏 + `${POSTGRES_PASSKEY}` 展开生效
- **JWT§7B + 乐观锁§7C + 速率限制§7B.2 已落地**：`PyJWT/bcrypt`、`version` 列、全局 `120/min` / 登录 `10/min` / 公共 `60/min`
- 表计数（hr_db_dev）：`position_numbers=5`（testingdata 当前为 5 行精简版，待扩充到 91）
- Python 3.14.7 + FastAPI 0.141.1 + SQLAlchemy 2.0.51 已就绪
- 分支 `main` 与 `origin/main` 同步，工作区 clean
- opencode 1.18.15 已安装，全局 provider: minimax / openrouter
