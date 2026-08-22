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
    """法律强制/可选（仅用于 CSV 导入映射，运行时以 LegalCategoryDef 字典为准）。"""
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
    TERMINATED = "离职"


class CostMode(str, enum.Enum):
    """岗位成本输入模式（自动计算 / 手动输入，互斥）。"""
    AUTO = "auto"
    MANUAL = "manual"


# ---------------------------------------------------------------- 表
class Company(Base):
    """隶属公司（法人实体，软删除：is_active=False 为 Closed）。"""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)  # True=opened, False=closed（软删除，id 保留）


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
    """工作地点字典。"""
    __tablename__ = "work_locations"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)


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
    """员工用工税额（按国家；科目 + 税率）。"""
    __tablename__ = "employment_tax_items"

    id = Column(Integer, primary_key=True)
    country_id = Column(Integer, ForeignKey("countries.id"), nullable=False)
    item_name = Column(String(100), nullable=False)
    tax_rate = Column(Numeric(6, 2), nullable=False, default=0)  # 百分比 %
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=_now)


class Position(Base):
    """职位（职能，不含工作范围）。"""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    numbers = relationship("PositionNumber", back_populates="position")


class User(Base):
    """系统用户（JWT 认证，PRD §7B）。"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="admin")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=_now)


class PositionNumber(Base):
    """岗位编号（管理主体）。"""
    __tablename__ = "position_numbers"

    id = Column(Integer, primary_key=True)
    number = Column(String(20), unique=True, nullable=False)  # P{seq}-{scope}

    position_id = Column(Integer, ForeignKey("positions.id"), nullable=False)
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
    # ---- 成本字段（人工成本口径）----
    cost_mode = Column(SAEnum(CostMode, native_enum=False, length=10),
                       nullable=False, default=CostMode.MANUAL)
    salary_before_tax = Column(Numeric(14, 2), nullable=True)   # 税前薪资
    company_share = Column(Numeric(14, 2), nullable=True)       # 公司份额
    labor_cost = Column(Numeric(14, 2), nullable=True)          # 用工成本
    version = Column(Integer, nullable=False, default=1)        # 乐观锁版本号（PRD §7C）
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    position = relationship("Position", back_populates="numbers")
    company = relationship("Company", foreign_keys=[company_id])
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


class Employee(Base):
    """人员档案（必须挂岗 — 在职 NOT NULL，离职解绑可为 NULL）。"""
    __tablename__ = "employees"
    __table_args__ = (
        # PRD §5 要求「必须挂岗」：在职/试用期/休假时 position_number_id 必须非空，
        # 仅离职（离职）允许解绑为 NULL。DB 层强制，防止绕过应用逻辑产生孤儿记录。
        # 保持 nullable=True 以支持离职解绑流程（app/routers/employees.py:_vacate），
        # 但用 CHECK 约束保证在职员工不为 NULL（issue #1，Option B）。
        CheckConstraint(
            "employment_status IN ('TERMINATED', '离职') OR position_number_id IS NOT NULL",
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
    )  # 在职必须挂岗；离职解绑后为 NULL（档案保留）
    remark = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1)        # 乐观锁版本号（PRD §7C）
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    position = relationship("PositionNumber")
