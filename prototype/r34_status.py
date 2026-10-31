"""R3/R4 进度监控。用法：python r34_status.py"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

for f, label, tgt in [("r3_prap.json", "R3 prap", 30),
                      ("r3_coagent.json", "R3 coagent", 30),
                      ("r4_transient_p50.json", "R4 间歇偏置", 30)]:
    try:
        d = json.load(open(f, encoding="utf-8"))
    except FileNotFoundError:
        print(f"{label:<14} 未开始")
        continue
    except Exception:
        print(f"{label:<14} 解析失败（正在写入）")
        continue
    n = len(d)
    sr = sum(r["success"] for r in d)
    extra = ""
    if "final_state" in d[0]:   # R3
        wrong = sum(1 for r in d if "错误位置" in r["fail_reason"])
        extra = f"  不可逆错位 {wrong}"
    else:                        # R4
        blocked = sum(r["blocked_intents"] for r in d)
        final_q = sum(r["n_quarantined_final"] for r in d)
        extra = f"  隔离拦截 {blocked}  终态仍隔离 {final_q}"
    print(f"{label:<14}{n:>3}/{tgt}  成功 {sr:>2}{extra}")
