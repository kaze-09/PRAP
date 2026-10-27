"""Overcooked 风格厨房环境：PRAP 协议零改动移植（独立于网格 testbed）。

移植范围（与 Collab-Overcooked 主线解耦，本模块自足）：
  - 布局：7x5 厨房，外墙 + 锅（不可通行）+ 投递点（不可通行）
  - 新状态 key：pot:1（锅状态机）、soup:1（汤）
  - 新算子：put_in / cook / serve / deliver（叠加 move/grab/place）
  - 协议机制（L0-L5 仲裁器、租约、TTL、L4 信念校验、v0.2 隔离）全部复用，
    仅通过 ops_registry 注入新算子——协议代码零改动

与原版 Overcooked 的简化（均为显式偏差，论文需声明）：
  1. 物品在可通行格上（原版在台面上需隔格抓取）
  2. 长事务压力改为"烹饪时长"（COOK_TICKS=10，锅被独占）而非多任务并行
  3. 汤配方固定 2 个洋葱 + 1 个盘子

状态约定：
  pot:1   = "pot:0"|"pot:1"|"pot:2"|"cooking@<ready_tick>"|"ready"
  soup:1  = "none" -> serve 后由 hold 携带 -> "delivered"
  item:X  = "world@(x,y)" | "held" | "in_pot" | "consumed"
  hold:A  = 持有的 key 名或 "none"
"""

from __future__ import annotations

from prap_core import Arbiter

COOK_TICKS = 10        # 烹饪时长：锅被独占的"长事务"窗口
N_ONIONS = 2           # 汤配方

# 7x5 布局：X=墙 P=锅(3,1) D=投递(4,3)（均不可通行）
LAYOUT = [
    "XXXXXXX",
    "X..P..X",
    "X.....X",
    "X.O.D.X",
    "XXXXXXX",
]
POT_POS = (3, 1)
DELIVERY_POS = (4, 3)
WALLS = {(x, y) for y, row in enumerate(LAYOUT)
         for x, c in enumerate(row) if c == "X"}
PASSABLE_BLOCK = WALLS | {POT_POS, DELIVERY_POS}

ITEMS = {"onion1": (1, 3), "onion2": (2, 3), "dish1": (5, 1)}
AGENTS = {"A": (1, 1), "B": (5, 3)}


def parse_pos(s: str) -> tuple:
    x, y = s.strip("()").split(",")
    return (int(x), int(y))


def _adjacent(pos: tuple, target: tuple) -> bool:
    return abs(pos[0] - target[0]) + abs(pos[1] - target[1]) == 1


# ---------- 算子实现（签名与 testbed 一致：(arb, lease, args) -> (ok, detail)）----------


def op_move(arb, lease, args) -> tuple:
    agent, dx, dy = args["agent"], args["dx"], args["dy"]
    pos = parse_pos(arb.state[f"pos:{agent}"].value)
    nx, ny = pos[0] + dx, pos[1] + dy
    if (nx, ny) in PASSABLE_BLOCK:
        return False, "blocked_cell"
    if not (0 <= nx < 7 and 0 <= ny < 5):
        return False, "out_of_bounds"
    arb._write(f"pos:{agent}", f"({nx},{ny})", agent, lease.lease_id)
    return True, "ok"


def op_grab(arb, lease, args) -> tuple:
    agent, key = args["agent"], args["key"]
    entry = arb.state.get(key)
    pos = arb.state[f"pos:{agent}"].value
    if entry is None or not entry.value.startswith("world@"):
        return False, "item_not_in_world"
    if entry.value[6:] != pos:
        return False, "not_same_cell"
    if arb.state[f"hold:{agent}"].value != "none":
        return False, "hands_full"
    arb._write(f"hold:{agent}", key, agent, lease.lease_id)
    arb._write(key, "held", agent, lease.lease_id)
    return True, "ok"


def op_place(arb, lease, args) -> tuple:
    agent, key = args["agent"], args["key"]
    if arb.state[f"hold:{agent}"].value != key:
        return False, "not_holding"
    pos = arb.state[f"pos:{agent}"].value
    arb._write(key, f"world@{pos}", agent, lease.lease_id)
    arb._write(f"hold:{agent}", "none", agent, lease.lease_id)
    return True, "ok"


def op_put_in(arb, lease, args) -> tuple:
    """把持有的物品放进相邻的锅（需要锅的租约——长事务资源的互斥点）。"""
    agent, key = args["agent"], args["key"]
    if arb.state[f"hold:{agent}"].value != key:
        return False, "not_holding"
    if arb.state["pot:1"].value not in tuple(f"pot:{i}" for i in range(N_ONIONS)):
        return False, "pot_not_accepting"
    pos = parse_pos(arb.state[f"pos:{agent}"].value)
    if not _adjacent(pos, POT_POS):
        return False, "not_adjacent_pot"
    n = int(arb.state["pot:1"].value.split(":")[1]) + 1
    arb._write("pot:1", f"pot:{n}", agent, lease.lease_id)
    arb._write(key, "in_pot", agent, lease.lease_id)
    arb._write(f"hold:{agent}", "none", agent, lease.lease_id)
    return True, "ok"


def op_cook(arb, lease, args) -> tuple:
    """点火：锅进入 cooking 状态，ready_tick 后视为 ready（长事务独占窗口）。"""
    agent = args["agent"]
    if arb.state["pot:1"].value != f"pot:{N_ONIONS}":
        return False, f"need_{N_ONIONS}_onions"
    pos = parse_pos(arb.state[f"pos:{agent}"].value)
    if not _adjacent(pos, POT_POS):
        return False, "not_adjacent_pot"
    arb._write("pot:1", f"cooking@{arb.now + COOK_TICKS}", agent, lease.lease_id)
    return True, "ok"


def _pot_ready(arb) -> bool:
    pv = arb.state["pot:1"].value
    return (pv.startswith("cooking@") and int(pv.split("@")[1]) <= arb.now) \
        or pv == "ready"


def op_serve(arb, lease, args) -> tuple:
    """汤好 + 端着盘子 + 相邻锅 -> 汤入盘（锅复位，盘子消耗，汤由 agent 持有）。"""
    agent = args["agent"]
    if not _pot_ready(arb):
        return False, f"pot_not_ready({arb.state['pot:1'].value})"
    pos = parse_pos(arb.state[f"pos:{agent}"].value)
    if not _adjacent(pos, POT_POS):
        return False, "not_adjacent_pot"
    if arb.state[f"hold:{agent}"].value != "item:dish1":
        return False, "need_dish_in_hand"
    arb._write("pot:1", "pot:0", agent, lease.lease_id)
    arb._write("item:dish1", "consumed", agent, lease.lease_id)
    arb._write("soup:1", "held", agent, lease.lease_id)
    arb._write(f"hold:{agent}", "none", agent, lease.lease_id)
    arb._write(f"hold:{agent}", "soup:1", agent, lease.lease_id)
    return True, "ok"


def op_deliver(arb, lease, args) -> tuple:
    """端汤站在投递点旁 -> 交付成功。"""
    agent = args["agent"]
    if arb.state[f"hold:{agent}"].value != "soup:1":
        return False, "not_holding_soup"
    pos = parse_pos(arb.state[f"pos:{agent}"].value)
    if not _adjacent(pos, DELIVERY_POS):
        return False, "not_adjacent_delivery"
    arb._write("soup:1", "delivered", agent, lease.lease_id)
    arb._write(f"hold:{agent}", "none", agent, lease.lease_id)
    return True, "ok"


OPS_REGISTRY_OC = {
    "move": op_move, "grab": op_grab, "place": op_place,
    "put_in": op_put_in, "cook": op_cook, "serve": op_serve, "deliver": op_deliver,
}


def init_oc_world(sim):
    """在传入的 sim（testbed.Sim 结构）上初始化厨房世界，返回 arbiter。"""
    arb = Arbiter(OPS_REGISTRY_OC)
    sim.arbiter = arb
    arb.width, arb.height = 7, 5
    for name, (x, y) in ITEMS.items():
        arb._write(f"item:{name}", f"world@({x},{y})", "init", None)
    for aid, (x, y) in AGENTS.items():
        arb._write(f"pos:{aid}", f"({x},{y})", "init", None)
        arb._write(f"hold:{aid}", "none", "init", None)
    arb._write("pot:1", "pot:0", "init", None)
    arb._write("soup:1", "none", "init", None)
    return arb


def oc_success(arb) -> tuple[bool, str]:
    """任务成功 = 汤 soup:1 已交付；不变量 = 无双重持有、锅状态机合法。"""
    if arb.state["soup:1"].value != "delivered":
        return False, f"soup 未交付（{arb.state['soup:1'].value}）"
    holds = [k for k, e in arb.state.items()
             if k.startswith("hold:") and e.value != "none"]
    if holds:
        return False, f"结束时仍持有: {holds}"
    pv = arb.state["pot:1"].value
    if pv.startswith("cooking@"):
        return False, "结束时锅仍在烹饪状态"
    return True, "ok"
