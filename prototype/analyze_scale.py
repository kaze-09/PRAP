"""P2 规模矩阵聚合 + 双幻觉假设验证（离线）。"""
import glob
import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

# ---- 聚合主表 ----
cells = {}
for f in sorted(glob.glob("scale*_f*_*.json")):
    rs = json.load(open(f, encoding="utf-8"))
    if not rs:
        continue
    key = rs[0]["cell"]
    cells[key] = {
        "n": len(rs),
        "success": round(sum(r["success"] for r in rs) / len(rs), 2),
        "wasted": round(sum(r["wasted_ops"] for r in rs) / len(rs), 1),
        "quarantined": sum(r["n_quarantined"] for r in rs),
        "tokens": round(sum(r["tokens"] for r in rs) / len(rs)),
        "ticks": round(sum(r["completion_tick"] for r in rs) / len(rs), 1),
        "runs": rs,
    }

print("== 主表：成功率（浪费算子 / 隔离数 / tokens）==")
print(f"{'cell':<26}{'成功':>6}{'浪费':>6}{'隔离':>5}{'tokens':>8}")
for key in sorted(cells):
    c = cells[key]
    print(f"{key:<26}{c['success']:>6.0%}{c['wasted']:>6.1f}"
          f"{c['quarantined']:>5}{c['tokens']:>8}")

# ---- 双幻觉假设验证：scale2/f30 prap 的失败局 ----
print("\n== 验证：scale2_f30_prap 失败局是否为'双幻觉'（全员隔离）==")
fault, arm = 0.30, "prap"
both_h = []
for t in range(10):
    rng = random.Random(f"2-{fault}-{arm}-{t}")
    h = [rng.random() < fault for _ in range(2)]
    both_h.append(all(h))
runs = cells["scale2_f30_prap"]["runs"]
for r, bh in zip(runs, both_h):
    if not r["success"]:
        print(f"  失败局 t{r['trial']}: 双幻觉={bh} 隔离数={r['n_quarantined']} "
              f"送达={r['delivered']}/{r['n_items']}")
n_both = sum(both_h)
n_fail = sum(1 for r in runs if not r["success"])
match = sum(1 for r, bh in zip(runs, both_h) if bh == (not r["success"]))
print(f"  双幻觉格数={n_both}, 失败局数={n_fail}, 逐局吻合={match}/10")

out = {k: {kk: vv for kk, vv in v.items()} for k, v in cells.items()}
json.dump(out, open("scale_results.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\n聚合已存 scale_results.json")
