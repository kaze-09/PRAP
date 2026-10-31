"""E6 竞品对比基线（机制级复现，方法论约定见 briefing §四：忠实复现/公允调参/
差异披露/措辞不越界）。

四个基线 agent，全部基于 NoProtocolAgent 的"LLM 规划 + 直接执行"骨架，
差异只在协调机制——与 PRAP（协议层）形成路线级对比：

  coagent   CoAgent 式 OCC+undo：乐观执行 + 逐算子 undo 补偿 + 冲突通知自修复
  orch      中央编排：orchestrator LLM 分工，worker 执行，失败重分配
  cnp       合同网：招标-投标-中标（确定性距离投标），承包方 LLM 执行
  groupchat 群聊协商：结构化对话 2 轮，一致按约定分工，不一致各自执行

忠实复现要点（差异披露三栏，正式版入论文附录）：
  - 保留：各竞品的核心协调机制与失败处理语义（乐观执行/undo/招标投标/
    对话协商/中央分派）；
  - 适配：执行层用本测试床算子（op_free 通道）；agent 间消息经 Msg.payload
    （虚拟时钟，延迟 0-2 tick）；冲突显现 = 算子失败（本环境中等价于
    CoAgent 的写冲突检测）；
  - 未实现：CoAgent 中间件调度细节、CNP 多轮招标优化、群聊多 topic 管理。

投标口径（公允性说明）：CNP 投标 = 曼哈顿距离确定性估价（教科书口径，零 LLM
噪声），承包方的执行规划仍用与 PRAP 相同的 LLM 与提示词——机制差异不含
规划质量差异。群聊每 agent 每局 1-2 次 LLM 调用（计入 token 对比）。
"""

from __future__ import annotations

import json

from llm_agent import NoProtocolAgent
from prap_core import Lease, Msg
from testbed import parse_pos


# ---------- 共用工具 ----------

def agent_send(sim, frm: str, to: str, mtype: str, payload: dict) -> None:
    """agent 间消息：经仲裁器 outbox 投递（复用 Msg.payload 信道）。"""
    arb = sim.arbiter
    arb._send(Msg(type=mtype, to=to, seq=arb._next_seq(),
                  payload={"from": frm, **payload}))


def _free_op(agent, op: tuple) -> tuple:
    """无协议直执行一个算子（OCC 系基线共用）。"""
    arb = agent.sim.arbiter
    lease = Lease("free", agent.id, (), 10 ** 9, 0, 0)
    handler = arb.ops_registry[op[0]]
    ok, detail = handler(arb, lease, op[1])
    arb.log.append(("op_free", arb._next_seq(), "free", op[0], op[1], ok, detail))
    return ok, detail


def _manhattan(a, b) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _item_pos_from_value(value: str):
    """'world@(x,y)' -> (x, y)；被持有（agent id）返回 None。"""
    if not value.startswith("world@"):
        return None
    return parse_pos(value[6:])


class _RetryExecMixin:
    """OCC 系执行骨架：计划 -> 逐 tick 直执行；失败即中止剩余并重规划。"""

    MAX_RETRIES = 2

    def _init_retry(self):
        self.retries_left = self.MAX_RETRIES
        self._aborted = False

    def _start_work(self, note: str | None = None):
        self._ever_worked = True
        self._aborted = False
        plan = self._plan(self._observe(), extra_note=note)
        self.ops = plan["ops"]
        self.keys = plan["keys"]
        for i, op in enumerate(self.ops):
            self.sim.schedule(self.sim.now + 2 + i, lambda op=op: self._exec_op(op))

    def _exec_op(self, op: tuple) -> None:
        if self._aborted:
            return
        ok, detail = _free_op(self, op)
        if ok:
            return
        self._aborted = True
        self._on_op_failed(op, detail)

    def _on_op_failed(self, op: tuple, detail: str) -> None:
        raise NotImplementedError

    def _plan(self, obs: dict, extra_note: str | None = None) -> dict:
        """把系统通知（冲突/分配）注入任务文本（LLMAgent._plan 的 goal 进 prompt）。"""
        if not extra_note:
            return super()._plan(obs)
        old, self.goal = self.goal, f"{self.goal}（系统通知：{extra_note}）"
        try:
            return super()._plan(obs)
        finally:
            self.goal = old


# ---------- 1. CoAgent 式 OCC + undo + 自修复 ----------

class CoAgentUndoAgent(_RetryExecMixin, NoProtocolAgent):
    """CoAgent 机制级复现：乐观执行，冲突时 undo 已执行算子再通知 agent 自修复。

    与 E4b 真 OCC 的区别：真 OCC 在首算子前验证（零部分执行）；CoAgent 乐观
    执行、靠预注册 undo 撤销错序写入——本复现按 CoAgent 语义：逐算子执行并
    记 undo 栈，冲突（算子失败）时逆序补偿，然后带冲突通知重新规划（自修复）。
    undo 互逆对：move(dx,dy)->move(-dx,-dy)；grab(key)<->place(key)。
    undo 本身可能失败（补偿不可逆操作）——如实计数（CoAgent 已知局限）。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._init_retry()
        self._undo_stack: list = []
        self.conflicts = 0
        self.undone = 0
        self.undo_failed = 0

    def _submit(self) -> None:
        self._undo_stack = []
        self._start_work()

    def _exec_op(self, op: tuple) -> None:
        if self._aborted:
            return
        ok, detail = _free_op(self, op)
        if ok:
            self._undo_stack.append(op)
            return
        # 冲突显现 -> 中止剩余 + undo 补偿 + 冲突通知自修复（CoAgent 语义）
        self._aborted = True
        self.conflicts += 1
        undone = failed = 0
        for uop in reversed(self._undo_stack):
            u = self._undo_of(uop)
            if u is None:
                continue
            uok, _ = _free_op(self, u)
            if uok:
                undone += 1
            else:
                failed += 1
        self.undone += undone
        self.undo_failed += failed
        print(f"  [{self.id}] CoAgent 冲突（{op[0]} 失败: {detail}）-> "
              f"undo {undone} 算子（{failed} 失败）-> 通知自修复")
        if self.retries_left > 0:
            self.retries_left -= 1
            note = (f"你上次计划在执行 {op[0]} 时冲突（{detail}），已撤销 "
                    f"{undone} 个已执行算子，请基于最新观察重新规划")
            self.sim.schedule(self.sim.now + 1, lambda: self._start_work(note))

    @staticmethod
    def _undo_of(op: tuple):
        name, args = op
        a = dict(args)
        if name == "move":
            return ("move", {"agent": a["agent"], "dx": -a["dx"], "dy": -a["dy"]})
        if name == "grab":
            return ("place", {"agent": a["agent"], "key": a["key"]})
        if name == "place":
            return ("grab", {"agent": a["agent"], "key": a["key"]})
        return None


# ---------- 2. 中央编排（orchestrator-worker） ----------

ORCH_SYS = """你是多 agent 系统的编排者（orchestrator）。你负责把任务物品分配给两个
worker（A、B），目标：总工作量最小、避免争抢同一物品。只输出一个 JSON 对象：
{"assignments": {"item:xxx": "A" 或 "B", ...}, "reason": "简短理由"}
每个任务物品必须恰好分给一个 worker，不得两人同做一件。"""


class OrchestratorAgent(_RetryExecMixin, NoProtocolAgent):
    """中央编排基线：A 兼任 orchestrator（看全局状态做一次 LLM 分工），
    A/B 各自执行分到的物品；worker 重试耗尽 -> 上报 -> 重分配给另一 worker。

    适配说明：重分配采用确定性"转交另一 worker"（2 worker 1 物品场景下
    这是唯一合理动作，LLM 重分配调用不增加信息）；编排者失败重分配保留。
    """

    def __init__(self, *a, is_orchestrator: bool = False, **kw):
        super().__init__(*a, **kw)
        self._init_retry()
        self.is_orch = is_orchestrator
        self.assigned: list[str] = []     # 分给我的 item keys
        self._assign_done = False
        self._ever_worked = False
        self.reassignments = 0
        self._failed_items: set = set()
        self._failed_workers: set = set()   # 重分配依据：失败过的 worker

    # -- orchestrator 侧 --

    def _assign(self) -> None:
        obs = self._observe()
        user = (f"你的ID: {self.id}（你是编排者）\n两个 worker: A、B\n"
                f"任务总目标: {self.goal}\n"
                f"世界状态: {json.dumps(obs, ensure_ascii=False)}\n"
                f"需要分配的物品: {json.dumps(self.item_keys)}\n输出 JSON 分配。")
        plan = None
        for _ in range(2):
            plan = self.client.extract_json(self.client.chat(ORCH_SYS, user))
            if plan and isinstance(plan.get("assignments"), dict):
                break
        if plan is None:
            plan = {"assignments": {k: "A" for k in self.item_keys},
                    "reason": "解析失败，退化为编排者自做"}
        self._assign_done = True
        mine, others = [], {}
        for k, w in plan["assignments"].items():
            if w == self.id or w not in ("A", "B"):
                mine.append(k)
            else:
                others[k] = w
        print(f"  [orch] 分配: {plan['assignments']} | {plan.get('reason', '')[:50]}")
        if others:
            agent_send(self.sim, self.id,
                       next(w for w in ("A", "B") if w != self.id),
                       "ORCH_ASSIGN", {"assignments": others})
        if mine:
            self.assigned = mine
            self.sim.schedule(self.sim.now + 1, self._start_work)

    # -- worker 侧 --

    def _submit(self) -> None:
        if self.is_orch and not self._assign_done:
            self._assign()
            return
        if self._ever_worked:
            return   # 已在执行/失败上报流程中（防 ORCH_ASSIGN 与 _submit 双触发）
        if self.assigned:
            self._start_work()
        elif self.sim.now < 10:
            # 等待编排分配（分配消息应在更早 tick 送达；再等 1 tick 兜底）
            self.sim.schedule(self.sim.now + 1, self._submit)
        else:
            print(f"  [{self.id}] 编排等待超时且未分到任务 -> 待命")

    def _on_op_failed(self, op: tuple, detail: str) -> None:
        print(f"  [{self.id}] orch worker 算子失败（{op[0]}: {detail}）-> 重规划")
        if self.retries_left > 0:
            self.retries_left -= 1
            note = f"你上次计划在 {op[0]} 失败（{detail}），基于最新观察重试"
            self.sim.schedule(self.sim.now + 1, lambda: self._start_work(note))
        else:
            item = self.assigned[0] if self.assigned else op[1].get("key", "")
            self._failed_items.add(item)
            if self.is_orch:
                self._reassign(item, failed=self.id)
            else:
                # 上报给编排者（动态查找，兼容规模版的 A0 等命名）
                orch = next((a for a in self.sim.agents.values()
                             if getattr(a, "is_orch", False)), None)
                if orch is not None:
                    agent_send(self.sim, self.id, orch.id, "ORCH_FAIL",
                               {"item": item, "detail": detail})
                    print(f"  [{self.id}] 重试耗尽 -> 上报编排者 {orch.id}（{item}）")

    def _reassign(self, item: str, failed: str) -> None:
        """重分配给未失败过的 worker（全部失败过则放弃，任务失败）。"""
        self._failed_workers.add(failed)
        candidates = [w for w in ("A", "B") if w not in self._failed_workers]
        if not candidates:
            print(f"  [orch] {item}: 两个 worker 均失败过 -> 放弃重分配")
            return
        other = candidates[0]
        self.reassignments += 1
        print(f"  [orch] {item} 失败（{failed}）-> 重分配给 {other}")
        if other == self.id:
            self.assigned = [item]
            self.sim.schedule(self.sim.now + 1, self._start_work)
        else:
            agent_send(self.sim, self.id, other, "ORCH_ASSIGN",
                       {"assignments": {item: other}})

    # -- 消息 --

    def on_msg(self, msg) -> None:
        p = getattr(msg, "payload", None) or {}
        if msg.type == "ORCH_ASSIGN":
            self.assigned = [k for k, w in p.get("assignments", {}).items()
                             if w == self.id]
            print(f"  [{self.id}] 收到编排分配: {self.assigned}")
            # 初始分配（t=1 投递）不在此调度（worker 自身 _submit 会启动，防双触发）；
            # 之后的重分配直接调度（含从未工作过的闲置 worker）。
            if self.assigned and self.sim.now > 1:
                self.sim.schedule(self.sim.now + 1, self._start_work)
        elif msg.type == "ORCH_FAIL" and self.is_orch:
            item = p.get("item", "")
            fw = p.get("from", "B")
            if fw not in self._failed_workers:
                self._reassign(item, failed=fw)


# ---------- 3. 合同网（CNP） ----------

class ContractNetAgent(_RetryExecMixin, NoProtocolAgent):
    """合同网基线：招标(ANNOUNCE)-投标(BID)-中标(AWARD)；确定性距离投标。

    经典 CNP 无故障恢复——本复现加两条工程化扩展（差异披露）：
      a) 败者轮询：物品状态 2 个轮询周期无变化 -> 判承包方停滞 -> 重新招标；
      b) 承包方重试耗尽 -> 发 CNP_FAIL -> 管理方立即重新招标。
    同时宣布时以 agent_id 字典序定唯一管理者（确定性 tie-break）。
    """

    POLL_EVERY = 10

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._init_retry()
        self.contracts: dict[str, str] = {}   # item -> winner
        self._bids: dict[str, dict] = {}      # item -> {agent: cost}
        self._announce_pos: dict[str, str] = {}
        self._my_announces: set = set()
        self._seen_announce: set = set()      # 已见他人招标（不重复招标）
        self._poll_state: dict[str, tuple] = {}  # item -> (version, value)
        self._poll_count: dict[str, int] = {}
        self._failed_on: set = set()          # 承包失败过的 item（拒标依据）
        self.tenders = 0

    # -- 观察/估价 --

    def _my_pos(self):
        return parse_pos(self.sim.arbiter.state[f"pos:{self.id}"].value)

    def _believed_item_pos(self, item: str):
        v = self.sim.arbiter.state[item].value
        override = self.belief_override.get(item)
        if override:
            return _item_pos_from_value(override), override
        return _item_pos_from_value(v), v

    def _bid_cost(self, item: str, announced_value: str) -> int:
        ipos = _item_pos_from_value(announced_value)
        return _manhattan(self._my_pos(), ipos) if ipos else 99

    # -- 招标流程 --

    def _submit(self) -> None:
        todo = [k for k in self.item_keys
                if k not in self.contracts and k not in self._my_announces
                and k not in self._seen_announce]
        if not todo:
            return
        for item in todo:
            self._tender(item)

    def _tender(self, item: str) -> None:
        """招标：ANNOUNCE 给其他 agent，+2 tick 后开标。"""
        obs = self._observe()
        announced = obs.get(item, {}).get("value") or \
            self.sim.arbiter.state[item].value
        self._my_announces.add(item)
        # 每轮招标重开标书池（旧轮投标不带入——拒标/失败者的旧标书必须作废）
        self._bids[item] = {self.id: self._bid_cost(item, announced)}
        self._announce_pos[item] = announced
        self.tenders += 1
        others = [aid for aid in ("A", "B") if aid != self.id]
        for o in others:
            agent_send(self.sim, self.id, o, "CNP_ANNOUNCE",
                       {"item": item, "pos": announced})
        print(f"  [{self.id}] CNP 招标 {item} @{announced}（己方估价 "
              f"{self._bids[item][self.id]}）")
        self.sim.schedule(self.sim.now + 2, lambda: self._award(item))

    def _award(self, item: str) -> None:
        if item not in self._my_announces:
            return
        bids = self._bids.get(item, {})
        if not bids:
            # 无人投标（对端拒标）-> 自己做
            winner = self.id
        else:
            winner = min(bids, key=lambda ag: (bids[ag], ag))
        self._my_announces.discard(item)
        self.contracts[item] = winner
        for o in [aid for aid in ("A", "B") if aid != self.id]:
            agent_send(self.sim, self.id, o, "CNP_AWARD",
                       {"item": item, "winner": winner,
                        "manager": self.id})
        print(f"  [{self.id}] CNP 开标 {item}: bids={bids} -> 中标 {winner}")
        if winner == self.id and item in self.item_keys:
            self.sim.schedule(self.sim.now + 1, self._start_work)

    # -- 承包方执行 --

    def _on_op_failed(self, op: tuple, detail: str) -> None:
        item = op[1].get("key", self.item_keys[0] if self.item_keys else "")
        print(f"  [{self.id}] CNP 承包执行失败（{op[0]}: {detail}）-> 重规划")
        if self.retries_left > 0:
            self.retries_left -= 1
            note = f"你上次计划在 {op[0]} 失败（{detail}），基于最新观察重试"
            self.sim.schedule(self.sim.now + 1, lambda: self._start_work(note))
        else:
            self.contracts.pop(item, None)
            self._failed_on.add(item)
            manager = next(aid for aid in ("A", "B") if aid != self.id)
            agent_send(self.sim, self.id, manager, "CNP_FAIL",
                       {"item": item, "detail": detail})
            print(f"  [{self.id}] 重试耗尽 -> CNP_FAIL {item} -> 重新招标")

    # -- 败者轮询 --

    def _poll(self, item: str) -> None:
        if self.contracts.get(item) == self.id:
            return   # 我自己在承包执行，无需自监视
        e = self.sim.arbiter.state.get(item)
        if e is None:
            return
        if not e.value.startswith("world@"):
            return   # 被持有，执行中
        drop = self._drop_pos()
        if drop and e.value[6:] == drop:
            return   # 已送达
        cur = (e.version, e.value)
        if self._poll_state.get(item) == cur:
            self._poll_count[item] = self._poll_count.get(item, 0) + 1
        else:
            self._poll_count[item] = 0
        self._poll_state[item] = cur
        if self._poll_count.get(item, 0) >= 2:
            print(f"  [{self.id}] CNP 轮询：{item} 两周期无变化 -> 重新招标")
            self.contracts.pop(item, None)
            self._tender(item)
            return
        self.sim.schedule(self.sim.now + self.POLL_EVERY,
                          lambda: self._poll(item))

    def _drop_pos(self):
        import re
        m = re.search(r"送到 \((\d+),(\d+)\)", self.goal)
        return (int(m.group(1)), int(m.group(2))) if m else None

    # -- 消息 --

    def on_msg(self, msg) -> None:
        p = getattr(msg, "payload", None) or {}
        t = msg.type
        if t == "CNP_ANNOUNCE":
            item, announced = p["item"], p["pos"]
            self._seen_announce.add(item)
            # 经典 CNP 语义：无法完成任务的承包方拒标（feasibility refusal）——
            # 承包失败的 item 不再投标，防止"低价但无能"的投标人循环中标
            if item in self._failed_on or item in self.contracts:
                agent_send(self.sim, self.id, p["from"], "CNP_REFUSE",
                           {"item": item})
                return
            cost = self._bid_cost(item, announced)
            agent_send(self.sim, self.id, p["from"], "CNP_BID",
                       {"item": item, "cost": cost})
            print(f"  [{self.id}] CNP 投标 {item}: cost={cost}")
        elif t == "CNP_BID":
            self._bids.setdefault(p["item"], {})[p["from"]] = p["cost"]
        elif t == "CNP_AWARD":
            item, winner = p["item"], p["winner"]
            self.contracts[item] = winner
            self._my_announces.discard(item)
            if winner == self.id:
                if item in self._failed_on:
                    # 被判中标但自己已知无法完成 -> 拒绝承包并通知管理方
                    print(f"  [{self.id}] 中标 {item} 但此前承包失败 -> 拒绝")
                    self.contracts.pop(item, None)
                    agent_send(self.sim, self.id, p.get("manager", "A"),
                               "CNP_FAIL", {"item": item, "detail": "declined"})
                    return
                if item in self.item_keys:
                    print(f"  [{self.id}] 中标 {item} -> 开始执行")
                    self.sim.schedule(self.sim.now + 1, self._start_work)
            else:
                # 败者：进入轮询监视
                self.sim.schedule(self.sim.now + self.POLL_EVERY,
                                  lambda: self._poll(item))
        elif t == "CNP_FAIL":
            item = p["item"]
            print(f"  [{self.id}] 收到 CNP_FAIL {item} -> 重新招标")
            self.contracts.pop(item, None)
            self._tender(item)
        elif t == "CNP_REFUSE":
            pass


# ---------- 4. 群聊协商 ----------

CHAT_SYS = """你是 5x5 网格世界中的具身 agent，与另一 agent 通过消息协商任务分工。
只输出一个 JSON 对象：
{"message": "给对方的简短话", "claim": "item:xxx" 或 "yield"}
claim="item:xxx" 表示你主张由自己完成该物品的送达；"yield" 表示你主张让给对方。
请考虑双方位置（谁近谁做更省总工作量）并明确表态，避免两人同做一件。"""


class GroupChatAgent(_RetryExecMixin, NoProtocolAgent):
    """群聊协商基线（AutoGen/MetaGPT 路线的机制级复现）。

    流程：各 agent 开场表态（LLM）-> 交换 -> 回应/终态（LLM）-> 依约执行。
    决策规则（确定性收尾，差异披露）：
      一方 claim + 另一方 yield -> claim 方执行；
      双方 claim 同一物品 -> 各自执行（冲突经算子失败显现——即 UIUC 2026-05
      发现的"对话减冲突却降成功率"的失败模式）；
      双方 yield -> agent_id 字典序较小者执行（防僵局的确定性兜底）；
      对话超时（+4 tick 无响应）-> 视为无协议各自执行。
    yield 方不做执行监视（对话层方案无内建故障感知——如实保留该弱点）。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._init_retry()
        self.transcript: list[dict] = []   # [{from, message, claim}]
        self._my_final: str | None = None
        self._other_final: str | None = None
        self._resolved = False
        self._opened = False
        self.chat_rounds = 0

    # -- LLM 对话 --

    def _chat(self) -> dict:
        user = (f"你的ID: {self.id}\n任务: {self.goal}\n"
                f"世界状态: {json.dumps(self._observe(), ensure_ascii=False)}\n"
                f"对话记录: {json.dumps(self.transcript, ensure_ascii=False)}\n"
                + ("请给出你的终态表态。" if self.transcript else "请开场表态。"))
        out = None
        for _ in range(2):
            out = self.client.extract_json(self.client.chat(CHAT_SYS, user))
            if out and "claim" in out:
                break
        if out is None or out.get("claim") not in (tuple(self.item_keys) + ("yield",)):
            out = {"message": "(解析失败，默认表态)", "claim": "yield"}
        self.chat_rounds += 1
        return out

    def _send_chat(self, out: dict, final: bool) -> None:
        self.transcript.append({"from": self.id, "message": out.get("message", ""),
                                "claim": out["claim"]})
        others = [aid for aid in ("A", "B") if aid != self.id]
        for o in others:
            agent_send(self.sim, self.id, o, "CHAT_MSG",
                       {"message": out.get("message", ""),
                        "claim": out["claim"], "final": final})
        print(f"  [{self.id}] 群聊{'终态' if final else '开场'}: "
              f"claim={out['claim']} | {out.get('message', '')[:40]}")

    # -- 状态机 --

    def _submit(self) -> None:
        if self.transcript:
            # 对端先开口：我的回应即终态
            out = self._chat()
            self._my_final = out["claim"]
            self._send_chat(out, final=True)
            self._try_resolve()
        else:
            out = self._chat()
            self._send_chat(out, final=False)
            # 开场表态暂存为候选终态（若对端终态一致即成交）
            self._my_final = out["claim"]
            self.sim.schedule(self.sim.now + 4, self._timeout_check)

    def _timeout_check(self) -> None:
        if not self._resolved:
            print(f"  [{self.id}] 群聊超时无终态 -> 视为无协议，各自执行")
            self._other_final = None
            self._resolve_execute()

    def _try_resolve(self) -> None:
        # 我已有终态且对端开场已到：若对端开场 yield 与我 claim 互补即成交，
        # 否则等对端终态（对端会回应）。这里保守：仅当互补才提前成交。
        mine, other = self._my_final, self._other_final
        if mine and other and (mine == "yield" or other == "yield"):
            self._resolve_execute()

    def _resolve_execute(self) -> None:
        if self._resolved:
            return
        self._resolved = True
        mine, other = self._my_final, self._other_final
        item = self.item_keys[0] if self.item_keys else ""
        other_id = next(aid for aid in ("A", "B") if aid != self.id)
        if mine == item and other == item:
            print(f"  [{self.id}] 群聊结果：双方同claim -> 各自执行（冲突模式）")
            self._start_work()
        elif mine == item and other == "yield":
            print(f"  [{self.id}] 群聊结果：对端让给我 -> 执行")
            self._start_work()
        elif mine == "yield" and other == item:
            print(f"  [{self.id}] 群聊结果：我让给 {other_id} -> 待命"
                  f"（对话层无故障监视）")
        else:
            # 双 yield / 信息不全 -> 字典序兜底
            if self.id < other_id:
                print(f"  [{self.id}] 群聊结果：双yield -> 字典序兜底，我执行")
                self._start_work()
            else:
                print(f"  [{self.id}] 群聊结果：双yield -> {other_id} 字典序优先，待命")

    # -- 消息 --

    def on_msg(self, msg) -> None:
        p = getattr(msg, "payload", None) or {}
        if msg.type != "CHAT_MSG":
            return
        self.transcript.append({"from": p["from"], "message": p.get("message", ""),
                                "claim": p.get("claim")})
        if p.get("final"):
            self._other_final = p["claim"]
            if self._my_final is not None:
                # 我此前发过开场；对端终态与我不一致且双方都非 yield -> 我补一次终态回应
                item = self.item_keys[0] if self.item_keys else ""
                if (self._my_final == item and self._other_final == item):
                    out = self._chat()
                    self._my_final = out["claim"]
                    self._send_chat(out, final=True)
                self._resolve_execute()
        else:
            self._other_final = p["claim"]   # 对端开场暂存
            if self._my_final is not None:   # 我已开场（时序晚的一方）
                self._try_resolve()

    def _on_op_failed(self, op: tuple, detail: str) -> None:
        print(f"  [{self.id}] 群聊路线算子失败（{op[0]}: {detail}）-> 重规划")
        if self.retries_left > 0:
            self.retries_left -= 1
            note = f"你上次计划在 {op[0]} 失败（{detail}），基于最新观察重试"
            self.sim.schedule(self.sim.now + 1, lambda: self._start_work(note))
        # 重试耗尽：对话层无重协商机制（如实保留弱点）


# ---------- 5. 中央编排（P2 规模矩阵版） ----------

class OrchScaleAgent(OrchestratorAgent):
    """P2 规模矩阵的 orch 臂：A0 兼任编排者，每件物品在其两个争用者中分派。

    适配披露：分派空间受限于与无协议臂相同的"成对争用"结构（每件物品
    二选一），不改变任务可达结构——orch 与基线的差异全部来自"选谁"与
    "失败重分配"，不来自任务重定义。重分配固定转给同 item 的另一个
    争用者（未失败过者），两者皆败则放弃该 item。
    """

    def setup_scale(self, all_agent_ids: list, item_candidates: dict) -> None:
        self.all_agent_ids = list(all_agent_ids)
        self.item_candidates = dict(item_candidates)   # item -> [aid, aid]

    def _assign(self) -> None:
        obs = self._observe()
        user = (f"你的ID: {self.id}（你是编排者）\n"
                f"任务总目标: 以下每件物品都要由其争用者之一取起并送到投放点 place\n"
                f"世界状态: {json.dumps(obs, ensure_ascii=False)}\n"
                f"物品与候选 worker: {json.dumps(self.item_candidates)}\n"
                '只输出 JSON: {"assignments": {"item:xxx": "A0", ...}, "reason": "..."}\n'
                f"每件物品恰好分给其候选 worker 之一。")
        plan = None
        for _ in range(2):
            plan = self.client.extract_json(self.client.chat(ORCH_SYS, user))
            if plan and isinstance(plan.get("assignments"), dict):
                break
        if plan is None:
            plan = {"assignments": {k: v[0] for k, v in self.item_candidates.items()},
                    "reason": "解析失败，退化为第一候选"}
        self._assign_done = True
        # 校验：候选之外的分派纠正为第一候选（LLM 输出不可全信）
        fixed = {}
        for k, cands in self.item_candidates.items():
            w = plan["assignments"].get(k)
            fixed[k] = w if w in cands else cands[0]
        print(f"  [orch] 分配: {fixed} | {plan.get('reason', '')[:40]}")
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
            self.sim.schedule(self.sim.now + 1, self._start_work)

    def _reassign(self, item: str, failed: str) -> None:
        self._failed_workers.add(failed)
        cands = [w for w in self.item_candidates.get(item, [])
                 if w not in self._failed_workers]
        if not cands:
            print(f"  [orch] {item}: 两个争用者均失败过 -> 放弃该 item")
            return
        other = cands[0]
        self.reassignments += 1
        print(f"  [orch] {item} 失败（{failed}）-> 重分配给 {other}")
        if other == self.id:
            self.assigned = [item]
            self.sim.schedule(self.sim.now + 1, self._start_work)
        else:
            agent_send(self.sim, self.id, other, "ORCH_ASSIGN",
                       {"assignments": {item: other}})
