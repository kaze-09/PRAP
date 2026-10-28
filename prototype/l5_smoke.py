"""L5 消融的离线冒烟：StubClient 按 obs 计算完美计划（零 API），
验证 always/never 两策略的刷新机制与失败路径按预期工作。"""
import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from llm_agent import LLMAgent
from run_l5_ablation import GOAL, a_plan, run_trial
from testbed import Sim, ScriptedAgent, fmt_pos, init_world
from run_e1_full import TASKS, check_success

SPEC = TASKS["T1"]
ITEM_KEY = "item:apple1"
DROP = SPEC["drop"]


class PerfectClient:
    """从 obs 解析世界状态并产出正确计划（替代 LLM 的确定性规划器）。"""
    name = "stub"
    model = "stub-perfect"

    def __init__(self):
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def chat(self, system, user):
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += 500
        self.usage["completion_tokens"] += 200
        obs = json.loads(user.split("世界状态: ")[1].rsplit("\n", 1)[0])
        item = obs[ITEM_KEY]["value"]          # world@(x,y)
        me = obs["pos:B"]["value"]
        ix, iy = int(item[7:].split(",")[0].strip("() ")), int(item[7:].split(",")[1].strip("() "))
        mx, my = int(me.strip("()").split(",")[0]), int(me.strip("()").split(",")[1])
        ops = []
        x, y = mx, my
        while x != ix:
            ops.append(["move", {"dx": 1 if x < ix else -1, "dy": 0}])
            x += 1 if x < ix else -1
        while y != iy:
            ops.append(["move", {"dx": 0, "dy": 1 if y < iy else -1}])
            y += 1 if y < iy else -1
        ops.append(["grab", {"key": ITEM_KEY}])
        dx, dy = DROP
        while x != dx:
            ops.append(["move", {"dx": 1 if x < dx else -1, "dy": 0}])
            x += 1 if x < dx else -1
        while y != dy:
            ops.append(["move", {"dx": 0, "dy": 1 if y < dy else -1}])
            y += 1 if y < dy else -1
        ops.append(["place", {"key": ITEM_KEY}])
        belief = {k: {"value": s["value"], "version": s["version"]}
                  for k, s in obs.items() if k.startswith(("item:", "pos:"))}
        return json.dumps({"thought": "stub", "keys": [ITEM_KEY], "ops": ops,
                           "ttl": 20, "priority": 5, "belief": belief},
                          ensure_ascii=False)

    def extract_json(self, s):
        return json.loads(s)


def smoke(policy: str) -> dict:
    sim = Sim()
    init_world(sim.arbiter, dict(SPEC["items"]), dict(SPEC["agents"]),
               width=SPEC["W"], height=SPEC["H"])
    a = ScriptedAgent(sim, "A", (ITEM_KEY,), a_plan("multi"), ttl=30, think=2)
    a.start(1)
    client = PerfectClient()
    b = LLMAgent(sim, "B", client, GOAL, [ITEM_KEY], SPEC["agents"]["B"],
                 sub_policy=policy)
    b.start(2)
    try:
        sim.run(SPEC["horizon"])
    except RuntimeError as e:
        print(f"  仿真异常: {e}")
    ok, why = check_success(sim, "C2", "T1")
    return {"policy": policy, "success": ok, "why": why,
            "proactive": b.proactive_refresh, "reactive": b.reactive_replans,
            "updates": b.updates_recv, "calls": client.usage["calls"],
            "final": sim.arbiter.state[ITEM_KEY].value}


if __name__ == "__main__":
    for p in ("never", "always", "threshold", "oracle"):
        r = smoke(p)
        print(f"[{p:<9}] 成功={r['success']} 主动刷新={r['proactive']} "
              f"被动重规划={r['reactive']} 消息={r['updates']} "
              f"LLM调用={r['calls']} 终态={r['final']}")
