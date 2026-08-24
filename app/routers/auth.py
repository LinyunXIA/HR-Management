"""认证路由：登录 / 当前用户（PRD §7B）。

v2.4.3：外部 API 能力边界收敛——仅「认证（登录）」与已授权的外部 API；
建号统一走内部管理接口 /admin/users（不对任何外部账号开放），/auth/register、
/users 等遗留端点已移除。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.limiter import limiter

from app.auth import create_access_token, get_current_user, verify_password
from app.db import get_db
from app.models import User

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


@router.post("/auth/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(401, "账号已停用")
    # v2.4.3：外部 API 用户的「认证」是可分配的 API 权限——未授予则拒绝换取 JWT
    from app.auth import can_login
    if not can_login(db, user):
        raise HTTPException(403, "该账号未被授予「认证」API 权限，无法登录")
    token = create_access_token(username=user.username, role=user.role)
    # 从 JWT 解出过期秒数（依赖默认配置 720m）
    from app.auth import JWT_EXPIRE_MINUTES
    return TokenResponse(
        access_token=token,
        username=user.username,
        role=user.role,
        expires_in=JWT_EXPIRE_MINUTES * 60,
    )


@router.get("/auth/me")
def me(current: User = Depends(get_current_user)):
    return {"id": current.id, "username": current.username, "role": current.role,
            "user_type": (current.user_type.value if hasattr(current.user_type, "value") else None),
            "is_active": current.is_active}
