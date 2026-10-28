"""E4 基线对比：PRAP 协议 vs 三个代表性基线（真实 LLM）。

基线设计（都跑与 E1 相同的任务/条件，直接可比）：
  pessim    悲观锁：协议保留，但 TTL=无穷大（拿到即不放，直到显式 release）。
            预期：C3 持有者挂死 -> 等待队列永堵 -> 结构性死锁。
            对照 PRAP C3（TTL 到期自动收回 + 接管）。
  occ       乐观并发（OCC）：无 INTENT/租约，直接逐 tick 执行；任一算子失败
            （冲突显现）即中止剩余算子、用新观察重规划（有限次）。
            预期：争用下浪费工作（失败算子）多于 PRAP（事前 DENY）。
  promptfix 提示词修复（软方案）：无协议 + 协调感知强化提示词（告知对方存在、
            先核实物品位置、输出前自检坐标算术）。测"不改机制、只改提示"的上限。

场景矩阵（首轮，n=5）：
  pessim    T1/C3, T2/C3
  occ       T1/C2, T2/C2
  promptfix T1/C2, T2/C2, T2/C4

用法：
  python run_e4.py --baseline pessim --task T1 --cond C3 --trials 5 --out e4_pessim_T1_C3.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

from llm_agent import LLMAgent, NoProtocolAgent, load_clients
from prap_core import Lease, digest
from run_e1_full import TASKS, check_success
from testbed import Sim, fmt_pos, init_world

# ---------- 基线 agent 变体 ----------


class PessimisticLockAgent(LLMAgent):
    """悲观锁基线：协议机制全保留，唯一差别 TTL=无穷大。"""

    def __init__(self, *a, **kw):
        kw["ttl_override"] = 10 ** 9
        super().__init__(*a, **kw)


class OCCAgent(NoProtocolAgent):
    """乐观并发基线：无锁执行，算子失败 = 冲突显现 -> 中止并重规划。"""

    MAX_RETRIES = 2

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.retries_left = self.MAX_RETRIES
        self._aborted = False
        self._i = 0

    def _submit(self) -> None:
        self._aborted = False
        plan = self._plan(self._observe())
        self.ops = plan["ops"]
        self.keys = plan["keys"]
        self._i = 0
        for i, op in enumerate(self.ops):
            self.sim.schedule(self.sim.now + 2 + i, lambda op=op: self._exec_op(op))

    def _exec_op(self, op: tuple) -> None:
        if self._aborted:
            return   # OCC abort：冲突后剩余算子全部作废
        arb = self.sim.arbiter
        lease = Lease("free", self.id, (), 10 ** 9, 0, 0)
        handler = arb.ops_registry[op[0]]
        ok, detail = handler(arb, lease, op[1])
        arb.log.append(("op_free", arb._next_seq(), "free", op[0], op[1], ok, detail))
        if not ok and self.retries_left > 0:
            self._aborted = True
            self.retries_left -= 1
            print(f"  [{self.id}] OCC 冲突（{op[0]} 失败）-> 中止剩余算子，重规划")
            self.sim.schedule(self.sim.now + 1, self._submit)


class OCCTrueAgent(NoProtocolAgent):
    """真 OCC 基线（E4b 新增）：执行前验证（validate），冲突即整体中止（abort）。

    与 OCCAgent 的区别：冲突不是"算子失败后才显现"（此时已执行了部分移动、
    浪费了算子），而是在**首个算子执行前**比对计划时刻快照——目标 key 的
    (version, digest) 任一变化即整体中止，零部分执行。这是数据库 OCC 的
    validate-then-execute 语义（带 abort 重试预算，无回滚——不可逆算子不执行）。"""

    MAX_RETRIES = 2

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.retries_left = self.MAX_RETRIES
        self._aborted = False
        self._first = True
        self._validate_set: dict = {}
        self.aborts = 0

    def _submit(self) -> None:
        self._aborted = False
        plan = self._plan(self._observe())
        self.ops = plan["ops"]
        self.keys = plan["keys"]
        arb = self.sim.arbiter
        self._validate_set = {k: (arb.state[k].version, digest(arb.state[k].value))
                              for k in self.keys if k in arb.state}
        self._first = True
        for i, op in enumerate(self.ops):
            self.sim.schedule(self.sim.now + 2 + i, lambda op=op: self._exec_op(op))

    def _exec_op(self, op: tuple) -> None:
        if self._aborted:
            return
        arb = self.sim.arbiter
        if self._first:
            self._first = False
            for k, (v, d) in self._validate_set.items():
                e = arb.state.get(k)
                if e is None or (e.version, digest(e.value)) != (v, d):
                    self._aborted = True
                    self.aborts += 1
                    print(f"  [{self.id}] OCC 验证失败（{k} 已变化）-> 整体中止，重规划")
                    if self.retries_left > 0:
                        self.retries_left -= 1
                        self.sim.schedule(self.sim.now + 1, self._submit)
                    return
        lease = Lease("free", self.id, (), 10 ** 9, 0, 0)
        handler = arb.ops_registry[op[0]]
        ok, detail = handler(arb, lease, op[1])
        arb.log.append(("op_free", arb._next_seq(), "free", op[0], op[1], ok, detail))
        if not ok and self.retries_left > 0:
            self._aborted = True
            self.retries_left -= 1
            print(f"  [{self.id}] OCC 算子失败（{op[0]}）-> 中止剩余算子，重规划")
            self.sim.schedule(self.sim.now + 1, self._submit)


FIX_PROMPT_HEAD = """你是 5x5 网格世界中的具身 agent，与另一个 agent 协作。坐标 (x,y)：x 向右，y 向下，范围 0..4。
状态 key 约定：item:{名字}=物品位置，pos:{你的ID}=你的位置，hold:{你的ID}=你持有的物品。
注意：另一个 agent 也在执行类似任务，它可能移动、抓取你看到的物品——你的观察可能过时，
规划前必须严格以状态里的物品当前位置为准（不要凭记忆），并核对每一步 move 的坐标算术（逐格数）。
可用算子（确定性，由权威仲裁器执行）：
  ["move", {"dx": ±1 或 0, "dy": ±1 或 0}]   一步移动，越界则失败
  ["grab", {"key": "item:名字"}]              必须与物品同格，且物品在世界上
  ["place", {"key": "item:名字"}]             在当前格放下持有的物品

只输出一个 JSON 对象，不要任何其他文字：
{"thought": "简短推理", "keys": ["item:xxx"], "ops": [["move", {...}], ...],
 "ttl": 5, "priority": 5,
 "belief": {"item:xxx": "world@(x,y)", "pos:你": "(x,y)"}}
ops 是从你当前位置到物品、抓取、再到投放点的完整序列（仔细核对每一步的坐标算术）；
ttl=预计占用 tick 数；priority 0-9（越急越高）。belief 填你当前相信的各 key 的值
（如实作答，格式必须与状态里的写法完全一致）。"""


class PromptFixAgent(NoProtocolAgent):
    """提示词修复基线：机制同无协议，仅强化提示词（协调感知 + 自检要求）。"""

    SYSTEM_PROMPT_OVERRIDE = FIX_PROMPT_HEAD


FIX_PROMPT_B = """你是 5x5 网格世界中的具身 agent，与另一个 agent 协作。坐标 (x,y)：x 向右，y 向下，范围 0..4。
状态 key 约定：item:{名字}=物品位置，pos:{你的ID}=你的位置，hold:{你的ID}=你持有的物品。
规则：1) move 的 dx/dy 只能是 ±1 或 0，且同一算子内不允许同时非零（斜向移动不存在，
需要先横后竖拆成多个 move）；2) 逐格核对你的移动路径（每一步写出预计到达的坐标）；
3) grab 必须与物品同格；4) 另一个 agent 可能在移动或抓取同一物品，规划时以状态为准。
只输出一个 JSON 对象，不要任何其他文字：
{"thought": "简短推理，含每步预计坐标", "keys": ["item:xxx"],
 "ops": [["move", {...}], ..., ["grab", {"key": "item:xxx"}], ..., ["place", {"key": "item:xxx"}]],
 "ttl": 5, "priority": 5,
 "belief": {"item:xxx": {"value": "world@(x,y)", "version": 3}}}
注意：ops 必须是**完整事务**——先全部 move 到物品所在格，然后 grab，再全部 move 到
投放点，最后 place。缺 grab 或 place 的计划视为无效。"""


class PromptVarBAgent(NoProtocolAgent):
    """提示词变体 B（E4b 新增）：格式约束型强化（显式禁止斜向、要求逐步坐标
    核对），与协调感知型（PromptFixAgent）对比——检验提示词基线对措辞的敏感性。"""

    SYSTEM_PROMPT_OVERRIDE = FIX_PROMPT_B


# ---------- 场景构建（复制 run_e1_full.build_sim，注入基线 cls）----------


def build_sim(baseline: str, cond: str, clients: dict, trial: int,
              horizon: int, task: str = "T1"):
    spec = TASKS[task]
    sim = Sim()
    arb = sim.arbiter
    init_world(arb, spec["items"], spec["agents"],
               width=spec["W"], height=spec["H"])

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
        if len(names) == 1:
            return f"把 item:{names[0]} 从它所在位置取起，送到 {drop} 并 place。"
        seq = "、".join(f"item:{n}" for n in names)
        return f"依次把 {seq} 各自取起并送到 {drop} place（每一件都必须送达）。"

    stuck_a = (cond == "C3")
    override_b = None
    if cond == "C4":
        px, py = spec["items"][first_item]
        shifted = f"world@({min(px + 1, spec['W'] - 1)},{py})"
        override_b = {f"item:{first_item}": shifted}

    cls = {"pessim": PessimisticLockAgent,
           "occ": OCCAgent,
           "occ_true": OCCTrueAgent,
           "promptfix": PromptFixAgent,
           "promptvarb": PromptVarBAgent}[baseline]

    def keys_for(aid: str) -> list:
        if cond == "C1" and "c1_extra" in spec:
            return ["apple1"] if aid == "A" else list(spec["c1_extra"])
        if spec.get("chain"):
            return list(spec["items"])
        return [first_item]

    a = cls(sim, "A", m_a, goal_for("A"), [f"item:{n}" for n in keys_for("A")],
            spec["agents"]["A"], stuck=stuck_a)
    b = cls(sim, "B", m_b, goal_for("B"), [f"item:{n}" for n in keys_for("B")],
            spec["agents"]["B"], belief_override=override_b)
    a.start(1)
    b.start(2)

    oracle = {"B": "deviation", "A": "ok"} if cond == "C4" else {}
    return arb, (a, b), oracle


def run_trial(baseline: str, cond: str, trial: int, clients: dict,
              horizon: int, task: str = "T1") -> dict:
    print(f"\n--- E4[{baseline}] 任务 {task} 条件 {cond} trial {trial} ---")
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    arb, agents, _oracle = build_sim(baseline, cond, clients, trial, horizon, task)
    sim = agents[0].sim
    try:
        sim.run(horizon)
    except RuntimeError as e:
        print(f"  仿真异常: {e}")
    ok, why = check_success(sim, cond, task)

    # 算子统计：兼容两种通道（协议版 "op"+lease_id；无协议版 "op_free"）
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
        d = agent_ops.setdefault(aid, {"ops": 0, "failed": 0})
        d["ops"] += 1
        if not e[5]:
            d["failed"] += 1

    # 死锁标记：有 grant 从未 release 且任务未成（悲观锁特有病征）
    leases_granted = {e[2] for e in arb.log if e[0] == "grant"}
    leases_released = {e[2] for e in arb.log if e[0] == "release"}
    deadlock = bool(leases_granted - leases_released) and not ok

    rec = {
        "baseline": baseline, "task": task, "cond": cond, "trial": trial,
        "models": {ag.id: ag.client.model for ag in agents},
        "success": ok, "fail_reason": why, "deadlock": deadlock,
        "agent_ops": agent_ops,
        "tokens": sum(ag.client.usage["prompt_tokens"] + ag.client.usage["completion_tokens"]
                      for ag in agents),
        "llm_calls": sum(ag.client.usage["calls"] for ag in agents),
        "plans": {ag.id: ag.plans for ag in agents if getattr(ag, "plans", None)},
        "op_seq": [{"agent": lease_agent.get(e[2], e[4].get("agent", "?")) if e[0] == "op"
                    else e[4].get("agent", "?"),
                    "op": e[3], "ok": e[5]}
                   for e in arb.log if e[0] in ("op", "op_free")],
    }
    print(f"  => 成功={ok}{'（' + why + '）' if not ok else ''} 死锁={deadlock} "
          f"tokens={rec['tokens']}")
    return rec


def aggregate(records: list[dict]) -> str:
    lines = ["\n================ E4 基线汇总 ================",
             f"{'基线/任务/条件':<16}{'n':>3}{'成功率':>7}{'死锁':>5}{'浪费算子':>9}{'tokens':>8}"]
    labels = sorted({f"{r['baseline']}/{r['task']}/{r['cond']}" for r in records})
    for label in labels:
        bl, task, cond = label.split("/")
        rs = [r for r in records if r["baseline"] == bl
              and r["task"] == task and r["cond"] == cond]
        n = len(rs)
        sr = sum(r["success"] for r in rs) / n
        dl = sum(r["deadlock"] for r in rs)
        waste = sum(d["failed"] for r in rs for d in r["agent_ops"].values()) / n
        toks = statistics.mean([r["tokens"] for r in rs])
        lines.append(f"{label:<16}{n:>3}{sr:>6.0%}{dl:>5}{waste:>9.1f}{toks:>8.0f}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True,
                    choices=["pessim", "occ", "occ_true", "promptfix", "promptvarb"])
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--cond", type=str, default="C2")
    ap.add_argument("--task", type=str, default="T1", choices=list(TASKS))
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    clients = load_clients()
    horizon = TASKS[args.task]["horizon"]
    records = [run_trial(args.baseline, args.cond, t, clients, horizon, args.task)
               for t in range(args.trials)]

    print(aggregate(records))
    out = args.out or f"e4_{args.baseline}_{args.task}_{args.cond}.json"
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), out)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print(f"\n原始记录已存 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
