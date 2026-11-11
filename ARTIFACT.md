# Artifact for the CSCWD 2027 PRAP draft

This file is the official record of the confirmatory campaign finished on 2026-11-11 (Asia/Shanghai). Numbers below are taken from `prototype/unified_seed_rerun/`. Recompute them with:

```bash
cd prototype
python summarize_confirmatory.py
```

No API key is required for that check. The design can be inspected in `prototype/prap_core.py` and `prototype/testbed.py`. Trial logs are deterministic given the recorded seeds and operator traces.

## What is official

| Cell | Status | Where |
|---|---|---|
| Eight-agent, five arms, *n* = 30, shared injection | **Confirmatory** | `unified_seed_rerun/scale8_f30_*.json` |
| Topology T2/C4 later baselines: lease-only, no protocol, prompt, *n* = 30 | Later baselines, **not** a new ranking and not Fisher-tested | `unified_seed_rerun/t2c4_*.json` |
| Topology T2/C4 orchestrator, contract net, group chat, write gating, OCC+undo | Original campaign, not rerun | existing `prototype/` JSON files cited in the paper |
| Earlier eight-agent campaign whose seed included the arm name | **Not confirmatory**; kept only as a record | older `prototype/scale8_f30_*.json` (not under `unified_seed_rerun/`) |

The eight-agent confirmatory seed is `unified-8-0.3-{trial}` (for trial 0…29). The arm name is not in the seed, so all five arms share one injection set. This is a disclosed deviation from the frozen preregistration in `PRAP_prereg_R1.md`, which used `8-0.3-{arm}-{trial}`. The earlier arm-in-seed campaign is the one that is **not** confirmatory.

Topology T2/C4 injection was already deterministic (always agent B, a +1 *x* shift) and was not changed.

## Measurement contract

- Models: Qwen-plus and DeepSeek-chat, temperature 0.2, protocol/cleaner v0.2.3.
- Default `belief_mode` is `sdk_cache`: the gate compares the cached observation already used for planning (which already contains the injection). A self-reported belief is logged and is not the grant criterion.
- Eight-agent assignment: agent *i* in trial *t* uses Qwen if `(i + t) % 2 == 0`, otherwise DeepSeek.
- The injected fault is a +1 shift of the *x* coordinate. On the 5×5 map, an injected shift for apple 4 at (4, 3) is a null fault (`I4` clamp). Expected injection rate 0.30; expected effective rate 0.30 × 0.75 = 0.225.
- Waste (preregistered) counts `op` / `op_free` log entries with `ok=False`. Refused intents are not waste. Use `wasted_ops`, not `wasted_ops_free_only`.
- Success means all items delivered with no unreleased hold.
- The three preregistered eight-agent Fisher tests are write gating vs lease-only, write gating vs orchestrator, and orchestrator vs lease-only. Bonferroni would set α = 0.0167; all three are already non-significant unadjusted. Unregistered comparisons are descriptive only.
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

The preregistered contrast is still orchestrator against write gating (27/30 against 16/30, *p* = 0.003). Later lease-only and no-protocol baselines finish as often as the orchestrator. The gap is mainly the cost of write gating cancelling rescue, not a unique benefit of the orchestrator relative to running without a lock.

## How to check a trial

Each eight-agent record in `unified_seed_rerun/scale8_f30_*.json` includes `seed`, `success`, `wasted_ops`, `tokens`, `llm_calls`, `n_injected`, `n_effective`, and per-agent injection flags. The shared plan is also written to `unified_seed_rerun/injection_plan_scale8.json`. `summarize_confirmatory.py` asserts that, for every trial, the five arms have identical injection flags and the same seed string.

## Live rerun (optional, costs API usage)

```bash
cd prototype
python run_unified_confirmatory.py --suite inject
python run_unified_confirmatory.py --suite scale8
python run_unified_confirmatory.py --suite t2c4
```

Write keys to `prototype/api_keys.json` (gitignored) or set `QWEN_API_KEY` and `DEEPSEEK_API_KEY`. Do not overwrite `unified_seed_rerun/` if you are comparing against the official logs; point the runner at a new output directory or copy the folder first.

## Files that must not be committed

`prototype/api_keys.json` and environment files listed in `.gitignore`.
