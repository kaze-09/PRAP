"""离线 sanity 测试：多任务定义、7x7 网格、成功判定（无 API，秒级）。"""

from __future__ import annotations

import sys

from run_e1_full import TASKS, check_success, make_goal
from testbed import Sim, init_world, op_move


def main() -> int:
    # 1. T4：7x7 世界初始化与移动边界
    sim = Sim()
    spec = TASKS["T4"]
    init_world(sim.arbiter, spec["items"], spec["agents"],
               width=spec["W"], height=spec["H"])
    assert sim.arbiter.width == 7 and sim.arbiter.height == 7
    assert sim.arbiter.state["pos:B"].value == "(6,6)"
    lease = type("L", (), {"lease_id": "t"})()
    ok, detail = op_move(sim.arbiter, lease, {"agent": "A", "dx": 1, "dy": 0})
    assert ok and detail == "(1,0)", detail
    for _ in range(5):   # 走到右边缘 (6,0)
        ok, detail = op_move(sim.arbiter, lease, {"agent": "A", "dx": 1, "dy": 0})
        assert ok, detail
    ok, detail = op_move(sim.arbiter, lease, {"agent": "A", "dx": 1, "dy": 0})
    assert not ok and detail == "out_of_bounds", detail
    ok, detail = op_move(sim.arbiter, lease, {"agent": "A", "dx": 0, "dy": 1})
    assert ok and detail == "(6,1)", detail   # dy 多步在算子层允许（单位步约束在 SDK 清洗层）
    print("[PASS] T4 7x7 世界与移动边界")

    # 2. T1/C1 成功判定（双物品）
    sim2 = Sim()
    s1 = TASKS["T1"]
    items = dict(s1["items"]); items.update(s1["c1_extra"])
    init_world(sim2.arbiter, items, s1["agents"])
    ok, why = check_success(sim2, "C1", "T1")
    assert not ok and "未送达" in why, why          # 初始未送达 -> False
    for k in items:
        sim2.arbiter._write(k, f"world@{fmt_drop(s1)}", "test", None)
    ok, why = check_success(sim2, "C1", "T1")
    assert ok, why                                  # 全部送达 -> True
    sim2.arbiter._write("apple1", "A", "test", None)  # 被拿起 -> False
    ok, why = check_success(sim2, "C1", "T1")
    assert not ok and "未送达" in why, why
    print("[PASS] T1/C1 成功判定（双物品 + 持有不变量）")

    # 3. T3 链式任务目标文案
    g = make_goal(["apple1", "apple2"], "(0,4)", True)
    assert "apple1" in g and "apple2" in g and "每一件" in g, g
    print(f"[PASS] T3 链式目标文案: {g}")

    # 4. 各任务 spec 完整性
    for name, spec in TASKS.items():
        assert spec["W"] >= 5 and spec["H"] >= 5
        assert all(0 <= x < spec["W"] and 0 <= y < spec["H"]
                   for pos in list(spec["items"].values()) + list(spec["agents"].values())
                   for x, y in [pos]), name
        assert 0 <= spec["drop"][0] < spec["W"] and 0 <= spec["drop"][1] < spec["H"], name
    print("[PASS] 各任务坐标均在网格内")
    print("\n离线 sanity 全部通过。")
    return 0


def fmt_drop(spec) -> str:
    from testbed import fmt_pos
    return fmt_pos(spec["drop"])


if __name__ == "__main__":
    sys.exit(main())
