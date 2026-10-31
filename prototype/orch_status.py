"""P2 orch 臂进度监控。用法：python orch_status.py"""
import glob
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

for f in sorted(glob.glob("scale*_orch.json")):
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception as e:
        print(f"{f}: 解析失败（可能正在写入）")
        continue
    if not d:
        continue
    sr = sum(r["success"] for r in d)
    waste = sum(r["wasted_ops"] for r in d) / len(d)
    rea = sum(r["reassignments"] for r in d) / len(d)
    print(f"{f:<28}{len(d):>3}/10  成功 {sr:>2}  浪费 {waste:>4.1f}  重分配 {rea:.1f}")
