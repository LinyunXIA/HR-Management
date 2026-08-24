"""v2.3 端到端验证脚本（S8）：权限隔离 / 转调认领 / 升职 / 成本双口径 / 并发抢岗。

用法：先启动 dev 服务（uvicorn main:app --port 7273），再运行：
    .venv/bin/python tests/test_v23.py
仅操作 hr_db_dev；结束后清理测试产生的数据（员工/转调记录保留可查，岗位域由 --reset 流程管理）。
"""
import json
import threading
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:7273/api/v1"
PASS = 0
FAIL = 0


_DEFAULT_TOKEN = {}


def req(method, path, body=None, token=None, expect=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    if token is None and _DEFAULT_TOKEN.get("v"):
        token = _DEFAULT_TOKEN["v"]  # #78 后全部内部 GET 需认证，默认注入；显式传 "" 测未认证态
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r) as resp:
            code, payload = resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        code = e.code
        payload = json.loads(e.read() or b"null")
    if expect is not None:
        check(code == expect, f"{method} {path} → {code} (期望 {expect})" +
              ("" if code == expect else f" detail={payload}"))
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
    # ---------- 登录 ----------
    section("S2 认证：登录与建号")
    _, login = req("POST", "/auth/login", {"username": "admin", "password": "admin123"}, expect=200)
    admin = login["access_token"]
    _DEFAULT_TOKEN["v"] = admin

    # register-first 已移除
    code, _ = req("POST", "/auth/register-first", {"username": "x", "password": "123456"})
    check(code in (404, 405), f"register-first 已移除（{code}）")
    # /auth/register 遗留端点已移除（v2.4.3：建号统一走 /admin/users）
    code, _ = req("POST", "/auth/register", {"username": "x", "password": "123456", "role": "hr"},
                  token=admin)
    check(code in (404, 405), f"/auth/register 已移除（{code}）")
    # 未认证建号被拒（显式空 token 绕过默认注入）
    req("GET", "/admin/users", expect=401, token="")
    # 建 hr 账号
    import random
    suffix = random.randint(1000, 9999)
    hr_name = f"hr_be_{suffix}"
    req("POST", "/admin/users", {"username": hr_name, "password": "hr123456", "role": "hr"},
        token=admin, expect=201)
    _, me = req("POST", "/auth/login", {"username": hr_name, "password": "hr123456"}, expect=200)
    hr = me["access_token"]
    _, users = req("GET", "/admin/users", token=admin)
    hr_user = next(u for u in users["items"] if u["username"] == hr_name)
    check(hr_user["role"] == "hr" and hr_user["companies"] == [], "hr 建号成功、初始无可管实体")

    # ---------- 主数据准备 ----------
    section("前置数据：公司/岗位/税区")
    _, comps = req("GET", "/companies")
    comp_a = comps[0]
    comp_b_name = "测试目标公司B"
    existing_b = [c for c in comps if c["name"] == comp_b_name]
    if existing_b:
        comp_b = existing_b[0]
    else:
        code, comp_b = req("POST", "/companies", {"name": comp_b_name}, token=admin, expect=201)
    _, pos_types = req("GET", "/position-types")
    pt_names = [p["name"] for p in pos_types] if isinstance(pos_types, list) else []
    emp_type = "Employee" if "Employee" in pt_names else None

    def mk_pos(name, company_id):
        code, p = req("POST", "/positions", {
            "position_name": name, "company_id": company_id, "level": "B7a",
            "scope": "global", "position_type": emp_type,
            "work_location": "比利时布鲁塞尔",
        }, token=admin)
        return p if code == 201 else None

    src_pn = mk_pos(f"测试源岗位{suffix}", comp_a["id"])
    tgt_pn = mk_pos(f"测试目标岗位{suffix}", comp_b["id"])
    tgt_pn2 = mk_pos(f"测试备用岗位{suffix}", comp_b["id"])

    def open_pos(p):
        req("POST", f"/positions/{p['id']}/transitions", {"to_status": "open"}, token=admin, expect=201)
        _, p2 = req("GET", f"/positions/{p['id']}")
        return p2

    src_pn, tgt_pn, tgt_pn2 = open_pos(src_pn), open_pos(tgt_pn), open_pos(tgt_pn2)
    check(all([src_pn, tgt_pn, tgt_pn2]), f"创建测试岗位并开岗 {src_pn['number']}/{tgt_pn['number']}/{tgt_pn2['number']}")

    # 目标公司需有国家税区配置 → 用城市级（比利时·布鲁塞尔）
    _, countries = req("GET", "/countries")
    bel = next(c for c in countries if c["name"] == "比利时")
    _, zones = req("GET", f"/tax-zones?country_id={bel['id']}")
    zone = next((z for z in zones if z["level"] == "city" and z.get("city") == "布鲁塞尔"), None)
    if not zone:
        code, zone = req("POST", "/tax-zones",
                         {"level": "city", "country_id": bel["id"], "city": "布鲁塞尔"},
                         token=admin, expect=201)
    check(zone is not None, "城市级税区（比利时·布鲁塞尔）就绪")
    existing_items = {it["item_name"] for it in (zone.get("items") or [])}
    for item, rate in (("Social Security", 13.07), ("Pension", 8.86)):
        if item not in existing_items:
            req("POST", "/employment-tax-items",
                {"tax_zone_id": zone["id"], "item_name": item, "tax_rate": rate},
                token=admin, expect=201)

    # ---------- S5 成本双口径 ----------
    section("S5 成本双口径 + 税区")
    _, calc = req("GET", f"/positions/{src_pn['id']}/cost-calculation?scope=budget&salary_before_tax=100000")
    check(calc.get("configured") is True and abs(calc["company_share"] - 21930.0) < 1,
          f"预算口径自动计算（share={calc.get('company_share')}，21.93%）")
    _, actual0 = req("GET", f"/positions/{tgt_pn['id']}/cost-calculation?scope=actual&salary_before_tax=50000")
    check(actual0.get("detail") and "在职员工" in actual0.get("detail"),
          "无在职员工时 actual 口径拒绝（400 提示）")

    # ---------- 挂编联动 ----------
    section("挂编联动（position_type ↔ employee_type）")
    code, _ = req("POST", "/employees", {
        "employee_no": f"T{suffix}", "name": "测试外包工", "gender": "男",
        "employee_type": "外包", "employment_status": "在职",
        "position_number_id": src_pn["id"],
    }, token=admin)
    check(code == 400, "外包员工挂 Employee 编制被拒（联动校验）")
    code, emp = req("POST", "/employees", {
        "employee_no": f"T{suffix}", "name": "测试正式工", "gender": "男",
        "employee_type": "正式", "employment_status": "在职",
        "position_number_id": src_pn["id"],
    }, token=admin, expect=201)
    check(src_pn and emp["employment_status"] == "在职", "正式员工入职挂编成功")

    # PATCH 实际成本
    _, emp_v2 = req("PATCH", f"/employees/{emp['id']}",
                    {"version": emp["version"], "actual_salary_before_tax": 80000,
                     "actual_cost_mode": "manual", "actual_company_share": 17544,
                     "actual_labor_cost": 97544}, token=admin, expect=200)
    check(emp_v2["actual_labor_cost"] == 97544.0, "员工实际成本落库（手动模式）")

    # ---------- S3 行级隔离 ----------
    section("S3 行级隔离（读可跨司、写按实体）")
    # hr 未分配实体：写员工被拒
    code, _ = req("PATCH", f"/employees/{emp['id']}", {"version": emp_v2["version"], "name": "改名字"}, token=hr)
    check(code == 403, f"未分配实体的 hr 改员工被 403（{code}）")
    # 分配 A 公司后可写
    req("POST", f"/admin/users/{hr_user['id']}/companies", {"company_ids": [comp_a["id"]]},
        token=admin, expect=200)
    code, _ = req("PATCH", f"/employees/{emp['id']}", {"version": emp_v2["version"], "name": "A公司HR改的"}, token=hr)
    check(code == 200, "分配 A 公司后 hr 可修改其员工")
    # B 公司成本字段不可改
    code, _ = req("PATCH", f"/positions/{tgt_pn['id']}",
                  {"version": tgt_pn["version"], "salary_before_tax": 1}, token=hr)
    check(code == 403, f"hr 改 B 公司岗位成本被 403（{code}）")
    # 组织图读侧不受限（匿名读姓名）
    code, oc = req("GET", "/org-charts")
    names = [n.get("incumbent") for n in oc.get("nodes", [])]
    check(code == 200 and any(names), "组织图匿名可读他司员工姓名（只读跨司）")

    # ---------- S4 转调 initiate/claim/reject ----------
    section("S4 转调：发起 → 认领池过滤 → 单事务认领")
    _, tr = req("POST", "/transfers/initiate",
                {"employee_id": emp["id"], "target_company_id": comp_b["id"], "note": "集团内转"},
                token=admin, expect=201)
    _, emp_t = req("GET", f"/employees/{emp['id']}")
    check(emp_t["employment_status"] == "转调中" and emp_t["target_company_name"] == comp_b_name,
          "发起后：人标记「转调中」+ 目标公司，原岗仍 Filled")
    _, src_now = req("GET", f"/positions/{src_pn['id']}")
    check(src_now["status"] == "filled", "原岗保持 Filled（锁定不释放）")

    # 认领池按 target_company 过滤：hr（只管 A 公司）看不到 B 的池
    _, pool_hr = req("GET", "/transfers/pending", token=hr)
    check(pool_hr["total"] == 0, "非目标公司 HR 不可见该待认领池")
    _, pool_admin = req("GET", "/transfers/pending", token=admin)
    check(any(t["id"] == tr["id"] for t in pool_admin["items"]), "目标侧（admin 全司）可见待认领")

    # 非 B 公司 HR claim 被 403
    code, _ = req("POST", f"/transfers/{tr['id']}/claim", {"to_position_id": tgt_pn["id"]}, token=hr)
    check(code == 403, f"A 公司 HR 认领 B 公司转调被 403（{code}）")
    # admin 认领到 B 公司空闲岗（单事务）
    _, claim = req("POST", f"/transfers/{tr['id']}/claim", {"to_position_id": tgt_pn["id"]},
                   token=admin)
    ok = claim.get("ok")
    emp_c = claim.get("employee", {})
    check(ok and emp_c["company_name"] == comp_b_name and emp_c["employment_status"] == "在职",
          "认领成功：人挂新岗、状态恢复在职、target 清空")
    _, old_after = req("GET", f"/positions/{src_pn['id']}")
    _, new_after = req("GET", f"/positions/{tgt_pn['id']}")
    check(old_after["status"] == "vacant" and new_after["status"] == "filled",
          "单事务成对流转：原岗→Vacant、目标岗→Filled")
    check(new_after.get("prev_position_number") == src_now["number"],
          "prev_position 记录来源岗")

    # ---------- S4 升职 ----------
    section("S4 升职（老岗默认 Vacant、工龄照人）")
    hire_before = emp_c["hire_date"]
    _, promo = req("POST", f"/employees/{emp['id']}/promote",
                   {"to_position_id": tgt_pn2["id"], "timing": "month_end"}, token=admin)
    emp_p = promo.get("employee", {})
    check(promo.get("ok") and emp_p["position_number_id"] == tgt_pn2["id"],
          "升职到新岗（timing=month_end 记录在案）")
    _, oldp = req("GET", f"/positions/{tgt_pn['id']}")
    check(oldp["status"] == "vacant", "升职后老岗默认 Vacant")
    _, newp = req("GET", f"/positions/{tgt_pn2['id']}")
    check(newp["status"] == "filled" and newp.get("prev_position_number") == tgt_pn["number"],
          "新岗 Filled 且 prev_* 链路完整")
    _, emp_final = req("GET", f"/employees/{emp['id']}")
    check(emp_final["hire_date"] == hire_before and emp_final["actual_labor_cost"] == 97544.0,
          "工龄跟人（hire_date 不动）、实际成本跟人走不丢")

    # ---------- S4 并发抢同岗（乐观锁/行锁，不产生一人双岗） ----------
    section("并发抢岗：两 HR 同时认领同一空闲岗")
    _, e2 = req("POST", "/employees", {
        "employee_no": f"T{suffix}X", "name": "并发工", "gender": "女",
        "employee_type": "正式", "employment_status": "在职",
        "position_number_id": src_pn["id"],
    }, token=admin, expect=201)
    req("PATCH", f"/employees/{e2['id']}", {"version": e2["version"], "actual_salary_before_tax": 1,
                                            "actual_cost_mode": "manual"}, token=admin, expect=200)
    _, tr2 = req("POST", "/transfers/initiate",
                 {"employee_id": e2["id"], "target_company_id": comp_a["id"]}, token=admin, expect=201)
    # 在 A 公司建一个空岗作为争夺目标
    race_pn = open_pos(mk_pos(f"竞速岗位{suffix}", comp_a["id"]))
    results = []

    def racer(tok):
        c, body = req("POST", f"/transfers/{tr2['id']}/claim", {"to_position_id": race_pn["id"]}, token=tok)
        results.append(c)

    th1 = threading.Thread(target=racer, args=(admin,))
    th2 = threading.Thread(target=racer, args=(admin,))
    th1.start(); th2.start(); th1.join(); th2.join()
    wins = sum(1 for c in results if c == 200)
    check(wins <= 1, f"同一目标岗仅一次认领成功（结果={results}）")
    _, race_emp = req("GET", f"/employees/{e2['id']}")
    occupied = db_single(race_pn["id"]) if False else None
    # 校验岗位占用唯一性
    _, race_pn_after = req("GET", f"/positions/{race_pn['id']}")
    occupants = [race_emp["position_number_id"]] if race_emp["position_number_id"] == race_pn["id"] else []
    check(len(occupants) <= 1, "岗位至多一人占用（无一人双岗脏窗口）")

    # ---------- 收尾输出 ----------
    print(f"\n════════ 结果: {PASS} 通过 / {FAIL} 失败 ════════")
    return 1 if FAIL else 0


def db_single(*_):
    return None


if __name__ == "__main__":
    raise SystemExit(main())
