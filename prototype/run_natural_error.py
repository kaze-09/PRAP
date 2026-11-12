"""Natural document-read experiment (protocol frozen 2026-11-12 16:26 CST).

Stage A: 100 read-only extracts (10 instances x 5 templates x 2 models).
Stage B: lease-only vs write gating, n=30 each, document observation only.

Usage:
  python run_natural_error.py --selftest
  python run_natural_error.py --stage a
  python run_natural_error.py --stage b
  python run_natural_error.py --stage both
  python run_natural_error.py --summarize
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections import defaultdict

from design_doc import (
    A_WRITE,
    B_WRITE,
    DOC_OPS,
    HORIZON,
    KEY_BORE,
    KEY_OUTER,
    KEY_REV,
    KEY_STATUS,
    KEY_WALL,
    DesignAgent,
    consistent,
    init_document,
    snapshot,
    wasted_ops,
)
from llm_agent import load_clients
from natural_doc import (
    CURRENT_FIELDS,
    INSTANCES,
    TEMPLATES,
    classify_error,
    extract_from_document,
    ground_truth,
    normalize_extract,
    render_document,
    stage_a_units,
    stage_b_spec,
    wilson_ci,
)
from prap_core import Arbiter, digest
from testbed import Sim

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "natural_error_runs")
SEED_PREFIX = "natural-err-20261112"
PROTOCOL = "natural-err-20261112-16:26-Asia/Shanghai"


def _ensure_out() -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    return OUT_DIR


# ---------- offline self-test (no API) ----------

def selftest() -> int:
    failures = []

    if len(INSTANCES) != 10 or len(TEMPLATES) != 5:
        failures.append("instance/template counts")
    units = stage_a_units()
    if len(units) != 100:
        failures.append(f"stage A units {len(units)} != 100")

    for inst in INSTANCES:
        if inst.outer != inst.bore + 2 * inst.wall:
            failures.append(f"instance {inst.id} current not consistent")
        if inst.mating_outer != inst.mating_bore + 2 * inst.mating_wall:
            failures.append(f"instance {inst.id} mating not consistent")
        if inst.superseded_bore != inst.bore + 1:
            failures.append(f"instance {inst.id} superseded delta")

        for tmpl in TEMPLATES:
            doc = render_document(tmpl, inst)
            # Ground truth must appear.
            for val in (inst.bore, inst.wall, inst.outer):
                if str(val) not in doc:
                    failures.append(f"{tmpl}/{inst.id} missing GT {val}")
            # Ledger truth must not be labelled as a hint in a hidden tag.
            if "GROUND_TRUTH" in doc or "true_bore" in doc:
                failures.append(f"{tmpl}/{inst.id} leaked truth tag")
            if tmpl == "superseded" and str(inst.superseded_bore) not in doc:
                failures.append(f"superseded {inst.id} missing distractor")
            if tmpl == "mating_ref" and str(inst.mating_bore) not in doc:
                failures.append(f"mating_ref {inst.id} missing mating")
            if tmpl == "dual_unit" and " in)" not in doc:
                failures.append(f"dual_unit {inst.id} missing inches")
            if tmpl == "inspect_table" and "stock catalog" not in doc:
                failures.append(f"inspect_table {inst.id} missing catalog")
            if tmpl == "clean" and "已作废" in doc:
                failures.append(f"clean {inst.id} has superseded text")

            # Classifier on stub parses.
            def parsed(b, w, o):
                return normalize_extract(
                    {"bore_mm": b, "wall_mm": w, "outer_mm": o})

            ok = classify_error(parsed(inst.bore, inst.wall, inst.outer), inst, tmpl)
            if ok["mismatch"] or ok["error_type"] != "ok":
                failures.append(f"true parse flagged on {tmpl}/{inst.id}: {ok}")
            if tmpl == "superseded":
                c = classify_error(
                    parsed(inst.superseded_bore, inst.superseded_wall,
                           inst.superseded_outer), inst, tmpl)
                if c["error_type"] != "picked_superseded":
                    failures.append(f"superseded type {c}")
            if tmpl == "mating_ref":
                c = classify_error(
                    parsed(inst.mating_bore, inst.mating_wall, inst.mating_outer),
                    inst, tmpl)
                if c["error_type"] != "picked_mating":
                    failures.append(f"mating type {c}")
            if tmpl == "inspect_table":
                c = classify_error(
                    parsed(inst.catalog_bore, inst.catalog_wall, inst.catalog_outer),
                    inst, tmpl)
                if c["error_type"] != "picked_catalog":
                    failures.append(f"catalog type {c}")
                c = classify_error(
                    parsed(inst.draft_bore, inst.draft_wall, inst.draft_outer),
                    inst, tmpl)
                if c["error_type"] != "picked_draft":
                    failures.append(f"draft type {c}")
            if tmpl == "dual_unit":
                inch = inst.inches()
                c = classify_error(parsed(inch[0], inch[1], inch[2]), inst, tmpl)
                if c["error_type"] != "used_inch":
                    failures.append(f"inch type {c}")

    # Extract prompt must not contain a numeric ground truth by itself;
    # the document is interpolated later. Check the template string.
    from natural_doc import EXTRACT_USER, EXTRACT_SYSTEM
    if "{document}" not in EXTRACT_USER:
        failures.append("extract user missing document slot")
    if "10 mm" in EXTRACT_SYSTEM or "bore_mm = 10" in EXTRACT_SYSTEM:
        failures.append("extract system leaked an example truth")

    # init_document accepts a custom state; official INIT still works.
    from design_doc import INIT
    sim = Sim()
    sim.arbiter = Arbiter(DOC_OPS)
    init_document(sim.arbiter, INSTANCES[2].init_state())
    snap = snapshot(sim.arbiter)
    if int(snap[KEY_BORE]) != 16 or int(snap[KEY_WALL]) != 5:
        failures.append(f"custom init failed: {snap}")
    sim2 = Sim()
    sim2.arbiter = Arbiter(DOC_OPS)
    init_document(sim2.arbiter)
    snap2 = snapshot(sim2.arbiter)
    if snap2[KEY_BORE] != INIT[KEY_BORE]:
        failures.append("default init changed")

    if failures:
        print("SELFTEST FAIL:")
        for f in failures:
            print(" -", f)
        return 1
    print("SELFTEST OK: 10 instances, 5 templates, 100 stage-A units,")
    print("classifier stubs, no truth leak in prompts, init_document intact.")
    return 0


# ---------- Stage A ----------

def _stage_a_path() -> str:
    return os.path.join(_ensure_out(), "stage_a.jsonl")


def _load_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run_stage_a(clients: dict) -> str:
    path = _stage_a_path()
    done = {r["unit"] for r in _load_jsonl(path) if "unit" in r and "error" not in r}
    units = stage_a_units()
    print(f"Stage A: {len(done)}/{len(units)} already done, writing {path}")
    for unit in units:
        if unit["unit"] in done:
            continue
        inst = INSTANCES[unit["instance_id"]]
        doc = render_document(unit["template"], inst, serial=f"SN-A{unit['unit']:03d}")
        client = clients[unit["model"]]
        # Isolated usage counters for this unit.
        prev = dict(client.usage)
        try:
            parsed = extract_from_document(client, doc)
            classified = classify_error(parsed, inst, unit["template"])
            rec = {
                "protocol": PROTOCOL,
                **unit,
                "truth": ground_truth(inst),
                "classified": classified,
                "parsed_fields": parsed.get("fields"),
                "extract_ok": parsed.get("ok"),
                "raw_text": parsed.get("raw_text"),
                "tokens": (
                    client.usage.get("prompt_tokens", 0) - prev.get("prompt_tokens", 0)
                    + client.usage.get("completion_tokens", 0) - prev.get("completion_tokens", 0)
                ),
                "calls": client.usage.get("calls", 0) - prev.get("calls", 0),
            }
        except Exception as exc:  # noqa: BLE001
            rec = {
                "protocol": PROTOCOL,
                **unit,
                "error": repr(exc),
                "traceback": traceback.format_exc(),
            }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        status = rec.get("classified", {}).get("error_type", rec.get("error", "?"))
        print(f"  unit {unit['unit']:3d} {unit['model']:8s} "
              f"{unit['template']:14s} inst={unit['instance_id']} -> {status}")
        if "error" not in rec:
            done.add(unit["unit"])
    print(f"Stage A finished: {len(done)}/{len(units)}")
    return path


# ---------- Stage B ----------

def _belief_mismatch(arb, keys, belief) -> dict:
    mismatches, missing = [], []
    if not belief:
        return {"mismatches": [], "missing": list(keys), "disagree": False}
    for key, bver, bdig in belief:
        if key not in keys:
            continue
        entry = arb.state.get(key)
        if bdig in ("never_observed", "never_claimed") or bver < 0:
            missing.append(key)
            continue
        if entry is not None and bdig != digest(entry.value):
            mismatches.append(key)
    return {
        "mismatches": mismatches,
        "missing": missing,
        "disagree": bool(mismatches),
    }


def _wrap_submit(sim: Sim) -> None:
    orig = sim.arbiter.submit_intent

    def wrapped(agent_id, keys, ops=(), ttl=5, priority=5, belief=None, effect=None):
        oracle = sim.arbiter._classify(belief) if belief else None
        mismatch = _belief_mismatch(sim.arbiter, tuple(keys), belief)
        agent = sim.agents.get(agent_id)
        rec = {
            "tick": sim.now,
            "agent": agent_id,
            "keys": list(keys),
            "ops": list(ops) if ops else [],
            "mismatch": mismatch,
            "oracle_class": None if oracle is None else oracle.get("class"),
            "oracle_evidence": None if oracle is None else oracle.get("evidence"),
            "l4_enabled": bool(sim.arbiter.l4_enabled),
        }
        if agent is not None:
            getattr(agent, "intent_logs", []).append(rec)
        before = len(sim.arbiter.log)
        orig(agent_id, keys, ops=ops, ttl=ttl, priority=priority,
             belief=belief, effect=effect)
        new_ev = [e[0] for e in sim.arbiter.log[before:]]
        rec["log_events"] = new_ev
        rec["belief_denied"] = any(
            ev in ("intent_suspect_blocked", "detect_deviation", "correct")
            or ev == "intent"  # placeholder
            for ev in new_ev
        )
        # Belief deny: deviation path, not lock-contention queueing.
        # grant => not belief-denied. intent + waiting deny has verdict
        # already in oracle_class. suspect_blocked / no grant after deviation.
        rec["granted"] = "grant" in new_ev
        rec["belief_denied"] = (
            (not rec["granted"])
            and (
                rec["oracle_class"] == "deviation"
                or "intent_suspect_blocked" in new_ev
                or "correct" in new_ev
                or "detect_deviation" in new_ev
            )
        )
        rec["contention_denied"] = (
            (not rec["granted"]) and not rec["belief_denied"]
        )
        return None

    sim.arbiter.submit_intent = wrapped  # type: ignore[method-assign]


def _params_match(snap: dict, target: dict) -> bool:
    try:
        return (
            int(snap.get(KEY_BORE, -1)) == int(target[KEY_BORE])
            and int(snap.get(KEY_WALL, -1)) == int(target[KEY_WALL])
            and int(snap.get(KEY_OUTER, -1)) == int(target[KEY_OUTER])
        )
    except (TypeError, ValueError):
        return False


def models_for_trial(trial: int) -> dict[str, str]:
    if trial % 2 == 0:
        return {"A": "qwen", "B": "deepseek"}
    return {"A": "deepseek", "B": "qwen"}


def run_stage_b_trial(arm: str, trial: int, clients: dict) -> dict:
    names = models_for_trial(trial)
    spec = stage_b_spec(trial)
    inst = spec["instance"]
    sim = Sim()
    sim.arbiter = Arbiter(DOC_OPS)
    sim.arbiter.l4_enabled = arm != "leaseonly"
    sim.arbiter.width = 0
    sim.arbiter.height = 0
    init_document(sim.arbiter, inst.init_state())
    _wrap_submit(sim)
    seed = f"{SEED_PREFIX}-{arm}-{trial}"
    local = {}
    for aid, cname in names.items():
        src = clients[cname]
        local[aid] = type(src)(src.name, src.base_url, src.api_key, src.model,
                               src.temperature, src.timeout)
    DesignAgent(
        sim, "A", local["A"], A_WRITE, inject_bore=False,
        document_spec=spec, obs_seed=f"{seed}-A",
    )
    DesignAgent(
        sim, "B", local["B"], B_WRITE, inject_bore=False,
        document_spec=spec, obs_seed=f"{seed}-B",
    )
    sim.agents["A"].start(0)
    sim.agents["B"].start(1)
    error = None
    try:
        sim.run(until=HORIZON)
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)
        tb = traceback.format_exc()
    else:
        tb = None
    snap = snapshot(sim.arbiter)
    target = inst.target()
    params_ok = _params_match(snap, target)
    released = snap.get(KEY_STATUS) == "released"
    success = bool(params_ok and released and not sim.arbiter.leases) if error is None else False
    wrong_release = bool(released and not params_ok) if error is None else False
    intent_logs = []
    read_logs = []
    tokens = calls = 0
    for aid, ag in sim.agents.items():
        intent_logs.extend(getattr(ag, "intent_logs", []) or [])
        # Drop bulky document text from the trial record? Keep it: needed
        # for audit. Stage B n=30 x 2 is still small.
        read_logs.extend(getattr(ag, "read_logs", []) or [])
        client = getattr(ag, "client", None)
        if client is not None:
            tokens += int(client.usage.get("prompt_tokens", 0)) + int(
                client.usage.get("completion_tokens", 0))
            calls += int(client.usage.get("calls", 0))
    # First extract per agent (in situ observation error).
    first_reads = {}
    for log in read_logs:
        aid = log["agent"]
        if aid not in first_reads:
            first_reads[aid] = log.get("classified") or {}
    rec = {
        "protocol": PROTOCOL,
        "suite": "natural_error_stage_b",
        "arm": arm,
        "trial": trial,
        "seed": seed,
        "template": spec["template"],
        "instance_id": inst.id,
        "models": {k: sim.agents[k].client.model for k in ("A", "B") if k in sim.agents},
        "success": success,
        "wrong_release": wrong_release,
        "params_correct": params_ok if error is None else False,
        "consistent": bool(consistent(snap)) if error is None else False,
        "document": snap,
        "target": {k: target[k] for k in target},
        "error": error,
        "traceback": tb,
        "wasted_ops": wasted_ops(sim.arbiter) if error is None else None,
        "grants": sum(1 for e in sim.arbiter.log if e[0] == "grant"),
        "denies": int(sim.arbiter.stats.get("denies") or 0),
        "n_quarantined": sum(1 for st in sim.arbiter.agent_status.values()
                             if st == "suspect"),
        "tokens": tokens,
        "llm_calls": calls,
        "intent_logs": intent_logs,
        "read_logs": read_logs,
        "first_reads": first_reads,
        "agent_status": dict(sim.arbiter.agent_status),
    }
    return rec


def _stage_b_path(arm: str) -> str:
    return os.path.join(_ensure_out(), f"stage_b_{arm}.json")


def run_stage_b(clients: dict, arms: list[str], trials: int, resume: bool = True) -> None:
    for arm in arms:
        path = _stage_b_path(arm)
        rows = []
        if resume and os.path.exists(path):
            rows = json.loads(open(path, encoding="utf-8").read() or "[]")
        done = {r["trial"] for r in rows if r.get("error") is None}
        print(f"Stage B {arm}: {len(done)}/{trials} done, writing {path}")
        for t in range(trials):
            if t in done:
                continue
            print(f"\n--- {arm} trial {t} template={TEMPLATES[t % 5]} "
                  f"inst={INSTANCES[t % 10].id} ---")
            rec = run_stage_b_trial(arm, t, clients)
            rows = [r for r in rows if r.get("trial") != t]
            rows.append(rec)
            rows.sort(key=lambda r: r["trial"])
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
            print(f"  => success={rec['success']} wrong_release={rec['wrong_release']} "
                  f"waste={rec['wasted_ops']} tokens={rec['tokens']} "
                  f"error={rec.get('error')}")
            if rec.get("error") is None:
                done.add(t)
        print(f"Stage B {arm} finished: {len(done)}/{trials}")


# ---------- summaries ----------

def _rate(k: int, n: int) -> str:
    if n <= 0:
        return f"{k}/{n}"
    lo, hi = wilson_ci(k, n)
    return f"{k}/{n} = {k / n:.1%}  Wilson 95% CI [{lo:.1%}, {hi:.1%}]"


def summarize_stage_a(path: Optional[str] = None) -> str:
    path = path or _stage_a_path()
    rows = [r for r in _load_jsonl(path) if "classified" in r]
    lines = [f"Stage A ({path}): {len(rows)} completed extracts"]
    ok_rows = [r for r in rows if r["classified"].get("extract_ok")]
    fail_n = len(rows) - len(ok_rows)
    mis = sum(1 for r in ok_rows if r["classified"].get("mismatch"))
    lines.append(f"  extract failures (excluded from primary): {fail_n}/{len(rows)}")
    lines.append(f"  primary mismatch: {_rate(mis, len(ok_rows))}")
    for tmpl in TEMPLATES:
        sub = [r for r in ok_rows if r["template"] == tmpl]
        k = sum(1 for r in sub if r["classified"].get("mismatch"))
        lines.append(f"    {tmpl}: {_rate(k, len(sub))}")
    for model in ("qwen", "deepseek"):
        sub = [r for r in ok_rows if r["model"] == model]
        k = sum(1 for r in sub if r["classified"].get("mismatch"))
        # Map to official model name if present later.
        lines.append(f"    {model}: {_rate(k, len(sub))}")
    types = defaultdict(int)
    for r in ok_rows:
        if r["classified"].get("mismatch"):
            types[r["classified"].get("error_type") or "other"] += 1
    if types:
        lines.append("  error types: " + ", ".join(f"{k}={v}" for k, v in sorted(types.items())))
    text = "\n".join(lines)
    print(text)
    return text


def _intent_stats(rows: list[dict]) -> dict:
    intents = [it for r in rows for it in (r.get("intent_logs") or [])]
    tpr_n = tpr_k = fpr_n = fpr_k = 0
    should_catch = missed_write = 0
    for it in intents:
        disagree = bool((it.get("mismatch") or {}).get("disagree"))
        denied = bool(it.get("belief_denied"))
        if disagree:
            tpr_n += 1
            if denied:
                tpr_k += 1
            if not it.get("l4_enabled") and it.get("granted"):
                missed_write += 1
                should_catch += 1
            elif not it.get("l4_enabled"):
                should_catch += 1
        else:
            # Exclude missing-only? Protocol: FPR among intents whose
            # observed leased keys all agree.
            fpr_n += 1
            if denied:
                fpr_k += 1
    return {
        "intents": len(intents),
        "tpr": (tpr_k, tpr_n),
        "fpr": (fpr_k, fpr_n),
        "leaseonly_error_intents": should_catch,
        "leaseonly_granted_on_error": missed_write,
    }


def summarize_stage_b() -> str:
    lines = ["Stage B:"]
    for arm in ("leaseonly", "gate"):
        path = _stage_b_path(arm)
        if not os.path.exists(path):
            lines.append(f"  {arm}: no file")
            continue
        rows = json.loads(open(path, encoding="utf-8").read() or "[]")
        ok = [r for r in rows if r.get("error") is None]
        succ = sum(1 for r in ok if r.get("success"))
        wrong = sum(1 for r in ok if r.get("wrong_release"))
        first_mis = 0
        first_n = 0
        for r in ok:
            for cl in (r.get("first_reads") or {}).values():
                if cl.get("extract_ok"):
                    first_n += 1
                    if cl.get("mismatch"):
                        first_mis += 1
        waste = [r.get("wasted_ops") or 0 for r in ok]
        tokens = [r.get("tokens") or 0 for r in ok]
        stats = _intent_stats(ok)
        def mean(xs):
            return sum(xs) / len(xs) if xs else float("nan")
        lines.append(
            f"  {arm} n={len(ok)} success {_rate(succ, len(ok))}; "
            f"wrong_release {wrong}/{len(ok)}; "
            f"first-read mismatch {_rate(first_mis, first_n)}; "
            f"waste mean {mean(waste):.2f}; tokens mean {mean(tokens):.1f}"
        )
        tk, tn = stats["tpr"]
        fk, fn = stats["fpr"]
        lines.append(
            f"    intents={stats['intents']} TPR {_rate(tk, tn)}  FPR {_rate(fk, fn)}"
            + (f"  lease-only granted on error {stats['leaseonly_granted_on_error']}"
               if arm == "leaseonly" else "")
        )
    text = "\n".join(lines)
    print(text)
    return text


def write_summary() -> str:
    _ensure_out()
    text = summarize_stage_a() + "\n" + summarize_stage_b() + "\n"
    path = os.path.join(OUT_DIR, "summary.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("Wrote", path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--stage", choices=("a", "b", "both"), default=None)
    ap.add_argument("--summarize", action="store_true")
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--arms", default="leaseonly,gate")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.summarize and not args.stage:
        write_summary()
        return 0
    if args.stage is None:
        ap.print_help()
        return 2
    clients = load_clients()
    missing = [n for n in ("qwen", "deepseek") if n not in clients]
    if missing:
        print("缺少 API key：", missing)
        return 2
    if args.stage in ("a", "both"):
        run_stage_a(clients)
        summarize_stage_a()
    if args.stage in ("b", "both"):
        arms = [a.strip() for a in args.arms.split(",") if a.strip()]
        run_stage_b(clients, arms, args.trials, resume=True)
        summarize_stage_b()
    write_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
