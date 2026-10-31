"""E6 样本量充分性检查：Wilson 95% CI + Fisher 精确检验。零依赖。"""
import math
import sys

sys.stdout.reconfigure(encoding="utf-8")
Z = 1.959963984540054


def wilson(k, n):
    p = k / n
    c = (p + Z * Z / (2 * n)) / (1 + Z * Z / n)
    hw = Z / (1 + Z * Z / n) * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))
    return c - hw, c + hw


def fisher(a, b, c, d):
    """2x2 Fisher 精确检验（双侧）。a/b=臂1成功/失败，c/d=臂2成功/失败。
    固定两臂样本量 n1=a+b, n2=c+d 与总成功数 s=a+c，枚举超几何分布。"""
    n1, n2 = a + b, c + d
    s = a + c
    N = n1 + n2
    def p_table(x):
        return (math.comb(n1, x) * math.comb(n2, s - x)) / math.comb(N, s)
    p0 = p_table(a)
    lo = max(0, s - n2)
    hi = min(n1, s)
    total = 0.0
    for x in range(lo, hi + 1):
        px = p_table(x)
        if px <= p0 * (1 + 1e-9):
            total += px
    return min(1.0, total)


print("== T2/C4 各臂 Wilson 95% CI（n=30 扩样后） ==")
cells = [
    ("orch", 27, 30), ("cnp", 18, 30), ("groupchat", 17, 30),
    ("coagent", 15, 30), ("PRAP(v0.2.3)", 16, 30),
]
for name, k, n in cells:
    lo, hi = wilson(k, n)
    print(f"  {name:<14} {k}/{n} = {k / n:.0%}  CI [{lo:.0%}, {hi:.0%}]")

print("\n== 关键对比 Fisher 精确检验（n=30） ==")
pairs = [
    ("orch 27/30 vs PRAP 16/30", 27, 30, 16, 30),
    ("orch 27/30 vs cnp 18/30", 27, 30, 18, 30),
    ("orch 27/30 vs coagent 15/30", 27, 30, 15, 30),
    ("orch 27/30 vs groupchat 17/30", 27, 30, 17, 30),
    ("cnp 18/30 vs PRAP 16/30", 18, 30, 16, 30),
    ("groupchat 17/30 vs PRAP 16/30", 17, 30, 16, 30),
]
for name, k1, n1, k2, n2 in pairs:
    print(f"  {name:<34} p = {fisher(k1, n1 - k1, k2, n2 - k2):.4f}")

print("\n== T1/C4 群聊崩塌（n=10，未扩样） ==")
print(f"  groupchat 2/10 vs orch 10/10  p = {fisher(2, 8, 10, 0):.4f}")

print("\n== P2 规模矩阵：orch vs PRAP（各格 n=10） ==")
cells2 = [
    ("scale2/f30: orch 10/10 vs PRAP 8/10", 10, 10, 8, 10),
    ("scale4/f10: orch 8/10 vs PRAP 9/10", 8, 10, 9, 10),
    ("scale4/f30: orch 7/10 vs PRAP 10/10", 7, 10, 10, 10),
    ("scale8/f0:  orch 9/10 vs PRAP 10/10", 9, 10, 10, 10),
    ("scale8/f10: orch 6/10 vs PRAP 10/10", 6, 10, 10, 10),
    ("scale8/f30: orch 6/10 vs PRAP 8/10", 6, 10, 8, 10),
]
for name, k1, n1, k2, n2 in cells2:
    print(f"  {name:<38} p = {fisher(k1, n1 - k1, k2, n2 - k2):.4f}")

print("\n== 合并故障格（f10+f30，各 n=20/臂） ==")
print(f"  orch 27/40 vs PRAP 37/40  p = {fisher(27, 40 - 27, 37, 40 - 37):.5f}")
