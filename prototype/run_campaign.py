"""E1 战役编排器：n=30 主任务（T1）+ 泛化任务（T2/T3/T4），断点续跑。

- 每个 (task, cond) 独立输出文件，每个 trial 落盘一次 —— 中断后重跑自动跳过已完成 trial
- 文件名：e1_{task}_{cond}_n{trials}.json
"""

from __future__ import annotations

import json
import os
import sys

from run_e1_full import TASKS, aggregate, run_trial
from llm_agent import load_clients

BASE = os.path.dirname(os.path.abspath(__file__))
CAMPAIGN = (
    [("T1", c, 30) for c in ("C1", "C2", "C3", "C4", "C5")]
    + [(t, c, 10) for t in ("T2", "T3", "T4") for c in ("C2", "C3", "C4")]
)


def out_path(task: str, cond: str, trials: int) -> str:
    return os.path.join(BASE, f"e1_{task}_{cond}_n{trials}.json")


def main() -> int:
    clients = load_clients()
    missing = [n for n in ("qwen", "deepseek") if n not in clients]
    if missing:
        print(f"缺少 API key：{missing}")
        return 2

    all_records: list[dict] = []
    for task, cond, trials in CAMPAIGN:
        path = out_path(task, cond, trials)
        recs: list[dict] = []
        if os.path.exists(path):
            try:
                recs = json.load(open(path, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                recs = []
        done = {r["trial"] for r in recs}
        horizon = TASKS[task]["horizon"]
        for t in range(trials):
            if t in done:
                continue
            recs.append(run_trial(cond, t, clients, horizon, task=task))
            with open(path, "w", encoding="utf-8") as f:
                json.dump(recs, f, ensure_ascii=False, indent=1)   # 逐 trial 落盘
        print(f"[{task}/{cond}] 完成 {len(recs)}/{trials} -> {os.path.basename(path)}",
              flush=True)
        all_records.extend(recs)

    print(aggregate(all_records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
