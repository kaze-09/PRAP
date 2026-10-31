"""R2+R5 批次驱动（预注册 PRAP_prereg_R1.md）：
1) scale8/f30 四臂续跑至 n=30（prap/noproto/promptfix/orch 各补 20）
2) scale8/f30 leaseonly 全新 30 局
3) T1/C4 leaseonly n=10
顺序执行，逐格落盘断点续跑。"""
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

JOBS = [
    ("python", "scale_campaign.py --arms prap --scales 8 --faults 30 --trials 30"),
    ("python", "scale_campaign.py --arms noproto --scales 8 --faults 30 --trials 30"),
    ("python", "scale_campaign.py --arms promptfix --scales 8 --faults 30 --trials 30"),
    ("python", "run_scale_orch.py --scales 8 --faults 30 --trials 30"),
    ("python", "scale_campaign.py --arms leaseonly --scales 8 --faults 30 --trials 30"),
    ("python", "run_leaseonly_t1c4.py"),
]

for i, (exe, args) in enumerate(JOBS, 1):
    print(f"\n===== [{i}/{len(JOBS)}] {args} =====", flush=True)
    r = subprocess.run([exe, *args.split()], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = (r.stdout or "").strip().splitlines()[-3:]
    print("\n".join(tail), flush=True)
    if r.returncode != 0:
        print(f"!! 退出码 {r.returncode}，stderr 尾部：", flush=True)
        print((r.stderr or "").strip()[-600:], flush=True)

print("\n===== R2+R5 批次全部完成 =====", flush=True)
