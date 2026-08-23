"""JWT 认证与密码哈希（PRD §7B）。

- 密码：bcrypt 哈希
- Token：PyJWT，HS256，payload 含 sub/role/exp
- 依赖：get_current_user（外部 API 强制）/ get_current_user_optional（内部可选）
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


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """内部接口可选认证：有 Token 则校验，无则放行（返回 None）。"""
    token = _extract_token(request)
    if not token:
        return None
    try:
        payload = decode_token(token)
    except HTTPException:
        # 内部接口：Token 无效时仍抛 401，避免静默放行过期 Token 误用
        raise
    username = payload.get("sub")
    if not username:
        return None
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(401, "用户不存在或已停用")
    return user


def require_admin(current: User = Depends(get_current_user)) -> User:
    """仅 admin 可操作（建号 / 分配可管实体 / 主数据维护等）。"""
    if current.role != "admin":
        raise HTTPException(403, "仅管理员可执行此操作")
    return current


def require_user(current: User = Depends(get_current_user)) -> User:
    """写操作统一要求登录（admin 或 hr）。"""
    return current
