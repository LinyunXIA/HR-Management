"""认证路由：登录 / 当前用户（PRD §7B）。

v2.4.3：外部 API 能力边界收敛——仅「认证（登录）」与已授权的外部 API；
建号统一走内部管理接口 /admin/users（不对任何外部账号开放），/auth/register、
/users 等遗留端点已移除。

v2.5 登录入口拆分：
- POST /auth/login    程序化登录（外部 API 接入）：API 类型用户须持「认证」授权
- POST /auth/ui-login Web 界面专用登录：仅 UI 类型账号，API 账号一律 403
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.limiter import limiter

from app.auth import create_access_token, get_current_user, verify_password
from app.db import get_db
from app.models import User, UserType

router = APIRouter(prefix="/api/v1", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str
    expires_in: int  # seconds


def _authenticate(db: Session, username: str, password: str) -> User:
    """共享凭据校验：401 用户名/密码错误、401 账号停用。"""
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(401, "账号已停用")
    return user


def _issue_token(user: User) -> TokenResponse:
    token = create_access_token(username=user.username, role=user.role)
    from app.auth import JWT_EXPIRE_MINUTES
    return TokenResponse(
        access_token=token,
        username=user.username,
        role=user.role,
        expires_in=JWT_EXPIRE_MINUTES * 60,
    )


def _is_api_user(user: User) -> bool:
    ut = user.user_type.value if hasattr(user.user_type, "value") else str(user.user_type or "")
    return ut.lower() == UserType.API.value.lower()


@router.post("/auth/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    """程序化登录（外部 API 接入）。API 类型用户须持「认证」授权换取 JWT。"""
    user = _authenticate(db, payload.username, payload.password)
    # v2.4.3：外部 API 用户的「认证」是可分配的 API 权限——未授予则拒绝换取 JWT
    from app.auth import can_login
    if not can_login(db, user):
        raise HTTPException(403, "该账号未被授予「认证」API 权限，无法登录")
    return _issue_token(user)


@router.post("/auth/ui-login", response_model=TokenResponse)
@limiter.limit("10/minute")
def ui_login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    """Web 界面专用登录。仅 UI 类型账号；API 账号不支持网页界面登录（403）。

    外部集成账号即使持有「认证」授权，也只能经 /auth/login 程序化接入，
    防止其进入内部管理系统 UI。
    """
    user = _authenticate(db, payload.username, payload.password)
    if _is_api_user(user):
        raise HTTPException(
            403,
            "API 账号不支持网页界面登录：请通过外部 API 方式接入"
            "（POST /auth/login，须持「认证」授权）",
        )
    return _issue_token(user)


@router.get("/auth/me")
def me(current: User = Depends(get_current_user)):
    return {"id": current.id, "username": current.username, "role": current.role,
            "user_type": (current.user_type.value if hasattr(current.user_type, "value") else None),
            "is_active": current.is_active}
