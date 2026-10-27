"""合并 E1 分片结果：按 (cond, trial) 去重（后写的文件覆盖先写的），
等待全部条件各 10 trial 完成后输出总汇总表，存 e1_results_n10.json。

用法：python merge_e1.py [--wait-minutes 20]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import time

from run_e1_full import aggregate

BASE = os.path.dirname(os.path.abspath(__file__))


def part_files() -> list[str]:
    """收集所有分片：旧 e1_part_* 与新 e1_T*_C*_n*.json，按 mtime 升序（新文件覆盖旧）。"""
    pats = [os.path.join(BASE, "e1_part_*.json"),
            os.path.join(BASE, "e1_T?_C?_n*.json")]
    files = sorted({f for p in pats for f in glob.glob(p)},
                   key=os.path.getmtime)
    return files


def load_merged() -> dict:
    merged: dict[tuple, dict] = {}
    for path in part_files():
        try:
            for rec in json.load(open(path, encoding="utf-8")):
                key = (rec.get("task", "T1"), rec["cond"], rec["trial"])
                merged[key] = rec   # mtime 晚的文件覆盖早的
        except (json.JSONDecodeError, OSError):
            pass   # 文件尚在写入
    return merged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-minutes", type=int, default=0)
    ap.add_argument("--out", type=str, default="e1_results_all.json")
    args = ap.parse_args()
    deadline = time.time() + args.wait_minutes * 60

    def counts(merged) -> dict:
        c: dict[str, int] = {}
        for (task, cond, _), _rec in merged.items():
            c[f"{task}/{cond}"] = c.get(f"{task}/{cond}", 0) + 1
        return c

    merged = load_merged()
    while args.wait_minutes > 0 and time.time() < deadline:
        print(f"等待跑批完成... 各组记录数: {counts(merged)}", flush=True)
        time.sleep(60)
        merged = load_merged()

    records = list(merged.values())
    print(aggregate(records))
    out = os.path.join(BASE, args.out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print(f"\n合并 {len(records)} 条记录 -> {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
