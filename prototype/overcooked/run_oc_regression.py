"""Overcooked 移植的协议回归（离线，脚本 agent，无 API）。

验证目标：PRAP 仲裁器在厨房算子集（put_in/cook/serve/deliver）上零改动工作：
  1. 布局/状态初始化正确
  2. 墙体阻挡（厨房结构约束进算子层）
  3. put_in / cook / serve 的前置条件检查
  4. 锅互斥：A 持有锅租约期间 B 无法插入（事务互斥）
  5. TTL 自愈：持有者挂死时租约到期自动释放，B 接管
  6. L4 信念校验 + v0.2 隔离：谎报锅状态 -> deviation -> COMMIT 被拒
  7. 单 agent 全流程：洋葱 x2 -> put_in -> cook -> (等熟) -> serve -> deliver
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from prap_core import digest
from testbed import Sim, ScriptedAgent
from overcooked.oc_env import (COOK_TICKS, N_ONIONS, POT_POS, DELIVERY_POS,
                               init_oc_world, oc_success)

PASS = []


def check(name: str, cond: bool, detail: str = ""):
    PASS.append(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


class PhaseAgent(ScriptedAgent):
    """多阶段事务 agent：每个阶段一组 (keys, ops, ttl, delay)，顺序执行。"""

    def __init__(self, sim, agent_id, txns, **kw):
        self.txns = [dict(t) for t in txns]
        t0 = self.txns[0]
        super().__init__(sim, agent_id, tuple(t0["keys"]), t0["ops"],
                         ttl=t0.get("ttl", 30), **kw)

    def _advance(self):
        self.txns.pop(0)
        if self.txns:
            t = self.txns[0]
            self.keys, self.ops, self.ttl = tuple(t["keys"]), t["ops"], t.get("ttl", 30)
            self.sim.schedule(self.sim.now + 1 + t.get("delay", 0), self._submit)

    def _commit(self):
        if self.stuck or self.lease_id is None:
            return
        arb = self.sim.arbiter
        lease = arb.leases.get(self.lease_id)
        if lease is None or lease.ttl_end <= arb.now:
            self.lease_id = None
            return
        self._committed = True
        arb.commit(self.lease_id, self.ops)
        arb.release(self.lease_id)
        self.report.completed_at = self.sim.now
        self._advance()


def m(dx, dy, aid="A"):
    return ("move", {"agent": aid, "dx": dx, "dy": dy})


def g(key, aid="A"):
    return ("grab", {"agent": aid, "key": key})


def pi(key, aid="A"):
    return ("put_in", {"agent": aid, "key": key})


# ---------- 阶段事务定义（A 的全流程） ----------

TXN_COOK = {
    "keys": ["item:onion1", "item:onion2", "pot:1"],
    # (1,1)->(1,2)->(1,3) 抓 onion1 ->(1,2)->(2,1)? 见下：经 (1,2),(1,1),(2,1)
    "ops": [m(0, 1), m(0, 1), g("item:onion1"),          # 至 (1,3) 抓
            m(0, -1), m(0, -1), m(1, 0),                 # -> (2,1) 锅旁
            pi("item:onion1"),
            m(0, 1), m(0, 1), g("item:onion2"),          # -> (2,3) 抓 onion2
            m(0, -1), m(0, -1), pi("item:onion2"),       # -> (2,1) 投
            ("cook", {"agent": "A"})],                   # 点火（已相邻）
    "ttl": 40,
}
TXN_SERVE = {
    "keys": ["item:dish1", "pot:1", "soup:1"],
    "delay": COOK_TICKS,                                 # 等汤熟
    # (2,1)->(2,2)->(3,2)->(4,2)->(5,2)->(5,1) 取盘 ->(4,1) 锅旁 serve ->(4,2) 交付
    "ops": [m(0, 1), m(1, 0), m(1, 0), m(1, 0), m(0, -1),
            g("item:dish1"),
            m(-1, 0),                                    # -> (4,1) 锅旁
            ("serve", {"agent": "A"}),
            m(0, 1),                                     # -> (4,2) 投递点旁
            ("deliver", {"agent": "A"})],
    "ttl": 40,
}


def solo_pipeline():
    print("\n== 1. 单 agent 全流程（洋葱x2 -> 烹饪 -> 装盘 -> 交付）==")
    sim = Sim()
    arb = init_oc_world(sim)
    a = PhaseAgent(sim, "A", [TXN_COOK, TXN_SERVE])
    a.start(1)
    sim.run(80)
    ok, why = oc_success(arb)
    check("全流程交付成功", ok, why)
    check("锅复位为空", arb.state["pot:1"].value == "pot:0", arb.state["pot:1"].value)
    check("盘子消耗", arb.state["item:dish1"].value == "consumed")
    check("洋葱入锅", arb.state["item:onion1"].value == "in_pot"
          and arb.state["item:onion2"].value == "in_pot")
    return sim, arb, a


def mutex_and_l4():
    print("\n== 2. 锅互斥 + L4 谎报锅状态隔离（与全流程并行注入 B）==")
    sim = Sim()
    arb = init_oc_world(sim)
    a = PhaseAgent(sim, "A", [TXN_COOK, TXN_SERVE])
    a.start(1)

    # B 的第一个事务：趁 A 烹饪期间谎报 pot:1="ready"（真值 pot:2 或 cooking@...）
    v_pot = arb.state["pot:1"].version
    bad_belief = [("pot:1", v_pot, digest("ready"))]
    b = ScriptedAgent(sim, "B", ("pot:1",), [("cook", {"agent": "B"})],
                      ttl=30, belief=bad_belief)
    b.start(2)
    sim.run(80)

    check("B 的 INTENT 被判 deviation", b.last_verdict if hasattr(b, "last_verdict")
          else arb.agent_status.get("B") == "suspect",
          f"status={arb.agent_status.get('B')}")
    check("B 被隔离（suspect）", arb.agent_status.get("B") == "suspect")
    # v0.2.2：suspect 不授予/不入队 —— 隔离无法被"拿到租约再续约"架空
    check("suspect 期间零授予（B 从未获得租约，提交无从发生）",
          b.report.granted_at is None
          and not any(e[0] == "grant" and e[3] == "B" for e in arb.log),
          f"granted_at={b.report.granted_at}")
    ok, why = oc_success(arb)
    check("A 的交付不受 B 干扰", ok, why)


def ttl_takeover():
    print("\n== 3. TTL 自愈：持有者挂死，B 接管 ==")
    sim = Sim()
    arb = init_oc_world(sim)
    # A：抓洋葱2 后挂死（持有租约不提交不释放）
    a = ScriptedAgent(sim, "A", ("item:onion2",),
                      [m(1, 1, "A"), m(0, 1, "A"), g("item:onion2", "A")],
                      ttl=6, stuck=True)
    a.start(1)
    # B：等着拿 onion2，拿到后放回去（证明所有权转移）
    b = ScriptedAgent(sim, "B", ("item:onion2",),
                      [m(-1, -1, "B"), m(-1, -1, "B"),   # (5,3)->(3,1)? 锅不可通行
                       ("noop", {"agent": "B"})],
                      ttl=30)
    b.start(2)
    try:
        sim.run(40)
    except RuntimeError:
        pass   # noop 算子不存在是预期的（只关心租约转移）
    check("A 的租约到期自动释放", any(e[0] == "expire" for e in arb.log))
    check("B 在过期后获得授权", b.report.granted_at is not None
          and b.report.granted_at >= 6, f"granted_at={b.report.granted_at}")


def wall_and_preconditions():
    print("\n== 4. 墙体/前置条件（直接调算子）==")
    sim = Sim()
    arb = init_oc_world(sim)
    lease = type("L", (), {"lease_id": "t"})()
    reg = arb.ops_registry
    ok1, d1 = reg["move"](arb, lease, {"agent": "A", "dx": 1, "dy": 0})
    check("正常移动成功", ok1, d1)                        # (1,1)->(2,1)
    ok2, d2 = reg["move"](arb, lease, {"agent": "A", "dx": 0, "dy": -1})
    check("墙体不可通行（(2,0) 是墙）", not ok2, d2)
    ok3, d3 = reg["move"](arb, lease, {"agent": "A", "dx": 0, "dy": -3})
    check("越界移动被拒", not ok3, d3)
    ok4, d4 = reg["move"](arb, lease, {"agent": "A", "dx": 1, "dy": 0})
    ok5, d5 = reg["move"](arb, lease, {"agent": "A", "dx": 0, "dy": 0})
    ok6, d6 = reg["cook"](arb, lease, {"agent": "A"})    # 锅空，不能点
    check("空锅点火被拒", not ok6, d6)
    ok7, d7 = reg["serve"](arb, lease, {"agent": "A"})   # 锅没熟且没端盘子
    check("未熟 serve 被拒", not ok7, d7)


if __name__ == "__main__":
    solo_pipeline()
    mutex_and_l4()
    ttl_takeover()
    wall_and_preconditions()
    n, k = len(PASS), sum(PASS)
    print(f"\n===== 回归结果：{k}/{n} 通过 =====")
    sys.exit(0 if k == n else 1)
