# 对外接口文档（Public API）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.5 |
| 更新日期 | 2026-08-24 |
| Base URL | `http://127.0.0.1:8000/api/v1`（端口随 `uvicorn --port` 可变，dev 默认 `7273`） |
| 说明 | 面向外部系统/第三方调用的对外接口（字典查询为只读；基准推送为写入型），**需 JWT 认证**（PRD §7B）+ 对应 **API 权限**，限流保护 |

> 本文件单独收录对外暴露的接口；系统内部管理接口见 [API.md](./API.md)。

## 接口总览

| # | 方法 | 路径 | 功能 | 所需 API 权限 | 限流 |
| --- | --- | --- | --- | --- | --- |
| 0 | POST | `/auth/login` | 登录换取 JWT | `auth.login`（API 类型账号必需） | `10/min` |
| 1 | GET | `/public/companies` | 获取所有隶属公司 | `public.companies` | `60/min` |
| 2 | GET | `/public/levels` | 获取级别字典（v2.6 新增） | `public.levels` | `60/min` |
| 4 | GET | `/public/positions` | 在岗岗位数据导出，第三方自行计算用工成本（v2.6 R2 新增） | `public.positions` | `60/min` |

---

## 0. 认证（JWT，PRD §7B）

### 0.1 获取 Token

外部系统先通过登录接口换取 JWT，后续所有对外接口在请求头中携带。

| 项 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/auth/login` |
| 认证 | 无 |
| 限流 | `10/minute` / IP（`429` 超限） |
| 返回 | `200 OK` + `{access_token, token_type, username, role, expires_in}` |

**请求**

```http
POST /api/v1/auth/login HTTP/1.1
Content-Type: application/json

{"username": "admin", "password": "admin123"}
```

默认种子账号：`admin / admin123`（可通过环境变量 `DEFAULT_ADMIN_USER/PASSWORD` 覆盖，首次启动自动创建，`app/seed.py:1`）。

**响应 `200 OK`**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "username": "admin",
  "role": "admin",
  "expires_in": 43200
}
```

- `expires_in` 为秒数，默认 `720` 分钟（`JWT_EXPIRE_MINUTES`，`app/auth.py:22`）。
- `access_token` 为 HS256 JWT，payload 含 `sub`（用户名）、`role`、`exp`。
- **API 类型账号（`user_type=api`）须被授予「认证」权限**（`auth.login`，v2.4.3），否则登录返回 `403`；UI 类型账号天然可登录。

### 0.2 调用对外接口

所有对外接口需在请求头中携带（`app/auth.py:59` 三种携带方式，优先级 1>2>3）：

```
Authorization: Bearer <access_token>   # 推荐
X-Token: <access_token>               # 兼容内部 Token 头
?token=<access_token>                 # 便于 curl 调试
```

**错误码**

| 码 | 含义 | 示例 |
| --- | --- | --- |
| `401` | 未提供/过期/无效 Token、用户停用 | `{"detail":"未提供认证 Token（请在 Authorization: Bearer <token> 头中携带）"}` |
| `429` | 限流（全局 `120/min`，登录 `10/min`，公共 `60/min`，`app/limiter.py:1`/`main.py:1`） | `{"detail":"请求过于频繁，请稍后重试（10 per 1 minute）"}` |

**相关接口**

| 方法 | 路径 | 说明 | 认证 |
| --- | --- | --- | --- |
| `GET` | `/auth/me` | 当前用户信息（含账号类型） | JWT |

> 建号与用户管理为**内部管理接口**（`/admin/users*`，仅 UI admin），不对外部开放；
> 遗留的 `/auth/register`、`/users`、`/auth/register-first` 已移除（v2.4.3）。
>
> 密码存储：`bcrypt` 哈希（`app/auth.py:29`），明文永不落库。

---

## 1. 获取所有隶属公司

获取系统中全部「隶属公司」列表，供外部系统同步公司基础信息。**需 JWT**（PRD §7B 外部 API 强制）。

| 项 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/public/companies` |
| 认证 | **JWT**（`Authorization: Bearer`）+ **「获取隶属公司列表」API 权限**（v2.4.3） |
| 限流 | `60/minute` / IP |
| 返回 | JSON 数组：公司ID / 名称 / 开业日期 / 关闭日期 / 股权结构 / 状态（v1.2 扩展） |

### 请求

```http
GET /api/v1/public/companies HTTP/1.1
Authorization: Bearer <access_token>
```

`curl` 示例：

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:7273/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s http://127.0.0.1:7273/api/v1/public/companies \
  -H "Authorization: Bearer $TOKEN"
```

### 响应 `200 OK`（v1.2 全字段）

```json
[
  {
    "id": 5,
    "name": "Peeters Luxembourg S.à r.l.",
    "is_active": true,
    "opening_date": null,
    "closing_date": null,
    "shareholders": [
      {
        "id": 12,
        "internal_company_id": 1,
        "internal_company_name": "Family Asset Management SPRL",
        "external_company_id": null,
        "external_company_name": null,
        "person_name": null,
        "ownership_pct": 100.0,
        "sort_order": 0
      }
    ],
    "status": "opened"
  }
]
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 公司主键（数据库内部值） |
| name | string | 隶属公司名称（唯一） |
| is_active | boolean | 是否启用（与关闭日期联动） |
| opening_date | string\|null | 开业日期（`YYYY-MM-DD`；年份精度按 `YYYY-01-01` 存） |
| closing_date | string\|null | 关闭日期（有值视为已关闭） |
| shareholders | array | 股权结构（0..N 行，三来源互斥：内部公司 / 外部合作公司 / 自然人） |
| shareholders[].ownership_pct | number\|null | 持股比例 %（可选填） |
| status | string | `opened` / `closed` |

- 按名称升序排列；无分页。
- **权限结合（v2.4.3）**：admin 返回全部；其余账号仅返回其可管实体（数据权限过滤）。

### 错误示例

```http
HTTP/1.1 403 Forbidden
{"detail":"该账号未被授予 API 权限「获取隶属公司列表」（public.companies）"}

HTTP/1.1 401 Unauthorized
{"detail":"未提供认证 Token（请在 Authorization: Bearer <token> 头中携带）"}

HTTP/1.1 429 Too Many Requests
{"detail":"请求过于频繁，请稍后重试（60 per 1 minute）"}
```

---

## 2. 获取级别字典（v2.6 新增）

获取系统级别字典（`B6~B10b / M7~M12b`），供外部系统对齐我方级别编码做展示与映射（原「基准包推送」链路已随 v2.6 R2 整体废弃）。**需 JWT + `public.levels` 权限**。

| 项 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/public/levels` |
| 认证 | **JWT**（`Authorization: Bearer`）+ **「获取级别字典」API 权限**（`public.levels`） |
| 限流 | `60/minute` / IP |
| 返回 | JSON 数组：级别代码 / 显示名 / 是否管理岗 |

### 请求

```http
GET /api/v1/public/levels HTTP/1.1
Authorization: Bearer <access_token>
```

### 响应 `200 OK`

```json
[
  { "code": "M8a", "label": "Manager", "is_management": true },
  { "code": "B6",  "label": "Coordinator", "is_management": false }
]
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| code | string | 级别代码（唯一，作为我方级别唯一标识） |
| label | string\|null | 显示名（如 Manager / Coordinator） |
| is_management | boolean | 是否管理岗（code 以 M 开头） |

- 按 `sort_order` 升序排列；无分页。

### 错误示例

```http
HTTP/1.1 403 Forbidden
{"detail":"该账号未被授予 API 权限「获取级别字典」（public.levels）"}
```

---

## 3. 在岗岗位数据导出（v2.6 R2 新增）

> **⚠️ 第二轮修订**：原「基准包推送 + 报告拉取」链路整体废弃——**计算权移交第三方**。我方仅提供在岗岗位数据导出；第三方拉取岗位信息后配合其自有费率数据自行计算用工成本。

| 项 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/public/positions` |
| 认证 | **JWT** + **「获取在岗岗位数据」API 权限**（`public.positions`） |
| 限流 | `60/minute` / IP |
| 返回 | 指定年份的在岗岗位明细（CSV 字段对齐，**无任何成本字段**） |

### 过滤语义

- **在岗判定（公司/岗位同规则）**：`opening_date ≤ Y-12-31` 且（closing 空 或 ≥ Y-01-01）——与年份至少有一天交集即计入；不看岗位当前状态；opening 为空不计入
- **模式 A（仅传 year）**：先筛「该年在营」的公司（公司开业/关闭日期同规则；开业日未知视为在营），再取旗下在岗岗位
- **模式 B（company_ids + year）**：仅按年份过滤点名公司的在岗岗位，不做公司在营过滤

### 请求

```http
GET /api/v1/public/positions?year=2030&company_ids=3,7 HTTP/1.1
Authorization: Bearer <access_token>
```

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| year | 是 | 目标自然年（1900~2999） |
| company_ids | 否 | 逗号分隔的我方公司 ID（§1 字典）；缺省 = 全部在营公司 |

### 响应 `200 OK`

```json
{
  "year": 2030,
  "company_filter": [3, 7],
  "total": 2,
  "items": [
    {
      "number": "P11", "position_name": "Statutory Manager",
      "position_type": "Employee", "company_id": 3,
      "company_name": "Peeters Luxembourg S.à r.l.", "level": "M8a",
      "country_or_region": "Country·卢森堡",
      "opening_date": "2029-01-01", "closing_date": null,
      "work_location": "卢森堡", "job_responsibility": "…",
      "solid_line_manager": "Managing Director",
      "dotted_managers": ["Group AML Officer"],
      "legal_category": "法律强制·内部全职不可外包",
      "org_chart_display": "Statutory Manager - 卢森堡法定经理",
      "remark": null, "incumbent_name": null
    }
  ]
}
```

**字段 ↔ 导入 CSV 列映射**：

position_name↔职位 · position_type↔职位类型 · number↔岗位编号 · company_id/company_name↔隶属公司 · level↔级别 · country_or_region↔国家或地区（`Country·X` 同构） · opening_date/closing_date↔职位开启日/关闭日（月折算依据） · work_location↔工作地点 · job_responsibility↔工作职责描述 · solid_line_manager↔直线经理 · dotted_managers↔虚线经理（数组） · legal_category↔法律强制/可选 · org_chart_display↔Org-Chart中的显示 · remark↔备注。另附 `incumbent_name`（在岗员工姓名，可空）。

> **成本六栏一律不输出**——费率与计算完全由第三方掌握。月折算由第三方依据 opening_date/closing_date 与年份的自然交集自行完成。

### 错误示例

```http
HTTP/1.1 403 Forbidden
{"detail":"该账号未被授予 API 权限「获取在岗岗位数据」（public.positions）"}

HTTP/1.1 400 Bad Request
{"detail":"company_ids 须为逗号分隔的整数（如 1,2,3）"}

HTTP/1.1 422 Unprocessable Entity
{"detail":[{"loc":["query","year"],"msg":"…"}]}
```

---

## 4. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-08-04 | v1.0 | 新增 `GET /public/companies` 对外接口（无认证） |
| 2026-08-22 | v1.1 | **Breaking**: `GET /public/companies` 改为 **JWT 必需** + `60/min` 限流；新增 `§0 认证`（`POST /auth/login` 等，登录 `10/min`，`bcrypt`）；Base URL 修正为 `/api/v1`；补充 `401/429` 错误码 |
| 2026-08-24 | v1.2 | 响应扩展为**全字段**：开业/关闭日期、股权结构（三来源股东 + 持股比例）、状态；需「获取隶属公司列表」API 权限（v2.4.3 权限拆分）；非 admin 按可管实体过滤 |
| 2026-08-24 | v1.3 | **移除遗留端点**：`/auth/register`、`/users`、`/auth/register-first` 从代码与文档删除——外部仅「登录」+ 已授权对外 API，建号统一走内部 `/admin/users` |
| 2026-08-24 | v1.5 | **第二轮修订（R2）**：原 `POST /benchmarks` + `GET /benchmarks/reports/{year}` 整体废弃（计算权移交第三方）；新增 `GET /public/positions` 在岗岗位数据导出（scope=public.positions，year+可选 company_ids 过滤，CSV 字段对齐、零成本字段）；接口总览同步 |
| 2026-08-24 | v1.4 | **新增两条对外 API（v2.6）**：`§2 GET /public/levels` 级别字典（scope=public.levels）；`§3 POST /benchmarks` + `GET /benchmarks/reports/{year}` 年度用工成本预估（scope=benchmarks）；新增接口总览；说明改为含写入型接口。**（v1.5 已按 R2 废弃该链路）** |
