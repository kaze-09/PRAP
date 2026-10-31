"""R2+R5 批次进度。用法：python r2_status.py"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

FILES = [
    ("scale8_f30_prap.json", "prap 8/30"),
    ("scale8_f30_orch.json", "orch 8/30"),
    ("scale8_f30_noproto.json", "noproto 8/30"),
    ("scale8_f30_promptfix.json", "promptfix 8/30"),
    ("scale8_f30_leaseonly.json", "leaseonly 8/30"),
    ("e1_T1_C4_leaseonly_n10.json", "leaseonly T1/C4"),
]
total_done = 0
total_target = 0
for f, label in FILES:
    try:
        d = json.load(open(f, encoding="utf-8"))
    except FileNotFoundError:
        print(f"{label:<18} 未开始")
        continue
    except Exception:
        print(f"{label:<18} 解析失败（正在写入）")
        continue
    n = len(d)
    sr = sum(r["success"] for r in d)
    waste = sum(r.get("wasted_ops", sum(v["failed"] for v in r.get("agent_ops", {}).values()))
                for r in d) / n if n else 0
    tgt = 30 if "scale8" in f else 10
    total_done += n
    total_target += tgt
    print(f"{label:<18}{n:>3}/{tgt}  成功 {sr:>2}  浪费 {waste:.1f}")
print(f"\n总进度 {total_done}/{total_target} 局")
