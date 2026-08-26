"""主数据配置路由（F0）：公司/国家/级别/工作地点/工作范围/法律强制/用工税额。"""
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from fastapi import Request

from app.auth import get_current_user, require_admin, require_api_scope
from app.db import get_db
from app.helpers import get_or_404
from app.limiter import RATE_LIMIT_PUBLIC, limiter
from app.models import (
    Company,
    CompanyShareholder,
    Country,
    Employee,
    EmploymentTaxItem,
    ExternalCompany,
    LegalCategoryDef,
    Level,
    PositionNumber,
    PositionNumberDottedLine,
    PositionStatus,
    PositionType,
    ScopeDef,
    TaxZone,
    User,
    UserCompany,
    WorkLocation,
)
from app.schemas import (
    CompanyCreate,
    CompanyOut,
    CompanyShareholderIn,
    CompanyUpdate,
    CountryCreate,
    CountryOut,
    CountryUpdate,
    EmploymentTaxItemCreate,
    EmploymentTaxItemOut,
    EmploymentTaxItemUpdate,
    ExternalCompanyCreate,
    ExternalCompanyOut,
    ExternalCompanyUpdate,
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
    def list_items(_user=Depends(get_current_user), db: Session = Depends(get_db)):
        return db.query(model).order_by(getattr(model, order_by), model.id).all()

    @router.post(path, response_model=out_schema, status_code=201)
    def create_item(payload: create_schema, response: Response,
                    _admin=Depends(require_admin), db: Session = Depends(get_db)):
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
    def update_item(obj_id: int, payload: update_schema,
                    _admin=Depends(require_admin), db: Session = Depends(get_db)):
        obj = get_or_404(db, model, obj_id)
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(obj, k, v)
        db.commit()
        db.refresh(obj)
        return obj

    @router.delete(path + "/{obj_id}")
    def delete_item(obj_id: int, _admin=Depends(require_admin), db: Session = Depends(get_db)):
        obj = get_or_404(db, model, obj_id)
        if ref_check:
            ref_check(db, obj)
        db.delete(obj)
        db.commit()
        return {"ok": True, "id": obj_id}


# ---------------------------------------------------------------- 各字典
# ---------------------------------------------------------------- 隶属公司（v2.4 专用路由：开业/关闭日期 + 股权结构）
def _serialize_shareholder(sh: CompanyShareholder) -> dict:
    return {
        "id": sh.id,
        "internal_company_id": sh.internal_company_id,
        "internal_company_name": sh.internal_company.name if sh.internal_company else None,
        "external_company_id": sh.external_company_id,
        "external_company_name": sh.external_company.name if sh.external_company else None,
        "person_name": sh.person_name,
        "ownership_pct": float(sh.ownership_pct) if sh.ownership_pct is not None else None,
        "sort_order": sh.sort_order,
    }


def _tax_zone_label(db: Session, zone: TaxZone | None) -> str | None:
    """税区展示标签：国家·城市（级别）。"""
    if not zone:
        return None
    cname = zone.country.name if zone.country else "?"
    base = f"{cname}·{zone.city}" if zone.level == "city" and zone.city else cname
    return f"{base}（{'城市级' if zone.level == 'city' else '国家级'}）"


def _serialize_company(db: Session, company: Company) -> dict:
    zone = company.tax_zone
    return {
        "id": company.id,
        "name": company.name,
        "is_active": company.is_active,
        "opening_date": company.opening_date.isoformat() if company.opening_date else None,
        "closing_date": company.closing_date.isoformat() if company.closing_date else None,
        "tax_zone_id": company.tax_zone_id,
        "tax_zone_label": _tax_zone_label(db, zone),
        "shareholders": [_serialize_shareholder(sh) for sh in (company.shareholders or [])],
    }


def _assert_tax_zone(db: Session, tax_zone_id: int | None) -> None:
    if tax_zone_id is not None:
        get_or_404(db, TaxZone, tax_zone_id, f"税区不存在 (id={tax_zone_id})")


def _validate_shareholders(db: Session, rows: list[CompanyShareholderIn],
                           self_id: int | None = None) -> list[CompanyShareholder]:
    """股东行校验：三来源互斥、内部股东拒自环、引用存在性、同源去重。"""
    out: list[CompanyShareholder] = []
    seen_internal, seen_external, seen_person = set(), set(), set()
    for i, row in enumerate(rows):
        sources = [row.internal_company_id, row.external_company_id,
                   (row.person_name or "").strip() or None]
        if sum(1 for v in sources if v not in (None, "")) != 1:
            raise HTTPException(400, f"第 {i + 1} 行股东：内部公司 / 外部合作公司 / 自然人必须恰好填写其一")
        pct = row.ownership_pct
        if pct is not None and not (0 < pct <= 100):
            raise HTTPException(400, f"第 {i + 1} 行股东：持股比例须在 (0, 100] 区间")
        if row.internal_company_id is not None:
            if self_id is not None and row.internal_company_id == self_id:
                raise HTTPException(400, "股权结构不允许自环：公司不能以自身为股东")
            if row.internal_company_id in seen_internal:
                raise HTTPException(400, f"同一内部公司股东重复出现（id={row.internal_company_id}）")
            seen_internal.add(row.internal_company_id)
            if not db.get(Company, row.internal_company_id):
                raise HTTPException(400, f"内部公司不存在（id={row.internal_company_id}）")
        if row.external_company_id is not None:
            if row.external_company_id in seen_external:
                raise HTTPException(400, f"同一外部合作公司股东重复出现（id={row.external_company_id}）")
            seen_external.add(row.external_company_id)
            if not db.get(ExternalCompany, row.external_company_id):
                raise HTTPException(400, f"外部合作公司不存在（id={row.external_company_id}），请先在主数据中维护")
        if sources[2] is not None:
            key = sources[2]
            if key in seen_person:
                raise HTTPException(400, f"自然人股东「{key}」重复出现")
            seen_person.add(key)
        out.append(CompanyShareholder(
            internal_company_id=row.internal_company_id,
            external_company_id=row.external_company_id,
            person_name=sources[2],
            ownership_pct=pct,
            sort_order=row.sort_order if row.sort_order else i,
        ))
    return out


def _check_ownership_cycle(db: Session, company: Company) -> None:
    """沿内部公司股东链上溯环检测（A→B→A 拒绝）。

    基于会话当前（含本次 replace-all 后、commit 前）状态；新环必经过本公司，
    故从本公司出发 DFS 即可覆盖。
    """
    target = company.id
    seen: set[int] = set()
    stack = [target]
    while stack:
        cid = stack.pop()
        if cid == target and seen:
            raise HTTPException(400, "股权结构存在环路（A→B→A），请检查内部公司股东链")
        if cid in seen:
            continue
        seen.add(cid)
        owners = db.query(CompanyShareholder.internal_company_id).filter(
            CompanyShareholder.company_id == cid,
            CompanyShareholder.internal_company_id.isnot(None),
        ).all()
        stack.extend(o[0] for o in owners)


def _pct_warning(rows: list[CompanyShareholder]) -> str | None:
    """pct 合计 ≠100% 软校验（不拦截保存，仅告警）。"""
    pcts = [float(r.ownership_pct) for r in rows if r.ownership_pct is not None]
    if pcts and abs(sum(pcts) - 100) > 0.005:
        return f"股东持股比例合计为 {sum(pcts):g}%（≠100%），请确认"
    return None


def _assert_company_closable(db: Session, company_id: int) -> None:
    """关闭前置校验：公司名下全部岗位须为 Closed 才允许设置关闭日期（v2.4.1）。"""
    n = db.query(PositionNumber).filter(
        PositionNumber.company_id == company_id,
        PositionNumber.status != PositionStatus.CLOSED.name,
    ).count()
    if n:
        raise HTTPException(
            400, f"该公司仍有 {n} 个岗位未关闭（仅当名下全部岗位均为「关闭」后才可设置关闭日期）")


@router.get("/companies", response_model=list[CompanyOut])
def list_companies(_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return [_serialize_company(db, c) for c in
            db.query(Company).order_by(Company.name, Company.id).all()]


@router.post("/companies", status_code=201)
def create_company(payload: CompanyCreate, response: Response,
                   _admin=Depends(require_admin), db: Session = Depends(get_db)):
    _assert_tax_zone(db, payload.tax_zone_id)
    company = Company(
        name=payload.name,
        opening_date=payload.opening_date,
        closing_date=payload.closing_date,
        tax_zone_id=payload.tax_zone_id,
        # 填关闭日 ⇔ 自动置停用（PRD F0.1 联动）
        is_active=False if payload.closing_date else (payload.is_active if payload.is_active is not None else True),
    )
    db.add(company)
    try:
        db.flush()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"公司创建失败（名称重复或约束冲突）: {e}")
    if payload.shareholders is not None:
        company.shareholders = _validate_shareholders(db, payload.shareholders, self_id=company.id)
        db.flush()  # 先落会话再查链，保证环检测基于本次提交后的全图
        _check_ownership_cycle(db, company)
    if payload.closing_date:
        _assert_company_closable(db, company.id)
    db.commit()
    db.refresh(company)
    response.headers["Location"] = f"/api/v1/companies/{company.id}"
    body = _serialize_company(db, company)
    warning = _pct_warning(company.shareholders or [])
    if warning:
        body["warning"] = warning
    return body


@router.patch("/companies/{company_id}", response_model=None)
def update_company(company_id: int, payload: CompanyUpdate,
                   _admin=Depends(require_admin), db: Session = Depends(get_db)):
    company = get_or_404(db, Company, company_id)
    data = payload.model_dump(exclude_unset=True)
    closing_changed = "closing_date" in data
    # shareholders 由下方专用逻辑处理（model_dump 产出 dict，不可直接塞给 relationship）
    for k, v in {k2: v2 for k2, v2 in data.items() if k2 != "shareholders"}.items():
        setattr(company, k, v)
    if "tax_zone_id" in data:
        _assert_tax_zone(db, data["tax_zone_id"])
    # closing_date ↔ is_active 联动（issue #146 补口）：最终态由 closing_date 派生——
    # 有关闭日强制停用（显式传 is_active=true 亦不可绕过「有关闭日却启用」矛盾态）；
    # 清空关闭日恢复启用
    if company.closing_date is not None:
        company.is_active = False
    elif closing_changed or "is_active" in data:
        company.is_active = company.closing_date is None
    if company.closing_date and closing_changed:
        _assert_company_closable(db, company.id)
    if payload.shareholders is not None:
        company.shareholders = []  # 先删旧行再插新行，避免 replace-all 撞 Unique(company_id, *_id)
        db.flush()
        company.shareholders = _validate_shareholders(db, payload.shareholders, self_id=company.id)
        db.flush()  # 先落会话再查链，保证环检测基于本次提交后的全图
        _check_ownership_cycle(db, company)
    db.commit()
    db.refresh(company)
    body = _serialize_company(db, company)
    warning = _pct_warning(company.shareholders or [])
    if warning:
        body["warning"] = warning
    return body


@router.delete("/companies/{company_id}")
def delete_company(company_id: int, db: Session = Depends(get_db),
                   _admin=Depends(require_admin)):
    """物理删除（v2.4.1）：被岗位/股权/转调目标/HR 绑定引用时禁止，需先解除。仅 admin（#95）。"""
    company = get_or_404(db, Company, company_id)
    n_pos = db.query(PositionNumber).filter(
        (PositionNumber.company_id == company_id)
        | (PositionNumber.prev_company_id == company_id)).count()
    if n_pos:
        raise HTTPException(400, f"公司「{company.name}」已被 {n_pos} 个岗位（隶属/转岗来源）引用，禁止删除")
    n_sh = db.query(CompanyShareholder).filter(
        CompanyShareholder.internal_company_id == company_id).count()
    if n_sh:
        raise HTTPException(400, f"公司「{company.name}」被 {n_sh} 条股权结构记录作为内部股东引用，禁止删除")
    n_emp = db.query(Employee).filter(Employee.target_company_id == company_id).count()
    if n_emp:
        raise HTTPException(400, f"公司「{company.name}」是 {n_emp} 名「转调中」员工的目标公司，禁止删除")
    n_uc = db.query(UserCompany).filter(UserCompany.company_id == company_id).count()
    if n_uc:
        raise HTTPException(400, f"公司「{company.name}」绑定了 {n_uc} 个 HR 账号的可管实体，请先在用户管理中解除")
    db.delete(company)  # 自身股东行由 ORM cascade 一并删除
    db.commit()
    return {"ok": True, "id": company_id, "deleted": True}


def _external_company_ref(db: Session, obj: ExternalCompany):
    used = db.query(CompanyShareholder).filter(
        CompanyShareholder.external_company_id == obj.id).first()
    if used:
        raise HTTPException(400, f"外部合作公司「{obj.name}」已被股权结构引用，禁止删除")


# ---------------------------------------------------------------- 外部合作公司（v2.4.1：关闭日期管理启停，弃用启用开关）
@router.get("/external-companies", response_model=list[ExternalCompanyOut])
def list_external_companies(_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(ExternalCompany).order_by(ExternalCompany.name, ExternalCompany.id).all()


@router.post("/external-companies", response_model=ExternalCompanyOut, status_code=201)
def create_external_company(payload: ExternalCompanyCreate, response: Response,
                            _admin=Depends(require_admin), db: Session = Depends(get_db)):
    obj = ExternalCompany(
        name=payload.name,
        remark=payload.remark,
        opening_date=payload.opening_date,
        closing_date=payload.closing_date,
        is_active=False if payload.closing_date else payload.is_active,
    )
    db.add(obj)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"外部合作公司创建失败（名称重复或约束冲突）: {e}")
    db.refresh(obj)
    response.headers["Location"] = f"/api/v1/external-companies/{obj.id}"
    return obj


@router.patch("/external-companies/{obj_id}", response_model=None)
def update_external_company(obj_id: int, payload: ExternalCompanyUpdate,
                            _admin=Depends(require_admin), db: Session = Depends(get_db)):
    obj = get_or_404(db, ExternalCompany, obj_id)
    data = payload.model_dump(exclude_unset=True)
    closing_changed = "closing_date" in data
    for k, v in data.items():
        setattr(obj, k, v)
    # 关闭日期 ↔ 启用联动（issue #146 补口，同 companies）：最终态由 closing_date 派生，
    # 显式 is_active=true 不可绕过「有关闭日却启用」矛盾态
    if obj.closing_date is not None:
        obj.is_active = False
    elif closing_changed or "is_active" in data:
        obj.is_active = obj.closing_date is None
    db.commit()
    db.refresh(obj)
    return {
        "id": obj.id, "name": obj.name, "remark": obj.remark, "is_active": obj.is_active,
        "opening_date": obj.opening_date.isoformat() if obj.opening_date else None,
        "closing_date": obj.closing_date.isoformat() if obj.closing_date else None,
    }


@router.delete("/external-companies/{obj_id}")
def delete_external_company(obj_id: int, db: Session = Depends(get_db),
                            _admin=Depends(require_admin)):
    obj = get_or_404(db, ExternalCompany, obj_id)
    _external_company_ref(db, obj)
    db.delete(obj)
    db.commit()
    return {"ok": True, "id": obj_id}


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


# 公司与外部合作公司均走上方 v2.4 专用路由（开业/关闭日期管理启停；公司含股权结构）
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
@limiter.limit(RATE_LIMIT_PUBLIC)
def public_companies(request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_api_scope("public.companies"))):
    """对外暴露：隶属公司完整信息（v2.4.3：公司ID / 名称 / 开业日期 / 关闭日期 / 股权结构 / 状态）。

    数据权限结合：admin 返回全部；其余按其可管实体绑定过滤。
    """
    from app.helpers import ALL_COMPANIES, get_operable_company_ids
    q = db.query(Company).order_by(Company.name)
    operable = get_operable_company_ids(db, user)
    if operable is not ALL_COMPANIES:  # 非 admin：仅可见可管实体
        q = q.filter(Company.id.in_(operable))
    out = []
    for c in q.all():
        body = _serialize_company(db, c)  # id/name/is_active/opening_date/closing_date/shareholders[]
        body["status"] = "opened" if c.is_active else "closed"
        out.append(body)
    return out


# ---------------------------------------------------------------- 对外接口：获取级别字典（v2.6 外部基准对接）
@router.get("/public/levels")
@limiter.limit(RATE_LIMIT_PUBLIC)
def public_levels(request: Request, db: Session = Depends(get_db),
                  _user: User = Depends(require_api_scope("public.levels"))):
    """对外暴露：级别字典（code/label/is_management），供外部系统按我方 code 下发基准。"""
    from app.models import Level
    return [{"code": lv.code, "label": lv.label, "is_management": lv.is_management}
            for lv in db.query(Level).order_by(Level.sort_order).all()]


# ---------------------------------------------------------------- 对外接口：在岗岗位数据导出（v2.6 R2：第三方计算，不含成本）
def _country_or_region_raw(pn: PositionNumber) -> str:
    """「国家或地区」原始值（与导入 CSV 同构）：Country·X / Family / Global / Regional。"""
    from app.models import Scope
    if pn.scope is None:
        return ""
    if pn.scope == Scope.COUNTRY:
        return f"Country·{pn.country.name}" if pn.country else ""
    return {"family": "Family", "global": "Global", "regional": "Regional"}.get(pn.scope.value, "")


@router.get("/public/positions")
@limiter.limit(RATE_LIMIT_PUBLIC)
def public_positions(request: Request, year: int,
                     company_ids: str | None = None,
                     user: User = Depends(require_api_scope("public.positions")),
                     db: Session = Depends(get_db)):
    """对外暴露：指定年份的在岗岗位明细（**不含任何成本字段**）。

    - 在岗判定（公司/岗位同规则）：opening ≤ Y-12-31 且（closing 空 或 ≥ Y-01-01），
      即与年份至少有一天交集即计入；不看 lifecycle 当前状态。
    - company_ids 缺省（=all）：先筛「该年在营」的公司（同规则作用于公司开业/关闭日；
      开业日未知视为在营），再取其旗下在岗岗位。
    - 传入具体 company_ids（逗号分隔）：仅按 year 过滤这些公司的在岗岗位，
      不做公司在营过滤。
    -     计算由第三方完成：本接口只提供岗位业务字段，成本六栏一律不输出。
    """
    from app.benchmark import active_positions_in_year, year_bounds
    from app.models import Employee

    if not (1900 <= year <= 2999):
        raise HTTPException(400, "year 取值非法（1900~2999）")

    ys, ye = year_bounds(year)

    def _company_alive(cid: int) -> bool:
        c = db.get(Company, cid)
        if not c:
            return False
        o, cl = c.opening_date, c.closing_date
        return (o is None or o <= ye) and (cl is None or cl >= ys)

    if company_ids:
        try:
            ids = sorted({int(x) for x in company_ids.split(",") if x.strip()})
        except ValueError:
            raise HTTPException(400, "company_ids 须为逗号分隔的整数（如 1,2,3）")
        # 指定公司不做存续过滤，仅按年份交集筛岗位
        positions = [
            p for p in db.query(PositionNumber)
            .filter(PositionNumber.company_id.in_(ids)).all()
            if p.opening_date is not None and p.opening_date <= ye
            and (p.closing_date is None or p.closing_date >= ys)
        ]
        company_scope = ids
    else:
        alive_ids = {c.id for c in db.query(Company).all() if _company_alive(c.id)}
        positions = [p for p in active_positions_in_year(db, year)
                     if p.company_id in alive_ids]
        company_scope = None  # all

    items = []
    for pn in sorted(positions, key=lambda p: (p.company_id or 0, p.number)):
        sl = db.get(PositionNumber, pn.solid_line_manager_id) if pn.solid_line_manager_id else None
        dotted_rows = db.query(PositionNumberDottedLine.dotted_manager_id).filter(
            PositionNumberDottedLine.position_number_id == pn.id).all()
        dotted_names = []
        for (mid,) in dotted_rows:
            m = db.get(PositionNumber, mid)
            if m and m.position:
                dotted_names.append(m.position.name)
        incumbent = db.query(Employee).filter(Employee.position_number_id == pn.id).first()
        items.append({
            # 字段集与导入 CSV 列对齐（映射表见 API_PUBLIC.md §3）；无任何成本字段
            "number": pn.number,
            "position_name": (pn.position.name if pn.position else None),
            "position_type": pn.position_type,
            "company_id": pn.company_id,
            "company_name": (pn.company.name if pn.company else None),
            "level": pn.level,
            "country_or_region": _country_or_region_raw(pn),
            "opening_date": (pn.opening_date.isoformat() if pn.opening_date else None),
            "closing_date": (pn.closing_date.isoformat() if pn.closing_date else None),
            "work_location": pn.work_location,
            "job_responsibility": pn.job_responsibility,
            "solid_line_manager": (sl.position.name if sl and sl.position else None),
            "dotted_managers": dotted_names,
            "legal_category": pn.legal_category,
            "org_chart_display": pn.org_chart_display,
            "remark": pn.remark,
            "incumbent_name": (incumbent.name if incumbent else None),
        })

    return {
        "year": year,
        "company_filter": company_scope,
        "total": len(items),
        "items": items,
    }


# ---------------------------------------------------------------- 员工用工税额（v2.3：按税区；v2.6 两类科目）
@router.get("/employment-tax-items", response_model=list[EmploymentTaxItemOut])
def list_tax_items(country_id: int | None = None, tax_zone_id: int | None = None,
                   _user=Depends(get_current_user), db: Session = Depends(get_db)):
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
            "item_name": it.item_name,
            "item_kind": it.item_kind or "rate",
            "tax_rate": float(it.tax_rate or 0),
            "fixed_amount": float(it.fixed_amount) if it.fixed_amount is not None else None,
            "is_active": it.is_active,
        })
    return out


def _serialize_tax_item(it: EmploymentTaxItem) -> dict:
    return {"id": it.id, "country_id": it.country_id,
            "tax_zone_id": it.tax_zone_id, "tax_zone_label": None,
            "item_name": it.item_name,
            "item_kind": it.item_kind or "rate",
            "tax_rate": float(it.tax_rate or 0),
            "fixed_amount": float(it.fixed_amount) if it.fixed_amount is not None else None,
            "is_active": it.is_active}


@router.post("/employment-tax-items", response_model=EmploymentTaxItemOut, status_code=201)
def create_tax_item(payload: EmploymentTaxItemCreate, response: Response,
                    _admin=Depends(require_admin), db: Session = Depends(get_db)):
    if payload.item_kind not in ("rate", "fixed"):
        raise HTTPException(400, "item_kind 仅支持 rate（强制税率%）/ fixed（强制定额扣费）")
    if payload.item_kind == "fixed" and payload.fixed_amount is None:
        raise HTTPException(400, "定额科目（item_kind=fixed）必须填写 fixed_amount")
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
        item_name=payload.item_name, item_kind=payload.item_kind,
        tax_rate=0 if payload.item_kind == "fixed" else payload.tax_rate,
        fixed_amount=payload.fixed_amount if payload.item_kind == "fixed" else None,
        is_active=payload.is_active,
    )
    db.add(it)
    db.commit()
    db.refresh(it)
    response.headers["Location"] = f"/api/v1/employment-tax-items/{it.id}"
    return _serialize_tax_item(it)


@router.patch("/employment-tax-items/{item_id}", response_model=EmploymentTaxItemOut)
def update_tax_item(item_id: int, payload: EmploymentTaxItemUpdate,
                    _admin=Depends(require_admin), db: Session = Depends(get_db)):
    it = get_or_404(db, EmploymentTaxItem, item_id)
    data = payload.model_dump(exclude_unset=True)
    kind = data.get("item_kind") or (it.item_kind or "rate")
    if kind not in ("rate", "fixed"):
        raise HTTPException(400, "item_kind 仅支持 rate（强制税率%）/ fixed（强制定额扣费）")
    if "item_kind" in data:
        it.item_kind = kind
    if "fixed_amount" in data:
        it.fixed_amount = data["fixed_amount"]
    if "tax_rate" in data:
        it.tax_rate = data["tax_rate"]
    if kind == "fixed" and it.fixed_amount is None:
        raise HTTPException(400, "定额科目（item_kind=fixed）必须填写 fixed_amount")
    if kind == "fixed":
        it.tax_rate = 0  # 定额科目不参与百分比计算，置 0 防脏读
    if "is_active" in data:
        it.is_active = data["is_active"]
    if "item_name" in data:
        it.item_name = data["item_name"]
    db.commit()
    return _serialize_tax_item(it)


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
def list_tax_zones(country_id: int | None = None, _user=Depends(get_current_user), db: Session = Depends(get_db)):
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
    try:
        db.commit()
    except Exception as e:
        # issue #145：应用层查重与提交之间的并发窗口由部分唯一索引兜底
        db.rollback()
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise HTTPException(400, "该税区已存在（并发创建被唯一索引拦截）")
        raise
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
    # v2.6 R1：公司绑定该税区时禁止删除（成本键唯一来源）
    bound = db.query(Company).filter(Company.tax_zone_id == z.id).count()
    if bound:
        raise HTTPException(400, f"该税区已被 {bound} 家隶属公司绑定为成本税区，禁止删除（请先在主数据中解绑）")
    db.delete(z)
    db.commit()
    return {"ok": True, "id": zone_id}
