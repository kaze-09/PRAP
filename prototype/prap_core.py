"""PRAP 仲裁器核心 —— PRAP_protocol_spec.md v0.1 的最小可运行实现。

覆盖层：
  L0 权威状态层   版本化状态 + 可重放写日志（WAL redo）
  L1 租约层       意图 + TTL 租约，过期自动释放（无 undo）
  L2 仲裁层       确定性全序 (epoch, seq) + 确定性 tie-break + 老化防饥饿
  L3 和解层       DENY_WITH_STATE：拒绝时附带权威状态差量
  L4 信念校验层   版本号比较式故障二分类（时序型 / 偏差型），不依赖 LLM
  L5 订阅决策层   SUBSCRIBE / STATE_UPDATE 推送管道

确定性保证：全序 (epoch, seq)、确定性 tie-break、确定性操作算子、无随机源。
时间模型：虚拟整数时钟（tick），由外部仿真驱动，不使用真实时间。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

TTL_MIN = 2        # 最小租约时长（tick），见规范 §8.1 自适应问题，先取常量
AGING_UNIT = 10    # 每等待 10 tick，有效优先级 +1
PRIORITY_CAP = 9


def digest(value: Any) -> str:
    """状态值摘要（sha256 全长 64 hex = 256 bit，消除截断碰撞——L4 分类正确性依赖它）。"""
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


@dataclass
class StateEntry:
    value: Any
    version: int = 0
    last_writer: str = "-"
    last_change_seq: int = 0


@dataclass
class Lease:
    lease_id: str
    agent_id: str
    keys: tuple
    ttl_end: int
    created: int
    priority: int
    hold_total: int = 0


@dataclass
class PendingIntent:
    """被拒绝后停留在仲裁器等待队列的意图（§5.2 租约状态机 FREE->HELD 的候补）。"""
    intent_seq: int
    agent_id: str
    keys: tuple
    ops: tuple
    ttl: int
    priority: int
    wait_since: int

    def effective_priority(self) -> int:
        return min(self.priority + self.wait_since // AGING_UNIT, PRIORITY_CAP)

    def sort_key(self):
        # §3.3 确定性 tie-break：有效优先级(降) -> 等待时长(升) -> agent_id(字典序) -> seq(升)
        return (-self.effective_priority(), self.wait_since, self.agent_id, self.intent_seq)


@dataclass
class Msg:
    type: str
    to: str
    seq: int
    lease_id: Optional[str] = None
    keys: tuple = ()
    state_diff: tuple = ()
    ttl: int = 0
    queue_pos: int = -1
    verdict: Optional[dict] = None
    payload: Any = None


class Arbiter:
    def __init__(self, ops_registry: dict[str, Callable]):
        self.epoch = 1
        self.seq = 0
        self.now = 0
        self.state: dict[str, StateEntry] = {}
        self.log: list[tuple] = []          # WAL：重放 write 条目即可重建状态
        self.leases: dict[str, Lease] = {}
        self.key_holder: dict[str, str] = {}   # key -> lease_id
        self.waiting: list[PendingIntent] = []
        self.subs: dict[str, set] = {}      # L5：agent -> 订阅 key 集
        self.outbox: list[tuple[int, int, Msg]] = []  # (deliver_at, seq, msg)
        self.ops_registry = ops_registry
        self._lease_counter = 0
        self.max_hold: Optional[int] = None   # v0.2.2：累计持有时长上界（tick），None = 不限
        # v0.2：信念隔离。agent -> "ok" | "suspect"（曾报 deviation 且未以正确信念清除）
        self.agent_status: dict[str, str] = {}
        self.stats: dict[str, Any] = {"grants": 0, "denies": 0, "expires": 0,
                                      "commits": 0, "renews": 0,
                                      "commits_rejected": 0, "msgs": {}}

    # ---------- 内部工具 ----------

    def _next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def _send(self, msg: Msg, delay: int = 0) -> Msg:
        self.outbox.append((self.now + delay, self._next_seq(), msg))
        self.stats["msgs"][msg.type] = self.stats["msgs"].get(msg.type, 0) + 1
        return msg

    def _write(self, key: str, value: Any, writer: str, lease_id: Optional[str]) -> None:
        seq = self._next_seq()
        entry = self.state.get(key) or StateEntry(value=None)
        entry.value = value
        entry.version += 1
        entry.last_writer = writer
        entry.last_change_seq = seq
        self.state[key] = entry
        self.log.append(("write", seq, lease_id or "-", key, value, entry.version))
        # L5：向订阅者推送状态更新（不含写入者自身）
        for agent_id, keys in self.subs.items():
            if key in keys and agent_id != writer:
                self._send(Msg("STATE_UPDATE", agent_id, seq, keys=(key,),
                               payload=(entry.version, digest(value))))

    def state_diff(self, keys) -> tuple:
        """L3：给定 key 集合的权威状态差量 (key, (version, digest, holder))。"""
        return tuple(
            (k, (self.state[k].version, digest(self.state[k].value), self.key_holder.get(k)))
            for k in keys if k in self.state
        )

    # ---------- 协议入口（规范 §4 消息定义） ----------

    def _set_status(self, agent_id: str, verdict_class: str) -> None:
        """v0.2：deviation -> 隔离（suspect）；ok/stale -> 解除。"""
        if verdict_class == "deviation":
            self.agent_status[agent_id] = "suspect"
        elif verdict_class in ("ok", "stale"):
            self.agent_status[agent_id] = "ok"

    def submit_intent(self, agent_id: str, keys, ops=(), ttl: int = 5,
                      priority: int = 5, belief: Optional[list] = None) -> None:
        """INTENT：原子授予 keys（all-or-nothing，§3.2 消除 hold-and-wait）。

        v0.2.2 信念隔离强化：suspect 状态的 agent **不授予、不入等待队列**
        （否则它可持满一个 TTL 甚至靠续约架空隔离）。解除路径不变：重新
        观察，以正确信念 INTENT（本调用内 classify 判 ok/stale 即解除并正常授予）。
        """
        keys = tuple(keys)
        ttl = max(int(ttl), TTL_MIN)
        iseq = self._next_seq()
        self.log.append(("intent", iseq, agent_id, keys, priority))
        verdict = self._classify(belief) if belief else None
        if verdict:
            self._set_status(agent_id, verdict["class"])   # v0.2：信念隔离判定
        if self.agent_status.get(agent_id) == "suspect":
            # 隔离期间：不授予、不入队（解除后须重新 INTENT）
            self.stats["denies"] += 1
            self.log.append(("intent_suspect_blocked", iseq, agent_id, keys))
            self._send(Msg("DENY_WITH_STATE", agent_id, iseq, keys=keys,
                           state_diff=self.state_diff(keys),
                           verdict={"class": "deviation",
                                    "evidence": [("suspect_blocked",
                                                  "reobserve_and_reintent_with_belief")]}))
            return
        if all(k not in self.key_holder for k in keys):
            self._grant(agent_id, keys, tuple(ops), ttl, priority, verdict, iseq)
            return
        # 拒绝 + 停靠等待队列 + DENY_WITH_STATE
        self.stats["denies"] += 1
        self.waiting.append(PendingIntent(iseq, agent_id, keys, tuple(ops),
                                          ttl, priority, self.now))
        msg = Msg("DENY_WITH_STATE", agent_id, iseq, keys=keys,
                  state_diff=self.state_diff(keys), queue_pos=len(self.waiting),
                  verdict=verdict)   # L4 随和解消息一并返回
        self._send(msg)

    def _grant(self, agent_id, keys, ops, ttl, priority, verdict, iseq) -> None:
        self._lease_counter += 1
        lease_id = f"L{self.epoch}.{self._lease_counter}"
        self.leases[lease_id] = Lease(lease_id, agent_id, keys,
                                      self.now + ttl, self.now, priority)
        for k in keys:
            self.key_holder[k] = lease_id
        self.stats["grants"] += 1
        self.log.append(("grant", self._next_seq(), lease_id, agent_id, keys, self.now + ttl))
        msg = Msg("GRANT", agent_id, iseq, lease_id=lease_id, keys=keys, ttl=ttl,
                  verdict=verdict)
        self._send(msg)

    def renew(self, lease_id: str, ext: int) -> None:
        """RENEW：续约；对已过期租约无效（返回 EXPIRED）。

        v0.2.2 两道闸门：
          1. suspect agent 拒绝续约（关闭"隔离被续约架空"漏洞；
             其在途租约由 TTL 兜底，不强夺——避免误杀合法在途事务）；
          2. max_hold 累计持有时长上界（None = 不限），超限拒绝续约
             ——活性兜底：任何 agent 不能靠续约无限期占用资源。
        """
        lease = self.leases.get(lease_id)
        if lease is None or lease.ttl_end <= self.now:
            self._send(Msg("EXPIRED", lease.agent_id if lease else "?",
                           self._next_seq(), lease_id=lease_id))
            return
        if self.agent_status.get(lease.agent_id) == "suspect":
            self.log.append(("renew_refused_suspect", self._next_seq(), lease_id,
                             lease.agent_id))
            self._send(Msg("RENEW_REFUSED", lease.agent_id, self._next_seq(),
                           lease_id=lease_id,
                           verdict={"reason": "suspect_agent"}))
            return
        if self.max_hold is not None and lease.hold_total + ext > self.max_hold:
            self.log.append(("renew_refused_maxhold", self._next_seq(), lease_id,
                             lease.hold_total + ext))
            self._send(Msg("RENEW_REFUSED", lease.agent_id, self._next_seq(),
                           lease_id=lease_id,
                           verdict={"reason": "max_hold_exceeded"}))
            return
        lease.ttl_end += ext
        lease.hold_total += ext
        self.stats["renews"] += 1
        self.log.append(("renew", self._next_seq(), lease_id, lease.ttl_end))
        self._send(Msg("GRANT", lease.agent_id, self._next_seq(), lease_id=lease_id, ttl=ext))

    def release(self, lease_id: str) -> None:
        """RELEASE：主动释放，立即按全序唤醒等待者。"""
        lease = self.leases.pop(lease_id, None)
        if lease is None:
            return
        for k in lease.keys:
            self.key_holder.pop(k, None)
        self.log.append(("release", self._next_seq(), lease_id))
        self._try_grant_waiting()

    def commit(self, lease_id: str, ops) -> None:
        """COMMIT：在有效租约内执行确定性操作序列（L0 唯一写路径）。

        v0.2 信念隔离：suspect 状态的 agent 一律拒绝提交，并强制收回其租约
        （资源立即让给健康 agent）——持续幻觉 agent 被隔离，无法破坏
        他人已完成的产出。解除条件：重新以正确信念 INTENT（判 ok/stale）。
        """
        lease = self.leases.get(lease_id)
        if lease is None or lease.ttl_end <= self.now:
            if lease is not None:
                self._send(Msg("EXPIRED", lease.agent_id, self._next_seq(), lease_id=lease_id))
            return
        if self.agent_status.get(lease.agent_id) == "suspect":
            self.stats["commits_rejected"] += 1
            self.log.append(("commit_rejected", self._next_seq(), lease_id, lease.agent_id))
            self._send(Msg("COMMIT_REJECTED", lease.agent_id, self._next_seq(),
                           lease_id=lease_id,
                           verdict={"class": "deviation",
                                    "evidence": [("commit_refused", "suspect_agent")]}))
            self.release(lease_id)   # 强制收回，唤醒等待队列
            return
        for name, args in ops:
            handler = self.ops_registry.get(name)
            ok, detail = handler(self, lease, args) if handler else (False, "unknown_op")
            self.log.append(("op", self._next_seq(), lease_id, name, args, ok, detail))
        self.stats["commits"] += 1

    def subscribe(self, agent_id: str, keys, decision: Optional[dict] = None) -> None:
        """L5：订阅（decision 记录 p_hat/代价参数，供校准曲线用）。"""
        self.subs.setdefault(agent_id, set()).update(keys)
        self.log.append(("subscribe", self._next_seq(), agent_id, tuple(keys), decision or {}))

    def unsubscribe(self, agent_id: str, keys) -> None:
        s = self.subs.get(agent_id)
        if s:
            s.difference_update(keys)

    # ---------- L4 信念校验（§3.5，纯版本号比较，无 LLM） ----------

    def _classify(self, belief) -> dict:
        verdict = {"class": "ok", "evidence": []}
        for key, bver, bdig in belief:
            entry = self.state.get(key)
            if entry is None:
                verdict = {"class": "deviation",
                           "evidence": [(key, "key_absent_in_authority")]}
                break
            if bver > entry.version or (bver == entry.version and bdig != digest(entry.value)):
                verdict = {"class": "deviation",
                           "evidence": [(key, f"belief_v{bver} vs authority_v{entry.version}")]}
                break
            if bver < entry.version:
                verdict = {"class": "stale",
                           "evidence": [(key, f"belief_v{bver} < authority_v{entry.version}")]}
        return verdict

    # ---------- L2 等待队列仲裁 ----------

    def _try_grant_waiting(self) -> None:
        if not self.waiting:
            return
        self.waiting.sort(key=PendingIntent.sort_key)
        remaining = []
        for p in self.waiting:
            if self.agent_status.get(p.agent_id) == "suspect":
                remaining.append(p)   # v0.2.2：隔离者不授予（留在队列，解除后可复活）
                continue
            if all(k not in self.key_holder for k in p.keys):
                self._grant(p.agent_id, p.keys, p.ops, p.ttl, p.priority, None, p.intent_seq)
            else:
                remaining.append(p)
        self.waiting = remaining

    # ---------- 时钟推进 ----------

    def tick(self, now: int) -> None:
        """L1：TTL 到期自动释放（无 undo），随后按全序唤醒等待者。"""
        self.now = now
        expired = [lid for lid, l in self.leases.items() if l.ttl_end <= now]
        for lid in expired:
            lease = self.leases.pop(lid)
            for k in lease.keys:
                self.key_holder.pop(k, None)
            self.stats["expires"] += 1
            self.log.append(("expire", self._next_seq(), lid))
            self._send(Msg("EXPIRED", lease.agent_id, self._next_seq(), lease_id=lid))
        self._try_grant_waiting()

    # ---------- 重放 ----------

    def replay_writes(self) -> dict:
        """从日志 redo 重建 (key -> (value, version))，用于确定性验证（§6.4）。"""
        st: dict[str, tuple] = {}
        for e in self.log:
            if e[0] == "write":
                _, seq, lease_id, key, value, ver = e
                st[key] = (value, ver)
        return st
