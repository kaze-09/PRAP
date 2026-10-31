"""orch 臂 vs P2 三方案臂全矩阵对比。用法：python orch_vs_p2.py"""
import glob
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

# ---- orch（新数据） ----
orch = {}
for f in glob.glob("scale*_orch.json"):
    for r in json.load(open(f, encoding="utf-8")):
        key = (r["scale"], int(r["fault"] * 100))
        orch.setdefault(key, []).append(r)

# ---- P2 三方案（旧数据） ----
p2 = {}
for f in glob.glob("scale?_f[0-9]*_*.json"):
    if "_orch" in f:
        continue
    for r in json.load(open(f, encoding="utf-8")):
        key = (r["scale"], int(r["fault"] * 100))
        p2.setdefault((key, r["arm"]), []).append(r)

print("成功率矩阵（行=规模/故障率，orch 为 n=10 新跑，其余 P2 原始）")
print(f"{'规模/故障':<10}{'orch':>8}{'PRAP':>8}{'无协议':>8}{'提示词':>8}   "
      f"{'orch浪费':>8}{'PRAP浪费':>8}{'无协议浪费':>9}")
for s in (2, 4, 8):
    for ft in (0, 10, 30):
        row = f"{s}/{ft:<8}"
        o = orch.get((s, ft), [])
        o_sr = sum(r["success"] for r in o) / len(o) if o else float("nan")
        o_w = sum(r["wasted_ops"] for r in o) / len(o) if o else float("nan")
        row += f"{o_sr:>7.0%}  "
        for arm in ("prap", "noproto", "promptfix"):
            rs = p2.get(((s, ft), arm), [])
            sr = sum(r["success"] for r in rs) / len(rs) if rs else float("nan")
            w = (sum(r["wasted_ops"] for r in rs) / len(rs)) if rs else float("nan")
            row += f"{sr:>7.0%}  "
        row += f"{o_w:>8.1f}"
        for arm in ("prap", "noproto"):
            rs = p2.get(((s, ft), arm), [])
            w = (sum(r["wasted_ops"] for r in rs) / len(rs)) if rs else float("nan")
            row += f"{w:>9.1f}"
        print(row)
