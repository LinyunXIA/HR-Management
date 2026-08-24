# HR_Management — AGENTS 指南（OpenCode 接管）

> 本文件为 **OpenCode** 项目级指令**，替代原 `CLAUDE.md`（已移除，原文件与 `.claude/`、`.mcp.json` 已于 a4a7d1f 清理）。
> 启动时自动加载，优先级：AGENTS.md > docs/PRD.md > docs/DESIGN.md。

## 1. 项目概览

轻量级 HR 管理系统（单用户本地工具），三大能力：**岗位全生命周期**、**员工档案**、**组织架构图（汇报线树）**。
技术栈：**FastAPI + SQLite（v2.5 起，同机三文件隔离）+ SQLAlchemy + Pydantic v2**，前端**原生 JS（零依赖、无 npm/构建）**。
需求与设计见 `docs/PRD.md`、`docs/DESIGN.md`、`docs/API.md`。

## 2. 常用命令

```bash
# 安装依赖（Python 3.14 venv；SQLite 用标准库 sqlite3，无数据库服务）
.venv/bin/pip install -r requirements.txt

# 三环境配置（PRD §7D，单文件 .env 合并版）
cp .env.example .env            # 单文件内含 dev/test/prod 三段，通过 APP_ENV 切换
# .env 内示例：
#   DATABASE_URL_dev=sqlite:///./data/hr_db_dev.db
#   DATABASE_URL_test=sqlite:///./data/hr_db_test.db
#   DATABASE_URL_prod=sqlite:///./data/hr_db_prod.db
# 文件名强制 hr_db_{env}.db，与 APP_ENV 不一致则拒启；相对路径基于项目根规范化

# 启动（dev 默认；自动建表 + 种子数据 + 挂编联动触发器）
.venv/bin/uvicorn main:app --reload --port 7273   # http://127.0.0.1:7273

# 切换环境（dev / test / prod）
APP_ENV=dev  .venv/bin/uvicorn main:app --reload --port 7273   # data/hr_db_dev.db
APP_ENV=test .venv/bin/uvicorn main:app --reload --port 7274   # data/hr_db_test.db
APP_ENV=prod .venv/bin/uvicorn main:app --reload --port 7275   # data/hr_db_prod.db

# 数据导入（幂等 upsert；dev/test 允许 --reset；prod 禁止）
APP_ENV=dev  .venv/bin/python -m scripts.import_csv testingdata/原始文件/Position.csv --reset
APP_ENV=test .venv/bin/python -m scripts.import_csv testingdata/原始文件/Position.csv
APP_ENV=prod .venv/bin/python -m scripts.import_csv testingdata/原始文件/Position.csv

# 或在网页「数据导入」Tab 上传 Position.csv
```

**生产护栏**：`APP_ENV=prod` 时 `--reset` / `Base.metadata.drop_all` 一律被 `app.db.assert_writable()` 拦截并退出码 1。生产重置必须走 `.db 文件复制备份` + 受控迁移，不经本系统。

**无测试/静态检查配置**。验证方式：`curl /api/*` 或无头浏览器 `--headless --dump-dom http://127.0.0.1:7273/#orgchart` 检查 DOM。

## 3. 领域模型（必读）

**职位(Position) ≠ 岗位编号(Position Number)**：
- `positions`（职位/职能）= 干什么活；一个职位可对应多个岗位编号。
- `position_numbers`（岗位编号）= 编制名额（slot），业务主键 `number` 由**系统强制分配**（v2.3）：正式岗 `P{seq}`（P1、P2…）、外包岗 `PA{seq}`（PA1…），纯序号无范围后缀；源文件编号一律忽视。生命周期与员工挂编以它为主体。
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
- `app/db.py:1`：三环境引擎（`DATABASE_URL_{env}` + `APP_ENV` 分流 + SQLite 文件名强制 `hr_db_{env}.db` + 每连接 `PRAGMA foreign_keys=ON/WAL/busy_timeout` + 读写护栏）；单文件 `.env` 合并版
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

- **SQLite 注意**：外键约束靠每连接 `PRAGMA foreign_keys=ON`（`app/db.py` event listener，勿删）；并发写靠事务 `BEGIN IMMEDIATE` + `busy_timeout=30s`（begin event listener 勿删，承接 PG 行锁守卫语义，`with_for_update()` 为 no-op）；金额 `Numeric` 以 float 存储；备份 = 复制 `.db` 文件（连同 `-wal/-shm` 或先 wal_checkpoint）
- `PositionNumber.company` 必须 `foreign_keys=[company_id]`，否则 AmbiguousForeignKeys（存在 `company_id` + `prev_company_id` 双外键）
- `solid_line_manager_id` 未声明 ORM relationship，一律 `db.get(PositionNumber, id)` 查询
- 全局用 `_now()`（timezone-aware，`app/models.py:23`），禁用 `datetime.utcnow`
- 直线经理变更必须 `check_cycle`（`app/helpers.py:74`）环检测
- 岗位有在职员工或已有 `position_events` 时禁止 DELETE（`app/routers/positions.py`）
- 年份精度：`Position.csv` 年份如 `1982` → 存储 `1982-01-01`（Date）

## 7. 前端结构（static/）

单页 hash 路由（`#data_clean` / `#master` / `#positions` / `#employees` / `#orgchart` / `#import`）：
- `static/index.html:1`：单页骨架（6 个 Tab）；header 含环境徽章 `#env-badge`（main.py 注入 window.APP_ENV/APP_DB，dev=绿/test=黄/prod=红）
- `static/js/api.js`：fetch 封装 + `esc` / `statusBadge` / `openModal`
- `static/js/auth.js`：登录态管理；登录弹窗内显示当前环境（防误登）
- `static/js/app.js`：Tab 切换、顶部统计、字典预加载
- `static/js/positions.js`：列表/新建/详情/编辑；含成本字段（auto/manual 互斥）与 `TRANSITIONS`
- `static/js/employees.js`：入职（选 Open/Vacant/Offered）/调岗/离职
- `static/js/orgchart.js`：SVG 汇报线树（`computeLayout()`，`H_SPACE=230`/`V_SPACE=132`）、虚拟根、筛选、折叠、缩放拖拽平移
- `static/js/import.js` / `data_clean.js` / `master_data.js`

## 8. 数据与目录

- `testingdata/原始文件/`：`Org-Chart3.md`（**唯一支持格式**：无编号树 + 权责说明续行）/ `Position.md`（规则）/ `Position.csv`（模版）；`Org-Chart.md` / `Org-Chart2.md` 为历史存档，系统不再解析
- `data/`（gitignored）：SQLite 三环境库文件 `hr_db_{dev,test,prod}.db`（WAL 模式伴生 `-wal/-shm`）+ `backups/`
- `docs/`：`PRD.md` / `DESIGN.md` / `API.md` / `API_PUBLIC.md` / `UI_MOCKUP.html`
- `scripts/import_csv.py`：CLI 导入脚本；`scripts/migrate_pg_master_data.py`：PG→SQLite 主数据一次性迁移
- `.env`（gitignored, 合并版）：`DATABASE_URL_{dev,test,prod}` 三段 sqlite URL + `APP_ENV` 切换；`.env.example` 为模版（含 JWT/limiter 示例）

## 9. Opencode 工作约定

- **沟通**：简洁、客观、基于事实；引用代码用 `file:line` 格式
- **校验**：改动后必须通过执行验证（`curl /api/*`、启动 `uvicorn`、导入 CSV 等）
- **文件操作**：优先 `read`/`edit`，避免不必要的 `bash` 替代；新建文件仅在必要时
- **Git**：未明确要求时不自动 commit/push；commit 前检查 `git status` / `diff`
- **配置变更**：修改 `opencode.json` / `.opencode/**` 后需重启 opencode 生效

## 10. 当前运行状态（2026-08-24 更新）

- **v2.6 已落地（第二轮修订定稿）**：对外 `GET /public/positions` 在岗岗位数据导出（第三方计算用工成本，原基准推送/报告链路整体废弃）；成本六栏 + 税额科目 rate/fixed 两类；**Company 绑税区一对一，全部成本场景统一公司税区口径**（resolve_tax_zone 退役）；scope=public.positions + GET /admin/scopes；`tests/test_public_positions.py` 24 项
- **v2.5 存储切换已落地：PostgreSQL → SQLite 同机三文件**（`data/hr_db_{dev,test,prod}.db`，WAL + 每连接 `PRAGMA foreign_keys=ON`）；psycopg2 已移出运行时依赖；主数据字典已从 PG 迁入（`scripts/migrate_pg_master_data.py`，幂等可重跑）
- **编号系统重制已落地（PRD v2.3）**：源编号一律忽视，导入/创建时系统分配——正式 `P{seq}` / 外包 `PA{seq}`；幂等键=职位名+公司+国家或地区+开启日；经理引用按职位名解析
- **数据清洗仅支持 Org-Chart3 格式**：无编号树 + 权责说明续行；直线经理=真实树祖先（兄弟不互挂、公司清栈）
- 表计数（hr_db_dev）：`position_numbers=4`（Org-Chart3.md 导入：P1~P4）、`companies=4`、users 仅 admin
- **三环境 DB 隔离（PRD §7D）**：单文件 `.env` 内 `DATABASE_URL_{dev,test,prod}` sqlite 三段 + `APP_ENV` 切换；文件名一致性校验 + prod 护栏（复制 .db 备份替代 pg_dump）
- **JWT§7B + 乐观锁§7C + 速率限制§7B.2 已落地**：`PyJWT/bcrypt`、`version` 列、全局 `120/min` / 登录 `10/min` / 公共 `60/min`
- **回归基线**：`tests/test_integration.py` 45/45、`tests/test_v23.py` 43/43 全过（SQLite 下并发抢岗 [200,400] 语义保持）；挂编联动触发器 SQLite 版直写拦截验证通过
- Python 3.14.7 + FastAPI 0.141.1 + SQLAlchemy 2.0.51 已就绪
- opencode 1.18.15 已安装，全局 provider: minimax / openrouter
