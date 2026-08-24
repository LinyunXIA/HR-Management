"""一次性主数据迁移：PostgreSQL 存量库 → SQLite（v2.5 数据库切换，PRD §7D）。

用法（在旧 PG 库仍可达时执行一次，可重复跑、幂等合入）：

    # 先确保一次性驱动可用（运行时已不再依赖 psycopg2）：
    .venv/bin/pip install psycopg2-binary

    # 迁移 dev（默认 APP_ENV 或 --env 指定 test/prod）：
    .venv/bin/python -m scripts.migrate_pg_master_data \
        --source postgresql://postgres:***@localhost:5432/hr_db_dev [--env dev]

迁移范围（仅主数据字典，用户与岗位域不迁移——admin 由种子重建、岗位可用
Org-Chart3 清洗随时重建）：
    companies / external_companies / company_shareholders / countries /
    levels / work_locations / scopes / legal_categories / position_types /
    tax_zones / employment_tax_items

行为：
- 目标库先 create_all + seed_master_data 兜底，再按唯一键逐行 upsert 合入；
- 已存在行用源数据覆盖非键字段（以源为权威）；关联引用（国家/公司/税区）
  按「名称」解析，目标侧缺失时自动补建；
- 输出各表 {inserted, updated} 计数报告。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(description="PG → SQLite 主数据字典迁移（一次性、幂等）")
    ap.add_argument("--source", required=True,
                    help="源 PostgreSQL 连接串 postgresql://user:pwd@host:port/hr_db_{env}")
    ap.add_argument("--env", choices=("dev", "test", "prod"), default=None,
                    help="目标环境（默认取 APP_ENV / .env，回退 dev）")
    args = ap.parse_args()

    if args.env:
        os.environ["APP_ENV"] = args.env

    try:
        import psycopg2  # noqa: F401
    except ImportError:
        print("[FATAL] 缺少 PostgreSQL 驱动。本系统运行时已不依赖 psycopg2，"
              "请临时安装后重试：.venv/bin/pip install psycopg2-binary", file=sys.stderr)
        sys.exit(1)

    # ---- 目标（SQLite）：延迟导入，APP_ENV 已就位 ----
    from app import models as M
    from app.db import Base, SessionLocal, engine, startup_banner
    from app.seed import seed_master_data

    print(startup_banner(), file=sys.stderr)
    print(f"[migrate] 源库: {args.source}", file=sys.stderr)

    Base.metadata.create_all(bind=engine)

    import psycopg2
    from psycopg2.extras import RealDictCursor

    src = psycopg2.connect(args.source)
    src.set_session(readonly=True)

    def fetch(sql):
        with src.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]

    report: dict[str, list[int]] = {}

    def bump(table, inserted=0, updated=0):
        cur = report.setdefault(table, [0, 0])
        cur[0] += inserted
        cur[1] += updated

    db = SessionLocal()
    try:
        seed_master_data(db)  # 基线字典兜底（幂等）

        def upsert(model, key_filter, values):
            """按唯一键 upsert：存在→覆盖非键字段，缺失→插入。"""
            row = db.query(model).filter_by(**key_filter).first()
            if row:
                for k, v in values.items():
                    if k not in key_filter:
                        setattr(row, k, v)
                bump(model.__tablename__, updated=1)
                return row
            row = model(**{**key_filter, **values})
            db.add(row)
            db.flush()
            bump(model.__tablename__, inserted=1)
            return row

        # 1) 国家/地区（其余表的引用基础）
        for r in fetch("SELECT name, code FROM countries ORDER BY id"):
            upsert(M.Country, {"name": r["name"]}, {"code": r["code"]})
        db.flush()

        # 2) 级别 / 工作地点 / 范围 / 法律分类 / 职位类型
        for r in fetch("SELECT code, label, is_management, sort_order FROM levels ORDER BY sort_order"):
            upsert(M.Level, {"code": r["code"]},
                   {"label": r["label"], "is_management": r["is_management"],
                    "sort_order": r["sort_order"] or 0})
        for r in fetch("SELECT name, country, city, sort_order FROM work_locations ORDER BY sort_order"):
            upsert(M.WorkLocation, {"name": r["name"]},
                   {"country": r["country"], "city": r["city"], "sort_order": r["sort_order"] or 0})
        for r in fetch("SELECT code, label, suffix_code, sort_order FROM scopes ORDER BY sort_order"):
            upsert(M.ScopeDef, {"code": r["code"]},
                   {"label": r["label"], "suffix_code": r["suffix_code"], "sort_order": r["sort_order"] or 0})
        for r in fetch("SELECT name, sort_order FROM legal_categories ORDER BY sort_order"):
            upsert(M.LegalCategoryDef, {"name": r["name"]}, {"sort_order": r["sort_order"] or 0})
        for r in fetch("SELECT name, sort_order FROM position_types ORDER BY sort_order"):
            upsert(M.PositionType, {"name": r["name"]}, {"sort_order": r["sort_order"] or 0})
        db.flush()

        # 3) 隶属公司 / 外部合作公司
        for r in fetch("SELECT name, is_active, opening_date, closing_date FROM companies ORDER BY id"):
            upsert(M.Company, {"name": r["name"]},
                   {"is_active": r["is_active"], "opening_date": r["opening_date"],
                    "closing_date": r["closing_date"]})
        for r in fetch("SELECT name, remark, is_active, opening_date, closing_date "
                       "FROM external_companies ORDER BY id"):
            upsert(M.ExternalCompany, {"name": r["name"]},
                   {"remark": r["remark"], "is_active": r["is_active"],
                    "opening_date": r["opening_date"], "closing_date": r["closing_date"]})
        db.flush()

        def company_by_name(name):
            return db.query(M.Company).filter(M.Company.name == name).first() if name else None

        # 4) 税区 + 用工税额（国家级或城市级挂载，按国家名解析）
        for r in fetch(
            "SELECT tz.level, c.name AS country_name, tz.city, tz.sort_order "
            "FROM tax_zones tz JOIN countries c ON c.id = tz.country_id ORDER BY tz.id"
        ):
            country = company_by_name(r["country_name"]) or db.query(M.Country).filter(
                M.Country.name == r["country_name"]).first()
            if not country:
                country = M.Country(name=r["country_name"], code="")
                db.add(country)
                db.flush()
            upsert(M.TaxZone, {"level": r["level"], "country_id": country.id, "city": r["city"]},
                   {"sort_order": r["sort_order"] or 0})
        db.flush()

        def tax_zone_lookup(level, country_name, city):
            c = db.query(M.Country).filter(M.Country.name == country_name).first()
            if not c:
                return None
            return db.query(M.TaxZone).filter_by(level=level, country_id=c.id, city=city).first()

        for r in fetch(
            "SELECT i.item_name, i.tax_rate, i.is_active, tz.level, c.name AS country_name, tz.city "
            "FROM employment_tax_items i "
            "JOIN tax_zones tz ON tz.id = i.tax_zone_id "
            "JOIN countries c ON c.id = tz.country_id ORDER BY i.id"
        ):
            tz = tax_zone_lookup(r["level"], r["country_name"], r["city"])
            if tz is None:
                print(f"[warn] 跳过税额项 {r['item_name']!r}：税区未解析"
                      f"（{r['level']}/{r['country_name']}/{r['city']}）", file=sys.stderr)
                continue
            upsert(M.EmploymentTaxItem, {"tax_zone_id": tz.id, "item_name": r["item_name"]},
                   {"tax_rate": r["tax_rate"], "is_active": r["is_active"]})

        # 5) 股权结构（三来源互斥；内部/外部公司按名解析，目标缺公司则跳过告警）
        for r in fetch(
            "SELECT co.name AS company_name, ic.name AS internal_name, ec.name AS external_name, "
            "cs.person_name, cs.ownership_pct, cs.sort_order "
            "FROM company_shareholders cs "
            "JOIN companies co ON co.id = cs.company_id "
            "LEFT JOIN companies ic ON ic.id = cs.internal_company_id "
            "LEFT JOIN external_companies ec ON ec.id = cs.external_company_id "
            "ORDER BY cs.id"
        ):
            comp = company_by_name(r["company_name"])
            if not comp:
                print(f"[warn] 跳过股东行：公司 {r['company_name']!r} 目标库不存在", file=sys.stderr)
                continue
            internal = company_by_name(r["internal_name"]) if r["internal_name"] else None
            external = db.query(M.ExternalCompany).filter(
                M.ExternalCompany.name == r["external_name"]).first() if r["external_name"] else None
            if r["internal_name"] and not internal:
                print(f"[warn] 跳过股东行：内部股东 {r['internal_name']!r} 目标库不存在", file=sys.stderr)
                continue
            if r["external_name"] and not external:
                print(f"[warn] 跳过股东行：外部股东 {r['external_name']!r} 目标库不存在", file=sys.stderr)
                continue
            exists = db.query(M.CompanyShareholder).filter_by(
                company_id=comp.id,
                internal_company_id=internal.id if internal else None,
                external_company_id=external.id if external else None,
                person_name=r["person_name"],
            ).first()
            if exists:
                exists.ownership_pct = r["ownership_pct"]
                exists.sort_order = r["sort_order"] or 0
                bump("company_shareholders", updated=1)
            else:
                db.add(M.CompanyShareholder(
                    company_id=comp.id,
                    internal_company_id=internal.id if internal else None,
                    external_company_id=external.id if external else None,
                    person_name=r["person_name"],
                    ownership_pct=r["ownership_pct"],
                    sort_order=r["sort_order"] or 0,
                ))
                bump("company_shareholders", inserted=1)

        db.commit()

        print("\n━━ 迁移完成 ━━")
        for tbl, (ins, upd) in report.items():
            print(f"  {tbl:24s} inserted={ins:<4d} updated={upd}")
        skipped_users = ("users / user_companies / user_apis 不迁移"
                         "（admin 由种子重建；hr 账号如需保留请在系统内手动重建）")
        print(f"  {'(未迁移)':24s} {skipped_users}")
    finally:
        db.close()
        src.close()


if __name__ == "__main__":
    main()
