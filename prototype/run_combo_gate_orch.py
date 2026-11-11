"""Lease + write-gate + orchestrator reassignment (the missing combination arm).

The official orchestrator inherits NoProtocolAgent and writes through op_free,
so the cache gate is off. This arm keeps the official assignment / reassignment
logic, but workers submit intents through the lease and the grant-time cache
gate (LLMAgent path, belief_mode=sdk_cache, l4 on).

Reassignment triggers:
  * the assigned worker is isolated (suspect_blocked) after the same retry
    budget as the official orchestrator (2), or
  * a granted plan fails at execution and those retries are exhausted.

Eight-agent cell: same shared injection seed as the official campaign,
``unified-8-0.3-{trial}``. Topology T2/C4 injection is unchanged (always
agent B, +1 x). Logs go to ``combo_gate_orch/`` and do not overwrite
``unified_seed_rerun/``.

Usage:
  python run_combo_gate_orch.py --suite inject
  python run_combo_gate_orch.py --suite t2c4 --trials 1   # smoke
  python run_combo_gate_orch.py --suite all
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime

from comp_baselines import ORCH_SYS, agent_send
from llm_agent import LLMAgent, load_clients
from run_e1_full import TASKS, check_success
from scale_campaign import (
    AGENT_STARTS,
    DROP,
    SPEC_HORIZON,
    W,
    H,
    cell_name,
    check_all_delivered,
)
from testbed import Sim, fmt_pos, init_world

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "combo_gate_orch")
OFFICIAL_PLAN = os.path.join(BASE, "unified_seed_rerun", "injection_plan_scale8.json")
SCALE = 8
FAULT = 0.3
N_TRIALS = 30
SEED_PREFIX = "unified"
MAX_RETRIES = 2


def log(msg: str) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT_DIR, "run.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def unified_rng(scale: int, fault: float, trial: int) -> random.Random:
    return random.Random(f"{SEED_PREFIX}-{scale}-{fault}-{trial}")


def injection_plan(scale: int, fault: float, trial: int) -> list[dict]:
    spec_items = {f"apple{i + 1}": (i + 1, 3) for i in range(scale // 2)}
    item_keys = [f"item:{k}" for k in spec_items]
    rng = unified_rng(scale, fault, trial)
    plan = []
    for i in range(scale):
        my_item = item_keys[i % len(item_keys)]
        base = my_item.split(":")[1]
        px, py = spec_items[base]
        injected = bool(fault > 0 and rng.random() < fault)
        override = None
        effective = False
        if injected:
            shifted = (min(px + 1, W - 1), py)
            override = {my_item: f"world@{fmt_pos(shifted)}"}
            effective = shifted != (px, py)
        plan.append({
            "agent": f"A{i}",
            "item": my_item,
            "true_pos": [px, py],
            "injected": injected,
            "effective": effective,
            "override": override,
            "model": "qwen" if (i + trial) % 2 == 0 else "deepseek",
        })
    return plan


def wasted_ops(arb) -> tuple[int, int]:
    both = sum(1 for e in arb.log if e[0] in ("op", "op_free") and not e[5])
    free_only = sum(1 for e in arb.log if e[0] == "op_free" and not e[5])
    return both, free_only


def save_records(path: str, records: list[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_records(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _isolated(msg) -> bool:
    ev = (getattr(msg, "verdict", None) or {}).get("evidence") or []
    return any(isinstance(e, (list, tuple)) and e and e[0] == "suspect_blocked" for e in ev)


class GateOrchAgent(LLMAgent):
    """Official orch assignment / reassignment, writes go through the lease+gate."""

    def __init__(self, *a, is_orchestrator: bool = False, **kw):
        super().__init__(*a, **kw)
        self.is_orch = is_orchestrator
        self.assigned: list[str] = []
        self._assign_done = False
        self._ever_worked = False
        self.reassignments = 0
        self._failed_items: set = set()
        self._failed_workers: set = set()
        self.retries_left = MAX_RETRIES
        self._handed_off = False
        self.all_agent_ids: list[str] | None = None
        self.item_candidates: dict | None = None
        self.handoffs: list[dict] = []

    def setup_scale(self, all_agent_ids: list, item_candidates: dict) -> None:
        self.all_agent_ids = list(all_agent_ids)
        self.item_candidates = dict(item_candidates)

    def _ready_for_new_task(self) -> None:
        self._handed_off = False
        self.retries_left = MAX_RETRIES
        self._reapplies_left = 3
        self.replans_left = self.max_replans

    def _submit(self) -> None:
        if self.is_orch and not self._assign_done:
            self._assign()
            return
        if self._handed_off:
            return
        if not self.assigned:
            if self.sim.now < 10:
                self.sim.schedule(self.sim.now + 1, self._submit)
            return
        self._ever_worked = True
        self.item_keys = list(self.assigned)
        LLMAgent._submit(self)

    def _assign(self) -> None:
        obs = self._observe()
        if self.item_candidates is not None:
            user = (
                f"你的ID: {self.id}（你是编排者）\n"
                f"任务总目标: 以下每件物品都要由其争用者之一取起并送到投放点 place\n"
                f"世界状态: {json.dumps(obs, ensure_ascii=False)}\n"
                f"物品与候选 worker: {json.dumps(self.item_candidates)}\n"
                '只输出 JSON: {"assignments": {"item:xxx": "A0", ...}, "reason": "..."}\n'
                f"每件物品恰好分给其候选 worker 之一。"
            )
        else:
            user = (
                f"你的ID: {self.id}（你是编排者）\n两个 worker: A、B\n"
                f"任务总目标: {self.goal}\n"
                f"世界状态: {json.dumps(obs, ensure_ascii=False)}\n"
                f"需要分配的物品: {json.dumps(self.item_keys)}\n输出 JSON 分配。"
            )
        plan = None
        for _ in range(2):
            plan = self.client.extract_json(self.client.chat(ORCH_SYS, user))
            if plan and isinstance(plan.get("assignments"), dict):
                break
        self._assign_done = True
        if self.item_candidates is not None:
            if plan is None:
                plan = {
                    "assignments": {k: v[0] for k, v in self.item_candidates.items()},
                    "reason": "解析失败，退化为第一候选",
                }
            fixed = {}
            for k, cands in self.item_candidates.items():
                w = plan["assignments"].get(k)
                fixed[k] = w if w in cands else cands[0]
            print(f"  [gateorch] 分配: {fixed} | {plan.get('reason', '')[:40]}")
            mine = [k for k, w in fixed.items() if w == self.id]
            for w in self.all_agent_ids:
                if w == self.id:
                    continue
                items = [k for k, ww in fixed.items() if ww == w]
                if items:
                    agent_send(self.sim, self.id, w, "ORCH_ASSIGN",
                               {"assignments": {k: w for k in items}})
            if mine:
                self.assigned = mine
                self.sim.schedule(self.sim.now + 1, self._submit)
            return

        if plan is None:
            plan = {"assignments": {k: "A" for k in self.item_keys},
                    "reason": "解析失败，退化为编排者自做"}
        mine, others = [], {}
        for k, w in plan["assignments"].items():
            if w == self.id or w not in ("A", "B"):
                mine.append(k)
            else:
                others[k] = w
        print(f"  [gateorch] 分配: {plan['assignments']} | {plan.get('reason', '')[:50]}")
        if others:
            agent_send(self.sim, self.id,
                       next(w for w in ("A", "B") if w != self.id),
                       "ORCH_ASSIGN", {"assignments": others})
        if mine:
            self.assigned = mine
            self.sim.schedule(self.sim.now + 1, self._submit)

    def _reassign(self, item: str, failed: str) -> None:
        self._failed_workers.add(failed)
        if self.item_candidates is not None:
            cands = [w for w in self.item_candidates.get(item, [])
                     if w not in self._failed_workers]
        else:
            cands = [w for w in ("A", "B") if w not in self._failed_workers]
        if not cands:
            print(f"  [gateorch] {item}: no unused worker left, give up")
            return
        other = cands[0]
        self.reassignments += 1
        print(f"  [gateorch] {item} failed ({failed}) -> reassign to {other}")
        if other == self.id:
            self.assigned = [item]
            self._ready_for_new_task()
            self.sim.schedule(self.sim.now + 1, self._submit)
        else:
            agent_send(self.sim, self.id, other, "ORCH_ASSIGN",
                       {"assignments": {item: other}})

    def _handoff(self, reason: str, item: str | None = None) -> None:
        if self._handed_off:
            return
        self._handed_off = True
        item = item or (self.assigned[0] if self.assigned else (
            self.item_keys[0] if self.item_keys else ""))
        self._failed_items.add(item)
        self.handoffs.append({
            "tick": self.sim.now, "agent": self.id,
            "item": item, "reason": reason,
        })
        print(f"  [gateorch {self.id}] handoff {item} ({reason})")
        if self.is_orch:
            self._reassign(item, failed=self.id)
        else:
            orch = next((a for a in self.sim.agents.values()
                         if getattr(a, "is_orch", False)), None)
            if orch is not None:
                agent_send(self.sim, self.id, orch.id, "ORCH_FAIL",
                           {"item": item, "detail": reason})

    def on_msg(self, msg) -> None:
        p = getattr(msg, "payload", None) or {}
        if msg.type == "ORCH_ASSIGN":
            self.assigned = [k for k, w in p.get("assignments", {}).items()
                             if w == self.id]
            print(f"  [{self.id}] received assignment: {self.assigned}")
            if self.assigned:
                self._ready_for_new_task()
                if self.sim.now > 1:
                    self.sim.schedule(self.sim.now + 1, self._submit)
            return
        if msg.type == "ORCH_FAIL" and self.is_orch:
            item = p.get("item", "")
            fw = p.get("from", "B")
            if fw not in self._failed_workers:
                self._reassign(item, failed=fw)
            return

        isolated = msg.type == "DENY_WITH_STATE" and _isolated(msg)
        if isolated and self.retries_left <= 0:
            self._reapplies_left = 0
        elif isolated:
            self.retries_left -= 1
        super().on_msg(msg)
        if isolated and self.retries_left <= 0:
            self._handoff("isolated")

    def _handle_failed_commit(self, failed) -> bool:
        if self.retries_left > 0:
            self.retries_left -= 1
            self.sim.schedule(self.sim.now + 1, self._submit)
            return True
        item = None
        for e in failed:
            args = e[3] if len(e) > 3 and isinstance(e[3], dict) else (
                e[4] if len(e) > 4 and isinstance(e[4], dict) else {})
            if isinstance(args, dict) and args.get("key"):
                item = args["key"]
                break
        self._handoff("exec_failed", item=item)
        return True


def agent_details(agents, plan: list[dict] | None, arb) -> list[dict]:
    by_id = {p["agent"]: p for p in (plan or [])}
    details = []
    for ag in agents:
        p = by_id.get(ag.id, {})
        details.append({
            "id": ag.id,
            "model": getattr(ag.client, "model", None),
            "item": p.get("item"),
            "injected": p.get("injected"),
            "effective": p.get("effective"),
            "override": p.get("override"),
            "assigned": list(getattr(ag, "assigned", []) or []),
            "status": arb.agent_status.get(ag.id) if hasattr(arb, "agent_status") else None,
            "last_verdict": getattr(ag, "last_verdict", None),
            "handoffs": list(getattr(ag, "handoffs", []) or []),
            "completed_at": getattr(getattr(ag, "report", None), "completed_at", None),
            "blocked_from": getattr(getattr(ag, "report", None), "blocked_from", None),
            "granted_at": getattr(getattr(ag, "report", None), "granted_at", None),
        })
    return details


def write_injection_plan(trials: int) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    trials_plan = []
    n_inj = n_eff = 0
    zero_eff_trials = []
    for t in range(trials):
        plan = injection_plan(SCALE, FAULT, t)
        n_i = sum(1 for p in plan if p["injected"])
        n_e = sum(1 for p in plan if p["effective"])
        n_inj += n_i
        n_eff += n_e
        if n_e == 0:
            zero_eff_trials.append(t)
        trials_plan.append({
            "trial": t, "n_injected": n_i, "n_effective": n_e, "agents": plan,
        })
    total = trials * SCALE
    summary = {
        "seed_formula": f"{SEED_PREFIX}-{{scale}}-{{fault}}-{{trial}}",
        "scale": SCALE, "fault": FAULT, "trials": trials,
        "agent_slots": total,
        "injected": n_inj, "injected_rate": n_inj / total,
        "effective": n_eff, "effective_rate": n_eff / total,
        "expected_effective_rate": FAULT * 6 / 8,
        "zero_effective_trials": zero_eff_trials,
        "note": "Same seed as the official unified-seed campaign.",
    }
    out = {"summary": summary, "trials": trials_plan}
    path = os.path.join(OUT_DIR, "injection_plan_scale8.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(
        f"injection plan: inj {n_inj}/{total}={n_inj/total:.3f}, "
        f"eff {n_eff}/{total}={n_eff/total:.3f} "
        f"(expected {FAULT * 6 / 8:.3f}), zero-eff trials={zero_eff_trials}"
    )
    if os.path.exists(OFFICIAL_PLAN):
        with open(OFFICIAL_PLAN, encoding="utf-8") as f:
            official = json.load(f)
        mismatches = []
        for t in range(trials):
            a = official["trials"][t]["agents"]
            b = trials_plan[t]["agents"]
            for pa, pb in zip(a, b):
                if (pa["injected"], pa["effective"], pa["override"], pa["model"]) != (
                    pb["injected"], pb["effective"], pb["override"], pb["model"]
                ):
                    mismatches.append(t)
                    break
        if mismatches:
            log(f"ERROR: injection plan differs from official on trials {mismatches}")
            raise SystemExit(3)
        log("injection plan matches official unified_seed_rerun/injection_plan_scale8.json")
    log(f"wrote {path}")
    return out


def build_scale_sim(trial: int, clients: dict, plan: list[dict]):
    spec_items = {f"apple{i + 1}": (i + 1, 3) for i in range(SCALE // 2)}
    item_keys = [f"item:{k}" for k in spec_items]
    sim = Sim()
    init_world(
        sim.arbiter, spec_items,
        {f"A{i}": AGENT_STARTS[i] for i in range(SCALE)},
        width=W, height=H,
    )
    # l4_enabled stays True (default)
    agents = []
    for i, p in enumerate(plan):
        client = clients["qwen"] if p["model"] == "qwen" else clients["deepseek"]
        goal = f"把 {p['item']} 从它所在位置取起，送到 {fmt_pos(DROP)} 并 place。"
        ag = GateOrchAgent(
            sim, p["agent"], client, goal, [p["item"]], AGENT_STARTS[i],
            belief_override=p["override"], is_orchestrator=(i == 0),
        )
        agents.append(ag)
    item_candidates = {k: [f"A{j}", f"A{j + SCALE // 2}"] for j, k in enumerate(item_keys)}
    agents[0].setup_scale([f"A{i}" for i in range(SCALE)], item_candidates)
    for i, ag in enumerate(agents):
        ag.start(i + 1)
    return sim, agents, item_keys


def build_t2c4_sim(trial: int, clients: dict):
    spec = TASKS["T2"]
    sim = Sim()
    init_world(sim.arbiter, spec["items"], spec["agents"],
               width=spec["W"], height=spec["H"])
    if trial % 2 == 0:
        m_a, m_b = clients["qwen"], clients["deepseek"]
    else:
        m_a, m_b = clients["deepseek"], clients["qwen"]
    drop = fmt_pos(spec["drop"])
    first_item = list(spec["items"])[0]
    goal = f"把 item:{first_item} 从它所在位置取起，送到 {drop} 并 place。"
    px, py = spec["items"][first_item]
    override_b = {f"item:{first_item}": f"world@({min(px + 1, spec['W'] - 1)},{py})"}
    a = GateOrchAgent(sim, "A", m_a, goal, [f"item:{first_item}"],
                      spec["agents"]["A"], is_orchestrator=True)
    b = GateOrchAgent(sim, "B", m_b, goal, [f"item:{first_item}"],
                      spec["agents"]["B"], belief_override=override_b)
    a.start(1)
    b.start(2)
    return sim, (a, b)


def run_scale_trial(trial: int, clients: dict) -> dict:
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    plan = injection_plan(SCALE, FAULT, trial)
    sim, agents, item_keys = build_scale_sim(trial, clients, plan)
    err = None
    try:
        sim.run(SPEC_HORIZON)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        log(f"  scale gateorch t{trial} exception: {err}")
    ok, why = check_all_delivered(sim.arbiter, item_keys)
    arb = sim.arbiter
    waste, waste_free = wasted_ops(arb)
    rec = {
        "cell": cell_name(SCALE, FAULT, "gateorch"),
        "suite": "combo_gate_orch",
        "seed": f"{SEED_PREFIX}-{SCALE}-{FAULT}-{trial}",
        "trial": trial,
        "scale": SCALE,
        "fault": FAULT,
        "arm": "gateorch",
        "success": ok,
        "fail_reason": why,
        "error": err,
        "delivered": sum(
            1 for k in item_keys
            if arb.state[k].value == f"world@{fmt_pos(DROP)}"
        ),
        "n_items": len(item_keys),
        "n_quarantined": sum(
            1 for a in getattr(arb, "agent_status", {}).values() if a == "suspect"
        ),
        "wasted_ops": waste,
        "wasted_ops_free_only": waste_free,
        "tokens": sum(
            c.usage["prompt_tokens"] + c.usage["completion_tokens"]
            for c in clients.values()
        ),
        "llm_calls": sum(c.usage["calls"] for c in clients.values()),
        "completion_tick": max((a.report.completed_at or 0) for a in agents),
        "n_injected": sum(1 for p in plan if p["injected"]),
        "n_effective": sum(1 for p in plan if p["effective"]),
        "reassignments": sum(getattr(a, "reassignments", 0) for a in agents),
        "agents": agent_details(agents, plan, arb),
    }
    return rec


def run_t2c4_trial(trial: int, clients: dict) -> dict:
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    sim, agents = build_t2c4_sim(trial, clients)
    err = None
    try:
        sim.run(TASKS["T2"]["horizon"])
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        log(f"  t2c4 gateorch t{trial} exception: {err}")
    arb = sim.arbiter
    ok, why = check_success(sim, "C4", "T2")
    waste, waste_free = wasted_ops(arb)
    rec = {
        "suite": "combo_gate_orch",
        "arm": "gateorch",
        "task": "T2",
        "cond": "C4",
        "trial": trial,
        "models": {ag.id: ag.client.model for ag in agents},
        "success": ok,
        "fail_reason": why,
        "error": err,
        "wasted_ops": waste,
        "wasted_ops_free_only": waste_free,
        "failed_ops": waste,
        "n_quarantined": sum(
            1 for a in getattr(arb, "agent_status", {}).values() if a == "suspect"
        ),
        "grants": arb.stats.get("grants") if hasattr(arb, "stats") else None,
        "denies": arb.stats.get("denies") if hasattr(arb, "stats") else None,
        "expires": arb.stats.get("expires") if hasattr(arb, "stats") else None,
        "tokens": sum(
            ag.client.usage["prompt_tokens"] + ag.client.usage["completion_tokens"]
            for ag in agents
        ),
        "llm_calls": sum(ag.client.usage["calls"] for ag in agents),
        "reassignments": sum(getattr(a, "reassignments", 0) for a in agents),
        "verdicts": {ag.id: getattr(ag, "last_verdict", None) for ag in agents},
        "status": dict(getattr(arb, "agent_status", {})),
        "item_final": arb.state["item:apple1"].value,
        "handoffs": [h for ag in agents for h in getattr(ag, "handoffs", [])],
        "agents": agent_details(agents, None, arb),
    }
    return rec


def run_suite(name: str, path: str, trials: int, fn, clients: dict) -> None:
    records = load_records(path)
    if len(records) >= trials:
        log(f"{name}: already has {len(records)} trials, skip")
        return
    log(f"{name}: resume from t{len(records)} -> {trials}, out={path}")
    for t in range(len(records), trials):
        t0 = time.time()
        rec = fn(t, clients)
        records.append(rec)
        save_records(path, records)
        dt = time.time() - t0
        log(
            f"  [{name} t{t}] success={int(rec['success'])} "
            f"waste={rec['wasted_ops']} reassign={rec.get('reassignments', 0)} "
            f"tokens={rec['tokens']} {dt:.1f}s"
        )
    n_ok = sum(r["success"] for r in records)
    log(f"  {name} done: {n_ok}/{len(records)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["inject", "scale8", "t2c4", "all"], default="all")
    ap.add_argument("--trials", type=int, default=N_TRIALS)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    log(f"start suite={args.suite} trials={args.trials}")

    if args.suite in ("inject", "scale8", "all"):
        write_injection_plan(args.trials)
        if args.suite == "inject":
            return 0

    clients = load_clients()
    missing = [n for n in ("qwen", "deepseek") if n not in clients]
    if missing:
        log(f"missing API keys: {missing}")
        return 2
    log("clients loaded: " + ", ".join(f"{k}={v.model}" for k, v in clients.items()))

    try:
        # Topology first: fewer agents, faster smoke signal.
        if args.suite in ("t2c4", "all"):
            run_suite(
                "t2c4 gateorch",
                os.path.join(OUT_DIR, "t2c4_gateorch.json"),
                args.trials, run_t2c4_trial, clients,
            )
        if args.suite in ("scale8", "all"):
            run_suite(
                "scale8 gateorch",
                os.path.join(OUT_DIR, "scale8_f30_gateorch.json"),
                args.trials, run_scale_trial, clients,
            )
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        return 1

    log("all requested work finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
