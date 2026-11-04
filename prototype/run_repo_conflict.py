"""仓库冲突试点。口径见 PRAP_repo_conflict_protocol.md，跑数前已写死。

两格：
  S  两人改同一个函数，规格不能同时成立
  P  B 第一次只看函数名，A 看全文；被拒后重做才看到全文

用法：
  python run_repo_conflict.py --self-check
  python run_repo_conflict.py --trials 16
  python run_repo_conflict.py --summarize
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

from llm_agent import load_clients
from run_repo_belief import parse_plan, safe_source

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "ccf_followup", "repo_conflict.jsonl")

SYSTEM = (
    "你在修改一个只有一个文件 calc.py 的仓库。"
    "按标记输出你认为的当前全文，以及修改后的完整文件。"
    "不要解释。"
)

BASE_TEST = "assert add(1, 2) == 3\nassert mul(3, 4) == 12\n"

S_TASKS = [
    {
        "id": "score",
        "body": "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n",
        "A": "新增函数 score(a, b)，返回 a - b。不要改 add 和 mul。",
        "B": "新增函数 score(a, b)，返回 b - a。不要改 add 和 mul。",
        "test_A": "assert score(5, 2) == 3\nassert score(2, 5) == -3\n",
        "test_B": "assert score(5, 2) == -3\nassert score(2, 5) == 3\n",
    },
    {
        "id": "greet",
        "body": "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n",
        "A": '新增函数 greet(name)，返回 "hi " 接上 name。不要改 add 和 mul。',
        "B": '新增函数 greet(name)，返回 "hello " 接上 name。不要改 add 和 mul。',
        "test_A": 'assert greet("Ann") == "hi Ann"\n',
        "test_B": 'assert greet("Ann") == "hello Ann"\n',
    },
]

P_TASKS = [
    {
        "id": "discount",
        "body": (
            "def add(a, b):\n    return a + b\n\n"
            "def mul(a, b):\n    return a * b\n\n"
            "def apply_discount(price, rate):\n"
            "    if rate < 0 or rate > 100:\n"
            "        return None\n"
            "    return price * (100 - rate) / 100\n"
        ),
        "names": ["add(a, b)", "mul(a, b)", "apply_discount(price, rate)"],
        "A": "新增函数 fee(price)，返回 apply_discount(price, 20)。不要改变已有函数的行为。",
        "B": "新增函数 tax(price)，返回 apply_discount(price, 10)。不要改变已有函数的行为。",
        "hidden": (
            "assert apply_discount(200, 10) == 180 or apply_discount(200, 10) == 180.0\n"
            "assert apply_discount(200, 100) == 0 or apply_discount(200, 100) == 0.0\n"
            "assert apply_discount(200, -1) is None\n"
        ),
        "test_A": "assert fee(200) == 160 or fee(200) == 160.0\n",
        "test_B": "assert tax(200) == 180 or tax(200) == 180.0\n",
    },
    {
        "id": "bucket",
        "body": (
            "def add(a, b):\n    return a + b\n\n"
            "def mul(a, b):\n    return a * b\n\n"
            "def bucket(n):\n"
            "    if n < 0:\n"
            "        return None\n"
            "    return n // 3\n"
        ),
        "names": ["add(a, b)", "mul(a, b)", "bucket(n)"],
        "A": "新增函数 triple(n)。n < 0 时返回 None，否则返回 bucket(n) * 3。不要改变已有函数的行为。",
        "B": "新增函数 twice(n)。n < 0 时返回 None，否则返回 bucket(n) * 2。不要改变已有函数的行为。",
        "hidden": "assert bucket(10) == 3\nassert bucket(0) == 0\nassert bucket(-1) is None\n",
        "test_A": "assert triple(10) == 9\nassert triple(-1) is None\n",
        "test_B": "assert twice(10) == 6\nassert twice(-1) is None\n",
    },
]


def digest(src: str) -> str:
    text = src.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    norm = "\n".join(lines) + ("\n" if lines else "")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def run_test(src: str, test: str) -> tuple[bool, str]:
    if not safe_source(src):
        return False, "unsafe_or_too_long"
    payload = json.dumps({"src": src, "test": test})
    child = (
        "import json,sys\n"
        "p=json.load(sys.stdin)\n"
        "ns={}\n"
        "exec(p['src'], ns, ns)\n"
        "exec(p['test'], ns, ns)\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", child],
            input=payload.encode("utf-8"),
            capture_output=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    if proc.returncode == 0:
        return True, "ok"
    err = (proc.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
    return False, (err[-1] if err else "fail")[:180]


def ask(client, user: str) -> tuple[dict | None, str]:
    last = ""
    prompt = user
    for _ in range(2):
        last = client.chat(SYSTEM, prompt, max_tokens=1800)
        plan = parse_plan(last)
        if plan:
            return plan, last
        prompt = user + "\n上次输出无法解析。请只按标记再输出一次。"
    return None, last


def full_user(version: int, source: str, instruction: str) -> str:
    return (
        f"当前权威版本: {version}\n"
        "当前 calc.py 如下，标记之间是文件全文。\n"
        "FILE_BEGIN\n"
        f"{source}"
        "FILE_END\n"
        f"你的任务: {instruction}\n"
        "按下面的格式输出，文件里不要包含这些标记：\n"
        "BELIEVED_VERSION: <整数>\n"
        "BELIEVED_SOURCE_BEGIN\n<你认为的当前文件全文，修改前>\nBELIEVED_SOURCE_END\n"
        "NEW_SOURCE_BEGIN\n<修改后的完整文件>\nNEW_SOURCE_END\n"
    )


def summary_user(version: int, names: list[str], instruction: str) -> str:
    listed = "\n".join(f"- {name}" for name in names)
    return (
        f"当前权威版本: {version}\n"
        "你看不到 calc.py 的函数体，只能看到下面这些函数名：\n"
        f"{listed}\n"
        f"你的任务: {instruction}\n"
        "你仍然要输出完整文件。按下面的格式，文件里不要包含这些标记：\n"
        "BELIEVED_VERSION: <整数>\n"
        "BELIEVED_SOURCE_BEGIN\n<你认为的当前文件全文，修改前>\nBELIEVED_SOURCE_END\n"
        "NEW_SOURCE_BEGIN\n<修改后的完整文件>\nNEW_SOURCE_END\n"
    )


def judge_s(src: str, task: dict) -> dict:
    base_ok, base_why = run_test(src, BASE_TEST)
    a_ok, _ = run_test(src, BASE_TEST + task["test_A"])
    b_ok, _ = run_test(src, BASE_TEST + task["test_B"])
    neither = base_ok and not a_ok and not b_ok
    bad = (not base_ok) or neither
    return {"base_ok": base_ok, "spec_A": a_ok, "spec_B": b_ok,
            "neither": neither, "bad": bad, "own_fail": False, "why": base_why}


def judge_p(src: str, task: dict, who: str) -> dict:
    preserve = BASE_TEST + task["hidden"]
    base_ok, why = run_test(src, preserve)
    own = task["test_A"] if who == "A" else task["test_B"]
    own_ok, own_why = run_test(src, preserve + own)
    return {"base_ok": base_ok, "spec_A": who == "A" and own_ok,
            "spec_B": who == "B" and own_ok, "neither": False,
            "bad": not base_ok, "own_fail": base_ok and not own_ok,
            "why": why if not base_ok else own_why}


def admit(authority: dict, plan: dict | None, cell: str, task: dict, who: str) -> dict:
    if plan is None:
        return {"admitted": False, "bad": False, "own_fail": False, "class": "unparsed",
                "version": None, "why": "unparsed", "source": None}
    version_ok = plan["believed_version"] == authority["version"]
    if not version_ok:
        label = "stale" if plan["believed_version"] < authority["version"] else "version_ahead"
        return {"admitted": False, "bad": False, "own_fail": False, "class": label,
                "version": plan["believed_version"], "why": "", "source": None}
    src = plan["new_source"]
    if not src.endswith("\n"):
        src += "\n"
    verdict = judge_s(src, task) if cell == "S" else judge_p(src, task, who)
    authority["source"] = src
    authority["version"] += 1
    authority["digest"] = digest(src)
    return {"admitted": True, "bad": verdict["bad"], "own_fail": verdict["own_fail"],
            "class": "bad_admit" if verdict["bad"] else "admitted",
            "version": plan["believed_version"], "why": verdict["why"],
            "base_ok": verdict["base_ok"], "spec_A": verdict["spec_A"],
            "spec_B": verdict["spec_B"], "neither": verdict["neither"],
            "source": src}


def run_episode(cell: str, seed: int, client) -> dict:
    tasks = S_TASKS if cell == "S" else P_TASKS
    task = tasks[(seed // 2) % len(tasks)]
    source = f"# episode {seed}\n" + task["body"]
    authority = {"version": 1, "source": source, "digest": digest(source)}
    order = ["A", "B"] if (seed // 4) % 2 == 0 else ["B", "A"]
    plans = {}
    for name in order:
        if cell == "P" and name == "B":
            user = summary_user(1, task["names"], task["B"])
        else:
            user = full_user(1, source, task[name])
        plans[name] = ask(client, user)
    writes = []
    for name in order:
        outcome = admit(authority, plans[name][0], cell, task, name)
        outcome["agent"] = name
        outcome["phase"] = "first"
        writes.append(outcome)
        print(f"  {name} {outcome['class']} bad={int(outcome['bad'])} "
              f"own={int(outcome['own_fail'])} {outcome['why'][:60]}", flush=True)
    second = order[1]
    if not writes[-1]["admitted"]:
        plan, raw = ask(client, full_user(authority["version"], authority["source"], task[second]))
        outcome = admit(authority, plan, cell, task, second)
        outcome["agent"] = second
        outcome["phase"] = "retry"
        outcome["raw_tail"] = (raw or "")[-200:]
        writes.append(outcome)
        print(f"  {second} retry {outcome['class']} bad={int(outcome['bad'])} "
              f"own={int(outcome['own_fail'])}", flush=True)
    return {
        "cell": cell, "seed": seed, "model": client.model, "task": task["id"],
        "order": order, "final_version": authority["version"],
        "bad_admit": sum(1 for w in writes if w["bad"]),
        "own_fail": sum(1 for w in writes if w["own_fail"]),
        "admitted": sum(1 for w in writes if w["admitted"]),
        "writes": [{k: v for k, v in w.items() if k != "source"} | (
            {"source": w["source"]} if w.get("bad") else {}) for w in writes],
    }


def decide(rows: list[dict]) -> dict:
    out = {}
    for cell in ("S", "P"):
        got = [r for r in rows if r["cell"] == cell]
        admitted = sum(r["admitted"] for r in got)
        bad = sum(r["bad_admit"] for r in got)
        own = sum(r["own_fail"] for r in got)
        if admitted < 4:
            status = "INCONCLUSIVE"
        elif bad == 0:
            status = "STOP"
        else:
            status = "CANDIDATE"
        out[cell] = {"episodes": len(got), "admitted": admitted, "bad_admit": bad,
                     "own_fail": own, "status": status}
    statuses = {v["status"] for v in out.values()}
    if statuses <= {"STOP"} or statuses <= {"STOP", "INCONCLUSIVE"}:
        topic = "STOP"
    elif "CANDIDATE" in statuses:
        topic = "CANDIDATE"
    else:
        topic = "INCONCLUSIVE"
    return {"cells": out, "topic": topic}


def summarize(rows: list[dict]) -> str:
    verdict = decide(rows)
    lines = ["", "================ 仓库冲突试点 ================"]
    for cell, info in verdict["cells"].items():
        lines.append(
            f"{cell}  局 {info['episodes']}  准入 {info['admitted']}  "
            f"坏写入 {info['bad_admit']}  自身错误 {info['own_fail']}  {info['status']}")
    lines.append(f"整题: {verdict['topic']}")
    return "\n".join(lines)


def self_check() -> int:
    score = S_TASKS[0]
    good_a = score["body"] + "\ndef score(a, b):\n    return a - b\n"
    only_a = judge_s(good_a, score)
    assert only_a["base_ok"] and only_a["spec_A"] and not only_a["bad"], only_a
    broken = "def add(a, b):\n    return 0\n\ndef mul(a, b):\n    return a * b\n\ndef score(a, b):\n    return a - b\n"
    assert judge_s(broken, score)["bad"]
    neither = score["body"] + "\ndef score(a, b):\n    return 0\n"
    got = judge_s(neither, score)
    assert got["neither"] and got["bad"], got

    discount = P_TASKS[0]
    invented = (
        "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n\n"
        "def apply_discount(price, rate):\n    return price * (1 - rate)\n\n"
        "def tax(price):\n    return apply_discount(price, 10)\n"
    )
    blind = judge_p(invented, discount, "B")
    assert blind["bad"] and not blind["base_ok"], blind
    kept = discount["body"] + "\ndef tax(price):\n    return apply_discount(price, 10)\n"
    held = judge_p(kept, discount, "B")
    assert held["base_ok"] and not held["bad"] and not held["own_fail"], held

    authority = {"version": 1, "source": score["body"], "digest": digest(score["body"])}
    stale = admit(authority, {"believed_version": 0, "believed_source": "", "new_source": good_a},
                  "S", score, "A")
    assert not stale["admitted"] and authority["version"] == 1, stale
    bad = admit({"version": 1, "source": score["body"], "digest": digest(score["body"])},
                {"believed_version": 1, "believed_source": score["body"], "new_source": neither},
                "S", score, "A")
    assert bad["admitted"] and bad["bad"], bad
    fake = [
        {"cell": "S", "admitted": 2, "bad_admit": 0, "own_fail": 1},
        {"cell": "S", "admitted": 2, "bad_admit": 0, "own_fail": 0},
        {"cell": "P", "admitted": 1, "bad_admit": 0, "own_fail": 0},
    ]
    assert decide(fake)["topic"] == "STOP", decide(fake)
    assert decide(fake)["cells"]["P"]["status"] == "INCONCLUSIVE"
    fake[0]["bad_admit"] = 1
    assert decide(fake)["topic"] == "CANDIDATE"
    print("self-check ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--summarize", action="store_true")
    ap.add_argument("--trials", type=int, default=16)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    if args.self_check:
        return self_check()
    rows = []
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    if args.summarize:
        print(summarize(rows))
        return 0
    clients = load_clients()
    if "qwen" not in clients or "deepseek" not in clients:
        print("缺少 qwen 或 deepseek")
        return 2
    done = {(r["cell"], r["seed"]) for r in rows}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "a", encoding="utf-8") as out:
        for cell in ("S", "P"):
            for seed in range(args.trials):
                if (cell, seed) in done:
                    continue
                client = clients["qwen"] if seed % 2 == 0 else clients["deepseek"]
                print(f"\n== {cell} seed {seed} {client.name} ==", flush=True)
                for c in clients.values():
                    c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
                rec = run_episode(cell, seed, client)
                rec["llm_calls"] = client.usage["calls"]
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                rows.append(rec)
    print(summarize(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
