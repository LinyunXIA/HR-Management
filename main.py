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

# 生产安全配置显式校验（issue #70）：prod 下 JWT_SECRET_KEY 未覆盖/过短直接 FATAL，
# 于启动最早期执行，不再依赖 app.auth 模块导入顺序
from app.auth import validate_prod_config

validate_prod_config()

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

# 轻量迁移：补/修 employees 在职必须挂岗的 CHECK 约束（issue #1，PRD §5；#57 清理冗余值）
def _ensure_employee_check_constraint():
    try:
        # SAEnum(native_enum=False) 持久化枚举「名」（'TERMINATED'），中文值不入库；
        # 约束仅保留实际存储值（#57：去除误导性冗余项 '离职'）
        from app.models import EmploymentStatus as _ES
        from app.models import EmployeeType as _ETO
        _terminated_name = _ES.TERMINATED.name  # 'TERMINATED'
        _outsourced_name = _ETO.OUTSOURCED.name  # 'OUTSOURCED'
        _want_ck = (
            f"CHECK ((employment_status = '{_terminated_name}'::text) "
            f"OR (position_number_id IS NOT NULL) "
            f"OR ((employee_type)::text = '{_outsourced_name}'::text))"
        )
        with engine.begin() as conn:
            # 幂等添加/修复 CHECK（PostgreSQL 无 IF NOT EXISTS，依赖 pg_constraint 判断）
            exists = conn.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_employees_position_required_if_active'"
            )).first()
            cur = exists[0] if exists else None
            need_replace = bool(cur) and "OUTSOURCED" not in cur
            if need_replace:
                conn.execute(text("ALTER TABLE employees DROP CONSTRAINT ck_employees_position_required_if_active"))
                print("[migrate] ck_employees_position_required_if_active 旧版（无外包豁免）已删除待重建（v2.4.2）", file=sys.stderr)
            if not exists or need_replace:
                conn.execute(text(
                    f"ALTER TABLE employees ADD CONSTRAINT ck_employees_position_required_if_active "
                    f"CHECK (employment_status = '{_terminated_name}' OR position_number_id IS NOT NULL "
                    f"OR employee_type = '{_outsourced_name}')"
                ))
                print("[migrate] ck_employees_position_required_if_active 已就绪（外包可虚拟建档不挂岗，v2.4.2）", file=sys.stderr)
    except Exception as e:
        print(f"[migrate] employees CHECK constraint skipped: {e}", file=sys.stderr)

_ensure_employee_check_constraint()

# 轻量迁移：scope=COUNTRY 时 country_id 必填的 DB 层 CHECK（issue #69，PRD §3.2）
def _ensure_country_scope_check():
    try:
        from app.models import Scope as _Scope
        _country_name = _Scope.COUNTRY.name  # 'COUNTRY'（SAEnum 持久化「名」）
        conname = "ck_positions_country_required_when_country_scope"
        with engine.begin() as conn:
            v = conn.execute(text(
                f"SELECT count(*) FROM position_numbers WHERE scope='{_country_name}' AND country_id IS NULL"
            )).scalar()
            if v and v > 0:
                print(f"[migrate] WARNING: {v} 行 scope={_country_name} 岗位缺少 country_id，CHECK 约束将拒绝此数据，请先修复", file=sys.stderr)
            exists = conn.execute(text(
                f"SELECT 1 FROM pg_constraint WHERE conname='{conname}'"
            )).first()
            if not exists:
                conn.execute(text(
                    f"ALTER TABLE position_numbers ADD CONSTRAINT {conname} "
                    f"CHECK (scope <> '{_country_name}' OR country_id IS NOT NULL)"
                ))
                print(f"[migrate] {conname} 已添加（issue #69）", file=sys.stderr)
    except Exception as e:
        print(f"[migrate] country-scope CHECK constraint skipped: {e}", file=sys.stderr)

_ensure_country_scope_check()

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

# 轻量迁移：v2.3 新增列（转调 / 实际成本 / 工作地点两级 / 税区 / 虚线标签）
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
        # 虚线经理标签
        "ALTER TABLE position_number_dotted_lines ADD COLUMN IF NOT EXISTS label VARCHAR(100)",
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

# 轻量迁移：v2.4 公司主数据改造（开业/关闭日期；
# 新表 external_companies / company_shareholders 由 create_all 幂等创建）
def _ensure_v24_company_columns():
    stmts = [
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS opening_date DATE",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS closing_date DATE",
        # v2.4.1：外部合作公司同样以开业/关闭日期管理启停
        "ALTER TABLE external_companies ADD COLUMN IF NOT EXISTS opening_date DATE",
        "ALTER TABLE external_companies ADD COLUMN IF NOT EXISTS closing_date DATE",
    ]
    try:
        with engine.begin() as conn:
            for sql in stmts:
                try:
                    conn.execute(text(sql))
                except Exception as e:
                    print(f"[migrate] skipped: {sql[:60]}...: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[migrate] v24 company columns ensure failed: {e}", file=sys.stderr)

_ensure_v24_company_columns()

# 轻量迁移：v2.4.3 用户权限拆分（users.user_type；user_apis 表由 create_all 幂等创建）
def _ensure_v243_user_columns():
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS user_type VARCHAR(10) "
                "NOT NULL DEFAULT 'UI'"))
    except Exception as e:
        print(f"[migrate] users.user_type ensure failed: {e}", file=sys.stderr)

_ensure_v243_user_columns()
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

# 轻量迁移：挂编联动 DB 层兜底触发器（issue #50，PRD §4 F1.5）
# 应用层 _assert_type_match 之外的硬保证：绕过 API 直写库 / 并发窗口均被拦截。
# 注意：SAEnum(native_enum=False) 持久化枚举「名」（如 REGULAR/OUTSOURCED），
# 故此处由 Python 枚举派生存储值，与 models.EmployeeType 保持单一事实源。
from app.models import EmployeeType as _ET

ATTACH_TYPE_TRIGGER_FN_SQL = f"""
CREATE OR REPLACE FUNCTION fn_check_attach_type() RETURNS trigger AS $fn$
DECLARE
    v_ptype TEXT;
BEGIN
    IF NEW.position_number_id IS NOT NULL THEN
        SELECT position_type INTO v_ptype FROM position_numbers WHERE id = NEW.position_number_id;
        IF v_ptype = 'Consultant' AND NEW.employee_type <> '{_ET.REGULAR.name}' THEN
            RAISE EXCEPTION '挂编联动校验失败：岗位为顾问编制（Consultant），仅允许「正式」员工';
        END IF;
        IF v_ptype = 'External Employee' AND NEW.employee_type <> '{_ET.OUTSOURCED.name}' THEN
            RAISE EXCEPTION '挂编联动校验失败：岗位为外包编制（External Employee），仅允许「外包」员工';
        END IF;
        IF v_ptype = 'Employee'
           AND NEW.employee_type NOT IN ('{_ET.REGULAR.name}', '{_ET.INTERN.name}', '{_ET.LABOR.name}') THEN
            RAISE EXCEPTION '挂编联动校验失败：岗位为正式编制（Employee），仅允许「正式/实习/劳务」员工';
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
"""

ATTACH_TYPE_TRIGGER_DDL_SQL = """
DROP TRIGGER IF EXISTS trg_employees_attach_type ON employees;
CREATE TRIGGER trg_employees_attach_type
    BEFORE INSERT OR UPDATE ON employees
    FOR EACH ROW EXECUTE FUNCTION fn_check_attach_type();
"""


def _ensure_attach_type_trigger():
    try:
        with engine.begin() as conn:
            conn.execute(text(ATTACH_TYPE_TRIGGER_FN_SQL))
            conn.execute(text(ATTACH_TYPE_TRIGGER_DDL_SQL))
            print("[migrate] trg_employees_attach_type 已就绪（挂编联动 DB 兜底，#50）",
                  file=sys.stderr)
    except Exception as e:
        print(f"[migrate] attach-type trigger skipped: {e}", file=sys.stderr)


_ensure_attach_type_trigger()

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
