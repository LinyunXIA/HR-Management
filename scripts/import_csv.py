"""CLI 导入脚本。

用法：
    python -m scripts.import_csv testingdata/Position.csv [--reset]
    --reset  先清空全部数据表再导入（重建空库）
"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, SessionLocal, engine
from app.import_csv import import_csv


def main():
    args = sys.argv[1:]
    path = "testingdata/Position.csv"
    reset = False
    if "--reset" in args:
        reset = True
        args.remove("--reset")
    if args:
        path = args[0]

    if reset:
        print("[reset] 清空数据表…", file=sys.stderr)
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            report = import_csv(db, reader)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
