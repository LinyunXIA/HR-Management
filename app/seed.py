"""主数据字典初始化（按 Position.md 规则）。幂等：仅空表时写入。"""
from sqlalchemy.orm import Session

from app.models import (
    LegalCategoryDef,
    Level,
    ScopeDef,
    WorkLocation,
)

# 级别：按 Position.md §3.6 级别对照表（19 项）
LEVELS = [
    ("B6", "Coordinator", False),
    ("B7a", "Specialist", False),
    ("B7b", "Senior Specialist", False),
    ("M7", "Supervisor", True),
    ("B8a", "Staff", False),
    ("M8a", "Manager", True),
    ("B8b", "Senior Staff", False),
    ("M8b", "Senior Manager", True),
    ("B9", "Principal", False),
    ("M9a", "Director", True),
    ("M9b", "Senior Director", True),
    ("B10a", "Distinguished", False),
    ("M10", "Head", True),
    ("B10b", "Fellow", False),
    ("M11a", "VP", True),
    ("M11b", "SVP", True),
    ("M11c", "EVP", True),
    ("M12a", "Chief", True),
    ("M12b", "CEO", True),
]

# 工作地点：按 Position.md §3.3（12 个）
WORK_LOCATIONS = [
    "比利时布鲁塞尔", "比利时Spa", "丹麦", "瑞典", "荷兰", "卢森堡",
    "英国伦敦", "美国特拉华", "美国纽约", "美国洛杉矶", "中国香港", "中国上海",
]

# 工作范围：Family/Global/Regional/Country（suffix_code 驱动编号）
SCOPES = [
    ("family", "Family（家族全域）", "1"),
    ("global", "Global（全球跨法域）", "2"),
    ("regional", "Regional（区域）", "3"),
    ("country", "Country（国家/地区）", "4"),
]

# 法律强制/可选：按 Position.md §3.5（3 类）
LEGAL_CATEGORIES = [
    "法律强制·内部全职不可外包",
    "可选（集团内控推荐）",
    "纯后勤可选",
]


def seed_master_data(db: Session):
    """初始化主数据字典（空表时写入）。"""
    if db.query(Level).count() == 0:
        for i, (code, label, is_mgmt) in enumerate(LEVELS):
            db.add(Level(code=code, label=label, is_management=is_mgmt, sort_order=i))
    if db.query(WorkLocation).count() == 0:
        for i, name in enumerate(WORK_LOCATIONS):
            db.add(WorkLocation(name=name, sort_order=i))
    if db.query(ScopeDef).count() == 0:
        for i, (code, label, suffix) in enumerate(SCOPES):
            db.add(ScopeDef(code=code, label=label, suffix_code=suffix, sort_order=i))
    if db.query(LegalCategoryDef).count() == 0:
        for i, name in enumerate(LEGAL_CATEGORIES):
            db.add(LegalCategoryDef(name=name, sort_order=i))
    db.commit()
