# Artifact for the CSCWD 2027 PRAP draft

This file is the official record of the eight-agent corrective replication finished on 2026-11-11 (Asia/Shanghai), of a later combination arm finished the same day, of a later design-file cell finished the same afternoon, of two later design-file recovery arms finished the same afternoon, and of a later descriptive design-file plan-slip cell finished the same evening. Official five-arm and later-baseline numbers are taken from `prototype/unified_seed_rerun/`. The later gate-plus-reassignment logs are in `prototype/combo_gate_orch/`. The later design-file logs, including the recovery arms, are in `prototype/design_doc_rerun/`. Recompute the grid counts with:

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
| Later design-file cell: lease-only vs write gating on a shared parametric coupling-plate, *n* = 30 | Later measurement, **not** a prospectively registered contrast; not a success-rate test of the gate | `design_doc_rerun/design_leaseonly.json` and `design_doc_rerun/design_gate.json` |
| Later design-file recovery: isolation then reassignment, and pinning without isolation, *n* = 30 | Later measurement, **not** a prospectively registered contrast | `design_doc_rerun/design_gaterassign.json` and `design_doc_rerun/design_gatepin.json` |
| Later design-file plan-slip cell: lease-only vs write gating under an injected +1 mm slip on B's submitted wall and outer, cache left correct, *n* = 10 | Later descriptive measurement, **not** a prospectively registered contrast and **not** a confirmatory test; not a naturally occurring model error | `design_doc_rerun/design_leaseonly_planslip.json` and `design_doc_rerun/design_gate_planslip.json` |
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
- The main testbed is a 5×5 grid of apples. A later cell uses a shared parametric JSON coupling-plate file, not a CAD kernel; see below. Stopped pilots (Overcooked, CuisineWorld, repository splice/conflict) are not results.

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


## Later design-file cell (lease vs gate)

Added after the ranked grid cells and finished on 2026-11-11 at 14:47 (Asia/Shanghai). The object is a shared JSON parametric coupling-plate file, not a CAD kernel. Agent A writes only the bore (`doc:bore_mm`); B writes only wall and outer diameter (`doc:wall_mm`, `doc:outer_mm`); either may include status and revision when releasing. The change order is to reach (12, 5, 22) mm and release. There is no authority-side dimension check: a geometrically inconsistent file can be released, and a wrong release is counted the same way a wrong consume is counted. B's observed current bore is shifted by +1 mm into the cache at observation time (the shift tracks the true value after A updates). Models, temperature 0.2, `belief_mode=sdk_cache`, and the even/odd A=Qwen vs DeepSeek swap match the topology cell. Seeds are `design-doc-{trial}` (the arm name is not in the seed). Horizon 50. Scripts: `prototype/design_doc.py` and `prototype/run_design_doc.py`. Logs: `prototype/design_doc_rerun/`. This cell is **not** a prospectively registered contrast and is **not** a success-rate test of the gate. Do not overwrite `unified_seed_rerun/` or `combo_gate_orch/`.

Success means the parameters are correct, the file is released, and no lease is left held. A wrong release means the file is released and the parameters are not correct.

**Lease-only:** 24/30 success (80%, Wilson 95% CI [63%, 90%]), 2/30 wrong releases (trials 7 and 19, released outer 21 mm and 23 mm), parameters correct in 26/30, waste 1.30 (1.42), tokens 6242.60 (1708.79), LLM calls 7.30 (1.90), grants 6.6, denies 0, errors 0. A non-target outer (21 or 23 mm) was successfully checked in at least once in 18/30 trials; that count is descriptive, not a ranked test.

**Write gating:** 0/30 success (Wilson 95% CI [0%, 11%]), 0/30 wrong releases, 0/30 parameters correct. Every trial ends at bore 12 mm, wall 4 mm, outer 18 mm, status `in_design`, revision 0. A updates the bore in 30/30 trials; B never writes wall or outer and is isolated in 30/30. Grants 4.0, denies 4.0, quarantined 1.00, waste 0.00, tokens 6977.70 (737.74), LLM calls 8.33 (0.55), errors 0.

Fisher two-sided: success 24 vs 0, *p* ≈ 3.29 × 10^{-11}; wrong release 2 vs 0, *p* ≈ 0.49 (not significant); parameters correct 26 vs 0, *p* ≈ 7.84 × 10^{-13}. The honest claim is isolation at the cost of completion, the same pattern as 16/30 on the two-agent grid. Do not claim a success-rate win for the gate, and do not treat the wrong-release contrast as a statistical win. The strong irreversible-action number remains the consume cell (0/30 vs 30/30). The fault is an injected cache shift, with the same circularity as the grid cells.

## Later design-file recovery arms (reassignment and pinning)

Added after the lease-versus-gate cell and finished on 2026-11-11 (Asia/Shanghai): reassignment at 16:10, pinning at 16:46. Same seeds `design-doc-{trial}`, same injected +1 mm bore shift, `belief_mode=sdk_cache`, *n* = 30. These arms are **not** a prospectively registered contrast. Do not overwrite `design_leaseonly.json` or `design_gate.json`.

**Gate + reassignment** (`recovery_mode` stays `quarantine`; on first isolation, B's wall, outer, and release rights are handed to healthy A; no extra orchestrator LLM): 30/30 success (Wilson 95% CI [89%, 100%]), 0/30 wrong releases, parameters correct and consistent in 30/30. Every trial ends at (12, 5, 22) mm, status `released`, revision 1. B is isolated in 30/30; mean reassignments 1.00 (0.00); corrections 0; quarantined 1; waste 2.27 (0.52); tokens 5709.83 (847.50); LLM calls 7.13 (0.43); grants 4.77; denies 4.43. Tokens per completed file (sum of tokens over 30 trials / number of successes) equal 5709.83. Waste is mainly failed `release_revision` attempts after the dimensions were already correct (`status_not_leased` or `bad_status_released`), plus some late `set_param` after release, not a wrong dimension.

**Gate + pinning** (`recovery_mode=correct_pin`: the arbiter does not isolate B; it pushes authority values and requires the next plan to use them; `DesignAgent` also pins `doc:bore_mm`, which B does not lease): 30/30 success (Wilson 95% CI [89%, 100%]), 0/30 wrong releases, parameters correct and consistent in 30/30. Every trial ends at (12, 5, 22) mm, status `released`, revision 1. B is never isolated; reassignments 0; mean corrections 2.97 (0.72); quarantined 0; waste 0.90 (0.55); tokens 6778.67 (1364.31); LLM calls 8.40 (1.59); grants 5.07; denies 0.67. Tokens per completed file equal 6778.67.

Pinning is not a substitute for the gate. Detection is still the grant-time cache mismatch; pinning overwrites the next plan with authority values and needs the corrupted field in that snapshot. Reassignment needs only a disagreement and a healthy writer.

Fisher two-sided: both recovery arms versus write gating, success *p* ≈ 1.69 × 10^{-17}; both versus lease-only, success *p* = 0.024; both versus lease-only, wrong release *p* = 0.49 (not significant). Waste favors pinning (0.90) over lease-only (1.30) over reassignment (2.27). Tokens per completed file favor reassignment (5709.83) over pinning (6778.67) over lease-only (7803.25; equivalently the lease-only mean 6242.60 × 30 / 24). Zero waste after reassignment holds on the two-agent map (28/30), not on this file or the eight-agent combination (waste 3.37). Do not treat these arms as a registered contrast, and do not claim that pinning always dominates or that reassignment always has zero waste.



## Later design-file plan-slip cell (descriptive, n = 10)

Added after the observation-bias design-file cells and finished on 2026-11-11 at 19:06 (Asia/Shanghai). This cell is **not** a prospectively registered contrast, is **not** a confirmatory analysis, and does **not** measure naturally occurring model errors. The fault is an injected persistent +1 mm slip applied to B's submitted `set_param` values on wall and outer after planning; the observation cache is left correct, and A is not slipped. Seeds are `design-doc-planslip-{trial}` (the arm name is not in the seed). Default *n* = 10. Horizon, models, temperature 0.2, `belief_mode=sdk_cache`, and the even/odd model swap match the other design-file cells. Observation-bias injection is off. Logs: `prototype/design_doc_rerun/design_leaseonly_planslip.json` and `prototype/design_doc_rerun/design_gate_planslip.json`. Do not overwrite `design_leaseonly.json`, `design_gate.json`, `design_gaterassign.json`, or `design_gatepin.json`. `--suite all` does not include this cell.

Because the cache matches the ledger, the grant-time gate should grant. It did: 0 denies and 0 isolations on both arms.

**Lease-only + plan slip:** 0/10 success, 7/10 wrong releases, 3/10 not released. Every trial ends at bore 12 mm, wall 6 mm, outer 23 mm (target 12, 5, 22). The three unreleased trials are already at those wrong dimensions, status `in_design`, revision 0. Waste 0.00 (0.00), tokens 5260.60 (1280.85), LLM calls 6.70 (1.70), mean plan slips 5.30 (2.16), grants 6.2, denies 0, errors 0.

**Write gating + plan slip:** 0/10 success, 6/10 wrong releases, 4/10 not released. Every trial ends at the same (12, 6, 23) mm document. Waste 0.00 (0.00), tokens 5821.90 (1422.42), LLM calls 7.20 (1.62), mean plan slips 6.60 (1.90), grants 7.0, denies 0, quarantined 0, errors 0.

Do not treat 7/10 versus 6/10 wrong releases as a comparison between the arms. The result supports the measurement contract: the gate compares the cache with the ledger and does not inspect whether the plan is reasonable.

## How to check a trial

Each eight-agent record in `unified_seed_rerun/scale8_f30_*.json` includes `seed`, `success`, `wasted_ops`, `tokens`, `llm_calls`, `n_injected`, `n_effective`, and per-agent injection flags. The shared plan is also written to `unified_seed_rerun/injection_plan_scale8.json`. `summarize_confirmatory.py` asserts that, for every trial, the five arms have identical injection flags and the same seed string. Each later combination record in `combo_gate_orch/` includes the same success, waste, token, and (for the eight-agent cell) seed and injection fields, plus `reassignments` and `handoffs`. Each later design-file record in `design_doc_rerun/design_leaseonly.json` and `design_doc_rerun/design_gate.json` includes `seed`, `success`, `wrong_release`, `params_correct`, `wasted_ops`, `tokens`, `llm_calls`, `grants`, `denies`, `n_quarantined`, and the final `document`. Each later recovery-arm record in `design_doc_rerun/design_gaterassign.json` and `design_doc_rerun/design_gatepin.json` includes the same fields, plus `reassignments`, `n_corrections`, `recovery_mode`, and per-agent `inherited_from`. Each later plan-slip record in `design_doc_rerun/design_leaseonly_planslip.json` and `design_doc_rerun/design_gate_planslip.json` includes the same design-file fields, plus `plan_slip` and `n_plan_slips`. The eight-agent plan written to `combo_gate_orch/injection_plan_scale8.json` has the same per-trial injection lists as the official plan; only the summary note differs. `summarize_confirmatory.py` reprints the combination counts and checks that those injection flags match the official write-gating arm.

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

To rerun the later design-file cells, first copy or rename `prototype/design_doc_rerun/` if you need to keep the posted logs (the runner writes to that folder and resumes by default). `--suite all` runs only lease-only and write gating. `--suite recovery` runs only the two recovery arms. `--suite planslip` runs the later plan-slip cell (default *n* = 10). Do not overwrite `design_leaseonly.json`, `design_gate.json`, `design_gaterassign.json`, or `design_gatepin.json`.

```bash
python run_design_doc.py --suite selftest
python run_design_doc.py --suite all
python run_design_doc.py --suite recovery
python run_design_doc.py --suite planslip
```

## Files that must not be committed

`prototype/api_keys.json` and environment files listed in `.gitignore`.
