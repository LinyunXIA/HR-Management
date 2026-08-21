"""认证路由：登录 / 注册 / 当前用户（PRD §7B）。"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.db import get_db
from app.models import User

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
def login(payload: LoginRequest, db: Session = Depends(get_db)):
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
def register(payload: RegisterRequest, response: Response,
             current: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """注册新用户（需已登录的管理员）。首个用户可无认证注册（种子阶段兜底）。"""
    # 若库中无用户，允许匿名注册首个管理员（便于初始化）
    has_any = db.query(User).count() > 0
    if has_any and current.role != "admin":
        raise HTTPException(403, "仅管理员可注册新用户")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(400, f"用户名已存在: {payload.username}")
    if len(payload.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    user = User(
        username=payload.username.strip(),
        hashed_password=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    response.headers["Location"] = f"/api/v1/users/{user.id}"
    return {"id": user.id, "username": user.username, "role": user.role}


# 允许匿名注册首个用户的备用端点（与上方复用逻辑，避免循环依赖）
@router.post("/auth/register-first", status_code=201)
def register_first(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    if db.query(User).count() > 0:
        raise HTTPException(400, "系统已有用户，请使用 /auth/register（需管理员 Token）")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(400, f"用户名已存在: {payload.username}")
    if len(payload.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    user = User(
        username=payload.username.strip(),
        hashed_password=hash_password(payload.password),
        role=payload.role,
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
