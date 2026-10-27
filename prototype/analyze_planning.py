"""规划可靠性分析：健康 agent 的"单次任务规划成功率"，分模型统计。

数据源（离线，无 API 调用）：
  e1_results_all.json —— merge_e1.py 合并的全部 240 局
  （T1×C1..C5×30 + T2/T3/T4×C2..C4×10；C4 均为 v0.2 干净归因版本）

归因规则（保守：健康角色统一记试验整体成功；理由见下）：
  C1  两个健康 agent 各送各的物品，joint 成功 -> 双方各记一次成功（失败保守记双方失败）
  C2  A 为首次提交者（无重规划）；B 被 DENY 后以新观察重规划 -> 双方各记一次，
      成功 = 试验整体成功
  C3  A 挂死（排除），B 接管 -> 记 B，成功 = 试验整体成功
  C4  B 被注入幻觉（v0.2 中被隔离、零提交）-> 记 A（健康方），成功 = 试验整体成功
  C5  无协议，两个 agent 盲执行，失败无法归因到单一模型 -> 不计入分模型

  注：记录中的 completed_at 在租约执行完即置位（即使算子失败），不能作为
  "该 agent 送达" 的证据；单方交付无法从现有记录中分离，故统一采用保守的
  试验级归因。该归因对两个模型完全对称，不影响模型间比较。

产出：分模型成功率表 + Wilson 95% CI + 任务族分解 + 失败明细 + Fisher 精确检验。
"""

from __future__ import annotations

import json
import math
import os


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - spread) / denom, (centre + spread) / denom)


def main() -> int:
    base = os.path.dirname(os.path.abspath(__file__))
    rs = json.load(open(os.path.join(base, "e1_results_all.json"), encoding="utf-8"))

    tasks: list[dict] = []   # {model, role, cond, task, success}

    for r in rs:
        task, cond = r.get("task", "T1"), r["cond"]
        m = r["models"]
        if cond == "C1":
            # joint：两个物品都送达才成功；失败保守记双方失败
            for aid in ("A", "B"):
                tasks.append({"model": m[aid], "role": aid, "cond": "C1",
                              "task": task, "success": r["success"]})
        elif cond == "C2":
            tasks.append({"model": m["A"], "role": "A(first)", "cond": "C2",
                          "task": task, "success": r["success"]})
            tasks.append({"model": m["B"], "role": "B(replan)", "cond": "C2",
                          "task": task, "success": r["success"]})
        elif cond == "C3":
            tasks.append({"model": m["B"], "role": "B(takeover)", "cond": "C3",
                          "task": task, "success": r["success"]})
        elif cond == "C4":
            tasks.append({"model": m["A"], "role": "A(healthy)", "cond": "C4v02",
                          "task": task, "success": r["success"]})
        elif cond == "C5":
            continue   # 无协议，失败不可归因到单一模型

    # ---- 汇总：分模型总表 ----
    models = sorted({t["model"] for t in tasks})
    print("== 健康 agent 单任务规划成功率（分模型，全任务族合并）==\n")
    print(f"{'模型':<16}{'成功/任务':>10}{'成功率':>8}{'Wilson 95% CI':>22}")
    summary = {}
    for m in models:
        ts = [t for t in tasks if t["model"] == m]
        k, n = sum(t["success"] for t in ts), len(ts)
        lo, hi = wilson(k, n)
        summary[m] = (k, n)
        print(f"{m:<16}{f'{k}/{n}':>10}{f'{k / n:.0%}':>8}{f'[{lo:.0%}, {hi:.0%}]':>22}")

    # ---- 任务族 × 模型分解 ----
    print("\n== 任务族 × 模型分解 ==\n")
    task_fams = sorted({t["task"] for t in tasks})
    for tf in task_fams:
        cells = []
        for m in models:
            ts = [t for t in tasks if t["task"] == tf and t["model"] == m]
            if ts:
                k = sum(t["success"] for t in ts)
                cells.append(f"{m.split('-')[0]} {k}/{len(ts)}")
        print(f"  {tf}: " + "   ".join(cells))

    # ---- 条件 × 任务 × 模型明细 ----
    print("\n== 分条件 × 任务 × 模型明细 ==\n")
    conds = sorted({t["cond"] for t in tasks})
    for c in conds:
        for tf in task_fams:
            for m in models:
                ts = [t for t in tasks if t["cond"] == c and t["task"] == tf and t["model"] == m]
                if ts:
                    k = sum(t["success"] for t in ts)
                    print(f"  {c:<7}{tf:<4}{m:<16}{ts[0]['role']:<12}{k}/{len(ts)}")

    fails = [t for t in tasks if not t["success"]]
    print(f"\n失败明细（{len(fails)} 条）:")
    for t in fails:
        print(f"  {t['task']}/{t['cond']} {t['role']} model={t['model']}")

    # ---- Fisher 精确检验（单侧，2x2）——不依赖 scipy 的实现 ----
    (k1, n1), (k2, n2) = summary[models[0]], summary[models[1]]
    from math import comb
    a, b, cc, d = k1, n1 - k1, k2, n2 - k2
    row1, col1, total = a + b, a + cc, n1 + n2

    def hyper(p_x):   # P(X = p_x | 边际固定)
        return comb(col1, p_x) * comb(total - col1, row1 - p_x) / comb(total, row1)

    p_obs = hyper(a)
    lo, hi = max(0, row1 - (total - col1)), min(row1, col1)
    p_val = sum(hyper(x) for x in range(lo, hi + 1) if hyper(x) <= p_obs + 1e-12)
    print(f"\nFisher 单侧 p ≈ {p_val:.3f}（{models[0]} {k1}/{n1} vs {models[1]} {k2}/{n2}）")
    print("注：模型×条件为随机效应未建模的边际比较；T3 高失败率混合后，"
          "差异解释需结合任务族分解（见上表）。")

    out = os.path.join(base, "planning_reliability.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks, "summary": summary, "fisher_p": p_val},
                  f, ensure_ascii=False, indent=1)
    print(f"\n结果已存 {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
