"""E1 批次实验：n 扩样 + 多任务泛化（真实 LLM agent，Qwen + DeepSeek 交叉指派）。

任务族（TASKS）：
  T1  5x5，apple1@(2,3)，投放 (0,4)              —— 主任务（统计功效用，n=30）
  T2  5x5 换拓扑，apple1@(1,3)，投放 (4,0)        —— 泛化：布局无关性
  T3  5x5 双物品链式任务（apple1、apple2 依次送达）—— 泛化：长事务/多目标
  T4  7x7 大图                                    —— 泛化：路径长度/时长压力

条件：
  C1 无争用基线（仅 T1）      C2 争用        C3 争用+挂死
  C4 争用+幻觉               C5 无协议对照（仅 T1）

运行（支持 --task / --trials / --out，断点续跑见 run_campaign.py）：
  python run_e1_full.py --trials 30 --conds C1 --task T1 --out e1_T1_C1_n30.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from llm_agent import LLMAgent, NoProtocolAgent, load_clients
from testbed import Sim, fmt_pos, init_world

TASKS = {
    "T1": {"W": 5, "H": 5, "items": {"apple1": (2, 3)},
           "agents": {"A": (0, 0), "B": (4, 4)}, "drop": (0, 4),
           "c1_extra": {"apple2": (4, 0)}, "horizon": 80},
    "T2": {"W": 5, "H": 5, "items": {"apple1": (1, 3)},
           "agents": {"A": (0, 4), "B": (2, 0)}, "drop": (4, 0), "horizon": 80},
    "T3": {"W": 5, "H": 5, "items": {"apple1": (2, 3), "apple2": (4, 1)},
           "agents": {"A": (0, 0), "B": (4, 4)}, "drop": (0, 4),
           "chain": True, "horizon": 140},
    "T4": {"W": 7, "H": 7, "items": {"apple1": (3, 5)},
           "agents": {"A": (0, 0), "B": (6, 6)}, "drop": (0, 6), "horizon": 100},
}


def make_goal(names: list[str], drop: str, chain: bool) -> str:
    if len(names) == 1:
        return f"把 item:{names[0]} 从它所在位置取起，送到 {drop} 并 place。"
    seq = "、".join(f"item:{n}" for n in names)
    return f"依次把 {seq} 各自取起并送到 {drop} place（每一件都必须送达）。"


def build_sim(cond: str, clients: dict, trial: int, horizon: int | None = None,
              task: str = "T1"):
    spec = TASKS[task]
    sim = Sim()
    items = dict(spec["items"])
    if cond == "C1" and "c1_extra" in spec:
        items.update(spec["c1_extra"])
    init_world(sim.arbiter, items, spec["agents"],
               width=spec["W"], height=spec["H"])

    # 模型交叉指派：偶数 trial A=qwen / B=deepseek，奇数对调
    if trial % 2 == 0:
        m_a, m_b = clients["qwen"], clients["deepseek"]
    else:
        m_a, m_b = clients["deepseek"], clients["qwen"]

    drop = fmt_pos(spec["drop"])
    first_item = list(spec["items"])[0]

    def goal_for(aid: str) -> str:
        if cond == "C1" and "c1_extra" in spec:
            names = ["apple1"] if aid == "A" else list(spec["c1_extra"])
        elif spec.get("chain"):
            names = list(spec["items"])
        else:
            names = [first_item]
        return make_goal(names, drop, bool(spec.get("chain")))

    stuck_a = (cond == "C3")
    override_b = None
    if cond == "C4":
        px, py = spec["items"][first_item]
        shifted = f"world@({min(px + 1, spec['W'] - 1)},{py})"
        override_b = {f"item:{first_item}": shifted}

    cls = NoProtocolAgent if cond == "C5" else LLMAgent
    keys_a = tuple(f"item:{n}" for n in
                   (["apple1"] if (cond == "C1" and "c1_extra" in spec)
                    else (list(spec["items"]) if spec.get("chain") else [first_item])))
    keys_b = tuple(f"item:{n}" for n in
                   (list(spec["c1_extra"]) if (cond == "C1" and "c1_extra" in spec)
                    else (list(spec["items"]) if spec.get("chain") else [first_item])))
    a = cls(sim, "A", m_a, goal_for("A"), list(keys_a),
            spec["agents"]["A"], stuck=stuck_a)
    b = cls(sim, "B", m_b, goal_for("B"), list(keys_b),
            spec["agents"]["B"], belief_override=override_b)
    a.start(1)
    b.start(2)

    # oracle：仅 C4 有已知真值（B 被注入幻觉应判 deviation，健康 A 应判 ok）
    oracle = {"B": "deviation", "A": "ok"} if cond == "C4" else {}
    return sim, (a, b), oracle


def check_success(sim: Sim, cond: str, task: str = "T1") -> tuple[bool, str]:
    """成功 = 该任务所有目标物品都在投放点且无人仍持有；不变量 = 无双重持有。"""
    spec = TASKS[task]
    arb = sim.arbiter
    targets = [f"item:{n}" for n in spec["items"]]
    if cond == "C1" and "c1_extra" in spec:
        targets += [f"item:{n}" for n in spec["c1_extra"]]
    for key in targets:
        e = arb.state.get(key)
        if e is None or e.value != f"world@{fmt_pos(spec['drop'])}":
            return False, f"{key} 未送达（{getattr(e, 'value', '?')}）"
    holds = [k.split(":")[1] for k, e in arb.state.items()
             if k.startswith("hold:") and e.value != "none"]
    if holds:
        return False, f"结束时仍有人持有: {holds}"
    return True, "ok"


def run_trial(cond: str, trial: int, clients: dict, horizon: int,
              task: str = "T1") -> dict:
    print(f"\n--- 任务 {task} 条件 {cond} trial {trial} ---")
    # client 跨 trial 复用，usage 是累计的：每个 trial 前清零，保证 token 统计按次计
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    sim, agents, oracle = build_sim(cond, clients, trial, horizon, task)
    try:
        sim.run(horizon)
    except RuntimeError as e:
        print(f"  仿真异常: {e}")
    arb = sim.arbiter
    ok, why = check_success(sim, cond, task)
    failed_ops = sum(1 for e in arb.log if e[0] in ("op", "op_free") and not e[5])

    # 按 agent 归因的算子级诊断（规划可靠性分析用）
    lease_agent: dict = {}
    for e in arb.log:
        if e[0] == "grant":
            lease_agent[e[2]] = e[3]
    agent_ops: dict = {}
    for e in arb.log:
        if e[0] == "op":
            aid = lease_agent.get(e[2], "?")
        elif e[0] == "op_free":
            aid = e[4].get("agent", "?")
        else:
            continue
        d = agent_ops.setdefault(aid, {"ops": 0, "failed": 0, "fail_names": []})
        d["ops"] += 1
        if not e[5]:
            d["failed"] += 1
            d["fail_names"].append(e[3])

    rec = {
        "task": task, "cond": cond, "trial": trial,
        "models": {ag.id: ag.client.model for ag in agents},
        "success": ok, "fail_reason": why,
        "grants": arb.stats["grants"], "denies": arb.stats["denies"],
        "expires": arb.stats["expires"], "commits": arb.stats["commits"],
        "commits_rejected": arb.stats.get("commits_rejected", 0),
        "replans": sum(max(0, len(ag.plans) - 1) for ag in agents),
        "failed_ops": failed_ops,
        "agent_ops": agent_ops,
        "tokens": sum(ag.client.usage["prompt_tokens"] + ag.client.usage["completion_tokens"]
                      for ag in agents),
        "tokens_by_agent": {ag.id: ag.client.usage["prompt_tokens"]
                            + ag.client.usage["completion_tokens"] for ag in agents},
        "llm_calls": sum(ag.client.usage["calls"] for ag in agents),
        "verdicts": {ag.id: ag.last_verdict for ag in agents if hasattr(ag, "last_verdict")},
        "blocked_from": {ag.id: ag.report.blocked_from for ag in agents},
        "granted_at": {ag.id: ag.report.granted_at for ag in agents},
        "completed_at": {ag.id: ag.report.completed_at for ag in agents},
    }
    # 逐算子调试数据：LLM 计划全量（ops 文本）+ 仲裁器算子执行序列
    rec["plans"] = {ag.id: ag.plans for ag in agents if getattr(ag, "plans", None)}
    rec["op_seq"] = [{"agent": lease_agent.get(e[2], "?"), "op": e[3],
                      "args": e[4], "ok": e[5]}
                     for e in arb.log if e[0] == "op"]
    # L4 oracle 评测（仅 C4）
    l4 = {}
    if cond == "C4":
        for aid, expected in (("B", "deviation"), ("A", "ok")):
            ag = next(a for a in agents if a.id == aid)
            l4[aid] = {"expected": expected, "got": ag.last_verdict,
                       "correct": ag.last_verdict == expected}
    rec["l4_oracle"] = l4
    print(f"  => 成功={ok}{'（' + why + '）' if not ok else ''} "
          f"denies={rec['denies']} expires={rec['expires']} replans={rec['replans']} "
          f"失败算子={failed_ops} tokens={rec['tokens']}")
    return rec


def aggregate(records: list[dict]) -> str:
    import statistics
    lines = ["\n================ E1 批次汇总 ================",
             f"{'任务/条件':<10}{'n':>3}{'成功率':>7}{'denies':>7}{'expires':>8}"
             f"{'重规划':>7}{'失败算子':>9}{'tokens(均值±标差)':>18}{'L4正确':>8}"]
    labels = sorted({f"{r.get('task', 'T1')}/{r['cond']}" for r in records})
    for label in labels:
        task, cond = label.split("/")
        rs = [r for r in records
              if r.get("task", "T1") == task and r["cond"] == cond]
        n = len(rs)
        sr = sum(r["success"] for r in rs) / n
        l4 = [x for r in rs for x in r["l4_oracle"].values()]
        l4s = f"{sum(x['correct'] for x in l4)}/{len(l4)}" if l4 else "-"
        toks = [r["tokens"] for r in rs]
        tok_s = (f"{statistics.mean(toks):.0f}±{statistics.stdev(toks):.0f}"
                 if n > 1 else str(toks[0]))
        lines.append(f"{label:<10}{n:>3}{sr:>6.0%}{sum(r['denies'] for r in rs) / n:>7.1f}"
                     f"{sum(r['expires'] for r in rs) / n:>8.1f}"
                     f"{sum(r['replans'] for r in rs) / n:>7.1f}"
                     f"{sum(r['failed_ops'] for r in rs) / n:>9.1f}"
                     f"{tok_s:>18}"
                     f"{l4s:>8}")
    lines.append("--------------------------------------------")
    for r in records:
        if not r["success"]:
            lines.append(f"未成功: {r.get('task', 'T1')}/{r['cond']}/t{r['trial']}"
                         f" - {r['fail_reason']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--conds", type=str, default="C2")
    ap.add_argument("--task", type=str, default="T2", choices=list(TASKS))
    ap.add_argument("--horizon", type=int, default=None,
                    help="覆盖任务默认 horizon")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    clients = load_clients()
    missing = [n for n in ("qwen", "deepseek") if n not in clients]
    if missing:
        print(f"缺少 API key：{missing}")
        return 2

    conds = [c.strip().upper() for c in args.conds.split(",")]
    horizon = args.horizon or TASKS[args.task]["horizon"]
    records = []
    for c in conds:
        for t in range(args.trials):
            records.append(run_trial(c, t, clients, horizon, task=args.task))

    print(aggregate(records))
    out = args.out or f"e1_{args.task}_batch.json"
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), out)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print(f"\n原始记录已存 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
