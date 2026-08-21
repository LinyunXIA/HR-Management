"""Pydantic 请求/响应模型。"""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, model_validator

from app.models import (
    CostMode,
    EmployeeType,
    EmploymentStatus,
    Gender,
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
    position_type: str | None = None  # 职位类型：Consultant/Employee/External Employee
    opening_date: date | None = None
    closing_date: date | None = None
    work_location: str | None = None
    job_responsibility: str | None = None
    legal_category: str | None = None
    solid_line_manager_id: int | None = None
    dotted_manager_ids: list[int] = []
    org_chart_display: str | None = None
    prev_position_id: int | None = None
    prev_company_id: int | None = None
    remark: str | None = None
    # 成本字段（新建时可留空）
    cost_mode: CostMode | None = None
    salary_before_tax: float | None = None
    company_share: float | None = None
    labor_cost: float | None = None
    # 岗位编号仅自动生成，不接受手工输入

    @model_validator(mode="after")
    def check_position_exclusive(self):
        has_id = self.position_id is not None
        has_name = self.position_name is not None and str(self.position_name).strip() != ""
        if not (has_id ^ has_name):
            raise ValueError("Exactly one of position_id or position_name required")
        return self


class PositionNumberUpdate(BaseModel):
    position_id: int | None = None
    company_id: int | None = None
    level: str | None = None
    scope: Scope | None = None
    country_id: int | None = None
    position_type: str | None = None
    opening_date: date | None = None
    closing_date: date | None = None
    work_location: str | None = None
    job_responsibility: str | None = None
    legal_category: str | None = None
    solid_line_manager_id: int | None = None
    dotted_manager_ids: list[int] | None = None
    org_chart_display: str | None = None
    prev_position_id: int | None = None
    prev_company_id: int | None = None
    remark: str | None = None
    # 成本字段
    cost_mode: CostMode | None = None
    salary_before_tax: float | None = None
    company_share: float | None = None
    labor_cost: float | None = None
    version: int | None = None  # 乐观锁版本号（PRD §7C），携带时校验


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
    version: int | None = None  # 乐观锁版本号（PRD §7C）


class TransferRequest(BaseModel):
    to_position_id: int


class TransferCreate(BaseModel):
    employee_id: int
    to_position_id: int


# ---------------------------------------------------------------- 主数据（F0）
class CompanyCreate(BaseModel):
    name: str


class CompanyUpdate(BaseModel):
    name: str | None = None


class CountryCreate(BaseModel):
    name: str
    code: str


class CountryUpdate(BaseModel):
    name: str | None = None
    code: str | None = None


class LevelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    label: str | None
    is_management: bool
    sort_order: int


class LevelCreate(BaseModel):
    code: str
    label: str | None = None
    is_management: bool = False
    sort_order: int = 0


class LevelUpdate(BaseModel):
    label: str | None = None
    is_management: bool | None = None
    sort_order: int | None = None


class WorkLocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    sort_order: int


class WorkLocationCreate(BaseModel):
    name: str
    sort_order: int = 0


class WorkLocationUpdate(BaseModel):
    name: str | None = None
    sort_order: int | None = None


class ScopeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    label: str
    suffix_code: str
    sort_order: int


class ScopeCreate(BaseModel):
    code: str
    label: str
    suffix_code: str
    sort_order: int = 0


class ScopeUpdate(BaseModel):
    label: str | None = None
    suffix_code: str | None = None
    sort_order: int | None = None


class LegalCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    sort_order: int


class LegalCategoryCreate(BaseModel):
    name: str
    sort_order: int = 0


class LegalCategoryUpdate(BaseModel):
    name: str | None = None
    sort_order: int | None = None


class PositionTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    sort_order: int


class PositionTypeCreate(BaseModel):
    name: str
    sort_order: int = 0


class PositionTypeUpdate(BaseModel):
    name: str | None = None
    sort_order: int | None = None


class EmploymentTaxItemOut(BaseModel):
    id: int
    country_id: int
    country_name: str | None = None
    item_name: str
    tax_rate: float
    is_active: bool


class EmploymentTaxItemCreate(BaseModel):
    country_id: int
    item_name: str
    tax_rate: float = 0.0
    is_active: bool = True


class EmploymentTaxItemUpdate(BaseModel):
    item_name: str | None = None
    tax_rate: float | None = None
    is_active: bool | None = None


class ManagerOption(BaseModel):
    id: int
    number: str
    position_name: str | None
    level: str | None
