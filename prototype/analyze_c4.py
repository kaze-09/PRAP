"""分析 C4 v0.1 vs v0.2 失败明细与 E2b 结果（一次性分析脚本）。"""
import json

for fn, tag in [("e1_part_C4.json", "v0.1"), ("e1_part_C4_v02.json", "v0.2")]:
    rs = json.load(open(fn, encoding="utf-8"))
    print(f"== C4 {tag} ==")
    for r in rs:
        mark = "FAIL" if not r["success"] else "ok  "
        print(f"t{r['trial']} [{mark}] fail={r['fail_reason']} "
              f"verdicts={r['verdicts']} completed={r['completed_at']} "
              f"tokensA={r['tokens_by_agent'].get('A')} tokensB={r['tokens_by_agent'].get('B')}")
    print()

e2 = json.load(open("e2_results.json", encoding="utf-8"))
print("== E2b (v0.2) ==")
for r in e2["e2b"]:
    print(f"d={r['d']} t{r['trial']} verdicts={r['verdicts']} success={r['success']} "
          f"failed_ops={r['failed_ops']} "
          f"claim_B={r['claimed'].get('B', {}).get('item:apple1')} "
          f"auth={r['authority']['item:apple1']}")
