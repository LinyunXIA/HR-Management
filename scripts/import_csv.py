"""CLI 导入脚本。

用法：
    python -m scripts.import_csv testingdata/原始文件/Position.csv [--reset] [--strict-legal]
    --reset         先清空全部数据表再导入（重建空库）
    --strict-legal  法律分类不在主数据字典时该行报错不导入（默认仅告警）

三环境行为（PRD §7D.3）：
    - dev  : --reset / drop_all 允许
    - test : --reset / drop_all 允许
    - prod : --reset / drop_all 直接拒绝（FATAL）
"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import APP_ENV, Base, SessionLocal, assert_writable, engine, startup_banner
from app.import_csv import import_csv
from app.seed import seed_master_data


def main():
    args = sys.argv[1:]
    path = "testingdata/原始文件/Position.csv"
    reset = False
    strict_legal = False
    if "--reset" in args:
        reset = True
        args.remove("--reset")
    if "--strict-legal" in args:
        strict_legal = True
        args.remove("--strict-legal")
    if args:
        path = args[0]

    print(startup_banner(), file=sys.stderr)

    if reset:
        try:
            assert_writable("--reset / drop_all")
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        print(f"[reset] APP_ENV={APP_ENV} 清空数据表…", file=sys.stderr)
        Base.metadata.drop_all(bind=engine)

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_master_data(db)

    db = SessionLocal()
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            report = import_csv(db, reader, strict_legal=strict_legal)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()