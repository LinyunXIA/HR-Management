"""Org-Chart.md 解析（Org-Chart3 单一格式）+ Position.md 规则解析 + 数据清洗/校验/CSV 导出。

格式规范（唯一支持格式，见 testingdata/原始文件/Org-Chart3.md）：
- 树区以 `# 完整组织架构树…` 标题开始，至下一个 `#` 标题结束
- 根节点行（如「创始Peeters家族自然人股东」）忽略
- 公司节点：`公司名（工作地点 | 年份｜开户行…）`
- 类型标记行：🧑‍💼 In-house Full-time / 👨‍👩‍👧 Family Volunteer Unpaid / 📋 Outsourced External
- 岗位行：`英文名 - 中文名 【法律分类】(Opening: YYYY)`（无 P 编号；即使带编号也一律忽视）
- 岗位下方 `权责说明：…` 续行 → 工作职责描述

编号策略：**源文件编号一律忽视，CSV「岗位编号」列留空**，由系统在导入时分配
（正式岗 P1/P2/…、外包岗 PA1/PA2/…，见 app/helpers.generate_number 与 app/import_csv）。

直线经理推断：真实树祖先 —— 岗位的经理 = 树中最近的**上层岗位节点**（跳过公司/类型分组节点）；
同层兄弟节点互不挂靠；公司节点清空其深度的祖先栈（跨公司不串报线）；
无显式层级 → N/A（用户后续在 UI 手动设置）。

流程：
1. parse_orgchart(md_text) → 提取全部岗位（🧑‍💼 + 👨‍👩‍👧 + 📋），含隶属公司、直线经理、职责
2. parse_rules(md_text) → 提取级别映射等规则
3. clean_data(positions, rules, company_map) → 字段校验/补全/显示名
4. validate_and_report(cleaned) → 清洗报告（通过/修复/警告/错误）
5. to_csv(cleaned) → 输出 17 列 CSV 文本
"""
import csv
import io
import re
from typing import Dict, List, Optional, Tuple

# ─── 行类型标记（Position.md §9 映射）─────────────────────────────
#   Family Volunteer Unpaid → Consultant
#   In-house Full-time     → Employee
#   Outsourced External    → External Employee
TYPE_MAP = {
    "🧑‍💼": "Employee",
    "👨‍👩‍👧": "Consultant",
    "📋": "External Employee",
}

# 源文件历史 P 编号（识别后剥离忽视）
LEGACY_NUMBER_RE = re.compile(r"^P(A?\d+)(?:-\d+(?:-\d+)?)?\s*-?\s*")

# 岗位行正则：英文名 - 中文名 【legal】(Opening: YYYY)
POS_RE = re.compile(r"^(.+?)\s*-\s*(.+?)\s*(?:【(.+?)】)?\s*\(Opening:\s*(\d{4})")
# (Opening:YYYY) 之后的尾随备注：【...】
POS_TRAIL_RE = re.compile(r"【(.+)】")

# 公司节点正则：公司名（详情），详情含 `地点 | 年份｜…`
COMPANY_RE = re.compile(r"^(.+?)（(.+?)）")
COMPANY_DETAIL_RE = re.compile(r"(.+?)\s*\|\s*(\d{4})")

# 树区起止标记
TREE_START_MARK = "完整组织架构树"


def _line_indent(line: str) -> int:
    """行首树形缩进宽度：首个「非前缀字符」的位置。

    前缀字符 = 空白 + 树干分支符（│ ├ └）；刻意**不含 ─**——
    使 "├─"/"├──"/"└─" 等不同画法的同层节点宽度一致（首个 ─ 位置相同），
    子层因多一级 "│   "/"    " 前缀而严格更大。仅做相对比较，
    对固定 4 空格、Tab、混用等源文件均稳健。
    """
    for i, ch in enumerate(line):
        if ch not in "│├└ \t":
            return i
    return len(line)


def _clean_text(line: str) -> str:
    """去除树字符前缀（│ ├ └ ─ 与空白）。"""
    return line.strip().lstrip("│├└─ ").strip()


def parse_orgchart(md_text: str) -> Tuple[List[Dict], Dict]:
    """解析 Org-Chart3 格式树形结构，提取全部三类岗位。

    返回 (positions, company_map)。
    positions 元素字段：name_en/name_cn/type/opening_year/legal_category/remark/
                        company/line_manager/job_responsibility/depth
    """
    lines = md_text.split("\n")
    positions: List[Dict] = []
    company_map: Dict[str, dict] = {}

    current_type: Optional[str] = None      # 最近类型标记行
    current_company: Optional[str] = None   # 最近公司节点（隶属公司）
    # 真实树祖先链：[(缩进宽度, 岗位)]，按缩进相对比较维护（不依赖固定步长）。
    # 岗位入链前弹出缩进 ≥ 自身的残留项（兄弟/旧子树）；公司节点按其宽度清栈（跨公司不串报线）。
    ancestor_chain: List[Tuple[int, Dict]] = []
    in_tree = False
    started = False                         # 是否已进入过树区（遇其他标题即结束）

    for raw_line in lines:
        stripped_line = raw_line.strip()
        if not stripped_line:
            continue

        # ── 标题行：树区起点 / 终点 ──
        if stripped_line.startswith("#"):
            if TREE_START_MARK in stripped_line:
                in_tree = True
                started = True
            elif in_tree:
                in_tree = False  # 下一个章节（开户行信息/岗位变更信息等）→ 树区结束
            continue

        # ── ```tree 围栏兼容（可选包裹）──
        if stripped_line.startswith("```"):
            if "tree" in stripped_line.lower():
                in_tree = True
                started = True
            elif started:
                in_tree = False
            continue

        if not in_tree:
            continue

        clean = _clean_text(raw_line)
        if not clean:
            continue

        # ── 1. 类型标记行 ──
        line_type = None
        for marker, type_name in TYPE_MAP.items():
            if marker in clean:
                line_type = type_name
                break
        if line_type is not None:
            current_type = line_type
            continue

        # ── 2. 公司节点（含 （...） 且非岗位行）──
        if "(Opening:" not in clean:
            cm = COMPANY_RE.search(clean)
            if cm:
                company_name = cm.group(1).strip()
                detail = cm.group(2)
                dm = COMPANY_DETAIL_RE.search(detail)
                location = dm.group(1).strip() if dm else detail.split("｜")[0].split("|")[0].strip()
                company_map[company_name] = {
                    "name": company_name,
                    "location": location,
                    "year": dm.group(2) if dm else None,
                }
                current_company = company_name
                # 公司子树边界：弹出缩进 ≥ 该公司宽度的岗位祖先链（跨公司不串报线；
                # 即使公司行带树干前缀也能正确清栈）
                w_company = _line_indent(raw_line)
                while ancestor_chain and ancestor_chain[-1][0] >= w_company:
                    ancestor_chain.pop()
                continue
            # 权责说明续行 → 归属最近的岗位
            if stripped_line.startswith("权责说明") or clean.startswith("权责说明"):
                body = clean.split("：", 1)[-1].split(":", 1)[-1].strip()
                if positions and body:
                    prev = positions[-1]["job_responsibility"]
                    positions[-1]["job_responsibility"] = f"{prev} {body}".strip() if prev else body
                continue
            continue  # 其他非岗位行（根节点等）忽略

        # ── 3. 岗位行 ──
        if not current_type:
            continue  # 类型标记前出现的岗位行跳过

        text = LEGACY_NUMBER_RE.sub("", clean)  # 剥离并忽视源文件编号
        pm = POS_RE.search(text)
        if not pm:
            continue

        pos_name_en = pm.group(1).strip()
        pos_name_cn = pm.group(2).strip()
        legal_cat = pm.group(3).strip() if pm.group(3) else None
        opening = pm.group(4)
        after_opening = text[pm.end():]
        tm = POS_TRAIL_RE.search(after_opening)
        remark = tm.group(1).strip() if tm else None

        # 直线经理 = 真实树祖先：弹出缩进 ≥ 自身的残留项后取链顶（兄弟不互挂）
        w_self = _line_indent(raw_line)
        while ancestor_chain and ancestor_chain[-1][0] >= w_self:
            ancestor_chain.pop()
        line_manager = ancestor_chain[-1][1] if ancestor_chain else None
        depth = len(ancestor_chain)
        entry = {
            "name_en": pos_name_en,
            "name_cn": pos_name_cn,
            "type": current_type,
            "opening_year": opening,
            "legal_category": legal_cat,
            "remark": remark,
            "company": current_company,
            "line_manager": line_manager["name_en"] if line_manager else None,
            "job_responsibility": None,
            "depth": depth,
        }
        positions.append(entry)
        ancestor_chain.append((w_self, entry))  # 入链，供更深节点挂靠

    return positions, company_map


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

    工作地点与国家或地区均从公司名字的地点信息推断（不依赖岗位编号——编号已由系统分配）。
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

        # 1. 职位名：去除工作范围前缀（Position.md 规则：职位名不含工作范围）
        name = row["name_en"]
        for prefix in ["Global ", "Regional ", "Family "]:
            if name.startswith(prefix) and len(name) > len(prefix):
                row["name_en"] = name[len(prefix):]
                row["warnings"] = row.get("warnings", [])
                row["warnings"].append(f"已去除工作范围前缀 '{prefix.strip()}'")
        row["position_name"] = row["name_en"]

        # 2. 生成 Org-Chart 中的显示名（无编号）
        row["org_chart_display"] = (
            f"{row['name_en']} - {row['name_cn']} (Opening: {row['opening_year']})"
        )

        # 3. 验证隶属公司
        if not row["company"]:
            warnings.append(f"⚠️ {row['name_en']}: 无法确定隶属公司")
            row["company"] = "未知"

        # 4. 法律分类（解析器已提取；兜底从备注推断）
        if not row.get("legal_category"):
            remark = row.get("remark", "") or ""
            if "法律强制" in remark:
                row["legal_category"] = "法律强制·内部全职不可外包"
            elif "可选" in remark:
                row["legal_category"] = "可选（集团内控推荐）"
            elif "纯后勤" in remark:
                row["legal_category"] = "纯后勤可选"

        # 5. 推断级别
        if not row.get("level"):
            row["level"] = _infer_level(row.get("name_en", ""))

        # 6. 国家或地区：从公司地点推断（不依赖编号）
        if not row.get("country_scope"):
            row["country_scope"] = _infer_country_scope_from_location(
                company_map.get(row["company"], {}).get("location", "") if company_map else ""
            )

        # 7. 工作地点：直接采用公司节点的地点信息
        if not row.get("work_location") or row["work_location"] == "未指定":
            loc = company_map.get(row["company"], {}).get("location", "") if company_map else ""
            if loc:
                row["work_location"] = loc

        # 8. 填充默认值（岗位编号留空 → 导入时由系统分配）
        row.setdefault("number", "")
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

        # 必要字段
        for field in ["position_name", "company", "opening_year"]:
            if not pos.get(field):
                errs.append(f"缺少必填字段: {field}")

        # 记录清洗过程中的修复
        if pos.get("warnings"):
            warns.extend(pos["warnings"])

        if errs:
            report["errors"].append({"position": pos.get("position_name", "?"), "errors": errs})
        elif warns:
            report["warnings"].extend([{"position": pos["position_name"], "warning": w} for w in warns])
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


# Org-Chart3 树无法表达跨公司虚线汇报（DESIGN §7.3 局限）：清洗期恒输出占位符，
# 导入器将 N/A 视为空，导入后由用户在 UI 手动补线。
DOTTED_MANAGER_PLACEHOLDER = "N/A"


def _format_manager(manager_name: str, positions_map: Dict) -> str:
    """格式化直线经理字段：直接使用职位名（编号由系统分配，无法在清洗期引用）。"""
    if not manager_name or manager_name == "N/A":
        return "N/A"
    return manager_name


# 工作地点 → 国家或地区（从公司地点推断，不依赖岗位编号）
LOCATION_TO_SCOPE = {
    "比利时布鲁塞尔": "Country·比利时",
    "比利时Spa": "Country·比利时",
    "丹麦": "Country·丹麦",
    "瑞典": "Country·瑞典",
    "荷兰": "Country·荷兰",
    "卢森堡": "Country·卢森堡",
    "英国伦敦": "Country·英国",
    "美国特拉华": "Country·美国",
    "美国纽约": "Country·美国",
    "美国洛杉矶": "Country·美国",
    "中国香港": "Country·中国香港",
    "中国上海": "Country·中国上海",
}


def _infer_country_scope_from_location(location: str) -> str:
    """从工作地点字符串推断国家或地区（精确匹配优先，其次包含匹配）。"""
    location = (location or "").strip()
    if not location:
        return "Global"
    if location in LOCATION_TO_SCOPE:
        return LOCATION_TO_SCOPE[location]
    for key, scope in LOCATION_TO_SCOPE.items():
        if key in location or location in key:
            return scope
    return "Global"


# 常见职位→级别映射（用于无显式级别的数据）
TITLE_LEVEL_MAP = {
    "Managing Director": "M11a",
    "Group Chief Executive": "M12b",
    "Chief Executive Officer": "M12b",
    "Statutory Manager": "M9a",
    "Legal Representative": "M9a",
    "Corporate President": "M11a",
    "General Manager": "M11a",
    "Executive Assistant": "B8a",
    "Group Financial Coordinator": "B6",
    "Financial Coordinator": "B6",
    "Finance Manager": "M8a",
    "Internal Audit Manager": "M8a",
}


def _infer_level(pos_name_en: str) -> str:
    """从职位名推断级别（精确匹配优先，其次包含匹配）。"""
    if pos_name_en in TITLE_LEVEL_MAP:
        return TITLE_LEVEL_MAP[pos_name_en]
    for key, level in TITLE_LEVEL_MAP.items():
        if key in pos_name_en:
            return level
    return ""


def to_csv(cleaned: List[Dict], positions_map: Dict = None) -> str:
    """将清洗后的数据输出为 CSV 字符串（17 列；岗位编号留空由系统分配）。"""
    # 先清理控制字符
    for pos in cleaned:
        for k, v in pos.items():
            if isinstance(v, str):
                pos[k] = v.replace("\n", " ").replace("\r", "").replace("\t", " ")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_HEADERS, lineterminator="\n")
    writer.writeheader()

    pm = positions_map or {p["name_en"]: p for p in cleaned}

    for pos in cleaned:
        row = {
            "职位": pos.get("position_name", pos.get("name_en", "")),
            "职位类型": pos.get("type", ""),
            "岗位编号": "",  # 一律留空：导入时由系统按 P/PA 序列分配
            "隶属公司": pos.get("company", ""),
            "级别": pos.get("level", ""),
            "国家或地区": pos.get("country_scope", ""),
            "职位开启日": pos.get("opening_year", ""),
            "职位关闭日": pos.get("closing_date", "N/A"),
            "工作地点": pos.get("work_location", ""),
            "工作职责描述": pos.get("job_responsibility", ""),
            "直线经理": _format_manager(pos.get("line_manager"), pm),
            "虚线经理": pos.get("dotted_managers") and ";".join(pos["dotted_managers"])
            or DOTTED_MANAGER_PLACEHOLDER,
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
    """执行完整清洗流程，返回 {cleaned, csv_text, report, company_map}。"""
    # 1. 解析 Org-Chart.md（Org-Chart3 格式）
    positions, company_map = parse_orgchart(orgchart_md)

    # 2. 解析 Position.md 规则（解析不完整时显式写入报告，不静默回退）
    rules = parse_rules(rules_md)
    rule_warnings: List[str] = []
    n_levels = len(rules.get("levels") or {})
    if n_levels == 0:
        rule_warnings.append(
            "规则文件未解析到「级别对照」（章节标题/表格格式变动？），级别推断退化为内置关键词映射")
    elif n_levels < 19:
        rule_warnings.append(
            f"规则文件级别对照解析不完整（{n_levels}/19 项），请检查 Position.md 表格格式")
    if not (rules.get("scopes") or {}):
        rule_warnings.append("规则文件未解析到工作范围编号表")

    # 3. 清洗数据（传入公司信息用于推断工作地点/国家范围）
    cleaned, warnings = clean_data(positions, rules, company_map=company_map)
    warnings.extend(rule_warnings)

    # 4. 补充推断字段（级别、国家范围兜底）
    for pos in cleaned:
        if not pos.get("level"):
            pos["level"] = _infer_level(pos.get("name_en", ""))
        if not pos.get("country_scope"):
            pos["country_scope"] = _infer_country_scope_from_location(
                company_map.get(pos.get("company", ""), {}).get("location", "")
            )

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
