"""R2 终表：scale8/f30 五臂 n=30 + 预注册 Fisher 比较（复用 check_power.fisher）。"""
import math
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from check_power import fisher, wilson

ARMS = [
    ("PRAP", 24, 30, 0.0),
    ("promptfix", 22, 30, 7.7),
    ("orch", 19, 30, 3.8),
    ("noproto", 19, 30, 8.6),
    ("leaseonly", 15, 30, 14.5),
]

print("== scale8/f30 五臂（n=30，预注册主终点） ==")
for name, k, n, w in ARMS:
    lo, hi = wilson(k, n)
    print(f"  {name:<11}{k}/{n} = {k / n:.0%}  CI [{lo:.0%}, {hi:.0%}]  浪费 {w:.1f}")

print("\n== 预注册比较（Fisher 精确，双侧） ==")
print(f"  1. PRAP 24/30 vs leaseonly 15/30  p = {fisher(24, 6, 15, 15):.4f}   <- L4 增量")
print(f"  2. PRAP 24/30 vs orch 19/30       p = {fisher(24, 6, 19, 11):.4f}   <- 规模优势")
print(f"  3. orch 19/30 vs leaseonly 15/30  p = {fisher(19, 11, 15, 15):.4f}")

print("\n== 浪费算子分离（均值，n=30） ==")
print("  PRAP 0.0 | orch 3.8 | promptfix 7.7 | noproto 8.6 | leaseonly 14.5")

print("\n== leaseonly T1/C4（n=10，对照 PRAP 100%/0.0） ==")
print("  成功 10/10（与 PRAP 持平）但浪费 6.4/局 —— L4 在单物品场景的价值 = 浪费与延迟，非成功率")
