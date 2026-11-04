"""写范围试点。口径见 PRAP_repo_splice_protocol.md，跑数前已写死。

全文臂用已落盘的 8 局。拼接臂只让盲写者交出新增函数，旧函数体由运行时照抄。

用法：
  python run_repo_splice.py --self-check
  python run_repo_splice.py
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys

from llm_agent import load_clients
from run_repo_conflict import BASE_TEST, P_TASKS, run_test

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
PRIOR = os.path.join(BASE, "ccf_followup", "repo_conflict.jsonl")
OUT = os.path.join(BASE, "ccf_followup", "repo_splice.jsonl")
SEEDS = (4, 5, 6, 7, 12, 13, 14, 15)

SYSTEM = "你在给 calc.py 新增一个函数。只输出这个新函数，不要输出其他函数，不要解释。"


def task_for(seed: int) -> dict:
    return P_TASKS[(seed // 2) % len(P_TASKS)]


def new_name(task: dict) -> str:
    return "tax" if task["id"] == "discount" else "twice"


def extract_func(text: str, name: str) -> str | None:
    start = text.find("NEW_FUNC_BEGIN")
    end = text.find("NEW_FUNC_END")
    body = text[start + len("NEW_FUNC_BEGIN"):end] if start >= 0 and end > start else text
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(body, node)
    return None


def splice(original: str, func_src: str) -> str:
    base = original if original.endswith("\n") else original + "\n"
    return base + "\n" + func_src.strip() + "\n"


def hidden_ok(src: str, task: dict) -> tuple[bool, str]:
    return run_test(src, BASE_TEST + task["hidden"])


def own_ok(src: str, task: dict) -> bool:
    ok, _ = run_test(src, BASE_TEST + task["hidden"] + task["test_B"])
    return ok


def ask(client, task: dict) -> str:
    listed = "\n".join(f"- {name}" for name in task["names"])
    user = (
        "你看不到 calc.py 的函数体，只能看到这些函数名：\n"
        f"{listed}\n"
        f"你的任务: {task['B']}\n"
        "只输出要新增的那一个函数，放在标记之间：\n"
        "NEW_FUNC_BEGIN\n"
        "def ...\n"
        "NEW_FUNC_END\n"
    )
    return client.chat(SYSTEM, user, max_tokens=800)


def prior_failures() -> int:
    rows = [json.loads(line) for line in open(PRIOR, encoding="utf-8") if line.strip()]
    bad = 0
    for row in rows:
        if row["cell"] != "P" or row["seed"] not in SEEDS:
            continue
        first = next(w for w in row["writes"] if w["agent"] == "B" and w["phase"] == "first")
        bad += int(not first.get("base_ok", False))
    return bad


def decide(rows: list[dict]) -> str:
    if len(rows) < len(SEEDS):
        return "INCOMPLETE"
    return "STOP" if sum(1 for r in rows if not r["hidden_ok"]) == 0 else "CANDIDATE"


def self_check() -> int:
    task = P_TASKS[0]
    original = task["body"]
    invented = (
        "def apply_discount(price, rate):\n"
        "    return price * (1 - rate)\n"
        "def tax(price):\n"
        "    return apply_discount(price, 10)\n"
    )
    func = extract_func(invented, "tax")
    assert func is not None and "def tax" in func and "1 - rate" not in func, func
    merged = splice(original, func)
    ok, why = hidden_ok(merged, task)
    assert ok, why
    assert own_ok(merged, task)
    assert extract_func("def fee(price):\n    return 1\n", "tax") is None
    print("self-check ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        return self_check()
    rows = []
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    done = {r["seed"] for r in rows}
    clients = load_clients()
    if "qwen" not in clients or "deepseek" not in clients:
        print("缺少 qwen 或 deepseek")
        return 2
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "a", encoding="utf-8") as out:
        for seed in SEEDS:
            if seed in done:
                continue
            task = task_for(seed)
            client = clients["qwen"] if seed % 2 == 0 else clients["deepseek"]
            print(f"\n== splice seed {seed} {client.name} {task['id']} ==", flush=True)
            text = ask(client, task)
            func = extract_func(text, new_name(task))
            original = f"# episode {seed}\n" + task["body"]
            merged = splice(original, func) if func else original
            preserved, why = hidden_ok(merged, task)
            rec = {
                "seed": seed, "model": client.model, "task": task["id"],
                "extracted": func is not None, "hidden_ok": preserved,
                "own_ok": own_ok(merged, task) if func else False,
                "why": why, "func": func,
            }
            print(f"  extracted={int(rec['extracted'])} hidden={int(preserved)} "
                  f"own={int(rec['own_ok'])} {why}", flush=True)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            rows.append(rec)
    failed = sum(1 for r in rows if not r["hidden_ok"])
    own = sum(1 for r in rows if r["own_ok"])
    print("\n================ 写范围试点 ================")
    print(f"全文臂（已落盘）隐藏契约失败 {prior_failures()}/8")
    print(f"拼接臂 隐藏契约失败 {failed}/{len(rows)}  新函数测试通过 {own}/{len(rows)}")
    print(f"判定: {decide(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
