"""CCF 补实验：纠正 / 隔离 / 重分配（机制回归 + 真实 LLM pilot）。

冻结口径见仓库根目录 CCF_FOLLOWUP_PROTOCOL.md。
默认 recovery_mode=quarantine，本文件显式切换，不改 E1 行为。

脚本组回答“机制是否按故障类型分开”；LLM 组回答“真实模型下排序是否还在”。
脚本组的成功/失败是构造出来的对照，不能当作经验胜负。

用法：
  python run_recovery.py --scripted
  python run_recovery.py --llm --trials 6
  python run_recovery.py --llm --trials 1 --only sole sensor correct_pin
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from llm_agent import LLMAgent, load_clients
from prap_core import digest
from testbed import ScriptedAgent, Sim, fmt_pos, init_world, parse_pos

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "ccf_followup")

ITEM = "item:apple"
ITEM_POS = (1, 3)
LIE = "world@(2,3)"
DROP = (4, 0)
GOAL = f"world@{fmt_pos(DROP)}"
B_POS = (0, 4)
A_POS = (2, 0)
H_POS = (4, 4)
HORIZON = 80

# 主终点：incorrect_commits（合法但落到非目标的 place）、wasted_ops。
# success 是次要终点。下面的 expected_success 只检查脚本机制是否按设计分开。
EXPECTED = {
    # sensor：传感器每次都撒谎。只有 pin 或别人接手能在 sole 下成功。
    ("sole", "sensor", "leaseonly"): False,
    ("sole", "sensor", "detect"): False,
    ("sole", "sensor", "correct_reobserve"): False,
    ("sole", "sensor", "correct_pin"): True,
    ("sole", "sensor", "quarantine"): False,
    ("sole", "sensor", "quarantine_reassign"): True,
    ("sole", "sensor", "adaptive"): True,
    ("sole", "sensor", "postcondition"): False,
    ("paired", "sensor", "leaseonly"): True,
    ("paired", "sensor", "detect"): True,
    ("paired", "sensor", "correct_reobserve"): True,
    ("paired", "sensor", "correct_pin"): True,
    ("paired", "sensor", "quarantine"): True,
    ("paired", "sensor", "quarantine_reassign"): True,
    ("paired", "sensor", "adaptive"): True,
    ("paired", "sensor", "postcondition"): True,
    # report：只有第一次信念是假的，重新观察即恢复。lease/detect 仍会先执行错计划。
    ("sole", "report", "leaseonly"): False,
    ("sole", "report", "detect"): False,
    ("sole", "report", "correct_reobserve"): True,
    ("sole", "report", "correct_pin"): True,
    ("sole", "report", "quarantine"): True,
    ("sole", "report", "quarantine_reassign"): True,
    ("sole", "report", "adaptive"): True,
    ("sole", "report", "postcondition"): True,
    ("paired", "report", "leaseonly"): True,
    ("paired", "report", "detect"): True,
    ("paired", "report", "correct_reobserve"): True,
    ("paired", "report", "correct_pin"): True,
    ("paired", "report", "quarantine"): True,
    ("paired", "report", "quarantine_reassign"): True,
    ("paired", "report", "adaptive"): True,
    ("paired", "report", "postcondition"): True,
    # plan_slip：信念正确，L4 全部看不见。只有提交后检查落点才能接手。
    ("sole", "slip", "leaseonly"): False,
    ("sole", "slip", "detect"): False,
    ("sole", "slip", "correct_reobserve"): False,
    ("sole", "slip", "correct_pin"): False,
    ("sole", "slip", "quarantine"): False,
    ("sole", "slip", "quarantine_reassign"): False,
    ("sole", "slip", "adaptive"): False,
    ("sole", "slip", "postcondition"): True,
    ("paired", "slip", "leaseonly"): False,
    ("paired", "slip", "detect"): False,
    ("paired", "slip", "correct_reobserve"): False,
    ("paired", "slip", "correct_pin"): False,
    ("paired", "slip", "quarantine"): False,
    ("paired", "slip", "quarantine_reassign"): False,
    ("paired", "slip", "adaptive"): False,
    ("paired", "slip", "postcondition"): True,
}

ARMS = ("leaseonly", "detect", "correct_reobserve", "correct_pin",
        "quarantine", "quarantine_reassign", "adaptive", "postcondition")
FAULTS = ("sensor", "report", "slip")
SCENES = ("sole", "paired")

# LLM pilot：能分开纠正、隔离、重分配的代表格。trial 同时是配对种子。
LLM_CELLS = (
    ("sole", "sensor", "leaseonly"),
    ("sole", "sensor", "correct_reobserve"),
    ("sole", "sensor", "correct_pin"),
    ("sole", "sensor", "quarantine"),
    ("sole", "sensor", "quarantine_reassign"),
    ("sole", "report", "correct_reobserve"),
    ("sole", "report", "quarantine"),
    ("paired", "sensor", "leaseonly"),
    ("paired", "sensor", "quarantine"),
    ("sole", "natural", "quarantine"),
)


def path(agent: str, start, goal) -> list:
    ops = []
    x, y = start
    while x != goal[0]:
        step = 1 if goal[0] > x else -1
        ops.append(("move", {"agent": agent, "dx": step, "dy": 0}))
        x += step
    while y != goal[1]:
        step = 1 if goal[1] > y else -1
        ops.append(("move", {"agent": agent, "dx": 0, "dy": step}))
        y += step
    return ops


def deliver_ops(agent: str, start, item_at, drop, key: str) -> list:
    ops = path(agent, start, item_at)
    ops.append(("grab", {"agent": agent, "key": key}))
    ops += path(agent, item_at, drop)
    ops.append(("place", {"agent": agent, "key": key}))
    return ops


def slip_ops(agent: str) -> list:
    """抓取正确，投放停在 (4,1)。信念与权威一致，L4 判 ok。"""
    ops = path(agent, B_POS, ITEM_POS)
    ops.append(("grab", {"agent": agent, "key": ITEM}))
    ops += path(agent, ITEM_POS, (4, 1))
    ops.append(("place", {"agent": agent, "key": ITEM}))
    return ops


class Helper(ScriptedAgent):
    def __init__(self, sim: Sim):
        super().__init__(sim, "H", (ITEM,), [], ttl=30, think=1, priority=8)
        self.armed = False

    def arm(self) -> None:
        if self.armed:
            return
        val = self.sim.arbiter.state[ITEM].value
        if not str(val).startswith("world@") or val == GOAL:
            return
        loc = parse_pos(str(val)[6:])
        me = parse_pos(self.sim.arbiter.state["pos:H"].value)
        self.ops = deliver_ops("H", me, loc, DROP, ITEM)
        self.armed = True
        self.start(self.sim.now + 1)


class FaultyScript(ScriptedAgent):
    """故障模型在 agent 侧，恢复动作在仲裁器侧。二者不要混在一个开关里。"""

    def __init__(self, sim: Sim, fault: str, helper: Helper, arm: str):
        super().__init__(sim, "B", (ITEM,), [], ttl=20, think=2, priority=9)
        self.fault = fault
        self.helper = helper
        self.arm = arm
        self.uses_truth = False
        self.submits = 0
        self.true_ops = deliver_ops("B", B_POS, ITEM_POS, DROP, ITEM)
        self.wrong_ops = path("B", B_POS, (2, 3)) + [("grab", {"agent": "B", "key": ITEM})]
        self.slip = slip_ops("B")

    def start(self, t: int) -> None:
        self.sim.schedule(t, self._submit)

    def _submit(self) -> None:
        if self.submits >= 3:
            return
        self.submits += 1
        entry = self.sim.arbiter.state[ITEM]
        if self.fault == "slip":
            claimed, self.ops = entry.value, self.slip
        elif self.uses_truth or (self.fault == "report" and self.submits > 1):
            claimed, self.ops = entry.value, self.true_ops
        else:
            claimed, self.ops = LIE, self.wrong_ops
        self.belief = [(ITEM, entry.version, digest(claimed))]
        self.sim.arbiter.submit_intent(
            self.id, self.keys, ops=self.ops, ttl=self.ttl,
            priority=self.priority, belief=self.belief)

    def on_msg(self, msg) -> None:
        super().on_msg(msg)
        if msg.type == "CORRECT" and self.submits < 3:
            style = (msg.payload or {}).get("style", "")
            if style == "correct_pin" or self.fault == "report":
                self.uses_truth = True
            self.sim.schedule(self.sim.now + 1, self._submit)
        elif msg.type == "DENY_WITH_STATE":
            ev = (msg.verdict or {}).get("evidence") or []
            blocked = any(isinstance(e, (list, tuple)) and e and e[0] == "suspect_blocked"
                          for e in ev)
            if blocked and self.arm == "quarantine_reassign":
                self.helper.arm()
            if blocked and self.fault == "report" and self.submits < 3:
                self.sim.schedule(self.sim.now + 1, self._submit)

    def _commit(self) -> None:
        super()._commit()
        if self.arm != "postcondition":
            return
        val = self.sim.arbiter.state[ITEM].value
        if str(val).startswith("world@") and val != GOAL:
            self.helper.arm()


def apply_arm(sim: Sim, arm: str) -> None:
    if arm == "leaseonly":
        sim.arbiter.l4_enabled = False
        return
    sim.arbiter.l4_enabled = True
    sim.arbiter.recovery_mode = {
        "detect": "detect",
        "correct_reobserve": "correct_reobserve",
        "correct_pin": "correct_pin",
        "adaptive": "adaptive",
    }.get(arm, "quarantine")


def build_scripted(scene: str, fault: str, arm: str):
    sim = Sim()
    agents = {"B": B_POS, "H": H_POS}
    if scene == "paired":
        agents["A"] = A_POS
    init_world(sim.arbiter, {"apple": ITEM_POS}, agents)
    apply_arm(sim, arm)
    helper = Helper(sim)
    faulty = FaultyScript(sim, fault, helper, arm)
    faulty.start(1)
    if scene == "paired":
        partner = ScriptedAgent(
            sim, "A", (ITEM,), deliver_ops("A", A_POS, ITEM_POS, DROP, ITEM),
            ttl=24, think=2, priority=5)
        partner.start(2)
    return sim


def metrics(sim: Sim) -> dict:
    arb = sim.arbiter
    incorrect = 0
    for e in arb.log:
        if e[0] != "write":
            continue
        _tag, _seq, lease_id, key, value, _ver = e
        if (key == ITEM and lease_id != "-" and str(value).startswith("world@")
                and value != GOAL):
            incorrect += 1
    wasted = sum(1 for e in arb.log if e[0] in ("op", "op_free") and not e[5])
    val = arb.state[ITEM].value
    holds = [k for k, ent in arb.state.items()
             if k.startswith("hold:") and ent.value != "none"]
    return {
        "success": val == GOAL and not holds,
        "final_item": val,
        "incorrect_commits": incorrect,
        "wasted_ops": wasted,
        "deviations": arb.stats.get("deviations", 0),
        "corrections": arb.stats.get("corrections", 0),
        "b_status": arb.agent_status.get("B", "-"),
    }


def run_scripted() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    mismatches = 0
    print("== 脚本机制组（每格 3 次，要求确定性）==")
    for scene in SCENES:
        for fault in FAULTS:
            for arm in ARMS:
                runs = []
                for _ in range(3):
                    sim = build_scripted(scene, fault, arm)
                    sim.run(HORIZON)
                    runs.append(metrics(sim))
                same = len({json.dumps(r, sort_keys=True) for r in runs}) == 1
                rec = runs[0]
                expect = EXPECTED[(scene, fault, arm)]
                ok = same and rec["success"] == expect
                if not ok:
                    mismatches += 1
                mark = "ok" if ok else "FAIL"
                print(f"  [{mark}] {scene:6} {fault:6} {arm:20} "
                      f"success={int(rec['success'])} expect={int(expect)} "
                      f"bad={rec['incorrect_commits']} waste={rec['wasted_ops']} "
                      f"corr={rec['corrections']} status={rec['b_status']} det={same}")
                rows.append({"scene": scene, "fault": fault, "arm": arm,
                             "deterministic": same, "expect_success": expect, **rec})
    path_out = os.path.join(OUT_DIR, "recovery_scripted.json")
    json.dump(rows, open(path_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"脚本组写到 {path_out}；不一致 {mismatches}")
    return mismatches


class FollowupLLM(LLMAgent):
    def __init__(self, *args, helper: Helper | None = None, arm: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = helper
        self.arm = arm
        self._helper_armed = False

    def on_msg(self, msg) -> None:
        super().on_msg(msg)
        if self.arm != "quarantine_reassign" or self.helper is None or self._helper_armed:
            return
        if msg.type != "DENY_WITH_STATE":
            return
        ev = (msg.verdict or {}).get("evidence") or []
        if any(isinstance(e, (list, tuple)) and e and e[0] == "suspect_blocked" for e in ev):
            self._helper_armed = True
            self.helper.arm()


def run_llm_cell(scene: str, fault: str, arm: str, trial: int, clients: dict) -> dict:
    model_name = "qwen" if trial % 2 == 0 else "deepseek"
    client = clients[model_name]
    client.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    sim = Sim()
    agents = {"B": B_POS, "H": H_POS}
    if scene == "paired":
        agents["A"] = A_POS
    init_world(sim.arbiter, {"apple": ITEM_POS}, agents)
    apply_arm(sim, arm)
    helper = Helper(sim)
    override = None
    once = False
    mode = "sdk_cache"
    if fault == "sensor":
        override = {ITEM: LIE}
    elif fault == "report":
        override = {ITEM: LIE}
        once = True
    elif fault == "natural":
        mode = "llm_claimed"
    goal = f"把 {ITEM} 从它所在位置取起，送到 {fmt_pos(DROP)} 并 place。"
    agent = FollowupLLM(
        sim, "B", client, goal, [ITEM], B_POS,
        belief_override=override, belief_mode=mode, helper=helper, arm=arm)
    agent.override_once = once
    agent.start(1)
    if scene == "paired":
        partner = ScriptedAgent(
            sim, "A", (ITEM,), deliver_ops("A", A_POS, ITEM_POS, DROP, ITEM),
            ttl=24, think=2, priority=5)
        partner.start(2)
    err = None
    try:
        sim.run(HORIZON)
    except Exception as exc:  # noqa: BLE001 — 单局失败不能停掉整格
        err = str(exc)
    rec = {
        "scene": scene, "fault": fault, "arm": arm, "trial": trial,
        "model": model_name, "error": err,
        "tokens": client.usage["prompt_tokens"] + client.usage["completion_tokens"],
        "llm_calls": client.usage["calls"],
        "belief_checks": agent.belief_checks,
        "n_plans": len(agent.plans),
        "corrections_taken": agent.corrections_taken,
        **metrics(sim),
    }
    return rec


def load_llm_done(path: str) -> set[tuple]:
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            done.add((row["scene"], row["fault"], row["arm"], row["trial"]))
    return done


def run_llm(trials: int, only: tuple[str, ...] | None) -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "recovery_llm.jsonl")
    done = load_llm_done(path)
    clients = load_clients()
    if "qwen" not in clients or "deepseek" not in clients:
        print("缺少 qwen 或 deepseek 客户端，LLM 组未启动")
        return 2
    cells = LLM_CELLS
    if only:
        cells = tuple(c for c in LLM_CELLS if c == tuple(only))
        if not cells:
            print(f"未知格 {only}，可选：{LLM_CELLS}")
            return 2
    with open(path, "a", encoding="utf-8") as out:
        for scene, fault, arm in cells:
            for trial in range(trials):
                key = (scene, fault, arm, trial)
                if key in done:
                    continue
                print(f"\n== LLM {scene} {fault} {arm} t{trial} ==")
                rec = run_llm_cell(scene, fault, arm, trial, clients)
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                print(f"  success={int(rec['success'])} bad={rec['incorrect_commits']} "
                      f"waste={rec['wasted_ops']} calls={rec['llm_calls']} "
                      f"model={rec['model']} err={rec['error']}")
    summarize_llm(path)
    return 0


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def summarize_llm(path: str) -> None:
    if not os.path.exists(path):
        return
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    print("\n== LLM pilot 汇总（成功率是次要终点；主终点是错误提交和浪费）==")
    groups: dict[tuple, list] = {}
    for row in rows:
        groups.setdefault((row["scene"], row["fault"], row["arm"]), []).append(row)
    for key in sorted(groups):
        grp = groups[key]
        n = len(grp)
        k = sum(1 for r in grp if r["success"])
        lo, hi = wilson(k, n)
        bad = sum(r["incorrect_commits"] for r in grp) / n
        waste = sum(r["wasted_ops"] for r in grp) / n
        print(f"  {key[0]:6} {key[1]:8} {key[2]:20} "
              f"{k}/{n} CI[{lo:.2f},{hi:.2f}] bad={bad:.2f} waste={waste:.2f}")
    natural = [r for r in rows if r["fault"] == "natural"]
    if not natural:
        return
    checks = [c for r in natural for c in r.get("belief_checks") or []]
    if not checks:
        print("自然组尚无自报信念")
        return
    agree = sum(1 for c in checks if c.get("agree") and c.get("version_agree"))
    value_bad = sum(1 for c in checks if not c.get("agree"))
    ver_bad = sum(1 for c in checks if not c.get("version_agree"))
    print(f"自然自报 {len(checks)} 条：值且版本一致 {agree}，"
          f"值不一致 {value_bad}，版本不一致 {ver_bad}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripted", action="store_true")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--trials", type=int, default=6)
    ap.add_argument("--only", nargs=3, default=None,
                    metavar=("SCENE", "FAULT", "ARM"))
    args = ap.parse_args()
    if not args.scripted and not args.llm:
        args.scripted = True
    code = 0
    if args.scripted:
        code = run_scripted()
    if args.llm:
        code = run_llm(args.trials, tuple(args.only) if args.only else None) or code
    return code


if __name__ == "__main__":
    sys.exit(main())
