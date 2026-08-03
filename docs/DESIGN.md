# 轻量级 HR 管理系统 — 技术设计文档（DESIGN）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 更新日期 | 2026-08-03 |
| 关联 | [PRD.md](./PRD.md)（v0.3，需求与决策） |
| 目标版本 | V1.0 |

---

## 1. 概述

基于集团既有职位规则与数据（`Position.md` / `Position.csv` / `Org-Chart.md`），构建轻量级 HR 管理系统，覆盖岗位全生命周期、人员信息、组织架构图（汇报线树）三大能力。本文档定义技术实现：技术选型、数据库设计、生命周期状态机、REST API、CSV 导入、前端与组织架构图渲染、实施步骤与验证方案。

关键既定决策（详见 PRD §3.7/§10）：
- **数据权威**：以 `Position.csv` 为准；`Org-Chart.md` 仅展示参考，V1 不直接读取。
- **组织图主视图**：汇报线树（节点=岗位，按直线经理成树，虚线另行标注）。
- **外包岗位**：V1 仅内部全职（CSV 91 岗），外包服务商不入系统。
- **员工必须挂岗**：不允许「待分配」员工。
- **虚拟根节点**：「家族自然人股东」默认开启、可开关。
- **年份精度**：开启/关闭日存 date，年份→`YYYY-01-01`。

## 2. 技术选型

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 语言/运行时 | Python 3.14.6（项目 .venv） | 已验证存在 |
| Web 框架 | FastAPI（同步端点） | REST API + 托管静态文件 |
| ORM | SQLAlchemy 2.x（同步） | 单用户本地工具，无需异步 |
| 校验 | Pydantic v2 | 与 FastAPI 集成 |
| 数据库 | SQLite（单文件 `hr.db`） | 备份=拷贝文件 |
| 前端 | 原生 JS + 自定义 SVG 树渲染 | **零依赖、无 npm、无构建** |
| 启动 | `uvicorn main:app --reload` | 一条命令 |

**组织架构图渲染决策**：自定义 SVG 树渲染器（约 350 行，零依赖）。理由：汇报线树结构明确、需同时绘制实线（直线）与跨树虚线（虚线汇报），SVG 完全可控，符合「轻量级」定位。备选：`vis-network`（vendored，hierarchical 布局 + dashes 边），若自定义布局工作量失控时启用。

依赖清单（`requirements.txt`）：`fastapi`、`uvicorn[standard]`、`sqlalchemy>=2.0`、`pydantic>=2.7`、`python-multipart`（文件上传）。

## 3. 项目结构

```
HR_Management/
├── main.py                 # FastAPI 入口：注册路由 + 挂载静态
├── requirements.txt
├── hr.db                   # SQLite（首次启动生成）
├── app/
│   ├── __init__.py
│   ├── db.py               # engine / SessionLocal / Base
│   ├── models.py           # SQLAlchemy 模型
│   ├── schemas.py          # Pydantic 模型
│   ├── lifecycle.py        # 状态机 + 流转校验 + 事件记录
│   ├── orgchart.py         # 组织树构建 + 环检测
│   ├── import_csv.py       # Position.csv 解析/校验/入库
│   └── routers/
│       ├── __init__.py
│       ├── positions.py
│       ├── employees.py
│       ├── orgchart.py
│       └── import_routes.py
├── static/
│   ├── index.html          # 单页，Tab：岗位/员工/组织架构/导入
│   ├── css/style.css
│   └── js/
│       ├── api.js          # fetch 封装
│       ├── app.js          # Tab 路由
│       ├── positions.js    # 岗位列表/详情/生命周期操作
│       ├── employees.js    # 员工管理/入职离职调岗
│       ├── orgchart.js     # SVG 汇报线树渲染
│       └── import.js       # 导入页
├── scripts/
│   └── import_csv.py       # CLI 导入：python -m scripts.import_csv data/Position.csv
└── data/                   # 拷贝一份 Position.csv 用于导入
```

## 4. 数据库设计

### 4.1 表结构

```python
# app/models.py —— 关键模型

class Company(Base):               # 隶属公司（法人实体）
    __tablename__ = 'companies'
    id, name = Column(unique)

class Country(Base):               # 国家/地区（仅 Country 范围用）
    __tablename__ = 'countries'
    id, name(unique), code        # code 如 '4-1'..'4-9'

class Position(Base):              # 职位（职能，不含工作范围）
    __tablename__ = 'positions'
    id, name(unique)

class PositionNumber(Base):        # 岗位编号（管理主体）
    __tablename__ = 'position_numbers'
    id
    number        unique          # P{seq}-{scope}，如 P063-4-5
    position_id   FK positions
    company_id    FK companies
    level                        # 字符串：M8a / B7a …
    scope         Enum(family|global|regional|country)
    country_id    FK countries, nullable    # scope=country 时必填
    opening_date  Date, nullable           # 年份→YYYY-01-01
    closing_date  Date, nullable
    work_location, job_responsibility, legal_category
    solid_line_manager_id  FK self, nullable   # 直线经理
    org_chart_display, prev_position_id, prev_company_id, remark
    status        Enum(planned|open|offered|filled|vacant|frozen|closed)
    created_at, updated_at

class PositionNumberDottedLine(Base):  # 虚线经理（多对多）
    __tablename__ = 'position_number_dotted_lines'
    id
    position_number_id  FK
    dotted_manager_id   FK
    Unique(position_number_id, dotted_manager_id)

class PositionEvent(Base):         # 生命周期事件（时间线）
    __tablename__ = 'position_events'
    id
    position_number_id  FK
    employee_id         FK employees, nullable
    from_status         nullable      # 首次创建为 null
    to_status
    changed_at          datetime
    note                nullable

class Employee(Base):              # 人员档案
    __tablename__ = 'employees'
    id
    employee_no  unique
    name, gender
    birth_date, phone, email, hire_date   # 可空
    employee_type, employment_status
    position_number_id  FK NOT NULL       # 必须挂岗
    remark, created_at, updated_at
```

### 4.2 约束与索引

- 唯一约束：`position_numbers.number`、`companies.name`、`positions.name`、`employees.employee_no`。
- 岗位↔员工一对一：`employees.position_number_id` 设唯一约束（一个岗位至多 1 名在职员工）。
- 删除保护：岗位有在职员工或已有 `position_events` 时禁止删除，仅允许状态关闭。
- 编号规则校验：`number` 与 `scope/country` 一致性在模型层 + 导入层双重校验（Country 级必须 `-4-{编号}`）。
- 索引：`position_numbers(status)`、`position_numbers(solid_line_manager_id)`、`position_events(position_number_id, changed_at)`、`employees(position_number_id)`。

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
- 挂编条件：岗位须为 `open / vacant`；`filled`（已占用）/`closed`/`planned` 拒绝挂编。

## 6. REST API 设计（前缀 /api）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /companies | 公司下拉/筛选（导入自动建） |
| GET | /countries | 国家下拉 |
| GET/POST | /position-functions | 职位（职能）列表/创建 |
| GET | /positions | 岗位列表：filter company_id/scope/status/search，分页 |
| POST | /positions | 创建（编号自动生成或手工，规则校验） |
| GET | /positions/{id} | 详情：字段 + 占用员工 + 直线/虚线 + 事件时间线 |
| PUT | /positions/{id} | 更新（含直线/虚线；直线变更做环检测） |
| POST | /positions/{id}/transition | 状态流转 {to_status, note} |
| DELETE | /positions/{id} | 仅无占用员工且无事件时允许 |
| GET | /employees | 员工列表：filter company_id/employee_type/employment_status/search，分页 |
| POST | /employees | 创建（必须挂岗 → 岗位自动 Filled） |
| GET | /employees/{id} | 详情（含岗位、直线/虚线经理解析） |
| PUT | /employees/{id} | 更新 |
| POST | /employees/{id}/transfer | 调岗 {to_position_id}（旧岗→Vacant，新岗→Filled） |
| POST | /employees/{id}/offboard | 离职（岗位→Vacant，解绑） |
| DELETE | /employees/{id} | 删除员工 |
| GET | /orgchart | 组织树数据：{nodes, solid_edges, dotted_edges, roots} |
| POST | /import/csv | 上传 Position.csv → 校验/幂等入库，返回报告 |

### 6.1 /orgchart 返回结构（供 SVG 渲染）

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

### 6.2 环检测

设置/导入 `solid_line_manager_id` 时，沿上级链上溯，若回到自身即拒绝（A→B→A）。

## 7. Position.csv 导入（app/import_csv.py + 路由 + CLI）

- 标准库 `csv` 解析（自动处理引号逗号，如 `"Peeters Shanghai IT Services Co., Ltd."`）。
- 字段映射 16 列 → `position_numbers`；自动建 company / position(职能) / country。
- 直线/虚线经理按正则 `\(P[\d\-]+\)` 解析编号 → 外键；`N/A` → 空；虚线列按 `;`/`、` 分割支持多值。
- 状态映射：`closing_date` 有值 → `closed`；否则 → `open`。已关闭岗写一条关闭事件。
- 年份解析：`1982` → `1982-01-01`。
- 校验：编号唯一且格式合法；Country 级必须 `-4-{编号}`；直线/虚线引用必须存在；关闭日不早于开启日。
- 幂等：按编号 upsert（已存在则更新字段，保留事件与员工关联）。返回报告 `{imported, updated, errors[]}`。

CLI：`python -m scripts.import_csv data/Position.csv`；Web：上传接口。

## 8. 前端设计

- **单页 + Tab**：岗位 / 员工 / 组织架构 / 导入；顶部显示库状态（岗位数、员工数）。
- **岗位管理**：列表（筛选+搜索+分页）→ 新建表单（编号自动生成预览）→ 详情抽屉：全部字段 + 占用员工 + 直线/虚线经理 + **生命周期时间线** + 流转操作按钮。
- **员工管理**：列表 → 新建（选择岗位，仅列 Open/Vacant）→ 详情：档案 + 入职/调岗/离职操作。
- **导入页**：文件选择上传 + 校验报告（imported/skipped/errors 明细）。
- **组织架构图（orgchart.js，核心）**：
  - 读取 `/api/orgchart`，按 `solid_line_manager_id` 建多根树。
  - **虚拟根开关（默认开）**：「家族自然人股东」根节点归拢全部根。
  - 布局：子树递归测宽 + 居中（tidy tree）；节点卡片显示 display 名、编号、公司、级别、状态徽标；Filled 显示在职员工。
  - **实线** = 直线（父→子，垂直+水平肘线）；**虚线** = 虚线汇报（跨树虚线曲线，悬浮标「虚线汇报」）。
  - 交互：点击展开/折叠、滚轮缩放、拖拽平移、悬浮 tooltip、按公司/范围/状态筛选高亮（其余置灰）、「含已关闭岗位」开关（默认关，关闭岗灰色且默认折叠）。

## 9. 实施步骤（顺序）

1. **骨架**：requirements → .venv 安装（验证 Python 3.14 兼容）→ `db.py`/`models.py`/`main.py` + 建表，`uvicorn main:app` 可启动。
2. **生命周期模块**：`lifecycle.py` 状态机 + 事件；`schemas.py`；`routers/positions.py`（CRUD + transition + 编号生成 + 环检测）。
3. **员工模块**：`routers/employees.py`（CRUD + transfer + offboard + 岗位联动）。
4. **组织数据**：`orgchart.py` 构建 `/api/orgchart`。
5. **导入**：`import_csv.py` + 路由 + CLI。
6. **前端基础**：`index.html`/`style.css`/`api.js`/`app.js`/`positions.js`/`employees.js`。
7. **组织架构图前端**：`orgchart.js`（SVG 树渲染，工作量最大）。
8. **导入页 + 联调**：导入真实 Position.csv，全流程走查。
9. **端到端验证**（见 §10）。

## 10. 验证方案（端到端）

1. `.venv/bin/pip install -r requirements.txt`（Python 3.14 兼容验证）。
2. `uvicorn main:app --reload` → http://127.0.0.1:8000 打开。
3. **导入**：`python -m scripts.import_csv data/Position.csv` → 期望 91 行、无 error；`position_numbers`=91。
4. **API 冒烟（curl）**：
   - `GET /api/positions?status=open` 数量正确（91 中 8 个已关闭）。
   - `GET /api/orgchart`：nodes=91、solid_edges 数、dotted_edges 数（约 20+）、roots=[P063,P066,P001]。
   - `POST /positions/{id}/transition` 非法流转（planned→filled）→ 422。
   - 直线设置成环（A→B→A）→ 422。
5. **UI 走查**：导入 → 岗位详情时间线（已关闭岗有关闭事件）→ 新建员工挂到 Open 岗 → 该岗变 Filled、组织图显示在职 → 调岗（旧岗 Vacant）→ 离职（岗位 Vacant）。
6. **组织图**：实线/虚线正确渲染；虚拟根开关生效；关闭岗置灰可隐藏；按公司筛选高亮。
7. 数据备份：复制 `hr.db` 后重开可用。

## 11. 风险与注意点

- **Python 3.14 包兼容**：若 pydantic/sqlalchemy 最新版无 3.14 wheel，按报错升降级（第 1 步先行验证）。
- **编号规则出入**（同职位多 P 序号）：以现有数据为准，不强制归一（PRD §3.1）。
- **Org-Chart.md 不一致项**：以 CSV 为准，系统内不提示冲突（PRD §3.8）。
- **虚线解析**：虚线列多为单值，按 `;`/`、` 分割支持多值（模型为 0~N）。
- **组织图性能**：91 节点量级自定义 SVG 足够；节点折叠控制渲染量。
