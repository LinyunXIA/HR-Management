"""集成测试套件（issue #64）：数据清洗 / 组织图 / 导出 MD / 乐观锁 / 三环境配置 / UI 冒烟。

用法：先启动 dev 服务（.venv/bin/uvicorn main:app --port 7273），再运行：
    .venv/bin/python tests/test_integration.py

覆盖（对应 issue #64 清单中可自动化部分）：
- S1 认证登录
- S2 数据清洗：Org-Chart3.md 解析报告 + CSV 结构
- S3 清洗导入幂等（两段式 upsert，编号系统分配）
- S4 组织图 JSON 结构（nodes/solid_edges/dotted_edges/roots）
- S5 导出 MD 3 格式（org/solid/dotted）
- S6 乐观锁 409 冲突与正常更新
- S7 三环境配置解析单元测试（库名一致性 / APP_ENV 解析 / prod 护栏）
- S8 UI 冒烟：headless Chrome 渲染 #orgchart DOM 检查（无 Chrome 时跳过）

仅操作 hr_db_dev；S6 创建的临时岗位命名带「集成测试」前缀，由 --reset 流程管理。
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:7273/api/v1"
PASS = 0
FAIL = 0


def req(method, path, body=None, token=None, expect=None, raw=False, headers=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r) as resp:
            code = resp.status
            payload = resp.read() if raw else json.loads(resp.read() or b"null")
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        code = e.code
        payload = e.read() if raw else json.loads(e.read() or b"null")
        resp_headers = {k.lower(): v for k, v in e.headers.items()}
    if expect is not None:
        check(code == expect, f"{method} {path} → {code} (期望 {expect})" +
              ("" if code == expect else f" detail={str(payload)[:200]}"))
    return code, payload, resp_headers


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


def s7_env_config():
    """三环境配置解析单元测试（纯函数，不连接 prod 库）。"""
    section("S7 三环境配置解析")
    from app import db as appdb

    # 库名一致性校验（v2.5 SQLite 三文件）
    got = appdb._validate_database_url("sqlite:///./data/hr_db_dev.db", "dev")
    check(got == "hr_db_dev.db", "_validate_database_url 匹配通过 (hr_db_dev.db)")
    try:
        appdb._validate_database_url("sqlite:///./data/hr_db_prod.db", "dev")
        check(False, "库名不一致应拒绝")
    except RuntimeError as e:
        check("hr_db_prod" in str(e) and "dev" in str(e), "库名与 APP_ENV 不一致拒绝启动")
    try:
        appdb._validate_database_url("postgresql://u:p@localhost:5432/hr_db_dev", "dev")
        check(False, "非 SQLite scheme 应拒绝")
    except RuntimeError:
        check(True, "仅支持 sqlite:///，其他 scheme 拒绝")

    # APP_ENV 解析优先级与合法性
    old = os.environ.pop("APP_ENV", None)
    try:
        os.environ["APP_ENV"] = "DEV"
        check(appdb.get_app_env() == "dev", "APP_ENV 大小写不敏感 → dev")
        os.environ["APP_ENV"] = "staging"
        try:
            appdb.get_app_env()
            check(False, "非法 APP_ENV 应抛出")
        except RuntimeError:
            check(True, "非法 APP_ENV 抛 RuntimeError")
        os.environ.pop("APP_ENV", None)
        check(appdb.get_app_env() in ("dev", "test", "prod"), "未设 APP_ENV 回退合法值")
    finally:
        if old is not None:
            os.environ["APP_ENV"] = old

    # prod 破坏性操作护栏
    check(appdb.assert_writable("--reset") is None, "dev 下 assert_writable 放行")
    saved = appdb.APP_ENV
    try:
        appdb.APP_ENV = "prod"
        try:
            appdb.assert_writable("--reset")
            check(False, "prod 下应拦截 --reset")
        except RuntimeError as e:
            check("备份" in str(e), "prod 下 assert_writable 拦截并提示文件复制备份")
    finally:
        appdb.APP_ENV = saved


def s8_ui_smoke():
    """headless 浏览器渲染冒烟：登录后组织图 SVG 节点 + 岗位管理表格（Playwright）。"""
    section("S8 UI 冒烟（headless Chromium，可选）")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ⏭ 未安装 playwright，跳过 UI 冒烟")
        return
    root = BASE.rsplit("/api", 1)[0]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(root + "/")
        page.wait_for_timeout(1200)
        if page.locator("#btn-login").count():
            page.click("#btn-login")
            page.wait_for_timeout(300)
            page.click("#lg-submit")
            page.wait_for_timeout(1200)
        page.click('nav button[data-tab="orgchart"]')
        page.wait_for_timeout(1500)
        check(page.locator("#org-svg").count() > 0, "#orgchart 渲染出 SVG 容器")
        check(page.locator(".org-node").count() >= 1, "组织图渲染出岗位节点卡片")
        page.click('nav button[data-tab="positions"]')
        page.wait_for_timeout(1000)
        check(page.locator("#tab-positions table").count() > 0, "#positions 列表容器渲染")
        browser.close()


def main():
    # ---------- S1 登录 ----------
    section("S1 认证")
    _, login, _ = req("POST", "/auth/login", {"username": "admin", "password": "admin123"}, expect=200)
    admin = login["access_token"]
    check(bool(admin), "admin 登录换取 JWT")

    # ---------- S2 数据清洗 ----------
    section("S2 数据清洗（Org-Chart3.md）")
    code, job, _ = req("POST", "/data-clean-jobs?source_file=" + urllib.parse.quote("Org-Chart3.md"),
                       token=admin, expect=201)
    total = job.get("total_positions", 0)
    report = job.get("report", {})
    check(total >= 1, f"清洗解析出 {total} 个岗位")
    check(report.get("errors") == [], "清洗报告无 error")
    csv_text = job.get("csv_text", "")
    for col in ("职位", "职位类型", "隶属公司", "国家或地区", "职位开启日", "工作地点"):
        check(col in csv_text, f"CSV 含「{col}」列")

    # ---------- S3 清洗导入幂等 ----------
    section("S3 清洗作业导入（幂等 upsert）")
    code, imp, _ = req("POST", f"/data-clean-jobs/{job['id']}/imports",
                       token=admin, expect=201)  # #95：导入端点已收口为 admin-only
    ir = imp.get("import_report", {})
    check(ir.get("errors") == [], "导入无 error")
    check(ir.get("imported", 0) + ir.get("updated", 0) == total,
          f"imported({ir.get('imported')})+updated({ir.get('updated')}) == total({total})")
    nums = [a["number"] for a in ir.get("assigned_numbers", [])]
    check(all(n.startswith(("P", "PA")) for n in nums), f"系统分配正式编号 {nums[:4]}{'…' if len(nums) > 4 else ''}")
    code, imp2, _ = req("POST", f"/data-clean-jobs/{job['id']}/imports",
                        token=admin, expect=201)  # #95：同上，带 admin token
    ir2 = imp2.get("import_report", {})
    check(ir2.get("imported", 0) == 0, "重复导入 imported=0（幂等认老，不产生重复数据）")

    # ---------- S4 组织图 JSON ----------
    section("S4 组织图结构")
    _, oc, _ = req("GET", "/org-charts", token=admin)
    for key in ("nodes", "solid_edges", "dotted_edges", "roots"):
        check(key in oc, f"返回含 {key}")
    nodes = oc.get("nodes", [])
    check(len(nodes) >= 1 and all(n.get("number") for n in nodes), f"节点均有编号（{len(nodes)} 节点）")
    edges = oc.get("solid_edges", [])
    tos = {e["to"] for e in edges}
    child_nums = {n["number"] for n in nodes if n["number"] in tos}
    roots = set(oc.get("roots", []))
    overlap = child_nums & roots
    check(not overlap, f"根节点不出现在任何 solid_edge 终点（交集={overlap or '∅'}）")

    # ---------- S5 导出 MD ----------
    section("S5 导出 MD 三格式（Accept: text/markdown 协商）")
    titles = {"org": "组织架构", "solid": "直线汇报线", "dotted": "虚线汇报线"}
    for fmt, kw in titles.items():
        c, body, hdr = req("GET", f"/org-charts?report={fmt}", raw=True, token=admin,
                           headers={"Accept": "text/markdown"})
        text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
        ctype = hdr.get("content-type", "")
        check(c == 200 and "text/markdown" in ctype, f"report={fmt} → text/markdown")
        check(kw in text, f"report={fmt} 含「{kw}」标题")

    # ---------- S6 乐观锁 ----------
    section("S6 乐观锁 409")
    suffix = os.urandom(3).hex()
    c, pn, _ = req("POST", "/positions", {
        "position_name": f"集成测试岗{suffix}", "company_id": 1, "level": "B7a",
        "scope": "global", "position_type": "Employee",
        "work_location": "比利时布鲁塞尔",
    }, token=admin, expect=201)
    v0 = pn["version"]
    c409, _b, _h = req("PATCH", f"/positions/{pn['id']}",
                       {"version": v0 + 999, "remark": "stale"}, token=admin)
    check(c409 == 409, f"过期版本 PATCH → 409（实际 {c409}）")
    c200, pn2, _h = req("PATCH", f"/positions/{pn['id']}",
                        {"version": v0, "remark": "ok"}, token=admin, expect=200)
    check(pn2["version"] == v0 + 1, f"正确版本更新成功 version {v0}→{pn2['version']}")
    check(pn["number"].startswith("P"), f"编号系统分配 {pn['number']}")

    # ---------- S7/S8 ----------
    s7_env_config()
    s8_ui_smoke()

    # ---------- 汇总 ----------
    print(f"\n━━ 结果：{PASS} 通过 / {FAIL} 失败 ━━")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
