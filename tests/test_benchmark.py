"""v2.6 外部用工成本基准对接测试：推送校验链（方案甲）/ 整年替换 / 报告计算。

用法：先启动 dev 服务（APP_ENV=dev uvicorn main:app --port 7273），再运行：
    .venv/bin/python tests/test_benchmark.py

覆盖：
- A 鉴权矩阵：无 token 401 / UI 用户 403 / API 用户按 scope 放行
- B 推送校验链：L2 引用 / L3 包内查重 / L4 覆盖（方案甲整批拒收）
- C 整年替换语义（最后一次提交为准）
- D 报告计算：公式 / 月折算(年中开启、closing 截断) / 公司汇总 / pending→ready
- E /public/levels 字典端点
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:7273/api/v1"
PASS = 0
FAIL = 0
YEAR = 2030  # 测试目标年


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
            code, payload = resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        code, payload = e.code, json.loads(e.read() or b"null")
    if expect is not None:
        check(code == expect, f"{method} {path} → {code} (期望 {expect})"
              + ("" if code == expect else f" detail={str(payload)[:220]}"))
    return code, payload


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
    # ---------- 准备 ----------
    section("准备：登录与字典")
    _, login = req("POST", "/auth/login", {"username": "admin", "password": "admin123"}, expect=200)
    admin = login["access_token"]
    _, comps = req("GET", "/companies", token=admin)
    comp_a = comps[0]
    _, countries = req("GET", "/countries", token=admin)
    lux = next(c for c in countries if c["name"] == "卢森堡")
    _, locs = req("GET", "/work-locations", token=admin)

    def hygienize(p):
        """把缺字段/无开启日的历史残留岗位移出测试年窗口（closing 到 2027），避免阻断 L4。"""
        if (not p.get("level")) or (p.get("country_id") is None) or (not p.get("work_location")) \
                or (p.get("opening_date") is None) or p["opening_date"] > f"{YEAR}-12-31":
            return True
        return False

    _, plist = req("GET", "/positions?page_size=500", token=admin)
    for p in plist["items"]:
        if hygienize(p):
            c, _b = req("PATCH", f"/positions/{p['id']}",
                        {"version": p["version"], "closing_date": "2027-06-30"}, token=admin)
            if c != 200:
                # 可能已被并发改过版本：重取一次
                _, fresh = req("GET", f"/positions/{p['id']}")
                req("PATCH", f"/positions/{p['id']}",
                    {"version": fresh["version"], "closing_date": "2027-06-30"}, token=admin)

    # 基准夹具岗位（全字段齐备，均在 2030 在岗）
    import random
    suffix = random.randint(10000, 99999)
    fixtures = {}

    def mk(name, level, opening, closing=None):
        code, p = req("POST", "/positions", {
            "position_name": f"基准测试岗{suffix}{name}", "company_id": comp_a["id"],
            "level": level, "scope": "country", "country_id": lux["id"],
            "work_location": "卢森堡", "opening_date": opening,
            **({"closing_date": closing} if closing else {}),
        }, token=admin, expect=201)
        fixtures[name] = p
        return p

    mk("全年", "M8a", f"{YEAR}-01-01")
    mk("年中开", "B6", f"{YEAR}-07-01")
    mk("截断关", "B7a", f"{YEAR - 1}-01-05", f"{YEAR}-04-15")

    def active_positions():
        _, pl = req("GET", "/positions?page_size=500", token=admin)
        ys, ye = f"{YEAR}-01-01", f"{YEAR}-12-31"
        out = []
        for p in pl["items"]:
            o, c = p.get("opening_date"), p.get("closing_date")
            if o and o <= ye and (c is None or c >= ys):
                out.append(p)
        return out

    def full_package(salary=60000.0, rate=0.0, fee=0.0, overrides=None):
        """为当前全部在岗岗位构造完整覆盖包；overrides 按岗位号补丁（与遍历序无关）。"""
        pos_list = active_positions()
        items: dict = {}
        num2key: dict = {}
        for p in pos_list:
            k = (p["company_id"], p["level"], p["country_id"], p["work_location"])
            num2key[p["number"]] = k
            if k not in items:
                items[k] = {"company_id": p["company_id"], "level": p["level"],
                            "country_id": p["country_id"], "work_location": p["work_location"],
                            "salary_before_tax": salary, "tax_rate": rate,
                            "mandatory_fixed_fee": fee}
        for number, ov in (overrides or {}).items():
            items[num2key[number]].update(ov)
        return {"year": YEAR, "items": list(items.values())}

    # ---------- A 鉴权矩阵 ----------
    section("A 鉴权矩阵")
    code, _b = req("POST", "/benchmarks", full_package())
    check(code == 401, f"无 token 推送 → 401（{code}）")
    ui_name = f"ui_bk_{suffix}"
    req("POST", "/admin/users", {"username": ui_name, "password": "ui123456", "role": "hr"},
        token=admin, expect=201)
    _, r = req("POST", "/auth/login", {"username": ui_name, "password": "ui123456"}, expect=200)
    code, _b = req("POST", "/benchmarks", full_package(), token=r["access_token"])
    check(code == 403, f"UI 用户（无任何 API 权限）推送 → 403（{code}）")

    api_name = f"api_bk_{suffix}"
    req("POST", "/admin/users", {"username": api_name, "password": "api123456",
                                 "role": "hr", "user_type": "api"}, token=admin, expect=201)
    # 先只授予「认证」：能登录换取 JWT，但推基准仍应 403
    _, users = req("GET", "/admin/users", token=admin)
    api_user = next(u for u in users["items"] if u["username"] == api_name)
    req("PUT", f"/admin/users/{api_user['id']}/apis", {"apis": ["auth.login"]},
        token=admin, expect=200)
    _, r = req("POST", "/auth/login", {"username": api_name, "password": "api123456"}, expect=200)
    api_tok = r["access_token"]
    code, _b = req("POST", "/benchmarks", full_package(), token=api_tok)
    check(code == 403, f"API 用户未授予 benchmarks scope → 403（{code}）")
    req("PUT", f"/admin/users/{api_user['id']}/apis",
        {"apis": ["auth.login", "benchmarks", "public.levels"]}, token=admin, expect=200)
    code, _b = req("GET", "/public/levels", token=api_tok)
    check(code == 200, "API 用户（授 public.levels）读级别字典 → 200")
    lv_codes = [x["code"] for x in (_b if isinstance(_b, list) else [])]
    check("M8a" in lv_codes and "B6" in lv_codes, "级别字典含 M8a/B6")

    # ---------- B 推送校验链 ----------
    section("B 推送校验链（L2/L3/L4，方案甲）")
    bad = full_package()
    bad["items"][0]["level"] = "XX9"
    code, body = req("POST", "/benchmarks", bad, token=api_tok)
    stage = (body.get("detail") or {}).get("stage") if isinstance(body.get("detail"), dict) else None
    check(code == 400 and stage == "reference",
          f"L2 非法 level → 400 reference（{code}/{stage}）")

    dup = full_package()
    dup["items"].append(dict(dup["items"][0]))
    code, body = req("POST", "/benchmarks", dup, token=api_tok)
    stage = (body.get("detail") or {}).get("stage") if isinstance(body.get("detail"), dict) else None
    check(code == 400 and stage == "duplicate",
          f"L3 包内重复键 → 400 duplicate（{code}/{stage}）")

    pack = full_package()
    victim = next(p for p in active_positions() if p["number"] == fixtures["全年"]["number"])
    pack["items"] = [it for it in pack["items"]
                     if not (it["company_id"] == victim["company_id"]
                             and it["level"] == victim["level"]
                             and it["country_id"] == victim["country_id"]
                             and it["work_location"] == victim["work_location"])]
    code, body = req("POST", "/benchmarks", pack, token=api_tok)
    detail = body.get("detail") or {}
    ok_shape = (code == 400 and detail.get("stage") == "coverage"
                and any(m["number"] == victim["number"]
                        for m in detail["errors"].get("missing", [])))
    check(ok_shape, f"L4 缺一拒收 → 400 coverage 且缺失清单含 {victim['number']}（{code}）")

    # ---------- C/D 全量推送 → 替换 → 报告计算 ----------
    section("C/D 全量推送与报告计算")
    f_all = fixtures["全年"]["number"]
    overrides = {f_all: {"salary_before_tax": 120000.0, "tax_rate": 27.07,
                         "mandatory_fixed_fee": 500.0}}
    code, acc = req("POST", "/benchmarks", full_package(overrides=overrides), token=api_tok,
                    expect=202)
    check(acc.get("status") == "accepted" and acc["coverage"]["matched"] == acc["coverage"]["positions"],
          f"完整包 → 202 accepted，全覆盖 {acc.get('coverage')}")

    rep = None
    for _ in range(20):
        code, rep = req("GET", f"/benchmarks/reports/{YEAR}", token=api_tok)
        if rep.get("status") == "ready":
            break
        time.sleep(0.25)
    check(rep and rep.get("status") == "ready", "报告 pending → ready")

    rpt = rep["report"]
    ca = next(c for c in rpt["companies"] if c["company_id"] == comp_a["id"])
    det = {d["number"]: d for d in ca["positions"]}
    d_all = det[f_all]
    exp_unit = round(120000 * 1.2707 + 500, 2)
    check(abs(d_all["unit_annual_cost"] - exp_unit) < 0.01,
          f"公式：税前×(1+税率%)+定额 = {exp_unit}（实际 {d_all['unit_annual_cost']}）")
    d_mid = det[fixtures["年中开"]["number"]]
    check(abs(d_mid["months_factor"] - 0.5) < 1e-6, f"年中开启折算 0.5（{d_mid['months_factor']}）")
    d_cut = det[fixtures["截断关"]["number"]]
    check(abs(d_cut["months_factor"] - 4 / 12) < 1e-3,
          f"closing 截断 1~4 月含首尾共 4 个月 ≈0.3333（{d_cut['months_factor']}）")
    exp_total = round(sum(d["annual_labor_cost"] for d in ca["positions"]), 2)
    check(abs(ca["annual_labor_cost"] - exp_total) < 0.02,
          f"公司年度总额 = 明细求和 {exp_total}（实际 {ca['annual_labor_cost']}）")
    check(rpt["totals"]["unmatched_positions"] == 0 and rpt["unmatched"] == [],
          "完整快照下报告零缺失")

    # 整年替换：改薪重推 → 以最后一次为准
    overrides[f_all]["salary_before_tax"] = 100000.0
    req("POST", "/benchmarks", full_package(overrides=overrides), token=api_tok, expect=202)
    for _ in range(20):
        _, rep2 = req("GET", f"/benchmarks/reports/{YEAR}", token=api_tok)
        if rep2.get("status") == "ready":
            break
        time.sleep(0.25)
    ca2 = next(c for c in rep2["report"]["companies"] if c["company_id"] == comp_a["id"])
    d2 = next(d for d in ca2["positions"] if d["number"] == f_all)
    check(abs(d2["unit_annual_cost"] - round(100000 * 1.2707 + 500, 2)) < 0.01,
          f"重推替换生效：新单价 {d2['unit_annual_cost']}（期望 {round(100000*1.2707+500, 2)}）")

    # ---------- E 收尾输出 ----------
    print(f"\n════════ 结果: {PASS} 通过 / {FAIL} 失败 ════════")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
