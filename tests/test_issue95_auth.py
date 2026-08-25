"""Issue #95 验证：主数据写端点与清洗作业导入补 require_admin 鉴权（PRD §7B.1/§7B.3）。

用法：先启动 dev 服务（APP_ENV=dev uvicorn main:app --port 7273），再运行：
    .venv/bin/python tests/test_issue95_auth.py

覆盖：
- A 匿名写请求一律 401：_crud 字典 / companies / external-companies / 清洗作业导入 / imports
- B admin 写操作正常（国家 建→改→删 冒烟，自清理）
- C hr（user_type=ui）只读放行、写操作 403；对照：tax-zones 写原本即 admin-only
"""
import json
import os
import random
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:7273/api/v1"
PASS = 0
FAIL = 0


def req(method, path, body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {msg}")
    else:
        FAIL += 1
        print(f"  [FAIL] {msg}")


def section(title):
    print(f"\n== {title} ==")


def main():
    section("准备")
    _, login = req("POST", "/auth/login", {"username": "admin", "password": "admin123"})
    check(login and "access_token" in login, "admin 登录")
    admin = login["access_token"]
    _, comps = req("GET", "/companies", token=admin)
    any_company_id = comps[0]["id"] if comps else 1

    suffix = random.randint(10000, 99999)
    hr_name = f"audit95_hr_{suffix}"
    code, hr_user = req("POST", "/admin/users", {
        "username": hr_name, "password": "audit95pwd", "role": "hr",
        "user_type": "ui", "company_ids": [any_company_id],
    }, token=admin)
    check(code == 201, f"建临时 hr 账号 {hr_name}（{code}）")
    _, hlogin = req("POST", "/auth/ui-login", {"username": hr_name, "password": "audit95pwd"})
    check(hlogin and "access_token" in hlogin, "hr ui-login 换取 JWT")
    hr = hlogin["access_token"]

    section("A 匿名写请求 → 401")
    code, _ = req("POST", "/countries", {"name": "anon国", "code": f"A{suffix}"})
    check(code == 401, f"POST /countries 匿名 401（实际 {code}）")
    code, _ = req("PATCH", "/countries/1", {"name": "x"})
    check(code == 401, f"PATCH /countries/1 匿名 401（实际 {code}）")
    code, _ = req("DELETE", "/countries/99999999")
    check(code == 401, f"DELETE /countries/99999999 匿名 401（实际 {code}）")
    code, _ = req("POST", "/companies", {"name": "anon-co"})
    check(code == 401, f"POST /companies 匿名 401（实际 {code}）")
    code, _ = req("PATCH", f"/companies/{any_company_id}", {})
    check(code == 401, f"PATCH /companies/{{id}} 匿名 401（实际 {code}）")
    code, _ = req("DELETE", f"/companies/{any_company_id}")
    check(code == 401, f"DELETE /companies/{{id}} 匿名 401（实际 {code}）")
    code, _ = req("POST", "/external-companies", {"name": "anon-ext"})
    check(code == 401, f"POST /external-companies 匿名 401（实际 {code}）")
    code, _ = req("PATCH", "/external-companies/99999999", {})
    check(code == 401, f"PATCH /external-companies 匿名 401（实际 {code}）")
    code, _ = req("POST", "/data-clean-jobs/nonexistent/imports")
    check(code == 401, f"POST /data-clean-jobs/xx/imports 匿名 401 先于 404（实际 {code}）")
    code, _ = req("POST", "/tax-zones", {"level": "country", "country_id": 1})
    check(code == 401, f"对照 POST /tax-zones 匿名 401（原有行为，实际 {code}）")

    section("B admin 写操作正常 + 自清理")
    cname = f"审核95临时国{suffix}"
    code, c = req("POST", "/countries", {"name": cname, "code": f"T{suffix}"}, token=admin)
    check(code == 201, f"admin POST /countries 201（实际 {code}）")
    cid = (c or {}).get("id")
    if code == 201 and cid:
        code, _ = req("PATCH", f"/countries/{cid}", {"label": None, "sort_order": 99}, token=admin)
        check(code == 200, f"admin PATCH /countries/{cid} 200（实际 {code}）")
        code, _ = req("DELETE", f"/countries/{cid}", token=admin)
        check(code == 200, f"admin DELETE /countries/{cid} 自清理（实际 {code}）")

    section("C hr 只读放行 / 写拒绝 403")
    code, _ = req("GET", "/countries", token=hr)
    check(code == 200, f"hr GET /countries 只读 200（实际 {code}）")
    code, _ = req("POST", "/countries", {"name": "hr国", "code": f"H{suffix}"}, token=hr)
    check(code == 403, f"hr POST /countries 403（实际 {code}）")
    code, _ = req("POST", "/companies", {"name": "hr-co"}, token=hr)
    check(code == 403, f"hr POST /companies 403（实际 {code}）")
    code, _ = req("DELETE", f"/companies/{any_company_id}", token=hr)
    check(code == 403, f"hr DELETE /companies/{{id}} 403（实际 {code}）")
    code, _ = req("POST", "/data-clean-jobs/nonexistent/imports", token=hr)
    check(code == 403, f"hr 清洗导入 403 先于 404（实际 {code}）")

    section("清理")
    if hr_user and hr_user.get("id"):
        code, _ = req("PATCH", f"/admin/users/{hr_user['id']}/active",
                      {"is_active": False}, token=admin)
        check(code == 200, f"停用临时 hr 账号 id={hr_user['id']}（{code}，保留留痕不物理删除）")

    print(f"\n结果：PASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
