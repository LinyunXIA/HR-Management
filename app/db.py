"""数据库引擎与会话管理。

支持三环境 DB 隔离（PRD §7D）：

- 环境变量 `APP_ENV=dev|test|prod`（大小写不敏感，默认 dev）
- SQLite 同机三文件隔离，文件名强制 `hr_db_{env}.db`，与 APP_ENV 不一致时拒绝启动
- 加载优先级：
  1) 显式 `DATABASE_URL`（shell / 当前进程已设） → 直接使用，但需通过文件名校验
  2) 未设 → 按 APP_ENV 加载 `.env.{env}` / `.env` 后取 DATABASE_URL
  3) 仍无 → 拼默认 `sqlite:///{PROJECT_ROOT}/data/hr_db_{env}.db`
- 每连接自动注入 PRAGMA：foreign_keys=ON（SQLite 默认关闭外键！）、WAL、busy_timeout
- `assert_writable()` 用于 prod 环境下拦截破坏性操作（drop_all / --reset）
- 启动时打印 APP_ENV 与数据库文件路径，便于运维核对
"""
import os
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
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
    """加载 `.env`（统一单文件，内含三环境段）并兼容旧的 `.env.{env}`。

    支持 `${VAR}` / `$VAR` 引用 shell 环境变量（用于避免密码硬编码）。
    返回加载的文件列表，便于启动日志。
    """
    loaded: list[str] = []
    # 1) 统一单文件 .env（必读）
    # 2) 兼容旧的三文件 .env.{env}（若存在则追加，且仅对未显式键生效）
    for name in (".env", f".env.{env}"):
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
            # 去引号后暂不展开 ${VAR}，延迟到 _resolve 阶段统一展开，
            # 以支持 .env 内 DATABASE_URL_{env} 引用 shell 变量（如密码）且该变量后设的场景
            v = _strip_quotes(v)
            if k not in os.environ:
                # 先存原始值，带 ${} 的稍后展开
                os.environ[k] = v
                # 对非 DATABASE_URL_* 的普通键立即展开（JWT 等）
                if not k.startswith("DATABASE_URL"):
                    try:
                        os.environ[k] = _expand_env(v)
                    except RuntimeError:
                        # 延迟展开，保留原始值供后续错误提示
                        pass
        if name not in loaded:
            loaded.append(name)
    return loaded


def _peek_app_env_from_dotenv() -> str | None:
    """若 shell 未设 APP_ENV，尝试从 .env 读取 APP_ENV（首个非注释行）。"""
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return None
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == "APP_ENV":
                return _strip_quotes(v).strip().lower() or None
    except OSError:
        return None
    return None


def get_app_env() -> str:
    """解析 APP_ENV；未设则回退 dev；非法值抛出。

    优先级：shell 显式 > .env 内 APP_ENV > dev。
    """
    raw = os.environ.get("APP_ENV", "").strip().lower()
    if not raw:
        peek = _peek_app_env_from_dotenv()
        if peek:
            raw = peek
    if not raw:
        raw = "dev"
    if raw not in ALLOWED_ENVS:
        raise RuntimeError(
            f"APP_ENV 非法: {raw!r}（必须为 dev|test|prod）"
        )
    return raw


def _validate_database_url(database_url: str, app_env: str) -> str:
    """校验 DATABASE_URL：必须为 SQLite 文件库，文件名 == hr_db_{env}.db；不一致拒绝启动。"""
    expected = f"hr_db_{app_env}.db"
    parsed = urlparse(database_url)
    if parsed.scheme != "sqlite":
        raise RuntimeError(
            f"DATABASE_URL 仅支持 SQLite（sqlite:///...），当前 scheme={parsed.scheme!r}。"
            f"三环境文件名强制 hr_db_{{env}}.db，与 APP_ENV={app_env!r} 对应，参见 PRD §7D。"
        )
    db_name = Path(unquote(parsed.path or "")).name
    if not db_name or db_name == ":memory:":
        raise RuntimeError(
            f"DATABASE_URL 缺少数据库文件名（APP_ENV={app_env}，期望 {expected}）；"
            f"内存库 :memory: 不支持三环境隔离，禁止使用"
        )
    if db_name != expected:
        raise RuntimeError(
            f"DATABASE_URL 库名 {db_name!r} 与 APP_ENV={app_env!r} 不一致"
            f"（应为 {expected!r}）。三环境文件名强制 hr_db_{{env}}.db，参见 PRD §7D。"
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
    """默认 SQLite 文件库：{PROJECT_ROOT}/data/hr_db_{env}.db（绝对路径，任意 cwd 可运行）。"""
    data_dir = PROJECT_ROOT / "data"
    return f"sqlite:///{data_dir / f'hr_db_{app_env}.db'}"


def _absolutize_sqlite_url(url: str) -> str:
    """把 SQLite 相对路径规范化为项目根绝对路径（任意 cwd 启动行为一致）。

    sqlite:///./data/x.db（相对）→ sqlite:////{PROJECT_ROOT}/data/x.db；
    绝对路径 / 内存库原样返回。
    """
    u = make_url(url)
    if u.drivername.startswith("sqlite"):
        db_path = u.database
        if db_path and db_path != ":memory:":
            p = Path(db_path)
            if not p.is_absolute():
                u = u.set(database=str(PROJECT_ROOT / p))
                url = str(u)
    return url


def _resolve_database_url() -> tuple[str, str, list[str]]:
    """确定 (DATABASE_URL, APP_ENV, loaded_env_files)。

    支持合并 .env：
      - 若显式 DATABASE_URL 已设（shell/.env 直接写），直接使用
      - 否则若存在 DATABASE_URL_{env}（如 DATABASE_URL_prod），取该值并展开 ${VAR}
      - 否则回退到默认拼接
    """
    app_env = get_app_env()
    loaded = _load_env_file(app_env)
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        url = _expand_env(url)
    else:
        per_env_key = f"DATABASE_URL_{app_env}"
        per_env_val = os.environ.get(per_env_key, "").strip()
        if per_env_val:
            url = _expand_env(per_env_val)
        else:
            url = _default_url(app_env)
    url = _absolutize_sqlite_url(url)
    # 将解析后的 URL 同步为 DATABASE_URL，便于后续代码/日志一致
    os.environ["DATABASE_URL"] = url
    _validate_database_url(url, app_env)
    return url, app_env, loaded


# ---------------------------------------------------------------- 解析（模块导入即生效）
DATABASE_URL, APP_ENV, LOADED_ENV_FILES = _resolve_database_url()
SAFE_DATABASE_URL = _sanitize_url(DATABASE_URL)

# ---------------------------------------------------------------- 引擎
# SQLite 文件的父目录必须存在（sqlite3 不自动建目录）
_db_file = Path(make_url(DATABASE_URL).database or "")
if _db_file and _db_file != Path(":memory:"):
    _db_file.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    # FastAPI 同步端点跑在线程池：允许跨线程复用连接；写锁等待 30s（配合 busy_timeout）
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    """每个新连接注入 PRAGMA 与事务模式。

    - foreign_keys=ON：SQLite 默认**不启用**外键约束，必须显式打开，
      否则 FK / ON DELETE CASCADE / 引用完整性全部失效（关键！）
    - journal_mode=WAL：读写不互斥，并发读性能好
    - busy_timeout：写锁冲突时等待而非立即报 database is locked
    - synchronous=NORMAL：WAL 下的推荐安全档位
    - isolation_level=None：关闭 pysqlite 隐式事务管理，改由下方 begin
      事件显式开启事务（SQLAlchemy 官方配方）
    """
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()
    # 延迟到 cursor 关闭后设置：交给 SQLAlchemy 的 begin 事件管理事务边界
    dbapi_conn.isolation_level = None


@event.listens_for(engine, "begin")
def _tx_begin_immediate(conn):
    """事务统一以 BEGIN IMMEDIATE 开启（v2.5 并发语义修复）。

    pysqlite 遗留模式下 SELECT 不开事务（autocommit 读），
    「先 SELECT 守卫、后 UPDATE 写入」的竞态窗口会双双放行
    （如两 HR 同时认领同一空闲岗 → [200,200] 一人双岗）。
    BEGIN IMMEDIATE 让写锁在事务首条语句（含守卫 SELECT）前取得，
    后到者阻塞（busy_timeout 内等待），拿到锁后读到已提交的新状态
    ——与 PostgreSQL `SELECT … FOR UPDATE` 行锁守卫语义等价。
    """
    conn.exec_driver_sql("BEGIN IMMEDIATE")


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
            f"  生产重置必须走线下备份+受控迁移：先复制 data/hr_db_prod.db 备份"
            f"（含 -wal/-shm 伴生文件，或先 PRAGMA wal_checkpoint(TRUNCATE)），再手工恢复。\n"
            f"  本系统不提供 prod 库清空入口（PRD §7D.3）。"
        )


def startup_banner() -> str:
    """返回启动自检日志文本，供 main.py 打印。"""
    files = ", ".join(LOADED_ENV_FILES) if LOADED_ENV_FILES else "（无）"
    return (
        f"[startup] APP_ENV={APP_ENV}  DB={SAFE_DATABASE_URL}\n"
        f"          env files loaded: {files}"
    )