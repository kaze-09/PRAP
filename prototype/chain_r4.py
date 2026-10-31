"""链式驱动：等待 R3（2 臂 × 30）完成后自动运行 R4（间歇偏置 30 局）。"""
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

while True:
    done = True
    for f in ("r3_prap.json", "r3_coagent.json"):
        try:
            d = json.load(open(f, encoding="utf-8"))
            if len(d) < 30:
                done = False
        except Exception:
            done = False
    if done:
        break
    time.sleep(60)

print("R3 已完成（2 臂 × 30）-> 启动 R4", flush=True)
r = subprocess.run([sys.executable, "run_r4.py", "--trials", "30", "--prob", "0.5"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
print((r.stdout or "").strip().splitlines()[-3:], flush=True)
if r.returncode != 0:
    print((r.stderr or "")[-500:], flush=True)
print("===== R4 完成 =====", flush=True)
