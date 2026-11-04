"""离线门槛 1：用已有 T2/C4 计划做效果预演。

预演规则（不调用模型）：在该计划提交时的权威位置上把算子空跑一遍。
若计划里的 place 不在投放点 (4,0)，或 grab 不在物品当时的位置，则拒绝。
只统计健康方 A。B 在 C4 里带着注入信念，不拿来证明「自然滑差」。

通过条件：能拦下的坏计划不只一种，而且不只来自 deepseek 的 (4,1)。
"""

from __future__ import annotations

import json
import os
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
DROP = (4, 0)
START = {"A": (0, 4), "B": (2, 0)}
FILES = (
    "e1_T2_C4_n30_v023.json",
    "e1_T2_C4_n10_debug.json",
)


def forecast(ops, pos, item):
    x, y = pos
    item_pos = item
    held = False
    place_at = grab_at = None
    oob = False
    for op in ops:
        name, args = op[0], op[1]
        if name == "move":
            x += int(args.get("dx", 0))
            y += int(args.get("dy", 0))
            if not (0 <= x < 5 and 0 <= y < 5):
                oob = True
        elif name == "grab":
            grab_at = (x, y)
            if item_pos == (x, y) and not held:
                held = True
                item_pos = None
        elif name == "place":
            place_at = (x, y)
            if held:
                held = False
                item_pos = (x, y)
    return {"place_at": place_at, "grab_at": grab_at, "item": item_pos, "oob": oob}


def apply_ok(pos, item, hold, event):
    if not event["ok"]:
        return pos, item, hold
    name, args = event["op"], event["args"]
    agent = event["agent"]
    x, y = pos[agent]
    if name == "move":
        pos[agent] = (x + int(args.get("dx", 0)), y + int(args.get("dy", 0)))
    elif name == "grab" and item == (x, y) and hold[agent] is None:
        hold[agent] = args.get("key")
        item = None
    elif name == "place" and hold[agent] is not None:
        item = pos[agent]
        hold[agent] = None
    return pos, item, hold


def replay_state(op_seq, stop: int):
    pos = {k: v for k, v in START.items()}
    item = (1, 3)
    hold = {"A": None, "B": None}
    for event in op_seq[:stop]:
        pos, item, hold = apply_ok(pos, item, hold, event)
    return pos, item


def agent_global_indexes(op_seq, agent: str) -> list[int]:
    return [i for i, event in enumerate(op_seq) if event["agent"] == agent]


def same_op(plan_op, event) -> bool:
    name, args = plan_op[0], plan_op[1]
    if event["op"] != name:
        return False
    if name == "move":
        return (int(args.get("dx", 0)), int(args.get("dy", 0))) == (
            int(event["args"].get("dx", 0)), int(event["args"].get("dy", 0)))
    return args.get("key") == event["args"].get("key")


def analyze_file(path: str) -> dict:
    rows = json.load(open(path, encoding="utf-8"))
    blocked = []
    false_positives = []
    missed_failures = []
    for row in rows:
        op_seq = row.get("op_seq") or []
        plans = (row.get("plans") or {}).get("A") or []
        indexes = agent_global_indexes(op_seq, "A")
        cursor = 0
        caught_this = False
        for plan in plans:
            ops = plan.get("ops") or []
            global_at = indexes[cursor] if cursor < len(indexes) else len(op_seq)
            pos, item = replay_state(op_seq, global_at)
            pred = forecast(ops, pos["A"], item if item is not None else (-1, -1))
            executed = cursor < len(indexes) and _matches(ops, op_seq, indexes, cursor)
            would_block = False
            reason = ""
            if pred["place_at"] is not None and pred["place_at"] != DROP:
                would_block, reason = True, f"place@{pred['place_at']}"
            elif pred["grab_at"] is not None and item is not None and pred["grab_at"] != item:
                would_block, reason = True, f"grab@{pred['grab_at']}"
            elif pred["oob"]:
                would_block, reason = True, "oob"
            if would_block:
                caught_this = True
                blocked.append({
                    "file": os.path.basename(path),
                    "trial": row["trial"],
                    "model": row["models"]["A"],
                    "success": row["success"],
                    "reason": reason,
                    "replan": plan.get("replan", 0),
                    "executed": executed,
                })
            if executed:
                cursor += len(ops)
                end = indexes[cursor - 1] + 1 if cursor <= len(indexes) else len(op_seq)
                _, item_after = replay_state(op_seq, end)
                if would_block and item_after == DROP:
                    false_positives.append((row["trial"], row["models"]["A"], reason))
        if not row["success"] and not caught_this:
            missed_failures.append({
                "trial": row["trial"],
                "model": row["models"]["A"],
                "fail_reason": row.get("fail_reason"),
            })
    by_model_reason = Counter((b["model"], b["reason"]) for b in blocked)
    failed = [r for r in rows if not r["success"]]
    failed_caught = {b["trial"] for b in blocked if not b["success"]}
    return {
        "file": os.path.basename(path),
        "n": len(rows),
        "failures": len(failed),
        "failures_caught": len(failed_caught),
        "blocked_plans": len(blocked),
        "false_positives": len(false_positives),
        "patterns": [
            {"model": m, "reason": reason, "n": n}
            for (m, reason), n in sorted(by_model_reason.items())
        ],
        "missed_failures": missed_failures,
    }


def _matches(ops, op_seq, indexes, cursor) -> bool:
    if cursor + len(ops) > len(indexes):
        return False
    for k, op in enumerate(ops):
        if not same_op(op, op_seq[indexes[cursor + k]]):
            return False
    return True


def main() -> int:
    reports = []
    print("== T2/C4 效果预演离线回放（只看健康方 A）==")
    for name in FILES:
        report = analyze_file(os.path.join(BASE, name))
        reports.append(report)
        print(f"\n{report['file']}: {report['n']} 局, 失败 {report['failures']}, "
              f"预演能碰上的失败局 {report['failures_caught']}, "
              f"拦下的计划 {report['blocked_plans']}, 误伤 {report['false_positives']}")
        for pat in report["patterns"]:
            print(f"  {pat['model']:16} {pat['reason']:16} ×{pat['n']}")
        if report["missed_failures"]:
            print(f"  没拦住的失败局: {report['missed_failures']}")
    reasons = {(p["model"], p["reason"]) for rep in reports for p in rep["patterns"]}
    only_one_bug = reasons <= {("deepseek-chat", "place@(4, 1)")}
    caught = sum(rep["failures_caught"] for rep in reports)
    passed = caught > 0 and not only_one_bug and len(reasons) >= 2
    print("\n不同的(模型, 原因)组合:", len(reasons))
    print("门槛 1:", "PASS" if passed else "FAIL")
    out = os.path.join(BASE, "ccf_followup", "effect_preview.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"passed": passed, "reports": reports}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
