"""网格测试床：确定性操作算子 + 脚本化 agent（薄 SDK）+ 离散事件仿真。

- 世界：5x5 网格；状态 key：item:{name} / pos:{agent} / hold:{agent}
- 算子集（规范 §8.4 最小确定性算子）：move / grab / place
- agent 为脚本策略（不调用真实 LLM），think_time 模拟 LLM 推理延迟；
  stuck=True 模拟 LLM agent 挂死（持租约后不再续约/提交/释放）。
- 无随机源：相同场景脚本 -> 相同日志与终态（§6.4 可复现性）。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Callable, Optional

from prap_core import Arbiter, Msg, digest

W, H = 5, 5


def fmt_pos(p) -> str:
    return f"({p[0]},{p[1]})"


def parse_pos(s: str):
    x, y = s.strip("()").split(",")
    return int(x), int(y)


# ---------- 确定性算子（由仲裁器在 COMMIT 中调用，签名 fn(arb, lease, args)） ----------

def op_move(arb: Arbiter, lease, args) -> tuple:
    agent, dx, dy = args["agent"], args["dx"], args["dy"]
    pos = parse_pos(arb.state[f"pos:{agent}"].value)
    nx, ny = pos[0] + dx, pos[1] + dy
    w = getattr(arb, "width", W)
    h = getattr(arb, "height", H)
    if not (0 <= nx < w and 0 <= ny < h):
        return False, "out_of_bounds"
    arb._write(f"pos:{agent}", fmt_pos((nx, ny)), agent, lease.lease_id)
    return True, fmt_pos((nx, ny))


def op_grab(arb: Arbiter, lease, args) -> tuple:
    agent, key = args["agent"], args["key"]
    entry = arb.state.get(key)
    pos = arb.state[f"pos:{agent}"].value
    if entry is None or not entry.value.startswith("world@"):
        return False, "item_not_in_world"
    if entry.value[6:] != pos:
        return False, f"agent_not_at_item({entry.value[6:]} != {pos})"
    arb._write(key, agent, agent, lease.lease_id)
    arb._write(f"hold:{agent}", key, agent, lease.lease_id)
    return True, "grabbed"


def op_place(arb: Arbiter, lease, args) -> tuple:
    agent, key = args["agent"], args["key"]
    hold = arb.state[f"hold:{agent}"].value
    if hold != key:
        return False, f"not_holding({hold})"
    pos = arb.state[f"pos:{agent}"].value
    arb._write(key, f"world@{pos}", agent, lease.lease_id)
    arb._write(f"hold:{agent}", "none", agent, lease.lease_id)
    return True, f"placed@{pos}"


OPS_REGISTRY: dict[str, Callable] = {"move": op_move, "grab": op_grab, "place": op_place}


def init_world(arb: Arbiter, items: dict, agents: dict,
               width: int = W, height: int = H) -> None:
    arb.width, arb.height = width, height
    for name, (x, y) in items.items():
        arb._write(f"item:{name}", f"world@({x},{y})", "init", None)
    for a, (x, y) in agents.items():
        arb._write(f"pos:{a}", fmt_pos((x, y)), "init", None)
        arb._write(f"hold:{a}", "none", "init", None)


def fetch_ops(agent: str, start, item_key: str, item_pos, drop) -> list:
    """取物并送达的确定性操作序列：move 到物品 -> grab -> move 到投放点 -> place。"""
    ops: list = []

    def moves(frm, to):
        dx, dy = to[0] - frm[0], to[1] - frm[1]
        for _ in range(abs(dx)):
            ops.append(("move", {"agent": agent, "dx": 1 if dx > 0 else -1, "dy": 0}))
        for _ in range(abs(dy)):
            ops.append(("move", {"agent": agent, "dx": 0, "dy": 1 if dy > 0 else -1}))

    moves(start, item_pos)
    ops.append(("grab", {"agent": agent, "key": item_key}))
    moves(item_pos, drop)
    ops.append(("place", {"agent": agent, "key": item_key}))
    return ops


# ---------- 离散事件仿真 ----------

class Sim:
    def __init__(self):
        self.arbiter = Arbiter(OPS_REGISTRY)
        self.agents: dict[str, "ScriptedAgent"] = {}
        self.events: list = []
        self._counter = 0
        self.now = 0

    def schedule(self, t: int, fn: Callable) -> None:
        self._counter += 1
        heapq.heappush(self.events, (t, self._counter, fn))

    def run(self, until: int) -> None:
        # 逐 tick 推进：即使事件队列变空（如 agent 挂死）也要让虚拟时钟前进，
        # 否则 TTL 永不到期、等待者永远无法接管（这正是协议要打破的情形）。
        for t in range(0, until + 1):
            self.now = t
            self.arbiter.tick(t)      # 1) 到期释放 + 等待队列授予
            self._deliver(t)          # 2) 投递到期消息
            while self.events and self.events[0][0] == t:
                _, _, fn = heapq.heappop(self.events)
                fn()                  # 3) 执行 agent 定时动作
                self._deliver(t)      # 4) 投递动作产生的响应（同一 tick）
        if self.events:
            raise RuntimeError("仿真在截止时刻前仍有未决事件（可能死锁）")

    def _deliver(self, t: int) -> None:
        due = [o for o in self.arbiter.outbox if o[0] <= t]
        self.arbiter.outbox = [o for o in self.arbiter.outbox if o[0] > t]
        for _, _, msg in sorted(due, key=lambda o: o[1]):
            self.agents[msg.to].on_msg(msg)


# ---------- 脚本化 agent（薄 SDK） ----------

@dataclass
class AgentReport:
    agent_id: str
    granted_at: Optional[int] = None
    blocked_from: Optional[int] = None
    completed_at: Optional[int] = None
    verdict: Optional[str] = None
    expiry_notifications: int = 0
    state_updates: int = 0
    renewed: bool = False
    commit_rejections: int = 0   # v0.2：被信念隔离拦截的提交次数


class ScriptedAgent:
    """事务模型：INTENT(keys) -> GRANT -> think -> COMMIT(ops) + RELEASE。

    stuck=True 时不续约、不提交、不释放 —— 模拟 LLM agent 挂死（§2 故障模型）。
    """

    def __init__(self, sim: Sim, agent_id: str, keys, ops: list,
                 ttl: int = 5, priority: int = 5, think: int = 2,
                 belief: Optional[list] = None, stuck: bool = False):
        self.sim = sim
        self.id = agent_id
        self.keys = tuple(keys)
        self.ops = ops
        self.ttl = ttl
        self.priority = priority
        self.think = think
        self.belief = belief
        self.stuck = stuck
        self.lease_id: Optional[str] = None
        self._committed = False
        self.report = AgentReport(agent_id)
        sim.agents[agent_id] = self

    def start(self, t: int) -> None:
        self.sim.schedule(t, self._submit)

    def _submit(self) -> None:
        self.sim.arbiter.submit_intent(self.id, self.keys, ops=self.ops,
                                       ttl=self.ttl, priority=self.priority,
                                       belief=self.belief)

    def on_msg(self, msg: Msg) -> None:
        r = self.report
        if msg.type == "DENY_WITH_STATE":
            if r.blocked_from is None:
                r.blocked_from = self.sim.now
            if msg.verdict:
                r.verdict = msg.verdict["class"]
        elif msg.type == "GRANT":
            if r.granted_at is None:
                r.granted_at = self.sim.now
                if msg.verdict:
                    r.verdict = msg.verdict["class"]
            is_renew = (msg.lease_id == self.lease_id)
            if msg.lease_id and not is_renew:
                self.lease_id = msg.lease_id
                if not self.stuck:
                    self.sim.schedule(self.sim.now + self.think, self._commit)
        elif msg.type == "EXPIRED":
            r.expiry_notifications += 1
        elif msg.type == "STATE_UPDATE":
            r.state_updates += 1
        elif msg.type == "COMMIT_REJECTED":
            r.commit_rejections += 1   # v0.2 信念隔离拦截

    def _commit(self) -> None:
        if self.stuck or self.lease_id is None:
            return
        self._committed = True
        self.sim.arbiter.commit(self.lease_id, self.ops)
        self.sim.arbiter.release(self.lease_id)
        self.report.completed_at = self.sim.now

    def renew_at(self, t: int, ext: int) -> None:
        """在时刻 t 续约一次（E1 场景 1b 用）。"""
        def _do():
            if self.lease_id and not self._committed:
                self.sim.arbiter.renew(self.lease_id, ext)
                self.report.renewed = True
        self.sim.schedule(t, _do)
