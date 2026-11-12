"""Shared parametric design-document testbed (later CSCWD cell).

Two LLM agents edit a coupling-plate file hosted by the same authority
arbiter as the grid cells. Operators are set_param and release_revision.
This module does not touch unified_seed_rerun/ or combo_gate_orch/.
"""
from __future__ import annotations

from typing import Any, Optional

from prap_core import Arbiter, digest
from llm_agent import LLMAgent

PART = "coupling_plate"

# Ledger keys. Mating dimensions are read-only reference values.
KEY_BORE = "doc:bore_mm"
KEY_WALL = "doc:wall_mm"
KEY_OUTER = "doc:outer_mm"
KEY_MATING_BORE = "doc:mating_bore_mm"
KEY_MATING_WALL = "doc:mating_wall_mm"
KEY_STATUS = "doc:status"
KEY_REV = "doc:revision"
KEY_PART = "doc:part"

DOC_KEYS = (
    KEY_PART, KEY_BORE, KEY_WALL, KEY_OUTER,
    KEY_MATING_BORE, KEY_MATING_WALL, KEY_STATUS, KEY_REV,
)
WRITEABLE = {KEY_BORE, KEY_WALL, KEY_OUTER}

INIT = {
    KEY_PART: PART,
    KEY_BORE: 10,
    KEY_WALL: 4,
    KEY_OUTER: 18,          # 10 + 2*4, consistent at start
    KEY_MATING_BORE: 12,
    KEY_MATING_WALL: 5,
    KEY_STATUS: "in_design",
    KEY_REV: 0,
}
# After a correct change order: bore matches mating, wall matches mating,
# outer = 12 + 2*5 = 22, then the revision is released.
TARGET = {
    KEY_BORE: 12,
    KEY_WALL: 5,
    KEY_OUTER: 22,
    KEY_STATUS: "released",
}

A_WRITE = (KEY_BORE,)
B_WRITE = (KEY_WALL, KEY_OUTER)

INJECT_KEY = KEY_BORE
INJECT_SHIFT = 1  # millimetres, same persistent +1 family as the grid
# Plan-slip cell: cache is correct; +1 mm is applied to B's submitted
# set_param values (wall and outer), not to the observation path.
PLAN_SLIP_SHIFT = 1

HORIZON = 50

SYSTEM_PROMPT = """你是协同设计系统里的智能体，和其他智能体一起编辑一份由产品数据管理库托管的参数化零件文件 coupling_plate。
尺寸单位是毫米。世界状态里的 key 含义如下：
  doc:bore_mm, doc:wall_mm, doc:outer_mm 是本零件当前的孔径、壁厚、外径；
  doc:mating_bore_mm, doc:mating_wall_mm 是配合件上应对齐的孔径和壁厚，只读，你不能改它们；
  doc:status 为 in_design 或 released；doc:revision 是版本号。
几何约束：外径必须满足 outer_mm = bore_mm + 2 * wall_mm。请用你在世界状态里看到的数值计算，不要编造尺寸，也不要改写配合件上的数字。

任务：把本零件的孔径改成与配合件孔径一致，把壁厚改成与配合件壁厚一致，再按上述约束更新外径。仅当这三项都已正确时，把 doc:status 改为 released。released 不可撤销，之后不能再改尺寸。

可用算子：
  ["set_param", {"key": "doc:bore_mm", "value": 整数}]
  ["release_revision", {}]
一次 INTENT 只能锁定你被分配的可写 key（见用户消息）。获得 GRANT 后 ops 才会执行。若被 DENY，稍后可再次申请。
只输出一个 JSON 对象，不要任何其他文字：
{"thought": "简短推理", "keys": ["doc:..."], "ops": [["set_param", {"key": "doc:wall_mm", "value": 0}]], "ttl": 5, "priority": 5}
"""


def op_set_param(arb: Arbiter, lease, args) -> tuple:
    status = arb.state.get(KEY_STATUS)
    if status is not None and status.value == "released":
        return False, "already_released"
    key = str(args.get("key", ""))
    if key not in WRITEABLE:
        return False, "not_writable"
    if key not in lease.keys:
        return False, "key_not_leased"
    try:
        val = int(args.get("value"))
    except (TypeError, ValueError):
        return False, "bad_value"
    if val <= 0 or val > 100:
        return False, "out_of_range"
    arb._write(key, val, lease.agent_id, lease.lease_id)
    return True, "ok"


def op_release_revision(arb: Arbiter, lease, args) -> tuple:
    status = arb.state.get(KEY_STATUS)
    if status is None or status.value != "in_design":
        return False, f"bad_status_{None if status is None else status.value}"
    if KEY_STATUS not in lease.keys:
        return False, "status_not_leased"
    arb._write(KEY_STATUS, "released", lease.agent_id, lease.lease_id)
    if KEY_REV in lease.keys and KEY_REV in arb.state:
        arb._write(KEY_REV, int(arb.state[KEY_REV].value) + 1,
                   lease.agent_id, lease.lease_id)
    return True, "ok"


DOC_OPS = {
    "set_param": op_set_param,
    "release_revision": op_release_revision,
}


def init_document(arb: Arbiter, state: Optional[dict] = None) -> None:
    for key, value in (state or INIT).items():
        arb._write(key, value, "init", None)


def snapshot(arb: Arbiter) -> dict[str, Any]:
    return {k: arb.state[k].value for k in DOC_KEYS if k in arb.state}


def consistent(s: dict) -> bool:
    try:
        return int(s[KEY_OUTER]) == int(s[KEY_BORE]) + 2 * int(s[KEY_WALL])
    except (KeyError, TypeError, ValueError):
        return False


def params_correct(s: dict) -> bool:
    return (
        int(s.get(KEY_BORE, -1)) == TARGET[KEY_BORE]
        and int(s.get(KEY_WALL, -1)) == TARGET[KEY_WALL]
        and int(s.get(KEY_OUTER, -1)) == TARGET[KEY_OUTER]
    )


def is_success(arb: Arbiter) -> bool:
    s = snapshot(arb)
    return params_correct(s) and s.get(KEY_STATUS) == "released" and not arb.leases


def is_wrong_release(arb: Arbiter) -> bool:
    s = snapshot(arb)
    return s.get(KEY_STATUS) == "released" and not params_correct(s)


def wasted_ops(arb: Arbiter) -> int:
    return sum(1 for e in arb.log if e[0] == "op" and e[5] is False)


def apply_plan_slip(ops: list, write_keys=None) -> tuple[list, int]:
    """Add +1 mm to each set_param on a writable dimension. The cache is not touched."""
    allowed = set(write_keys) if write_keys is not None else set(WRITEABLE)
    slipped: list = []
    n = 0
    for op in ops:
        if not (isinstance(op, (list, tuple)) and len(op) == 2):
            slipped.append(op)
            continue
        name, args = op
        args = dict(args) if isinstance(args, dict) else {}
        if name == "set_param":
            key = str(args.get("key", ""))
            if key in allowed and key in WRITEABLE:
                try:
                    val = int(args.get("value")) + PLAN_SLIP_SHIFT
                except (TypeError, ValueError):
                    slipped.append(("set_param", args))
                    continue
                slipped.append(("set_param", {"key": key, "value": val}))
                n += 1
                continue
        slipped.append((name, args))
    return slipped, n


class DesignAgent(LLMAgent):
    """LLM agent on the design document. Does not change llm_agent.py."""

    SYSTEM_PROMPT_OVERRIDE = SYSTEM_PROMPT

    def __init__(self, sim, agent_id: str, client, write_keys: tuple[str, ...],
                 inject_bore: bool = False, reassign_on_isolate: bool = False,
                 plan_slip: bool = False,
                 **kwargs):
        goal = (
            f"你的 ID 是 {agent_id}。你可写的 key 是 {list(write_keys)}。"
            "把本零件改到与配合件对齐，更新外径，并在三项都正确后 release。"
        )
        kwargs.setdefault("belief_mode", "sdk_cache")
        kwargs.setdefault("item_keys", list(write_keys))
        kwargs.setdefault("start_pos", (0, 0))
        kwargs.setdefault("max_replans", 3)
        kwargs.setdefault("think", 2)
        self.document_spec = kwargs.pop("document_spec", None)
        self.read_logs: list = []
        self.intent_logs: list = []
        super().__init__(sim, agent_id, client, goal, **kwargs)
        self.write_keys = tuple(write_keys)
        self.inject_bore = inject_bore
        self.plan_slip = plan_slip
        self.reassign_on_isolate = reassign_on_isolate
        self.reassignments = 0
        self.n_plan_slips = 0
        self.inherited_from = None
        self._handed_off = False
        self.op_cap = 8

    def _observe(self) -> dict:
        if self.document_spec is not None:
            from natural_doc import observe_from_document
            return observe_from_document(self)
        obs = super()._observe()
        # A correct_pin must not be overwritten by the persistent injection;
        # the next plan is supposed to use the authority value.
        if (self.inject_bore and INJECT_KEY in self.sim.arbiter.state
                and INJECT_KEY not in getattr(self, "pinned_values", {})):
            entry = self.sim.arbiter.state[INJECT_KEY]
            try:
                val = int(entry.value) + INJECT_SHIFT
            except (TypeError, ValueError):
                return obs
            obs[INJECT_KEY] = {"value": val, "version": entry.version}
            self._cache(INJECT_KEY, entry.version, digest(val))
        return obs

    def _refresh_goal(self) -> None:
        extra = ""
        if self.inherited_from:
            extra = f"另一名写者 {self.inherited_from} 已被隔离，其可写字段已转交给你。"
        self.goal = (
            f"你的 ID 是 {self.id}。你可写的 key 是 {list(self.write_keys)}。"
            "把本零件改到与配合件对齐，更新外径，并在三项都正确后 release。"
            + extra
        )

    def inherit_writes(self, from_agent: "DesignAgent") -> None:
        """Take the isolated writer's fields and replan (reassignment arm)."""
        merged = tuple(dict.fromkeys(list(self.write_keys) + list(from_agent.write_keys)))
        if merged == self.write_keys and self.inherited_from:
            return
        self.write_keys = merged
        self.item_keys = list(merged)
        self.inherited_from = from_agent.id
        self.reassignments += 1
        self.replans_left = max(int(self.replans_left), 2)
        self._reapplies_left = max(int(getattr(self, "_reapplies_left", 0)), 2)
        self._refresh_goal()
        self.sim.schedule(self.sim.now + 1, self._submit)

    def _handoff_to(self, to_id: str = "A") -> None:
        if self._handed_off:
            return
        other = self.sim.agents.get(to_id)
        if other is None or not hasattr(other, "inherit_writes"):
            return
        if snapshot(self.sim.arbiter).get(KEY_STATUS) == "released":
            return
        self._handed_off = True
        self._reapplies_left = 0
        self.replans_left = 0
        other.inherit_writes(self)

    def on_msg(self, msg) -> None:
        # Pin the true bore even if it was not in the leased key set
        # (B only leases wall/outer; the deviation is on the bore).
        if getattr(msg, "type", None) == "CORRECT":
            entry = self.sim.arbiter.state.get(KEY_BORE)
            if entry is not None:
                self.pinned_values[KEY_BORE] = entry.value
                self._cache(KEY_BORE, entry.version, digest(entry.value))
        super().on_msg(msg)
        if not self.reassign_on_isolate or self._handed_off:
            return
        ev = (getattr(msg, "verdict", None) or {}).get("evidence") or []
        isolated = (
            getattr(msg, "type", None) == "DENY_WITH_STATE"
            and (
                (getattr(msg, "verdict", None) or {}).get("class") == "deviation"
                or any(isinstance(e, (list, tuple)) and e and e[0] == "suspect_blocked"
                       for e in ev)
            )
        )
        if isolated and self.sim.arbiter.agent_status.get(self.id) == "suspect":
            self._handoff_to("A")

    def _sanitize_ops(self, ops: list) -> list:
        clean = []
        for op in ops:
            if not (isinstance(op, (list, tuple)) and len(op) == 2):
                continue
            name, args = op
            args = dict(args) if isinstance(args, dict) else {}
            if name == "set_param":
                key = str(args.get("key", ""))
                if key not in self.write_keys:
                    continue
                try:
                    val = int(args.get("value"))
                except (TypeError, ValueError):
                    continue
                clean.append(("set_param", {"key": key, "value": val}))
            elif name == "release_revision":
                clean.append(("release_revision", {}))
        return clean

    def _submit(self) -> None:
        plan = self._plan(self._observe())
        # _plan already sanitizes; do not re-filter, because sanitized
        # ops are tuples and a list-only check would drop them all.
        ops = list(plan.get("ops") or [])
        if self.plan_slip:
            ops, nslip = apply_plan_slip(ops, self.write_keys)
            self.n_plan_slips += nslip
        keys = list(self.write_keys)
        if any(name == "release_revision" for name, _ in ops):
            if KEY_STATUS not in keys:
                keys.append(KEY_STATUS)
            if KEY_REV not in keys:
                keys.append(KEY_REV)
        # Lease only the assigned write set (plus status on release).
        # The gate still sees the agent's view of the whole document.
        self.keys = tuple(keys)
        self.ops = ops
        floor = self.think + len(self.ops) + 2
        self.ttl = max(int(plan.get("ttl", 5)), floor)
        plan["ttl_requested"] = plan.get("ttl", 5)
        plan["ttl_enforced"] = self.ttl
        if self.ttl_override is not None:
            self.ttl = self.ttl_override
            plan["ttl_enforced"] = self.ttl
        self.priority = max(0, min(int(plan.get("priority", 5)), 9))
        belief = []
        for k in DOC_KEYS:
            if k in self.observed:
                ver, dig = self.observed[k]
                belief.append((k, ver, dig))
        self.sim.arbiter.submit_intent(
            self.id, self.keys, ops=self.ops,
            ttl=self.ttl, priority=self.priority, belief=belief,
        )

    def _handle_clean_commit(self) -> None:
        if is_success(self.sim.arbiter):
            return
        if self.replans_left > 0 and snapshot(self.sim.arbiter).get(KEY_STATUS) != "released":
            self.replans_left -= 1
            self.sim.schedule(self.sim.now + 2, self._submit)
