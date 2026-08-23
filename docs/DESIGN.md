# 轻量级 HR 管理系统 — 技术设计文档（DESIGN）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v2.3 |
| 更新日期 | 2026-08-23 |
| 关联 | [PRD.md](./PRD.md)（v2.3，需求与决策） |
| 目标版本 | V2.3 |

---

## 1. 概述

基于集团既有职位规则与数据（`Position.md` / `Position.csv` 模版 / `Org-Chart3.md`），构建轻量级 HR 管理系统，覆盖主数据配置、岗位全生命周期（含人工成本字段）、人员信息、组织架构图（汇报线树 + 导出 MD）、JWT 认证、乐观锁、速率限制、三环境 DB 隔离能力。本文档定义技术实现：技术选型、数据库设计、生命周期状态机、REST API、CSV 导入、前端与组织架构图渲染、实施步骤与验证方案。

关键既定决策（详见 PRD §3.1/§3.7/§10）：
- **编号系统重制（v2.3）**：源编号一律忽视，系统强制分配——正式岗 `P{seq}`、外包岗 `PA{seq}`；导入幂等键 = 职位名+隶属公司+国家或地区+开启日；清洗期编号为临时 T 占位（不占正式池）；**两段式识别**（首次播种不校验内容/迭代全量带 ID 认老、幂等键认新）。
- **数据权威**：清洗后的 CSV 为准；数据清洗**仅支持 Org-Chart3 格式**（无编号树 + 权责说明续行），旧版 Org-Chart/Org-Chart2 不再解析。
- **组织图主视图**：汇报线树（节点=岗位，按直线经理成树，虚线另行标注）。
- **外包岗位**：V2.1 起纳入，外包岗以 `External Employee` 导入系统（与内部全职同流程）。
- **员工必须挂岗**：不允许「待分配」员工。
- **虚拟根节点**：「家族自然人股东」默认开启、可开关。
- **年份精度**：开启/关闭日存 date，年份→`YYYY-01-01`。
- **主数据字典化**：公司/级别/工作地点/工作范围/国家/法律强制/员工用工税额均为可维护字典（F0）。
- **经理下拉限定管理岗**：直线/虚线经理下拉仅显示级别 M 开头的岗位。
- **岗位成本字段**：自动（按税区科目）/ 手动两种模式互斥；税率挂载点可配置（国家级或城市级），城市级分拆后**无国家兜底**，未配置地区成本无法自动计算。
- **挂编联动**：岗位 `position_type` ↔ 员工 `employee_type` 按映射强制联动，数据层兜底（Consultant→正式 / External Employee→外包 / Employee→正式·实习·劳务）。
- **组织图导出 MD**：公司+岗位 / 直线汇报线 / 虚线汇报线 3 种格式。
- **三环境 DB 隔离（PRD §7D 合并版）**：单文件 `.env` 内 `DATABASE_URL_{dev,test,prod}` + `APP_ENV` 切换，含 `${POSTGRES_PASSKEY}` 展开。
- **权限（v2.3）**：`users` 加角色 admin/hr；`user_companies` 多对多；关闭自主注册、仅 admin 建号+分配可管实体；**读可跨司、写按实体隔离**（岗位全局读、员工/成本按实体写隔离、组织图可读他司员工姓名）；汇报接线由目标岗位操作者维护、源岗位只读不限管。
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
│   ├── data_clean.py       # Org-Chart3.md 解析 + 清洗（仅 Org-Chart3 格式）
│   ├── export_md.py        # 组织图导出 MD（3 格式）
│   ├── seed.py             # 主数据初始化 + 管理员种子 admin/admin123
│   ├── import_csv.py       # Position.csv 解析/校验/入库
│   └── routers/
│       ├── __init__.py
│       ├── auth.py         # 认证：POST /auth/login, GET /auth/me
│       ├── users.py        # 用户管理（仅 admin 建号+分配可管公司，v2.3）
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
├── testingdata/            # 源数据 Org-Chart3.md / Position.md / Position.csv（模版）
└── docs/                   # PRD.md / DESIGN.md / API.md / API_PUBLIC.md / UI_MOCKUP.html
```

## 4. 数据库设计

### 4.1 表结构

```python
# app/models.py —— 关键模型（v2.2：新增 users + version 乐观锁）

# ---- 系统用户（JWT，PRD §7B，v2.3 加角色/可管实体）----
class User(Base):
    __tablename__ = 'users'
    id, username(unique), hashed_password(bcrypt), role Enum(admin|hr), is_active, created_at
    # 种子：admin/admin123（DEFAULT_ADMIN_USER/PASSWORD 可覆盖，app/seed.py:1）
    # 仅 admin 建号；hr 经 user_companies 绑定可管实体（行级隔离，PRD §7B.3）

class UserCompany(Base):             # v2.3：hr ↔ 可管法人实体 多对多
    __tablename__ = 'user_companies'
    id, user_id FK, company_id FK
    Unique(user_id, company_id)
    # admin 自带全司（无需记录）；hr 无记录则无任何可管实体

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

class WorkLocation(Base):          # 工作地点（v2.3：国家+城市 两级）
    __tablename__ = 'work_locations'
    id, name(unique), country, city, sort_order   # 如 比利时·布鲁塞尔

class Scope(Base):                 # 工作范围（Family/Global/Regional/Country）
    __tablename__ = 'scopes'
    id, code(unique), label, suffix_code(1..4), sort_order   # suffix_code 仅作字典编码参考（编号已与范围解耦）

class LegalCategory(Base):         # 法律强制/可选（按 §3.5 初始化 4 类）
    __tablename__ = 'legal_categories'
    id, name(unique), sort_order

class PositionType(Base):          # 职位类型（PRD §3.7 三类）
    __tablename__ = 'position_types'
    id, name(unique), sort_order  # Consultant / Employee / External Employee

class TaxZone(Base):               # 税区挂载点配置（v2.3，F1.6）
    __tablename__ = 'tax_zones'
    id, level Enum(country|city), country_id FK, city String nullable, sort_order
    # 城市级分拆后无国家兜底；未配置税率的地区成本无法自动计算

class EmploymentTaxItem(Base):     # 员工用工税额（v2.3：按税区）
    __tablename__ = 'employment_tax_items'
    id, tax_zone_id FK, item_name(科目), tax_rate Numeric(税率%), is_active

# ---- 业务表 ----
class Position(Base):              # 职位（职能，不含工作范围）
    __tablename__ = 'positions'
    id, name(unique)

class PositionNumber(Base):        # 岗位编号（管理主体）
    __tablename__ = 'position_numbers'
    id
    number        unique          # 系统分配：正式岗 P{seq} / 外包岗 PA{seq}，纯序号无后缀
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
    # ---- 预算成本字段（v2.3：预算口径，留在岗位、不随人走）----
    cost_mode     Enum(auto|manual), default manual   # 两种模式互斥（预算口径）
    salary_before_tax  Numeric(14,2), nullable   # 预算·税前薪资
    company_share      Numeric(14,2), nullable   # 预算·公司份额
    labor_cost         Numeric(14,2), nullable   # 预算·用工成本
    # 空岗可录预算；Filled 后与占用员工实际成本并置对照（见 Employee 实际成本）
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
    employee_type, employment_status（含「转调中」v2.3）
    position_number_id  FK NOT NULL       # 人永不脱岗（在职/转调/升职均挂岗），仅离职解绑 NULL
    target_company_id  FK companies, nullable   # v2.3 转调中：目标公司（认领前原岗保持）
    # ---- 实际成本字段（v2.3：跟人走，升职转调不丢、离职留档）----
    actual_cost_mode   Enum(auto|manual), default manual
    actual_salary_before_tax  Numeric(14,2), nullable   # 实际·税前薪资
    actual_company_share      Numeric(14,2), nullable   # 实际·公司份额
    actual_labor_cost         Numeric(14,2), nullable   # 实际·用工成本
    remark, version Integer default 1,    # 乐观锁（PRD §7C）
    created_at, updated_at
    # 挂编联动（v2.3）：employee_type 必须匹配所挂岗位的 position_type
    #   Consultant→正式 / External Employee→外包 / Employee→正式·实习·劳务
    #   DB 层约束兜底（应用层校验 + 可选 CHECK），防「外包挂 Employee 编制」四不像
```

> 实现说明：`level / work_location / legal_category` 在 `position_numbers` 上保留**字符串**（与 CSV 一致），由字典表提供下拉选项并在创建/更新时校验；`scope` 保留枚举，由 `scopes` 字典驱动下拉展示与编号。成本字段为新增列，导入时置空（人工模式默认）。`version` 存量库由 `main.py:1` 的 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 轻量迁移补齐。`company_id` / `prev_company_id` 双外键需在 `relationship` 上显式 `foreign_keys`（`prev_company = relationship(Company, foreign_keys=[prev_company_id])`）避免 `AmbiguousForeignKeys`（#12）。

> 创建校验：`PositionNumberCreate` **不接收 `number`**（新建只能自动生成，编辑不改编号）；`EmployeeCreate` 中 `birth_date / phone / email / remark` 可空。`PATCH` 需携带 `version`，`app/helpers.py:1` 的 `assert_version` 负责 409。`LegalCategory` 枚举为历史兼容，运行时以 `legal_categories` 字典表为准（#16）。

> `employees` 的 `CHECK (employment_status IN (...) OR position_number_id IS NOT NULL)` 约束值派生自 `EmploymentStatus.TERMINATED` 枚举（兼容历史 `'TERMINATED'`），迁移脚本 `main.py` 同步派生（#13）。

### 4.2 约束与索引

- 唯一约束：`position_numbers.number`、`companies.name`、`positions.name`、`employees.employee_no`、`levels.code`、`work_locations.name`、`scopes.code`、`legal_categories.name`、`users.username`。
- 岗位↔员工一对一：`employees.position_number_id` 设唯一约束（一个岗位至多 1 名在职员工）。
- 删除保护：岗位有在职员工或已有 `position_events` 时禁止删除，仅允许状态关闭。主数据被岗位引用时禁止删除（可停用）。
- 编号规则校验（v2.3）：编号由系统分配，格式仅校验 `P{seq}` / `PA{seq}` 纯序号；与 scope/country **解耦**（范围/国家存独立字段）。
- **管理岗限定**：设置直线/虚线经理时，目标岗位级别必须 `is_management=True`（M 开头）。
- **乐观锁**：`position_numbers.version`、`employees.version`（`main.py` 兜底迁移；`PATCH` 携带 `version`，`app/helpers.py:assert_version`）。
- **挂编联动**：员工 `employee_type` 必须匹配所挂岗位的 `position_type`（Consultant→正式 / External Employee→外包 / Employee→正式·实习·劳务），应用层校验 + 数据层约束兜底（防四不像）。
- 索引：`position_numbers(status)`、`position_numbers(solid_line_manager_id)`、`position_events(position_number_id, changed_at)`、`employees(position_number_id)`、`employment_tax_items(tax_zone_id)`。

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

- 手动流转：`POST /positions/{id}/transitions`；`filled↔vacant` 由员工入职/离职/调岗自动触发（共用同一事件记录器）。
- **事务原子（v2.3）**：转调认领 / 升职 / 离职解绑等**一次动作同时改变多个状态（如目标岗 filled + 原岗 vacant + 人挂新岗）必须包进**下单事务**，要么全部生效、要么整体回滚**，不产生「目标岗已 filled、原岗仍卡着」的"一人双岗"脏窗口；乐观锁分别标各自记录挡并发，事务保证成对。
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
| 用户 | `users` 表，`bcrypt` 哈希，`admin/admin123` 种子（`DEFAULT_ADMIN_*` 可覆盖）；**仅 admin 建号、关闭自主注册**（v2.3） |
| 可管实体 | `user_companies` 多对多绑定 hr → 法人实体；admin 自带全司；实现行级隔离（v2.3，`app/routers/security.py` 或 `app/routers/users.py`） |
| 限流 | `slowapi` 全局 `120/min` / IP，登录 `10/min`、建号 `5/min`、公共 `60/min`，超限 `429`（`main.py:1`） |

| 方法 | 路径 | 说明 | 认证 | 限流 |
| --- | --- | --- | --- | --- |
| POST | /auth/login | 登录换取 JWT | 无 | `10/min` |
| POST | /auth/register | **建号（仅 admin；关闭自主注册，v2.3）** | JWT | `10/min` |
| GET | /auth/me | 当前用户信息 | JWT | 全局 |
| GET/POST | /admin/users | 用户列表 / 建号+分配可管公司（仅 admin） | JWT(admin) | 全局 |
| POST | /admin/users/{id}/companies | 给 hr 分配/撤销可管实体（仅 admin） | JWT(admin) | 全局 |
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

> **行级隔离（v2.3）**：`hr` 的岗位/员工访问按其 `user_companies` 过滤（列表戳 unavailable 实体，详情/写操作未授权返回 403）。**汇报接线（直线/虚线）由目标岗位所在公司的可管 HR 操作**：改 `solid_line_manager_id` / 虚线时，校验**被汇报目标岗位的 company_id** 在操作者可管集内；源岗位仅需可读、不限管。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /positions | 岗位列表：filter company_id/scope/status/search/**role=manager**/，分页 |
| POST | /positions | 创建（201，**编号自动生成**，不可手工；描述/成本字段/备注可空） |
| GET | /positions/{id} | 详情：字段 + 占用员工 + 直线/虚线 + 事件时间线 + 成本字段 + `version` |
| PATCH | /positions/{id} | 部分更新（含直线/虚线、成本字段；直线变更做环检测；**需 `version` 乐观锁 409**） |
| POST | /positions/{id}/transitions | 状态流转（201，创建一条事件并变更状态；不受 `version` 约束） |
| GET | /positions/{id}/transitions | 该岗位的流转事件列表 |
| GET | /transitions?positionId= | 全局流转事件列表（可按岗位过滤） |
| GET | /positions/{id}/cost-calculation?salary_before_tax=&scope=budget\|actual | 成本测算（v2.3 双口径）：`scope=budget` 按**岗位税区**（空岗可用）计算预算；`scope=actual` 按**当前占用员工的归属税区**计算实际；未配置税率返回「未配置」，不猜测 |
| DELETE | /positions/{id} | 仅无占用员工且无事件时允许 |
| GET | /employees | 员工列表：filter company_id/employee_type/employment_status/search，分页（写按实体隔离，读组织图可跨司见名） |
| POST | /employees | 创建（201，必须挂岗 → 岗位自动 Filled） |
| GET | /employees/{id} | 详情（含岗位、直线/虚线经理解析 + `version`） |
| PATCH | /employees/{id} | 部分更新；`employment_status=离职` 触发解绑→岗位 Vacant；**需 `version` 乐观锁 409** |
| POST | /transfers | 调岗（201，旧岗→Vacant，新岗→Filled） |
| GET | /transfers?employeeId= | 调岗记录列表（可按员工过滤） |
| POST | /transfers/initiate | 转调发起（v2.3）：原 HR 把人转到目标公司 B（人仍挂原岗、原岗锁定，标「转调中」+ target_company_id=B）不释放；**此后仅 `B` 的 HR 可见/可认领**（池按 target_company 过滤，其他 HR 不可见） |
| POST | /transfers/{id}/claim | 转调认领（v2.3）：**仅 target_company 可管 HR**认领+分配空闲目标岗；**单事务**：目标岗 Filled + 原岗 Vacant + 人挂新岗 + 记 `prev_*`，全部生效或整体回滚 |
| POST | /transfers/{id}/reject | 转调退回（v2.3）：仅 target_company HR 拒绝 → 退回原公司、原岗继续（人不脱岗） |
| POST | /employees/{id}/promote | 升职（v2.3）：时节=月末/即时；Filled 新岗、老岗默认 Vacant（可手动 Closed）、工龄照人 |
| DELETE | /employees/{id} | 删除员工（仅已离职且已解绑） |
| GET | /org-charts | 组织树数据：{nodes, solid_edges, dotted_edges, roots} |
| GET | /org-charts?report={org\|solid\|dotted} | 导出 Markdown（`Accept: text/markdown`） |
| POST | /imports | 上传 Position.csv → 校验/幂等入库，返回报告 |

### 6.3 /org-charts 返回结构（供 SVG 渲染）

```json
{
  "nodes": [{
    "id": 7, "number": "P1",
    "display": "Statutory Manager - 卢森堡法定经理",
    "company": "Peeters Luxembourg S.à r.l.", "level": "M9a",
    "status": "open", "closed": false, "incumbent": null
  }],
  "solid_edges":  [{"from": "P1", "to": "P4"}],
  "dotted_edges": [{"from": "P3", "to": "P2", "label": "虚线汇报"}],
  "roots": ["P1", "P2"]    // 直线经理为空的岗位
}
```

### 6.4 环检测

设置/导入 `solid_line_manager_id` 时，沿上级链上溯，若回到自身即拒绝（A→B→A）。同时校验目标岗位为管理岗（`levels.is_management`）。

### 6.5 导出 MD（app/export_md.py，3 格式）

`GET /org-charts?report={org|solid|dotted}` `Accept: text/markdown` 返回 `text/markdown`（前端一键下载）。

- **`org`（公司 + 岗位，无汇报线）**：按公司分组，岗位列在所属公司下（含显示名、编号、级别、状态）：
  ```markdown
  ## 组织架构（公司 + 岗位）
  ### Family Asset Management SPRL
  - P1 Statutory Manager - 比利时法定管理人 (Opening: 1982)【M9a · open】
  - P2 Executive Assistant - 执行助理 (Opening: 1982)【B8a · open】
  ...
  ### Peeters Luxembourg S.à r.l.
  - P4 Statutory Manager - 卢森堡法定经理 (Opening:1982)【M9a · open】
  ```
- **`solid`（直线汇报线）**：按 `solid_line_manager_id` 生成层级树（缩进表示汇报层级）：
  ```markdown
  ## 直线汇报线
  - Statutory Manager (P1)
      - Executive Assistant (P2)
          - Group Financial Coordinator (P3)
  ```
- **`dotted`（虚线汇报线）**：按虚线关系输出「岗位 → 虚线经理」：
  ```markdown
  ## 虚线汇报线
  - UK Statutory MLRO (P6) → Group Global AML Compliance Officer (P2)
  - US Statutory MLRO (P7) → Group Global AML Compliance Officer (P2)
  ```

## 7. Org-Chart 数据清洗（app/data_clean.py，仅 Org-Chart3 格式）

上传 Org-Chart3.md，按 Position.csv 模版格式自动解析、清洗、输出标准 CSV，可一键导入。**仅支持 Org-Chart3 格式**（v2.3），`Org-Chart.md` / `Org-Chart2.md` 不再解析。

### 7.1 解析流程（Org-Chart3 格式规范）
1. **树块检测**：`# 完整组织架构树…` 标题开始，至下一个 `#` 标题结束（天然排除「开户行信息」「岗位变更信息」等章节）
2. **行解析**：根节点行忽略 → 公司节点 `名（地点 | 年份｜…）` → 🧑‍💼/👨‍👩‍👧/📋 类型标记行 → 岗位行 `英文名 - 中文名 【法律分类】(Opening: YYYY)`（无编号；带编号也剥离忽视）
3. **权责说明续行**：岗位下方 `权责说明：…` → 工作职责描述
4. **提取全部三类岗位**（🧑‍💼 In-house + 👨‍👩‍👧 Family Volunteer + 📋 Outsourced），外包岗以 `External Employee` 入库

### 7.2 清洗规则（8 个关键字段完整读取）
| 字段 | 来源 |
| --- | --- |
| 职位名 | 岗位行英文名，去除 Global/Regional/Family 前缀（Position.md 规则：不含工作范围） |
| 职位类型 | 树中的 🧑‍💼/👨‍👩‍👧 类型标记，按 Position.md §9 映射（Family Volunteer→Consultant，In-house→Employee，Outsourced→External Employee） |
| 隶属公司 | 树中最近的公司父节点（从 parenthetical 提取公司名） |
| 级别 | 从职位名推断（Statutory Manager→M9a，Coordinator→B6，Managing Director→M11a 等） |
| 国家或地区 | 从**公司地点**推断（卢森堡→Country·卢森堡等，不依赖编号） |
| 职位开启日 | `(Opening:YYYY)` |
| **工作地点** | **从公司名字的地点信息推断**（不依赖岗位编号） |
| 法律强制/可选 | 岗位行的 `【...】` 注解提取 |

### 7.3 编号与直线经理
- **岗位编号列输出临时占位 T 序号**（供 review 识别岗位/经理，不占正式编号池、不跨轮累积），正式 `P/PA` 编号由系统在导入时分配（见 §8）；源文件编号一律忽视。
  - 迭代导入时：CSV 由上次导出生成、**携带正式岗位 ID**（识别老岗，见 §8 两段式）。
- **直线经理 = 真实树祖先**：岗位的经理取树中最近的上层**岗位**节点（跳过公司/类型分组节点）；同层兄弟不互挂；公司节点清空祖先栈（跨公司不串线）；无显式层级 → N/A，用户后续 UI 手动设置。CSV 经理列按**职位名**输出。
  - **局限（v2.3）**：Org-Chart3 树无法表达**跨公司/跨域虚线汇报**；缩进层级推断的直线关系可能含错。→ 直线/虚拟关系导入后由用户 UI 手动补充校验（清洗只给一个可改的初稿）。

## 8. Position.csv 导入（app/import_csv.py + 路由 + CLI，两段式，v2.3）

- 标准库 `csv` 解析（自动处理引号逗号，如 `"Peeters Shanghai IT Services Co., Ltd."`）。
- 字段映射 17 列（含「职位类型」）→ `position_numbers`；自动建 company / position(职能) / country。
- **岗位编号列一律忽视**，系统分配：正式岗 `P{seq}`、外包岗 `PA{seq}`，序号=库内同系列最大值+1（`app/helpers.py:generate_number`）。
- 直线/虚线经理按**职位名**解析 → 外键；同名多岗取首个并告警；`N/A` → 空；虚线列按 `;`/`、` 分割支持多值。
- 状态映射：`closing_date` 有值 → `closed`；否则 → `open`。已关闭岗写一条关闭事件。
- 年份解析：`1982` → `1982-01-01`。
- **两段式识别（v2.3）**：
  - **首次（播种）**：不校验内容（无老数据可比），仅 DB 硬唯一兜底，发首批编号。
  - **迭代（全量）**：强制复用上次导出格式，导出带正式 ID；**带 ID 认老更新、无 ID 幂等键新建**；同名双编制（含同开启日）→ 报错由用户区分。
- 校验：隶属公司/职位必填；国家或地区可识别；**文件内幂等键（职位+公司+国家+开启日）重复 → 报错（该行不导入）**。
- 幂等：首次按「职位名+隶属公司+国家+开启日」判定重复；迭代按**正式 ID** 判定老岗。更新字段时保留编号/事件/员工关联。返回报告 `{imported, updated, errors[], warnings[], assigned_numbers[], …}`。
- **临时占位**：清洗/导出阶段编号为 T 序号（仅 review 识别用，不占正式池、不回归）；落库后经理引用重写为正式编号。

CLI：`python -m scripts.import_csv data/Position.csv`（首次 `--reset` 语义不清空存量岗位，仅播种/迭代 upsert）；Web：上传接口。`prod` 下破坏性操作被 `app/db.py:assert_writable` 拦截。

## 9. 前端设计

- **单页 + Tab**：数据清洗 / 主数据配置 / 岗位 / 员工 / 组织架构 / 导入；顶部显示库状态（岗位数、员工数）+ 右侧登录徽章（`static/js/auth.js`）。
- **全局 fetch（`api.js`）**：自动携带 `Authorization: Bearer`，统一处理 `401`（弹登录）、`409`（乐观锁冲突）、`429`（限流）。
- **登录态（`auth.js`）**：JWT 存 `localStorage:hr_token`，`GET /auth/me` 校验，`admin/admin123` 默认账号。
- **主数据配置（master_data.js）**：左列表 + 右表单（或 Tab 页签），维护公司/级别/工作地点/工作范围/国家/法律强制/员工用工税额；用工税额按国家分组展示科目与税率。
- **岗位管理（`positions.js`）**：列表（筛选+搜索+分页）→ 新建表单（**编号自动生成、不可填**；工作职责描述/三个成本字段/备注可空）→ 详情抽屉：全部字段 + 占用员工 + 直线/虚线经理（**仅管理岗可选**）+ **成本字段区** + **生命周期时间线** + 流转操作按钮。
  - 成本字段区：模式切换（自动计算 / 手动输入）单选框，**未启用模式字段置灰**；自动模式点「重算」调用 `/positions/{id}/cost-calculation` 并带 `version` 重试。
  - 编辑：`PATCH /positions/{id}` 携带 `version`，`409` 提示“数据已被他人修改，请刷新后重试”。
- **员工管理（`employees.js`）**：列表 → 新建（选择岗位，仅列 Open/Vacant/Offered）→ 详情：档案 + 入职/调岗/离职操作（PATCH 携带 `version`，`409` 同岗位）。
- **数据清洗（data_clean.js）**：上传 Org-Chart3.md（或选择服务器原始文件）→ 解析报告 → CSV 预览（Position.csv 格式，编号列显示「导入时分配」）→ 下载/复制/确认导入。
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
2. `uvicorn main:app --reload` → http://127.0.0.1:7273 打开（启动打印 `[startup] APP_ENV=dev DB=...` 脱敏库名）。
3. **三环境**：`APP_ENV=dev/test .venv/bin/python -m scripts.import_csv ... --reset` 成功；`APP_ENV=prod ... --reset` → `FATAL` 1；库名与 `APP_ENV` 不一致拒绝启动；单文件 `.env` 三段切换。
4. **认证**：`POST /auth/login` → JWT → `GET /public/companies` 需 `Authorization: Bearer`（无 token `401`，带 token `200`）；`GET /auth/me` 校验；`bcrypt` 哈希。
5. **限流**：`POST /auth/login` 11 次突发 → 第 11 次 `429`；全局 `120/min`；前端 `429` toast。
6. **乐观锁**：`GET /positions/{id}` 取 `version` → `PATCH` 带 `version` 成功自增；旧 `version` → `409 {"detail":"已被他人修改"}`（岗位/员工同理）。
7. **导入**：清洗作业导入 Org-Chart3.md → 期望 `imported=4`、`assigned_numbers=[P1..P4]`、无 error；重复导入 `updated=4` 不产生重复数据、编号不变。
8. **API 冒烟（curl）**：
   - `GET /api/v1/positions?status=open` 数量正确。
   - `GET /api/v1/org-charts`：nodes=4、roots 并行、solid_edges/dotted_edges 与汇报线一致。
   - `POST /positions/{id}/transitions` 非法流转（planned→filled）→ 422。
   - 直线设置成环（A→B→A）→ 422；设置非管理岗为经理 → 422。
   - `GET /api/v1/levels` 返回 19 项；`GET /positions?role=manager` 仅含 M 开头岗位。
   - `GET /api/v1/positions/{id}/cost-calculation` 按税区（国家或城市）税务科目正确计算；未配置税率返回「未配置」。
   - CSV 内幂等键（职位+公司+国家+开启日）重复 → 报告 errors 列出明细，重复行不导入；迭代导入带 ID 行按正式 ID 认老。
   - `POST /api/v1/positions` 不传 `number` 正常生成（正式 P{seq}/外包 PA{seq}）；`POST /api/v1/employees` 省略 birth_date/phone/email/remark 正常保存。
9. **UI 走查**：登录 → 数据清洗 → 导入 → 主数据配置 → 岗位详情（`version`/`409`） → 成本字段 → 员工管理（`version`） → 组织图 → 导出 MD。
10. **组织图**：实线/虚线正确渲染；虚拟根开关生效；公司聚焦视图正确；缩放/平移可用；导出 MD 3 种格式内容正确；关闭岗置灰可隐藏。
11. **数据清洗验证**：上传 Org-Chart3.md（或 `?source_file=`）→ 4 岗位全部解析、8 个关键字段正确读取、权责说明续行写入职责描述；旧格式（Org-Chart.md/Org-Chart2.md）不再解析。
12. 数据备份：PostgreSQL `pg_dump` 定期备份；无需历史迁移，全新建库。

## 12. 风险与注意点

- **Python 3.14 包兼容**：若 pydantic/sqlalchemy 最新版无 3.14 wheel，按报错升降级（第 1 步先行验证）。
- **编号系统重制（v2.3）**：源编号一律忽视，系统按 P/PA 纯序号全新分配；同职位多 P 序号的历史问题已消解（PRD §3.1）。
- **Org-Chart.md 不一致项**：以 CSV 为准，系统内不提示冲突（PRD §3.8）。
- **虚线解析**：虚线列多为单值，按 `;`/`、` 分割支持多值（模型为 0~N）。
- **组织图性能**：91 节点量级自定义 SVG 足够；节点折叠控制渲染量。
- **PostgreSQL 部署**：需确保 PostgreSQL 服务可用；数据库**全部新建**，无需从 SQLite 迁移；连接信息通过单文件 `.env` 的 `DATABASE_URL_{dev,test,prod}` + `APP_ENV` + `${POSTGRES_PASSKEY}` 配置（`app/db.py:1`）。
- **成本计算精度/口径**：税率按百分比存 Numeric；自动计算保留两位小数；币种/年度月度口径待确认（PRD §10）。
- **税区无兜底**：城市级分拆后不作国家兜底，未配置税率的地区成本返回「未配置」——需前端明确提示引导用户配税率，避免「空成本」被误读为 0。
- **管理岗过滤**：直线/虚线经理下拉用 `levels.is_management` 过滤；导入历史数据若含 B 级经理引用需兼容（跳过或告警）。
- **JWT 密钥**：`JWT_SECRET_KEY` 生产必须覆盖为强随机串（≥32 字符），开发默认值仅 32+ 字符占位。
- **限流存储**：`slowapi` 默认内存存储，多实例部署需改 Redis 后端。

---

## 13. Phase v2.3 实施清单（开发执行顺序）

> 依据本轮 grill 定案（参见 PRD §2/§3.1/§3.7/§4 F1.5b·F1.6·F7B/§5/§10）。**2026-08-23 开发完成**（S6 两处按后续决策调整，见注）。任一阶段改动跨 `models/schemas/routers/前端` 时先更新 `schemas.py` 与 `main.py` 的兜底迁移，保持 `create_all` 幂等。

### S0 前置与数据重置（无代码）
- [x] 确认三库岗位域数据已 TRUNCATE（prod 备份 `data/backups/hr_db_prod_*.dump` 在）；主数据字典/用户保留。
- [x] `.env` 三环境 `DATABASE_URL_{dev,test,prod}` + `APP_ENV` 校验一致；`prod` 禁止 `--reset`/`drop_all`。

### S1 数据模型与兜底迁移（`app/models.py` + `main.py`）
- [x] `User.role` → `Enum(admin|hr)`（保留默认 `admin`），`is_active` 语义保留。
- [x] 新增 `UserCompany`（`users ↔ companies` 多对多，`Unique(user_id, company_id)`）。
- [x] `Employee` 增加：`employment_status` 纳入「转调中」、`target_company_id`（FK companies, nullable）、实际成本字段 `actual_cost_mode / actual_salary_before_tax / actual_company_share / actual_labor_cost`。
- [x] `PositionNumber` 成本字段 `cost_mode/salary_before_tax/company_share/labor_cost` 语义标注为**预算口径**（字段不变）。
- [x] `WorkLocation` 加 `country/city` 两级；新增 `TaxZone`（`level=country|city`、`country_id`、`city`）；`EmploymentTaxItem.tax_zone_id`（替换 `country_id`）。
- [x] 新增 `transfers` 表（独立表，含 kind=transfer|promotion、status、target_company_id 等）。
- [x] 数据层硬约束：①挂编联动 Check（`position_type`↔`employee_type`）→ **应用层实现**（PG CHECK 无法跨表，见 `employees._assert_type_match`）；②在职含转调中必挂岗 Check（沿用既有 `ck_employees_position_required_if_active`）；③乐观锁 `version` 兜底 `ALTER`（既有）。
- [x] 清理 `Employee` 旧 CHECK 与转调/升职语义合并（旧 CHECK 语义已覆盖转调中必挂岗，无需重建）。

### S2 认证与账号管理（`app/auth.py` + `routers/auth.py` + 新增 `routers/users.py`）
- [x] 移除 `POST /auth/register-first`；`POST /auth/register` 仅 `admin` 可调用（建号；可管公司经 `/admin/users` 分配）。
- [x] 新增 `POST /admin/users`（建号+`user_companies`）、`POST /admin/users/{id}/companies`（分配/撤销可管实体）、`GET /admin/users`（仅 admin）；另加 `PATCH /admin/users/{id}/active` 启停用。
- [x] `create_access_token` 已携 `role`；解码支持 `role=admin|hr`（UserRole 为 str 枚举，字符串比较兼容）。
- [x] 行级隔离 helper：`get_operable_company_ids(db, user) -> set[int] | ALL_COMPANIES`（admin 全司、hr 按 `user_companies`）+ `assert_can_write_company`（403 拦截）。
- [x] 前端：登录后按角色显示「用户管理」入口（仅 admin，`users.js`）；`api.js` 401 弹登录 / 403 提示已有。

### S3 行级隔离接入业务写接口（`routers/positions.py` / `employees.py` / `transfers.py` / `orgchart.py` + `app/schemas.py`）
- [x] 写操作按「读可跨司、写按实体」：岗位全局可读（写需登录）；员工/成本**修改**须其可管实体含该岗位 `company_id`，否则 403；成本字段与汇报接线分别按岗位实体/目标岗位实体校验。
- [x] 组织图 `/org-charts`：读可跨司、显示他司员工**姓名**（只读），不影响隔离。
- [x] 列表接口保持读侧跨司可见（符合「读可跨司」），写侧拦截。

### S4 转调/升职 / 人永不脱岗 / 单事务（`app/lifecycle.py` + `routers/transfers.py` + `app/helpers.py`）
- [x] `ALLOWED_EMPLOYEE` 扩展：`PLANNED→FILLED`（认领分配 Planned 空闲编制）；转调中持有=原岗保持 Filled（不流转）。
- [x] `POST /transfers/initiate`：原 HR 把人转出到目标公司（人仍挂原岗、原岗锁定、`target_company_id`=B），不释放。
- [x] **认领池按 `target_company_id` 过滤**：`GET /transfers/pending` 仅 B 的 HR 可见/可认领。
- [x] `POST /transfers/{id}/claim`：**单事务**——目标岗 Filled + 原岗 Vacant + 人挂新岗 + `prev_*` + `position_events`，行锁防并发抢岗。
- [x] `POST /transfers/{id}/reject`：退回原公司、原岗继续（人不脱岗）。
- [x] `POST /employees/{id}/promote`：`时节=month_end|immediate`（记入事件供财务月归属）；Filled 新岗、老岗默认 Vacant、`prev_*` 记录、工龄照人。注：无调度器，动作即时生效、时节留痕。
- [x] 事务原子性：认领/升职/离职解绑包进**单 commit**；并发抢同岗由 `with_for_update` 行锁 + 占用唯一约束保证（S8 实测 [200,400]，无一人双岗）。

### S5 成本双口径 + 税区（`app/helpers.py` 成本 + `routers/positions.py` + F0 前端）
- [x] `TaxZone` 作主数据可维护（`/tax-zones` CRUD，仅 admin 写）；`WorkLocation` 两级（国家+城市）+ 种子补齐。
- [x] **预算成本**自动计算按**岗位税区**；**实际成本**按**人的归属税区**（当前所挂岗位工作地点）；未配置税率 → `configured=false`「未配置」（不猜）。
- [x] `GET /positions/{id}/cost-calculation?scope=budget|actual&salary_before_tax=`。
- [x] 岗位详情显示预算∪实际两层（serialize_position join 占用员工输出 `actual_*`）。
- [x] 前端：岗位详情实际成本层展示；`master_data.js` 税区配置页（挂载级别/城市分拆/科目维护）。

### S6 编号系统 + 两段式导入（`app/helpers.py:generate_number` + `app/import_csv.py` + `scripts/import_csv.py`）（2026-08-23 已完成，两处按后续用户决策调整）
- [x] `generate_number`：正式 `P{seq}`、外包 `PA{seq}` 双序列，取同系列 max+1。
- [x] ~~清洗阶段输出 T 占位~~ **调整为「岗位编号列留空」**（2026-08-23 用户决策：源编号一律忽视、编号列空由系统导入时分配）。
- [x] ~~带 ID 认老~~ **调整为幂等键识别**（CSV 无 ID 列；按幂等键 upsert 认老更新、无匹配新建）。
- [x] 幂等键 = 职位名+公司+国家或地区（3 列，2026-08-23 决策，较本清单 4 列少开启日——以 PRD §10 决策记录为准）；文件内重复 → 报错该行不导入。
- [x] 导入报告返回 `{imported, updated, errors[], warnings[], assigned_numbers[]}`。
- [x] `prod` 下导入/清洗写操作拦截（沿用 #14 既有护栏）。

### S7 清洗 / 组织图 / 前端联动（`app/data_clean.py` + `app/export_md.py` + `routers/orgchart.py` + `static/js/*`）
- [x] `data_clean`：仅支持 Org-Chart3 格式（无编号树+权责说明续行）；编号列留空；真实树祖先推断直线经理（兄弟不互挂、公司清栈）。
- [x] 汇报接线权限：目标侧操作（S3 已实现）；跨司虚线靠导入后人工补。
- [x] 前端组织图读他司员工姓名（只读，天然满足）；`master_data.js` 加税区/工作地点两级配置。

### S8 端到端验证与并发/事务测试
- [x] 新增 `tests/test_v23.py`（42 项断言全通过）：认证建号/隔离读写 403/认领池过滤/转调 initiate-claim-reject/升职工龄照人/**并发抢同岗无一人双岗**/成本双口径/未配置税率回归/挂编联动拒绝。
- [x] `uvicorn main:app` → http://127.0.0.1:7273 全流程走查（启动打印 `APP_ENV`/库名）。
- [x] 三环境：dev/test `--reset` 允许、prod 拦截；`.env` 三段切换（S0 阶段已验证）。
- [x] `pg_dump` 备份（`data/backups/hr_db_prod_20260823_192018.dump`）。

> **实施顺序建议**：S1 → S2 → S3 → S4（权限 & 转调/升职是最重、最影响接口面）→ S5（成本）→ S6（导入）→ S7（清洗/前端）→ S8（验证）。PRD/DESIGN 为本清单唯一事实源，开发中发现问题优先回写文档再改代码。

