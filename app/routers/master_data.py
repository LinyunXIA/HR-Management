"""主数据配置路由（F0）：公司/国家/级别/工作地点/工作范围/法律强制/用工税额。"""
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from fastapi import Request

from app.auth import get_current_user, require_admin
from app.db import get_db
from app.helpers import get_or_404
from app.limiter import limiter
from app.models import (
    Company,
    Country,
    EmploymentTaxItem,
    LegalCategoryDef,
    Level,
    PositionNumber,
    PositionType,
    ScopeDef,
    TaxZone,
    WorkLocation,
)
from app.schemas import (
    CompanyCreate,
    CompanyOut,
    CompanyUpdate,
    CountryCreate,
    CountryOut,
    CountryUpdate,
    EmploymentTaxItemCreate,
    EmploymentTaxItemOut,
    EmploymentTaxItemUpdate,
    LegalCategoryCreate,
    LegalCategoryOut,
    LegalCategoryUpdate,
    LevelCreate,
    LevelOut,
    LevelUpdate,
    PositionTypeCreate,
    PositionTypeOut,
    PositionTypeUpdate,
    ScopeCreate,
    ScopeOut,
    ScopeUpdate,
    TaxZoneCreate,
    TaxZoneOut,
    TaxZoneUpdate,
    WorkLocationCreate,
    WorkLocationOut,
    WorkLocationUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["master-data"])


def _crud(model, out_schema, create_schema, update_schema, path: str,
          order_by: str = "id", ref_check: Callable[[Session, object], None] | None = None):
    """通用字典 CRUD。"""

    @router.get(path, response_model=list[out_schema])
    def list_items(db: Session = Depends(get_db)):
        return db.query(model).order_by(getattr(model, order_by), model.id).all()

    @router.post(path, response_model=out_schema, status_code=201)
    def create_item(payload: create_schema, response: Response, db: Session = Depends(get_db)):
        obj = model(**payload.model_dump())
        db.add(obj)
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            if "UniqueViolation" in type(e).__mro__[0].__name__ or "unique" in str(e).lower() \
                    or "duplicate key" in str(e).lower():
                raise HTTPException(400, f"记录已存在或违反唯一约束: {e.orig if hasattr(e, 'orig') else e}")
            raise
        db.refresh(obj)
        response.headers["Location"] = f"/api/v1{path}/{obj.id}"
        return obj

    @router.patch(path + "/{obj_id}", response_model=out_schema)
    def update_item(obj_id: int, payload: update_schema, db: Session = Depends(get_db)):
        obj = get_or_404(db, model, obj_id)
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(obj, k, v)
        db.commit()
        db.refresh(obj)
        return obj

    @router.delete(path + "/{obj_id}")
    def delete_item(obj_id: int, db: Session = Depends(get_db)):
        obj = get_or_404(db, model, obj_id)
        # 公司软删除：id 保留，仅标记 is_active=False (closed)，被引用时也允许停用
        if model == Company:
            obj.is_active = False
            db.commit()
            db.refresh(obj)
            return {"ok": True, "id": obj_id, "is_active": False, "status": "closed"}
        if ref_check:
            ref_check(db, obj)
        db.delete(obj)
        db.commit()
        return {"ok": True, "id": obj_id}


# ---------------------------------------------------------------- 各字典
def _company_ref(db: Session, obj: Company):
    used = db.query(PositionNumber).filter(
        (PositionNumber.company_id == obj.id) | (PositionNumber.prev_company_id == obj.id)
    ).first()
    if used:
        raise HTTPException(400, f"公司「{obj.name}」已被岗位引用，禁止删除（可停用）")


def _country_ref(db: Session, obj: Country):
    used = db.query(PositionNumber).filter(PositionNumber.country_id == obj.id).first()
    if used or db.query(EmploymentTaxItem).filter(EmploymentTaxItem.country_id == obj.id).first():
        raise HTTPException(400, f"国家「{obj.name}」已被岗位/税额配置引用，禁止删除")


def _level_ref(db: Session, obj: Level):
    used = db.query(PositionNumber).filter(PositionNumber.level == obj.code).first()
    if used:
        raise HTTPException(400, f"级别「{obj.code}」已被岗位引用，禁止删除")


def _location_ref(db: Session, obj: WorkLocation):
    used = db.query(PositionNumber).filter(PositionNumber.work_location == obj.name).first()
    if used:
        raise HTTPException(400, f"工作地点「{obj.name}」已被岗位引用，禁止删除")


def _scope_ref(db: Session, obj: ScopeDef):
    used = db.query(PositionNumber).filter(PositionNumber.scope == obj.code).first()
    if used:
        raise HTTPException(400, f"工作范围「{obj.label}」已被岗位引用，禁止删除")


def _legal_ref(db: Session, obj: LegalCategoryDef):
    used = db.query(PositionNumber).filter(PositionNumber.legal_category == obj.name).first()
    if used:
        raise HTTPException(400, f"「{obj.name}」已被岗位引用，禁止删除")


def _position_type_ref(db: Session, obj: PositionType):
    used = db.query(PositionNumber).filter(PositionNumber.position_type == obj.name).first()
    if used:
        raise HTTPException(400, f"职位类型「{obj.name}」已被岗位引用，禁止删除")


_crud(Company, CompanyOut, CompanyCreate, CompanyUpdate, "/companies", order_by="name", ref_check=_company_ref)
_crud(Country, CountryOut, CountryCreate, CountryUpdate, "/countries", order_by="name", ref_check=_country_ref)
_crud(Level, LevelOut, LevelCreate, LevelUpdate, "/levels", order_by="sort_order", ref_check=_level_ref)
_crud(WorkLocation, WorkLocationOut, WorkLocationCreate, WorkLocationUpdate,
      "/work-locations", order_by="sort_order", ref_check=_location_ref)
_crud(ScopeDef, ScopeOut, ScopeCreate, ScopeUpdate, "/scopes", order_by="sort_order", ref_check=_scope_ref)
_crud(LegalCategoryDef, LegalCategoryOut, LegalCategoryCreate, LegalCategoryUpdate,
      "/legal-categories", order_by="sort_order", ref_check=_legal_ref)
_crud(PositionType, PositionTypeOut, PositionTypeCreate, PositionTypeUpdate,
      "/position-types", order_by="sort_order", ref_check=_position_type_ref)


# ---------------------------------------------------------------- 对外接口：获取所有隶属公司（PRD §7B 外部 API 需 JWT）
@router.get("/public/companies")
@limiter.limit("60/minute")
def public_companies(request: Request, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    """对外暴露：返回所有隶属公司列表（含状态）。需 JWT。"""
    return [
        {"id": c.id, "name": c.name, "is_active": c.is_active, "status": "opened" if c.is_active else "closed"}
        for c in db.query(Company).order_by(Company.name).all()
    ]


# ---------------------------------------------------------------- 员工用工税额（v2.3：按税区）
@router.get("/employment-tax-items", response_model=list[EmploymentTaxItemOut])
def list_tax_items(country_id: int | None = None, tax_zone_id: int | None = None,
                   db: Session = Depends(get_db)):
    q = db.query(EmploymentTaxItem)
    if tax_zone_id:
        q = q.filter(EmploymentTaxItem.tax_zone_id == tax_zone_id)
    elif country_id:
        q = q.filter(EmploymentTaxItem.country_id == country_id)
    items = q.order_by(EmploymentTaxItem.id).all()
    countries = {c.id: c.name for c in db.query(Country).all()}
    zones = {z.id: z for z in db.query(TaxZone).all()}
    out = []
    for it in items:
        z = zones.get(it.tax_zone_id)
        zone_label = None
        if z:
            cname = countries.get(z.country_id)
            zone_label = f"{cname}·{z.city}" if z.level == "city" and z.city else cname
        out.append({
            "id": it.id, "country_id": it.country_id,
            "tax_zone_id": it.tax_zone_id, "tax_zone_label": zone_label,
            "item_name": it.item_name, "tax_rate": float(it.tax_rate or 0),
            "is_active": it.is_active,
        })
    return out


@router.post("/employment-tax-items", response_model=EmploymentTaxItemOut, status_code=201)
def create_tax_item(payload: EmploymentTaxItemCreate, response: Response,
                    _admin=Depends(require_admin), db: Session = Depends(get_db)):
    if not payload.tax_zone_id and not payload.country_id:
        raise HTTPException(400, "必须指定税区（tax_zone_id）")
    if payload.tax_zone_id and payload.country_id:
        raise HTTPException(400, "tax_zone_id 与 country_id 互斥，仅允许填写其一（推荐 tax_zone_id）")
    if payload.tax_zone_id:
        get_or_404(db, TaxZone, payload.tax_zone_id, "税区不存在")
    else:
        get_or_404(db, Country, payload.country_id, "国家不存在")
    # 互斥：新口径仅用 tax_zone_id，country_id 置空防脏数据
    it = EmploymentTaxItem(
        tax_zone_id=payload.tax_zone_id, country_id=None if payload.tax_zone_id else payload.country_id,
        item_name=payload.item_name, tax_rate=payload.tax_rate, is_active=payload.is_active,
    )
    db.add(it)
    db.commit()
    db.refresh(it)
    response.headers["Location"] = f"/api/v1/employment-tax-items/{it.id}"
    return {"id": it.id, "country_id": it.country_id, "tax_zone_id": it.tax_zone_id,
            "tax_zone_label": None, "item_name": it.item_name,
            "tax_rate": float(it.tax_rate or 0), "is_active": it.is_active}


@router.patch("/employment-tax-items/{item_id}", response_model=EmploymentTaxItemOut)
def update_tax_item(item_id: int, payload: EmploymentTaxItemUpdate,
                    _admin=Depends(require_admin), db: Session = Depends(get_db)):
    it = get_or_404(db, EmploymentTaxItem, item_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(it, k, v)
    db.commit()
    return {"id": it.id, "country_id": it.country_id, "tax_zone_id": it.tax_zone_id,
            "tax_zone_label": None, "item_name": it.item_name,
            "tax_rate": float(it.tax_rate or 0), "is_active": it.is_active}


@router.delete("/employment-tax-items/{item_id}")
def delete_tax_item(item_id: int, _admin=Depends(require_admin), db: Session = Depends(get_db)):
    it = get_or_404(db, EmploymentTaxItem, item_id)
    db.delete(it)
    db.commit()
    return {"ok": True, "id": item_id}


# ---------------------------------------------------------------- 税区挂载点配置（v2.3 F1.6）
def _serialize_zone(db: Session, z: TaxZone) -> dict:
    items = (
        db.query(EmploymentTaxItem)
        .filter(EmploymentTaxItem.tax_zone_id == z.id)
        .order_by(EmploymentTaxItem.id)
        .all()
    )
    return {
        "id": z.id, "level": z.level, "country_id": z.country_id,
        "country_name": z.country.name if z.country else None,
        "city": z.city, "sort_order": z.sort_order,
        "items": [
            {"id": it.id, "country_id": None, "tax_zone_id": z.id, "tax_zone_label": None,
             "item_name": it.item_name, "tax_rate": float(it.tax_rate or 0),
             "is_active": it.is_active}
            for it in items
        ],
    }


@router.get("/tax-zones", response_model=list[TaxZoneOut])
def list_tax_zones(country_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(TaxZone)
    if country_id:
        q = q.filter(TaxZone.country_id == country_id)
    return [_serialize_zone(db, z) for z in q.order_by(TaxZone.sort_order, TaxZone.id).all()]


@router.post("/tax-zones", response_model=TaxZoneOut, status_code=201)
def create_tax_zone(payload: TaxZoneCreate, response: Response,
                    _admin=Depends(require_admin), db: Session = Depends(get_db)):
    """创建税区（国家级或城市级；城市级分拆后该国无国家兜底，同一国家不可同时存在两级）。"""
    if payload.level not in ("country", "city"):
        raise HTTPException(400, "level 仅支持 country / city")
    if payload.level == "city" and not (payload.city or "").strip():
        raise HTTPException(400, "城市级税区必须填写 city")
    get_or_404(db, Country, payload.country_id, "国家不存在")
    dup = (
        db.query(TaxZone)
        .filter(TaxZone.level == payload.level, TaxZone.country_id == payload.country_id,
                TaxZone.city == (payload.city or None))
        .first()
    )
    if dup:
        raise HTTPException(400, f"该税区已存在 (id={dup.id})")
    # PRD §4 F1.6：城市级分拆后该国无国家兜底 — 同一国家不可同时存在 country 与 city 两级
    if payload.level == "country":
        if db.query(TaxZone).filter(TaxZone.country_id == payload.country_id, TaxZone.level == "city").first():
            raise HTTPException(400, "该国家已存在城市级税区，按「城市级分拆后无国家兜底」规则，禁止再建国家级税区（请先删除城市级税区）")
    else:  # city
        if db.query(TaxZone).filter(TaxZone.country_id == payload.country_id, TaxZone.level == "country").first():
            raise HTTPException(400, "该国家已存在国家级税区，按「城市级分拆后无国家兜底」规则，禁止再建城市级税区（请先删除国家级税区）")
    z = TaxZone(level=payload.level, country_id=payload.country_id,
                city=(payload.city or None), sort_order=payload.sort_order or 0)
    db.add(z)
    db.commit()
    db.refresh(z)
    response.headers["Location"] = f"/api/v1/tax-zones/{z.id}"
    return _serialize_zone(db, z)


@router.patch("/tax-zones/{zone_id}", response_model=TaxZoneOut)
def update_tax_zone(zone_id: int, payload: TaxZoneUpdate,
                    _admin=Depends(require_admin), db: Session = Depends(get_db)):
    z = get_or_404(db, TaxZone, zone_id)
    # 若更新涉及 city/level 变更，需同步校验「同一国家不可两级并存」
    # 目前 TaxZoneUpdate 仅含 city/sort_order，level 不可改；city 变更时仍需校验
    if payload.city is not None:
        new_city = payload.city
        # 若从 country 改为 city 语义或 city 名称变更，检查冲突
        if z.level == "country" and new_city:
            raise HTTPException(400, "国家级税区不可设置 city")
        if z.level == "city" and not (new_city or "").strip():
            raise HTTPException(400, "城市级税区必须填写 city")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(z, k, v)
    db.commit()
    db.refresh(z)
    return _serialize_zone(db, z)


@router.delete("/tax-zones/{zone_id}")
def delete_tax_zone(zone_id: int, _admin=Depends(require_admin), db: Session = Depends(get_db)):
    z = get_or_404(db, TaxZone, zone_id)
    used = db.query(EmploymentTaxItem).filter(EmploymentTaxItem.tax_zone_id == z.id).first()
    if used:
        raise HTTPException(400, "该税区下已有税务科目，禁止删除（请先清空科目）")
    db.delete(z)
    db.commit()
    return {"ok": True, "id": zone_id}
