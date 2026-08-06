"""主数据配置路由（F0）：公司/国家/级别/工作地点/工作范围/法律强制/用工税额。"""
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import get_or_404
from app.models import (
    Company,
    Country,
    EmploymentTaxItem,
    LegalCategoryDef,
    Level,
    PositionNumber,
    PositionType,
    ScopeDef,
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
    WorkLocationCreate,
    WorkLocationOut,
    WorkLocationUpdate,
)

router = APIRouter(prefix="/api", tags=["master-data"])


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
        db.commit()
        db.refresh(obj)
        response.headers["Location"] = f"/api{path}/{obj.id}"
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


# ---------------------------------------------------------------- 对外接口：获取所有隶属公司
@router.get("/public/companies")
def public_companies(db: Session = Depends(get_db)):
    """对外暴露：返回所有隶属公司列表（仅 id + 名称）。"""
    return [{"id": c.id, "name": c.name} for c in db.query(Company).order_by(Company.name).all()]


# ---------------------------------------------------------------- 员工用工税额（按国家）
@router.get("/employment-tax-items", response_model=list[EmploymentTaxItemOut])
def list_tax_items(country_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(EmploymentTaxItem)
    if country_id:
        q = q.filter(EmploymentTaxItem.country_id == country_id)
    items = q.order_by(EmploymentTaxItem.id).all()
    countries = {c.id: c.name for c in db.query(Country).all()}
    return [
        {"id": it.id, "country_id": it.country_id, "country_name": countries.get(it.country_id),
         "item_name": it.item_name, "tax_rate": float(it.tax_rate or 0), "is_active": it.is_active}
        for it in items
    ]


@router.post("/employment-tax-items", response_model=EmploymentTaxItemOut, status_code=201)
def create_tax_item(payload: EmploymentTaxItemCreate, response: Response, db: Session = Depends(get_db)):
    get_or_404(db, Country, payload.country_id, "国家不存在")
    it = EmploymentTaxItem(country_id=payload.country_id, item_name=payload.item_name,
                           tax_rate=payload.tax_rate, is_active=payload.is_active)
    db.add(it)
    db.commit()
    response.headers["Location"] = f"/api/employment-tax-items/{it.id}"
    country = db.get(Country, it.country_id)
    return {"id": it.id, "country_id": it.country_id, "country_name": country.name if country else None,
            "item_name": it.item_name, "tax_rate": float(it.tax_rate or 0), "is_active": it.is_active}


@router.patch("/employment-tax-items/{item_id}", response_model=EmploymentTaxItemOut)
def update_tax_item(item_id: int, payload: EmploymentTaxItemUpdate, db: Session = Depends(get_db)):
    it = get_or_404(db, EmploymentTaxItem, item_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(it, k, v)
    db.commit()
    country = db.get(Country, it.country_id)
    return {"id": it.id, "country_id": it.country_id, "country_name": country.name if country else None,
            "item_name": it.item_name, "tax_rate": float(it.tax_rate or 0), "is_active": it.is_active}


@router.delete("/employment-tax-items/{item_id}")
def delete_tax_item(item_id: int, db: Session = Depends(get_db)):
    it = get_or_404(db, EmploymentTaxItem, item_id)
    db.delete(it)
    db.commit()
    return {"ok": True, "id": item_id}
