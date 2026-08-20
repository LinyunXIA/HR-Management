"""Org-Chart.md 解析 + Position.md 规则解析 + 数据清洗/校验/CSV 导出。

解析流程：
1. parse_orgchart(md_text) → 提取全部岗位（🧑‍💼 + 👨‍👩‍👧 + 📋），含隶属公司、直线经理
2. parse_rules(md_text) → 提取级别映射、国家编号、工作地点
3. clean_data(positions, rules) → 格式校验、字段补全、生成显示名
4. validate_and_report(cleaned) → 生成清洗报告（通过/修复/警告/错误）
5. to_csv(cleaned) → 输出 17 列 CSV 文本
"""
import csv
import io
import re
from typing import Dict, List, Optional, Tuple

# ─── Org-Chart.md 解析 ─────────────────────────────────────────────

# 岗位编号格式：P{seq}-{1,2,3} 或 P{seq}-4-{country}（1~2位数字）
P_NUM_PATTERN = r"P\d{3,}-(?:[123]|4-\d{1,2})"
P_NUM_RE = re.compile(rf"\b({P_NUM_PATTERN})\b")

# 行类型：🧑‍💼 In-house Full-time / 👨‍👩‍👧 Family Volunteer Unpaid / 📋 Outsourced External
# 映射规则（Position.md §9）：
#   Family Volunteer Unpaid → Consultant
#   In-house Full-time     → Employee
#   Outsourced External    → External Employee
TYPE_MAP = {
    "🧑‍💼": "Employee",
    "👨‍👩‍👧": "Consultant",
    "📋": "External Employee",
}
SKIP_TYPES: set[str] = set()

# 岗位行正则（两步匹配，兼容两种格式）：
# 格式1: P001-1 - Family Chairman & General Manager - 家族主席兼总经理 【可选...】(Opening: 1982) 【备注】
# 格式2: P063-4-5 Soparfi Managing Director - 卢森堡控股总经理 (Opening:2007)【法律强制...】
POS_BASE_RE = re.compile(rf"({P_NUM_PATTERN})\s*(?:-\s*|\s+)(.+?)\(Opening:\s*(\d{{4}})")
POS_MID_RE = re.compile(r"(.+?)\s*-\s*(.+?)(?:\s*【(.+?)】)?")
POS_TRAIL_RE = re.compile(r"【(.+)】")

# 公司行正则：提取公司名、地点、年份
# 例：Family Asset Management SPRL（比利时布鲁塞尔 | 1982｜开户行...）
COMPANY_RE = re.compile(r"^(.+?)（(.+?)）")
COMPANY_DETAIL_RE = re.compile(r"(.+?)\s*\|\s*(\d{4})")


def parse_orgchart(md_text: str) -> Tuple[List[Dict], Dict]:
    """解析 Org-Chart.md 树形结构，提取内部全职岗位。

    深度计算：│ 字符在行中的全局列位置 ÷ 4 = 祖先层级，+1 = 当前节点深度。
    直线经理推断：同深度节点取该深度首个节点（领袖）；非首个节点报告给领袖。
    隶属公司：向上追溯最近的公司节点。

    返回 (positions, company_map)。
    """
    lines = md_text.split("\n")
    positions = []
    company_map: Dict[str, dict] = {}
    depth_first_pos: Dict[int, str] = {}  # depth -> 第一个出现的岗位编号
    parse_orgchart._ext_seq = 900  # type: ignore  # 外包合成编号起始，防与 P001-P091 冲突

    current_type: Optional[str] = None  # 从类型标记行继承
    current_outsourced_legal: Optional[str] = None  # 外包小类标题（【...】）继承
    current_company: Optional[str] = None  # 当前隶属公司（用于合成外包岗）
    in_tree = False  # 只处理树结构区域内的行

    for line in lines:
        # 检测代码块边界（兼容 ````tree` 和无标记格式）
        stripped_line = line.strip()
        if stripped_line.startswith("```"):
            if "tree" in stripped_line.lower() or stripped_line == "```tree":
                in_tree = True
                continue
            elif stripped_line == "```":
                in_tree = False
                continue
        # 也支持：标题行后直接跟随树内容（无 ```tree 标记）
        if stripped_line.startswith("# 完整组织架构树") or stripped_line == "完整组织架构树":
            in_tree = True
            continue

        if not in_tree:
            continue  # 跳过代码块外的行

        if not line.strip():
            continue

        # ── 1. 计算节点深度 ──
        stripped = line.lstrip()
        bar_pos = stripped.find("│")
        if bar_pos >= 0:
            full_pos = (len(line) - len(stripped)) + bar_pos
            depth = full_pos // 4
        else:
            depth = 0

        # ── 2. 去除树字符前缀，获取 clean 文本 ──
        clean = stripped.lstrip("│├└─ ")
        if not clean:
            continue

        # ── 3. 类型标记行（🧑‍💼 / 👨‍👩‍👧 / 📋）──
        line_type = None
        for marker, type_name in TYPE_MAP.items():
            if marker in clean:
                line_type = type_name
                break
        if line_type is not None:
            current_type = line_type
            current_outsourced_legal = None  # 切换类型时重置外包小类
            continue

        # ── 4. 公司节点 ──
        if not P_NUM_RE.search(clean):
            # 公司行特征：含 （...）且不含 (Opening:，避免误判含 （ 的外包法律分类
            if "(Opening:" not in clean:
                cm = COMPANY_RE.search(clean)
                if cm:
                    company_name = cm.group(1).strip()
                    detail = cm.group(2)
                    dm = COMPANY_DETAIL_RE.search(detail)
                    # 只取第一个地名（| 或｜之前的部分）
                    raw_location = dm.group(1).strip() if dm else detail.split("｜")[0].split("|")[0].strip()
                    company_map[company_name] = {
                        "name": company_name,
                        "location": raw_location,
                        "year": dm.group(2) if dm else None,
                    }
                    current_company = company_name
                    continue  # 公司行不做更多处理
            # ── 4b. 外包岗（无 P 编号）── 按 External Employee 捕获
            # 例：Local Belgian Tax Advisor - 比利时本地税务顾问 【法律强制·允许第三方外包】(Opening: 1982)
            if current_type == "External Employee":
                # 先检测是否为法律分类小标题（仅 【...】），记录后跳过
                only_bracket = re.match(r"^【(.+)】$", clean.strip())
                if only_bracket:
                    current_outsourced_legal = only_bracket.group(1).strip()
                    continue
                om = re.search(r"(.+?)\s*-\s*(.+?)\s*(?:【(.+?)】)?\s*\(Opening:\s*(\d{4})", clean)
                if om:
                    pos_name_en = om.group(1).strip()
                    pos_name_cn = om.group(2).strip()
                    legal_cat = om.group(3).strip() if om.group(3) else None
                    if not legal_cat:
                        legal_cat = current_outsourced_legal
                    opening = om.group(4)
                    after_opening = clean[om.end():]
                    tm = POS_TRAIL_RE.search(after_opening)
                    remark = tm.group(1).strip() if tm else None
                    # 生成合成 P 编号（避免与既有 P001-P091 冲突，从 P900 起）
                    if not hasattr(parse_orgchart, "_ext_seq"):
                        parse_orgchart._ext_seq = 900  # type: ignore
                    pos_number = f"P{parse_orgchart._ext_seq:03d}-2"  # 外包默认按 Global 处理
                    parse_orgchart._ext_seq += 1  # type: ignore
                    # 直线经理逻辑同正式岗
                    line_manager = None
                    if depth in depth_first_pos:
                        line_manager = depth_first_pos[depth]
                    else:
                        for shallower in range(depth - 1, -1, -1):
                            if shallower in depth_first_pos:
                                line_manager = depth_first_pos[shallower]
                                break
                        depth_first_pos[depth] = pos_number
                    node_stack_entry = {
                        "number": pos_number,
                        "name_en": pos_name_en,
                        "name_cn": pos_name_cn,
                        "type": current_type,
                        "line_manager": line_manager,
                        "opening_year": opening,
                        "legal_category": legal_cat,
                        "remark": remark,
                        "depth": depth,
                        "company": current_company,
                        "line_idx": None,
                    }
                    positions.append(node_stack_entry)
            continue

        # ── 5. 岗位节点（含 P 编号） ── 两步匹配
        if not current_type:
            continue

        bm = POS_BASE_RE.search(clean)
        if not bm:
            continue

        pos_number = bm.group(1)
        mid_str = bm.group(2).strip()  # 英文名 - 中文名 【legal】
        opening = bm.group(3)

        mm = POS_MID_RE.search(mid_str)
        if not mm:
            continue

        pos_name_en = mm.group(1).strip()
        pos_name_cn = mm.group(2).strip()
        legal_cat = mm.group(3).strip() if mm.group(3) else None

        # 第三步：在 (Opening:YYYY) 之后提取可选的 【备注】
        after_opening = clean[bm.end():]
        tm = POS_TRAIL_RE.search(after_opening)
        remark = tm.group(1).strip() if tm else None

        # 确定直线经理：
        # 同深度节点 → 报告给该深度首个节点（领袖）
        # 更深节点 → 报告给最近的更浅节点
        line_manager = None
        if depth in depth_first_pos:
            # 同深度已有节点 → 报告给该深度的领袖（首个节点）
            line_manager = depth_first_pos[depth]
        else:
            # 该深度首个节点 → 报告给最近的更浅深度的节点
            for shallower in range(depth - 1, -1, -1):
                if shallower in depth_first_pos:
                    line_manager = depth_first_pos[shallower]
                    break
            depth_first_pos[depth] = pos_number

        # 确定隶属公司：向上查找最近的公司节点
        # 简化：在positions列表中，找到最近一个不同公司的位置
        parent_company = None
        # 由于无法直接从深度推断公司，使用栈逻辑在 positions 中查找
        # （公司信息存在 company_map 中，这里用简单启发：找到最近的、与当前不同编号模式的前驱）
        # 实际上，公司信息在树中位于岗位的父节点，我们通过全局扫描回溯
        # 简单做法：扫描前面所有 positions，找到最近一个公司不同的
        # 这不够准确，改为：扫描文件，在当前行之前找到最近的公司节点

        # 这部分改为：在解析时同步记录每个岗位行之前的最近公司
        # （由于公司节点已处理，这里无法直接获取，需在主循环中记录）

        # 暂时留空，在下面的后处理中填充公司
        node_stack_entry = {
            "number": pos_number,
            "name_en": pos_name_en,
            "name_cn": pos_name_cn,
            "type": current_type,
            "line_manager": line_manager,
            "opening_year": opening,
            "legal_category": legal_cat,
            "remark": remark,
            "depth": depth,
            "line_idx": None,  # 稍后填充
        }
        positions.append(node_stack_entry)

    # ── 后处理：从树结构推断隶属公司 ──
    # 简化策略：解析文件，找到每个岗位行之前的最近公司节点
    _infer_companies_from_text(lines, positions, company_map)

    return positions, company_map


def _infer_companies_from_text(lines: List[str], positions: List[Dict], company_map: Dict):
    """扫描原文的 tree 代码块，为每个岗位行找到最近的父公司。"""
    pos_numbers = {p["number"] for p in positions}
    current_company = None
    in_tree = False

    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith("```"):
            if "tree" in stripped_line.lower() or stripped_line == "```tree":
                in_tree = True
                continue
            elif stripped_line == "```":
                in_tree = False
                continue
        if stripped_line.startswith("# 完整组织架构树") or stripped_line == "完整组织架构树":
            in_tree = True
            continue

        if not in_tree:
            continue

        if not line.strip():
            continue

        stripped = line.lstrip()
        clean = stripped.lstrip("│├└─ ")
        if not clean:
            continue

        # 公司行（仅在 tree 块内）
        if not P_NUM_RE.search(clean):
            cm = COMPANY_RE.search(clean)
            if cm:
                current_company = cm.group(1).strip()

        # 岗位行
        pm = P_NUM_RE.search(clean)
        if pm:
            pn = pm.group(1)
            if pn in pos_numbers:
                for p in positions:
                    if p["number"] == pn and not p.get("company"):
                        p["company"] = current_company
                        break


def parse_rules(md_text: str) -> Dict:
    """解析 Position.md 规则文件：级别对照、工作范围/国家编号、工作地点。"""
    rules = {
        "levels": {},       # {code: {ic, mgmt, label}}
        "scopes": {},       # {name: {code, suffix}}
        "work_locations": [],
    }

    # 提取级别对照表
    level_section = re.search(r"级别对照.*?\n(\|.+\n)+", md_text, re.DOTALL)
    if level_section:
        rows = level_section.group(1).strip().split("\n")[2:]  # 跳过表头和分隔线
        for row in rows:
            cols = [c.strip() for c in row.split("|")[1:-1]]
            if len(cols) >= 3:
                ic = cols[0].strip()
                mgmt = cols[1].strip()
                level_str = cols[2].strip()
                # 解析级别代码（如 "B7b - M7" → B7b, M7）
                parts = [p.strip() for p in level_str.split("-")]
                if len(parts) == 2:
                    ic_code, mgmt_code = parts
                    if ic_code and ic_code != "N/A":
                        rules["levels"][ic_code] = {"ic": ic_code, "mgmt": mgmt_code, "label": ic}
                    if mgmt_code and mgmt_code != "N/A":
                        rules["levels"][mgmt_code] = {"ic": ic_code, "mgmt": mgmt_code, "label": mgmt}

    # 提取工作范围
    scope_section = re.search(r"\|\s*编号\s*\|\s*工作范围.*?\n(\|.+\n)+", md_text, re.DOTALL)
    if scope_section:
        rows = scope_section.group(1).strip().split("\n")[2:]
        for row in rows:
            cols = [c.strip() for c in row.split("|")[1:-1]]
            if len(cols) >= 3:
                code = cols[0].strip()
                name = cols[1].strip()
                suffix = cols[2].strip()
                rules["scopes"][name] = {"code": code, "suffix": suffix}

    # 提取工作地点
    loc_section = re.search(r"工作地点.*?：([\s\S]*?)(?=###|\n##)", md_text)
    if loc_section:
        for line in loc_section.group(1).strip().split("\n"):
            loc = line.strip().lstrip("-").strip()
            if loc:
                rules["work_locations"].append(loc)

    return rules


def clean_data(positions: List[Dict], rules: Dict, company_map: Dict = None) -> Tuple[List[Dict], List[str]]:
    """清洗数据：验证字段、补全默认值、生成显示名。

    工作地点从公司名字推断（company_map），不依赖岗位编号。
    返回 (cleaned_positions, warnings)。
    """
    warnings = []
    cleaned = []

    for pos in positions:
        row = dict(pos)  # 复制

        # 去除字符串值中的控制字符（换行、制表符等）
        for k, v in row.items():
            if isinstance(v, str):
                row[k] = v.replace("\n", " ").replace("\r", "").replace("\t", " ").strip()

        # 1. 验证岗位编号格式
        if not P_NUM_RE.match(row["number"]):
            warnings.append(f"⚠️ {row['number']}: 岗位编号格式异常")

        # 2. 职位名：确保不含工作范围前缀（Position.md 规则）
        # "Family Chairman & General Manager" → "Chairman & General Manager"（含）
        # "Global Chief Executive Officer" → "Chief Executive Officer"（去 Global）
        # "Regional General Manager" → "General Manager"（去 Regional）
        name = row["name_en"]
        for prefix in ["Family ", "Global ", "Regional "]:
            if name.startswith(prefix) and len(name) > len(prefix):
                # 保留 "Family" 如果它是职位核心（如 "Family Chairman"）
                # 但去除 "Global"/"Regional" 因为它们是工作范围
                if prefix.strip() in ("Global", "Regional"):
                    row["name_en"] = name[len(prefix):]
                    row["warnings"] = row.get("warnings", [])
                    row["warnings"].append(f"已去除工作范围前缀 '{prefix.strip()}'")
        row["position_name"] = row["name_en"]  # 标准职位名字段

        # 3. 生成 Org-Chart 中的显示名
        display_name = f"{row['number']} - {pos['name_en']} - {row['name_cn']} (Opening: {row['opening_year']})"
        row["org_chart_display"] = display_name

        # 4. 验证隶属公司
        if not row["company"]:
            warnings.append(f"⚠️ {row['number']}: 无法确定隶属公司")
            row["company"] = "未知"

        # 5. 推断法律强制（从 legal_cat 或 remark 提取，兼容两种格式）
        if not row.get("legal_category") and not row.get("legal_cat"):
            remark = row.get("remark", "") or ""
            if "法律强制" in remark:
                row["legal_category"] = "法律强制·内部全职不可外包"
            elif "可选" in remark:
                row["legal_category"] = "可选（集团内控推荐）"
            elif "纯后勤" in remark:
                row["legal_category"] = "纯后勤可选"
        # 如果有 legal_cat（从解析器提取），复制到 legal_category
        if row.get("legal_cat") and not row.get("legal_category"):
            row["legal_category"] = row["legal_cat"]

        # 6. 推断级别
        if not row.get("level"):
            row["level"] = _infer_level(row.get("name_en", ""))

        # 6b. 推断国家/地区范围（用于推断工作地点）
        if not row.get("country_scope"):
            row["country_scope"] = _infer_country_scope(pos["number"])

        # 7. 推断工作地点（从公司名字的地点信息推断，不依赖岗位编号）
        if not row.get("work_location") or row["work_location"] == "未指定":
            company = row.get("company", "")
            if company_map and company in company_map:
                loc = company_map[company].get("location", "")
                if loc:
                    row["work_location"] = loc

        # 8. 填充默认值
        row.setdefault("closing_date", "N/A")
        row.setdefault("work_location", "未指定")
        row.setdefault("prev_position", "N/A")
        row.setdefault("prev_company", "N/A")

        cleaned.append(row)

    return cleaned, warnings


def validate_and_report(cleaned: List[Dict]) -> Dict:
    """生成清洗报告。"""
    report = {
        "total_positions": len(cleaned),
        "valid": 0,
        "fixed": 0,
        "warnings": [],
        "errors": [],
    }

    for pos in cleaned:
        errs = []
        warns = []

        # 检查必要字段
        for field in ["number", "position_name", "company", "opening_year"]:
            if not pos.get(field):
                errs.append(f"缺少必填字段: {field}")

        # 检查岗位编号格式
        if pos.get("number") and not P_NUM_RE.match(pos["number"]):
            errs.append(f"岗位编号格式异常: {pos['number']}")

        # 检查公司是否为已知公司（从 parsed company_map 中验证）
        # （此处简化，实际可交叉验证）

        # 记录清洗过程中的修复
        if pos.get("warnings"):
            warns.extend(pos["warnings"])

        if errs:
            report["errors"].append({"position": pos.get("number", "?"), "errors": errs})
        elif warns:
            report["warnings"].extend([{"position": pos["number"], "warning": w} for w in warns])
            report["fixed"] += 1
        else:
            report["valid"] += 1

    return report


# ─── CSV 输出 ─────────────────────────────────────────────

CSV_HEADERS = [
    "职位", "职位类型", "岗位编号", "隶属公司", "级别", "国家或地区",
    "职位开启日", "职位关闭日", "工作地点", "工作职责描述",
    "直线经理", "虚线经理", "法律强制/可选", "Org-Chart中的显示",
    "之前的职位", "之前的公司", "备注"
]


def _format_manager(manager_number: str, positions_map: Dict) -> str:
    """格式化直线经理字段：'职位名 (P编号)'"""
    if not manager_number or manager_number == "N/A":
        return "N/A"
    p = positions_map.get(manager_number)
    if p:
        name = p.get("position_name") or p.get("name_en", "")
        return f"{name} ({manager_number})"
    return f"未知 ({manager_number})"


def _infer_country_scope(number: str) -> Optional[str]:
    """从岗位编号推断国家或地区。"""
    parts = number.split("-")
    if len(parts) < 2:
        return None
    # Country scope: Pxxx-4-{country}（parts[-2]=='4'）
    if len(parts) >= 3 and parts[-2] == "4":
        country_map = {
            "1": "Country·比利时", "2": "Country·丹麦", "3": "Country·瑞典",
            "4": "Country·荷兰", "5": "Country·卢森堡", "6": "Country·英国",
            "7": "Country·美国", "8": "Country·中国香港", "9": "Country·中国上海",
        }
        country_id = parts[-1]
        return country_map.get(country_id, f"Country·未知({country_id})")
    suffix = parts[-1]
    if suffix == "1":
        return "Family"
    elif suffix == "2":
        return "Global"
    elif suffix == "3":
        return "Regional"
    return None


# 工作地点映射（从国家或地区推断）
SCOPE_TO_LOCATION = {
    "Family": "卢森堡",
    "Global": "比利时布鲁塞尔",
    "Regional": "未指定",
    "Country·比利时": "比利时布鲁塞尔",
    "Country·丹麦": "丹麦",
    "Country·瑞典": "瑞典",
    "Country·荷兰": "荷兰",
    "Country·卢森堡": "卢森堡",
    "Country·英国": "英国伦敦",
    "Country·美国": "美国特拉华",
    "Country·中国香港": "中国香港",
    "Country·中国上海": "中国上海",
}

# 常见职位→级别映射（用于 Org-Chart2 等无显式级别的数据）
TITLE_LEVEL_MAP = {
    "Managing Director": "M11a",
    "Global AML Supervisor": "M10",
    "Tax & TP Officer": "M7",
    "Chief Consultant": "M9a",
    "Group Chief Executive": "M12b",
    "Chief Executive Officer": "M12b",
    "Group Global AML Compliance Officer": "M10",
    "MLRO Governance Supervisor": "M7",
    "Investment Governance Manager": "M10",
    "Contract Governance Manager": "M9a",
    "Transfer Pricing Supervisor": "M7",
    "Internal Audit Manager": "M8a",
    "Senior Global Internal Audit Specialist": "B8a",
    "Corporate President": "M11a",
    "General Manager": "M11a",
    "Executive Assistant": "B8a",
    "Finance Manager": "M8a",
    "Statutory MLRO": "M8a",
    "AML Deputy Specialist": "B7a",
    "Operation Coordinator": "M8b",
    "SSC Finance Manager": "M8a",
    "Receivable & Payable Accountant": "B7a",
    "Consolidation Accountant": "B7b",
    "Payroll Calculation Specialist": "B7a",
    "HR Master Data Specialist": "B7a",
    "System Support Specialist": "M8a",
    "Vendor Governance Manager": "M8a",
    "Real Estate General Manager": "M9a",
    "Real Estate Compliance Manager": "B8a",
    "Real Estate Compliance Director": "M9a",
    "SSC Operation Coordinator": "M8b",
    "SSC AML Deputy Backup": "B7b",
    "SSC Statutory MLRO": "M8a",
    "IT General Manager": "M11a",
    "IT Delivery Supervisor": "M7",
    "IT Compliance": "M8a",
    "Finance Manager": "M8a",
    "Payment Approving Supervisor": "M7",
    "Chief Accountant": "B7b",
    "Tax & Treasury Officer": "B7a",
    "HR & Admin Manager": "M8a",
    "Corporate Legal Officer": "B8a",
    "Internal Audit Liaison": "M8a",
    "Outsourcing Governance Coordinator": "M7",
    "Senior Domestic HR Manager": "M8a",
    "China SSC General Manager": "M11a",
    "Finance Shared Service Supervisor": "M7",
    "HR Shared Service Supervisor": "M7",
    "IT Shared Service Supervisor": "M7",
    "Admin Shared Supervisor": "M7",
    "Admin & IT Shared Service Manager": "M8a",
    "SPF Asset Compliance Manager": "M8a",
    "SPF Real Estate Administrator": "B7a",
    "Corporate Secretary": "M9a",
    "Corporate Treasurer": "M9a",
    "Corporate President": "M11a",
    "Legal Representative": "M9a",
    "Film Investment Manager": "M8a",
}


def _infer_level(pos_name_en: str) -> str:
    """从职位名推断级别。"""
    # 精确匹配
    if pos_name_en in TITLE_LEVEL_MAP:
        return TITLE_LEVEL_MAP[pos_name_en]
    # 模糊匹配
    for key, level in TITLE_LEVEL_MAP.items():
        if key in pos_name_en:
            return level
    return ""


def to_csv(cleaned: List[Dict], positions_map: Dict = None) -> str:
    """将清洗后的数据输出为 CSV 字符串（17 列，与 Position.csv 格式对齐）。"""
    # 先清理控制字符
    for pos in cleaned:
        for k, v in pos.items():
            if isinstance(v, str):
                pos[k] = v.replace("\n", " ").replace("\r", "").replace("\t", " ")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_HEADERS, lineterminator="\n")
    writer.writeheader()

    pm = positions_map or {p["number"]: p for p in cleaned}

    for pos in cleaned:
        row = {
            "职位": pos.get("position_name", pos.get("name_en", "")),
            "职位类型": pos.get("type", ""),
            "岗位编号": pos.get("number", ""),
            "隶属公司": pos.get("company", ""),
            "级别": pos.get("level", ""),
            "国家或地区": pos.get("country_scope", ""),
            "职位开启日": pos.get("opening_year", ""),
            "职位关闭日": pos.get("closing_date", "N/A"),
            "工作地点": pos.get("work_location", ""),
            "工作职责描述": pos.get("job_responsibility", ""),
            "直线经理": _format_manager(pos.get("line_manager"), pm),
            "虚线经理": pos.get("dotted_manager", "N/A"),
            "法律强制/可选": pos.get("legal_category", ""),
            "Org-Chart中的显示": pos.get("org_chart_display", ""),
            "之前的职位": pos.get("prev_position", "N/A"),
            "之前的公司": pos.get("prev_company", "N/A"),
            "备注": pos.get("remark", ""),
        }
        writer.writerow(row)

    return output.getvalue()


# ─── 主清洗流程 ─────────────────────────────────────────────

def run_clean(orgchart_md: str, rules_md: str) -> Dict:
    """执行完整清洗流程，返回 {cleaned, csv_text, report}。"""
    # 1. 解析 Org-Chart.md
    positions, company_map = parse_orgchart(orgchart_md)

    # 2. 解析 Position.md 规则
    rules = parse_rules(rules_md)

    # 3. 清洗数据（传入公司信息用于推断工作地点）
    cleaned, warnings = clean_data(positions, rules, company_map=company_map)

    # 4. 补充推断字段（级别、国家范围）
    for pos in cleaned:
        if not pos.get("level"):
            pos["level"] = _infer_level(pos.get("name_en", ""))
        if not pos.get("country_scope"):
            pos["country_scope"] = _infer_country_scope(pos["number"])

    # 5. 验证并生成报告
    report = validate_and_report(cleaned)
    report["warnings"].extend([{"position": "-", "warning": w} for w in warnings])

    # 6. 输出 CSV
    csv_text = to_csv(cleaned)

    return {
        "cleaned": cleaned,
        "csv_text": csv_text,
        "report": report,
        "company_map": company_map,
    }
