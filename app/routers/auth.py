"""认证路由：登录 / 注册 / 当前用户（PRD §7B）。"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.limiter import limiter

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.db import get_db
from app.models import User, UserRole

router = APIRouter(prefix="/api/v1", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "admin"


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
    token = create_access_token(username=user.username, role=user.role)
    # 从 JWT 解出过期秒数（依赖默认配置 720m）
    from app.auth import JWT_EXPIRE_MINUTES
    return TokenResponse(
        access_token=token,
        username=user.username,
        role=user.role,
        expires_in=JWT_EXPIRE_MINUTES * 60,
    )


@router.post("/auth/register", status_code=201)
@limiter.limit("5/minute")
def register(request: Request, payload: RegisterRequest, response: Response,
             current: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """建号（v2.3：关闭自主注册，仅 admin 可调用；角色限 admin/hr）。"""
    if current.role != "admin":
        raise HTTPException(403, "仅管理员可创建账号")
    if payload.role not in ("admin", "hr"):
        raise HTTPException(400, "role 仅支持 admin / hr")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(400, f"用户名已存在: {payload.username}")
    if len(payload.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    user = User(
        username=payload.username.strip(),
        hashed_password=hash_password(payload.password),
        role=UserRole(payload.role),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    response.headers["Location"] = f"/api/v1/users/{user.id}"
    return {"id": user.id, "username": user.username, "role": user.role}


@router.get("/auth/me")
def me(current: User = Depends(get_current_user)):
    return {"id": current.id, "username": current.username, "role": current.role, "is_active": current.is_active}


@router.get("/users")
def list_users(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current.role != "admin":
        raise HTTPException(403, "仅管理员可查看用户列表")
    users = db.query(User).order_by(User.id).all()
    return [{"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active,
             "created_at": u.created_at} for u in users]
