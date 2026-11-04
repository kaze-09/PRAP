"""重规划触发器试点。口径见 PRAP_replan_trigger_protocol.md，跑数前已写死。

三臂：
  fail_only  只在失败算子后重规划
  remain     提交成功但还有物品未送达时，改目标句为剩余物品再规划
  handoff    失败后把未送达物品交给另一个智能体，自己不再重规划

用法：
  python run_replan_trigger.py --smoke
  python run_replan_trigger.py --trials 6
  python run_replan_trigger.py --summarize
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from llm_agent import LLMAgent, load_clients
from testbed import Sim, fetch_ops, fmt_pos, init_world, parse_pos

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "ccf_followup", "replan_trigger.jsonl")

ARMS = ("fail_only", "remain", "handoff")
BUDGET = 3
OP_CAP = 64
MIN_CELL = 4

MAPS = {
    "M1": {"W": 5, "H": 5, "items": {"apple1": (2, 3), "apple2": (4, 1)},
           "agents": {"A": (0, 0), "B": (4, 4)}, "drop": (0, 4), "horizon": 160},
    "M2": {"W": 5, "H": 5, "items": {"apple1": (1, 4), "apple2": (3, 1)},
           "agents": {"A": (0, 1), "B": (4, 0)}, "drop": (4, 4), "horizon": 160},
}


def full_goal(names: list[str], drop: str) -> str:
    seq = "、".join(f"item:{n}" for n in names)
    return f"依次把 {seq} 各自取起并送到 {drop} place（每一件都必须送达）。"


def remain_goal(keys: list[str], drop: str) -> str:
    seq = "、".join(keys)
    return f"尚未送达：把 {seq} 各自取起并送到 {drop} place。已送达的不要再规划。"


def handoff_goal(keys: list[str], drop: str) -> str:
    seq = "、".join(keys)
    return f"交接：同伴未能完成。把 {seq} 各自取起并送到 {drop} place。只做这些。"


class TriggerAgent(LLMAgent):
    def __init__(self, *a, mode: str, targets: list[str], drop_text: str,
                 partner_id: str, **kw):
        super().__init__(*a, **kw)
        self.op_cap = OP_CAP
        self.mode = mode
        self.targets = list(targets)
        self.drop_text = drop_text
        self.drop_value = f"world@{drop_text}"
        self.partner_id = partner_id
        self.subgoal_replans = 0
        self.handoffs_out = 0
        self.received_handoff = False
        self.handoff_pending = False
        self.wake_scheduled = False

    def _submit(self) -> None:
        self.wake_scheduled = False
        super()._submit()

    def _plan(self, obs: dict) -> dict:
        plan = super()._plan(obs)
        plan["goal"] = self.goal
        return plan

    def _undelivered(self) -> list[str]:
        left = []
        for key in self.targets:
            entry = self.sim.arbiter.state.get(key)
            if entry is None or entry.value != self.drop_value:
                left.append(key)
        return left

    def _partner_busy(self, partner: "TriggerAgent") -> bool:
        arb = self.sim.arbiter
        if partner.lease_id and partner.lease_id in arb.leases:
            return True
        return any(p.agent_id == partner.id for p in arb.waiting)

    def _adopt(self, keys: list[str], kind: str) -> None:
        self.item_keys = list(keys)
        self.goal = remain_goal(keys, self.drop_text) if kind == "remain" else handoff_goal(keys, self.drop_text)

    def _wake(self, partner: "TriggerAgent") -> None:
        if self._partner_busy(partner):
            partner.handoff_pending = True
            return
        partner.wake_scheduled = True
        self.sim.schedule(self.sim.now + 1, partner._submit)

    def _consume_handoff(self) -> bool:
        """对方已把剩余物品交给自己。返回 True 表示这次去向已经安排好。"""
        if not self.handoff_pending:
            return False
        self.handoff_pending = False
        left = self._undelivered()
        if left and self.replans_left > 0:
            self.replans_left -= 1
            self.sim.schedule(self.sim.now + 1, self._submit)
        return True

    def _handle_failed_commit(self, failed) -> bool:
        if self.wake_scheduled:
            self.wake_scheduled = False
            return True
        if self._consume_handoff():
            return True
        if self.mode != "handoff" or self.received_handoff or self.handoffs_out:
            return False
        left = self._undelivered()
        if not left:
            return False
        partner: TriggerAgent = self.sim.agents[self.partner_id]
        if partner.received_handoff or partner.handoffs_out:
            return False
        self.handoffs_out += 1
        partner.received_handoff = True
        if partner.replans_left < 1:
            partner.replans_left = 1
        partner._adopt(left, "handoff")
        self._wake(partner)
        print(f"  [{self.id}] 失败 -> 把 {left} 交给 {partner.id}")
        return True

    def _handle_clean_commit(self) -> None:
        if self._consume_handoff():
            return
        if self.mode != "remain" or self.received_handoff:
            return
        left = self._undelivered()
        if not left or self.replans_left <= 0:
            return
        self.replans_left -= 1
        self.subgoal_replans += 1
        self._adopt(left, "remain")
        print(f"  [{self.id}] 还有未送达 {left} -> 按剩余目标重规划"
              f"（剩 {self.replans_left}）")
        self.sim.schedule(self.sim.now + 1, self._submit)


class OneItemStub:
    """离线冒烟：全量目标时只规划 apple1；目标句被改成剩余/交接时规划其中第一件。"""

    def __init__(self, name: str):
        self.name = name
        self.model = "stub"
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def chat(self, system: str, user: str) -> str:
        self.usage["calls"] += 1
        aid = re.search(r"你的ID: (\w+)", user).group(1)
        goal = re.search(r"任务: (.*)\n世界状态:", user).group(1)
        obs = json.loads(re.search(r"世界状态: (\{.*\})\n", user).group(1))
        drop_m = re.search(r"送到 \((-?\d+),(-?\d+)\)", goal)
        drop = (int(drop_m.group(1)), int(drop_m.group(2)))
        if "尚未送达" in goal or goal.startswith("交接"):
            target = re.findall(r"item:\w+", goal)[0]
        else:
            target = "item:apple1"
        pos = parse_pos(obs[f"pos:{aid}"]["value"])
        value = obs[target]["value"]
        if value.startswith("world@"):
            ops = fetch_ops(aid, pos, target, parse_pos(value[6:]), drop)
        elif value == aid:
            ops = []
            here = pos
            dx, dy = drop[0] - here[0], drop[1] - here[1]
            for _ in range(abs(dx)):
                ops.append(("move", {"agent": aid, "dx": 1 if dx > 0 else -1, "dy": 0}))
            for _ in range(abs(dy)):
                ops.append(("move", {"agent": aid, "dx": 0, "dy": 1 if dy > 0 else -1}))
            ops.append(("place", {"agent": aid, "key": target}))
        else:
            ops = [("grab", {"key": target})]
        plan = {"thought": f"stub {target}", "keys": [target],
                "ops": [[name, args] for name, args in ops],
                "ttl": 30, "priority": 5, "belief": {}}
        return json.dumps(plan, ensure_ascii=False)

    @staticmethod
    def extract_json(text: str):
        return json.loads(text)


def check_success(sim: Sim, spec: dict) -> tuple[bool, str, list[str]]:
    drop = f"world@{fmt_pos(spec['drop'])}"
    delivered = []
    for name in spec["items"]:
        key = f"item:{name}"
        entry = sim.arbiter.state.get(key)
        if entry is not None and entry.value == drop:
            delivered.append(key)
        elif entry is None or entry.value != drop:
            return False, f"{key} 未送达（{getattr(entry, 'value', '?')}）", delivered
    holds = [k for k, e in sim.arbiter.state.items()
             if k.startswith("hold:") and e.value != "none"]
    if holds:
        return False, f"结束时仍有人持有: {holds}", delivered
    return True, "ok", delivered


def run_trial(map_id: str, arm: str, trial: int, clients: dict) -> dict:
    spec = MAPS[map_id]
    print(f"\n--- {map_id} {arm} trial {trial} ---", flush=True)
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    sim = Sim()
    init_world(sim.arbiter, spec["items"], spec["agents"], spec["W"], spec["H"])
    if trial % 2 == 0:
        model_a, model_b = clients["qwen"], clients["deepseek"]
    else:
        model_a, model_b = clients["deepseek"], clients["qwen"]
    drop = fmt_pos(spec["drop"])
    names = list(spec["items"])
    targets = [f"item:{n}" for n in names]
    goal = full_goal(names, drop)
    pair = {"A": model_a, "B": model_b}
    agents = {}
    for aid, pos in spec["agents"].items():
        agents[aid] = TriggerAgent(
            sim, aid, pair[aid], goal, targets, pos,
            max_replans=BUDGET, mode=arm, targets=targets,
            drop_text=drop, partner_id="B" if aid == "A" else "A")
    agents["A"].start(1)
    agents["B"].start(2)
    error = None
    try:
        sim.run(spec["horizon"])
    except RuntimeError as exc:
        error = str(exc)
        print(f"  仿真异常: {exc}", flush=True)
    ok, why, delivered = check_success(sim, spec)
    capped = any(p.get("capped") for ag in agents.values() for p in ag.plans)
    before = [p.get("ops_before_cap", 0) for ag in agents.values() for p in ag.plans]
    rec = {
        "map": map_id, "arm": arm, "trial": trial,
        "models": {aid: ag.client.model for aid, ag in agents.items()},
        "success": bool(ok) and error is None,
        "fail_reason": error or why,
        "delivered": delivered,
        "inconclusive": error is not None,
        "capped": capped,
        "would_cut_24": any(n > 24 for n in before),
        "max_ops_before_cap": max(before) if before else 0,
        "replans": sum(max(0, len(ag.plans) - 1) for ag in agents.values()),
        "subgoal_replans": sum(ag.subgoal_replans for ag in agents.values()),
        "handoffs": sum(ag.handoffs_out for ag in agents.values()),
        "tokens": sum(ag.client.usage["prompt_tokens"] + ag.client.usage["completion_tokens"]
                      for ag in agents.values()),
        "llm_calls": sum(ag.client.usage["calls"] for ag in agents.values()),
        "goals": [p.get("goal", "") for ag in agents.values() for p in ag.plans],
    }
    print(f"  => 成功={rec['success']} {rec['fail_reason']} "
          f"送达={delivered} 子目标重规划={rec['subgoal_replans']} "
          f"交接={rec['handoffs']} 调用={rec['llm_calls']}", flush=True)
    return rec


def primary(rows: list[dict], map_id: str, arm: str) -> list[dict]:
    return [r for r in rows if r["map"] == map_id and r["arm"] == arm
            and not r.get("capped") and not r.get("inconclusive")]


def decide(rows: list[dict]) -> dict:
    """返回继续或停止。handoff 不参与。"""
    cells = {}
    invalid = []
    for map_id in MAPS:
        for arm in ("fail_only", "remain"):
            got = primary(rows, map_id, arm)
            horizon_deaths = sum(1 for r in rows
                                 if r["map"] == map_id and r["arm"] == arm
                                 and r.get("inconclusive"))
            cells[f"{map_id}/{arm}"] = {
                "n": len(got),
                "success": sum(1 for r in got if r["success"]),
                "horizon_deaths": horizon_deaths,
            }
            if len(got) < MIN_CELL or horizon_deaths > 2:
                invalid.append(f"{map_id}/{arm}")
    m1 = cells["M1/remain"]["success"] - cells["M1/fail_only"]["success"]
    m2_better = cells["M2/remain"]["success"] > cells["M2/fail_only"]["success"]
    cont = (not invalid) and m1 >= 2 and m2_better
    return {"decision": "CONTINUE" if cont else "STOP", "cells": cells,
            "invalid": invalid, "m1_gap": m1, "m2_remain_higher": m2_better}


def _load(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def summarize(rows: list[dict]) -> str:
    lines = ["", "================ 重规划触发器试点 ================"]
    for map_id in MAPS:
        for arm in ARMS:
            got = [r for r in rows if r["map"] == map_id and r["arm"] == arm]
            prim = primary(rows, map_id, arm)
            k = sum(1 for r in prim if r["success"])
            cut = sum(1 for r in got if r.get("would_cut_24"))
            lines.append(
                f"{map_id}/{arm:<10} 完成 {len(got):>2}  主比较 {k}/{len(prim)}"
                f"  交接 {sum(r['handoffs'] for r in got)}"
                f"  子目标重规划 {sum(r['subgoal_replans'] for r in got)}"
                f"  步数曾>24 {cut}")
    verdict = decide(rows)
    lines.append(f"判定: {verdict['decision']}  M1 差 {verdict['m1_gap']}  "
                 f"M2 remain 更高={verdict['m2_remain_higher']}  "
                 f"无效格={verdict['invalid'] or '无'}")
    lines.append("handoff 不参与判定。")
    return "\n".join(lines)


def smoke() -> int:
    # 判定函数本身：差 2 且第二张图同方向才继续。
    fake = []
    for trial, ok in enumerate([1, 1, 1, 1, 0, 0]):
        fake.append({"map": "M1", "arm": "remain", "trial": trial,
                     "success": bool(ok), "capped": False, "inconclusive": False,
                     "handoffs": 0, "subgoal_replans": 1, "would_cut_24": False})
        fake.append({"map": "M1", "arm": "fail_only", "trial": trial,
                     "success": trial == 0, "capped": False, "inconclusive": False,
                     "handoffs": 0, "subgoal_replans": 0, "would_cut_24": False})
        fake.append({"map": "M2", "arm": "remain", "trial": trial,
                     "success": trial < 3, "capped": False, "inconclusive": False,
                     "handoffs": 0, "subgoal_replans": 1, "would_cut_24": False})
        fake.append({"map": "M2", "arm": "fail_only", "trial": trial,
                     "success": trial < 1, "capped": False, "inconclusive": False,
                     "handoffs": 0, "subgoal_replans": 0, "would_cut_24": False})
    assert decide(fake)["decision"] == "CONTINUE", decide(fake)
    fake_stop = [dict(r, success=(r["arm"] == "remain" and r["map"] == "M1" and r["trial"] < 3)
                      or (r["arm"] == "fail_only" and r["trial"] == 0)) for r in fake]
    assert decide(fake_stop)["decision"] == "STOP"

    clients = {"qwen": OneItemStub("qwen"), "deepseek": OneItemStub("deepseek")}
    expect = {("M1", "fail_only"): False, ("M1", "remain"): True, ("M1", "handoff"): True,
              ("M2", "fail_only"): False, ("M2", "remain"): True, ("M2", "handoff"): True}
    failed = 0
    for map_id, arm in expect:
        rec = run_trial(map_id, arm, 0, clients)
        if rec["success"] != expect[(map_id, arm)]:
            print(f"[FAIL] {map_id} {arm}: 得到成功={rec['success']}，期望 {expect[(map_id, arm)]}")
            failed += 1
        else:
            print(f"[PASS] {map_id} {arm}")
        if arm == "remain" and rec["subgoal_replans"] < 1:
            print(f"[FAIL] {map_id} remain 没有走剩余目标重规划")
            failed += 1
        if arm == "handoff" and rec["handoffs"] < 1:
            print(f"[FAIL] {map_id} handoff 没有交接")
            failed += 1
        if arm == "fail_only" and rec["subgoal_replans"] != 0:
            print(f"[FAIL] {map_id} fail_only 触发了剩余目标重规划")
            failed += 1
    print("冒烟通过" if failed == 0 else f"冒烟失败 {failed}")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--summarize", action="store_true")
    ap.add_argument("--trials", type=int, default=6)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    if args.smoke:
        return smoke()
    rows = _load(args.out)
    if args.summarize:
        print(summarize(rows))
        return 0
    done = {(r["map"], r["arm"], r["trial"]) for r in rows}
    clients = load_clients()
    missing = [n for n in ("qwen", "deepseek") if n not in clients]
    if missing:
        print(f"缺少 API key：{missing}")
        return 2
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    for map_id in MAPS:
        for arm in ARMS:
            for trial in range(args.trials):
                if (map_id, arm, trial) in done:
                    continue
                rec = run_trial(map_id, arm, trial, clients)
                with open(args.out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                rows.append(rec)
    print(summarize(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
