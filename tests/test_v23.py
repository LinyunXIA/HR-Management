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

    # v2.5 登录入口拆分：API 账号禁止 UI 登录（POST /auth/ui-login 一律 403）
    api_name = f"api_be_{suffix}"
    req("POST", "/admin/users", {"username": api_name, "password": "api123456",
                                 "role": "hr", "user_type": "api"}, token=admin, expect=201)
    code, _b = req("POST", "/auth/ui-login", {"username": api_name, "password": "api123456"})
    check(code == 403, f"API 账号 UI 登录被拒（{code}，期望 403）")
    # 无「认证」授权 → 程序化登录也拒绝
    code, _b = req("POST", "/auth/login", {"username": api_name, "password": "api123456"})
    check(code == 403, f"API 账号未授予「认证」时 /auth/login 拒绝（{code}）")
    # 授予「认证」后：程序化登录放行、UI 登录仍拒绝
    _, users2 = req("GET", "/admin/users", token=admin)
    api_user = next(u for u in users2["items"] if u["username"] == api_name)
    req("PUT", f"/admin/users/{api_user['id']}/apis",
        {"apis": ["auth.login", "public.companies"]}, token=admin, expect=200)
    req("POST", "/auth/login", {"username": api_name, "password": "api123456"}, expect=200)
    code, _b = req("POST", "/auth/ui-login", {"username": api_name, "password": "api123456"})
    check(code == 403, f"持「认证」授权的 API 账号 UI 登录仍被拒（{code}）")
    # UI 类型账号经 /auth/ui-login 正常换取 JWT
    _, ui_login_r = req("POST", "/auth/ui-login", {"username": hr_name, "password": "hr123456"},
                        expect=200)
    check(bool(ui_login_r.get("access_token")), "UI 账号 /auth/ui-login 登录成功")

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
            "work_location": "比利时布鲁塞尔", "opening_date": "2026-01-01",
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
    # v2.6 R1：成本键 = 公司所绑税区 → 源岗位所属公司绑定该税区
    req("PATCH", f"/companies/{comp_a['id']}", {"tax_zone_id": zone["id"]},
        token=admin, expect=200)

    # ---------- S5 成本双口径 ----------
    section("S5 成本双口径 + 税区")
    _, calc = req("GET", f"/positions/{src_pn['id']}/cost-calculation?scope=budget&salary_before_tax=100000")
    check(calc.get("configured") is True and abs(calc["mandatory_tax"] - 21930.0) < 1,
          f"预算口径自动计算（强制扣税={calc.get('mandatory_tax')}，21.93%）")
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

    # PATCH 实际成本（v2.6 六栏）
    _, emp_v2 = req("PATCH", f"/employees/{emp['id']}",
                    {"version": emp["version"], "actual_salary_before_tax": 80000,
                     "actual_cost_mode": "manual", "actual_mandatory_tax": 17544,
                     "actual_labor_cost": 97544}, token=admin, expect=200)
    check(emp_v2["actual_labor_cost"] == 97544.0, "员工实际成本落库（手动模式，六栏）")

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
    check(wins == 1, f"同一目标岗仅一次认领成功（结果={results}）")
    # issue #151：原「无一人双岗」断言由单条员工记录构造（恒真）→ 改为
    # 岗位 incumbent 与员工实际挂岗双向核验，真正锁定占用唯一性
    _, race_pn_after = req("GET", f"/positions/{race_pn['id']}")
    check(race_pn_after.get("incumbent_id") == e2["id"],
          f"目标岗 incumbent = 胜者（{race_pn_after.get('incumbent_id')}）")
    _, race_emp = req("GET", f"/employees/{e2['id']}")
    check(race_emp["position_number_id"] == race_pn["id"],
          "认领员工挂在新岗上（岗位↔员工双向一致，无一人双岗脏窗口）")

    # ---------- 第二轮审计回归（issue #131~#152） ----------
    section("审计回归：状态机 422 / 环检测 / 管理岗 / 员工乐观锁")
    planned_pn = mk_pos(f"审计规划岗{suffix}", comp_a["id"])  # POST 创建即为 planned
    code, _b = req("POST", f"/positions/{planned_pn['id']}/transitions",
                   {"to_status": "filled"}, token=admin)
    check(code == 422, f"planned→filled 非法流转被 422（{code}）")
    _, levels_all = req("GET", "/levels")
    m_level = next((l["code"] for l in levels_all if l["is_management"]), None)

    def mk_mgr(name, cid):
        code_, p_ = req("POST", "/positions", {
            "position_name": name, "company_id": cid, "level": m_level,
            "scope": "global", "opening_date": "2026-01-01"}, token=admin)
        return p_ if code_ == 201 else None

    cyc_a, cyc_b = mk_mgr(f"环测A{suffix}", comp_a["id"]), mk_mgr(f"环测B{suffix}", comp_a["id"])
    req("PATCH", f"/positions/{cyc_b['id']}",
        {"version": cyc_b["version"], "solid_line_manager_id": cyc_a["id"]},
        token=admin, expect=200)
    code, _b = req("PATCH", f"/positions/{cyc_a['id']}",
                   {"version": cyc_a["version"], "solid_line_manager_id": cyc_b["id"]}, token=admin)
    check(code == 422, f"直线成环 A→B→A 被 422（#139 裁决，{code}）")
    _, cyc_a_now = req("GET", f"/positions/{cyc_a['id']}")
    code, _b = req("PATCH", f"/positions/{cyc_a['id']}",
                   {"version": cyc_a_now["version"], "solid_line_manager_id": src_pn["id"]}, token=admin)
    check(code == 422, f"B 级岗位任直线经理被 422（#139 裁决，{code}）")

    section("审计回归：员工乐观锁缺失/冲突 + 成本 auto/manual 服务端互斥")
    code, _b = req("PATCH", f"/employees/{emp['id']}", {"name": "缺版本"})
    check(code == 422, f"员工 PATCH 缺 version 被 422（#112，{code}）")
    code, _b = req("PATCH", f"/employees/{emp['id']}", {"name": "旧版本", "version": 999999})
    check(code == 409, f"员工 PATCH 过期 version 被 409（{code}）")
    _, tgt2_now = req("GET", f"/positions/{tgt_pn2['id']}")
    code, pa = req("PATCH", f"/positions/{tgt_pn2['id']}",
                   {"version": tgt2_now["version"], "cost_mode": "auto",
                    "salary_before_tax": 100000, "mandatory_tax": 99999,
                    "fixed_bonus": 1000, "floating_bonus": 2000}, token=admin)
    check(code == 200 and pa["cost_mode"] == "auto" and pa["mandatory_tax"] is None
          and pa["mandatory_fixed_fee"] is None and pa["labor_cost"] is None
          and pa["fixed_bonus"] == 1000,
          "manual→auto 切换清空手填派生三栏、奖金输入项保留（#141）")

    section("审计回归：虚拟建档首次挂编留痕 / promote 拦截 / 跨司脱敏贯通")
    code, vemp = req("POST", "/employees", {
        "employee_no": f"T{suffix}V", "name": "虚拟建档工", "gender": "男",
        "employee_type": "外包", "employment_status": "在职"}, token=admin)
    check(code == 201 and vemp.get("position_number_id") is None, "外包虚拟建档成功（无岗）")
    code, _b = req("POST", f"/employees/{vemp['id']}/promote",
                   {"to_position_id": race_pn["id"], "timing": "immediate"}, token=admin)
    check(code == 400, f"虚拟建档 promote 被 400 引导先挂编（#140，{code}）")
    code, pa_pos = req("POST", "/positions", {
        "position_name": f"外包岗{suffix}", "company_id": comp_a["id"], "level": "B7a",
        "scope": "global", "position_type": "External Employee",
        "opening_date": "2026-01-01"}, token=admin)
    check(code == 201 and pa_pos["number"].startswith("PA"), f"外包岗分配 PA 编号（{pa_pos.get('number')}）")
    req("POST", f"/positions/{pa_pos['id']}/transitions", {"to_status": "open"},
        token=admin, expect=201)
    code, tr3 = req("POST", "/transfers",
                    {"employee_id": vemp["id"], "to_position_id": pa_pos["id"]}, token=admin)
    check(code == 201 and tr3.get("position_number_id") == pa_pos["id"],
          "虚拟建档经 POST /transfers 首次挂编成功（#140）")
    _, trs3 = req("GET", f"/transfers?employeeId={vemp['id']}")
    rec3 = next((t for t in trs3["items"] if t.get("kind") == "transfer"), None)
    check(rec3 is not None and rec3.get("to_position_id") == pa_pos["id"],
          "首次挂编写入 kind=transfer 结构化留痕（#140/#113 同口径）")
    # hr（仅可管 A 公司）经岗位接口读 B 公司 Filled 岗位 → actual_* 置 null、姓名保留（#131）
    _, pos_b_view = req("GET", f"/positions/{tgt_pn2['id']}", token=hr)
    check(bool(pos_b_view.get("incumbent_name")), "hr 跨司可见在职员工姓名（只读例外保留）")
    check(pos_b_view.get("actual_salary_before_tax") is None
          and pos_b_view.get("actual_labor_cost") is None,
          "hr 经岗位接口不可见他司实际成本六栏（#131 serialize_position 脱敏贯通）")
    code, _b = req("GET", f"/employees/{emp['id']}/cost-calculation?salary_before_tax=50000", token=hr)
    check(code == 403, f"hr 测算他司员工实际成本被 403（#132 第二旁路封堵，{code}）")
    _, pos_admin_view = req("GET", f"/positions/{tgt_pn2['id']}", token=admin)
    check(pos_admin_view.get("actual_salary_before_tax") is not None,
          "admin 岗位接口实际成本明文不受影响（对照）")

    section("审计回归：CSV 导入契约（关开日/锚前移/拒绝集/scope 归一化/职能改名/item_kind）")

    def upload_csv(csv_text):
        boundary = "----auditb" + suffix.__str__()
        payload = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audit.csv\"\r\n"
            f"Content-Type: text/csv\r\n\r\n{csv_text}\r\n--{boundary}--\r\n").encode()
        rq = urllib.request.Request(BASE + "/imports", data=payload, method="POST")
        rq.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        rq.add_header("Authorization", f"Bearer {admin}")
        try:
            with urllib.request.urlopen(rq) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    HDRS = ("职位,职位类型,岗位编号,隶属公司,级别,国家或地区,职位开启日,职位关闭日,"
            "工作地点,工作职责描述,直线经理,虚线经理,法律强制/可选,Org-Chart中的显示,"
            "之前的职位,之前的公司,备注,岗位ID\n")

    def csv_row(name, opening, *, company=f"审计公司{{s}}", closing="", wl="",
                solid="", pid=""):
        """按列序安全构造 CSV 行（避免手工逗号错位：法律分类留空以过 strict）。"""
        cols = [name, "Employee", "", company.format(s=suffix), "B7a", "Global",
                opening, closing, wl, "", solid, "", "", "N/A", "N/A", "", "", pid]
        return ",".join(cols) + "\n"

    code, rep1 = upload_csv(HDRS + csv_row(f"审计锚基岗{suffix}", "2031-01-01", wl="比利时布鲁塞尔"))
    ok1 = code == 201 and rep1["imported"] == 1 and not rep1["errors"]
    check(ok1 and not rep1["warnings"],
          f"CSV 基线行导入成功且零 warning（#148 岗位ID 缺列不再误告警，warnings={rep1['warnings']}）")
    import urllib.parse as _up
    _, found = req("GET", f"/positions?search={_up.quote(f'审计锚基岗{suffix}')}")
    base_pn = next(p for p in found["items"] if p["position_name"] == f"审计锚基岗{suffix}")

    _, rep2 = upload_csv(HDRS + csv_row(f"审计关开日岗{suffix}", "2026-01-01", closing="2025-01-01"))
    check(any("早于" in e for e in rep2["errors"]),
          f"关闭日早于开启日报错该行不导入（#138，errors={rep2['errors']}）")

    mismatch = csv_row(f"审计锚基岗{suffix}", "2032-01-01", pid=str(base_pn["id"]))
    _, rep3 = upload_csv(HDRS + mismatch)
    check(any("识别锚" in e for e in rep3["errors"]),
          f"带 ID 锚不一致在第 2 趟即报错不落库（#137，errors={rep3['errors']}）")
    _, base_after = req("GET", f"/positions/{base_pn['id']}")
    check(base_after["opening_date"].startswith("2031"),
          "锚不一致行的库内开启日未被覆盖（#137 防护生效）")

    ghost = csv_row(f"审计幽灵岗{suffix}", "2031-05-01",
                    solid=f"审计锚基岗{suffix}", pid="99999999")
    _, rep4 = upload_csv(HDRS + ghost)
    _, base_mgr = req("GET", f"/positions/{base_pn['id']}")
    check(any("库内不存在" in e for e in rep4["errors"])
          and base_mgr["solid_line_manager_id"] is None,
          f"带 ID 不存在行的直线经理未写到任何岗位（#133 拒绝集贯通第 3 趟）")

    scope_row1 = csv_row(f"审计范围岗{suffix}", "2030-01-01").replace(",Global,", ",family,")
    upload_csv(HDRS + scope_row1)  # 首导：warning + 按 Global 入库
    _, rep5 = upload_csv(HDRS + scope_row1)  # 再导同文件：应按归一化键认老
    check(any("不可识别" in w for w in rep5["warnings"])
          and rep5.get("updated_by_key", 0) == 1 and rep5["imported"] == 0,
          f"小写 scope 归一化后幂等键认老不重复建档（#148-2，imported={rep5['imported']}）")

    code, renamed = req("PATCH", f"/positions/{base_pn['id']}",
                        {"version": base_after["version"],
                         "position_name": f"审计新职能{suffix}"}, token=admin)
    check(code == 200 and renamed["position_name"] == f"审计新职能{suffix}",
          "PATCH position_name 支持职能改名/新建（#143）")
    code, _b = req("POST", "/employment-tax-items",
                   {"tax_zone_id": zone["id"], "item_name": "坏科目", "item_kind": "percent"},
                   token=admin)
    check(code == 422, f"item_kind 非法值被 schema Literal 422 拦截（#145，{code}）")

    # ---------- 收尾输出 ----------
    print(f"\n════════ 结果: {PASS} 通过 / {FAIL} 失败 ════════")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
