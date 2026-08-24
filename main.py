"""FastAPI 入口：建表、注册路由、托管静态文件。"""
import os
import sys

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.limiter import limiter

from app import models  # noqa: F401  确保模型注册到 Base.metadata
from app.db import APP_ENV, Base, DATABASE_URL, SessionLocal, engine, startup_banner
from app.seed import seed_master_data
from app.routers import auth, data_clean, employees, import_routes, master_data, orgchart, positions, transfers, users
from app.routers import benchmarks as benchmarks_router

# 启动时打印三环境自检日志（PRD §7D.2）
print(startup_banner(), file=sys.stderr, flush=True)

# 生产安全配置显式校验（issue #70）：prod 下 JWT_SECRET_KEY 未覆盖/过短直接 FATAL，
# 于启动最早期执行，不再依赖 app.auth 模块导入顺序
from app.auth import validate_prod_config

validate_prod_config()

# 启动时建表（幂等）+ 初始化主数据字典
# create_all 本身不会删除已有表（非破坏性），无需按 env 区分；
# v2.5 起 SQLite 全新建库：version/CHECK 约束/新列均已在 models 声明，一次到位，
# 历史 PG 时代的轻量列迁移与值迁移函数已整体移除。
Base.metadata.create_all(bind=engine)

# 挂编联动 DB 层兜底触发器（issue #50，PRD §4 F1.5）
# 应用层 _assert_type_match 之外的硬保证：绕过 API 直写库 / 并发窗口均被拦截。
# v2.5 SQLite 语法重写（原 plpgsql 函数 + EXECUTE FUNCTION 已弃用）：
# INSERT / UPDATE 各一个触发器；SAEnum(native_enum=False) 持久化枚举「名」
# （如 REGULAR/OUTSOURCED），故此处由 Python 枚举派生存储值，单一事实源。
from app.models import EmployeeType as _ET


def _sqlite_attach_trigger_ddl(kind: str) -> str:
    """生成挂编联动校验触发器 DDL（kind='insert'|'update'，SQLite 语法）。"""
    sel_pt = "(SELECT position_type FROM position_numbers WHERE id = NEW.position_number_id)"
    head = (
        f"BEFORE INSERT ON employees"
        if kind == "insert"
        else "BEFORE UPDATE OF employee_type, position_number_id ON employees"
    )
    return f"""
CREATE TRIGGER IF NOT EXISTS trg_employees_attach_type_{kind}
{head}
FOR EACH ROW
WHEN NEW.position_number_id IS NOT NULL
BEGIN
    SELECT CASE
        WHEN {sel_pt} = 'Consultant' AND NEW.employee_type <> '{_ET.REGULAR.name}'
            THEN RAISE(ABORT, '挂编联动校验失败：岗位为顾问编制（Consultant），仅允许「正式」员工')
        WHEN {sel_pt} = 'External Employee' AND NEW.employee_type <> '{_ET.OUTSOURCED.name}'
            THEN RAISE(ABORT, '挂编联动校验失败：岗位为外包编制（External Employee），仅允许「外包」员工')
        WHEN {sel_pt} = 'Employee'
             AND NEW.employee_type NOT IN ('{_ET.REGULAR.name}', '{_ET.INTERN.name}', '{_ET.LABOR.name}')
            THEN RAISE(ABORT, '挂编联动校验失败：岗位为正式编制（Employee），仅允许「正式/实习/劳务」员工')
    END;
END;
"""


def _ensure_attach_type_triggers():
    try:
        with engine.begin() as conn:
            for kind in ("insert", "update"):
                conn.execute(text(_sqlite_attach_trigger_ddl(kind)))
        print("[migrate] trg_employees_attach_type_{insert,update} 已就绪"
              "（挂编联动 DB 兜底，#50，SQLite 触发器）", file=sys.stderr)
    except Exception as e:
        print(f"[migrate] attach-type triggers skipped: {e}", file=sys.stderr)


_ensure_attach_type_triggers()


# 轻量迁移（v2.6）：成本六栏改造 + 外部基准对接
# - 新增列：position_numbers 四栏 / employees.actual_ 四栏 / employment_tax_items 两列
#   （create_all 只建缺失表不补列，存量库需逐列幂等 ADD）
# - 废弃列：company_share / actual_company_share（公司份额拆分为强制扣税+定额外，
#   三库均无数据）。dev/test 直接 DROP；prod 按 §7D.3 走受控迁移，仅告警不动结构
def _ensure_v26_cost_columns():
    from sqlalchemy import inspect as sa_inspect

    adds = {
        "position_numbers": [
            ("mandatory_tax", "NUMERIC(14,2)"),
            ("mandatory_fixed_fee", "NUMERIC(14,2)"),
            ("fixed_bonus", "NUMERIC(14,2)"),
            ("floating_bonus", "NUMERIC(14,2)"),
        ],
        "employees": [
            ("actual_mandatory_tax", "NUMERIC(14,2)"),
            ("actual_mandatory_fixed_fee", "NUMERIC(14,2)"),
            ("actual_fixed_bonus", "NUMERIC(14,2)"),
            ("actual_floating_bonus", "NUMERIC(14,2)"),
        ],
        "employment_tax_items": [
            ("item_kind", "VARCHAR(10) NOT NULL DEFAULT 'rate'"),
            ("fixed_amount", "NUMERIC(14,2)"),
        ],
    }
    drops = {
        "position_numbers": ["company_share"],
        "employees": ["actual_company_share"],
    }
    try:
        # 先在事务外完成全部反射，构建 DDL 计划——BEGIN IMMEDIATE 配方下，
        # 事务内再发起任何语句（含 inspector 反射的 SELECT）都会以 IMMEDIATE
        # 开启写事务，与外层写锁自死锁（v2.6 教训：反射绝不能进写事务）
        stmts: list[str] = []
        for tbl, cols in adds.items():
            existing = {c["name"] for c in sa_inspect(engine).get_columns(tbl)}
            if not existing:
                continue  # 新库由 create_all 建表
            for name, ddl in cols:
                if name not in existing:
                    stmts.append(f"ALTER TABLE {tbl} ADD COLUMN {name} {ddl}")
        drop_stmts: list[str] = []
        if APP_ENV != "prod":
            for tbl, cols in drops.items():
                existing = {c["name"] for c in sa_inspect(engine).get_columns(tbl)}
                for name in cols:
                    if name in existing:
                        drop_stmts.append(f"ALTER TABLE {tbl} DROP COLUMN {name}")
        elif any(c in {x["name"] for x in sa_inspect(engine).get_columns(tbl)}
                 for tbl, cols in drops.items() for c in cols):
            pending = [c for tbl, cols in drops.items() for c in cols
                       if c in {x["name"] for x in sa_inspect(engine).get_columns(tbl)}]
            if pending:
                print(f"[migrate] WARNING(prod): 废弃列 {pending} 未删除——"
                      f"生产变更走受控迁移：ALTER TABLE ... DROP COLUMN（§7D.3）", file=sys.stderr)

        if not stmts and not drop_stmts:
            return
        import time

        for attempt in range(3):
            try:
                engine.dispose()  # 弃用全部池化连接：排除残留事务持锁
                with engine.begin() as conn:
                    for s in stmts + drop_stmts:
                        conn.execute(text(s))
                        print(f"[migrate] {s}", file=sys.stderr)
                return
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    raise
                print(f"[migrate] v26 迁移第 {attempt + 1} 次尝试失败（{e}），重试…", file=sys.stderr)
                time.sleep(1)
    except Exception as e:
        print(f"[migrate] v26 cost columns ensure failed: {e}", file=sys.stderr)


_ensure_v26_cost_columns()

with SessionLocal() as db:
    seed_master_data(db)

app = FastAPI(title="轻量级 HR 管理系统", version="1.0.0")
app.state.limiter = limiter


def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": f"请求过于频繁，请稍后重试（{exc.detail}）"})


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(master_data.router)
app.include_router(data_clean.router)
app.include_router(positions.router)
app.include_router(employees.router)
app.include_router(orgchart.router)
app.include_router(import_routes.router)
app.include_router(transfers.router)
app.include_router(benchmarks_router.router)


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "app": "HR Management", "env": APP_ENV}


@app.get("/", include_in_schema=False)
def index():
    """根路径返回前端单页（注入 APP_ENV 供前端环境徽章使用，无 DB 查询）。"""
    from pathlib import Path

    from fastapi.responses import HTMLResponse

    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
    html = Path(html_path).read_text(encoding="utf-8")
    # 从 DATABASE_URL 解析库文件名 hr_db_{env}.db，仅字符串解析，无 DB 连接
    try:
        from urllib.parse import unquote as _unquote, urlsplit as _urlsplit

        db_name = os.path.basename(_unquote(_urlsplit(DATABASE_URL).path))
    except Exception:
        db_name = ""
    # 同步注入：前端通过 window.APP_ENV / window.APP_DB 直接读取，无需额外 fetch
    injection = f'<script>window.APP_ENV="{APP_ENV}";window.APP_DB="{db_name}";</script>'
    if "</head>" in html:
        html = html.replace("</head>", f"{injection}</head>", 1)
    else:
        html = injection + html
    return HTMLResponse(html)


# 托管前端静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")
