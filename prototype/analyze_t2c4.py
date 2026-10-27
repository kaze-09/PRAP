"""T2/C4 坐标滑差逐算子分析（离线，读 e1_T2_C4_n10_debug.json）。

机理问题：C4 中健康 agent A（被 DENY 后重规划）为何把物品放错位置？
T2 规格：apple1 出生 (1,3)，投放点 (4,0)；B 被注入"apple1 在 (2,3)"。

方法：从 A 的最终计划 ops 重建意图路径（从 A 出生点逐步走），得到
  grab 位点与 place 位点，与真值对照；失败分类：
  PLACE_OFF   place 位点 = 投放点相邻格（算术滑差）
  NO_GRAB     计划中无 grab 或从未执行 grab（物品留在出生点）
  WRONG_PLACE place 位点为其他
  EXEC_ABORT  计划正确但执行中断（租约/TTL 等协议原因）
产出：逐 trial 明细 + 失败模式统计 + 对照组（成功局）place 位点分布。
"""

from __future__ import annotations

import json
import os
from collections import Counter

SPAWN = (1, 3)      # T2 apple1 真实出生点
DROP = (4, 0)       # T2 投放点
A_START = (0, 4)    # T2 agent A 出生点


def simulate_ops(ops: list, start: tuple) -> dict:
    """从 start 起走一遍计划 ops，返回 grab 位点与 place 位点（忽略执行可行性）。"""
    x, y = start
    grab_at = place_at = None
    for op in ops:
        name, args = op[0], op[1]
        if name == "move":
            x += int(args.get("dx", 0))
            y += int(args.get("dy", 0))
        elif name == "grab" and grab_at is None:
            grab_at = (x, y)
        elif name == "place" and place_at is None:
            place_at = (x, y)
    return {"grab_at": grab_at, "place_at": place_at,
            "final": (x, y), "n_ops": len(ops)}


def classify(sim_r: dict, success: bool) -> str:
    if success:
        return "OK"
    pa = sim_r["place_at"]
    ga = sim_r["grab_at"]
    if pa == DROP:
        return "EXEC_ABORT"          # 计划正确但没执行完
    if pa is not None and pa != DROP and max(abs(pa[0] - DROP[0]), abs(pa[1] - DROP[1])) == 1:
        return "PLACE_OFF"           # 投放点相邻格：坐标算术滑差
    if ga != SPAWN:
        return "WRONG_GRAB"          # 去了错误位置抓取
    if pa is None:
        return "NO_PLACE"            # 计划没走到 place
    return "WRONG_PLACE"


def main() -> int:
    base = os.path.dirname(os.path.abspath(__file__))
    rs = json.load(open(os.path.join(base, "e1_T2_C4_n10_debug.json"), encoding="utf-8"))

    print("== T2/C4 逐算子分析（A = 健康 agent）==\n")
    rows = []
    for r in rs:
        # A 的最终计划（最后一次规划）
        plans_a = r.get("plans", {}).get("A", [])
        ops_a = plans_a[-1]["ops"] if plans_a else []
        sim_a = simulate_ops(ops_a, A_START)
        # B 的首个计划（幻觉目标）
        plans_b = r.get("plans", {}).get("B", [])
        sim_b = simulate_ops(plans_b[0]["ops"], (2, 0)) if plans_b else {}
        label = classify(sim_a, r["success"])
        rows.append({"trial": r["trial"], "model_A": r["models"]["A"],
                     "success": r["success"], "mode": label,
                     "grab_at": sim_a["grab_at"], "place_at": sim_a["place_at"],
                     "place_intended": plans_a[-1].get("thought", "")[-30:] if plans_a else "",
                     "B_grab_at": sim_b.get("grab_at")})

        flag = "成功" if r["success"] else label
        print(f"t{r['trial']} A={r['models']['A']:<13} [{flag}]"
              f" grab@{sim_a['grab_at']} place@{sim_a['place_at']}"
              f" (真值 grab@{SPAWN} place@{DROP})"
              + (f"  B(幻觉) grab@{sim_b.get('grab_at')}" if sim_b else ""))

    n_fail = sum(1 for x in rows if not x["success"])
    modes = Counter(x["mode"] for x in rows if not x["success"])
    print(f"\n失败 {n_fail}/{len(rows)}，模式分布：{dict(modes)}")

    off = [x for x in rows if x["mode"] == "PLACE_OFF"]
    if off:
        print("\n-- PLACE_OFF 明细（thought vs ops 的偏差）--")
        for x in off:
            print(f"  t{x['trial']} place@{x['place_at']}  thought 末尾: …{x['place_intended']}")

    nob = [x for x in rows if x["mode"] in ("NO_PLACE", "WRONG_GRAB")]
    if nob:
        print("\n-- 未抓取/抓错位明细 --")
        for x in nob:
            print(f"  t{x['trial']} A={x['model_A']} grab@{x['grab_at']} place@{x['place_at']}")

    ok_places = Counter(str(x["place_at"]) for x in rows if x["success"])
    print(f"\n成功局 place 位点分布：{dict(ok_places)}（应全为 {DROP}）")

    out = os.path.join(base, "t2c4_analysis.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "modes": dict(modes)}, f, ensure_ascii=False, indent=1)
    print(f"\n结果已存 {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
