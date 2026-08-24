"""SQLAlchemy 数据模型（见 docs/DESIGN.md §4）。"""
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db import Base


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 枚举
class Scope(str, enum.Enum):
    """工作范围（决定岗位编号后缀）。"""
    FAMILY = "family"
    GLOBAL = "global"
    REGIONAL = "regional"
    COUNTRY = "country"


class PositionStatus(str, enum.Enum):
    """岗位生命周期状态。"""
    PLANNED = "planned"
    OPEN = "open"
    OFFERED = "offered"
    FILLED = "filled"
    VACANT = "vacant"
    FROZEN = "frozen"
    CLOSED = "closed"


class LegalCategory(str, enum.Enum):
    """法律强制/可选（仅用于 CSV 导入兼容历史值，运行时以 LegalCategoryDef 字典为准）。

    P2 技术债：此枚举为历史遗留，新增逻辑应直接查询 LegalCategoryDef 字典表，
    枚举仅保留用于兼容旧 CSV 中的枚举名（如 MANDATORY_INTERNAL）。
    """
    MANDATORY_INTERNAL = "法律强制·内部全职不可外包"
    MANDATORY_OUTSOURCEABLE = "法律强制·允许第三方外包"
    OPTIONAL = "可选（集团内控推荐）"
    LOGISTICS = "纯后勤可选"


class Gender(str, enum.Enum):
    MALE = "男"
    FEMALE = "女"
    OTHER = "其他"


class EmployeeType(str, enum.Enum):
    REGULAR = "正式"
    INTERN = "实习"
    OUTSOURCED = "外包"
    LABOR = "劳务"


class EmploymentStatus(str, enum.Enum):
    PROBATION = "试用期"
    ACTIVE = "在职"
    LEAVE = "休假"
    TRANSFERRING = "转调中"  # v2.3 转调交接：人仍挂原岗（原岗保持 Filled 锁定）
    TERMINATED = "离职"


class UserRole(str, enum.Enum):
    """系统角色（PRD §7B.2）：admin=集团 full admin / hr=子公司 HR。"""
    ADMIN = "admin"
    HR = "hr"


class UserType(str, enum.Enum):
    """账号类型（v2.4.3 权限拆分）：数据权限→实体、API 权限→接口。

    - UI：仅数据权限（内部系统用户，经 user_companies 绑定实体）
    - API：外部 API 用户 = 数据权限 + API 权限 结合（user_apis 授权表）
    """
    UI = "ui"
    API = "api"


class CostMode(str, enum.Enum):
    """岗位成本输入模式（自动计算 / 手动输入，互斥）。"""
    AUTO = "auto"
    MANUAL = "manual"


# ---------------------------------------------------------------- 表
class Company(Base):
    """隶属公司（法人实体，软删除：is_active=False 为 Closed）。

    v2.4：开业/关闭日期 + 股权结构（CompanyShareholder 子表，三来源互斥）。
    closing_date 有值 ⇔ 公司关闭（与 is_active 联动，见 routers/master_data.py）。
    """
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)  # True=opened, False=closed（软删除，id 保留）
    opening_date = Column(Date, nullable=True)   # v2.4 开业日期（年份精度按 YYYY-01-01 存）
    closing_date = Column(Date, nullable=True)   # v2.4 关闭日期；有值视为关闭

    shareholders = relationship(
        "CompanyShareholder", cascade="all, delete-orphan",
        order_by="CompanyShareholder.sort_order", backref="company",
        # 双外键路径（company_id / internal_company_id）必须显式指定，否则 AmbiguousForeignKeys
        foreign_keys="CompanyShareholder.company_id",
    )


class ExternalCompany(Base):
    """外部合作公司（v2.4）：不在系统内设岗、仅作股权等关系引用的外部法人实体。

    v2.4.1：启停改由关闭日期管理（closing_date 有值 ⇔ 关闭），与隶属公司一致；
    不再使用手工「启用」开关（is_active 保留为派生字段）。
    """
    __tablename__ = "external_companies"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    remark = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    opening_date = Column(Date, nullable=True)
    closing_date = Column(Date, nullable=True)


class CompanyShareholder(Base):
    """股权结构子表（v2.4）：0..N 股东，每行三来源互斥——内部公司 / 外部合作公司 / 自然人。"""
    __tablename__ = "company_shareholders"
    __table_args__ = (
        # 三来源互斥（恰好其一非空）。可移植写法：布尔表达式求和 = 1，
        # SQLite/PostgreSQL 通用（PG 专属函数 num_nonnulls 已弃用，v2.5 SQLite 切换）
        CheckConstraint(
            "((internal_company_id IS NOT NULL) + (external_company_id IS NOT NULL)"
            " + (person_name IS NOT NULL)) = 1",
            name="ck_shareholder_source_exclusive",
        ),
        CheckConstraint(
            "internal_company_id IS NULL OR internal_company_id <> company_id",
            name="ck_shareholder_no_self_loop",
        ),
        UniqueConstraint("company_id", "internal_company_id",
                         name="uq_shareholder_internal"),
        UniqueConstraint("company_id", "external_company_id",
                         name="uq_shareholder_external"),
    )

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    internal_company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)          # 内部公司股东
    external_company_id = Column(Integer, ForeignKey("external_companies.id"), nullable=True)  # 外部合作公司股东
    person_name = Column(String(255), nullable=True)                                          # 自然人股东
    ownership_pct = Column(Numeric(5, 2), nullable=True)  # 持股比例 %（可选；合计≠100% 前端软告警）
    sort_order = Column(Integer, nullable=False, default=0)

    internal_company = relationship("Company", foreign_keys=[internal_company_id])
    external_company = relationship("ExternalCompany")


class Country(Base):
    """国家/地区（仅 Country 范围使用）。"""
    __tablename__ = "countries"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    code = Column(String(10), unique=True, nullable=False)  # 如 '4-1'


class Level(Base):
    """级别字典（按 Position.md 级别对照初始化；M 开头=管理岗）。"""
    __tablename__ = "levels"

    id = Column(Integer, primary_key=True)
    code = Column(String(10), unique=True, nullable=False)  # B6 / M8a …
    label = Column(String(100), nullable=True)
    is_management = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)


class WorkLocation(Base):
    """工作地点字典（v2.3：国家+城市 两级，用于税区挂载）。"""
    __tablename__ = "work_locations"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    country = Column(String(100), nullable=True)   # 国家/地区（如 比利时）
    city = Column(String(100), nullable=True)      # 城市（如 布鲁塞尔；国家级地点可空）
    sort_order = Column(Integer, nullable=False, default=0)


class TaxZone(Base):
    """税区挂载点配置（v2.3 F1.6）：税率集合挂到国家级或城市级。

    城市级分拆后**无国家兜底**——未配置税率的地区成本无法自动计算。
    """
    __tablename__ = "tax_zones"
    __table_args__ = (
        CheckConstraint("level IN ('country', 'city')", name="ck_tax_zones_level"),
        UniqueConstraint("level", "country_id", "city", name="uq_tax_zone_scope"),
    )

    id = Column(Integer, primary_key=True)
    level = Column(String(10), nullable=False)               # country | city
    country_id = Column(Integer, ForeignKey("countries.id"), nullable=False)
    city = Column(String(100), nullable=True)                # level=city 时必填
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_now)

    country = relationship("Country")


class ScopeDef(Base):
    """工作范围字典（Family/Global/Regional/Country；suffix_code 驱动编号）。"""
    __tablename__ = "scopes"

    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False)      # family/global/regional/country
    label = Column(String(50), nullable=False)
    suffix_code = Column(String(5), nullable=False)              # 1/2/3/4
    sort_order = Column(Integer, nullable=False, default=0)


class LegalCategoryDef(Base):
    """法律强制/可选字典。"""
    __tablename__ = "legal_categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)


class PositionType(Base):
    """职位类型字典（按 Position.md §9 映射）。"""
    __tablename__ = "position_types"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)  # Consultant / Employee / External Employee
    sort_order = Column(Integer, nullable=False, default=0)


class EmploymentTaxItem(Base):
    """员工用工税额（v2.3：按税区 TaxZone 挂载；v2.6 起支持两类科目）。

    - item_kind='rate'  强制税率科目：tax_rate 为百分比 %，计提基数=税前
    - item_kind='fixed' 强制定额扣费科目：fixed_amount 为年度固定金额
    """
    __tablename__ = "employment_tax_items"

    id = Column(Integer, primary_key=True)
    tax_zone_id = Column(Integer, ForeignKey("tax_zones.id"), nullable=True)  # v2.3 税区
    country_id = Column(Integer, ForeignKey("countries.id"), nullable=True)  # 旧口径保留兼容
    item_name = Column(String(100), nullable=False)
    item_kind = Column(String(10), nullable=False, default="rate")   # v2.6: rate | fixed
    tax_rate = Column(Numeric(7, 4), nullable=False, default=0)      # 百分比 %（rate 科目）
    fixed_amount = Column(Numeric(14, 2), nullable=True)             # 年度定额（fixed 科目）
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=_now)

    tax_zone = relationship("TaxZone")


class Position(Base):
    """职位（职能，不含工作范围）。"""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    numbers = relationship("PositionNumber", back_populates="position")


class User(Base):
    """系统用户（JWT 认证，PRD §7B）。

    v2.4.3 权限拆分：user_type=UI 仅数据权限（实体绑定）；
    user_type=API 为外部 API 用户，数据权限 + API 权限（user_apis）结合。
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(SAEnum(UserRole, native_enum=False, length=10),
                  nullable=False, default=UserRole.ADMIN)
    user_type = Column(SAEnum(UserType, native_enum=False, length=10),
                       nullable=False, default=UserType.UI)  # UI=仅数据权限 / API=数据+API 结合
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=_now)

    companies = relationship("UserCompany", back_populates="user",
                             cascade="all, delete-orphan")
    api_permissions = relationship("UserApiPermission", back_populates="user",
                                   cascade="all, delete-orphan")


class UserApiPermission(Base):
    """外部 API 用户的接口授权（v2.4.3，0..N；api_key 见 app/auth.py::API_SCOPES 注册表）。"""
    __tablename__ = "user_apis"
    __table_args__ = (UniqueConstraint("user_id", "api_key"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    api_key = Column(String(50), nullable=False)

    user = relationship("User", back_populates="api_permissions")


class UserCompany(Base):
    """hr 用户 ↔ 可管法人实体 多对多（v2.3 行级隔离，PRD §7B.2）。

    admin 自带全司（无需记录）；hr 无记录则无任何可管实体。
    """
    __tablename__ = "user_companies"
    __table_args__ = (UniqueConstraint("user_id", "company_id"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=_now)

    user = relationship("User", back_populates="companies")
    company = relationship("Company")


class PositionNumber(Base):
    """岗位编号（管理主体）。"""
    __tablename__ = "position_numbers"
    __table_args__ = (
        # issue #69：scope=country（枚举名 COUNTRY，SAEnum 持久化「名」）时 country_id 必填，
        # DB 层兜底防绕过 API 直写库产生脏数据
        CheckConstraint(
            f"scope <> '{Scope.COUNTRY.name}' OR country_id IS NOT NULL",
            name="ck_positions_country_required_when_country_scope",
        ),
    )

    id = Column(Integer, primary_key=True)
    # 系统分配纯序号（PRD §3.1 v2.3）：正式岗 P{seq} / 外包岗 PA{seq}，与 scope/country 解耦
    number = Column(String(20), unique=True, nullable=False)

    position_id = Column(Integer, ForeignKey("positions.id"), nullable=False)
    # 双外键指向 companies：company_id（当前隶属）+ prev_company_id（转岗前公司），
    # 需在 relationship 上显式 foreign_keys 避免 AmbiguousForeignKeys（见 P0-12 / P2-1）
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    level = Column(String(20), nullable=True)
    scope = Column(SAEnum(Scope, native_enum=False, length=20), nullable=False)
    country_id = Column(Integer, ForeignKey("countries.id"), nullable=True)
    position_type = Column(String(30), nullable=True)  # 职位类型：Consultant/Employee/External Employee

    opening_date = Column(Date, nullable=True)
    closing_date = Column(Date, nullable=True)
    work_location = Column(String(255), nullable=True)
    job_responsibility = Column(Text, nullable=True)
    # DESIGN §4.1：String 引用 legal_categories 字典，允许运行时扩展（issue #2）
    legal_category = Column(String(50), nullable=True)

    # 直线经理：未声明 ORM relationship，避免 AmbiguousForeignKeys（company_id / prev_company_id 双外键冲突）
    # 查询时一律使用 db.get(PositionNumber, id) 而非 relationship
    solid_line_manager_id = Column(
        Integer, ForeignKey("position_numbers.id", ondelete="SET NULL"), nullable=True
    )
    org_chart_display = Column(String(255), nullable=True)
    prev_position_id = Column(
        Integer, ForeignKey("position_numbers.id", ondelete="SET NULL"), nullable=True
    )
    prev_company_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    remark = Column(Text, nullable=True)

    status = Column(
        SAEnum(PositionStatus, native_enum=False, length=20),
        nullable=False,
        default=PositionStatus.PLANNED,
    )
    # ---- 预算成本六栏（v2.6：公司份额拆分为强制扣税+强制定额扣费，新增奖金两栏）----
    cost_mode = Column(SAEnum(CostMode, native_enum=False, length=10),
                       nullable=False, default=CostMode.MANUAL)
    salary_before_tax = Column(Numeric(14, 2), nullable=True)        # 预算·税前（年薪）
    mandatory_tax = Column(Numeric(14, 2), nullable=True)            # 预算·强制扣税（=税前×税率%，金额）
    mandatory_fixed_fee = Column(Numeric(14, 2), nullable=True)      # 预算·强制定额扣费
    fixed_bonus = Column(Numeric(14, 2), nullable=True)              # 预算·固定奖金
    floating_bonus = Column(Numeric(14, 2), nullable=True)           # 预算·浮动奖金
    labor_cost = Column(Numeric(14, 2), nullable=True)               # 预算·用工成本=五栏之和
    version = Column(Integer, nullable=False, default=1)        # 乐观锁版本号（PRD §7C）
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    position = relationship("Position", back_populates="numbers")
    company = relationship("Company", foreign_keys=[company_id])
    prev_company = relationship("Company", foreign_keys=[prev_company_id])
    country = relationship("Country")


class PositionNumberDottedLine(Base):
    """虚线经理（岗位多对多）。"""
    __tablename__ = "position_number_dotted_lines"
    __table_args__ = (UniqueConstraint("position_number_id", "dotted_manager_id"),)

    id = Column(Integer, primary_key=True)
    position_number_id = Column(
        Integer, ForeignKey("position_numbers.id", ondelete="CASCADE"), nullable=False
    )
    dotted_manager_id = Column(
        Integer, ForeignKey("position_numbers.id", ondelete="CASCADE"), nullable=False
    )
    label = Column(String(100), nullable=True)  # 虚线标签（如 "AML 虚线"、"IT 虚线"）


class PositionEvent(Base):
    """生命周期事件（时间线）。"""
    __tablename__ = "position_events"

    id = Column(Integer, primary_key=True)
    position_number_id = Column(
        Integer, ForeignKey("position_numbers.id", ondelete="CASCADE"), nullable=False
    )
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    from_status = Column(String(20), nullable=True)
    to_status = Column(String(20), nullable=False)
    changed_at = Column(DateTime, default=_now)
    note = Column(Text, nullable=True)


class Transfer(Base):
    """转调交接记录（v2.3 F1.5b）：转出 → 待认领 → 认领分配 / 退回。人永不脱岗。

    - initiate：原 HR 转出到目标公司；人仍挂原岗、原岗保持 Filled 锁定；
      员工标记「转调中」+ target_company_id。
    - claim：仅目标公司 HR 认领 + 分配空闲目标岗（单事务：目标岗 Filled +
      原岗 Vacant + 人挂新岗 + prev_*）。
    - reject：目标公司 HR 拒绝 → 退回原公司、原岗继续。
    """
    __tablename__ = "transfers"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    from_position_id = Column(
        Integer, ForeignKey("position_numbers.id", ondelete="SET NULL"), nullable=True
    )
    target_company_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"),
                               nullable=False)  # 目标公司（认领池过滤键）
    to_position_id = Column(
        Integer, ForeignKey("position_numbers.id", ondelete="SET NULL"), nullable=True
    )  # 认领时分配的目标岗
    status = Column(String(20), nullable=False, default="initiated")  # initiated|claimed|rejected
    timing = Column(String(20), nullable=True)  # 预留：month_end | immediate（升职时节）
    kind = Column(String(20), nullable=False, default="transfer")  # transfer | promotion
    initiated_by = Column(String(100), nullable=True)  # 发起人用户名
    claimed_by = Column(String(100), nullable=True)    # 认领/退回操作人
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)
    claimed_at = Column(DateTime, nullable=True)

    employee = relationship("Employee")
    from_position = relationship("PositionNumber", foreign_keys=[from_position_id])
    to_position = relationship("PositionNumber", foreign_keys=[to_position_id])
    target_company = relationship("Company")


class Employee(Base):
    """人员档案（必须挂岗 — 在职 NOT NULL，离职解绑可为 NULL）。"""
    __tablename__ = "employees"
    __table_args__ = (
        # PRD §5「必须挂岗」+ v2.4.2 例外：外包人员可虚拟建档不挂岗（由外包公司管理，
        # 系统仅登记名单）。在职/试用期/休假时 position_number_id 非空，外包或离职除外。
        # 保持 nullable=True 以支持离职解绑流程（app/routers/employees.py:_vacate），
        # SAEnum(native_enum=False) 持久化枚举「名」（'TERMINATED'/'OUTSOURCED'）。
        CheckConstraint(
            f"employment_status = '{EmploymentStatus.TERMINATED.name}' "
            f"OR position_number_id IS NOT NULL "
            f"OR employee_type = '{EmployeeType.OUTSOURCED.name}'",
            name="ck_employees_position_required_if_active",
        ),
    )

    id = Column(Integer, primary_key=True)
    employee_no = Column(String(50), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    gender = Column(SAEnum(Gender, native_enum=False, length=10), nullable=False)
    birth_date = Column(Date, nullable=True)
    phone = Column(String(50), nullable=True)
    email = Column(String(255), nullable=True)
    hire_date = Column(Date, nullable=True)
    employee_type = Column(SAEnum(EmployeeType, native_enum=False, length=20), nullable=False)
    employment_status = Column(
        SAEnum(EmploymentStatus, native_enum=False, length=20),
        nullable=False,
        default=EmploymentStatus.ACTIVE,
    )
    position_number_id = Column(
        Integer, ForeignKey("position_numbers.id"), unique=True, nullable=True
    )  # 在职必须挂岗（含转调中——人永不脱岗）；仅离职解绑后为 NULL
    target_company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )  # v2.3 转调中：目标公司（认领前原岗保持 Filled）
    # ---- 实际成本六栏（v2.6：跟人走，结构同岗位预算口径）----
    actual_cost_mode = Column(SAEnum(CostMode, native_enum=False, length=10),
                              nullable=False, default=CostMode.MANUAL)
    actual_salary_before_tax = Column(Numeric(14, 2), nullable=True)     # 实际·税前（年薪）
    actual_mandatory_tax = Column(Numeric(14, 2), nullable=True)         # 实际·强制扣税
    actual_mandatory_fixed_fee = Column(Numeric(14, 2), nullable=True)   # 实际·强制定额扣费
    actual_fixed_bonus = Column(Numeric(14, 2), nullable=True)           # 实际·固定奖金
    actual_floating_bonus = Column(Numeric(14, 2), nullable=True)        # 实际·浮动奖金
    actual_labor_cost = Column(Numeric(14, 2), nullable=True)            # 实际·用工成本=五栏之和
    remark = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1)        # 乐观锁版本号（PRD §7C）
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    position = relationship("PositionNumber")
    target_company = relationship("Company")


class LaborBenchmark(Base):
    """外部用工成本基准包（v2.6，整年快照行）。

    外部系统按我方 schema 经 POST /benchmarks 推送，整批原子、整年替换
    （最后一次提交为准）。匹配键 = (year, company_id, level, country_id,
    work_location)，与岗位同名字段全等值精确对应（PRD §4 F6）。
    """
    __tablename__ = "labor_benchmarks"
    __table_args__ = (
        UniqueConstraint("year", "company_id", "level", "country_id", "work_location",
                         name="uq_labor_benchmarks_key"),
    )

    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    level = Column(String(20), nullable=False)                 # levels.code（如 M8a）
    country_id = Column(Integer, ForeignKey("countries.id"), nullable=False)
    work_location = Column(String(255), nullable=False)        # work_locations.name
    salary_before_tax = Column(Numeric(14, 2), nullable=False)  # 税前（年薪）
    tax_rate = Column(Numeric(7, 4), nullable=False, default=0)     # 强制税率 %
    mandatory_fixed_fee = Column(Numeric(14, 2), nullable=False, default=0)  # 强制定额扣费
    created_at = Column(DateTime, default=_now)

    company = relationship("Company")
    country = relationship("Country")


class BenchmarkReport(Base):
    """年度用工成本预估报告（v2.6）：一年一份最新结果，推送后异步生成覆盖。"""
    __tablename__ = "benchmark_reports"

    id = Column(Integer, primary_key=True)
    year = Column(Integer, unique=True, nullable=False)
    status = Column(String(20), nullable=False, default="pending")  # pending|ready|failed
    payload = Column(Text, nullable=True)          # 报告 JSON（公司汇总 + 岗位明细 + 缺失清单）
    error_count = Column(Integer, nullable=False, default=0)
    generated_at = Column(DateTime, nullable=True)
