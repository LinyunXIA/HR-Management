"""Pydantic 请求/响应模型。"""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models import (
    EmployeeType,
    EmploymentStatus,
    Gender,
    LegalCategory,
    PositionStatus,
    Scope,
)


# ---------------------------------------------------------------- 基础字典
class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class CountryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str


class PositionFunctionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class PositionFunctionCreate(BaseModel):
    name: str


# ---------------------------------------------------------------- 岗位
class PositionNumberCreate(BaseModel):
    position_id: int | None = None
    position_name: str | None = None
    company_id: int
    level: str | None = None
    scope: Scope
    country_id: int | None = None
    opening_date: date | None = None
    closing_date: date | None = None
    work_location: str | None = None
    job_responsibility: str | None = None
    legal_category: LegalCategory | None = None
    solid_line_manager_id: int | None = None
    dotted_manager_ids: list[int] = []
    org_chart_display: str | None = None
    prev_position_id: int | None = None
    prev_company_id: int | None = None
    remark: str | None = None
    number: str | None = None  # 缺省自动生成


class PositionNumberUpdate(BaseModel):
    position_id: int | None = None
    company_id: int | None = None
    level: str | None = None
    scope: Scope | None = None
    country_id: int | None = None
    opening_date: date | None = None
    closing_date: date | None = None
    work_location: str | None = None
    job_responsibility: str | None = None
    legal_category: LegalCategory | None = None
    solid_line_manager_id: int | None = None
    dotted_manager_ids: list[int] | None = None
    org_chart_display: str | None = None
    prev_position_id: int | None = None
    prev_company_id: int | None = None
    remark: str | None = None


class TransitionRequest(BaseModel):
    to_status: PositionStatus
    note: str | None = None


class PositionEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    from_status: str | None
    to_status: str
    changed_at: datetime
    note: str | None
    employee_id: int | None


# ---------------------------------------------------------------- 员工
class EmployeeCreate(BaseModel):
    employee_no: str
    name: str
    gender: Gender
    birth_date: date | None = None
    phone: str | None = None
    email: str | None = None
    hire_date: date | None = None
    employee_type: EmployeeType
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE
    position_number_id: int
    remark: str | None = None


class EmployeeUpdate(BaseModel):
    name: str | None = None
    gender: Gender | None = None
    birth_date: date | None = None
    phone: str | None = None
    email: str | None = None
    hire_date: date | None = None
    employee_type: EmployeeType | None = None
    employment_status: EmploymentStatus | None = None
    remark: str | None = None


class TransferRequest(BaseModel):
    to_position_id: int
