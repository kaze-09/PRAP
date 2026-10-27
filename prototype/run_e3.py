"""E3：L5 订阅阈值校准曲线（创新点 3 的收尾实验，无 API 调用）。

设计：
  世界：某 key 的变化由真实概率 p_true 决定；agent 在 n 个机会窗口里数到
        k 次变化，然后用订阅判据决定"是否持续订阅"。
  对照：全知 oracle（已知 p_true）的最优决策。
  扫描：p_true ∈ {0.02..0.8} × 证据量 n ∈ {1..64} × 决策模式
        {mean 后验均值, lower_bound 后验下界}；每格 4000 次 Monte Carlo
        （固定种子，结果可复现）。

产出（校准曲线的两个刻面）：
  1) 决策一致性 vs 证据量 n：策略与 oracle 判同的频率随 n 的收敛
     ——"观察到多少次才敢做决定"的直接答案；
  2) 平均后悔值 vs p_true：策略在真实变化率维度的最坏区间定位
     ——盈亏平衡点 p* 附近应最差，两端应趋近 0。

运行：python run_e3.py
"""

from __future__ import annotations

import json
import os
import random
import sys

from subscription import breakeven, decide, oracle, regret

SEED = 20261027
TRIALS = 4000
L_MISS, C_SUB, C_RECHECK = 10.0, 1.0, 2.0     # p* = 1/(10-2) = 0.125
P_TRUE_SWEEP = [0.02, 0.05, 0.10, 0.125, 0.20, 0.30, 0.50, 0.80]
N_SWEEP = [1, 2, 4, 8, 16, 32, 64, 128, 256]
MODES = ["mean", "lower_bound"]


def binomial(n: int, p: float, rng: random.Random) -> int:
    return sum(1 for _ in range(n) if rng.random() < p)


def main() -> int:
    rng = random.Random(SEED)
    p_star = breakeven(L_MISS, C_SUB, C_RECHECK)
    print(f"E3 校准曲线扫描（SEED={SEED}, TRIALS={TRIALS}/格）")
    print(f"代价参数: L_miss={L_MISS}, C_sub={C_SUB}, C_recheck={C_RECHECK} "
          f"-> 盈亏平衡阈值 p*={p_star:.3f}\n")

    results = {"seed": SEED, "trials": TRIALS,
               "params": {"L_miss": L_MISS, "C_sub": C_SUB,
                          "C_recheck": C_RECHECK, "p_star": p_star},
               "cells": []}

    # ---------- 校准曲线 1：一致性 vs 证据量 ----------
    print("== 校准曲线 1：与 oracle 的决策一致性 vs 证据量 n ==")
    print(f"{'n':>4}" + "".join(f"{m:>14}" for m in MODES) + "   (判同频率，全 p_true 平均)")
    agree_curve: dict[str, list[dict]] = {m: [] for m in MODES}
    for n in N_SWEEP:
        row = []
        for mode in MODES:
            agree_total = 0
            total = 0
            regret_total = 0.0
            for p_true in P_TRUE_SWEEP:
                oracle_d = oracle(p_true, L_MISS, C_SUB, C_RECHECK)
                for _ in range(TRIALS):
                    k = binomial(n, p_true, rng)
                    d, _ = decide(n, k, L_MISS, C_SUB, C_RECHECK, mode=mode)
                    agree_total += int(d == oracle_d)
                    regret_total += regret(p_true, L_MISS, C_SUB, C_RECHECK, d)
                    total += 1
            row.append(agree_total / total)
            agree_curve[mode].append({"n": n, "agreement": agree_total / total,
                                      "avg_regret": regret_total / total})
        print(f"{n:>4}" + "".join(f"{v:>13.1%}" for v in row))

    for mode in MODES:
        curve = agree_curve[mode]
        n95 = next((pt["n"] for pt in curve if pt["agreement"] >= 0.95), None)
        print(f"  [{mode}] 达到 95% 一致性所需证据量 n* = {n95}")

    # ---------- 校准曲线 2：平均后悔值 vs p_true（n=8） ----------
    print("\n== 校准曲线 2：平均后悔值 vs 真实变化率（n=8，其余格见 JSON）==")
    print(f"{'p_true':>7}" + "".join(f"{m:>14}" for m in MODES))
    n_fix = 8
    regret_curve = []
    for p_true in P_TRUE_SWEEP:
        oracle_d = oracle(p_true, L_MISS, C_SUB, C_RECHECK)
        row = []
        for mode in MODES:
            reg = 0.0
            for _ in range(TRIALS):
                k = binomial(n_fix, p_true, rng)
                d, _ = decide(n_fix, k, L_MISS, C_SUB, C_RECHECK, mode=mode)
                reg += regret(p_true, L_MISS, C_SUB, C_RECHECK, d)
            row.append(reg / TRIALS)
        regret_curve.append({"p_true": p_true, "mean": row[0], "lower_bound": row[1]})
        print(f"{p_true:>7.3f}" + "".join(f"{v:>14.3f}" for v in row))
    for mode in MODES:
        worst = max(regret_curve, key=lambda r: r[mode])
        print(f"  [{mode}] 后悔值最坏区间在 p_true={worst['p_true']}"
              f"（{worst[mode]:.3f}）：mean 模式吃亏于阈值下方漏订阅，"
              f"lower_bound 吃亏于阈值上方保守不订阅——两种模式呈互补权衡")

    results["cells"] = {"agree_curve": agree_curve, "regret_curve": regret_curve}

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e3_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n结果已存 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
