# 对外接口文档（Public API）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.1 |
| 更新日期 | 2026-08-22 |
| Base URL | `http://127.0.0.1:8000/api/v1`（端口随 `uvicorn --port` 可变，dev 默认 `7273`） |
| 说明 | 面向外部系统/第三方调用的**只读对外接口**，**需 JWT 认证**（PRD §7B），限流保护 |

> 本文件单独收录对外暴露的接口；系统内部管理接口见 [API.md](./API.md)。

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
| `GET` | `/auth/me` | 当前用户信息 | JWT |
| `GET` | `/users` | 用户列表（仅 admin） | JWT |
| `POST` | `/auth/register` | 注册新用户（需 admin） | JWT |
| `POST` | `/auth/register-first` | 首个用户免认证注册（系统空库时） | 无（`5/min`） |

> 密码存储：`bcrypt` 哈希（`app/auth.py:29`），明文永不落库。

---

## 1. 获取所有隶属公司

获取系统中全部「隶属公司」列表，供外部系统同步公司基础信息。**需 JWT**（PRD §7B 外部 API 强制）。

| 项 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/public/companies` |
| 认证 | **JWT**（`Authorization: Bearer`） |
| 限流 | `60/minute` / IP |
| 返回 | JSON 数组，仅含 `id` 与 `name` |

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

### 响应 `200 OK`

```json
[
  { "id": 1,  "name": "Peeters Capital Holding SPRL" },
  { "id": 12, "name": "Peeters Americas Holdings Inc." },
  { "id": 18, "name": "Family Asset Management SPRL" }
]
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 公司主键 |
| name | string | 隶属公司名称（唯一） |

- 返回全部公司，按名称升序排列，数量随主数据增减变化。
- 无分页；无其他字段（不返回岗位数等统计）。
- 实现：`app/routers/master_data.py:144`，`app/auth.py:get_current_user` 强制校验。

### 错误示例

```http
HTTP/1.1 401 Unauthorized
{"detail":"未提供认证 Token（请在 Authorization: Bearer <token> 头中携带）"}

HTTP/1.1 429 Too Many Requests
{"detail":"请求过于频繁，请稍后重试（60 per 1 minute）"}
```

---

## 2. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-08-04 | v1.0 | 新增 `GET /public/companies` 对外接口（无认证） |
| 2026-08-22 | v1.1 | **Breaking**: `GET /public/companies` 改为 **JWT 必需** + `60/min` 限流；新增 `§0 认证`（`POST /auth/login` 等，登录 `10/min`，`bcrypt`）；Base URL 修正为 `/api/v1`；补充 `401/429` 错误码 |
