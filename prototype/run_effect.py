"""按声明效果准入：T2/C4 真实 LLM 三臂。冻结口径见 CCF_FOLLOWUP_PROTOCOL.md §C。

三臂同一份系统提示（都要求声明 effect），差别只在仲裁器：
  base     不检查
  reject   提交时空跑，不符即拒，只说“效果不符”
  predict  同上，并回传空跑后的实际值与失败算子

用法：
  python run_effect.py --self-check
  python run_effect.py --arm predict --trials 30
  python run_effect.py --report
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from llm_agent import LLMAgent, load_clients, system_prompt
from run_counterfactual import A_FAULTY_OPS
from run_e1_full import TASKS, check_success, make_goal
from testbed import ScriptedAgent, Sim, fmt_pos, init_world

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "ccf_followup")
ARMS = ("plain", "base", "reject", "predict")
SPEC = TASKS["T2"]
ITEM = "item:apple1"
GOAL_VALUE = f"world@{fmt_pos(SPEC['drop'])}"

EFFECT_NOTE = """

另外必须在 JSON 里加一项 effect：你的 ops 全部执行完以后，keys 里每个 key 应该等于什么值，
写法与状态里完全一致，例如 {"effect": {"item:xxx": "world@(x,y)"}}。
仲裁器可能会在执行前按权威状态空跑你的 ops，核对 effect。"""


def out_path(arm: str) -> str:
    return os.path.join(OUT_DIR, f"effect_T2C4_{arm}.json")


class EffectAgent(LLMAgent):
    SYSTEM_PROMPT_OVERRIDE = system_prompt(SPEC["W"], SPEC["H"]) + EFFECT_NOTE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.base_goal = self.goal
        self.feedback = ""
        self.declared: list[dict] = []
        self.effect_rejects = 0
        self.pending_effect: dict | None = None

    def _plan(self, obs: dict) -> dict:
        self.goal = self.base_goal + (f"\n{self.feedback}" if self.feedback else "")
        try:
            plan = super()._plan(obs)
        finally:
            self.goal = self.base_goal
        effect = plan.get("effect")
        if isinstance(effect, dict):
            self.pending_effect = {str(k): str(v).replace(" ", "")
                                   for k, v in effect.items() if str(k) in plan["keys"]}
        else:
            self.pending_effect = None
        self.declared.append({"tick": self.sim.now, "effect": self.pending_effect,
                              "had_feedback": bool(self.feedback)})
        return plan

    def _submit(self) -> None:
        plan = self._plan(self._observe())
        self.keys = plan["keys"]
        self.ops = plan["ops"]
        self.ttl = max(plan["ttl"], self.think + len(self.ops) + 2)
        self.priority = plan["priority"]
        self.sim.arbiter.submit_intent(self.id, self.keys, ops=self.ops,
                                       ttl=self.ttl, priority=self.priority,
                                       belief=self._build_belief(),
                                       effect=self.pending_effect or None)

    def on_msg(self, msg) -> None:
        if msg.type == "EFFECT_REJECTED":
            # 父类 _commit 发现租约已收回，会按重规划余额安排重申；这里只写反馈。
            self.effect_rejects += 1
            payload = msg.payload or {}
            if payload.get("mode") == "predict":
                parts = []
                for key, item in (payload.get("mismatch") or {}).items():
                    parts.append(f"{key} 实际会是 {item['predicted']}，你声明的是 {item['claimed']}")
                failed = payload.get("failed_ops") or []
                if failed:
                    parts.append("会失败的算子：" + "；".join(
                        f"第{f['index'] + 1}个 {f['op']}（{f['detail']}）" for f in failed))
                self.feedback = ("上一份计划被仲裁器拒绝，没有执行。按当前权威状态空跑你的 ops："
                                 + "。".join(parts) + "。请从当前位置重新核对每一步。")
            else:
                self.feedback = "上一份计划被仲裁器拒绝，没有执行：空跑结果与你声明的 effect 不符。请重新核对每一步。"
            print(f"  [{self.id}] 效果不符被拒（第 {self.effect_rejects} 次）")
            return
        super().on_msg(msg)


class PlainAgent(EffectAgent):
    """原始系统提示，不要求 effect。只用来复现 v0.2.3 的 T2/C4 口径。"""
    SYSTEM_PROMPT_OVERRIDE = None


def build(arm: str, trial: int, clients: dict):
    sim = Sim()
    init_world(sim.arbiter, dict(SPEC["items"]), dict(SPEC["agents"]),
               width=SPEC["W"], height=SPEC["H"])
    sim.arbiter.effect_check = {"plain": "off", "base": "off",
                                "reject": "reject", "predict": "predict"}[arm]
    cls = PlainAgent if arm == "plain" else EffectAgent
    if trial % 2 == 0:
        m_a, m_b = clients["qwen"], clients["deepseek"]
    else:
        m_a, m_b = clients["deepseek"], clients["qwen"]
    goal = make_goal(["apple1"], fmt_pos(SPEC["drop"]), False)
    px, py = SPEC["items"]["apple1"]
    override_b = {ITEM: f"world@({min(px + 1, SPEC['W'] - 1)},{py})"}
    a = cls(sim, "A", m_a, goal, [ITEM], SPEC["agents"]["A"])
    b = cls(sim, "B", m_b, goal, [ITEM], SPEC["agents"]["B"],
            belief_override=override_b)
    a.start(1)
    b.start(2)
    return sim, a, b


def run_trial(arm: str, trial: int, clients: dict) -> dict:
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    sim, a, b = build(arm, trial, clients)
    err = None
    try:
        sim.run(SPEC["horizon"])
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
    ok, why = check_success(sim, "C4", "T2")
    arb = sim.arbiter
    rejects = [e for e in arb.log if e[0] == "effect_rejected"]
    final = arb.state[ITEM].value
    a_decl = [d["effect"] for d in a.declared]
    return {
        "arm": arm, "trial": trial,
        "model_A": a.client.model, "model_B": b.client.model,
        "success": ok, "fail_reason": why, "error": err,
        "final_item": final,
        "slip_landed": str(final).startswith("world@") and final != GOAL_VALUE,
        "effect_rejects": len(rejects),
        "rejects_by_agent": {ag: sum(1 for e in rejects if e[3] == ag) for ag in ("A", "B")},
        "reject_detail": [{"agent": e[3], "mismatch": e[4], "failed": e[5]} for e in rejects],
        "fixed_after_reject": bool(ok and any(e[3] == "A" for e in rejects)),
        "a_declared": a_decl,
        "a_missing_effect": sum(1 for d in a_decl if not d),
        "a_effect_not_goal": sum(1 for d in a_decl if d and d.get(ITEM) != GOAL_VALUE),
        "a_plans": [{"thought": p["thought"], "n_ops": p["n_ops"], "ops": p["ops"]}
                    for p in a.plans],
        "b_status": arb.agent_status.get("B", "-"),
        "wasted_ops": sum(1 for e in arb.log if e[0] == "op" and not e[5]),
        "tokens": sum(c.usage["prompt_tokens"] + c.usage["completion_tokens"]
                      for c in clients.values()),
        "llm_calls": sum(c.usage["calls"] for c in clients.values()),
    }


def run_arm(arm: str, trials: int) -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = out_path(arm)
    rows = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    clients = load_clients()
    if "qwen" not in clients or "deepseek" not in clients:
        print("缺少 qwen 或 deepseek")
        return 2
    for t in range(len(rows), trials):
        print(f"\n== {arm} t{t} ==", flush=True)
        rec = run_trial(arm, t, clients)
        rows.append(rec)
        json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"  success={int(rec['success'])} final={rec['final_item']} "
              f"rejects={rec['effect_rejects']} A={rec['model_A']} err={rec['error']}",
              flush=True)
    return 0


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    from math import comb
    n = a + b + c + d
    row1, col1 = a + b, a + c

    def prob(x: int) -> float:
        return comb(col1, x) * comb(n - col1, row1 - x) / comb(n, row1)

    observed = prob(a)
    lo, hi = max(0, row1 - (n - col1)), min(row1, col1)
    return min(1.0, sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= observed + 1e-12))


def report() -> None:
    data = {}
    for arm in ARMS:
        path = out_path(arm)
        if os.path.exists(path):
            data[arm] = json.load(open(path, encoding="utf-8"))
    print("== T2/C4 按声明效果准入 ==")
    for arm, rows in data.items():
        n = len(rows)
        k = sum(r["success"] for r in rows)
        by_model = {}
        for r in rows:
            m = r["model_A"]
            s = by_model.setdefault(m, [0, 0])
            s[0] += int(r["success"])
            s[1] += 1
        rej_trials = [r for r in rows if r["rejects_by_agent"]["A"] > 0]
        fixed = sum(1 for r in rej_trials if r["success"])
        miss = sum(r["a_missing_effect"] for r in rows)
        notgoal = sum(r["a_effect_not_goal"] for r in rows)
        plans = sum(len(r["a_declared"]) for r in rows)
        slips = sum(r["slip_landed"] for r in rows)
        tok = sum(r["tokens"] for r in rows) / max(n, 1)
        print(f"\n[{arm}] 成功 {k}/{n}；按 A 模型 "
              + "，".join(f"{m} {s[0]}/{s[1]}" for m, s in sorted(by_model.items())))
        print(f"  物品落在非投放点 {slips}；A 被效果检查拦过的局 {len(rej_trials)}，其中最终成功 {fixed}")
        print(f"  A 的计划 {plans} 份：没声明 effect {miss}，声明值不是投放点 {notgoal}；平均 tokens {tok:.0f}")
    if "base" in data:
        bn, bk = len(data["base"]), sum(r["success"] for r in data["base"])
        for arm in ("plain", "reject", "predict"):
            if arm in data:
                n, k = len(data[arm]), sum(r["success"] for r in data[arm])
                p = fisher_two_sided(k, n - k, bk, bn - bk)
                print(f"\n{arm} vs base: {k}/{n} vs {bk}/{bn}, Fisher p={p:.4f}")
    if "reject" in data and "predict" in data:
        rn, rk = len(data["reject"]), sum(r["success"] for r in data["reject"])
        pn, pk = len(data["predict"]), sum(r["success"] for r in data["predict"])
        print(f"predict vs reject: {pk}/{pn} vs {rk}/{rn}, "
              f"Fisher p={fisher_two_sided(pk, pn - pk, rk, rn - rk):.4f}")


def self_check() -> int:
    for mode in ("off", "predict"):
        sim = Sim()
        init_world(sim.arbiter, dict(SPEC["items"]), dict(SPEC["agents"]),
                   width=SPEC["W"], height=SPEC["H"])
        sim.arbiter.effect_check = mode
        a = ScriptedAgent(sim, "A", (ITEM,), A_FAULTY_OPS, ttl=15, think=2)
        orig = sim.arbiter.submit_intent

        def submit(agent_id, keys, ops=(), ttl=5, priority=5, belief=None, effect=None):
            orig(agent_id, keys, ops=ops, ttl=ttl, priority=priority, belief=belief,
                 effect={ITEM: GOAL_VALUE})

        sim.arbiter.submit_intent = submit
        a.start(1)
        sim.run(30)
        final = sim.arbiter.state[ITEM].value
        rejects = [e for e in sim.arbiter.log if e[0] == "effect_rejected"]
        if mode == "off":
            assert final == "world@(4,1)" and not rejects, (final, rejects)
        else:
            assert final == "world@(1,3)", final
            assert rejects and rejects[0][4][ITEM]["predicted"] == "world@(4,1)", rejects
    print("self-check ok：off 时错误计划落在 (4,1)；predict 时空跑预测 (4,1) 并拒绝，苹果未动")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=ARMS)
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        return self_check()
    if args.report:
        report()
        return 0
    if not args.arm:
        print("需要 --arm")
        return 2
    code = run_arm(args.arm, args.trials)
    report()
    return code


if __name__ == "__main__":
    sys.exit(main())
