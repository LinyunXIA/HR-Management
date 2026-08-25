"""Issue #97（口径 C）验证：职位开启日强制必填，法律分类/Org显示保持可选。

用法：先启动 dev 服务（APP_ENV=dev uvicorn main:app --port 7273），再运行：
    .venv/bin/python tests/test_issue97_opening_required.py

覆盖：
- A POST /positions 缺 opening_date → 422；携带则 201（legal_category / org_chart_display 空仍放行）
- B PATCH 显式 opening_date=null → 400 不可清空
- C CSV 导入：缺「职位开启日」的行报错不导入；带开启日的对照行正常入库
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
    """multipart 上传 Position.csv 到 POST /imports。"""
    boundary = "----issue97boundary"
    body = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"file\"; filename=\"issue97.csv\"\r\n"
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

    section("A 创建岗位：opening_date 必填；法律分类/Org显示可空")
    code, body = req("POST", "/positions", {
        "position_name": f"Issue97缺开启日岗{suffix}", "company_id": comps[0]["id"],
        "scope": "global", "level": "B7a", "work_location": "卢森堡",
    }, token=admin)
    check(code == 422, f"缺 opening_date → 422（实际 {code}）")
    code, pn = req("POST", "/positions", {
        "position_name": f"Issue97可选字段岗{suffix}", "company_id": comps[0]["id"],
        "scope": "global", "level": "B7a", "work_location": "卢森堡",
        "opening_date": "2026-01-01",
    }, token=admin)
    check(code == 201 and pn.get("legal_category") is None and pn.get("org_chart_display") is None,
          f"无法律分类/Org显示仍 201 且两字段为空（实际 {code}）")

    section("B PATCH 清空 opening_date → 400")
    code, _b = req("PATCH", f"/positions/{pn['id']}",
                   {"version": pn["version"], "opening_date": None}, token=admin)
    check(code == 400, f"显式置 null → 400（实际 {code}）")

    section("C CSV 导入：缺开启日报错不导入")
    csv_text = "\n".join([
        HEADERS,
        f"Issue97CSV缺开启日{suffix},Employee,,{company_name},B7a,Global,,,,,,, ,,",
        f"Issue97CSV对照岗{suffix},Employee,,{company_name},B7a,Global,2026,,,,,,,,",
    ]) + "\n"
    code, rep = upload_csv(admin, csv_text)
    errs = [e for e in (rep or {}).get("errors", []) if f"Issue97CSV缺开启日{suffix}" in e]
    imported_labels = [a["label"] for a in (rep or {}).get("assigned_numbers", [])]
    check(code == 201 and bool(errs) and "该行不导入" in errs[0],
          f"缺开启日行报错且含『该行不导入』（errors={errs[:1]}）")
    check(any("对照岗" in l for l in imported_labels),
          f"对照行正常入库（assigned={[l for l in imported_labels if '对照' in l]}）")

    print(f"\n结果：PASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
