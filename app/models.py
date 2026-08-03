"""SQLAlchemy 数据模型（见 docs/DESIGN.md §4）。"""
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
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
    """法律强制/可选。"""
    MANDATORY_INTERNAL = "法律强制·内部全职不可外包"
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


# ---------------------------------------------------------------- 表
class Company(Base):
    """隶属公司（法人实体）。"""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)


class Country(Base):
    """国家/地区（仅 Country 范围使用）。"""
    __tablename__ = "countries"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    code = Column(String(10), unique=True, nullable=False)  # 如 '4-1'


class Position(Base):
    """职位（职能，不含工作范围）。"""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    numbers = relationship("PositionNumber", back_populates="position")


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

    opening_date = Column(Date, nullable=True)
    closing_date = Column(Date, nullable=True)
    work_location = Column(String(255), nullable=True)
    job_responsibility = Column(Text, nullable=True)
    legal_category = Column(SAEnum(LegalCategory, native_enum=False, length=50), nullable=True)

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
    """人员档案（必须挂岗）。"""
    __tablename__ = "employees"

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
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    position = relationship("PositionNumber")
