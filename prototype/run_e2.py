"""E2：L4 信念校验的两个实验（创新点 1 的主证据）。

  E2a（无 API，纯仲裁器）：分类规则的全覆盖扫描。
     噪声两轴：版本滞后 lag（agent 缓存落后 j 个版本）× 值噪声（相信的值被扰动）。
     oracle：滞后（无论值对错）-> stale；版本不落后但值错 -> deviation；全对 -> ok。
     回答：版本号比对规则在全部 (lag, noise) 组合上是否精确可分。

  E2b（真实 API）：LLM 自报信念口径（belief_mode="llm_claimed"）。
     噪声注入：给 agent 一份位置偏移 d 的错误观察（d=0 为健康对照），
     LLM 看到错误世界后大概率自报错误信念 -> L4 应回判 deviation。
     同时记录所有 agent 的"自然误报率"：LLM 刚看完世界仍报错值的频率。

运行：python run_e2.py [--e2b-trials 2]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from llm_agent import LLMAgent, load_clients
from prap_core import Arbiter, digest
from testbed import Sim, fmt_pos, init_world

ITEM, P_TRUE = "apple1", (2, 3)
ITEM_KEY = "item:apple1"
DROP = (0, 4)
GOAL = f"把 item:{ITEM} 从它所在位置取起，送到 {fmt_pos(DROP)} 并 place。"


def shifted(d: int) -> str:
    """位置偏移 d 的（错误）观察值。"""
    x = min(P_TRUE[0] + d, 4)
    return f"world@({x},{P_TRUE[1]})"


# ---------------- E2a：分类规则全覆盖扫描（无 API） ----------------

def run_e2a() -> list[dict]:
    arb = Arbiter(__import__("testbed").OPS_REGISTRY)
    init_world(arb, {ITEM: P_TRUE}, {"A": (0, 0)})
    v_true = arb.state[ITEM_KEY].version
    d_true = digest(f"world@{fmt_pos(P_TRUE)}")

    rows = []
    print("\n== E2a：L4 分类规则扫描（无 API，oracle 已知）==")
    print(f"{'滞后lag':>6}{'值正确':>8}{'期望类别':>12}{'实际类别':>12}{'判定':>6}")
    for lag in range(0, 4):
        for correct_value in (True, False):
            bver = v_true - lag
            bdig = d_true if correct_value else digest(shifted(1))
            if lag > 0:
                expected = "stale"       # 版本落后 = 时序型（规范 §3.5：版本优先）
            else:
                expected = "ok" if correct_value else "deviation"
            got = arb._classify([(ITEM_KEY, bver, bdig)])["class"]
            ok = got == expected
            rows.append({"lag": lag, "correct_value": correct_value,
                         "expected": expected, "got": got, "ok": ok})
            print(f"{lag:>6}{str(correct_value):>8}{expected:>12}{got:>12}"
                  f"{'OK' if ok else 'FAIL':>6}")
    n_ok = sum(r["ok"] for r in rows)
    print(f"  => 分类精确性：{n_ok}/{len(rows)} 组合符合规范 §3.5 定义")
    return rows


# ---------------- E2b：LLM 自报信念 + 注入偏移扫描（真实 API） ----------------

def build_e2b_sim(clients: dict, d: int, trial: int):
    """d=0 健康对照；d>=1 给 B 一份位置偏移 d 的观察。模型按 trial 奇偶交叉。"""
    sim = Sim()
    init_world(sim.arbiter, {ITEM: P_TRUE}, {"A": (0, 0), "B": (4, 4)})
    if trial % 2 == 0:
        m_a, m_b = clients["qwen"], clients["deepseek"]
    else:
        m_a, m_b = clients["deepseek"], clients["qwen"]
    override = {ITEM_KEY: shifted(d)} if d > 0 else None
    a = LLMAgent(sim, "A", m_a, GOAL, [ITEM_KEY], (0, 0),
                 belief_mode="llm_claimed")
    b = LLMAgent(sim, "B", m_b, GOAL, [ITEM_KEY], (4, 4),
                 belief_override=override, belief_mode="llm_claimed")
    a.start(1)
    b.start(2)
    return sim, (a, b)


def run_e2b(clients: dict, trials: int, horizon: int) -> list[dict]:
    records = []
    for d in (0, 1, 2, 3):
        for t in range(trials):
            for c in clients.values():
                c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
            print(f"\n--- E2b 偏移 d={d} trial {t} ---")
            sim, agents = build_e2b_sim(clients, d, t)
            try:
                sim.run(horizon)
            except RuntimeError as e:
                print(f"  仿真异常: {e}")
            arb = sim.arbiter
            rec = {"d": d, "trial": t,
                   "models": {ag.id: ag.client.model for ag in agents},
                   "verdicts": {ag.id: ag.last_verdict for ag in agents},
                   "success": arb.state[ITEM_KEY].value == f"world@{fmt_pos(DROP)}",
                   "failed_ops": sum(1 for e in arb.log
                                     if e[0] in ("op", "op_free") and not e[5]),
                   "tokens": sum(ag.client.usage["prompt_tokens"]
                                 + ag.client.usage["completion_tokens"]
                                 for ag in agents),
                   "claimed": {ag.id: dict(ag.claimed_belief) for ag in agents},
                   "authority": {ITEM_KEY: arb.state[ITEM_KEY].value},
                   "belief_checks": {ag.id: list(ag.belief_checks) for ag in agents},
                   }
            records.append(rec)
            print(f"  => verdicts={rec['verdicts']} success={rec['success']} "
                  f"failed_ops={rec['failed_ops']} tokens={rec['tokens']}")

    # 自然误报率（v0.2.1 修复口径）：每次规划的"自报值 vs 计划时刻真值"，
    # 分"健康 agent"（自然噪声）与"被注入 agent"（受控噪声）两桶统计
    print("\n== E2b 附：LLM 自然误报率（计划时刻真值快照口径）==")
    buckets: dict[str, dict[str, list[bool]]] = {"健康": {}, "被注入": {}}
    for rec in records:
        for aid, checks in rec["belief_checks"].items():
            model = rec["models"][aid]
            for c in checks:
                name = "被注入" if c["injected"] else "健康"
                bucket = buckets[name].setdefault(model, [])
                bucket.append(not c["agree"])   # True = 误报
    for name in ("健康", "被注入"):
        for model, flags in sorted(buckets[name].items()):
            if flags:
                print(f"  [{name}] {model}: 误报 {sum(flags)}/{len(flags)} "
                      f"({sum(flags) / len(flags):.0%})")
        if not buckets[name]:
            print(f"  [{name}] 无样本")

    # v0.2.2 新增：版本自报保真率（LLM 对 version 的转抄保真度）
    print("\n== E2b 附：LLM 版本自报保真率（v0.2.2 新口径）==")
    vbuckets: dict[str, dict[str, list[bool]]] = {"健康": {}, "被注入": {}}
    for rec in records:
        for aid, checks in rec["belief_checks"].items():
            model = rec["models"][aid]
            for c in checks:
                name = "被注入" if c["injected"] else "健康"
                vbuckets[name].setdefault(model, []).append(not c["version_agree"])
    for name in ("健康", "被注入"):
        for model, flags in sorted(vbuckets[name].items()):
            if flags:
                print(f"  [{name}] {model}: 版本误报 {sum(flags)}/{len(flags)} "
                      f"({sum(flags) / len(flags):.0%})")
        if not vbuckets[name]:
            print(f"  [{name}] 无样本")
    return records


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2b-trials", type=int, default=2)
    ap.add_argument("--horizon", type=int, default=80)
    args = ap.parse_args()

    rows_a = run_e2a()

    clients = load_clients()
    missing = [n for n in ("qwen", "deepseek") if n not in clients]
    if missing:
        print(f"\n缺少 API key：{missing}，仅完成 E2a。")
        records_b = []
    else:
        records_b = run_e2b(clients, args.e2b_trials, args.horizon)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"e2a": rows_a, "e2b": records_b}, f, ensure_ascii=False, indent=1)
    print(f"\n结果已存 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
