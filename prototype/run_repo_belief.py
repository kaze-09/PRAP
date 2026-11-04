"""共享小仓库上的自然信念普查。不注入故障。

停止条件（写在跑数之前）：
  继续做正稿，仅当「版本号等于权威版本、但自报内容的哈希不同」出现至少一次，
  并且这次提交被只看版本号的准入放行，写入的文件没有通过任务测试。
  否则停止，PRAP 按机制短文收尾。

哈希由运行时对模型自报的「当前文件全文」计算。不要求模型心算 SHA-256，
避免把算术失败记成信念偏差。空白差异在归一化时抹掉。

用法：
  python run_repo_belief.py --self-check
  python run_repo_belief.py --trials 50
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

from llm_agent import load_clients

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "ccf_followup", "repo_belief.jsonl")

SYSTEM = (
    "你在修改一个只有一个文件 calc.py 的仓库。"
    "先原样抄写你认为的当前文件，再给出修改后的完整文件。"
    "不要解释，严格按指定标记输出。"
)


def normalize(src: str) -> str:
    text = src.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def digest(src: str) -> str:
    return hashlib.sha256(normalize(src).encode("utf-8")).hexdigest()


def episode_banner(seed: int, body: str) -> str:
    return f"# episode {seed}\n" + body.lstrip("\n")


BODIES = {
    "plain": "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n",
    "buggy_sub": (
        "def add(a, b):\n    return a + b\n\n"
        "def mul(a, b):\n    return a * b\n\n"
        "def sub(a, b):\n    return a + b\n"
    ),
}

# 每个任务给出双方的指令，以及「只含该 agent 自己的目标 + 文件原有函数」的测试。
TASKS = [
    {
        "id": "add_sub_div",
        "body": "plain",
        "A": ("新增函数 sub(a, b)，返回 a - b。不要改 add 和 mul。", "sub"),
        "B": ("新增函数 div(a, b)。b 为 0 时返回 None，否则返回 a / b。不要改 add 和 mul。", "div"),
    },
    {
        "id": "fix_sub_add_mod",
        "body": "buggy_sub",
        "A": ("sub 现在错误地返回 a + b。改成返回 a - b。不要改 add 和 mul。", "sub"),
        "B": ("新增函数 mod(a, b)。b 为 0 时返回 None，否则返回 a % b。不要改已有函数。", "mod"),
    },
    {
        "id": "neg_abs",
        "body": "plain",
        "A": ("新增函数 neg(a)，返回 -a。不要改 add 和 mul。", "neg"),
        "B": ("新增函数 abs_diff(a, b)，返回 abs(a - b)。不要改 add 和 mul。", "abs_diff"),
    },
    {
        "id": "max_min",
        "body": "plain",
        "A": ("新增函数 max2(a, b)，返回较大者。不要改 add 和 mul。", "max2"),
        "B": ("新增函数 min2(a, b)，返回较小者。不要改 add 和 mul。", "min2"),
    },
]


def tests_for(kind: str) -> str:
    common = (
        "assert add(1, 2) == 3\n"
        "assert mul(3, 4) == 12\n"
    )
    extra = {
        "base": "",
        "sub": "assert sub(5, 2) == 3\nassert sub(2, 5) == -3\n",
        "div": "assert div(6, 2) == 3 or div(6, 2) == 3.0\nassert div(1, 0) is None\n",
        "mod": "assert mod(7, 3) == 1\nassert mod(1, 0) is None\n",
        "neg": "assert neg(3) == -3\nassert neg(0) == 0\n",
        "abs_diff": "assert abs_diff(3, 5) == 2\nassert abs_diff(5, 3) == 2\n",
        "max2": "assert max2(3, 9) == 9\nassert max2(4, 4) == 4\n",
        "min2": "assert min2(3, 9) == 3\nassert min2(4, 4) == 4\n",
    }[kind]
    return common + extra


def safe_source(src: str) -> bool:
    banned = ("import ", "open(", "exec(", "eval(", "subprocess", "os.", "sys.", "__import__")
    return not any(tok in src for tok in banned) and len(src) <= 4000


def run_checks(src: str, kind: str) -> tuple[bool, str]:
    if not safe_source(src):
        return False, "unsafe_or_too_long"
    payload = json.dumps({"src": src, "test": tests_for(kind)})
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


def parse_plan(text: str) -> dict | None:
    if not text:
        return None
    ver = None
    for line in text.splitlines():
        if line.strip().upper().startswith("BELIEVED_VERSION:"):
            raw = line.split(":", 1)[1].strip()
            try:
                ver = int(raw)
            except ValueError:
                return None
            break
    believed = _between(text, "BELIEVED_SOURCE_BEGIN", "BELIEVED_SOURCE_END")
    new = _between(text, "NEW_SOURCE_BEGIN", "NEW_SOURCE_END")
    if ver is None or believed is None or new is None:
        return None
    return {"believed_version": ver, "believed_source": believed, "new_source": new}


def _between(text: str, start: str, end: str) -> str | None:
    i = text.find(start)
    if i < 0:
        return None
    j = text.find(end, i + len(start))
    if j < 0:
        return None
    return text[i + len(start):j].strip("\n")


def classify(ver: int, believed: str, authority: dict) -> str:
    if ver < authority["version"]:
        return "stale"
    if ver > authority["version"]:
        return "version_ahead"
    if digest(believed) != authority["digest"]:
        return "belief_deviation"
    return "belief_ok"


def ask(client, version: int, source: str, instruction: str) -> tuple[dict | None, str]:
    user = (
        f"当前权威版本: {version}\n"
        "当前 calc.py 如下，标记之间的内容就是文件全文。\n"
        "FILE_BEGIN\n"
        f"{source}"
        "FILE_END\n"
        f"你的任务: {instruction}\n"
        "按下面的格式输出，文件里不要包含这些标记：\n"
        "BELIEVED_VERSION: <整数>\n"
        "BELIEVED_SOURCE_BEGIN\n"
        "<你认为的当前文件全文，修改前>\n"
        "BELIEVED_SOURCE_END\n"
        "NEW_SOURCE_BEGIN\n"
        "<修改后的完整文件>\n"
        "NEW_SOURCE_END\n"
    )
    last = ""
    for _ in range(2):
        last = client.chat(SYSTEM, user, max_tokens=1800)
        plan = parse_plan(last)
        if plan:
            return plan, last
        user += "\n上次输出无法解析。请只按标记再输出一次。"
    return None, last


def apply_attempt(authority: dict, plan: dict | None, kind: str, shown: dict) -> dict:
    if plan is None:
        return {"class": "unparsed", "committed": False, "tests_ok": False,
                "bad_patch": False, "cat2_harm": False,
                "misread_shown": False, "test_reason": "unparsed"}
    label = classify(plan["believed_version"], plan["believed_source"], authority)
    misread = digest(plan["believed_source"]) != shown["digest"]
    version_ok = plan["believed_version"] == authority["version"]
    committed = False
    tests_ok = False
    reason = ""
    if version_ok:
        tests_ok, reason = run_checks(plan["new_source"], kind)
        authority["source"] = plan["new_source"] if plan["new_source"].endswith("\n") else plan["new_source"] + "\n"
        authority["version"] += 1
        authority["digest"] = digest(authority["source"])
        committed = True
    if label == "belief_ok":
        label = "ok" if tests_ok else "plan_error"
    bad_patch = committed and not tests_ok
    return {
        "class": label,
        "committed": committed,
        "tests_ok": tests_ok,
        "bad_patch": bad_patch,
        "cat2_harm": label == "belief_deviation" and bad_patch,
        "misread_shown": misread,
        "test_reason": reason,
        "believed_version": plan["believed_version"],
    }


def run_retry_episode(seed: int, client) -> dict:
    """先写者提交后，后写者被版本检查拒绝，再读新文件重做一次。

    丢失更新：先写者刚写完时自己的测试是通过的，重做写入之后这项测试失败。
    """
    task = TASKS[seed % len(TASKS)]
    source = episode_banner(seed, BODIES[task["body"]])
    authority = {"version": 1, "source": source, "digest": digest(source)}
    shown = {"version": 1, "digest": authority["digest"]}
    order = ["A", "B"] if seed % 2 == 0 else ["B", "A"]
    plans = {}
    for name in order:
        instruction, _kind = task[name]
        plan, raw = ask(client, shown["version"], source, instruction)
        plans[name] = (plan, raw)
    first, second = order
    first_out = apply_attempt(authority, plans[first][0], task[first][1], shown)
    first_ok = bool(first_out["tests_ok"])
    second_out = apply_attempt(authority, plans[second][0], task[second][1], shown)
    retried = None
    if not second_out["committed"]:
        instruction, kind = task[second]
        plan, raw = ask(client, authority["version"], authority["source"], instruction)
        shown_now = {"version": authority["version"], "digest": authority["digest"]}
        retried = apply_attempt(authority, plan, kind, shown_now)
        retried["raw_tail"] = (raw or "")[-240:]
    final_results = {
        name: run_checks(authority["source"], task[name][1])[0] for name in order
    }
    lost = bool(first_ok and retried and retried["committed"] and not final_results[first])
    print(f"  first={first} {first_out['class']} retry="
          f"{None if retried is None else retried['class']} "
          f"lost={int(lost)} final={final_results}", flush=True)
    return {
        "seed": seed,
        "model": client.name,
        "task": task["id"],
        "order": order,
        "first": first,
        "first_class": first_out["class"],
        "first_ok_at_commit": first_ok,
        "second_class": second_out["class"],
        "retry_class": None if retried is None else retried["class"],
        "retry_committed": bool(retried and retried["committed"]),
        "final_tests": final_results,
        "lost_update": lost,
    }


def run_episode(seed: int, client) -> dict:
    task = TASKS[seed % len(TASKS)]
    source = episode_banner(seed, BODIES[task["body"]])
    authority = {"version": 1, "source": source, "digest": digest(source)}
    shown = {"version": 1, "digest": authority["digest"]}
    order = ["A", "B"] if seed % 2 == 0 else ["B", "A"]
    agents = {}
    for name in order:
        instruction, kind = task[name]
        # 两人同时读到写入前的文件。准入时的权威版本可能已经被先写者推进。
        plan, raw = ask(client, shown["version"], source, instruction)
        outcome = apply_attempt(authority, plan, kind, shown)
        outcome["agent"] = name
        outcome["kind"] = kind
        outcome["raw_tail"] = (raw or "")[-240:]
        agents[name] = outcome
        print(f"  [{name}] {outcome['class']} commit={int(outcome['committed'])} "
              f"harm={int(outcome['cat2_harm'])} misread={int(outcome['misread_shown'])}",
              flush=True)
    return {
        "seed": seed,
        "model": client.name,
        "task": task["id"],
        "order": order,
        "final_version": authority["version"],
        "agents": agents,
    }


def load_done() -> set[int]:
    done = set()
    if not os.path.exists(OUT):
        return done
    with open(OUT, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                done.add(json.loads(line)["seed"])
    return done


def summarize(path: str) -> dict:
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    counts = {}
    harm = 0
    attempts = 0
    for row in rows:
        for ag in row["agents"].values():
            attempts += 1
            counts[ag["class"]] = counts.get(ag["class"], 0) + 1
            harm += int(ag["cat2_harm"])
    dev = counts.get("belief_deviation", 0)
    decision = "CONTINUE" if dev > 0 and harm > 0 else "STOP"
    summary = {
        "episodes": len(rows),
        "attempts": attempts,
        "counts": counts,
        "belief_deviation": dev,
        "cat2_harm": harm,
        "decision": decision,
    }
    print("\n== 共享仓库自然信念普查 ==")
    print(f"  局数 {len(rows)}  提交尝试 {attempts}")
    for key in ("ok", "stale", "belief_deviation", "plan_error", "version_ahead", "unparsed"):
        print(f"  {key:18} {counts.get(key, 0)}")
    print(f"  版本对但内容错、并且写入了坏补丁: {harm}")
    print(f"  停止规则判定: {decision}")
    if decision == "STOP":
        print("  第二类没有造成坏补丁。按规则停止，不再把 L4 幻觉叙事做正稿。")
    else:
        print("  第二类出现并写成了坏补丁。正稿的前提还在，可以沿这条证据往下做。")
    return summary


def self_check() -> int:
    src = episode_banner(1, BODIES["plain"])
    authority = {"version": 1, "source": src, "digest": digest(src)}
    shown = {"version": 1, "digest": authority["digest"]}
    ok_plan = {"believed_version": 1, "believed_source": src,
               "new_source": src + "\ndef sub(a, b):\n    return a - b\n"}
    out = apply_attempt(authority, ok_plan, "sub", shown)
    assert out["class"] == "ok" and out["committed"] and not out["cat2_harm"], out
    # 版本号抄对，正文少一行：信念偏差，且新文件把 sub 写成加法，测试失败。
    fresh = {"version": 1, "source": src, "digest": digest(src)}
    lied = {"believed_version": 1, "believed_source": src.replace("return a + b", "return 0"),
            "new_source": src + "\ndef sub(a, b):\n    return a + b\n"}
    bad = apply_attempt(fresh, lied, "sub", shown)
    assert bad["class"] == "belief_deviation" and bad["cat2_harm"], bad
    # 先写者推进版本后，后写者仍报版本 1：过期，不写入。
    stale = apply_attempt(fresh, ok_plan, "sub", shown)
    assert stale["class"] == "stale" and not stale["committed"], stale
    print("self-check ok")
    return 0


def summarize_retry(path: str) -> dict:
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    lost = sum(1 for row in rows if row["lost_update"])
    committed = sum(1 for row in rows if row["retry_committed"])
    both_ok = sum(1 for row in rows if all(row["final_tests"].values()))
    decision = "PASS" if lost > 0 else "FAIL"
    summary = {
        "episodes": len(rows),
        "retry_committed": committed,
        "both_tests_ok": both_ok,
        "lost_update": lost,
        "decision": decision,
    }
    print("\n== 被拒后重做 ==")
    print(f"  局数 {len(rows)}  重做并写入 {committed}  双方测试都过 {both_ok}")
    print(f"  丢失更新 {lost}")
    print(f"  门槛 2: {decision}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--retry", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        return self_check()
    out_path = os.path.join(BASE, "ccf_followup",
                            "repo_retry.jsonl" if args.retry else "repo_belief.jsonl")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    clients = load_clients()
    if "qwen" not in clients or "deepseek" not in clients:
        print("缺少 qwen 或 deepseek")
        return 2
    done = set()
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    done.add(json.loads(line)["seed"])
    with open(out_path, "a", encoding="utf-8") as out:
        for seed in range(args.trials):
            if seed in done:
                continue
            client = clients["qwen"] if seed % 2 == 0 else clients["deepseek"]
            print(f"\n== seed {seed} {client.name} ==", flush=True)
            rec = run_retry_episode(seed, client) if args.retry else run_episode(seed, client)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
    if args.retry:
        summary = summarize_retry(out_path)
        name = "repo_retry_summary.json"
    else:
        summary = summarize(out_path)
        name = "repo_belief_summary.json"
    path = os.path.join(BASE, "ccf_followup", name)
    json.dump(summary, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
