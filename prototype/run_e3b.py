"""E3b：刷新经济学相图（P1-3 消融发现的代价模型修正，无 API）。

背景：L5 四臂消融（2026-10-28）证伪了"主动刷新更好"的直觉——never（被动修复）
全面优于 always/oracle（主动刷新）。诊断表明：主动刷新把"确定性小损失"替换成
"一次新的 LLM 重采样"，而重采样的计划更长、失败率更高。

本实验：把刷新决策参数化，画出 **刷新划算的相图**——
  决策变量：检测到计划过期后，主动刷新 vs 被动修复
  E[refresh]  = C_replan + P_fail(len_full) × L_fail
  E[reactive] = C_waste  + P_fail(len_red)  × L_fail   （C_replan 相消）
  刷新划算 ⟺ C_waste > L_fail × (P_full − P_red)

两轴：
  x = C_waste（浪费代价，归一化单位：任务失败 = 10，1 个浪费算子 ≈ 0.2）
  y = 规划器可靠性（logistic 中点 len0：越大越可靠）
固定：len_red=5, len_full=7（T1 反事实实测的计划长度形状），s=1.5。

预期相变结构：
  - 可靠规划器（len0 大）：P_full ≈ P_red ≈ 0 → 刷新不划算（浪费不值一提）
  - 极不可靠（len0 小）：两个计划都几乎必败 → 刷新同样不划算
  - **中间可靠性 + 高浪费代价** 才是刷新的收益区
并标注 L5 消融的实测工作点（T1：C_waste ≈ 0.9，len0 ≈ 9-10 → reactive 区，
与消融结果 never 优于 always/oracle 一致）。

运行：python run_e3b.py
"""

from __future__ import annotations

import json
import os
import sys

from subscription import refresh_economics

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
L_FAIL = 10.0
LEN_RED, LEN_FULL, S = 5.0, 7.0, 1.5
WASTE_OPS_TO_UNITS = 0.2     # 1 个浪费算子 ≈ 0.2 单位（任务失败 = 10 单位）

C_WASTE_SWEEP = [round(0.5 * i, 1) for i in range(0, 21)]        # 0..10 单位
LEN0_SWEEP = [12.0, 10.5, 9.0, 7.5, 6.0]                          # 可靠 -> 不可靠


def main() -> int:
    print("== E3b：刷新经济学相图 ==")
    print(f"固定: len_red={LEN_RED}, len_full={LEN_FULL}, s={S}, L_fail={L_FAIL}")
    print(f"坐标: 列 = C_waste（单位；括号内为等价浪费算子数），行 = 规划器可靠性\n")

    header = f"{'len0(可靠性)':>14}" + "".join(f"{c:>7}" for c in C_WASTE_SWEEP)
    print(header + "   R=被动修复  F=主动刷新")
    print("-" * len(header))

    grid = []
    for len0 in LEN0_SWEEP:
        eco = refresh_economics(LEN_FULL, LEN_RED, L_FAIL, len0, S)
        cells = ["F" if c > eco["c_waste_breakeven"] else "R" for c in C_WASTE_SWEEP]
        grid.append({"len0": len0,
                     "p_fail_red": round(eco["p_fail_red"], 3),
                     "p_fail_full": round(eco["p_fail_full"], 3),
                     "c_waste_breakeven": round(eco["c_waste_breakeven"], 3),
                     "policies": cells})
        label = f"{len0:>6.1f} ({'可靠' if len0 >= 10.5 else '中' if len0 >= 7.5 else '不可靠'})"
        print(f"{label:>14}" + "".join(f"{p:>7}" for p in cells))

    print("\n== 各规划器可靠性的盈亏平衡 C_waste ==")
    print(f"{'len0':>6}{'P_fail(5)':>11}{'P_fail(7)':>11}{'盈亏平衡C_waste':>18}"
          f"{'等价浪费算子':>14}")
    for g in grid:
        ops_eq = g["c_waste_breakeven"] / WASTE_OPS_TO_UNITS
        print(f"{g['len0']:>6.1f}{g['p_fail_red']:>11.3f}{g['p_fail_full']:>11.3f}"
              f"{g['c_waste_breakeven']:>18.3f}{ops_eq:>14.1f}")

    # 实测工作点标注（L5 消融 never 臂：single 组浪费算子均值 4.5）
    measured_ops = 4.5
    measured_cw = measured_ops * WASTE_OPS_TO_UNITS
    measured_len0 = 9.5          # 消融中被动恢复计划（len 7-9）近乎全对的粗标定
    eco_m = refresh_economics(LEN_FULL, LEN_RED, L_FAIL, measured_len0, S)
    in_reactive = measured_cw <= eco_m["c_waste_breakeven"]
    print(f"\n== 实测工作点 ==")
    print(f"  T1 消融: C_waste ≈ {measured_cw:.2f}（{measured_ops} 个浪费算子），"
          f"len0 ≈ {measured_len0}（被动恢复计划近乎全对）")
    print(f"  该点盈亏平衡 C_waste = {eco_m['c_waste_breakeven']:.2f} -> "
          f"{'reactive 区（与消融一致：never 优于 always/oracle）' if in_reactive else 'refresh 区（与消融矛盾，需复查）'}")
    print(f"  推论：同一判据下，当浪费代价升高（如不可逆操作）或规划器更可靠时，"
          f"工作点移入 refresh 区——刷新策略应是 规划器可靠性(len0) 与 C_waste 的函数，非常量。")

    results = {"params": {"L_fail": L_FAIL, "len_red": LEN_RED,
                          "len_full": LEN_FULL, "s": S,
                          "waste_ops_to_units": WASTE_OPS_TO_UNITS},
               "c_waste_sweep": C_WASTE_SWEEP, "grid": grid,
               "measured_point": {"c_waste": measured_cw, "waste_ops": measured_ops,
                                  "len0": measured_len0,
                                  "region": "reactive" if in_reactive else "refresh"}}

    out = os.path.join(BASE, "e3b_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n结果已存 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
