import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
rs = json.load(open("scale8_f30_prap.json", encoding="utf-8"))
print("局数:", len(rs))
for r in rs:
    print(f"t{r['trial']} 成功={int(r['success'])} 送达 {r['delivered']}/{r['n_items']} "
          f"隔离={r['n_quarantined']} tokens={r['tokens']} calls={r['llm_calls']} "
          f"浪费={r['wasted_ops']} 失败原因={r['fail_reason'][:30]}")
