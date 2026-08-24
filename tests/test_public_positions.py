"""v2.6 第二轮修订测试：GET /public/positions 岗位数据导出（第三方计算，不含成本）
+ R1 公司绑税区。

用法：先启动 dev 服务（APP_ENV=dev uvicorn main:app --port 7273），再运行：
    .venv/bin/python tests/test_public_positions.py

覆盖：
- A 鉴权矩阵：无 token 401 / UI 用户 403 / API 用户按 public.positions scope 放行
- B 模式 A（仅 year）：公司存续过滤（年中歇业计入/往年歇业排除/未开业排除）
    + 岗位日期交集（年中关闭计入/往年关闭排除/未来开启排除）
- C 模式 B（company_ids）：跳过公司存续过滤（已歇业公司的未关闭岗位仍返回）
- D 字段契约：CSV 对齐字段齐全 + **无任何成本字段键**
- E R1 公司绑税区：绑定回显 / 被绑定税区禁止删除 / 解绑后可删
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
YEAR = 2030

COST_KEYS = {"salary_before_tax", "mandatory_tax", "mandatory_fixed_fee",
             "fixed_bonus", "floating_bonus", "labor_cost",
             "actual_salary_before_tax", "actual_labor_cost"}
EXPECT_KEYS = {"number", "position_name", "position_type", "company_id", "company_name",
               "level", "country_or_region", "opening_date", "closing_date",
               "work_location", "job_responsibility", "solid_line_manager",
               "dotted_managers", "legal_category", "org_chart_display", "remark"}


def req(method, path, body=None, token=None, expect=None):
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
        return e.code, json.loads(e.read() or b"null")


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {msg}")
    else:
        FAIL += 1
        print(f"  ❌ {msg}")


def section(title):
    print(f"\n━━ {title} ━━")


def main():
    section("准备")
    _, login = req("POST", "/auth/login", {"username": "admin", "password": "admin123"}, expect=200)
    admin = login["access_token"]
    _, comps = req("GET", "/companies", token=admin)
    comp_a = comps[0]
    _, countries = req("GET", "/countries", token=admin)
    lux = next(c for c in countries if c["name"] == "卢森堡")

    suffix = random.randint(10000, 99999)

    def mk_company(name, opening, closing=None):
        code, c = req("POST", "/companies", {
            "name": name, "opening_date": opening,
            **({"closing_date": closing} if closing else {}),
        }, token=admin, expect=201)
        return c

    co_alive_mid = mk_company(f"导出在营司{suffix}", "2025-01-01", f"{YEAR}-06-30")   # 2030 上半年在营
    co_dead = mk_company(f"导出歇业司{suffix}", "2025-01-01", "2029-06-30")           # 2030 前已歇业
    co_future = mk_company(f"导出未开司{suffix}", f"{YEAR + 1}-01-01")                # 未开业

    def mk_pos(name, company_id, level, opening, closing=None):
        code, p = req("POST", "/positions", {
            "position_name": f"导出岗{suffix}{name}", "company_id": company_id,
            "level": level, "scope": "country", "country_id": lux["id"],
            "work_location": "卢森堡", "opening_date": opening,
            **({"closing_date": closing} if closing else {}),
        }, token=admin, expect=201)
        return p

    f_full = mk_pos("全年", comp_a["id"], "M8a", f"{YEAR - 1}-01-01")
    f_midopen = mk_pos("年中开", comp_a["id"], "B6", f"{YEAR}-07-01")
    f_midclose = mk_pos("年中关", comp_a["id"], "B7a", f"{YEAR - 1}-01-05", f"{YEAR}-04-15")
    f_preclosed = mk_pos("往年关", comp_a["id"], "B8a", f"{YEAR - 2}-01-01", f"{YEAR - 1}-11-30")
    f_future = mk_pos("未来开", comp_a["id"], "B8b", f"{YEAR + 1}-03-01")
    f_dead_co = mk_pos("歇业司岗", co_dead["id"], "B7a", "2026-01-01")
    f_alive_mid_co = mk_pos("年中歇司岗", co_alive_mid["id"], "B7a", "2026-01-01")

    def numbers(items):
        return {d["number"] for d in items}

    # 把历史残留岗位移出测试窗口（closing 到 2027），避免污染模式断言
    _, plist = req("GET", "/positions?page_size=500", token=admin)
    for p in plist["items"]:
        if p["number"] in numbers([]) | {f_full["number"]}:
            continue
        known = {x["number"] for x in (f_full, f_midopen, f_midclose, f_preclosed,
                                       f_future, f_dead_co, f_alive_mid_co)}
        if p["number"] in known:
            continue
        o = p.get("opening_date")
        if (o is None) or (p.get("closing_date") is None and o <= f"{YEAR}-12-31"):
            c, fresh_ok = req("PATCH", f"/positions/{p['id']}",
                              {"version": p["version"], "closing_date": "2027-06-30"}, token=admin)
            if c != 200:
                _, fresh = req("GET", f"/positions/{p['id']}")
                req("PATCH", f"/positions/{p['id']}",
                    {"version": fresh.get("version", 1), "closing_date": "2027-06-30"}, token=admin)

    # ---------- A 鉴权矩阵 ----------
    section("A 鉴权矩阵")
    code, _b = req("GET", f"/public/positions?year={YEAR}")
    check(code == 401, f"无 token → 401（{code}）")
    ui_name = f"ui_pp_{suffix}"
    req("POST", "/admin/users", {"username": ui_name, "password": "ui123456", "role": "hr"},
        token=admin, expect=201)
    _, r = req("POST", "/auth/login", {"username": ui_name, "password": "ui123456"}, expect=200)
    code, _b = req("GET", f"/public/positions?year={YEAR}", token=r["access_token"])
    check(code == 403, f"UI 用户无 API 权限 → 403（{code}）")

    api_name = f"api_pp_{suffix}"
    req("POST", "/admin/users", {"username": api_name, "password": "api123456",
                                 "role": "hr", "user_type": "api"}, token=admin, expect=201)
    _, users = req("GET", "/admin/users", token=admin)
    api_user = next(u for u in users["items"] if u["username"] == api_name)
    req("PUT", f"/admin/users/{api_user['id']}/apis", {"apis": ["auth.login"]},
        token=admin, expect=200)
    _, r = req("POST", "/auth/login", {"username": api_name, "password": "api123456"}, expect=200)
    api_tok = r["access_token"]
    code, _b = req("GET", f"/public/positions?year={YEAR}", token=api_tok)
    check(code == 403, f"API 用户未授予 public.positions → 403（{code}）")
    req("PUT", f"/admin/users/{api_user['id']}/apis",
        {"apis": ["auth.login", "public.positions"]}, token=admin, expect=200)

    # ---------- B/C 过滤语义 ----------
    section("B 模式 A：仅 year（公司存续 + 岗位交集）")
    code, body = req("GET", f"/public/positions?year={YEAR}", token=api_tok, expect=200)
    nums = numbers(body["items"])
    check(f_full["number"] in nums, "全年岗位计入")
    check(f_midopen["number"] in nums, "年中开启计入")
    check(f_midclose["number"] in nums, "年中关闭计入（年内有日子）")
    check(f_preclosed["number"] not in nums, "往年关闭排除")
    check(f_future["number"] not in nums, "未来开启排除")
    check(f_alive_mid_co["number"] in nums, "年中歇业公司的在岗岗位计入")
    check(f_dead_co["number"] not in nums, "已歇业公司的岗位排除（模式 A 公司层过滤）")
    check(body["company_filter"] is None, "模式 A 无 company_filter 回显")

    section("C 模式 B：company_ids 指定（跳过公司存续过滤）")
    code, body_b = req("GET",
                       f"/public/positions?year={YEAR}&company_ids={co_dead['id']}",
                       token=api_tok, expect=200)
    nums_b = numbers(body_b["items"])
    check(f_dead_co["number"] in nums_b,
          "已歇业公司被点名 → 其未关闭岗位仍返回（不做公司层过滤）")
    check(all(d["company_id"] == co_dead["id"] for d in body_b["items"]),
          "模式 B 仅含点名公司的岗位")
    code, body_multi = req("GET",
                           f"/public/positions?year={YEAR}&company_ids={comp_a['id']},{co_dead['id']}",
                           token=api_tok, expect=200)
    check(f_full["number"] in numbers(body_multi["items"])
          and f_dead_co["number"] in numbers(body_multi["items"]),
          "company_ids 数组多公司生效")

    # ---------- D 字段契约 ----------
    section("D 字段契约（CSV 对齐，无成本字段）")
    sample = next(d for d in body["items"] if d["number"] == f_full["number"])
    missing_keys = EXPECT_KEYS - set(sample.keys())
    check(not missing_keys, f"业务字段齐全（缺：{missing_keys or '无'}）")
    leaked = COST_KEYS & set(sample.keys())
    check(not leaked, f"零成本字段泄漏（泄漏：{leaked or '无'}）")
    check(sample["country_or_region"] == "Country·卢森堡", f"国家或地区 CSV 同构（{sample['country_or_region']}）")
    check(sample["opening_date"] == f"{YEAR - 1}-01-01", "开启日 ISO 输出")
    code, e400 = req("GET", "/public/positions?year=abc", token=api_tok)
    check(code == 422, f"非法 year → 422（{code}）")
    code, _b = req("GET", f"/public/positions?year={YEAR}&company_ids=a,b", token=api_tok)
    check(code == 400, f"非法 company_ids → 400（{code}）")

    # ---------- E R1 公司绑税区 ----------
    section("E R1 公司绑税区")
    code, zone = req("POST", "/tax-zones",
                     {"level": "country", "country_id": lux["id"]}, token=admin, expect=201)
    code, patched = req("PATCH", f"/companies/{comp_a['id']}",
                        {"tax_zone_id": zone["id"]}, token=admin, expect=200)
    check(patched.get("tax_zone_id") == zone["id"]
          and "卢森堡" in (patched.get("tax_zone_label") or ""),
          f"绑定回显：{patched.get('tax_zone_label')}")
    code, body = req("DELETE", f"/tax-zones/{zone['id']}", token=admin)
    check(code == 400, f"被公司绑定的税区禁止删除（{code}）")
    req("PATCH", f"/companies/{comp_a['id']}", {"tax_zone_id": None}, token=admin, expect=200)
    code, _b = req("DELETE", f"/tax-zones/{zone['id']}", token=admin, expect=200)
    check(True, "解绑后税区可删除")

    code, scopes = req("GET", "/admin/scopes", token=admin)
    keys = [s["key"] for s in scopes]
    check("public.positions" in keys and "benchmarks" not in keys,
          f"/admin/scopes 注册表换血完成（{keys}）")

    print(f"\n════════ 结果: {PASS} 通过 / {FAIL} 失败 ════════")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
