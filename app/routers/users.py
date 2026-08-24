"""用户管理（仅 admin）：建号 + 分配可管法人实体（v2.3 PRD §7B.2）。"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fastapi import Request

from app.auth import API_SCOPES, get_user_api_keys, hash_password, require_admin
from app.db import get_db
from app.limiter import limiter
from app.models import Company, User, UserApiPermission, UserCompany, UserRole, UserType

router = APIRouter(prefix="/api/v1", tags=["users"])


class AdminUserCreate(BaseModel):
    username: str
    password: str
    role: str = "hr"
    user_type: str = "ui"          # ui=仅数据权限 / api=外部API（数据+API 结合）
    company_ids: list[int] = []
    apis: list[str] = []           # api_key 列表（见 app/auth.py::API_SCOPES），仅 user_type=api 生效


class AssignApis(BaseModel):
    apis: list[str]                # 全量覆盖式授权（空数组 = 撤销全部）


class AssignCompanies(BaseModel):
    company_ids: list[int]  # 全量覆盖式分配（传空数组 = 撤销全部可管实体）


def _serialize_user(db: Session, u: User) -> dict:
    companies = [
        {"id": uc.company_id, "name": uc.company.name if uc.company else None}
        for uc in db.query(UserCompany).filter(UserCompany.user_id == u.id).all()
    ]
    utype = u.user_type.value if hasattr(u.user_type, "value") else (u.user_type or "ui")
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role.value if hasattr(u.role, "value") else u.role,
        "user_type": utype,
        "is_active": u.is_active,
        "companies": companies,
        "apis": [{"key": r.api_key, "label": API_SCOPES.get(r.api_key, r.api_key)}
                 for r in db.query(UserApiPermission).filter(UserApiPermission.user_id == u.id).all()],
        "created_at": u.created_at,
    }


@router.get("/admin/users")
def list_admin_users(current: User = Depends(require_admin), db: Session = Depends(get_db)):
    """用户列表（仅 admin），含各自可管实体。"""
    users = db.query(User).order_by(User.id).all()
    return {"total": len(users), "items": [_serialize_user(db, u) for u in users]}


@router.get("/admin/scopes")
def list_api_scopes(_admin: User = Depends(require_admin)):
    """对外 API 权限注册表（单一事实源 = app/auth.py::API_SCOPES），供用户管理页动态渲染复选框。"""
    return [{"key": k, "label": v} for k, v in API_SCOPES.items()]


@router.post("/admin/users", status_code=201)
@limiter.limit("5/minute")
def create_user(request: Request, payload: AdminUserCreate, response: Response,
                current: User = Depends(require_admin), db: Session = Depends(get_db)):
    """建号并分配可管公司（关闭自主注册，仅 admin）。"""
    if payload.role not in ("admin", "hr"):
        raise HTTPException(400, "role 仅支持 admin / hr")
    try:
        utype = UserType(payload.user_type.lower())
    except ValueError:
        raise HTTPException(400, "user_type 仅支持 ui / api")
    if db.query(User).filter(User.username == payload.username.strip()).first():
        raise HTTPException(400, f"用户名已存在: {payload.username}")
    if len(payload.password) < 6:
        raise HTTPException(400, "密码至少 6 位")

    user = User(
        username=payload.username.strip(),
        hashed_password=hash_password(payload.password),
        role=UserRole(payload.role),
        user_type=utype,
        is_active=True,
    )
    db.add(user)
    db.flush()
    _assign_companies(db, user, payload.company_ids)
    if utype == UserType.API:
        _assign_apis(db, user, payload.apis)
    db.commit()
    db.refresh(user)
    response.headers["Location"] = f"/api/v1/admin/users/{user.id}"
    return _serialize_user(db, user)


@router.put("/admin/users/{user_id}/apis")
def assign_apis(user_id: int, payload: AssignApis,
                current: User = Depends(require_admin), db: Session = Depends(get_db)):
    """全量覆盖外部 API 用户的接口授权（v2.4.3；UI 用户无 API 权限概念）。"""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, f"用户不存在 (id={user_id})")
    utype = user.user_type.value if hasattr(user.user_type, "value") else str(user.user_type)
    if utype != "api":
        raise HTTPException(400, "UI 类型用户不持有 API 权限（仅数据权限）")
    _assign_apis(db, user, payload.apis)
    db.commit()
    return _serialize_user(db, user)


@router.patch("/admin/users/{user_id}/type")
def update_user_type(user_id: int, payload: dict,
                     current: User = Depends(require_admin), db: Session = Depends(get_db)):
    """切换账号类型（v2.4.3）：ui ↔ api；改回 ui 时清空全部 API 授权。"""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, f"用户不存在 (id={user_id})")
    try:
        utype = UserType(str(payload.get("user_type", "")).lower())
    except ValueError:
        raise HTTPException(400, "user_type 仅支持 ui / api")
    user.user_type = utype
    if utype == UserType.UI:
        db.query(UserApiPermission).filter(UserApiPermission.user_id == user.id).delete()
    db.commit()
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


def _assign_apis(db: Session, user: User, api_keys: list[str]):
    """全量覆盖外部 API 用户的接口授权（校验 api_key 在 API_SCOPES 注册表内）。"""
    for k in dict.fromkeys(api_keys or []):
        if k not in API_SCOPES:
            raise HTTPException(400, f"未知 API 权限: {k}（可用：{', '.join(API_SCOPES)}）")
    db.query(UserApiPermission).filter(UserApiPermission.user_id == user.id).delete()
    for k in dict.fromkeys(api_keys or []):
        db.add(UserApiPermission(user_id=user.id, api_key=k))
