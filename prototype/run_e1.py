"""E1 最小实验：验证 PRAP 原型的四条关键路径（规范 §7 评测挂钩的冒烟版）。

  场景 1   TTL 释放：持约 agent 挂死 -> 租约到期自动释放 -> 等待者接管（无死锁）
  场景 1b  续约路径：RENEW 延长持有，等待者在延长后的到期 tick 接管
  场景 2   L4 信念校验：healthy -> ok / 落后 -> stale / 幻觉 -> deviation
  场景 3   确定性：两次运行日志与终态一致 + WAL redo 重放重建终态
  场景 4   L5 订阅：被订阅 key 变化时收到 STATE_UPDATE

运行：python run_e1.py （纯标准库，无外部依赖）
"""

from __future__ import annotations

import json
import sys

from prap_core import digest
from testbed import Sim, ScriptedAgent, fetch_ops, fmt_pos, init_world

ITEM = "apple1"
ITEM_KEY = f"item:{ITEM}"
ITEM_POS = (2, 3)
DROP = (0, 4)
HORIZON = 40

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def make_contended_sim(a_stuck: bool) -> tuple[Sim, ScriptedAgent, ScriptedAgent]:
    """场景 1/1b/3 共用：A(0,0) 与 B(4,4) 争抢 item:apple1 并送达 (0,4)。"""
    sim = Sim()
    init_world(sim.arbiter, {ITEM: ITEM_POS}, {"A": (0, 0), "B": (4, 4)})
    a = ScriptedAgent(sim, "A", (ITEM_KEY,), fetch_ops("A", (0, 0), ITEM_KEY, ITEM_POS, DROP),
                      ttl=5, stuck=a_stuck)
    b = ScriptedAgent(sim, "B", (ITEM_KEY,), fetch_ops("B", (4, 4), ITEM_KEY, ITEM_POS, DROP),
                      ttl=5, think=2)
    a.start(1)
    b.start(2)
    return sim, a, b


def scenario_1_ttl_release() -> None:
    print("\n== 场景 1：TTL 释放（持约者挂死 -> 自动释放 -> 等待者接管）==")
    sim, a, b = make_contended_sim(a_stuck=True)
    sim.run(HORIZON)
    expiry_tick = 1 + 5  # A 在 t=1 获授，ttl=5
    check("A 在 t=1 获授", a.report.granted_at == 1, f"granted_at={a.report.granted_at}")
    check("B 被 DENY 后停靠（t=2）", b.report.blocked_from == 2,
          f"blocked_from={b.report.blocked_from}")
    check(f"A 挂死后租约在 t={expiry_tick} 自动过期",
          sim.arbiter.stats["expires"] == 1, f"expires={sim.arbiter.stats['expires']}")
    check("B 在过期 tick 接管", b.report.granted_at == expiry_tick,
          f"granted_at={b.report.granted_at}")
    check("B 无死锁地完成事务", b.report.completed_at is not None,
          f"completed_at={b.report.completed_at}")
    check("终态：apple1 已由 B 送达投放点",
          sim.arbiter.state[ITEM_KEY].value == f"world@{fmt_pos(DROP)}",
          sim.arbiter.state[ITEM_KEY].value)
    check("A 收到 EXPIRED 通知", a.report.expiry_notifications == 1)


def scenario_1b_renew() -> None:
    print("\n== 场景 1b：续约路径（RENEW 延长持有，接管推迟到新到期 tick）==")
    sim, a, b = make_contended_sim(a_stuck=True)
    a.renew_at(4, ext=5)   # 挂死前续约一次：ttl_end 6 -> 11
    sim.run(HORIZON)
    check("A 续约成功", a.report.renewed, f"renews={sim.arbiter.stats['renews']}")
    check("B 在续约后的到期 tick (11) 才接管", b.report.granted_at == 1 + 5 + 5,
          f"granted_at={b.report.granted_at}")
    check("B 完成事务", b.report.completed_at is not None)


def scenario_2_belief_verdicts() -> None:
    print("\n== 场景 2：L4 信念校验（版本号比较，无 LLM 参与）==")
    sim = Sim()
    init_world(sim.arbiter, {ITEM: ITEM_POS}, {"C": (1, 1)})
    # 先让 C 动一步：pos:C 升到 v2，制造"世界已前进"
    mover = ScriptedAgent(sim, "C", ("pos:C",), [("move", {"agent": "C", "dx": 1, "dy": 0})],
                          think=0)
    mover.start(1)

    item_v = sim.arbiter.state[ITEM_KEY].version  # = 1
    item_dig = digest(f"world@{fmt_pos(ITEM_POS)}")
    healthy = ScriptedAgent(sim, "H", (ITEM_KEY,), [],
                            belief=[(ITEM_KEY, item_v, item_dig)])
    stale = ScriptedAgent(sim, "S", (ITEM_KEY,), [],
                          belief=[("pos:C", 1, digest("(1,1)"))])          # v1 < v2
    ghost = ScriptedAgent(sim, "G", (ITEM_KEY,), [],
                          belief=[("item:ghost", 3, "deadbeef")])          # key 不存在
    ahead = ScriptedAgent(sim, "X", (ITEM_KEY,), [],
                          belief=[(ITEM_KEY, item_v + 99, "x")])           # 版本超前
    for ag in (healthy, stale, ghost, ahead):
        ag.start(3)
    sim.run(HORIZON)
    check("healthy -> ok", healthy.report.verdict == "ok", str(healthy.report.verdict))
    check("落后世界 -> stale", stale.report.verdict == "stale", str(stale.report.verdict))
    check("引用不存在的 key -> deviation", ghost.report.verdict == "deviation",
          str(ghost.report.verdict))
    check("版本超前 -> deviation", ahead.report.verdict == "deviation",
          str(ahead.report.verdict))


def scenario_3_determinism() -> None:
    print("\n== 场景 3：确定性（同脚本两次运行一致 + WAL redo 重放）==")
    sims = []
    for _ in range(2):
        sim, a, b = make_contended_sim(a_stuck=True)
        sim.run(HORIZON)
        sims.append(sim)
    log0, log1 = json.dumps(sims[0].arbiter.log), json.dumps(sims[1].arbiter.log)
    st0 = {k: (e.value, e.version) for k, e in sims[0].arbiter.state.items()}
    st1 = {k: (e.value, e.version) for k, e in sims[1].arbiter.state.items()}
    check("两次运行日志完全一致", log0 == log1)
    check("两次运行终态完全一致", st0 == st1)
    replayed = sims[0].arbiter.replay_writes()
    check("WAL redo 重放重建终态", replayed == st0,
          f"{len(replayed)} keys replayed")


def scenario_5_subscription() -> None:
    print("\n== 场景 4：L5 订阅管道（被订阅 key 变化 -> STATE_UPDATE 推送）==")
    sim = Sim()
    init_world(sim.arbiter, {ITEM: ITEM_POS, "apple2": (4, 0)}, {"C": (4, 0)})
    watcher = ScriptedAgent(sim, "W", (f"item:apple2",), [])
    watcher.start(1)
    # W 订阅 apple2（决策参数留空，校准曲线在 E3 实验中扫描）
    sim.schedule(2, lambda: sim.arbiter.subscribe(
        "W", (f"item:apple2",), decision={"p_hat": 0.5, "L_miss": 4, "C_sub": 1}))
    grabber = ScriptedAgent(sim, "C", ("item:apple2",),
                            [("grab", {"agent": "C", "key": "item:apple2"})], think=0)
    grabber.start(3)
    sim.run(HORIZON)
    check("订阅者收到 STATE_UPDATE", watcher.report.state_updates >= 1,
          f"updates={watcher.report.state_updates}")
    check("订阅决策已入日志（供 E3 校准）",
          any(e[0] == "subscribe" and e[4] for e in sim.arbiter.log))


def scenario_6_quarantine() -> None:
    """v0.2 信念隔离：suspect agent 的 COMMIT 被拒、租约强制收回；
    以正确信念重新 INTENT 后解除隔离。验证"幻觉 agent 无法破坏他人成果"。"""
    print("\n== 场景 5（v0.2）：deviation 隔离 ==")
    sim = Sim()
    init_world(sim.arbiter, {ITEM: ITEM_POS}, {"A": (0, 0), "B": (4, 4)})
    arb = sim.arbiter

    a = ScriptedAgent(sim, "A", (ITEM_KEY,),
                      fetch_ops("A", (0, 0), ITEM_KEY, ITEM_POS, DROP), think=2)
    a.start(1)

    class Stub:
        rejects = 0
        def on_msg(self, msg):
            if msg.type == "COMMIT_REJECTED":
                Stub.rejects += 1
    sim.agents["B"] = Stub()

    # B 持错误信念（苹果不在它以为的位置）-> deviation -> suspect
    wrong_belief = [(ITEM_KEY, 1, "deadbeef")]
    sim.schedule(2, lambda: arb.submit_intent(
        "B", (ITEM_KEY,), ops=fetch_ops("B", (4, 4), ITEM_KEY, ITEM_POS, DROP),
        ttl=5, belief=wrong_belief))

    def b_commit():
        lid = next(l for l, le in arb.leases.items() if le.agent_id == "B")
        arb.commit(lid, fetch_ops("B", (4, 4), ITEM_KEY, ITEM_POS, DROP))
    sim.schedule(5, b_commit)          # B 在 t=3 获授后于 t=5 尝试提交

    captured: dict = {}
    sim.schedule(5, lambda: captured.update(status=arb.agent_status.get("B")))

    def b_recover():
        e = arb.state[ITEM_KEY]        # B 重新观察：以当前真实状态自报
        arb.submit_intent("B", (ITEM_KEY,),
                          ops=[("grab", {"agent": "B", "key": ITEM_KEY})],
                          ttl=5, belief=[(ITEM_KEY, e.version, digest(e.value))])
    sim.schedule(6, b_recover)

    def b_commit2():
        lid = next((l for l, le in arb.leases.items() if le.agent_id == "B"), None)
        if lid:
            arb.commit(lid, [("grab", {"agent": "B", "key": ITEM_KEY})])
    sim.schedule(8, b_commit2)         # 恢复后的第二次提交（验证被接受）
    sim.run(HORIZON)

    check("B 携错误信念 -> 被标记 suspect", captured.get("status") == "suspect",
          captured.get("status"))
    check("B 的 COMMIT 被拒且租约强制收回", Stub.rejects == 1
          and arb.stats["commits_rejected"] == 1,
          f"rejects={arb.stats['commits_rejected']}")
    check("A 已送达的苹果未被 B 破坏",
          arb.state[ITEM_KEY].value == f"world@{fmt_pos(DROP)}",
          arb.state[ITEM_KEY].value)
    check("B 以正确信念重新 INTENT -> 解除隔离", arb.agent_status.get("B") == "ok",
          arb.agent_status.get("B"))
    check("解除后 B 的提交被接受（无第二次拦截）",
          Stub.rejects == 1 and arb.stats["commits"] >= 2,
          f"commits={arb.stats['commits']}")


def main() -> int:
    print(f"PRAP 原型 E1 最小实验（HORIZON={HORIZON} ticks）")
    scenario_1_ttl_release()
    scenario_1b_renew()
    scenario_2_belief_verdicts()
    scenario_3_determinism()
    scenario_5_subscription()
    scenario_6_quarantine()

    failed = [r for r in results if not r[1]]
    print(f"\n结果：{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        for name, _, _ in failed:
            print(f"  FAILED: {name}")
        return 1
    print("全部通过：TTL 释放 / 续约 / L4 分类 / 确定性重放 / L5 订阅 均按规范工作。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
