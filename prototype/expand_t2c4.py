"""E6 扩样驱动：T2/C4 四臂 10->30 + PRAP(v0.2.3 口径) T2/C4 n=30。
顺序执行，逐格落盘（run_comparison 自带断点续跑）。用法：python expand_t2c4.py"""
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

JOBS = [
    # E6 四臂续跑（已有 0-9 局，补 10-29）
    ("python", "run_comparison.py --baseline coagent --task T2 --cond C4 --trials 30"),
    ("python", "run_comparison.py --baseline orch --task T2 --cond C4 --trials 30"),
    ("python", "run_comparison.py --baseline cnp --task T2 --cond C4 --trials 30"),
    ("python", "run_comparison.py --baseline groupchat --task T2 --cond C4 --trials 30"),
    # PRAP 对照臂（v0.2.3 清洗器口径，全新 30 局）
    ("python", "run_e1_full.py --task T2 --conds C4 --trials 30 --out e1_T2_C4_n30_v023.json"),
]

for i, (exe, args) in enumerate(JOBS, 1):
    print(f"\n===== [{i}/{len(JOBS)}] {args} =====", flush=True)
    r = subprocess.run([exe, *args.split()], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = (r.stdout or "").strip().splitlines()[-3:]
    print("\n".join(tail), flush=True)
    if r.returncode != 0:
        print(f"!! 退出码 {r.returncode}，stderr 尾部：", flush=True)
        print((r.stderr or "").strip()[-500:], flush=True)

print("\n===== 扩样批次全部完成 =====", flush=True)
