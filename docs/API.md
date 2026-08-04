# 轻量级 HR 管理系统 — REST API 文档

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 更新日期 | 2026-08-04 |
| 基准 | 严格 REST 规范（见 [DESIGN.md](./DESIGN.md) §6） |
| Base URL | `http://127.0.0.1:8000/api` |

---

## 1. 通用约定

- **REST 规范**：名词复数资源、HTTP 方法映射 CRUD、创建返回 `201 Created` + `Location` 头、部分更新用 `PATCH`。
- **请求/响应**：JSON（`Content-Type: application/json`）；导出为 `text/markdown`。
- **状态码**：
  | 码 | 含义 |
  | --- | --- |
  | 200 | 成功（GET / PATCH / DELETE） |
  | 201 | 创建成功（POST，响应头含 `Location`） |
  | 400 | 参数/业务校验失败（详情见 `detail`） |
  | 404 | 资源不存在 |
  | 422 | 状态流转非法 |
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

### 2.1 隶属公司 / 国家 / 级别 / 工作地点 / 工作范围

资源路径：`/companies`、`/countries`、`/levels`、`/work-locations`、`/scopes`

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

当前对外接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/public/companies` | 所有隶属公司（仅 `id` + `name`），无认证 |

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
| GET | `/positions/{id}` | 详情（含事件时间线） |
| PATCH | `/positions/{id}` | 部分更新（含成本字段；直线经理变更做环检测） |
| POST | `/positions/{id}/events` | 状态流转（201，创建一条事件并变更状态） |
| GET | `/positions/{id}/cost` | 按国家用工税额计算成本（只读） |
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

响应（201 + `Location: /api/positions/{id}`）：完整岗位对象，含 `number`、`status`、`cost_mode`、`salary_before_tax`、`company_share`、`labor_cost`、`incumbent_name` 等（键列表见 §3.3）。

**POST /positions/{id}/events（状态流转）请求体**

```json
{ "to_status": "open", "note": "开岗招聘" }
```

响应（201）：`{ "id", "position_number_id", "from_status", "to_status", "changed_at", "note" }`。非法流转返回 422。

**GET /positions/{id}/cost（自动模式成本计算，只读）**

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

> 计算规则：公司份额 = 税前薪资 × Σ(该岗位国家全部启用科目税率)；用工成本 = 税前薪资 + 公司份额。手动模式/无税前薪资/无国家 → 400。保存计算值请 `PATCH /positions/{id}`（`company_share` / `labor_cost`）。

### 3.3 岗位对象字段

```json
{
  "id": 13, "number": "P013-4-1", "position_id": 5, "position_name": "Statutory MLRO Manager",
  "company_id": 4, "company_name": "Peeters Global Shared Services SPRL",
  "level": "M8a", "scope": "country", "country_id": 1, "country_name": "比利时",
  "opening_date": "2001-01-01", "closing_date": null,
  "work_location": "比利时布鲁塞尔", "job_responsibility": "SSC 法定反洗钱负责人",
  "legal_category": null,
  "solid_line_manager_id": 15, "solid_line_number": "P015-4-1", "solid_line_manager_name": "Senior Operation Manager",
  "dotted_manager_ids": [5], "dotted_manager_numbers": ["P005-2"],
  "org_chart_display": "SSC Statutory MLRO", "prev_position_id": null, "prev_position_number": null,
  "prev_company_id": null, "prev_company_name": null, "remark": "双线汇报",
  "status": "open", "cost_mode": "manual", "salary_before_tax": null, "company_share": null, "labor_cost": null,
  "incumbent_id": null, "incumbent_name": null,
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
| GET | `/employees/{id}` | 详情（含岗位、直线/虚线经理） |
| PATCH | `/employees/{id}` | 部分更新；`employment_status=离职` 触发解绑 → 岗位 Vacant |
| POST | `/employees/{id}/transfers` | 调岗（201，旧岗→Vacant，新岗→Filled） |
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

**PATCH /employees/{id}（离职）**

```json
{ "employment_status": "离职" }
```

**POST /employees/{id}/transfers 请求体**

```json
{ "to_position_id": 15 }
```

**员工对象字段**：`id, employee_no, name, gender, birth_date, phone, email, hire_date, employee_type, employment_status, position_number_id, position_number, position_name, company_id, company_name, solid_line_manager_id, solid_line_number, solid_line_manager_name, dotted_manager_ids, dotted_manager_numbers, remark, created_at, updated_at`

---

## 5. 组织架构

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/orgchart` | 汇报线树数据（JSON） |
| GET | `/orgchart?format=md&report={org\|solid\|dotted}` | 导出 Markdown |

**GET /orgchart（JSON）**

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
| POST | `/import/csv` | 上传 Position.csv（multipart，字段名 `file`），幂等 upsert |

响应：

```json
{ "total": 63, "imported": 63, "updated": 0, "errors": [], "warnings": [] }
```

- 校验：编号唯一且格式合法；**文件内编号重复 → 报错（该行不导入）**；Country 级必须 `-4-{编号}`；直线/虚线引用必须存在；关闭日不早于开启日。
- 幂等：与库内已有编号重复按更新处理。

---

## 7. 健康检查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | `{"status": "ok", "app": "HR Management"}` |

---

## 附录：完整端点清单

| 方法 | 路径 | 模块 |
| --- | --- | --- |
| GET/POST, PATCH/DELETE | /companies、/countries、/levels、/work-locations、/scopes | master_data |
| GET/POST, PATCH/DELETE | /employment-tax-items、/employment-tax-items/{id} | master_data |
| GET | /public/companies | master_data |
| GET/POST | /position-functions | positions |
| GET/POST, GET/PATCH/DELETE | /positions、/positions/{id} | positions |
| POST | /positions/{id}/events | positions |
| GET | /positions/{id}/cost | positions |
| GET/POST, GET/PATCH/DELETE | /employees、/employees/{id} | employees |
| POST | /employees/{id}/transfers | employees |
| GET | /orgchart | orgchart |
| POST | /import/csv | import_routes |
| GET | /health | main |

> 注：`/legal-categories` 端点后端仍存在，但「法律强制/可选」字段已从当前数据源移除、系统不再维护，故不纳入上文（建议后续从代码中移除）。
