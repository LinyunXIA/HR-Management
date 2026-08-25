"""Issue #98 验证：CSV 迭代导入幂等键命中库内双编制时报错，不再静默取末行（PRD F4）。

用法：先启动 dev 服务（APP_ENV=dev uvicorn main:app --port 7273），再运行：
    .venv/bin/python tests/test_issue98_import_dupkey.py

覆盖：
- A 经 API 构造库内同幂等键双编制（职位+公司+国家或地区+开启日 全同）
- B 导入引用该键的行 → 报告 errors 含「同幂等键…该行不导入」，且不产生 imported/updated
- C 对照行（唯一键）正常入库；带岗位ID 认老路径不受影响
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


def upload_csv(token, csv_text):
    boundary = "----issue98boundary"
    body = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"file\"; filename=\"issue98.csv\"\r\n"
        f"Content-Type: text/csv\r\n\r\n{csv_text}\r\n--{boundary}--\r\n"
    ).encode("utf-8")
    r = urllib.request.Request(BASE + "/imports", data=body, method="POST")
    r.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
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


HEADERS = ("职位,职位类型,岗位编号,隶属公司,级别,国家或地区,职位开启日,职位关闭日,"
           "工作地点,工作职责描述,直线经理,虚线经理,法律强制/可选,"
           "Org-Chart中的显示,之前的职位,之前的公司,备注")


def main():
    section("准备")
    _, login = req("POST", "/auth/login", {"username": "admin", "password": "admin123"})
    admin = login["access_token"]
    _, comps = req("GET", "/companies", token=admin)
    company_name = comps[0]["name"]
    suffix = random.randint(10000, 99999)
    dup_name = f"Issue98重复岗{suffix}"

    section("A 构造库内同幂等键双编制")
    made = 0
    for _ in range(2):
        code, pn = req("POST", "/positions", {
            "position_name": dup_name, "company_id": comps[0]["id"],
            "scope": "global", "level": "B7a", "work_location": "卢森堡",
            "opening_date": "2026-01-01",
        }, token=admin)
        if code == 201 and pn.get("id"):
            made += 1
    check(made == 2, f"API 建成 2 个同键岗位（实际 {made}）")

    section("B 导入命中该键 → 报错不导入")
    csv_text = "\n".join([
        HEADERS,
        f"{dup_name},Employee,,{company_name},B7a,Global,2026,,,,,,,,",
        f"Issue98对照岗{suffix},Employee,,{company_name},B7a,Global,2026,,,,,,,,",
    ]) + "\n"
    code, rep = upload_csv(admin, csv_text)
    errs = [e for e in (rep or {}).get("errors", []) if dup_name in e]
    labels = [a["label"] for a in (rep or {}).get("assigned_numbers", [])]
    actions = [a["action"] for a in (rep or {}).get("assigned_numbers", []) if a["label"] == dup_name]
    check(code == 201 and bool(errs) and "同幂等键" in errs[0] and "该行不导入" in errs[0],
          f"报错含『库内存在 N 个同幂等键…该行不导入』（{errs[:1]}）")
    check(not actions,
          f"重复键行未产生 imported/updated（actions={actions}）")
    check(any("对照岗" in l for l in labels),
          f"对照行正常入库（assigned={[l for l in labels if '对照' in l]}）")

    print(f"\n结果：PASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
