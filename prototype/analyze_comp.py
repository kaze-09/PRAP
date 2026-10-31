"""E6 竞品对比聚合 + 失败归因。用法：python analyze_comp.py"""
import glob
import json
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

cells = {}
for f in sorted(glob.glob("comp_*_T*.json")):
    d = json.load(open(f, encoding="utf-8"))
    r0 = d[0]
    key = f"{r0['baseline']}/{r0['task']}/{r0['cond']}"
    cells[key] = d

print(f"{'臂/任务/条件':<22}{'n':>3}{'成功率':>7}{'浪费算子':>9}{'tokens':>8}{'调用':>6}")
for k in sorted(cells):
    d = cells[k]
    n = len(d)
    sr = sum(r["success"] for r in d) / n
    waste = sum(v["failed"] for r in d for v in r["agent_ops"].values()) / n
    toks = sum(r["tokens"] for r in d) / n
    calls = sum(r["llm_calls"] for r in d) / n
    print(f"{k:<22}{n:>3}{sr:>6.0%}{waste:>9.1f}{toks:>8.0f}{calls:>6.1f}")

print("\n== 失败归因（逐局） ==")
for k in sorted(cells):
    fails = [r for r in cells[k] if not r["success"]]
    if not fails:
        continue
    reasons = Counter()
    for r in fails:
        fr = r["fail_reason"]
        # 归一化失败模式
        if "未送达" in fr:
            pos = fr.split("world@")[-1].rstrip("）)") if "world@" in fr else "?"
            reasons[f"未送达@{pos}"] += 1
        else:
            reasons[fr[:40]] += 1
    print(f"  {k}: {dict(reasons)}")

print("\n== 机制计数（均值/局） ==")
for k in sorted(cells):
    d = cells[k]
    n = len(d)
    agg = {}
    for r in d:
        for aid, ex in r["extra"].items():
            for kk, vv in ex.items():
                agg[kk] = agg.get(kk, 0) + vv
    line = {kk: round(vv / n, 1) for kk, vv in agg.items()}
    if line:
        print(f"  {k}: {line}")
