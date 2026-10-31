"""R5：T1/C4 lease-only 臂（关闭 L4 的完整租约协议），n=10。
复用 run_e1_full 的场景与记录格式，build_sim 打补丁关闭 l4_enabled。"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

import run_e1_full

_orig = run_e1_full.build_sim


def patched(*a, **k):
    sim, agents, oracle = _orig(*a, **k)
    sim.arbiter.l4_enabled = False   # R5：lease-only 消融
    return sim, agents, oracle


run_e1_full.build_sim = patched
sys.argv = ["run_e1_full.py", "--trials", "10", "--conds", "C4",
            "--task", "T1", "--out", "e1_T1_C4_leaseonly_n10.json"]
sys.exit(run_e1_full.main())
