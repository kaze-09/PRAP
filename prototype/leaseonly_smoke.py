"""R5 lease-only 冒烟（零 API）：
1) scale2 fault=1.0 全员幻觉：PRAP 应全隔离，leaseonly 应零隔离且执行错误算子；
2) T1/C4 单局路径可用。"""
import json
import random
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

import scale_campaign as sc
from comp_smoke import StubClient
from run_e1_full import TASKS

clients = {"qwen": StubClient(), "deepseek": StubClient()}

# --- 1. scale2 全幻觉（fault=1.0）双臂对照 ---
for arm in ("prap", "leaseonly"):
    sim, agents, item_keys = sc.build_scale_sim(2, 1.0, arm, 0, clients)
    sim.run(sc.SPEC_HORIZON)
    arb = sim.arbiter
    nq = sum(1 for v in arb.agent_status.values() if v == "suspect")
    ops = [e for e in arb.log if e[0] in ("op", "op_free")]
    print(f"[{arm}] 隔离数={nq} 算子数={len(ops)} "
          f"l4_enabled={getattr(arb, 'l4_enabled', True)}")

# --- 2. T1/C4 leaseonly 路径 ---
import run_e1_full

_orig = run_e1_full.build_sim


def patched(*a, **k):
    sim, agents, oracle = _orig(*a, **k)
    sim.arbiter.l4_enabled = False
    return sim, agents, oracle


run_e1_full.build_sim = patched
sys.argv = ["run_e1_full.py", "--trials", "1", "--conds", "C4",
            "--task", "T1", "--out", "_smoke_leaseonly_t1c4.json"]
run_e1_full.main()
print("smoke done")
