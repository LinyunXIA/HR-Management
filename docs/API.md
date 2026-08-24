# 轻量级 HR 管理系统 — REST API 文档

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.1 |
| 更新日期 | 2026-08-22 |
| 基准 | 严格 REST 规范（见 [DESIGN.md](./DESIGN.md) §6） |
| Base URL | `http://127.0.0.1:8000/api/v1`（端口随 `uvicorn --port` 可变，dev 默认 `7273`；三环境 `.env` 单文件 via `APP_ENV` 分流，见 `app/db.py:1`） |

---

## 1. 通用约定

- **REST 规范**：名词复数资源、HTTP 方法映射 CRUD、创建返回 `201 Created` + `Location` 头、部分更新用 `PATCH`。
- **请求/响应**：JSON（`Content-Type: application/json`）；导出为 `text/markdown`。
- **认证**：对外接口 `GET /public/companies` 需 JWT + 对应 API 权限，`GET /auth/me` 及内部管理接口需 JWT（`Authorization: Bearer <token>`，见 §2.4；`app/auth.py:76`）。
- **限流**：全局 `120/minute` / IP（`app/limiter.py:1` / `main.py:1`）；敏感接口单独更严：`POST /auth/login` `10/min`、`GET /public/companies` `60/min`，超限返回 `429`。
- **状态码**：
  | 码 | 含义 |
  | --- | --- |
  | 200 | 成功（GET / PATCH / DELETE） |
  | 201 | 创建成功（POST，响应头含 `Location`） |
  | 400 | 参数/业务校验失败（详情见 `detail`） |
  | 401 | 未认证/Token 过期或无效（对外接口） |
  | 404 | 资源不存在 |
  | 409 | 乐观锁冲突（`version` 不一致，见 §3.2/`app/helpers.py:1`） |
  | 422 | 状态流转非法 |
  | 429 | 限流（`RateLimitExceeded`，见 `main.py:1`） |
- **错误格式**：`{"detail": "错误描述"}` 或校验错误的字段数组。
- **分页**：列表接口返回 `{total, page, page_size, items}`，参数 `page` / `page_size`（默认 50）。

### 生命周期状态
`planned(编制规划) / open(招聘中) / offered(已录用) / filled(在职) / vacant(空缺) / frozen(冻结) / closed(关闭)`

手动流转白名单（`filled↔vacant` 由员工入职/调岗/离职自动触发，不可手动）：

| 当前 | 可流转到 |
| --- | --- |
| planned | open / closed / frozen |
| open | offered / closed |
| offered | open |
| vacant | open / closed / frozen |
| frozen | planned / open |
| closed | （终态） |

---

## 2. 主数据（Master Data）

### 2.1 隶属公司 / 国家 / 级别 / 工作地点 / 工作范围 / 法律强制 / 职位类型

资源路径：`/companies`、`/countries`、`/levels`、`/work-locations`、`/scopes`、`/legal-categories`、`/position-types`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/{resource}` | 列表 |
| POST | `/{resource}` | 新建（201） |
| PATCH | `/{resource}/{id}` | 部分更新 |
| DELETE | `/{resource}/{id}` | 删除（被岗位引用时禁止，返回 400） |

创建请求体示例（级别）：

```json
{ "code": "M9a", "label": "Director", "is_management": true, "sort_order": 9 }
```

响应示例（级别）：

```json
{ "id": 9, "code": "M9a", "label": "Director", "is_management": true, "sort_order": 9 }
```

各资源字段：

| 资源 | 字段 |
| --- | --- |
| companies | `name` |
| countries | `name`, `code`（如 `4-5`） |
| levels | `code`（如 M8a/B7b）, `label`, `is_management`, `sort_order` |
| work-locations | `name`, `sort_order` |
| scopes | `code`（family/global/regional/country）, `label`, `suffix_code`（1/2/3/4）, `sort_order` |
| legal-categories | `name`, `sort_order` |
| position-types | `name`, `sort_order` |

### 2.2 员工用工税额（按国家）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/employment-tax-items?country_id=` | 列表（可按国家过滤） |
| POST | `/employment-tax-items` | 新增科目（201） |
| PATCH | `/employment-tax-items/{id}` | 更新 |
| DELETE | `/employment-tax-items/{id}` | 删除 |

创建请求体：

```json
{ "country_id": 1, "item_name": "Social Security", "tax_rate": 13.07, "is_active": true }
```

响应：

```json
{ "id": 5, "country_id": 1, "country_name": "比利时", "item_name": "Social Security", "tax_rate": 13.07, "is_active": true }
```

### 2.3 对外接口（Public API）

对外暴露的只读接口已**单独成文**，见 **[API_PUBLIC.md](./API_PUBLIC.md)**。

当前对外接口（**需 JWT + 对应 API 权限**，v2.4.3 权限拆分；详见 [API_PUBLIC.md](./API_PUBLIC.md)）：

| 方法 | 路径 | 说明 | 认证 | 限流 |
| --- | --- | --- | --- | --- |
| GET | `/public/companies` | 隶属公司全字段：ID/名称/开业·关闭日期/股权结构/状态；非 admin 按可管实体过滤 | JWT + `public.companies` 授权 | `60/min` |

### 2.4 认证（Auth，PRD §7B）

| 方法 | 路径 | 说明 | 认证 | 限流 |
| --- | --- | --- | --- | --- |
| POST | `/auth/login` | 登录换取 JWT（API 类型用户须持「认证」授权） | 无 | `10/min` |
| GET | `/auth/me` | 当前用户信息 | JWT | 全局 |

> 建号/用户管理统一走 `/admin/users*`（§6.0，仅 UI admin）；`/auth/register`、
> `/auth/register-first`、`/users` 遗留端点已移除（v2.4.3）。

**POST /auth/login 请求体**

```json
{ "username": "admin", "password": "admin123" }
```

响应（`app/routers/auth.py:39`）：

```json
{ "access_token": "eyJhbGciOiJIUzI1NiIs...", "token_type": "bearer", "username": "admin", "role": "admin", "expires_in": 43200 }
```

- 默认种子 `admin/admin123`（`app/seed.py:1`，可由 `DEFAULT_ADMIN_USER/PASSWORD` 覆盖）。
- JWT：HS256，`app/auth.py:20`（`JWT_SECRET_KEY`/`JWT_EXPIRE_MINUTES`），`Authorization: Bearer <token>`（兼容 `X-Token` / `?token=`）。

---

## 3. 职位与岗位

### 3.1 职位（职能）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/position-functions` | 职位（职能）列表 |
| POST | `/position-functions` | 新建（201） |

### 3.2 岗位（Position Number）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/positions` | 列表（含筛选） |
| POST | `/positions` | 创建（201，**编号自动生成**） |
| GET | `/positions/{id}` | 详情（含事件时间线 + `version`） |
| PATCH | `/positions/{id}` | 部分更新（含成本字段；直线经理变更做环检测；**需 `version` 乐观锁，冲突 409**） |
| POST | `/positions/{id}/transitions` | 状态流转（201，创建一条事件并变更状态） |
| GET | `/positions/{id}/transitions` | 该岗位的流转事件列表 |
| GET | `/transitions?positionId=` | 全局流转事件列表（可按岗位过滤） |
| GET | `/positions/{id}/cost-calculation?salary_before_tax=` | 按国家用工税额计算成本（只读派生资源，支持 `?salary_before_tax=` 传入未落库薪资避免双重 PATCH，#15） |
| DELETE | `/positions/{id}` | 删除（有在职员工或已有事件时禁止） |

**GET /positions 查询参数**

| 参数 | 说明 |
| --- | --- |
| company_id | 隶属公司过滤 |
| scope | 工作范围（family/global/regional/country） |
| status | 生命周期状态 |
| search | 按编号/职位/显示名模糊搜索 |
| role | `manager` = 仅管理岗（级别 M 开头），用于经理下拉 |
| page / page_size | 分页 |

**POST /positions 请求体**（`number` 不接受，自动生成；描述/成本/备注可空）

```json
{
  "position_name": "Backend Engineer",
  "company_id": 1,
  "level": "B7b",
  "scope": "global",
  "country_id": null,
  "position_type": "Employee",
  "opening_date": "2026-01-01",
  "closing_date": null,
  "work_location": "比利时布鲁塞尔",
  "job_responsibility": "负责后端开发",
  "solid_line_manager_id": 4,
  "dotted_manager_ids": [7],
  "org_chart_display": "Backend Engineer",
  "remark": null,
  "cost_mode": "manual",
  "salary_before_tax": null,
  "company_share": null,
  "labor_cost": null
}
```

响应（201 + `Location: /api/positions/{id}`）：完整岗位对象，含 `number`、`status`、`cost_mode`、`salary_before_tax`、`company_share`、`labor_cost`、`incumbent_name`、`version` 等（键列表见 §3.3）。

**PATCH /positions/{id}（乐观锁，PRD §7C）**

- 请求体需携带 `version`（从 `GET /positions/{id}` 读取，`app/helpers.py:1` 的 `assert_version` 校验）：
  ```json
  { "version": 3, "remark": "更新备注" }
  ```
- 成功：`version` 自增并返回新对象；冲突：`409 {"detail":"岗位已被他人修改，请刷新后重试（当前版本 3，提交版本 2）"}`，前端 `static/js/positions.js:1` 提示刷新。

**POST /positions/{id}/transitions（状态流转）请求体**

```json
{ "to_status": "open", "note": "开岗招聘" }
```

响应（201）：`{ "id", "position_number_id", "from_status", "to_status", "changed_at", "note" }`。非法流转返回 422。不受乐观锁约束（`lifecycle.transition` 原子）。

**GET /positions/{id}/cost-calculation?salary_before_tax=（自动模式成本计算，只读派生资源，#15）**

- 支持 `?salary_before_tax=` 查询参数：传入前端输入的未落库薪资值直接计算，避免编辑页双重 `PATCH` 导致的 409 冲突；不传则使用 DB 已保存的 `salary_before_tax`。

响应：

```json
{
  "position_id": 13,
  "salary_before_tax": 120000,
  "tax_rate_total": 21.93,
  "tax_items": [ { "item_name": "Social Security", "tax_rate": 13.07 }, { "item_name": "Pension", "tax_rate": 8.86 } ],
  "company_share": 26316,
  "labor_cost": 146316
}
```

> 计算规则：公司份额 = 税前薪资（优先取 `?salary_before_tax=`，否则取 DB 值）× Σ(该岗位国家全部启用科目税率)；用工成本 = 税前薪资 + 公司份额。无税前薪资/无国家 → 400。保存计算值请 `PATCH /positions/{id}`（`company_share` / `labor_cost` + `version`），建议单次 PATCH 完成 `cost_mode`/`salary_before_tax`/`company_share`/`labor_cost` 批量保存。

### 3.3 岗位对象字段

```json
{
  "id": 13, "number": "P013-4-1", "position_id": 5, "position_name": "Statutory MLRO Manager",
  "company_id": 4, "company_name": "Peeters Global Shared Services SPRL",
  "level": "M8a", "scope": "country", "country_id": 1, "country_name": "比利时",
  "position_type": "Employee",
  "opening_date": "2001-01-01", "closing_date": null,
  "work_location": "比利时布鲁塞尔", "job_responsibility": "SSC 法定反洗钱负责人",
  "legal_category": null,
  "solid_line_manager_id": 15, "solid_line_number": "P015-4-1", "solid_line_manager_name": "Senior Operation Manager",
  "dotted_manager_ids": [5], "dotted_manager_numbers": ["P005-2"],
  "org_chart_display": "SSC Statutory MLRO", "prev_position_id": null, "prev_position_number": null,
  "prev_company_id": null, "prev_company_name": null, "remark": "双线汇报",
  "status": "open", "cost_mode": "manual", "salary_before_tax": null, "company_share": null, "labor_cost": null,
  "incumbent_id": null, "incumbent_name": null, "version": 3,
  "created_at": "2026-08-04T08:00:00", "updated_at": "2026-08-04T08:00:00"
}
```

> 详情接口 `GET /positions/{id}` 额外返回 `events[]`（`{id, from_status, to_status, changed_at, note}`，时间倒序）。

---

## 4. 员工

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/employees` | 列表（含筛选） |
| POST | `/employees` | 创建（201，必须挂岗 → 岗位自动 Filled） |
| GET | `/employees/{id}` | 详情（含岗位、直线/虚线经理 + `version`） |
| PATCH | `/employees/{id}` | 部分更新；`employment_status=离职` 触发解绑 → 岗位 Vacant；**需 `version` 乐观锁，冲突 409** |
| POST | `/transfers` | 调岗（201，旧岗→Vacant，新岗→Filled） |
| GET | `/transfers?employeeId=` | 调岗记录列表（可按员工过滤） |
| DELETE | `/employees/{id}` | 删除（仅已离职且已解绑） |

**GET /employees 查询参数**：`company_id`、`employee_type`、`employment_status`、`search`、`page`、`page_size`。

**POST /employees 请求体**（出生日期/手机/邮箱/备注可空）

```json
{
  "employee_no": "E9001", "name": "张三", "gender": "男",
  "birth_date": null, "phone": null, "email": null, "hire_date": "2026-08-01",
  "employee_type": "正式", "employment_status": "在职",
  "position_number_id": 13, "remark": null
}
```

**PATCH /employees/{id}（离职，乐观锁）**

```json
{ "version": 2, "employment_status": "离职" }
```

冲突同岗位：`409 {"detail":"员工已被他人修改，请刷新后重试"}`。

**POST /transfers 请求体**

```json
{ "employee_id": 5, "to_position_id": 15 }
```

**员工对象字段**：`id, employee_no, name, gender, birth_date, phone, email, hire_date, employee_type, employment_status, position_number_id, position_number, position_name, company_id, company_name, solid_line_manager_id, solid_line_number, solid_line_manager_name, dotted_manager_ids, dotted_manager_numbers, remark, version, created_at, updated_at`

---

## 5. 组织架构

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/org-charts` | 汇报线树数据（JSON） |
| GET | `/org-charts?report={org\|solid\|dotted}` | 导出 Markdown（`Accept: text/markdown`） |

**GET /org-charts（JSON）**

```json
{
  "nodes": [
    { "id": 7, "number": "P063-4-5", "display": "Soparfi Managing Director - 卢森堡控股总经理",
      "position_name": "Soparfi Managing Director", "company": "Soparfi S.à r.l.", "level": "M11a",
      "scope": "country", "country": "卢森堡", "status": "open", "closed": false,
      "incumbent": null, "incumbent_id": null }
  ],
  "solid_edges":  [ { "from": "P063-4-5", "to": "P004-1" } ],
  "dotted_edges": [ { "from": "P028-4-6", "to": "P005-2", "label": "虚线汇报" } ],
  "roots": [ "P063-4-5", "P066-1" ]
}
```

**导出 Markdown**（`text/markdown; charset=utf-8`）：

| report | 内容 |
| --- | --- |
| `org` | 公司 + 岗位（无汇报线，按公司分组） |
| `solid` | 直线汇报线（层级树） |
| `dotted` | 虚线汇报线（岗位 → 虚线经理） |

---

## 6. 数据导入

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/imports` | 上传 Position.csv（multipart，字段名 `file`），幂等 upsert（各环境均允许，v2.4.1 移除 #14 prod 拦截） |

响应：

```json
{ "total": 4, "imported": 4, "updated": 0, "updated_by_id": 0, "updated_by_key": 0,
  "errors": [], "warnings": [],
  "assigned_numbers": [{"label": "Statutory Manager", "number": "P1"}] }
```

- **编号由系统强制分配（v2.3）**：CSV「岗位编号」列一律忽视——正式岗 `P{seq}`、外包岗 `PA{seq}`，序号=库内同系列最大值+1；新建行返回 `assigned_numbers[]` 明细。
- **迭代识别（#49 定稿）**：CSV 可携带**可选「岗位ID」列**（导出环节输出正式 ID）。带 ID 且库内存在 → 按正式 ID 认老更新（计入 `updated_by_id`）；无 ID / 未携带 → 回退幂等键认老（`updated_by_key`）；均未命中 → 新建。带 ID 但库内不存在 / 同文件多行引用同一 ID / ID 格式非法 → 报错该行不导入。
- 幂等键：**职位名+隶属公司+国家或地区+开启日**（4 列，PRD §3.1 v2.3）；库内已存在则更新字段（保留编号/事件/员工关联）；文件内幂等键重复 → 报错该行不导入。strict 模式为全局默认（#51）：法律分类不在主数据字典的行报错不导入；仅历史数据迁移可显式关闭——CLI `--no-strict-legal`。
- 直线/虚线经理按**职位名**解析；同名多岗取首个并告警。
- 生产护栏（v2.4.1 调整）：Web 导入为幂等 upsert 非破坏性操作，各环境均允许；仅 `--reset`/`drop_all` 等清空库操作被 `assert_writable` 拦截。

---

## 7. 数据清洗（Org-Chart 解析 → CSV 生成）

上传 **Org-Chart3.md**（唯一支持格式），按 Position.csv 模版格式自动解析、清洗、输出标准 CSV。规则文件（Position.md）使用固定模版。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/data-clean-jobs/files/list` | 列出 testingdata/原始文件/ 下的文件 |
| POST | `/data-clean-jobs` | 创建清洗作业（上传 Org-Chart3.md 或 `?source_file=` 指定服务器文件，返回作业 id+报告+CSV） |
| GET | `/data-clean-jobs/{jobId}` | 查询清洗作业 |
| POST | `/data-clean-jobs/{jobId}/imports` | 将作业 CSV 导入系统（幂等 upsert，编号系统分配） |

**POST /data-clean-jobs**

两种方式：
1. multipart/form-data，字段 `orgchart`：Org-Chart3.md 文件；
2. 无文件时查询参数 `?source_file=Org-Chart3.md` 使用服务器 `testingdata/原始文件/` 下的 .md（未指定默认 `Org-Chart.md`）。

响应：

```json
{
  "total_positions": 4,
  "report": {
    "total_positions": 4,
    "valid": 4,
    "fixed": 0,
    "warnings": [],
    "errors": []
  },
  "csv_text": "职位,职位类型,岗位编号,隶属公司,...",
  "cleaned": [...],
  "template": "Position.csv"
}
```

**解析能力（Org-Chart3 格式）**：
- 树区：`# 完整组织架构树…` 开始、下一个 `#` 标题结束；根节点行忽略
- 提取全部三类岗位：🧑‍💼 / 👨‍👩‍👧 / 📋（外包岗以 `External Employee` 入库）
- 岗位行 `英文名 - 中文名 【法律分类】(Opening: YYYY)`（无编号；带编号也剥离忽视）
- `权责说明：…` 续行 → 工作职责描述
- 从树结构推断隶属公司与直线经理（真实树祖先：兄弟不互挂、公司清栈；无显式层级 → N/A）
- 8 个关键字段完整读取：职位名、职位类型、隶属公司、级别（从职位名推断）、国家或地区（从**公司地点**推断）、职位开启日、工作地点（从**公司名字**推断）、法律强制/可选
- CSV「岗位编号」列输出留空 → 导入时由系统分配 P/PA 编号

---

## 8. 健康检查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | `{"status": "ok", "app": "HR Management", "env": "dev"}`（`env` 为 `APP_ENV`，`main.py:1`） |

---

## 附录：完整端点清单

| 方法 | 路径 | 模块 | 认证 | 限流 |
| --- | --- | --- | --- | --- |
| POST | /auth/login | auth | 无 | `10/min` |
| GET | /auth/me | auth | JWT | 全局 |
| GET/POST, PATCH/DELETE | /companies、/countries、/levels、/work-locations、/scopes、/legal-categories、/position-types | master_data | 无 | 全局 |
| GET/POST, PATCH/DELETE | /employment-tax-items、/employment-tax-items/{id} | master_data | 无 | 全局 |
| GET | /public/companies | master_data | JWT | `60/min` |
| GET/POST | /position-functions | positions | 无 | 全局 |
| GET/POST, GET/PATCH/DELETE | /positions、/positions/{id} | positions | 无 | 全局（PATCH 需 `version`，`app/helpers.py:1`） |
| POST | /positions/{id}/transitions | positions | 无 | 全局 |
| GET | /positions/{id}/transitions | positions | 无 | 全局 |
| GET | /transitions | positions | 无 | 全局 |
| GET | /positions/{id}/cost-calculation?salary_before_tax= | positions | 无 | 全局 |
| GET/POST, GET/PATCH/DELETE | /employees、/employees/{id} | employees | 无 | 全局（PATCH 需 `version`） |
| POST | /transfers | transfers | 无 | 全局 |
| GET | /transfers | transfers | 无 | 全局 |
| GET | /org-charts | orgchart | 无 | 全局 |
| POST | /imports | import_routes | 无 | 全局 |
| GET | /imports | import_routes | 无 | 全局 |
| GET/POST | /data-clean-jobs、/data-clean-jobs/{jobId}、/data-clean-jobs/{jobId}/imports、/data-clean-jobs/files/list | data_clean | 无 | 全局 |
| GET | /health | main | 无 | 全局 |

> 注：以上为系统当前活跃端点全集。全局限流 `120/min`（`app/limiter.py:1`）；超限 `429`。

