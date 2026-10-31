"""P2 orch 臂离线冒烟：scale=2/4 × fault=0.3，StubClient 零 API 验证
分派/执行/失败重分配路径。"""
import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from comp_smoke import StubClient
from run_scale_orch import build_scale_sim_orch
from scale_campaign import SPEC_HORIZON, check_all_delivered


def run_one(scale, fault, trial=0):
    clients = {"qwen": StubClient(), "deepseek": StubClient()}
    sim, agents, item_keys = build_scale_sim_orch(scale, fault, trial, clients)
    try:
        sim.run(SPEC_HORIZON)
    except RuntimeError as e:
        print(f"  !! 仿真异常: {e}")
        return None
    ok, why = check_all_delivered(sim.arbiter, item_keys)
    print(f"  [scale{scale}/f{fault}] success={ok}{'' if ok else '（' + why + '）'}")
    return ok


if __name__ == "__main__":
    run_one(2, 0.0)
    run_one(2, 0.3)
    run_one(4, 0.3)
