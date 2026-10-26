"""E1 完整版：参数化批次实验（真实 LLM agent，Qwen + DeepSeek 交叉指派）。

条件矩阵（每条件 trials 次，模型指派按 trial 奇偶交叉）：
  C1 无争用基线    2 agent / 2 物品 / 无故障       —— 完整性检查
  C2 争用          2 agent / 1 物品 / 无故障       —— DENY -> 停靠 -> 接管 -> 重规划
  C3 争用+挂死     A 获授后挂死                    —— TTL 释放恢复时间
  C4 争用+幻觉     B 的观察被篡改（oracle 已知）   —— L4 检测准确率
  C5 无协议基线    争用但不做 INTENT/租约          —— 浪费工作对比

指标：成功率、完成 tick、denies/expires/重规划数、失败算子数、
      token 开销、L4 判定（oracle 对照）、不变量检查（双重持有）。

运行：python run_e1_full.py [--trials 2] [--conds C1,C2,C3,C4,C5] [--horizon 80]
结果存 e1_results.json。LLM 调用为阻塞式，整批约需几分钟。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from llm_agent import LLMAgent, NoProtocolAgent, load_clients
from prap_core import digest
from testbed import Sim, fmt_pos, init_world

ITEM1, P1 = "apple1", (2, 3)
ITEM2, P2 = "apple2", (4, 0)
DROP = (0, 4)


def build_sim(cond: str, clients: dict, trial: int, horizon: int):
    """按条件构建仿真。返回 (sim, agents, oracle)。

    oracle: 已知真值（幻觉 agent 应被判 deviation；健康 agent 应为 ok）。
    """
    sim = Sim()
    two_items = (cond == "C1")
    items = {ITEM1: P1, ITEM2: P2} if two_items else {ITEM1: P1}
    init_world(sim.arbiter, items, {"A": (0, 0), "B": (4, 4)})

    # 模型交叉指派：偶数 trial A=qwen / B=deepseek，奇数对调
    if trial % 2 == 0:
        m_a, m_b = clients["qwen"], clients["deepseek"]
    else:
        m_a, m_b = clients["deepseek"], clients["qwen"]

    goal_a = (f"把 item:{ITEM1} 从它所在位置取起，送到 {fmt_pos(DROP)} 并 place。")
    goal_b = (f"把 item:{ITEM2 if two_items else ITEM1} 从它所在位置取起，"
              f"送到 {fmt_pos(DROP)} 并 place。")

    stuck_a = (cond == "C3")
    override_b = {f"item:{ITEM1}": "world@(3,1)"} if cond == "C4" else None

    cls = NoProtocolAgent if cond == "C5" else LLMAgent
    a = cls(sim, "A", m_a, goal_a, [f"item:{ITEM1}"], (0, 0), stuck=stuck_a)
    b = cls(sim, "B", m_b, goal_b, [f"item:{ITEM2 if two_items else ITEM1}"], (4, 4),
            belief_override=override_b)
    a.start(1)
    b.start(2)

    # oracle：已知真值（C4 中 B 被注入幻觉应判 deviation，健康的 A 应判 ok）
    oracle = {"B": "deviation", "A": "ok"} if cond == "C4" else {}
    return sim, (a, b), oracle


def check_success(sim, cond: str, agents) -> tuple[bool, str]:
    """成功 = 所有目标物品被送达 DROP 且无人仍持有；不变量 = 无双重持有。"""
    arb = sim.arbiter
    targets = [f"item:{ITEM2}"] if cond == "C1" else [f"item:{ITEM1}"]
    for key in targets:
        e = arb.state.get(key)
        if e is None or e.value != f"world@{fmt_pos(DROP)}":
            return False, f"{key} 未送达（{getattr(e, 'value', '?')}）"
    holds = [k.split(":")[1] for k, e in arb.state.items()
             if k.startswith("hold:") and e.value != "none"]
    if holds:
        return False, f"结束时仍有人持有: {holds}"
    return True, "ok"


def run_trial(cond: str, trial: int, clients: dict, horizon: int) -> dict:
    print(f"\n--- 条件 {cond} trial {trial} ---")
    # client 跨 trial 复用，usage 是累计的：每个 trial 前清零，保证 token 统计按次计
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    sim, agents, oracle = build_sim(cond, clients, trial, horizon)
    try:
        sim.run(horizon)
    except RuntimeError as e:
        print(f"  仿真异常: {e}")
    arb = sim.arbiter
    ok, why = check_success(sim, cond, agents)
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
        "cond": cond, "trial": trial,
        "models": {ag.id: ag.client.model for ag in agents},
        "success": ok, "fail_reason": why,
        "grants": arb.stats["grants"], "denies": arb.stats["denies"],
        "expires": arb.stats["expires"], "commits": arb.stats["commits"],
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
    # L4 oracle 评测
    l4 = {}
    for aid, expected in oracle.items():
        if expected is not None:
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
             f"{'条件':<6}{'n':>3}{'成功率':>7}{'denies':>7}{'expires':>8}"
             f"{'重规划':>7}{'失败算子':>9}{'tokens(均值±标差)':>18}{'L4正确':>8}"]
    conds = sorted({r["cond"] for r in records})
    for c in conds:
        rs = [r for r in records if r["cond"] == c]
        n = len(rs)
        sr = sum(r["success"] for r in rs) / n
        l4 = [x for r in rs for x in r["l4_oracle"].values()]
        l4s = f"{sum(x['correct'] for x in l4)}/{len(l4)}" if l4 else "-"
        toks = [r["tokens"] for r in rs]
        tok_s = (f"{statistics.mean(toks):.0f}±{statistics.stdev(toks):.0f}"
                 if n > 1 else str(toks[0]))
        lines.append(f"{c:<6}{n:>3}{sr:>6.0%}{sum(r['denies'] for r in rs) / n:>7.1f}"
                     f"{sum(r['expires'] for r in rs) / n:>8.1f}"
                     f"{sum(r['replans'] for r in rs) / n:>7.1f}"
                     f"{sum(r['failed_ops'] for r in rs) / n:>9.1f}"
                     f"{tok_s:>18}"
                     f"{l4s:>8}")
    lines.append("--------------------------------------------")
    for r in records:
        if not r["success"]:
            lines.append(f"未成功: {r['cond']}/t{r['trial']} - {r['fail_reason']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--conds", type=str, default="C1,C2,C3,C4,C5")
    ap.add_argument("--horizon", type=int, default=80)
    ap.add_argument("--out", type=str, default="e1_results.json",
                    help="结果文件名（分片跑批时用不同文件名）")
    args = ap.parse_args()

    clients = load_clients()
    missing = [n for n in ("qwen", "deepseek") if n not in clients]
    if missing:
        print(f"缺少 API key：{missing}")
        return 2

    conds = [c.strip().upper() for c in args.conds.split(",")]
    records = []
    for c in conds:
        for t in range(args.trials):
            records.append(run_trial(c, t, clients, args.horizon))

    print(aggregate(records))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print(f"\n原始记录已存 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
