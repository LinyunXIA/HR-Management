"""JWT 认证与密码哈希（PRD §7B）。

- 密码：bcrypt 哈希
- Token：PyJWT，HS256，payload 含 sub/role/exp
- 依赖：get_current_user（全接口强制；可选版已随 #128 清理移除）
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User

# ---------------------------------------------------------------- 配置
_DEFAULT_JWT_SECRET = "dev-only-secret-please-change-in-prod-32+chars"
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", _DEFAULT_JWT_SECRET)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "720"))  # 默认 12h


def validate_prod_config(env: str | None = None) -> None:
    """生产环境安全配置校验（PRD §10 Item 6，issue #70）。

    由 main.py 启动时**显式调用**（而非模块导入期隐式触发），
    消除对 app.db.APP_ENV 导入顺序的耦合——重构导入顺序不再可能
    静默跳过生产安全校验。校验失败抛 RuntimeError 阻断启动。
    """
    if env is None:
        from app.db import APP_ENV  # 函数内导入：无循环依赖、无顺序要求
        env = APP_ENV
    if env != "prod":
        return
    if JWT_SECRET_KEY == _DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "[FATAL] JWT_SECRET_KEY 未覆盖：生产环境必须设置强随机 JWT_SECRET_KEY（≥32字符），"
            "参见 .env.example"
        )
    if len(JWT_SECRET_KEY) < 32:
        raise RuntimeError("[FATAL] JWT_SECRET_KEY 过短：生产环境要求 ≥32 字符")

# 形如 Authorization: Bearer <token>  或  X-Token: <token>（兼容内部 Token 头）
BEARER_PREFIX = "Bearer "


# ---------------------------------------------------------------- 密码
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


# ---------------------------------------------------------------- Token
def create_access_token(*, username: str, role: str = "admin",
                        expires_minutes: int | None = None) -> str:
    exp = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes if expires_minutes is not None else JWT_EXPIRE_MINUTES
    )
    payload = {"sub": username, "role": role, "exp": exp}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token 已过期，请重新登录")
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, f"无效 Token: {e}")


def _extract_token(request: Request) -> Optional[str]:
    # 1) Authorization: Bearer <token>
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if auth.startswith(BEARER_PREFIX):
        return auth[len(BEARER_PREFIX):].strip()
    # 2) X-Token: <token>（内部简单 Token 兼容）
    xt = request.headers.get("x-token") or request.headers.get("X-Token") or ""
    if xt:
        return xt.strip()
    # 3) query ?token=（便于 curl 调试）
    q = request.query_params.get("token")
    if q:
        return q.strip()
    return None


# ---------------------------------------------------------------- 依赖
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """外部 API 强制认证：无 Token 或过期返回 401。"""
    token = _extract_token(request)
    if not token:
        raise HTTPException(401, "未提供认证 Token（请在 Authorization: Bearer <token> 头中携带）")
    payload = decode_token(token)
    username = payload.get("sub")
    if not username:
        raise HTTPException(401, "Token 缺少用户信息")
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(401, "用户不存在或已停用")
    return user


# ---------------------------------------------------------------- API 权限（v2.4.3）
# 对外 API 授权注册表（单一事实源）：key 存 user_apis.api_key，value 为展示名。
# 新增外部 API 在此登记并挂 require_api_scope。
API_SCOPES = {
    "auth.login": "认证（登录换取 JWT）",
    "public.companies": "获取隶属公司列表",
    "public.levels": "获取级别字典",
    "public.positions": "获取在岗岗位数据（第三方用工成本计算用）",
}


def get_user_api_keys(db: Session, user: User) -> list[str]:
    """用户的已授权 api_key 列表（admin 角色视为全量）。"""
    from app.models import UserApiPermission, UserType
    if getattr(user, "role", None) and (
            (user.role.value if hasattr(user.role, "value") else str(user.role)) == "admin"):
        return list(API_SCOPES.keys())
    rows = db.query(UserApiPermission).filter(UserApiPermission.user_id == user.id).all()
    return [r.api_key for r in rows]


def has_api_scope(db: Session, user: User, api_key: str) -> bool:
    """UI 类型用户不持有任何 API 权限；仅 admin 与获授权的 API 类型用户放行。"""
    from app.models import UserType
    if (user.role.value if hasattr(user.role, "value") else str(user.role)) == "admin":
        return True
    if user.user_type != UserType.API:
        return False
    return api_key in get_user_api_keys(db, user)


def can_login(db: Session, user: User) -> bool:
    """登录门槛（v2.4.3）：仅外部 API 用户须持「认证」授权；admin / UI 用户天然可登录。"""
    from app.models import UserType
    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    if role == "admin" or user.user_type != UserType.API:
        return True
    return "auth.login" in get_user_api_keys(db, user)


def require_api_scope(api_key: str):
    """依赖工厂：要求当前 JWT 用户具备指定 API 权限（v2.4.3）。

    - admin 角色：全量放行
    - UI 用户：403（仅数据权限，不含 API 权限）
    - API 用户：须在用户管理中被授予该 api_key
    """
    def dep(current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        if not has_api_scope(db, current, api_key):
            label = API_SCOPES.get(api_key, api_key)
            raise HTTPException(403, f"该账号未被授予 API 权限「{label}」（{api_key}）")
        return current
    return dep


def require_admin(current: User = Depends(get_current_user)) -> User:
    """仅内部 admin 可操作（建号 / 分配可管实体 / 主数据维护等）。

    v2.4.3：外部 API 类型账号一律拒绝——外部只能登录与调用其已授权的外部 API，
    用户管理/注册类接口不对任何 API 账号开放（即使角色为 admin，属配置错误场景）。
    """
    ut = current.user_type.value if hasattr(current.user_type, "value") else str(current.user_type)
    if ut == "api":
        raise HTTPException(403, "外部 API 账号不可访问内部管理接口（仅可登录与调用已授权的外部 API）")
    if current.role != "admin":
        raise HTTPException(403, "仅管理员可执行此操作")
    return current


def require_user(current: User = Depends(get_current_user)) -> User:
    """写操作统一要求登录（admin 或 hr）。"""
    return current
