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
from app.db import APP_ENV, Base, SessionLocal, engine, startup_banner
from app.seed import seed_master_data
from app.routers import auth, data_clean, employees, import_routes, master_data, orgchart, positions, transfers, users

# 启动时打印三环境自检日志（PRD §7D.2）
print(startup_banner(), file=sys.stderr, flush=True)

# 启动时建表（幂等）+ 初始化主数据字典
# create_all 本身不会删除已有表（非破坏性），无需按 env 区分
Base.metadata.create_all(bind=engine)

# 轻量迁移：为已存在表补 version 列（PRD §7C 乐观锁，存量库兼容）
def _ensure_version_columns():
    try:
        with engine.begin() as conn:
            for tbl in ("position_numbers", "employees"):
                try:
                    conn.execute(text(
                        f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1"
                    ))
                    # 存量 NULL 回填
                    conn.execute(text(f"UPDATE {tbl} SET version=1 WHERE version IS NULL"))
                except Exception as e:
                    print(f"[migrate] {tbl}.version skipped: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[migrate] version columns ensure failed: {e}", file=sys.stderr)

_ensure_version_columns()

# 轻量迁移：补 employees 在职必须挂岗的 CHECK 约束（issue #1，PRD §5）
def _ensure_employee_check_constraint():
    try:
        # 约束值派生自枚举，避免硬编码分散（兼容历史英文值 'TERMINATED' + 当前值 '离职'）
        from app.models import EmploymentStatus as _ES
        _terminated_name = _ES.TERMINATED.name  # 'TERMINATED'
        _terminated_value = _ES.TERMINATED.value  # '离职'
        with engine.begin() as conn:
            # 若已存在旧的违反约束的行，先报告（不阻断启动，但约束将添加失败）
            v = conn.execute(text(
                f"SELECT count(*) FROM employees "
                f"WHERE employment_status NOT IN ('{_terminated_name}', '{_terminated_value}') AND position_number_id IS NULL"
            )).scalar()
            if v and v > 0:
                print(f"[migrate] WARNING: {v} 行在职员工 position_number_id 为 NULL，CHECK 约束将拒绝此数据，请先修复", file=sys.stderr)
            # 幂等添加/修复 CHECK（PostgreSQL 无 IF NOT EXISTS，依赖 pg_constraint 判断）
            exists = conn.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_employees_position_required_if_active'"
            )).first()
            expected_def = f"CHECK ((employment_status = ANY (ARRAY['{_terminated_name}'::text, '{_terminated_value}'::text])) OR (position_number_id IS NOT NULL))"
            need_replace = False
            if exists:
                # 若旧约束定义仅含 '离职'（未含 TERMINATED），则替换
                if _terminated_name not in exists[0]:
                    need_replace = True
                    conn.execute(text("ALTER TABLE employees DROP CONSTRAINT ck_employees_position_required_if_active"))
                    print("[migrate] 旧 ck_employees_position_required_if_active 定义不完整，已删除待重建", file=sys.stderr)
            if not exists or need_replace:
                conn.execute(text(
                    f"ALTER TABLE employees ADD CONSTRAINT ck_employees_position_required_if_active "
                    f"CHECK (employment_status IN ('{_terminated_name}', '{_terminated_value}') OR position_number_id IS NOT NULL)"
                ))
                print("[migrate] ck_employees_position_required_if_active 已添加", file=sys.stderr)
    except Exception as e:
        print(f"[migrate] employees CHECK constraint skipped: {e}", file=sys.stderr)

_ensure_employee_check_constraint()

# 轻量迁移：legal_category 由 SAEnum(冗余枚举名) → String 字典值（issue #2）
def _migrate_legal_category_values():
    try:
        with engine.begin() as conn:
            mapping = {
                "MANDATORY_INTERNAL": "法律强制·内部全职不可外包",
                "MANDATORY_OUTSOURCEABLE": "法律强制·允许第三方外包",
                "OPTIONAL": "可选（集团内控推荐）",
                "LOGISTICS": "纯后勤可选",
            }
            for old, new in mapping.items():
                res = conn.execute(text(
                    "UPDATE position_numbers SET legal_category = :new WHERE legal_category = :old"
                ), {"new": new, "old": old})
                if res.rowcount:
                    print(f"[migrate] legal_category {old} -> {new} ({res.rowcount} 行)", file=sys.stderr)
    except Exception as e:
        print(f"[migrate] legal_category migration skipped: {e}", file=sys.stderr)

_migrate_legal_category_values()

# 轻量迁移：companies 增加 is_active 软删除标记（opened/closed，id 保留）
def _ensure_company_is_active():
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"))
            conn.execute(text("UPDATE companies SET is_active=TRUE WHERE is_active IS NULL"))
            # 兼容旧库：若列已存在但无默认值，补默认值
    except Exception as e:
        print(f"[migrate] companies.is_active skipped: {e}", file=sys.stderr)

_ensure_company_is_active()

# 轻量迁移：v2.3 新增列（转调 / 实际成本 / 工作地点两级 / 税区）
def _ensure_v23_columns():
    stmts = [
        # 员工：转调中目标公司 + 实际成本四字段
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS target_company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS actual_cost_mode VARCHAR(10) NOT NULL DEFAULT 'manual'",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS actual_salary_before_tax NUMERIC(14,2)",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS actual_company_share NUMERIC(14,2)",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS actual_labor_cost NUMERIC(14,2)",
        # 工作地点：国家+城市 两级（税区挂载用）
        "ALTER TABLE work_locations ADD COLUMN IF NOT EXISTS country VARCHAR(100)",
        "ALTER TABLE work_locations ADD COLUMN IF NOT EXISTS city VARCHAR(100)",
        # 用工税额：挂载到税区（旧 country_id 保留兼容，放开非空约束）
        "ALTER TABLE employment_tax_items ADD COLUMN IF NOT EXISTS tax_zone_id INTEGER REFERENCES tax_zones(id) ON DELETE CASCADE",
        "ALTER TABLE employment_tax_items ALTER COLUMN country_id DROP NOT NULL",
    ]
    try:
        with engine.begin() as conn:
            for sql in stmts:
                try:
                    conn.execute(text(sql))
                except Exception as e:
                    print(f"[migrate] skipped: {sql[:60]}...: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[migrate] v23 columns ensure failed: {e}", file=sys.stderr)

_ensure_v23_columns()
# 轻量迁移：users.role 由旧 String 值('admin'/'hr') → 枚举名('ADMIN'/'HR')
def _migrate_user_role_values():
    try:
        with engine.begin() as conn:
            for old, new in (("admin", "ADMIN"), ("hr", "HR")):
                res = conn.execute(text(
                    f"UPDATE users SET role='{new}' WHERE role='{old}'"
                ))
                if res.rowcount:
                    print(f"[migrate] users.role {old} -> {new} ({res.rowcount} 行)", file=sys.stderr)
    except Exception as e:
        print(f"[migrate] users.role migration skipped: {e}", file=sys.stderr)

_migrate_user_role_values()

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


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "app": "HR Management", "env": APP_ENV}


@app.get("/", include_in_schema=False)
def index():
    """根路径返回前端单页。"""
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html"))


# 托管前端静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")
