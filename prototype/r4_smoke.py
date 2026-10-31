"""R4 离线冒烟（零 API）：transient 偏置下路径可跑、隔离为间歇性。"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from comp_smoke import StubClient
from run_r4 import build_r4_sim
from run_e1_full import check_success


def run_one(trial, prob):
    clients = {"qwen": StubClient(), "deepseek": StubClient()}
    sim, agents = build_r4_sim(trial, prob, clients)
    try:
        sim.run(80)
    except RuntimeError as e:
        print(f"  !! 异常: {e}")
        return
    ok, why = check_success(sim, "C4", "T1")
    arb = sim.arbiter
    blocked = sum(1 for e in arb.log if e[0] == "intent_suspect_blocked")
    print(f"  [t{trial} p{prob}] success={ok} 隔离拦截={blocked} "
          f"终态隔离={sum(1 for v in arb.agent_status.values() if v == 'suspect')}")


if __name__ == "__main__":
    for t in range(3):
        run_one(t, 0.5)
