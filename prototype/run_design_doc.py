"""n=30 lease-only vs write-gating on the parametric design document.

Same instrument (authority ledger, sdk_cache gate, Qwen / DeepSeek at
temperature 0.2). Agent B always has a persistent +1 mm shift on the
observed bore. Seeds are design-doc-{trial} (no arm name). Logs go to
design_doc_rerun/ and do not overwrite unified_seed_rerun/ or
combo_gate_orch/.

Usage:
  python run_design_doc.py --suite selftest
  python run_design_doc.py --suite smoke --trials 1
  python run_design_doc.py --suite all
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from statistics import mean, stdev

from prap_core import Arbiter, digest
from testbed import Sim, ScriptedAgent
from llm_agent import load_clients
from design_doc import (
    A_WRITE, B_WRITE, DOC_KEYS, DOC_OPS, HORIZON, INIT, INJECT_KEY,
    INJECT_SHIFT, KEY_BORE, KEY_OUTER, KEY_REV, KEY_STATUS, KEY_WALL,
    TARGET, DesignAgent, consistent, init_document, is_success,
    is_wrong_release, params_correct, snapshot, wasted_ops,
)

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "design_doc_rerun")
N_TRIALS = 30
ARMS = ("leaseonly", "gate")
SEED_PREFIX = "design-doc"


def log(msg: str) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT_DIR, "run.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def make_sim(l4_enabled: bool) -> Sim:
    sim = Sim()
    sim.arbiter = Arbiter(DOC_OPS)
    sim.arbiter.l4_enabled = l4_enabled
    sim.arbiter.width = 0
    sim.arbiter.height = 0
    init_document(sim.arbiter)
    return sim


def models_for_trial(trial: int) -> dict[str, str]:
    # Even trial: A = Qwen, B = DeepSeek; odd: swapped. Same rule as topology.
    if trial % 2 == 0:
        return {"A": "qwen", "B": "deepseek"}
    return {"A": "deepseek", "B": "qwen"}


def record_trial(sim: Sim, arm: str, trial: int, models: dict, error=None) -> dict:
    arb = sim.arbiter
    s = snapshot(arb)
    grants = sum(1 for e in arb.log if e[0] == "grant")
    denies = int(arb.stats.get("denies") or 0)
    quarantined = sum(1 for st in arb.agent_status.values() if st == "suspect")
    rec = {
        "suite": "design_doc",
        "arm": arm,
        "trial": trial,
        "seed": f"{SEED_PREFIX}-{trial}",
        "models": {k: sim.agents[k].client.model if hasattr(sim.agents[k], "client") else models[k]
                   for k in ("A", "B") if k in sim.agents},
        "success": bool(is_success(arb)) if error is None else False,
        "wrong_release": bool(is_wrong_release(arb)) if error is None else False,
        "params_correct": bool(params_correct(s)) if error is None else False,
        "consistent": bool(consistent(s)) if error is None else False,
        "document": s,
        "fail_reason": "ok",
        "error": error,
        "wasted_ops": wasted_ops(arb) if error is None else None,
        "n_quarantined": quarantined,
        "grants": grants,
        "denies": denies,
        "tokens": 0,
        "llm_calls": 0,
        "agent_status": dict(arb.agent_status),
        "arbiter_ops": [
            {"kind": e[0], "name": e[3], "args": e[4], "ok": e[5], "detail": e[6]}
            for e in arb.log if e[0] in ("op", "op_free")
        ],
    }
    tokens = calls = 0
    for aid, ag in sim.agents.items():
        client = getattr(ag, "client", None)
        if client is not None:
            tokens += int(client.usage.get("prompt_tokens", 0)) + int(client.usage.get("completion_tokens", 0))
            calls += int(client.usage.get("calls", 0))
        rec.setdefault("agents", []).append({
            "id": aid,
            "model": getattr(client, "model", models.get(aid)),
            "write_keys": list(getattr(ag, "write_keys", ())),
            "injected": bool(getattr(ag, "inject_bore", False)),
            "status": arb.agent_status.get(aid),
            "last_verdict": getattr(ag, "last_verdict", None),
            "granted_at": ag.report.granted_at,
            "blocked_from": ag.report.blocked_from,
            "completed_at": ag.report.completed_at,
        })
    rec["tokens"] = tokens
    rec["llm_calls"] = calls
    if error:
        rec["fail_reason"] = "error"
    elif rec["success"]:
        rec["fail_reason"] = "ok"
    elif rec["wrong_release"]:
        rec["fail_reason"] = "wrong_release"
    elif s.get(KEY_STATUS) != "released":
        rec["fail_reason"] = f"not_released:{s}"
    else:
        rec["fail_reason"] = f"incomplete:{s}"
    return rec


def run_llm_trial(arm: str, trial: int, clients: dict) -> dict:
    names = models_for_trial(trial)
    sim = make_sim(l4_enabled=(arm == "gate"))
    # Fresh usage counters per trial (clients are reused otherwise).
    local = {}
    for aid, cname in names.items():
        src = clients[cname]
        local[aid] = type(src)(src.name, src.base_url, src.api_key, src.model, src.temperature, src.timeout)
    DesignAgent(
        sim, "A", local["A"], A_WRITE, inject_bore=False,
        obs_seed=f"{SEED_PREFIX}-{trial}-A",
    )
    DesignAgent(
        sim, "B", local["B"], B_WRITE, inject_bore=True,
        obs_seed=f"{SEED_PREFIX}-{trial}-B",
    )
    sim.agents["A"].start(0)
    sim.agents["B"].start(1)
    try:
        sim.run(until=HORIZON)
        return record_trial(sim, arm, trial, names)
    except Exception as exc:  # noqa: BLE001
        rec = record_trial(sim, arm, trial, names, error=repr(exc))
        rec["traceback"] = traceback.format_exc()
        return rec


def append_json(path: str, rec: dict) -> None:
    rows = []
    if os.path.exists(path):
        rows = json.loads(open(path, encoding="utf-8").read() or "[]")
    # resume: replace same trial if rerun
    rows = [r for r in rows if r.get("trial") != rec["trial"]]
    rows.append(rec)
    rows.sort(key=lambda r: r["trial"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


# ---------- Offline self-test (no API key) ----------

def _belief_from_obs(sim: Sim, inject_bore: bool) -> list:
    belief = []
    for k in DOC_KEYS:
        entry = sim.arbiter.state[k]
        val = entry.value
        if inject_bore and k == INJECT_KEY:
            val = int(val) + INJECT_SHIFT
        belief.append((k, entry.version, digest(val)))
    return belief


def selftest() -> int:
    # 1. Healthy scripted writers, no injection: success.
    sim = make_sim(l4_enabled=True)
    ScriptedAgent(
        sim, "A", (KEY_BORE,),
        [("set_param", {"key": KEY_BORE, "value": 12})],
        ttl=8, think=0, belief=_belief_from_obs(sim, False),
    )
    ScriptedAgent(
        sim, "B", (KEY_WALL, KEY_OUTER, KEY_STATUS, KEY_REV),
        [("set_param", {"key": KEY_WALL, "value": 5}),
         ("set_param", {"key": KEY_OUTER, "value": 22}),
         ("release_revision", {})],
        ttl=8, think=0, belief=_belief_from_obs(sim, False),
    )
    sim.agents["A"].start(0)
    sim.agents["B"].start(2)
    sim.run(until=20)
    assert is_success(sim.arbiter), snapshot(sim.arbiter)
    assert not is_wrong_release(sim.arbiter)
    print("selftest 1 ok: healthy scripted release")

    # 2. Lease-only, B writes outer from a +1-shifted bore (11+2*5=21) and releases.
    sim = make_sim(l4_enabled=False)
    # B checks in the bad outer first; A then writes the correct bore
    # and releases, so the released file is (12, 5, 21).
    ScriptedAgent(
        sim, "A", (KEY_BORE, KEY_STATUS, KEY_REV),
        [("set_param", {"key": KEY_BORE, "value": 12}),
         ("release_revision", {})],
        ttl=8, think=0, belief=_belief_from_obs(sim, False),
    )
    ScriptedAgent(
        sim, "B", (KEY_WALL, KEY_OUTER),
        [("set_param", {"key": KEY_WALL, "value": 5}),
         ("set_param", {"key": KEY_OUTER, "value": 21})],
        ttl=8, think=0, belief=_belief_from_obs(sim, True),
    )
    sim.agents["B"].start(0)
    sim.agents["A"].start(2)
    sim.run(until=20)
    assert not is_success(sim.arbiter), snapshot(sim.arbiter)
    assert is_wrong_release(sim.arbiter), snapshot(sim.arbiter)
    s = snapshot(sim.arbiter)
    assert s[KEY_BORE] == 12 and s[KEY_WALL] == 5 and s[KEY_OUTER] == 21
    print("selftest 2 ok: lease-only wrong release (outer=21)")

    # 3. Write gating: B's +1 cache of the bore is a deviation; B is isolated
    #    and cannot check in the bad outer. A still writes the bore. No release.
    sim = make_sim(l4_enabled=True)
    ScriptedAgent(
        sim, "A", (KEY_BORE,),
        [("set_param", {"key": KEY_BORE, "value": 12})],
        ttl=8, think=0, belief=_belief_from_obs(sim, False),
    )
    ScriptedAgent(
        sim, "B", (KEY_WALL, KEY_OUTER, KEY_STATUS, KEY_REV),
        [("set_param", {"key": KEY_WALL, "value": 5}),
         ("set_param", {"key": KEY_OUTER, "value": 21}),
         ("release_revision", {})],
        ttl=8, think=0, belief=_belief_from_obs(sim, True),
    )
    sim.agents["B"].start(0)
    sim.agents["A"].start(2)
    sim.run(until=20)
    s = snapshot(sim.arbiter)
    assert s[KEY_BORE] == 12
    assert s[KEY_WALL] == 4 and s[KEY_OUTER] == 18  # B never wrote
    assert s[KEY_STATUS] == "in_design"
    assert not is_success(sim.arbiter)
    assert not is_wrong_release(sim.arbiter)
    assert sim.arbiter.agent_status.get("B") == "suspect"
    print("selftest 3 ok: gate isolates B, no wrong release")

    # 4. Injection on the current bore stays a same-version digest mismatch
    #    after A has already updated the field (offset tracks the true value).
    sim = make_sim(l4_enabled=True)
    ScriptedAgent(
        sim, "A", (KEY_BORE,),
        [("set_param", {"key": KEY_BORE, "value": 12})],
        ttl=8, think=0, belief=_belief_from_obs(sim, False),
    )
    sim.agents["A"].start(0)
    sim.run(until=8)
    assert snapshot(sim.arbiter)[KEY_BORE] == 12
    b = _belief_from_obs(sim, True)
    bore_belief = [row for row in b if row[0] == KEY_BORE][0]
    entry = sim.arbiter.state[KEY_BORE]
    assert bore_belief[1] == entry.version
    assert bore_belief[2] != digest(entry.value)
    assert bore_belief[2] == digest(int(entry.value) + INJECT_SHIFT)
    print("selftest 4 ok: +1 mm of the current bore stays a deviation")
    print("all selftests passed")
    return 0


def summarize(path: str) -> None:
    if not os.path.exists(path):
        print(f"missing {path}")
        return
    rows = json.loads(open(path, encoding="utf-8").read())
    n = len(rows)
    succ = sum(1 for r in rows if r.get("success"))
    wrong = sum(1 for r in rows if r.get("wrong_release"))
    waste = [float(r.get("wasted_ops") or 0) for r in rows]
    toks = [float(r.get("tokens") or 0) for r in rows]
    calls = [float(r.get("llm_calls") or 0) for r in rows]
    err = sum(1 for r in rows if r.get("error"))

    def ms(xs):
        if not xs:
            return "nan"
        if len(xs) == 1:
            return f"{xs[0]:.2f} (0.00)"
        return f"{mean(xs):.2f} ({stdev(xs):.2f})"

    print(f"{os.path.basename(path)}: {succ}/{n} success, {wrong}/{n} wrong releases, "
          f"waste {ms(waste)}, tokens {ms(toks)}, calls {ms(calls)}, errors={err}")


def run_arm(arm: str, trials: int, clients: dict, resume: bool) -> str:
    path = os.path.join(OUT_DIR, f"design_{arm}.json")
    done = set()
    if resume and os.path.exists(path):
        done = {r["trial"] for r in json.loads(open(path, encoding="utf-8").read() or "[]")
                if not r.get("error")}
    for t in range(trials):
        if t in done:
            log(f"skip {arm} t{t} (already recorded)")
            continue
        t0 = time.time()
        rec = run_llm_trial(arm, t, clients)
        append_json(path, rec)
        log(f"{arm} t{t} success={rec['success']} wrong_release={rec['wrong_release']} "
            f"waste={rec['wasted_ops']} tokens={rec['tokens']} calls={rec['llm_calls']} "
            f"reason={rec['fail_reason']} ({time.time()-t0:.1f}s)")
    summarize(path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["selftest", "smoke", "leaseonly", "gate", "all"],
                    default="all")
    ap.add_argument("--trials", type=int, default=N_TRIALS)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    if args.suite == "selftest":
        return selftest()

    os.makedirs(OUT_DIR, exist_ok=True)
    clients = load_clients()
    missing = [n for n in ("qwen", "deepseek") if n not in clients]
    if missing:
        log(f"missing API keys: {missing}")
        return 2
    log("clients loaded: " + ", ".join(f"{k}={v.model}" for k, v in clients.items()))
    log(f"start suite={args.suite} trials={args.trials}")

    try:
        if args.suite in ("smoke",):
            run_arm("leaseonly", args.trials, clients, resume=False)
            run_arm("gate", args.trials, clients, resume=False)
        else:
            arms = ARMS if args.suite == "all" else (args.suite,)
            for arm in arms:
                run_arm(arm, args.trials, clients, resume=not args.no_resume)
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        return 1
    log("all requested work finished")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
