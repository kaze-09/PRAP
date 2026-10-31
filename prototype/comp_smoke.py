"""E6 四基线离线冒烟：StubClient 零 API，验证 coagent/orch/cnp/groupchat
的协调机制与失败路径按预期工作（C2 健康 / C4 幻觉各 1 局）。"""
import json
import re
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from comp_baselines import (CoAgentUndoAgent, ContractNetAgent, GroupChatAgent,
                            OrchestratorAgent)
from run_comparison import build_sim
from run_e1_full import check_success


class StubClient:
    """确定性规划器：常规规划走完美计划；编排/对话走固定表态。"""
    name = "stub"
    model = "stub-perfect"

    def __init__(self):
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def chat(self, system, user):
        self.usage["calls"] += 1
        if "编排者" in system:
            return self._assign(user)
        if "协商" in system:
            return self._chat(user)
        return self._plan(user)

    # -- 编排：每件物品分给离它最近的候选 worker --
    def _assign(self, user):
        obs = json.loads(user.split("世界状态: ")[1].split("\n")[0])
        if "物品与候选 worker: " in user:   # 规模版格式
            cands = json.loads(user.split("物品与候选 worker: ")[1].split("\n只输出")[0])
        else:                                # 2-agent 格式
            items = json.loads(user.split("需要分配的物品: ")[1].split("\n")[0])
            cands = {k: ["A", "B"] for k in items}
        assignments = {}
        for item, workers in cands.items():
            v = obs[item]["value"]
            if not v.startswith("world@"):
                assignments[item] = workers[0]
                continue
            ipos = self._pos(v.replace("world@", ""))
            best, bd = workers[0], 10 ** 9
            for w in workers:
                d = self._man(self._pos(obs[f"pos:{w}"]["value"]), ipos)
                if d < bd:
                    best, bd = w, d
            assignments[item] = best
        return json.dumps({"assignments": assignments, "reason": "stub-距离"})

    # -- 群聊：A 主张做，B 让 --
    def _chat(self, user):
        aid = user.split("你的ID: ")[1].split("\n")[0]
        item = re.search(r"item:\w+", user.split("任务: ")[1]).group(0)
        claim = item if aid == "A" else "yield"
        return json.dumps({"message": f"stub-{aid}", "claim": claim})

    # -- 常规规划：从 obs 解析完美计划 --
    def _plan(self, user):
        aid = user.split("你的ID: ")[1].split("\n")[0]
        goal = user.split("任务: ")[1].split("\n")[0]
        obs = json.loads(user.split("世界状态: ")[1].rsplit("\n", 1)[0])
        item = re.search(r"item:\w+", goal).group(0)
        drop = self._pos(re.search(r"送到 \((\d+),(\d+)\)", goal).group(0).replace("送到 ", ""))
        if not obs[item]["value"].startswith("world@"):
            # 物品已被他人持有/不在世界：出空计划（等待），保持路径可验证
            return json.dumps({"thought": "stub-wait", "keys": [item], "ops": [],
                               "ttl": 5, "priority": 5,
                               "belief": {k: {"value": s["value"], "version": s["version"]}
                                          for k, s in obs.items()
                                          if k.startswith(("item:", "pos:"))}},
                              ensure_ascii=False)
        ipos = self._pos(obs[item]["value"].replace("world@", ""))
        me = self._pos(obs[f"pos:{aid}"]["value"])
        ops = self._moves(me, ipos) + [["grab", {"key": item}]] \
            + self._moves(ipos, drop) + [["place", {"key": item}]]
        belief = {k: {"value": s["value"], "version": s["version"]}
                  for k, s in obs.items() if k.startswith(("item:", "pos:"))}
        return json.dumps({"thought": "stub", "keys": [item], "ops": ops,
                           "ttl": 20, "priority": 5, "belief": belief},
                          ensure_ascii=False)

    @staticmethod
    def _pos(s):
        x, y = s.strip("()").split(",")
        return int(x), int(y)

    @staticmethod
    def _man(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

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


def run_one(baseline, cond, task="T1"):
    clients = {"qwen": StubClient(), "deepseek": StubClient()}
    sim, agents = build_sim(baseline, cond, clients, 0, task)
    try:
        sim.run(80)
    except RuntimeError as e:
        print(f"  !! 仿真异常: {e}")
        return False
    ok, why = check_success(sim, cond, task)
    print(f"  [{baseline}/{cond}] success={ok}{'' if ok else '（' + why + '）'}")
    return ok


def main():
    results = {}
    for bl in ("coagent", "orch", "cnp", "groupchat"):
        for cond in ("C2", "C4"):
            results[f"{bl}/{cond}"] = run_one(bl, cond)
    print("\n==== 冒烟结果 ====")
    for k, v in results.items():
        print(f"  {k:<18} {'PASS' if v else ('FAIL' if v is False else 'ERR')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
