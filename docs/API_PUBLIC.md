# 对外接口文档（Public API）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 更新日期 | 2026-08-04 |
| Base URL | `http://127.0.0.1:8000/api` |
| 说明 | 面向外部系统/第三方调用的**只读对外接口**，无认证 |

> 本文件单独收录对外暴露的接口；系统内部管理接口见 [API.md](./API.md)。

---

## 1. 获取所有隶属公司

获取系统中全部「隶属公司」列表，供外部系统同步公司基础信息。

| 项 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/public/companies` |
| 认证 | 无 |
| 返回 | JSON 数组，仅含 `id` 与 `name` |

### 请求

```http
GET /api/public/companies
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

---

## 2. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-08-04 | v1.0 | 新增 `GET /public/companies` 对外接口 |
