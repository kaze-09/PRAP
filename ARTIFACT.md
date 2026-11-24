# Artifact for the CSCWD 2027 PRAP draft

This file is the official record of the eight-agent corrective replication finished on 2026-11-11 (Asia/Shanghai), of a later combination arm finished the same day, of the design-file cell later expanded to *n* = 100 and finished on 2026-11-12 at 01:38 (Asia/Shanghai), and of a later descriptive design-file plan-slip cell finished on 2026-11-11 at 19:06 (Asia/Shanghai), and of a later natural-read experiment finished on 2026-11-12 (Asia/Shanghai). Official five-arm and later-baseline numbers are taken from `prototype/unified_seed_rerun/`. The later gate-plus-reassignment logs are in `prototype/combo_gate_orch/`. The official design-file logs, including the recovery arms, are in `prototype/design_doc_rerun/`. The later natural-read logs are in `prototype/natural_error_runs/`. Recompute the grid counts with:

```bash
cd prototype
python summarize_confirmatory.py
```

No API key is required for that check. The design can be inspected in `prototype/prap_core.py` and `prototype/testbed.py`. Trial logs are deterministic given the recorded seeds and operator traces. The randomized invariant-check log added on 2026-11-23 is a reproduction, not the 2026-10-28 historical run. It is `prototype/fuzz_invariants_400x300.log`.

## What is official

| Cell | Status | Where |
|---|---|---|
| Eight-agent, five arms, *n* = 30, shared injection | **Official (corrective replication)** | `unified_seed_rerun/scale8_f30_*.json` |
| Topology T2/C4 later baselines: lease-only, no protocol, prompt, *n* = 30 | Later baselines, **not** a new ranking and not Fisher-tested | `unified_seed_rerun/t2c4_*.json` |
| Later combination: lease + write gate + official reassignment, *n* = 30 on both cells | Later measurement, **not** a prospectively registered contrast; not a confirmatory success-rate test | `combo_gate_orch/t2c4_gateorch.json` and `combo_gate_orch/scale8_f30_gateorch.json` |
| Design-file cell: lease-only vs write gating on a shared parametric coupling-plate, *n* = 100 | Main RQ3 / design-domain evidence; added after the grid campaigns and then expanded from *n* = 30; **not** a prospectively registered analysis and **not** a confirmatory test | `design_doc_rerun/design_leaseonly.json` and `design_doc_rerun/design_gate.json` |
| Design-file recovery: isolation then reassignment, and pinning without isolation, *n* = 100 | Same *n* = 100 design-file cell as above; **not** a prospectively registered contrast | `design_doc_rerun/design_gaterassign.json` and `design_doc_rerun/design_gatepin.json` |
| Later design-file plan-slip cell: lease-only vs write gating under an injected +1 mm slip on B's submitted wall and outer, cache left correct, *n* = 10 | Later descriptive measurement, **not** a prospectively registered contrast and **not** a confirmatory test; not a naturally occurring model error | `design_doc_rerun/design_leaseonly_planslip.json` and `design_doc_rerun/design_gate_planslip.json` |
| Later natural-read Stage A: extracts from uniquely determined checkout notes, 10 instances × 5 templates × 2 models = 100, temperature 0.2 | Later exploratory add-on, **not** a prospectively registered confirmatory analysis; primary mismatches 0/100 | `natural_error_runs/stage_a.jsonl` |
| Later natural-read Stage B: closed-loop follow-on, lease-only vs write gating, *n* = 30 | Later exploratory add-on; first-read mismatches 0/60 per arm (0/120 combined); official log TPR/FPR are **not** valid detection rates (the checkout note did not display the live ledger outer after writes) and must not be cited | `natural_error_runs/stage_b_leaseonly.json` and `natural_error_runs/stage_b_gate.json` |
| Randomized invariant checks: 400 seeds × 300 events | Reproduction on 2026-11-23 (UTC) of `fuzz_invariants.py`; the 2026-10-28 historical stdout was never committed. Zero violations on the checks that run with `max_hold` unset. The success banner names I6, which this configuration does not execute | `fuzz_invariants_400x300.log` |
| Topology T2/C4 orchestrator, contract net, group chat, write gating, OCC+undo | Original campaign, not rerun; the public preregistration already records this outcome and is not a prospective registration of it | existing `prototype/` JSON files cited in the paper |
| Earlier eight-agent campaign whose seed included the arm name | **Not official**; kept only as a record | older `prototype/scale8_f30_*.json` (not under `unified_seed_rerun/`) |

## Instrument-correctness checks (paper Section III-D)

These are offline checks of the arbiter, not task-level results. They back the paper's statements that randomized invariant checks found no violation of the stated invariants, that an infinite lease deadlocks in 10 of 10 hang trials, and that a finite lease is reclaimed in 10 of 10.

- **Randomized invariant fuzzer.** Code: `prototype/fuzz_invariants.py` (checks I1 mutual exclusion, I2 no write outside a live lease, I3 WAL-consistent replay, I4 version monotonicity, I5 isolation of new grants, I6 the hold limit, I7 deterministic replay). Raw stdout is retained under `prototype/`:
  - `fuzz_400x300_maxhold_none.txt` — `python fuzz_invariants.py --seeds 400 --events 300`: 400 seeds × 300 events, zero violations of I1–I7.
  - `fuzz_400x300_maxhold50.txt` — `python fuzz_invariants.py --seeds 400 --events 300 --max-hold 50`: 400 seeds × 300 events, zero violations. **The hold-limit check I6 is only active when `--max-hold` is set**, so this second run is the one that substantiates the paper's "hold limit" clause; the fuzzer's summary line prints I6 in both runs regardless of configuration. Older notes described an earlier 200+100+100 seed accumulation; the retained logs above are a single 400-seed rerun of the same code.
- **Infinite lease under hang (10/10 deadlock).** Runner: `prototype/run_e4.py`; `PessimisticLockAgent` sets `ttl_override = 10**9` (`run_e4.py:38-43`) and the deadlock flag is recorded at `run_e4.py:276-279`. Raw logs: `prototype/e4_pessim_T1_C3.json` (T1/C3, 5 trials, all `deadlock: true`) and `prototype/e4_pessim_T2_C3.json` (T2/C3, 5 trials, all `deadlock: true`) — 5 + 5 = 10 hang trials, all deadlocked, task success 0/5 in each file.
- **Finite lease under hang (10/10 reclaimed).** Runner: `prototype/run_e1.py` scenario 1 (`run_e1.py:47-60`): the holder is stuck after granting, and the waiting agent takes over at TTL expiry. Raw log: `prototype/e1_T3_C3_n10.json` (10 trials; `expires: 1` in every trial, waiting agent B completes in 10/10). Caveat: this is the T3 two-item chain, so task-level `success` is 3/10; the instrument claim here is only that the lease was reclaimed and the hold handed over, not that the whole chained task finished. `prototype/e1_T2_C3_n10.json` (10 trials, `expires >= 1` in all 10) is an additional finite-lease reclamation log. These checks are about loss of contact (crash/hang), not about a plan formed from a wrong observation.

The official eight-agent seed is `unified-8-0.3-{trial}` (for trial 0…29). The arm name is not in the seed, so all five arms share one injection set. This is a corrective replication: the frozen preregistration in `PRAP_prereg_R1.md` specified `8-0.3-{arm}-{trial}`, so that file does not prospectively register these official counts. The earlier arm-in-seed campaign is **not** the official result. The three eight-agent Fisher tests named in that document are still reported on this replication.

The paper (Section IV-A) now discloses both campaigns. Under the preregistered arm-in-seed formula the first campaign gave write gating 24/30, lease-only 15/30 (Fisher *p* = 0.029), prompt 22/30, orchestrator 19/30 and no protocol 19/30; the corrective replication gave 21/30, 22/30, 16/30, 19/30 and 19/30 respectively. The replication is official because the first seed gave each arm a different injection set, which confounds the comparison. The paper claims no confirmatory test and re-analyses the paired replication with exact McNemar (discordant trials 1/30, 4/30, 3/30; *p* = 1.00, 0.63, 0.25); the three preregistered unadjusted Fisher values (1.00, 0.78, 0.58) are retained for comparison only.

Topology T2/C4 injection was already deterministic (always agent B, a +1 *x* shift) and was not changed.

## Measurement contract

- Models: Qwen-plus and DeepSeek-chat, temperature 0.2, protocol/cleaner v0.2.3.
- Default `belief_mode` is `sdk_cache`: the gate compares the cached observation already used for planning (which already contains the injection). A self-reported belief is logged and is not the grant criterion.
- Eight-agent assignment: agent *i* in trial *t* uses Qwen if `(i + t) % 2 == 0`, otherwise DeepSeek.
- The injected fault is a +1 shift of the *x* coordinate. On the 5×5 map, an injected shift for apple 4 at (4, 3) is a null fault (`I4` clamp). Expected injection rate 0.30; expected effective rate 0.30 × 0.75 = 0.225.
- Waste (the metric named in the preregistration) counts `op` / `op_free` log entries with `ok=False`. Refused intents are not waste. Use `wasted_ops`, not `wasted_ops_free_only`.
- Success means all items delivered with no unreleased hold.
- The three eight-agent Fisher tests named in the preregistration, computed on this corrective replication, are write gating vs lease-only, write gating vs orchestrator, and orchestrator vs lease-only. Bonferroni would set α = 0.0167; all three are already non-significant unadjusted. Other comparisons are descriptive only.
- The main testbed is a 5×5 grid of apples. The main RQ3 design-file cell uses a shared parametric JSON coupling-plate file, not a CAD kernel; see below. Stopped pilots (Overcooked, CuisineWorld, repository splice/conflict) are not results.

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


## Design-file cell (lease vs gate, *n* = 100)

Added after the ranked grid cells (*n* = 30 finished on 2026-11-11 at 14:47, Asia/Shanghai) and then expanded in place to *n* = 100, finished on 2026-11-12 at 01:38 (Asia/Shanghai). Trials 0…29 are unchanged from the *n* = 30 snapshot. This cell is the main RQ3 evidence. It is **not** a prospectively registered analysis and is **not** a confirmatory test. The object is a shared JSON parametric coupling-plate file, not a CAD kernel. Agent A writes only the bore (`doc:bore_mm`); B writes only wall and outer diameter (`doc:wall_mm`, `doc:outer_mm`); either may include status and revision when releasing. The change order is to reach (12, 5, 22) mm and release. There is no authority-side dimension check: a geometrically inconsistent file can be released, and a wrong release is counted the same way a wrong consume is counted. B's observed current bore is shifted by +1 mm into the cache at observation time (the shift tracks the true value after A updates). Models, temperature 0.2, `belief_mode=sdk_cache`, and the even/odd A=Qwen vs DeepSeek swap match the topology cell. Seeds are `design-doc-{trial}` (the arm name is not in the seed). Horizon 50. Scripts: `prototype/design_doc.py` and `prototype/run_design_doc.py`. Logs: `prototype/design_doc_rerun/`. Do not overwrite `unified_seed_rerun/` or `combo_gate_orch/`.

Success means the parameters are correct, the file is released, and no lease is left held. A wrong release means the file is released and the parameters are not correct.

**Lease-only:** 81/100 success (Wilson 95% CI [72%, 87%]), 2/100 wrong releases (only trials 7 and 19, released outer 21 mm and 23 mm; both are in the original trials 0…29), parameters correct in 91/100, waste 1.14 (1.43), tokens 6147.25 (1766.34), LLM calls 7.13 (1.80), grants 6.53, denies 0, errors 0. Tokens per completed file (sum of tokens over 100 trials / number of successes) equal 7589.20.

**Write gating:** 0/100 success (Wilson 95% CI [0%, 4%]), 0/100 wrong releases, 0/100 parameters correct. Every trial ends at bore 12 mm, wall 4 mm, outer 18 mm, status `in_design`, revision 0. A updates the bore in 100/100 trials; B never writes wall or outer and is isolated in 100/100. Grants 4.0, denies 4.0, quarantined 1.00, waste 0.00, tokens 7203.50 (1001.44), LLM calls 8.33 (0.53), errors 0.

Fisher two-sided: success 81 vs 0, *p* ≈ 1.08 × 10^{-37}; wrong release 2 vs 0, *p* ≈ 0.50 (not significant); parameters correct 91 vs 0, *p* ≈ 9.42 × 10^{-47}. The honest claim is isolation at the cost of completion, the same pattern as 16/30 on the two-agent grid. Do not claim a success-rate win for the gate, and do not treat the wrong-release contrast as a statistical win. The strong irreversible-action number remains the consume cell (0/30 vs 30/30). The fault is an injected cache shift, with the same circularity as the grid cells.

## Design-file recovery arms (reassignment and pinning, *n* = 100)

Added after the lease-versus-gate cell (*n* = 30 finished on 2026-11-11, Asia/Shanghai: reassignment at 16:10, pinning at 16:46) and then expanded in place to *n* = 100 with that cell, finished on 2026-11-12 at 01:38 (Asia/Shanghai). Trials 0…29 are unchanged from the *n* = 30 snapshot. Same seeds `design-doc-{trial}`, same injected +1 mm bore shift, `belief_mode=sdk_cache`. These arms are part of the main RQ3 design-file evidence and are **not** a prospectively registered contrast.

**Gate + reassignment** (`recovery_mode` stays `quarantine`; on first isolation, B's wall, outer, and release rights are handed to healthy A; no extra orchestrator LLM): 100/100 success (Wilson 95% CI [96%, 100%]), 0/100 wrong releases, parameters correct and consistent in 100/100. Every trial ends at (12, 5, 22) mm, status `released`, revision 1. B is isolated in 100/100; mean reassignments 1.00 (0.00); corrections 0; quarantined 1; waste 2.36 (0.64); tokens 5723.42 (676.45); LLM calls 7.11 (0.35); grants 4.73; denies 4.36. Tokens per completed file (sum of tokens over 100 trials / number of successes) equal 5723.42. Waste is mainly failed `release_revision` attempts after the dimensions were already correct (`status_not_leased` or `bad_status_released`), plus some late `set_param` after release, not a wrong dimension.

**Gate + pinning** (`recovery_mode=correct_pin`: the arbiter does not isolate B; it pushes authority values and requires the next plan to use them; `DesignAgent` also pins `doc:bore_mm`, which B does not lease): 100/100 success (Wilson 95% CI [96%, 100%]), 0/100 wrong releases, parameters correct and consistent in 100/100. Every trial ends at (12, 5, 22) mm, status `released`, revision 1. B is never isolated; reassignments 0; mean corrections 2.92 (0.68); quarantined 0; waste 0.83 (0.51); tokens 6618.07 (1436.62); LLM calls 8.32 (1.76); grants 5.07; denies 0.70. Tokens per completed file equal 6618.07.

Pinning is not a substitute for the gate. Detection is still the grant-time cache mismatch; pinning overwrites the next plan with authority values and needs the corrupted field in that snapshot. Reassignment needs only a disagreement and a healthy writer.

Fisher two-sided: both recovery arms versus write gating, success *p* ≈ 2.21 × 10^{-59}; both versus lease-only, success *p* ≈ 1.48 × 10^{-6}; both versus lease-only, wrong release *p* ≈ 0.50 (not significant). Waste favors pinning (0.83) over lease-only (1.14) over reassignment (2.36). Tokens per completed file favor reassignment (5723.42) over pinning (6618.07) over lease-only (7589.20; equivalently the lease-only mean 6147.25 × 100 / 81). Zero waste after reassignment holds on the two-agent map (28/30), not on this file or the eight-agent combination (waste 3.37). Do not treat these arms as a registered contrast, and do not claim that pinning always dominates or that reassignment always has zero waste.



## Later design-file plan-slip cell (descriptive, n = 10)

Added after the observation-bias design-file cells and finished on 2026-11-11 at 19:06 (Asia/Shanghai). This *n* = 10 cell was not part of the later *n* = 100 expansion. This cell is **not** a prospectively registered contrast, is **not** a confirmatory analysis, and does **not** measure naturally occurring model errors. The fault is an injected persistent +1 mm slip applied to B's submitted `set_param` values on wall and outer after planning; the observation cache is left correct, and A is not slipped. Seeds are `design-doc-planslip-{trial}` (the arm name is not in the seed). Default *n* = 10. Horizon, models, temperature 0.2, `belief_mode=sdk_cache`, and the even/odd model swap match the other design-file cells. Observation-bias injection is off. Logs: `prototype/design_doc_rerun/design_leaseonly_planslip.json` and `prototype/design_doc_rerun/design_gate_planslip.json`. Do not overwrite `design_leaseonly.json`, `design_gate.json`, `design_gaterassign.json`, or `design_gatepin.json`. `--suite all` does not include this cell.

Because the cache matches the ledger, the grant-time gate should grant. It did: 0 denies and 0 isolations on both arms.

**Lease-only + plan slip:** 0/10 success, 7/10 wrong releases, 3/10 not released. Every trial ends at bore 12 mm, wall 6 mm, outer 23 mm (target 12, 5, 22). The three unreleased trials are already at those wrong dimensions, status `in_design`, revision 0. Waste 0.00 (0.00), tokens 5260.60 (1280.85), LLM calls 6.70 (1.70), mean plan slips 5.30 (2.16), grants 6.2, denies 0, errors 0.

**Write gating + plan slip:** 0/10 success, 6/10 wrong releases, 4/10 not released. Every trial ends at the same (12, 6, 23) mm document. Waste 0.00 (0.00), tokens 5821.90 (1422.42), LLM calls 7.20 (1.62), mean plan slips 6.60 (1.90), grants 7.0, denies 0, quarantined 0, errors 0.

Do not treat 7/10 versus 6/10 wrong releases as a comparison between the arms. The result supports the measurement contract: the gate compares the cache with the ledger and does not inspect whether the plan is reasonable.


## Later natural-read experiment (exploratory)

Added after the injected-fault cells and finished on 2026-11-12 (Asia/Shanghai). Frozen protocol: `prototype/NATURAL_ERROR_PROTOCOL.md` (frozen 2026-11-12 16:26 Asia/Shanghai). Runners: `prototype/natural_doc.py` and `prototype/run_natural_error.py`. `prototype/design_doc.py` gained an optional `document_spec` observation hook used only when that argument is set. This experiment is **not** a prospectively registered confirmatory analysis. Errors had to come from the model's reading of a checkout note; the runtime did not inject a +1 into the cache. Models and temperature match the main cells: Qwen-plus and DeepSeek-chat, temperature 0.2.

**Stage A (read-only extracts).** Five templates (clean; superseded voided Rev A; dual-unit, mm authoritative; mating-reference, truth is the current part; inspection-table, as-inspected authority) times 10 instances times two models yield 100 extracts. Extract failures: 0/100. Primary mismatches, any of bore, wall, or outer, are 0/100 = 0.0% (Wilson 95% CI [0.0%, 3.7%]); each template is 0/20 and each model is 0/50 (Wilson 95% CI [0.0%, 7.1%]). Log: `prototype/natural_error_runs/stage_a.jsonl`.

**Stage B (closed-loop follow-on).** Same models and temperature, no injection, lease-only then write gating, *n* = 30 each. Seeds are `natural-err-20261112-{arm}-{t}` (the arm name is in the seed; this cell does not share one injection set, because there is no injection). First-read mismatches: 0/60 on each arm (Wilson 95% CI [0.0%, 6.0%]). Lease-only: 24/30 success (Wilson 95% CI [62.7%, 90.5%]), 6/30 wrong releases, waste mean 1.00, tokens mean 6538.8. Write gating: 24/30 success (same CI), 0/30 wrong releases, waste mean 0.80, tokens mean 7671.9. Failures were plan slips or omitted outer writes, not extract mismatches.

Do not cite official log TPR/FPR from this cell as detection rates. `instance_from_snapshot` rebuilds the checkout note from ledger bore and wall only, so `Instance.outer` is always `bore+2*wall`; after a partial or wrong outer write, the note displays a geometrically consistent outer, not the live ledger outer. The paper reports the Stage A 0/100 primary-mismatch rate and the closed-loop first-read mismatches, 0/60 per arm and 0/120 combined. It withholds gate true-positive and false-positive rates because of the renderer artefact above. Logs are kept for reproducibility: `prototype/natural_error_runs/stage_b_leaseonly.json`, `stage_b_gate.json`, and `stage_b_run.log`. The runner's `summary.txt` prints those official-log TPR/FPR figures; they must not be cited.

## Randomized invariant checks (reproduction, 2026-11-23)

Commit `a12e01e` (2026-10-28) says the invariant fuzzer found zero violations on 400 seeds × 300 events. The runner is `prototype/fuzz_invariants.py` (blob `9940290cf3b54ee2692179efcb8456f168ffbec7`, unchanged since that commit). The commit added the script and no result log. No stdout from that historical run is in a later commit, in leftover files on this checkout, or in GitHub Actions artifacts. The repository has no workflow runs.

The script default is `--seeds 200`. The reproduction passes `--seeds 400`. `--events` stays at its default of 300. `--max-hold` stays unset, so `Arbiter.max_hold` is `None`. In `check_invariants`, the I6 `hold_total` comparison runs only when `max_hold` is not `None`. The success line printed by the script still names I6. This transcript does not establish the max-hold bound.

This run used the script as it stands on `00928f4ea2adf50cf7873f8083f263ca13cdf203`. It is a new execution. It is not the original 2026-10-28 run.

- Command, from `prototype/`: `python3 fuzz_invariants.py --seeds 400 --events 300`
- Date: 2026-11-23T07:30:44Z through 2026-11-23T07:30:49Z
- Interpreter: Python 3.12.3
- Seeds: `range(400)` (0 through 399), 300 events each. Each seed is executed a second time for invariant I7.
- Exit code: 0. Stderr was empty.
- Outcome: the script reported no violation. The raw stdout is `prototype/fuzz_invariants_400x300.log`.

## Hang trials: infinite lease versus finite lease

The draft describes an infinite lease deadlocking in 10 of 10 hang trials and a finite lease being reclaimed in 10 of 10. No transcript of that paired contrast was recovered from git history, from leftover files, or from CI artifacts. This environment has no `QWEN_API_KEY`, no `DEEPSEEK_API_KEY`, and no `prototype/api_keys.json`. The live runners were not started, and no hang log was added.

The infinite-lease runner is `prototype/run_e4.py`. `--baseline pessim` uses `PessimisticLockAgent`, which sets `ttl_override` to `10**9`. `--cond C3` leaves agent A stuck after the grant. A 10-trial rerun calls Qwen-plus and DeepSeek-chat:

```bash
cd prototype
python run_e4.py --baseline pessim --task T1 --cond C3 --trials 10 --out <new-file>.json
```

The same command with `--task T2` is the other published pessim cell. Keys go in `prototype/api_keys.json` (gitignored) or in `QWEN_API_KEY` and `DEEPSEEK_API_KEY`.

The pessim files already in the tree are five trials each. `e4_pessim_T1_C3.json` and `e4_pessim_T2_C3.json` each record deadlock on 5/5 trials and success on 0/5. Console copies are `e4_run1.log` and `e4_run2.log`. Commit `e3dd99f` summarizes those runs as “pessimistic lock deadlocks 10/10”. The saved JSON files are the two *n* = 5 cells. They are not a 10-trial arm paired with a finite-TTL arm.

The finite-TTL hang runner is `prototype/run_e1_full.py` with `--conds C3`. It also calls both models. A 10-trial rerun is:

```bash
python run_e1_full.py --task T1 --conds C3 --trials 10 --out <new-file>.json
```

Existing finite-TTL C3 logs already record one lease expiry on every trial: `e1_T1_C3_n30.json` (30/30), `e1_T2_C3_n10.json` (10/10), `e1_T3_C3_n10.json` (10/10), and `e1_T4_C3_n10.json` (10/10). Those files are the earlier PRAP C3 campaign. They are not paired with an infinite-lease arm on the same 10 trials.

`prototype/run_e1.py` checks one scripted finite-TTL hang (scenario 1) and has no infinite-lease arm. A fresh run on 2026-11-23 exited 0 (31/31 assertions, including scenario 1). That console log is the single-trial scripted check. It is not the draft's 10-of-10 contrast, and it is not added here. `prototype/run_llm_smoke.py --stuck` is one live finite-TTL hang and also needs the two API keys.

## How to check a trial

Each eight-agent record in `unified_seed_rerun/scale8_f30_*.json` includes `seed`, `success`, `wasted_ops`, `tokens`, `llm_calls`, `n_injected`, `n_effective`, and per-agent injection flags. The shared plan is also written to `unified_seed_rerun/injection_plan_scale8.json`. `summarize_confirmatory.py` asserts that, for every trial, the five arms have identical injection flags and the same seed string. Each later combination record in `combo_gate_orch/` includes the same success, waste, token, and (for the eight-agent cell) seed and injection fields, plus `reassignments` and `handoffs`. Each official design-file record in `design_doc_rerun/design_leaseonly.json` and `design_doc_rerun/design_gate.json` includes `seed`, `success`, `wrong_release`, `params_correct`, `wasted_ops`, `tokens`, `llm_calls`, `grants`, `denies`, `n_quarantined`, and the final `document`. Each official recovery-arm record in `design_doc_rerun/design_gaterassign.json` and `design_doc_rerun/design_gatepin.json` includes the same fields, plus `reassignments`, `n_corrections`, `recovery_mode`, and per-agent `inherited_from`. Each later plan-slip record in `design_doc_rerun/design_leaseonly_planslip.json` and `design_doc_rerun/design_gate_planslip.json` includes the same design-file fields, plus `plan_slip` and `n_plan_slips`. The eight-agent plan written to `combo_gate_orch/injection_plan_scale8.json` has the same per-trial injection lists as the official plan; only the summary note differs. `summarize_confirmatory.py` reprints the combination counts and checks that those injection flags match the official write-gating arm.
Each Stage A record in `natural_error_runs/stage_a.jsonl` includes the template, instance, model, extracted dimensions, and a primary-mismatch flag. Each Stage B record in `natural_error_runs/stage_b_leaseonly.json` and `stage_b_gate.json` includes `seed`, `success`, `wrong_release`, `wasted_ops`, `tokens`, and per-writer first-read fields. `python run_natural_error.py --summarize` reprints the Stage A and Stage B counts from those files without an API call. Official-log TPR/FPR in that reprint must not be cited as detection rates.

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

To reproduce the official *n* = 100 design-file cell, first copy or rename `prototype/design_doc_rerun/` if you need to keep the posted logs (the runner writes to that folder and resumes by default). `--suite all` and `--suite recovery` still default to *n* = 30 and must not be pointed at the posted files. Pass `--trials 100` for the official observation-bias cell. The official files were extended in place: trials 0…29 are the original records; trials 30…99 were appended. `--suite planslip` is unchanged (default *n* = 10) and was not part of the *n* = 100 campaign. Do not use `--no-resume` against the posted `design_leaseonly.json`, `design_gate.json`, `design_gaterassign.json`, or `design_gatepin.json`.

```bash
python run_design_doc.py --suite selftest
python run_design_doc.py --suite leaseonly --trials 100
python run_design_doc.py --suite gate --trials 100
python run_design_doc.py --suite recovery --trials 100
python run_design_doc.py --suite planslip
```

To reproduce the later natural-read experiment, first copy or rename `prototype/natural_error_runs/` if you need to keep the posted logs. `--selftest` and `--summarize` do not call the API. A live `--stage a` or `--stage b` run incurs API cost. Do not use a live rerun to replace the posted 0/100 extracts after seeing the rate.

```bash
python run_natural_error.py --selftest
python run_natural_error.py --summarize
python run_natural_error.py --stage a
python run_natural_error.py --stage b
```

## R2: release-time geometric ledger constraint (2026-11-12, exploratory)

Frozen protocol: `PRAP_prereg_R2.md` (2026-11-12 21:18:46 +08:00, written **before the first API call**). Runner `prototype/run_constraint.py`, summary `prototype/summarize_r2.py`, logs `prototype/constraint_runs/`. The constraint `outer = bore + 2*wall` is checked only at `release_revision`, behind `arb.dimension_constraint` (default off, so all earlier arms keep their behaviour). It is deliberately **not** checked on `set_param`: after A writes bore=12 and before B updates wall/outer, the intermediate state is necessarily inconsistent.

Two conditions, three arms each, `n=10`, one shared seed `constraint-20261112-{cond}-{trial}`, same batch, `qwen-plus` / `deepseek-chat` at temperature 0.2. P = plan slip (cache correct, B's submitted wall/outer `+1 mm`); O = `+1 mm` bore observation bias.

| Cond | Arm | Success | Wrong release | Constraint denials | Waste mean | Tokens mean | Calls mean |
|---|---|---|---|---|---|---|---|
| P | leaseonly | 0/10 | 5/10 | 0 | 0.00 | 5435 | 7.00 |
| P | gate | 0/10 | 6/10 | 0 | 0.00 | 5442 | 7.00 |
| P | gate+constraint | 0/10 | 0/10 | 13 (8/10 trials) | 1.30 | 6459 | 8.30 |
| O | leaseonly | 10/10 | 0/10 | 0 | 1.20 | 4858 | 6.00 |
| O | gate | 0/10 | 0/10 | 0 | 0.00 | 7098 | 8.40 |
| O | gate+constraint | 0/10 | 0/10 | 0 | 0.00 | 6627 | 8.40 |

False rejections (final state correct and consistent but not released): **0 in every arm**. In condition O the constraint never fired and `gate` / `gate+constraint` coincide, so it does not disturb the observation-bias cell. In condition P, 2/10 constrained trials were unreleased **without any constraint denial**, so the `0/10` wrong releases are not wholly attributable to the check. `wrong_release = 0` is a definitional result; the informative columns are waste, tokens, calls and the `10/10` unreleased files. The constraint turns a wrong release into an unfinished file; **it does not replace recovery**.

Offline regression: `python run_constraint.py --suite selftest` passes the original 7 design-file self-tests plus 3 new ones (healthy release passes; intermediate writes are not refused; inconsistent release is refused; constraint-off preserves the original wrong release). Do **not** cite the `10/10` leaseonly success in condition O as a replication of the `81/100` design-file result: `n=10`, different seed prefix and batch.

## Files that must not be committed

`prototype/api_keys.json` and environment files listed in `.gitignore`.
