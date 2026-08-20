"""数据库引擎与会话管理。

支持三环境 DB 隔离（PRD §7D）：

- 环境变量 `APP_ENV=dev|test|prod`（大小写不敏感，默认 dev）
- 同机不同库强制 `hr_db_{env}`，库名与 APP_ENV 不一致时拒绝启动
- 加载优先级：
  1) 显式 `DATABASE_URL`（shell / 当前进程已设） → 直接使用，但需通过库名校验
  2) 未设 → 按 APP_ENV 加载 `.env.{env}` / `.env` 后取 DATABASE_URL
  3) 仍无 → 拼默认 `postgresql://postgres:postgres@localhost:5432/hr_db_{env}`
- `assert_writable()` 用于 prod 环境下拦截破坏性操作（drop_all / --reset）
- 启动时打印 APP_ENV 与脱敏后的连接串，便于运维核对
"""
import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# ---------------------------------------------------------------- 配置
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_ENVS = ("dev", "test", "prod")


# ---------------------------------------------------------------- 工具
def _strip_quotes(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _expand_env(value: str) -> str:
    """展开 `${VAR}` / `$VAR` 引用 shell 环境变量；未设则抛错。

    仅支持字母/数字/下划线变量名；不做命令替换。
    """
    import re

    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

    def repl(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        if name not in os.environ:
            raise RuntimeError(
                f"配置文件引用的环境变量 ${name} 未设置。"
                f"请在启动前 export {name}=…，或在 .env 中显式写入（密码等敏感信息）。"
            )
        return os.environ[name]

    return pattern.sub(repl, value)


def _load_env_file(env: str) -> list[str]:
    """加载 `.env.{env}` 与 `.env`（按需），仅写入未显式设置的 key。

    支持 `${VAR}` / `$VAR` 引用 shell 环境变量（用于避免密码硬编码）。
    返回加载的文件列表，便于启动日志。
    """
    loaded: list[str] = []
    for name in (f".env.{env}", ".env"):
        path = PROJECT_ROOT / name
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if not k:
                continue
            v = _strip_quotes(v)
            v = _expand_env(v)
            if k not in os.environ:
                os.environ[k] = v
        loaded.append(name)
    return loaded


def get_app_env() -> str:
    """解析 APP_ENV；未设则回退 dev；非法值抛出。"""
    raw = os.environ.get("APP_ENV", "").strip().lower()
    if not raw:
        raw = "dev"
    if raw not in ALLOWED_ENVS:
        raise RuntimeError(
            f"APP_ENV 非法: {raw!r}（必须为 dev|test|prod）"
        )
    return raw


def _validate_database_url(database_url: str, app_env: str) -> str:
    """校验 DATABASE_URL 库名 == hr_db_{app_env}；不一致拒绝启动。"""
    expected = f"hr_db_{app_env}"
    parsed = urlparse(database_url)
    db_name = (parsed.path or "").lstrip("/")
    # 去掉可能的查询串/片段影响
    if not db_name:
        raise RuntimeError(
            f"DATABASE_URL 缺少数据库名（APP_ENV={app_env}，期望 {expected}）"
        )
    if db_name != expected:
        raise RuntimeError(
            f"DATABASE_URL 库名 {db_name!r} 与 APP_ENV={app_env!r} 不一致"
            f"（应为 {expected!r}）。三环境库名强制 hr_db_{{env}}，参见 PRD §7D。"
        )
    return db_name


def _sanitize_url(database_url: str) -> str:
    """脱敏密码；host:port/db 保留以便核对。"""
    parsed = urlparse(database_url)
    if parsed.password:
        user = parsed.username or ""
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        netloc = f"{user}:***@{host}{port}"
        parsed = parsed._replace(netloc=netloc)
    return urlunparse(parsed)


def _default_url(app_env: str) -> str:
    user = os.environ.get("DB_USER", "postgres")
    pwd = os.environ.get("DB_PASSWORD", "postgres")
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    return f"postgresql://{user}:{pwd}@{host}:{port}/hr_db_{app_env}"


def _resolve_database_url() -> tuple[str, str, list[str]]:
    """确定 (DATABASE_URL, APP_ENV, loaded_env_files)。"""
    app_env = get_app_env()
    loaded = _load_env_file(app_env)
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        url = _default_url(app_env)
    _validate_database_url(url, app_env)
    return url, app_env, loaded


# ---------------------------------------------------------------- 解析（模块导入即生效）
DATABASE_URL, APP_ENV, LOADED_ENV_FILES = _resolve_database_url()
SAFE_DATABASE_URL = _sanitize_url(DATABASE_URL)

# ---------------------------------------------------------------- 引擎
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依赖：提供数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------- 守卫
def assert_writable(operation: str = "破坏性操作") -> None:
    """prod 环境禁止执行破坏性操作（drop_all / --reset 等）。

    dev/test 无限制；test 允许 --reset。
    """
    if APP_ENV == "prod":
        raise RuntimeError(
            f"[FATAL] APP_ENV=prod 禁止{operation}。\n"
            f"  生产重置必须走线下备份+受控迁移：先 pg_dump hr_db_prod，再手工 SQL 恢复。\n"
            f"  本系统不提供 prod 库清空入口（PRD §7D.3）。"
        )


def startup_banner() -> str:
    """返回启动自检日志文本，供 main.py 打印。"""
    files = ", ".join(LOADED_ENV_FILES) if LOADED_ENV_FILES else "（无）"
    return (
        f"[startup] APP_ENV={APP_ENV}  DB={SAFE_DATABASE_URL}\n"
        f"          env files loaded: {files}"
    )