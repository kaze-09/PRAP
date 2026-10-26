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

PARTS = ["e1_part_C1C2.json", "e1_part_C2.json",
         "e1_part_C3.json", "e1_part_C4.json", "e1_part_C5.json"]
CONDS = ["C1", "C2", "C3", "C4", "C5"]


def load_merged() -> dict:
    merged: dict[tuple, dict] = {}
    for f in PARTS:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
        if not os.path.exists(path):
            continue
        try:
            for rec in json.load(open(path, encoding="utf-8")):
                merged[(rec["cond"], rec["trial"])] = rec   # 后写覆盖先写
        except (json.JSONDecodeError, OSError):
            pass   # 文件尚在写入
    return merged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-minutes", type=int, default=0)
    args = ap.parse_args()
    deadline = time.time() + args.wait_minutes * 60

    merged = load_merged()
    while args.wait_minutes > 0 and time.time() < deadline:
        counts = {c: sum(1 for (cond, _) in merged if cond == c) for c in CONDS}
        if all(counts[c] >= 10 for c in CONDS):
            break
        print(f"等待跑批完成... 各条件记录数: {counts}", flush=True)
        time.sleep(60)
        merged = load_merged()

    records = list(merged.values())
    print(aggregate(records))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e1_results_n10.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print(f"\n合并 {len(records)} 条记录 -> {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
