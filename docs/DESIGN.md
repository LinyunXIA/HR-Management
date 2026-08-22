# 轻量级 HR 管理系统 — 技术设计文档（DESIGN）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v2.2 |
| 更新日期 | 2026-08-22 |
| 关联 | [PRD.md](./PRD.md)（v2.2，需求与决策） |
| 目标版本 | V2.2 |

---

## 1. 概述

基于集团既有职位规则与数据（`Position.md` / `Position.csv` / `Org-Chart.md`），构建轻量级 HR 管理系统，覆盖主数据配置、岗位全生命周期（含人工成本字段）、人员信息、组织架构图（汇报线树 + 导出 MD）、JWT 认证、乐观锁、速率限制、三环境 DB 隔离能力。本文档定义技术实现：技术选型、数据库设计、生命周期状态机、REST API、CSV 导入、前端与组织架构图渲染、实施步骤与验证方案。

关键既定决策（详见 PRD §3.7/§10）：
- **数据权威**：以 `Position.csv` 为准；`Org-Chart.md` 仅展示参考，V1 不直接读取。
- **组织图主视图**：汇报线树（节点=岗位，按直线经理成树，虚线另行标注）。
- **外包岗位**：V2.1 起纳入，外包岗以 `External Employee` 导入系统（与内部全职同流程）。
- **员工必须挂岗**：不允许「待分配」员工。
- **虚拟根节点**：「家族自然人股东」默认开启、可开关。
- **年份精度**：开启/关闭日存 date，年份→`YYYY-01-01`。
- **主数据字典化**：公司/级别/工作地点/工作范围/国家/法律强制/员工用工税额均为可维护字典（F0）。
- **经理下拉限定管理岗**：直线/虚线经理下拉仅显示级别 M 开头的岗位。
- **岗位成本字段**：自动（按国家税务科目）/ 手动两种模式互斥。
- **组织图导出 MD**：公司+岗位 / 直线汇报线 / 虚线汇报线 3 种格式。
- **三环境 DB 隔离（PRD §7D 合并版）**：单文件 `.env` 内 `DATABASE_URL_{dev,test,prod}` + `APP_ENV` 切换，含 `${POSTGRES_PASSKEY}` 展开。
- **安全**：JWT（`PyJWT`/`bcrypt`）+ 乐观锁（`version`）+ 速率限制（`slowapi`）。

## 2. 技术选型

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 语言/运行时 | Python 3.14.6（项目 .venv） | 已验证存在 |
| Web 框架 | FastAPI（同步端点） | REST API + 托管静态文件 |
| ORM | SQLAlchemy 2.x（同步） | 单用户本地工具，无需异步 |
| 校验 | Pydantic v2 | 与 FastAPI 集成 |
| 数据库 | **PostgreSQL 同机三库 `hr_db_dev/test/prod`** | 持久化关系型数据库，全部新建（无历史迁移），`APP_ENV` 分流（`app/db.py:1`） |
| 驱动 | psycopg2-binary | Python PostgreSQL 适配器 |
| 认证 | PyJWT + bcrypt | JWT HS256（`sub/role/exp`），`Authorization: Bearer`（`app/auth.py:1`） |
| 限流 | slowapi + limits | 全局 `120/min` / 登录 `10/min` / 公共 `60/min`（`app/limiter.py:1`） |
| 前端 | 原生 JS + 自定义 SVG 树渲染 | **零依赖、无 npm、无构建** |
| 启动 | `uvicorn main:app --reload` | 一条命令，打印 `APP_ENV` 脱敏库名（`main.py:1`） |

**组织架构图渲染决策**：自定义 SVG 树渲染器（约 350 行，零依赖）。理由：汇报线树结构明确、需同时绘制实线（直线）与跨树虚线（虚线汇报），SVG 完全可控，符合「轻量级」定位。备选：`vis-network`（vendored，hierarchical 布局 + dashes 边），若自定义布局工作量失控时启用。

依赖清单（`requirements.txt`）：`fastapi`、`uvicorn[standard]`、`sqlalchemy>=2.0`、`pydantic>=2.7`、`python-multipart`、`psycopg2-binary`、`PyJWT>=2.8`、`bcrypt>=4.1`、`slowapi`、`limits`。

## 3. 项目结构

```
HR_Management/
├── main.py                 # FastAPI 入口：建表（PostgreSQL）、注册路由、限流、version 迁移
├── requirements.txt
├── .env                    # 单文件合并版：DATABASE_URL_{dev,test,prod} + APP_ENV + JWT/limiter（gitignored）
├── .env.example            # 模版（含三环境 + JWT + limiter 示例）
├── app/                    # 数据库连接由 APP_ENV + DATABASE_URL_{env} 驱动
│   ├── __init__.py
│   ├── db.py               # engine / SessionLocal / Base + 三环境分流 + ${POSTGRES_PASSKEY} 展开 + 护栏
│   ├── limiter.py          # 全局限流器（120/min）
│   ├── auth.py             # JWT 签发/校验 + bcrypt（app/auth.py:1）
│   ├── models.py           # SQLAlchemy 模型（14 表，含 users + version）
│   ├── schemas.py          # Pydantic 模型（version 透传）
│   ├── helpers.py          # 岗位解析、编号规则、序列化、环检测、assert_version
│   ├── lifecycle.py        # 状态机 + 流转校验 + 事件记录
│   ├── orgchart.py         # 组织树构建 + 环检测
│   ├── data_clean.py       # Org-Chart.md 解析 + 清洗（兼容多种格式）
│   ├── export_md.py        # 组织图导出 MD（3 格式）
│   ├── seed.py             # 主数据初始化 + 管理员种子 admin/admin123
│   ├── import_csv.py       # Position.csv 解析/校验/入库
│   └── routers/
│       ├── __init__.py
│       ├── auth.py         # 认证：POST /auth/login|register|register-first, GET /auth/me|/users
│       ├── master_data.py  # 主数据配置 + GET /public/companies（JWT + 60/min）
│       ├── positions.py    # 岗位（含 version 校验、环检测、成本计算）
│       ├── employees.py    # 员工（含 version 校验）
│       ├── transfers.py    # 调岗
│       ├── orgchart.py     # GET /org-charts
│       ├── data_clean.py   # 数据清洗路由
│       └── import_routes.py
├── static/
│   ├── index.html          # 单页，Tab：数据清洗/主数据/岗位/员工/组织架构/导入 + 登录徽章
│   ├── css/style.css       # 含 .auth-badge
│   └── js/
│       ├── api.js          # fetch 封装（自动 Authorization + 401/429/409 处理）
│       ├── auth.js         # 登录态管理（JWT 本地存储 + 徽章）
│       ├── app.js          # Tab 路由 + Auth.fetchMe
│       ├── master_data.js  # 主数据配置页
│       ├── positions.js    # 岗位列表/详情/生命周期/成本字段（version 透传、409 提示）
│       ├── employees.js    # 员工管理/入职离职调岗（version 透传）
│       ├── orgchart.js     # SVG 汇报线树渲染 + 公司聚焦 + 导出
│       ├── data_clean.js   # 数据清洗页
│       └── import.js       # 导入页
├── scripts/
│   └── import_csv.py       # CLI 导入（含 prod --reset 拦截）
├── testingdata/            # 源数据 Position.csv / Position.md / Org-Chart.md
└── docs/                   # PRD.md / DESIGN.md / API.md / API_PUBLIC.md / UI_MOCKUP.html
```

## 4. 数据库设计

### 4.1 表结构

```python
# app/models.py —— 关键模型（v2.2：新增 users + version 乐观锁）

# ---- 系统用户（JWT，PRD §7B）----
class User(Base):
    __tablename__ = 'users'
    id, username(unique), hashed_password(bcrypt), role, is_active, created_at
    # 种子：admin/admin123（DEFAULT_ADMIN_USER/PASSWORD 可覆盖，app/seed.py:1）

# ---- 主数据字典（F0，均可维护）----
class Company(Base):               # 隶属公司
    __tablename__ = 'companies'
    id, name(unique), is_active

class Country(Base):               # 国家/地区（Country 范围二级菜单）
    __tablename__ = 'countries'
    id, name(unique), code        # code 如 '4-1'..'4-9'（编号规则用）

class Level(Base):                 # 级别（按 Position.md §3.6 初始化 19 项）
    __tablename__ = 'levels'
    id, code(unique)              # B6 / B7a / … / M12b
    label, is_management, sort_order   # is_management = code 以 M 开头

class WorkLocation(Base):          # 工作地点（按 §3.3 初始化 12 个）
    __tablename__ = 'work_locations'
    id, name(unique), sort_order

class Scope(Base):                 # 工作范围（Family/Global/Regional/Country）
    __tablename__ = 'scopes'
    id, code(unique), label, suffix_code(1..4), sort_order   # suffix_code 驱动编号

class LegalCategory(Base):         # 法律强制/可选（按 §3.5 初始化 4 类）
    __tablename__ = 'legal_categories'
    id, name(unique), sort_order

class PositionType(Base):          # 职位类型（PRD §3.7 三类）
    __tablename__ = 'position_types'
    id, name(unique), sort_order  # Consultant / Employee / External Employee

class EmploymentTaxItem(Base):     # 员工用工税额（按国家）
    __tablename__ = 'employment_tax_items'
    id, country_id FK, item_name(科目), tax_rate Numeric(税率%), is_active

# ---- 业务表 ----
class Position(Base):              # 职位（职能，不含工作范围）
    __tablename__ = 'positions'
    id, name(unique)

class PositionNumber(Base):        # 岗位编号（管理主体）
    __tablename__ = 'position_numbers'
    id
    number        unique          # P{seq}-{scope}，如 P063-4-5
    position_id   FK positions
    company_id    FK companies
    level         String(代码)     # 级别代码（如 M8a），对 levels 字典校验；M 开头=管理岗
    scope         Enum(family|global|regional|country)   # scopes 字典驱动下拉与编号
    country_id    FK countries, nullable    # scope=country 时必填
    opening_date  Date, nullable
    closing_date  Date, nullable
    work_location String(名称)     # 来自 work_locations 字典
    job_responsibility
    legal_category String(名称)    # 来自 legal_categories 字典
    position_type  String(名称)    # 职位类型：Consultant/Employee/External Employee
    solid_line_manager_id  FK self, nullable   # 直线经理（仅管理岗可选）
    org_chart_display, prev_position_id, prev_company_id FK companies, remark
    status        Enum(planned|open|offered|filled|vacant|frozen|closed)
    # ---- v1.1 成本字段 ----
    cost_mode     Enum(auto|manual), default manual   # 两种模式互斥
    salary_before_tax  Numeric(14,2), nullable   # 税前薪资（人工）
    company_share      Numeric(14,2), nullable   # 公司份额（人工）
    labor_cost         Numeric(14,2), nullable   # 用工成本（人工）
    version       Integer, default 1            # 乐观锁版本号（PRD §7C，app/models.py:1）
    created_at, updated_at

class PositionNumberDottedLine(Base):  # 虚线经理（多对多，仅管理岗可选）
    __tablename__ = 'position_number_dotted_lines'
    id, position_number_id FK, dotted_manager_id FK
    Unique(position_number_id, dotted_manager_id)

class PositionEvent(Base):         # 生命周期事件（时间线）
    __tablename__ = 'position_events'
    id, position_number_id FK, employee_id FK nullable
    from_status nullable, to_status, changed_at datetime, note nullable

class Employee(Base):              # 人员档案
    __tablename__ = 'employees'
    id, employee_no unique, name, gender
    birth_date, phone, email, hire_date
    employee_type, employment_status
    position_number_id  FK NOT NULL       # 必须挂岗
    remark, version Integer default 1,    # 乐观锁（PRD §7C）
    created_at, updated_at
```

> 实现说明：`level / work_location / legal_category` 在 `position_numbers` 上保留**字符串**（与 CSV 一致），由字典表提供下拉选项并在创建/更新时校验；`scope` 保留枚举，由 `scopes` 字典驱动下拉展示与编号。成本字段为新增列，导入时置空（人工模式默认）。`version` 存量库由 `main.py:1` 的 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 轻量迁移补齐。`company_id` / `prev_company_id` 双外键需在 `relationship` 上显式 `foreign_keys`（`prev_company = relationship(Company, foreign_keys=[prev_company_id])`）避免 `AmbiguousForeignKeys`（#12）。

> 创建校验：`PositionNumberCreate` **不接收 `number`**（新建只能自动生成，编辑不改编号）；`EmployeeCreate` 中 `birth_date / phone / email / remark` 可空。`PATCH` 需携带 `version`，`app/helpers.py:1` 的 `assert_version` 负责 409。`LegalCategory` 枚举为历史兼容，运行时以 `legal_categories` 字典表为准（#16）。

> `employees` 的 `CHECK (employment_status IN (...) OR position_number_id IS NOT NULL)` 约束值派生自 `EmploymentStatus.TERMINATED` 枚举（兼容历史 `'TERMINATED'`），迁移脚本 `main.py` 同步派生（#13）。

### 4.2 约束与索引

- 唯一约束：`position_numbers.number`、`companies.name`、`positions.name`、`employees.employee_no`、`levels.code`、`work_locations.name`、`scopes.code`、`legal_categories.name`、`users.username`。
- 岗位↔员工一对一：`employees.position_number_id` 设唯一约束（一个岗位至多 1 名在职员工）。
- 删除保护：岗位有在职员工或已有 `position_events` 时禁止删除，仅允许状态关闭。主数据被岗位引用时禁止删除（可停用）。
- 编号规则校验：`number` 与 `scope/country` 一致性在模型层 + 导入层双重校验（Country 级必须 `-4-{编号}`；非 Country 用 `scopes.suffix_code`）。
- **管理岗限定**：设置直线/虚线经理时，目标岗位级别必须 `is_management=True`（M 开头）。
- **乐观锁**：`position_numbers.version`、`employees.version`（`main.py` 兜底迁移；`PATCH` 携带 `version`，`app/helpers.py:assert_version`）。
- 索引：`position_numbers(status)`、`position_numbers(solid_line_manager_id)`、`position_events(position_number_id, changed_at)`、`employees(position_number_id)`、`employment_tax_items(country_id)`。

### 4.3 三环境 DB 隔离（`app/db.py:1`，PRD §7D 合并版）

- 单文件 `.env` 内三段 `DATABASE_URL_{dev,test,prod}` + `APP_ENV` 切换；`DATABASE_URL` 显式值优先（含 `${POSTGRES_PASSKEY}` 展开）。
- 加载后按 `APP_ENV` 选择 `DATABASE_URL_{env}`，否则拼默认 `postgresql://.../hr_db_{env}`。
- 校验库名 == `hr_db_{env}`，不一致拒绝启动；启动打印脱敏库名。
- 旧的 `.env.test`/`.env.prod` 仍兼容（按 `APP_ENV` 追加加载）。
- `assert_writable()` 在 `prod` 拦截 `drop_all`/`--reset`（`scripts/import_csv.py`）；`POST /imports` 与 `POST /data-clean-jobs/{id}/imports` 在 `prod` 直接返回 400（#14）。

## 5. 生命周期状态机（app/lifecycle.py）

```python
ALLOWED = {
  'planned': {'open', 'closed', 'frozen'},
  'open':    {'offered', 'closed'},
  'offered': {'filled', 'open'},
  'filled':  {'vacant'},            # 仅由员工离职/调岗自动触发
  'vacant':  {'open', 'closed', 'frozen'},
  'frozen':  {'planned', 'open'},
  'closed':  set(),                 # 终态
}
```

- 手动流转：`POST /positions/{id}/transition`；`filled↔vacant` 由员工入职/离职/调岗自动触发（共用同一事件记录器）。
- 每次流转写一条 `position_events`；首次创建记 `from_status=null → to_status=初始态`。
- 关闭（→closed）自动写 `closing_date`；解冻/重开清空 `closing_date`。
- 非法流转返回 422（如 `planned → filled`）。
- 挂编条件：岗位须为 `open / vacant / offered`；`filled`（已占用）/`closed`/`planned` 拒绝挂编。
- **乐观锁豁免**：状态流转（`POST .../transitions`）不受 `version` 校验（天然幂等，`lifecycle.transition` 原子）。

## 6. REST API 设计（前缀 /api/v1）

REST 规范：名词复数资源、HTTP 方法映射 CRUD（GET 查 / POST 建 / PATCH 部分更新 / DELETE 删）、创建返回 201 + `Location` 头、部分更新用 `PATCH`。**完全 RESTful：零 RPC，动作资源化**。

### 6.0 认证与限流（`app/auth.py:1` / `app/limiter.py:1`）

| 模块 | 说明 |
| --- | --- |
| JWT | `PyJWT` HS256，`Authorization: Bearer <token>`（兼容 `X-Token` / `?token`），`sub/role/exp`，`JWT_SECRET_KEY`/`JWT_EXPIRE_MINUTES` |
| 用户 | `users` 表，`bcrypt` 哈希，`admin/admin123` 种子（`DEFAULT_ADMIN_*` 可覆盖） |
| 限流 | `slowapi` 全局 `120/min` / IP，登录 `10/min`、首注 `5/min`、公共 `60/min`，超限 `429`（`main.py:1`） |

| 方法 | 路径 | 说明 | 认证 | 限流 |
| --- | --- | --- | --- | --- |
| POST | /auth/login | 登录换取 JWT | 无 | `10/min` |
| POST | /auth/register | 注册新用户（需 admin） | JWT | `10/min` |
| POST | /auth/register-first | 首个用户免认证注册（空库时） | 无 | `5/min` |
| GET | /auth/me | 当前用户信息 | JWT | 全局 |
| GET | /users | 用户列表（仅 admin） | JWT | 全局 |
| GET | /public/companies | 对外：所有隶属公司（id+name） | **JWT** | `60/min` |

### 6.1 主数据（F0，`routers/master_data.py`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | /companies, /countries, /levels, /work-locations, /scopes, /legal-categories, /position-types | 主数据列表 / 新建（201） |
| PATCH/DELETE | /companies/{id}, /countries/{id}, /levels/{id}, /work-locations/{id}, /scopes/{id}, /legal-categories/{id}, /position-types/{id} | 主数据部分更新 / 删除（被引用禁止删） |
| GET/POST | /employment-tax-items | 用工税额列表（?country_id= 过滤）/ 新增（201） |
| PATCH/DELETE | /employment-tax-items/{id} | 用工税额部分更新 / 删除 |

> 经理下拉数据：`GET /positions?role=manager`（见 6.2），不再有独立 manager-options 端点。

### 6.2 业务接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /positions | 岗位列表：filter company_id/scope/status/search/**role=manager**/，分页 |
| POST | /positions | 创建（201，**编号自动生成**，不可手工；描述/成本字段/备注可空） |
| GET | /positions/{id} | 详情：字段 + 占用员工 + 直线/虚线 + 事件时间线 + 成本字段 + `version` |
| PATCH | /positions/{id} | 部分更新（含直线/虚线、成本字段；直线变更做环检测；**需 `version` 乐观锁 409**） |
| POST | /positions/{id}/transitions | 状态流转（201，创建一条事件并变更状态；不受 `version` 约束） |
| GET | /positions/{id}/transitions | 该岗位的流转事件列表 |
| GET | /transitions?positionId= | 全局流转事件列表（可按岗位过滤） |
| GET | /positions/{id}/cost-calculation?salary_before_tax= | 按国家用工税额计算成本（只读派生资源，支持 `?salary_before_tax=` 传入未落库薪资避免双重 PATCH，#15） |
| DELETE | /positions/{id} | 仅无占用员工且无事件时允许 |
| GET | /employees | 员工列表：filter company_id/employee_type/employment_status/search，分页 |
| POST | /employees | 创建（201，必须挂岗 → 岗位自动 Filled） |
| GET | /employees/{id} | 详情（含岗位、直线/虚线经理解析 + `version`） |
| PATCH | /employees/{id} | 部分更新；`employment_status=离职` 触发解绑→岗位 Vacant；**需 `version` 乐观锁 409** |
| POST | /transfers | 调岗（201，旧岗→Vacant，新岗→Filled） |
| GET | /transfers?employeeId= | 调岗记录列表（可按员工过滤） |
| DELETE | /employees/{id} | 删除员工（仅已离职且已解绑） |
| GET | /org-charts | 组织树数据：{nodes, solid_edges, dotted_edges, roots} |
| GET | /org-charts?report={org\|solid\|dotted} | 导出 Markdown（`Accept: text/markdown`） |
| POST | /imports | 上传 Position.csv → 校验/幂等入库，返回报告 |

### 6.3 /org-charts 返回结构（供 SVG 渲染）

```json
{
  "nodes": [{
    "id": 7, "number": "P063-4-5",
    "display": "Soparfi Managing Director - 卢森堡控股总经理",
    "company": "Soparfi S.à r.l.", "level": "M11a",
    "status": "open", "closed": false, "incumbent": "张三"
  }],
  "solid_edges":  [{"from": "P063-4-5", "to": "P004-1"}],
  "dotted_edges": [{"from": "P028-4-6", "to": "P005-2", "label": "虚线汇报"}],
  "roots": ["P063-4-5", "P066-1"]    // 直线经理为空的岗位
}
```

### 6.4 环检测

设置/导入 `solid_line_manager_id` 时，沿上级链上溯，若回到自身即拒绝（A→B→A）。同时校验目标岗位为管理岗（`levels.is_management`）。

### 6.5 导出 MD（app/export_md.py，3 格式）

`GET /org-charts?report={org|solid|dotted}` `Accept: text/markdown` 返回 `text/markdown`（前端一键下载）。

- **`org`（公司 + 岗位，无汇报线）**：按公司分组，岗位列在所属公司下（含显示名、编号、级别、状态）：
  ```markdown
  ## 组织架构（公司 + 岗位）
  ### Peeters Capital Holding SPRL
  - P004-1 Group Chief Executive - 集团首席负责人 (Opening: 1989)【M12b · open】
  - P005-2 Group Global AML Compliance Officer - 集团全球反洗钱合规官 (Opening: 2001)【M10 · open】
  ...
  ### Soparfi S.à r.l.
  - P063-4-5 Soparfi Managing Director - 卢森堡控股总经理 (Opening:2007)【M11a · open】
  ```
- **`solid`（直线汇报线）**：按 `solid_line_manager_id` 生成层级树（缩进表示汇报层级）：
  ```markdown
  ## 直线汇报线
  - Soparfi Managing Director (P063-4-5)
      - Group Chief Executive (P004-1)
          - Group Global AML Compliance Officer (P005-2)
              - Global MLRO Governance Supervisor (P006-2)
          - Global Investment Governance Manager (P007-2)
  ```
- **`dotted`（虚线汇报线）**：按虚线关系输出「岗位 → 虚线经理」：
  ```markdown
  ## 虚线汇报线
  - UK Statutory MLRO (P028-4-6) → Group Global AML Compliance Officer (P005-2)
  - US Statutory MLRO (P054-4-7) → Group Global AML Compliance Officer (P005-2)
  ```

## 7. Org-Chart 数据清洗（app/data_clean.py）

上传 Org-Chart.md，按 Position.csv 模版格式自动解析、清洗、输出标准 CSV，可一键导入。

### 7.1 解析流程
1. **树块检测**：兼容两种格式——` ```tree ` 代码块（Org-Chart2）和 `# 完整组织架构树` 标题后无标记树（Org-Chart.md）
2. **行解析**：从树字符剥离 → 提取 P 编号（`P\d{3,}-(?:[123]|4-\d{1,2})`）→ 两步正则匹配职位名/中文名/法律分类/年份
3. **提取全部三类岗位**（🧑‍💼 In-house + 👨‍👩‍👧 Family Volunteer + 📋 Outsourced），外包岗以 `External Employee` 入库
4. **公司/经理推断**：仅扫描树块，从父节点继承隶属公司和直线经理

### 7.2 清洗规则（8 个关键字段完整读取）
| 字段 | 来源 |
| --- | --- |
| 职位名 | P 编号后的英文名，去除 Global/Regional/Family 前缀（Position.md 规则：不含工作范围） |
| 职位类型 | 树中的 🧑‍💼/👨‍👩‍👧 类型标记，按 Position.md §9 映射（Family Volunteer→Consultant，In-house→Employee，Outsourced→External Employee） |
| 隶属公司 | 树中最近的公司父节点（从 parenthetical 提取公司名） |
| 级别 | 从职位名推断（Managing Director→M11a，CEO→M12b，等） |
| 国家或地区 | 从岗位编号后缀推断（-4-5→Country·卢森堡） |
| 职位开启日 | `(Opening:YYYY)` |
| **工作地点** | **从公司名字的地点信息推断**（不依赖岗位编号，支持未来无编号格式） |
| 法律强制/可选 | P 编号后的 `【...】` 注解提取 |

### 7.3 兼容性
- **Org-Chart.md**（原始版）：5 个岗位全部正确读取
- **Org-Chart2.md**（新版）：68 个岗位全部读取（公司、级别、开启日、法律强制均正确）
- 直线经理：简单深度推断（扁平树完全正确，嵌套树可后续手动调整）

## 8. Position.csv 导入（app/import_csv.py + 路由 + CLI）

- 标准库 `csv` 解析（自动处理引号逗号，如 `"Peeters Shanghai IT Services Co., Ltd."`）。
- 字段映射 17 列（含「职位类型」）→ `position_numbers`；自动建 company / position(职能) / country。
- 直线/虚线经理按正则 `\(P[\d\-]+\)` 解析编号 → 外键；`N/A` → 空；虚线列按 `;`/`、` 分割支持多值。
- 状态映射：`closing_date` 有值 → `closed`；否则 → `open`。已关闭岗写一条关闭事件。
- 年份解析：`1982` → `1982-01-01`。
- 校验：编号唯一且格式合法；**CSV 内岗位编号重复 → 报错（该行不导入，列出明细）**；Country 级必须 `-4-{编号}`；直线/虚线引用必须存在；关闭日不早于开启日。
- 幂等：与库内已有编号按 upsert（已存在则更新字段，保留事件与员工关联）。返回报告 `{imported, updated, errors[]}`（errors 含重复编号明细）。

CLI：`python -m scripts.import_csv data/Position.csv`；Web：上传接口。`prod` 下 `--reset` 被 `app/db.py:assert_writable` 拦截。

## 9. 前端设计

- **单页 + Tab**：数据清洗 / 主数据配置 / 岗位 / 员工 / 组织架构 / 导入；顶部显示库状态（岗位数、员工数）+ 右侧登录徽章（`static/js/auth.js`）。
- **全局 fetch（`api.js`）**：自动携带 `Authorization: Bearer`，统一处理 `401`（弹登录）、`409`（乐观锁冲突）、`429`（限流）。
- **登录态（`auth.js`）**：JWT 存 `localStorage:hr_token`，`GET /auth/me` 校验，`admin/admin123` 默认账号。
- **主数据配置（master_data.js）**：左列表 + 右表单（或 Tab 页签），维护公司/级别/工作地点/工作范围/国家/法律强制/员工用工税额；用工税额按国家分组展示科目与税率。
- **岗位管理（`positions.js`）**：列表（筛选+搜索+分页）→ 新建表单（**编号自动生成、不可填**；工作职责描述/三个成本字段/备注可空）→ 详情抽屉：全部字段 + 占用员工 + 直线/虚线经理（**仅管理岗可选**）+ **成本字段区** + **生命周期时间线** + 流转操作按钮。
  - 成本字段区：模式切换（自动计算 / 手动输入）单选框，**未启用模式字段置灰**；自动模式点「重算」调用 `/positions/{id}/cost-calculation` 并带 `version` 重试。
  - 编辑：`PATCH /positions/{id}` 携带 `version`，`409` 提示“数据已被他人修改，请刷新后重试”。
- **员工管理（`employees.js`）**：列表 → 新建（选择岗位，仅列 Open/Vacant/Offered）→ 详情：档案 + 入职/调岗/离职操作（PATCH 携带 `version`，`409` 同岗位）。
- **数据清洗（data_clean.js）**：上传 Org-Chart.md → 解析报告 → CSV 预览（Position.csv 格式）→ 下载/复制/确认导入。
- **导入页**：文件选择上传 + 校验报告（imported/skipped/errors 明细）。
- **组织架构图（orgchart.js，核心）**：
  - 读取 `/api/org-charts`，按 `solid_line_manager_id` 建多根树。
  - **虚拟根开关（默认开）**：「家族自然人股东」根节点归拢全部根。
  - 布局：子树递归测宽 + 居中（tidy tree）；节点卡片显示 display 名、编号、公司、级别、状态徽标；Filled 显示在职员工。
  - **实线** = 直线（父→子，垂直+水平肘线）；**虚线** = 虚线汇报（跨树虚线曲线，悬浮标「虚线汇报」）。
  - **交互改进**：滚轮缩放（手感调优、以光标为中心）、拖拽平移、适应窗口按钮、公司聚焦（选中公司 → 只看该公司子树，一键返回全局）、悬浮 tooltip、按范围/状态筛选、「含已关闭岗位」开关（默认关）。
  - **导出 MD**：工具栏「导出」按钮 → 下拉选 3 种格式（公司+岗位 / 直线汇报线 / 虚线汇报线），生成 `.md` 文本并下载。

## 10. 实施步骤（顺序）

1. **骨架**：requirements → .venv 安装（验证 Python 3.14 兼容）→ `db.py`/`models.py`/`main.py` + 建表，`uvicorn main:app` 可启动。
2. **主数据模块**：新增字典表（levels/work_locations/scopes/legal_categories/employment_tax_items）+ 初始化脚本 + `routers/master_data.py`（CRUD；`GET /public/companies` 需 JWT `60/min`）。
3. **生命周期模块**：`lifecycle.py` 状态机 + 事件；`schemas.py`（含 `version`）；`routers/positions.py`（CRUD + transition + 编号生成 + 环检测 + 成本字段/cost-calculation + `version` 409）。
4. **员工模块**：`routers/employees.py`（CRUD + transfer + offboard + 岗位联动 + `version` 409）。
5. **认证与限流**：`app/auth.py`（JWT/bcrypt）+ `app/limiter.py`（`120/min`）+ `routers/auth.py`（`POST /auth/login` `10/min`）+ 前端 `api.js`/`auth.js`。
6. **组织数据**：`orgchart.py` 构建 `/api/org-charts`；`export_md.py` 3 格式导出；三环境 `.env` 合并版（`DATABASE_URL_{dev,test,prod}` + `APP_ENV` + `${POSTGRES_PASSKEY}`）。
7. **导入**：`import_csv.py` 适配字典外键 + 路由 + CLI（含 `prod --reset` 拦截）。
8. **前端基础**：`index.html`/`style.css`/`api.js`/`auth.js`/`app.js`/`master_data.js`/`positions.js`（含 `version`/`409`/`429`）/`employees.js`。
9. **组织架构图前端**：`orgchart.js`（SVG 树渲染 + 公司聚焦 + 缩放优化 + 导出按钮）。
10. **导入页 + 联调**：导入真实 Position.csv，全流程走查。
11. **端到端验证**（见 §11）。

## 11. 验证方案（端到端）

1. `.venv/bin/pip install -r requirements.txt`（Python 3.14 兼容验证；含 `PyJWT`/`bcrypt`/`slowapi`）。
2. `uvicorn main:app --reload` → http://127.0.0.1:8000 打开（启动打印 `[startup] APP_ENV=dev DB=...` 脱敏库名）。
3. **三环境**：`APP_ENV=dev/test .venv/bin/python -m scripts.import_csv ... --reset` 成功；`APP_ENV=prod ... --reset` → `FATAL` 1；库名与 `APP_ENV` 不一致拒绝启动；单文件 `.env` 三段切换。
4. **认证**：`POST /auth/login` → JWT → `GET /public/companies` 需 `Authorization: Bearer`（无 token `401`，带 token `200`）；`GET /auth/me` 校验；`bcrypt` 哈希。
5. **限流**：`POST /auth/login` 11 次突发 → 第 11 次 `429`；全局 `120/min`；前端 `429` toast。
6. **乐观锁**：`GET /positions/{id}` 取 `version` → `PATCH` 带 `version` 成功自增；旧 `version` → `409 {"detail":"已被他人修改"}`（岗位/员工同理）。
7. **导入**：`python -m scripts.import_csv testingdata/原始文件/Position.csv` → 期望 5 行、无 error；`position_numbers`=5。
8. **API 冒烟（curl）**：
   - `GET /api/v1/positions?status=open` 数量正确。
   - `GET /api/v1/org-charts`：nodes=5、solid_edges、dotted_edges、roots。
   - `POST /positions/{id}/transitions` 非法流转（planned→filled）→ 422。
   - 直线设置成环（A→B→A）→ 422；设置非管理岗为经理 → 422。
   - `GET /api/v1/levels` 返回 19 项；`GET /positions?role=manager` 仅含 M 开头岗位。
   - `POST /api/v1/positions/{id}/cost-calculation` 按国家税务科目正确计算。
   - 导入含重复编号的 CSV → 报告 errors 列出重复明细，重复行不导入。
   - `POST /api/v1/positions` 不传 `number` 正常生成；`POST /api/v1/employees` 省略 birth_date/phone/email/remark 正常保存。
9. **UI 走查**：登录 → 数据清洗 → 导入 → 主数据配置 → 岗位详情（`version`/`409`） → 成本字段 → 员工管理（`version`） → 组织图 → 导出 MD。
10. **组织图**：实线/虚线正确渲染；虚拟根开关生效；公司聚焦视图正确；缩放/平移可用；导出 MD 3 种格式内容正确；关闭岗置灰可隐藏。
11. **数据清洗验证**：原始 Org-Chart.md（5 岗）+ Org-Chart2.md（68 岗）均可正确解析；8 个关键字段全部读取。
12. 数据备份：PostgreSQL `pg_dump` 定期备份；无需历史迁移，全新建库。

## 12. 风险与注意点

- **Python 3.14 包兼容**：若 pydantic/sqlalchemy 最新版无 3.14 wheel，按报错升降级（第 1 步先行验证）。
- **编号规则出入**（同职位多 P 序号）：以现有数据为准，不强制归一（PRD §3.1）。
- **Org-Chart.md 不一致项**：以 CSV 为准，系统内不提示冲突（PRD §3.8）。
- **虚线解析**：虚线列多为单值，按 `;`/`、` 分割支持多值（模型为 0~N）。
- **组织图性能**：91 节点量级自定义 SVG 足够；节点折叠控制渲染量。
- **PostgreSQL 部署**：需确保 PostgreSQL 服务可用；数据库**全部新建**，无需从 SQLite 迁移；连接信息通过单文件 `.env` 的 `DATABASE_URL_{dev,test,prod}` + `APP_ENV` + `${POSTGRES_PASSKEY}` 配置（`app/db.py:1`）。
- **成本计算精度/口径**：税率按百分比存 Numeric；自动计算保留两位小数；币种/年度月度口径待确认（PRD §10）。
- **管理岗过滤**：直线/虚线经理下拉用 `levels.is_management` 过滤；导入历史数据若含 B 级经理引用需兼容（跳过或告警）。
- **JWT 密钥**：`JWT_SECRET_KEY` 生产必须覆盖为强随机串（≥32 字符），开发默认值仅 32+ 字符占位。
- **限流存储**：`slowapi` 默认内存存储，多实例部署需改 Redis 后端。

