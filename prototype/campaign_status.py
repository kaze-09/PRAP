"""战役进度查看：python campaign_status.py"""
import glob
import json
import os
import time

files = sorted(glob.glob("e1_T?_C?_n*.json"), key=os.path.getmtime)
total, target = 0, 0
plan = {("T1", c): 30 for c in ("C1", "C2", "C3", "C4", "C5")}
plan.update({(t, c): 10 for t in ("T2", "T3", "T4") for c in ("C2", "C3", "C4")})

for f in files:
    try:
        recs = json.load(open(f, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print(f"{f}: 写入中")
        continue
    n = len(recs)
    total += n
    age = time.time() - os.path.getmtime(f)
    succ = sum(r["success"] for r in recs)
    key = (recs[0]["task"], recs[0]["cond"])
    tgt = plan.get(key, n)
    target += tgt
    status = "DONE" if n >= tgt else ("RUNNING" if age < 300 else "STALLED?")
    print(f"{f}: {n}/{tgt} (成功 {succ}), 更新于 {age:.0f}s 前 [{status}]")

print(f"\n总计 {total}/{target}")
