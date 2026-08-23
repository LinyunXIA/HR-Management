"""用户管理（仅 admin）：建号 + 分配可管法人实体（v2.3 PRD §7B.2）。"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import hash_password, require_admin
from app.db import get_db
from app.models import Company, User, UserCompany, UserRole

router = APIRouter(prefix="/api/v1", tags=["users"])


class AdminUserCreate(BaseModel):
    username: str
    password: str
    role: str = "hr"
    company_ids: list[int] = []


class AssignCompanies(BaseModel):
    company_ids: list[int]  # 全量覆盖式分配（传空数组 = 撤销全部可管实体）


def _serialize_user(db: Session, u: User) -> dict:
    companies = [
        {"id": uc.company_id, "name": uc.company.name if uc.company else None}
        for uc in db.query(UserCompany).filter(UserCompany.user_id == u.id).all()
    ]
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role.value if hasattr(u.role, "value") else u.role,
        "is_active": u.is_active,
        "companies": companies,
        "created_at": u.created_at,
    }


@router.get("/admin/users")
def list_admin_users(current: User = Depends(require_admin), db: Session = Depends(get_db)):
    """用户列表（仅 admin），含各自可管实体。"""
    users = db.query(User).order_by(User.id).all()
    return {"total": len(users), "items": [_serialize_user(db, u) for u in users]}


@router.post("/admin/users", status_code=201)
def create_user(payload: AdminUserCreate, response: Response,
                current: User = Depends(require_admin), db: Session = Depends(get_db)):
    """建号并分配可管公司（关闭自主注册，仅 admin）。"""
    if payload.role not in ("admin", "hr"):
        raise HTTPException(400, "role 仅支持 admin / hr")
    if db.query(User).filter(User.username == payload.username.strip()).first():
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
    db.flush()
    _assign_companies(db, user, payload.company_ids)
    db.commit()
    db.refresh(user)
    response.headers["Location"] = f"/api/v1/admin/users/{user.id}"
    return _serialize_user(db, user)


@router.post("/admin/users/{user_id}/companies")
def assign_companies(user_id: int, payload: AssignCompanies,
                     current: User = Depends(require_admin), db: Session = Depends(get_db)):
    """给 hr 分配/撤销可管实体（全量覆盖；仅 hr 生效，admin 自带全司忽略）。"""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, f"用户不存在 (id={user_id})")
    _assign_companies(db, user, payload.company_ids)
    db.commit()
    return _serialize_user(db, user)


@router.patch("/admin/users/{user_id}/active")
def toggle_active(user_id: int, payload: dict,
                  current: User = Depends(require_admin), db: Session = Depends(get_db)):
    """启用/停用账号。"""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, f"用户不存在 (id={user_id})")
    if user.id == current.id:
        raise HTTPException(400, "不能停用自己")
    active = bool(payload.get("is_active", True))
    user.is_active = active
    db.commit()
    return _serialize_user(db, user)


def _assign_companies(db: Session, user: User, company_ids: list[int]):
    """全量覆盖用户的可管实体绑定（校验公司存在；admin 角色不落记录——自带全司）。"""
    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    if role != "hr":
        return
    for cid in dict.fromkeys(company_ids or []):  # 去重保序
        if not db.get(Company, cid):
            raise HTTPException(400, f"隶属公司不存在 (id={cid})")
    db.query(UserCompany).filter(UserCompany.user_id == user.id).delete()
    for cid in dict.fromkeys(company_ids or []):
        db.add(UserCompany(user_id=user.id, company_id=cid))
