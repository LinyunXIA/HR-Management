# 对外接口文档（Public API）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.4 |
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
| 3 | POST | `/benchmarks` | 推送年度用工成本基准包（v2.6 新增） | `benchmarks` | 全局 `120/min` |
| 3 | GET | `/benchmarks/reports/{year}` | 拉取年度用工成本预估报告（v2.6 新增） | `benchmarks` | 全局 `120/min` |

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

获取系统级别字典（`B6~B10b / M7~M12b`），供外部系统按**我方 level code** 下发用工成本基准。**需 JWT + `public.levels` 权限**。

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
| code | string | 级别代码（唯一，推送 `/benchmarks` 时 `level` 字段必须取自此列表） |
| label | string\|null | 显示名（如 Manager / Coordinator） |
| is_management | boolean | 是否管理岗（code 以 M 开头） |

- 按 `sort_order` 升序排列；无分页。

### 错误示例

```http
HTTP/1.1 403 Forbidden
{"detail":"该账号未被授予 API 权限「获取级别字典」（public.levels）"}
```

---

## 3. 年度用工成本预估（v2.6 新增）

外部系统按**我方定义的 schema** 推送「年度用工成本基准包」，本系统校验落库后对当年在岗岗位计算每公司年度用工成本；报告存库，由外部拉取。

- **匹配键**：`year + company_id + level code + country_id + work_location`——全部使用我方 ID/字典值，任何维度不回退、缺即报错。
- **字典准备**：公司 ID 取自 §1 `GET /public/companies`；级别 code 取自 §2 `GET /public/levels`。
- **替换语义**：同 `year` 以最后一次成功提交为准（整年快照 replace）；推送后若我方岗位设置变化，需重新推送刷新报告。

### 3.1 推送基准包

| 项 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/benchmarks` |
| 认证 | **JWT** + **「用工成本基准」API 权限**（`benchmarks`） |
| 限流 | 全局 `120/minute` / IP |
| 返回 | 校验通过 → `202 Accepted`（异步生成报告）；任一校验不过 → `400` 整批拒收 |

**请求体**

```json
{
  "year": 2030,
  "items": [
    {
      "company_id": 3,
      "level": "M8a",
      "country_id": 5,
      "work_location": "卢森堡",
      "salary_before_tax": 120000,
      "tax_rate": 27.07,
      "mandatory_fixed_fee": 500
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| year | integer | 是 | 目标自然年（1900~2999） |
| items[].company_id | integer | 是 | 我方隶属公司 ID（§1 字典） |
| items[].level | string | 是 | 我方级别代码（§2 字典） |
| items[].country_id | integer | 是 | 我方国家/地区 ID |
| items[].work_location | string | 是 | 我方工作地点名称（与岗位同名字段精确匹配） |
| items[].salary_before_tax | number | 是 | 税前年薪，≥ 0 |
| items[].tax_rate | number | 否 | 强制税率 %（社保/保险等合计），0~100，默认 0 |
| items[].mandatory_fixed_fee | number | 否 | 强制定额扣费（年度金额），≥ 0，默认 0 |

**校验链（同步、整批原子——任一阶段失败整批拒收，不落库）**

| 阶段 | 内容 |
| --- | --- |
| L1 格式 | 类型 / 非负 / year 合法（pydantic，`422`） |
| L2 引用 | company_id / level / country_id / work_location 必须存在于我方字典 |
| L3 查重 | 包内匹配键不得重复 |
| L4 覆盖 | 该年在岗岗位（按日期判定）逐一匹配本包，**缺一即拒收**并列出缺失清单 |

**响应 `202 Accepted`**

```json
{
  "status": "accepted",
  "year": 2030,
  "items": 12,
  "report_status": "computing",
  "coverage": { "positions": 16, "matched": 16 }
}
```

**响应 `400 Bad Request`（示例：L4 覆盖缺失）**

```json
{
  "detail": {
    "stage": "reference|duplicate|coverage",
    "errors": [
      { "message": "1/16 个在岗岗位未命中基准行，整批拒收",
        "missing": [ { "position_id": 7, "number": "P4",
                       "company_name": "…", "reason": "无对应基准行（该组合未推送）" } ] }
    ]
  }
}
```

> L2/L3 阶段的 `errors` 为逐行数组：`[{"index": 0, "reason": "level='XX9' 不在级别字典"}]`。

### 3.2 拉取预估报告

| 项 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/benchmarks/reports/{year}` |
| 认证 | **JWT** + **「用工成本基准」API 权限**（`benchmarks`） |
| 限流 | 全局 `120/minute` / IP |
| 返回 | `ready` 完整 JSON / `pending` 计算中 / `failed` 失败；从未推送该年 → `404` |

**响应 `200 OK`（status=ready）**

```json
{
  "year": 2030,
  "status": "ready",
  "generated_at": "2030-01-01T00:00:00+00:00",
  "error_count": 0,
  "report": {
    "totals": { "benchmark_rows": 12, "matched_positions": 16, "unmatched_positions": 0 },
    "companies": [
      {
        "company_id": 3,
        "company_name": "Peeters Luxembourg S.à r.l.",
        "annual_labor_cost": 991936.0,
        "positions": [
          {
            "number": "P11", "position_name": "Statutory Manager", "level": "M8a",
            "work_location": "卢森堡",
            "months_factor": 1.0,
            "salary_before_tax": 120000, "tax_rate_pct": 27.07,
            "mandatory_tax_amount": 32484, "mandatory_fixed_fee": 500,
            "bonus": 3000,
            "unit_annual_cost": 155984, "annual_labor_cost": 155984
          }
        ],
        "unmatched_count": 0
      }
    ],
    "unmatched": []
  }
}
```

计算规则：

| 规则 | 说明 |
| --- | --- |
| 在岗判定 | `opening_date ≤ Y-12-31` 且（closing 空 或 ≥ Y-01-01）；不看岗位当前状态；opening 为空不计入 |
| 月折算 | `months_factor` = 年内自然月数(含首尾) / 12（年中开启/关闭自动截断） |
| 单岗位公式 | `(税前 + 税前×税率% + 定额扣费 + 固定奖金 + 浮动奖金) × months_factor`（奖金取自我方岗位数据） |
| 适用范围 | 正式 / 外包 / 顾问岗统一公式，不特判 |
| 时效性 | 报告反映最近一次成功推送时刻的系统状态；此后岗位变化需重新推送 |

其他状态：

```json
{ "year": 2030, "status": "pending" }                          // 计算中，稍后重试
{ "year": 2030, "status": "failed", "error": "…" }             // 生成失败（如该年无基准）
HTTP/1.1 404 Not Found {"detail":"2030 年无基准数据"}           // 该年从未成功推送
```

### 错误示例

```http
HTTP/1.1 403 Forbidden
{"detail":"该账号未被授予 API 权限「用工成本基准推送与预估报告获取」（benchmarks）"}

HTTP/1.1 422 Unprocessable Entity
{"detail":[{"loc":["body","items",0,"salary_before_tax"],"msg":"Input should be greater than or equal to 0"}]}
```

---

## 4. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-08-04 | v1.0 | 新增 `GET /public/companies` 对外接口（无认证） |
| 2026-08-22 | v1.1 | **Breaking**: `GET /public/companies` 改为 **JWT 必需** + `60/min` 限流；新增 `§0 认证`（`POST /auth/login` 等，登录 `10/min`，`bcrypt`）；Base URL 修正为 `/api/v1`；补充 `401/429` 错误码 |
| 2026-08-24 | v1.2 | 响应扩展为**全字段**：开业/关闭日期、股权结构（三来源股东 + 持股比例）、状态；需「获取隶属公司列表」API 权限（v2.4.3 权限拆分）；非 admin 按可管实体过滤 |
| 2026-08-24 | v1.3 | **移除遗留端点**：`/auth/register`、`/users`、`/auth/register-first` 从代码与文档删除——外部仅「登录」+ 已授权对外 API，建号统一走内部 `/admin/users` |
| 2026-08-24 | v1.4 | **新增两条对外 API（v2.6）**：`§2 GET /public/levels` 级别字典（scope=public.levels）；`§3 POST /benchmarks` + `GET /benchmarks/reports/{year}` 年度用工成本预估（scope=benchmarks，L1~L4 校验链 / 整年快照 replace / 异步报告）；新增接口总览；说明改为含写入型接口 |
