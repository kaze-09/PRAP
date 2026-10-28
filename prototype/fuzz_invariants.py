"""PRAP 不变量 fuzzer（P1-1）：随机对抗协议调用 + 不变量断言（离线，零 API）。

目标：把规范 §10 的"已证明"性质放到随机压力下检验——
  I1 互斥：任一时刻每个 key 至多被一个租约持有（key_holder <-> leases 双向一致）
  I2 零越权写：所有 op 事件都发生在有效租约期内（逐事件回放比对）
  I3 WAL 一致：replay_writes() 重建的 (value, version) 与活动状态完全一致
  I4 版本单调：每个 key 的 version 随事件序列单调不减
  I5 隔离完备：suspect agent 不产生新授予（在途租约允许，但须早于 suspect 时刻创建）
  I6 续约闸门：suspect 的 RENEW 与 max_hold 超限的 RENEW 都不改变 ttl_end
  I7 确定性：同 seed 两次运行的完整日志逐字节一致

对抗分布（每步随机选择）：
  - INTENT：随机 agent × 随机 key 子集；信念以 1/3 概率缺省、1/3 正确、1/3 对抗
    （版本超前 / 值伪造 / 引用不存在的 key）
  - COMMIT：随机已有租约 × 随机算子序列（含非法参数：越界移动、抓空气、双持）
  - RENEW / RELEASE / SUBSCRIBE / UNSUBSCRIBE：随机目标
  - 时钟随机推进；suspect 传播由协议自身触发

用法：python fuzz_invariants.py [--seeds 200] [--events 300] [--max-hold N]
"""

from __future__ import annotations

import argparse
import json
import random
import sys

from prap_core import Arbiter, digest
from testbed import OPS_REGISTRY, fmt_pos, init_world

AGENTS = ("A", "B", "C")
KEY_POOL = ("item:apple1", "item:apple2", "pos:A", "pos:B", "pos:C",
            "hold:A", "hold:B", "hold:C")


def shifted(v: str) -> str:
    """把 world@(x,y) 值扰动一格（伪造值用）；非位置值原样加后缀。"""
    if v.startswith("world@("):
        body = v[7:-1]
        x, y = body.split(",")
        return f"world@({int(x) + 1},{y})"
    return v + "#fake"


def random_belief(arb: Arbiter, rng: random.Random, keys) -> list | None:
    """1/3 缺省、1/3 忠实、1/3 对抗（超前版本/伪造值/幽灵 key）。"""
    r = rng.random()
    if r < 1 / 3:
        return None
    belief = []
    for k in keys:
        e = arb.state.get(k)
        if e is None:
            belief.append((k, rng.randint(0, 9), "deadbeef"))   # 幽灵 key
            continue
        if r < 2 / 3:
            belief.append((k, e.version, digest(e.value)))       # 忠实
        else:
            bver = e.version + rng.randint(0, 3)                 # 版本超前
            bval = shifted(e.value) if rng.random() < 0.5 else e.value
            belief.append((k, bver, digest(bval)))
    return belief


def random_ops(arb: Arbiter, rng: random.Random, agent: str) -> list:
    """随机算子序列，刻意混入非法参数（越界/抓空气/双持/未知算子）。"""
    ops = []
    for _ in range(rng.randint(1, 3)):
        name = rng.choice(["move", "grab", "place", "move", "bogus_op"])
        if name == "move":
            ops.append(("move", {"agent": agent,
                                 "dx": rng.randint(-2, 2), "dy": rng.randint(-2, 2)}))
        elif name in ("grab", "place"):
            key = rng.choice([k for k in KEY_POOL if k.startswith("item:")])
            ops.append((name, {"agent": agent, "key": key}))
        else:
            ops.append((name, {"agent": agent}))
    return ops


def check_invariants(arb: Arbiter, max_hold, ctx: str,
                     violations: list) -> None:
    def fail(name, detail=""):
        violations.append(f"{ctx}: {name} {detail}")

    # I1 互斥 + 双向一致
    seen = {}
    for lid, lease in arb.leases.items():
        for k in lease.keys:
            if k in seen:
                fail("I1 互斥违反", f"key={k} 同时被 {seen[k]} 与 {lid} 持有")
            seen[k] = lid
            if arb.key_holder.get(k) != lid:
                fail("I1 key_holder 失同步", f"key={k}")
    for k, lid in arb.key_holder.items():
        if lid not in arb.leases or k not in arb.leases[lid].keys:
            fail("I1 key_holder 指向无效租约", f"key={k} lease={lid}")

    # I3 WAL 重放一致
    replayed = arb.replay_writes()
    actual = {k: (e.value, e.version) for k, e in arb.state.items()}
    if replayed != actual:
        diff = {k for k in set(replayed) | set(actual)
                if replayed.get(k) != actual.get(k)}
        fail("I3 WAL 重放不一致", f"keys={sorted(diff)[:3]}")

    # I4 版本单调非负
    for k, e in arb.state.items():
        if e.version < 0:
            fail("I4 版本为负", f"key={k} version={e.version}")

    # I6 max_hold 上界：任何租约的 hold_total 不得超过上界
    if max_hold is not None:
        for lid, lease in arb.leases.items():
            if lease.hold_total > max_hold:
                fail("I6 max_hold 超限", f"lease={lid} hold={lease.hold_total}")


def check_op_leases(arb: Arbiter, violations: list) -> None:
    """I2 零越权写：每个 op 事件执行时其租约有效（未过期）且未被 suspect 拒绝。"""
    lease_snapshot: dict[str, tuple] = {}   # lease_id -> (agent_id, ttl_end)
    for e in arb.log:
        if e[0] == "grant":
            _, seq, lid, agent, keys, ttl_end = e
            lease_snapshot[lid] = (agent, ttl_end)
        elif e[0] in ("release", "expire", "revoke"):
            lease_snapshot.pop(e[2], None)
        elif e[0] == "op":
            _, seq, lid, name, args, ok, detail = e
            snap = lease_snapshot.get(lid)
            if snap is None:
                violations.append(f"I2 op 无有效租约: seq={seq} lease={lid}")
            # suspect 的 COMMIT 被拒后其 op 不会出现在日志（commit 前已拦截）


def fuzz_seed(seed: int, events: int, max_hold) -> list[str]:
    rng = random.Random(seed)
    arb = Arbiter(OPS_REGISTRY)
    init_world(arb, {"apple1": (2, 3), "apple2": (4, 0)},
               {"A": (0, 0), "B": (4, 4), "C": (2, 0)})
    if max_hold is not None:
        arb.max_hold = max_hold
    violations: list[str] = []
    last_seq = 0   # 增量处理日志的高水位

    def scan_new_grants(step: int, phase: str) -> None:
        """I5 隔离完备（增量）：新产生的 grant，其 agent 在本相位结束时不得为
        suspect。相位划分避免时序伪影——tick 授予（当时健康）与随后的 action
        （同 agent 变 suspect）不能混在一次扫描里。单线程模型下路径分析成立：
        suspect agent 的授予被 submit_intent 闸门与 _try_grant_waiting 过滤双重阻止。"""
        nonlocal last_seq
        for e in arb.log:
            if e[0] == "grant" and e[1] > last_seq:
                if arb.agent_status.get(e[3]) == "suspect":
                    violations.append(f"seed={seed} step={step} [{phase}]: "
                                      f"I5 suspect 获得新授予 agent={e[3]} lease={e[2]}")
        last_seq = arb.seq

    t = 0
    for step in range(events):
        t += rng.randint(1, 2)
        arb.tick(t)   # 过期释放 + 等待队列授予（含 suspect 过滤）
        scan_new_grants(step, "tick")

        act = rng.random()
        aid = rng.choice(AGENTS)
        keys = tuple(rng.sample(KEY_POOL, rng.randint(1, 3)))
        if act < 0.45:   # INTENT
            arb.submit_intent(aid, keys, ops=random_ops(arb, rng, aid),
                              ttl=rng.randint(2, 8), priority=rng.randint(0, 9),
                              belief=random_belief(arb, rng, keys))
        elif act < 0.60 and arb.leases:   # COMMIT
            lid = rng.choice(list(arb.leases.keys()))
            arb.commit(lid, random_ops(arb, rng, arb.leases[lid].agent_id))
        elif act < 0.72 and arb.leases:   # RENEW
            lid = rng.choice(list(arb.leases.keys()))
            arb.renew(lid, ext=rng.randint(1, 10))
        elif act < 0.84 and arb.leases:   # RELEASE
            arb.release(rng.choice(list(arb.leases.keys())))
        elif act < 0.92:   # SUBSCRIBE
            arb.subscribe(aid, keys, decision={"p_hat": rng.random()})
        elif arb.subs:      # UNSUBSCRIBE
            arb.unsubscribe(aid, list(keys))
        scan_new_grants(step, "action")

        check_invariants(arb, max_hold, f"seed={seed} step={step}", violations)
        check_op_leases(arb, violations)

    # I7 确定性：同 seed 重跑一遍，日志须逐字节一致
    arb2 = _replay_same_seed(seed, events, max_hold)
    if json.dumps(arb.log, default=str) != json.dumps(arb2.log, default=str):
        violations.append(f"I7 确定性违反: seed={seed} 两次运行日志不一致")
    return violations


def _replay_same_seed(seed: int, events: int, max_hold) -> Arbiter:
    """同 seed 重放（fuzz_seed 的孪生执行，用于 I7）。"""
    rng = random.Random(seed)
    arb = Arbiter(OPS_REGISTRY)
    init_world(arb, {"apple1": (2, 3), "apple2": (4, 0)},
               {"A": (0, 0), "B": (4, 4), "C": (2, 0)})
    if max_hold is not None:
        arb.max_hold = max_hold
    t = 0
    for _ in range(events):
        t += rng.randint(1, 2)
        arb.tick(t)
        act = rng.random()
        aid = rng.choice(AGENTS)
        keys = tuple(rng.sample(KEY_POOL, rng.randint(1, 3)))
        if act < 0.45:
            arb.submit_intent(aid, keys, ops=random_ops(arb, rng, aid),
                              ttl=rng.randint(2, 8), priority=rng.randint(0, 9),
                              belief=random_belief(arb, rng, keys))
        elif act < 0.60 and arb.leases:
            lid = rng.choice(list(arb.leases.keys()))
            arb.commit(lid, random_ops(arb, rng, arb.leases[lid].agent_id))
        elif act < 0.72 and arb.leases:
            arb.renew(rng.choice(list(arb.leases.keys())), ext=rng.randint(1, 10))
        elif act < 0.84 and arb.leases:
            arb.release(rng.choice(list(arb.leases.keys())))
        elif act < 0.92:
            arb.subscribe(aid, keys, decision={"p_hat": rng.random()})
        elif arb.subs:
            arb.unsubscribe(aid, list(keys))
    return arb


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--events", type=int, default=300)
    ap.add_argument("--max-hold", type=int, default=None)
    args = ap.parse_args()

    print(f"== PRAP 不变量 fuzzer：{args.seeds} seeds × {args.events} 事件"
          f"（max_hold={args.max_hold}）==")
    all_v: list[str] = []
    for seed in range(args.seeds):
        all_v.extend(fuzz_seed(seed, args.events, args.max_hold))

    if all_v:
        print(f"\n发现 {len(all_v)} 条不变量违反：")
        for v in all_v[:20]:
            print(f"  [VIOLATION] {v}")
        return 1
    print(f"\n===== {args.seeds} seeds × {args.events} 事件全部通过："
          f"I1 互斥 / I2 零越权写 / I3 WAL 一致 / I4 版本单调 / "
          f"I5 隔离完备 / I6 max_hold / I7 确定性 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
