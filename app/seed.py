"""主数据字典初始化（按 Position.md 规则）。幂等：仅空表时写入。

级别从 testingdata/原始文件/Position.md 的「级别对照」表解析；
文件不存在时 fallback 到内置默认 LEVELS。
"""
import os
import re
import sys

from sqlalchemy.orm import Session

from app.models import (
    LegalCategoryDef,
    Level,
    PositionType,
    ScopeDef,
    WorkLocation,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSITION_MD = os.path.join(BASE_DIR, "testingdata", "原始文件", "Position.md")

# 级别对照表（fallback，文件不可读时使用）
FALLBACK_LEVELS = [
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
# 工作地点：v2.3 两级（国家 + 城市）；name 保持与源数据一致
WORK_LOCATIONS = [
    ("比利时布鲁塞尔", "比利时", "布鲁塞尔"),
    ("比利时Spa", "比利时", "Spa"),
    ("丹麦", "丹麦", None),
    ("瑞典", "瑞典", None),
    ("荷兰", "荷兰", None),
    ("卢森堡", "卢森堡", None),
    ("英国伦敦", "英国", "伦敦"),
    ("美国特拉华", "美国", "特拉华"),
    ("美国纽约", "美国", "纽约"),
    ("美国洛杉矶", "美国", "洛杉矶"),
    ("中国香港", "中国香港", None),
    ("中国上海", "中国", "上海"),
]

# 工作范围：Family/Global/Regional/Country（suffix_code 驱动编号）
SCOPES = [
    ("family", "Family（家族全域）", "1"),
    ("global", "Global（全球跨法域）", "2"),
    ("regional", "Regional（区域）", "3"),
    ("country", "Country（国家/地区）", "4"),
]

# 法律强制/可选：按 Position.md §10（4 类，含外包允许）
LEGAL_CATEGORIES = [
    "法律强制·内部全职不可外包",
    "法律强制·允许第三方外包",
    "可选（集团内控推荐）",
    "纯后勤可选",
]

# 职位类型：按 Position.md §9 映射规则
POSITION_TYPES = [
    "Consultant",          # Family Volunteer Unpaid → Consultant
    "Employee",            # In-house Full-time → Employee
    "External Employee",   # Outsourced External → External Employee
]


def _parse_levels_from_position_md() -> list | None:
    """从 Position.md 的「级别对照」表解析级别列表。

    表格格式示例：
        | Coordinator | N/A | B6 - N/A |
        | Senior Specialist | Supervisor | B7b - M7 |
        | Principal | Director | B9a - M9a |

    返回 [(code, label, is_management), ...]，或 None（解析失败）。
    """
    if not os.path.exists(POSITION_MD):
        return None
    try:
        with open(POSITION_MD, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None

    # 找到「级别对照」章节
    m = re.search(r"##\s*级别对照.*?(?=\n##|\Z)", text, re.DOTALL)
    if not m:
        return None

    levels = []
    seen = set()
    for row in m.group(0).split("\n"):
        cols = [c.strip() for c in row.split("|")[1:-1]]
        if len(cols) < 3:
            continue
        ic_title, mgmt_title, level_cell = cols[0], cols[1], cols[2]

        # 解析 "B7b - M7" 或 "B6 - N/A" → 拆分级别代码
        parts = [p.strip() for p in level_cell.split("-")]
        if len(parts) != 2:
            continue
        ic_code, mgmt_code = parts[0], parts[1]

        # 只接受有效的级别代码（B/M 开头）
        code_label_pairs = []
        if ic_code and ic_code != "N/A" and re.match(r"^[BM]\d", ic_code):
            code_label_pairs.append((ic_code, ic_title, False))
        if mgmt_code and mgmt_code != "N/A" and re.match(r"^[BM]\d", mgmt_code):
            code_label_pairs.append((mgmt_code, mgmt_title, True))

        for code, label, is_mgmt in code_label_pairs:
            if code not in seen:
                seen.add(code)
                levels.append((code, label, is_mgmt))

    return levels or None


def _get_levels() -> list:
    """返回级别列表：优先从 Position.md 解析，否则用 fallback（显式告警）。"""
    parsed = _parse_levels_from_position_md()
    if parsed:
        return parsed
    print("[seed] 警告：Position.md 级别对照解析失败（章节/表格格式变动？），"
          "已回退内置 19 级 fallback，请核查规则文件", file=sys.stderr)
    return FALLBACK_LEVELS


def seed_master_data(db: Session):
    """初始化主数据字典（幂等：缺失项补齐）。"""
    levels = _get_levels()
    if db.query(Level).count() == 0:
        for i, (code, label, is_mgmt) in enumerate(levels):
            db.add(Level(code=code, label=label, is_management=is_mgmt, sort_order=i))
    else:
        # 已有数据时，补齐 Position.md 新增级别（若有）
        existing_codes = {r[0] for r in db.query(Level.code).all()}
        for i, (code, label, is_mgmt) in enumerate(levels):
            if code not in existing_codes:
                db.add(Level(code=code, label=label, is_management=is_mgmt, sort_order=len(existing_codes) + i))
    if db.query(WorkLocation).count() == 0:
        for i, (name, country, city) in enumerate(WORK_LOCATIONS):
            db.add(WorkLocation(name=name, country=country, city=city, sort_order=i))
    else:
        existing = {r[0] for r in db.query(WorkLocation.name).all()}
        for i, (name, country, city) in enumerate(WORK_LOCATIONS):
            if name not in existing:
                db.add(WorkLocation(name=name, country=country, city=city,
                                    sort_order=len(existing) + i))
            else:
                # v2.3 兜底：补齐存量地点的 country/city 两级字段
                db.query(WorkLocation).filter(WorkLocation.name == name).update(
                    {"country": country, "city": city}
                )
    if db.query(ScopeDef).count() == 0:
        for i, (code, label, suffix) in enumerate(SCOPES):
            db.add(ScopeDef(code=code, label=label, suffix_code=suffix, sort_order=i))
    if db.query(LegalCategoryDef).count() == 0:
        for i, name in enumerate(LEGAL_CATEGORIES):
            db.add(LegalCategoryDef(name=name, sort_order=i))
    else:
        existing = {r[0] for r in db.query(LegalCategoryDef.name).all()}
        for i, name in enumerate(LEGAL_CATEGORIES):
            if name not in existing:
                db.add(LegalCategoryDef(name=name, sort_order=len(existing) + i))
    if db.query(PositionType).count() == 0:
        for i, name in enumerate(POSITION_TYPES):
            db.add(PositionType(name=name, sort_order=i))
    else:
        existing = {r[0] for r in db.query(PositionType.name).all()}
        for i, name in enumerate(POSITION_TYPES):
            if name not in existing:
                db.add(PositionType(name=name, sort_order=len(existing) + i))
    # 管理员种子（JWT，PRD §7B）
    _seed_admin(db)
    db.commit()


def _seed_admin(db: Session):
    """种子管理员：admin / admin123（幂等，仅首次创建）。"""
    from app.models import User, UserRole
    if db.query(User).count() > 0:
        return
    try:
        from app.auth import hash_password
    except Exception:
        return
    default_user = os.environ.get("DEFAULT_ADMIN_USER", "admin")
    default_pwd = os.environ.get("DEFAULT_ADMIN_PASSWORD", "admin123")
    db.add(User(
        username=default_user,
        hashed_password=hash_password(default_pwd),
        role=UserRole.ADMIN,
        is_active=True,
    ))
