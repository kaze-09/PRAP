"""T3 链式协调失败模式分析（离线）。

T3：5x5，apple1@(2,3)、apple2@(4,1)，投放 (0,4)，chain=True——
两个 agent 的目标都是"依次把 apple1、apple2 送到 (0,4)"（共享全量目标）。
问题：纯健康组合（C2）为何只有 30% 成功？失败时卡在哪个环节？
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
base = os.path.dirname(os.path.abspath(__file__))


def show(fname, tag):
    path = os.path.join(base, fname)
    if not os.path.exists(path):
        print(f"[skip] {fname}")
        return
    rs = json.load(open(path, encoding="utf-8"))
    print(f"\n== {tag} ({fname}) ==")
    for r in rs:
        ao = r["agent_ops"]
        done = r["completed_at"]
        print(f"t{r['trial']} A={r['models']['A'][:4]} ok={int(r['success'])} "
              f"fail={r['fail_reason'][:44]} | "
              f"A[ops={ao.get('A', {}).get('ops')},fail={ao.get('A', {}).get('failed')}] "
              f"B[ops={ao.get('B', {}).get('ops')},fail={ao.get('B', {}).get('failed')}] | "
              f"doneA={done.get('A')} doneB={done.get('B')} "
              f"replans={r['replans']} denies={r['denies']} grants={r['grants']} "
              f"tokens={r['tokens']}")


show("e1_T3_C2_n10.json", "T3/C2 争用（双健康）")
show("e1_T3_C3_n10.json", "T3/C3 争用+A挂死")
show("e1_T3_C4_n10.json", "T3/C4 争用+B幻觉")

# 失败原因聚类
print("\n== 失败原因聚类 ==")
from collections import Counter
c = Counter()
for f, tag in [("e1_T3_C2_n10.json", "C2"), ("e1_T3_C3_n10.json", "C3"),
               ("e1_T3_C4_n10.json", "C4")]:
    path = os.path.join(base, f)
    if not os.path.exists(path):
        continue
    rs = json.load(open(path, encoding="utf-8"))
    for r in rs:
        if not r["success"]:
            c[(tag, r["fail_reason"].split("（")[0])] += 1
for (tag, why), n in sorted(c.items()):
    print(f"  {tag} {why}: {n}")
