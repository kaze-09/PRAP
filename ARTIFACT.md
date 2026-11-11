# Artifact for the CSCWD 2027 PRAP draft

This file is the official record of the eight-agent corrective replication finished on 2026-11-11 (Asia/Shanghai), and of a later combination arm finished the same day. Official five-arm and later-baseline numbers are taken from `prototype/unified_seed_rerun/`. The later gate-plus-reassignment logs are in `prototype/combo_gate_orch/`. Recompute both with:

```bash
cd prototype
python summarize_confirmatory.py
```

No API key is required for that check. The design can be inspected in `prototype/prap_core.py` and `prototype/testbed.py`. Trial logs are deterministic given the recorded seeds and operator traces.

## What is official

| Cell | Status | Where |
|---|---|---|
| Eight-agent, five arms, *n* = 30, shared injection | **Official (corrective replication)** | `unified_seed_rerun/scale8_f30_*.json` |
| Topology T2/C4 later baselines: lease-only, no protocol, prompt, *n* = 30 | Later baselines, **not** a new ranking and not Fisher-tested | `unified_seed_rerun/t2c4_*.json` |
| Later combination: lease + write gate + official reassignment, *n* = 30 on both cells | Later measurement, **not** a prospectively registered contrast; not a confirmatory success-rate test | `combo_gate_orch/t2c4_gateorch.json` and `combo_gate_orch/scale8_f30_gateorch.json` |
| Topology T2/C4 orchestrator, contract net, group chat, write gating, OCC+undo | Original campaign, not rerun; the public preregistration already records this outcome and is not a prospective registration of it | existing `prototype/` JSON files cited in the paper |
| Earlier eight-agent campaign whose seed included the arm name | **Not official**; kept only as a record | older `prototype/scale8_f30_*.json` (not under `unified_seed_rerun/`) |

The official eight-agent seed is `unified-8-0.3-{trial}` (for trial 0…29). The arm name is not in the seed, so all five arms share one injection set. This is a corrective replication: the frozen preregistration in `PRAP_prereg_R1.md` specified `8-0.3-{arm}-{trial}`, so that file does not prospectively register these official counts. The earlier arm-in-seed campaign is **not** the official result. The three eight-agent Fisher tests named in that document are still reported on this replication.

Topology T2/C4 injection was already deterministic (always agent B, a +1 *x* shift) and was not changed.

## Measurement contract

- Models: Qwen-plus and DeepSeek-chat, temperature 0.2, protocol/cleaner v0.2.3.
- Default `belief_mode` is `sdk_cache`: the gate compares the cached observation already used for planning (which already contains the injection). A self-reported belief is logged and is not the grant criterion.
- Eight-agent assignment: agent *i* in trial *t* uses Qwen if `(i + t) % 2 == 0`, otherwise DeepSeek.
- The injected fault is a +1 shift of the *x* coordinate. On the 5×5 map, an injected shift for apple 4 at (4, 3) is a null fault (`I4` clamp). Expected injection rate 0.30; expected effective rate 0.30 × 0.75 = 0.225.
- Waste (the metric named in the preregistration) counts `op` / `op_free` log entries with `ok=False`. Refused intents are not waste. Use `wasted_ops`, not `wasted_ops_free_only`.
- Success means all items delivered with no unreleased hold.
- The three eight-agent Fisher tests named in the preregistration, computed on this corrective replication, are write gating vs lease-only, write gating vs orchestrator, and orchestrator vs lease-only. Bonferroni would set α = 0.0167; all three are already non-significant unadjusted. Other comparisons are descriptive only.
- The testbed is a 5×5 grid of apples, not a collaborative design file. Stopped pilots (Overcooked, CuisineWorld, repository splice/conflict) are not results.

## Official eight-agent counts

Shared injection: 71/240 injected (0.296), 52/240 effective (0.217). Seeds and injection patterns match across the five arms. Zero errors. The two zero-effective trials (16 and 28) both succeeded under the gate.

| Arm | Success | Wilson 95% CI | Waste mean (SD) | Tokens mean (SD) | LLM calls mean (SD) |
|---|---:|---|---:|---:|---:|
| Lease-only | 22/30 | [56%, 86%] | 12.50 (7.13) | 14662 (3065) | 12.17 (2.38) |
| Write gating (lease + isolation) | 21/30 | [52%, 83%] | 0.00 (0.00) | 17043 (4550) | 14.20 (3.60) |
| Orchestrator | 19/30 | [46%, 78%] | 3.50 (3.35) | 9408 (3609) | 8.23 (3.00) |
| No protocol | 19/30 | [46%, 78%] | 8.60 (0.97) | 9896 (743) | 8.07 (0.25) |
| Prompt | 16/30 | [36%, 70%] | 7.63 (1.47) | 10173 (1885) | 8.10 (0.31) |

Fisher two-sided: write gating vs lease-only *p* = 1.00; write gating vs orchestrator *p* = 0.78; orchestrator vs lease-only *p* = 0.58. Descriptive: write gating vs prompt *p* = 0.29; write gating vs no protocol *p* = 0.78. Write-gating mean quarantines: 2.07.

The honest eight-agent claim is the waste gap, not a success-rate win over the lease or the orchestrator.

## Topology later baselines

Same deterministic C4 injection as the original campaign (agent B, +1 *x*). These three arms were missing from the original table and are not a new ranking.

| Arm | Success | Wilson 95% CI | Waste mean (SD) | A = Qwen | A = DeepSeek |
|---|---:|---|---:|---:|---:|
| Lease-only (later) | 29/30 | [83%, 99%] | 5.23 (1.52) | 15/15 | 14/15 |
| No protocol (later) | 28/30 | [79%, 98%] | 2.00 (0.00) | 15/15 | 13/15 |
| Prompt (later) | 21/30 | [52%, 83%] | 1.93 (0.25) | 15/15 | 6/15 |

Original campaign, not rerun: orchestrator 27/30, contract net 18/30, group chat 17/30, write gating 16/30, OCC+undo 15/30. Original model split when the remaining writer is DeepSeek: orchestrator 13/15, write gating 3/15 (lease-only later 14/15, no protocol later 13/15). When the remaining writer is Qwen: orchestrator 14/15, write gating 13/15.

The original contrast is orchestrator against write gating (27/30 against 16/30, *p* = 0.003). Later lease-only and no-protocol baselines finish as often as the orchestrator. The gap is mainly the cost of write gating cancelling rescue, not a unique benefit of the orchestrator relative to running without a lock.


## Later combination (gate + reassignment)

The official orchestrator writes without the gate: `OrchestratorAgent` / `OrchScaleAgent` inherit `NoProtocolAgent` and write through `op_free`. This later arm keeps the grant-time cache gate (`LLMAgent` path, `belief_mode=sdk_cache`, Layer-4 on) and uses the official assignment / reassignment path. Reassignment triggers after the same retry budget (`MAX_RETRIES=2`) when the assigned worker is isolated (`suspect_blocked`) or a granted plan fails at execution.

The arm was added after the ranked cells, on the same injections, *n* = 30 on each map. It is **not** a prospectively registered contrast and is **not** a confirmatory success-rate test. Unadjusted Fisher comparisons against other arms are descriptive only. Logs and the runner are `prototype/combo_gate_orch/` and `prototype/run_combo_gate_orch.py`. Do not overwrite `unified_seed_rerun/`.

**Topology T2/C4** (same deterministic C4 injection: always agent B, a +1 *x* shift): 28/30, Wilson 95% CI [79%, 98%], waste 0.00 (0.00), tokens 2151 (887), LLM calls 3.00 (1.02), reassignments 0.50 (0.51), quarantined 0.50 (0.51). All 15 trials first assigned to biased B isolated that writer and handed the item to A (14/15 recovered). All 15 trials first assigned to healthy A finished 14/15 with no reassignment. Model split: 14/15 vs 14/15. Failures: trial 8 (A granted; item finished at (3,0)) and trial 17 (B isolated, handed to A; item finished at (4,1)). Isolation plus reassignment recovered successes that isolation alone cancelled (write gating 16/30) and kept zero waste; do not treat 28/30 vs 16/30 as a confirmatory test.

**Eight-agent cell** (same seeds `unified-8-0.3-{trial}`, identical 71/240 injected and 52/240 effective; zero-effective trials 16 and 28 both succeeded; zero errors): 18/30, Wilson 95% CI [42%, 75%], waste 3.37 (3.47) (`wasted_ops`; `wasted_ops_free_only` is 0 on every trial), tokens 9905 (3620), LLM calls 8.70 (3.03), reassignments 1.00 (0.79) (22/30 trials reassigned at least once), quarantined 1.27 (1.08). Delivered-item counts: 18 trials delivered 4, 11 delivered 3, 1 delivered 2. No reassignment: 7/8 success, waste 0; with reassignment: 11/22 success, waste 4.59. This cell tracks the official orchestrator (19/30, waste 3.50), not the gate (21/30, waste 0.00). Composition does not raise success and keep zero waste.

## How to check a trial

Each eight-agent record in `unified_seed_rerun/scale8_f30_*.json` includes `seed`, `success`, `wasted_ops`, `tokens`, `llm_calls`, `n_injected`, `n_effective`, and per-agent injection flags. The shared plan is also written to `unified_seed_rerun/injection_plan_scale8.json`. `summarize_confirmatory.py` asserts that, for every trial, the five arms have identical injection flags and the same seed string. Each later combination record in `combo_gate_orch/` includes the same success, waste, token, and (for the eight-agent cell) seed and injection fields, plus `reassignments` and `handoffs`. The eight-agent plan written to `combo_gate_orch/injection_plan_scale8.json` has the same per-trial injection lists as the official plan; only the summary note differs. `summarize_confirmatory.py` reprints the combination counts and checks that those injection flags match the official write-gating arm.

## Live rerun (optional, costs API usage)

```bash
cd prototype
python run_unified_confirmatory.py --suite inject
python run_unified_confirmatory.py --suite scale8
python run_unified_confirmatory.py --suite t2c4
```

Write keys to `prototype/api_keys.json` (gitignored) or set `QWEN_API_KEY` and `DEEPSEEK_API_KEY`. Do not overwrite `unified_seed_rerun/` if you are comparing against the official logs; point the runner at a new output directory or copy the folder first.

To rerun the later combination arm, first copy or rename `prototype/combo_gate_orch/` if you need to keep the posted logs (the runner writes to that folder and has no separate output flag):

```bash
python run_combo_gate_orch.py --suite inject
python run_combo_gate_orch.py --suite all
```

## Files that must not be committed

`prototype/api_keys.json` and environment files listed in `.gitignore`.
