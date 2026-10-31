"""R3 离线冒烟（零 API）：stub 规划器验证 prap（幻觉被隔离）与
coagent（不可逆错位消耗）两条路径。"""
import json
import re
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from run_r3 import build_r3_sim, check_r3


class StubClient:
    name = "stub"
    model = "stub"

    def __init__(self):
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def chat(self, system, user):
        self.usage["calls"] += 1
        aid = user.split("你的ID: ")[1].split("\n")[0]
        obs = json.loads(user.split("世界状态: ")[1].split("\n输出")[0]
                         if "\n输出" in user else
                         user.split("世界状态: ")[1].split("\n")[0])
        item = obs["item:apple1"]["value"]
        if not item.startswith("world@"):
            return json.dumps({"thought": "wait", "keys": ["item:apple1"],
                               "ops": [], "ttl": 5, "priority": 5,
                               "belief": {k: {"value": s["value"], "version": s["version"]}
                                          for k, s in obs.items()}})
        ipos = self._pos(item.replace("world@", ""))
        dpos = self._pos(obs["drop:good"]["value"].replace("world@", ""))
        me = self._pos(obs[f"pos:{aid}"]["value"])
        ops = (self._moves(me, ipos) + [["grab", {"key": "item:apple1"}]]
               + self._moves(ipos, dpos) + [["consume", {"key": "item:apple1"}]])
        belief = {k: {"value": s["value"], "version": s["version"]}
                  for k, s in obs.items()}
        return json.dumps({"thought": "stub", "keys": ["item:apple1", "drop:good"],
                           "ops": ops, "ttl": 25, "priority": 5, "belief": belief},
                          ensure_ascii=False)

    @staticmethod
    def _pos(s):
        x, y = s.strip("()").split(",")
        return int(x), int(y)

    @staticmethod
    def _moves(frm, to):
        ops = []
        x, y = frm
        while x != to[0]:
            ops.append(["move", {"dx": 1 if x < to[0] else -1, "dy": 0}])
            x += 1 if x < to[0] else -1
        while y != to[1]:
            ops.append(["move", {"dx": 0, "dy": 1 if y < to[1] else -1}])
            y += 1 if y < to[1] else -1
        return ops

    def extract_json(self, s):
        return json.loads(s)


def run_one(arm, trial=0):
    clients = {"qwen": StubClient(), "deepseek": StubClient()}
    sim, agents = build_r3_sim(arm, trial, clients)
    try:
        sim.run(120)
    except RuntimeError as e:
        print(f"  !! 异常: {e}")
        return
    ok, why = check_r3(sim.arbiter)
    print(f"  [{arm}] success={ok} 终态={sim.arbiter.state['item:apple1'].value} "
          f"隔离={sum(1 for v in sim.arbiter.agent_status.values() if v == 'suspect')}")


if __name__ == "__main__":
    run_one("prap")
    run_one("coagent")
