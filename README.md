# PRAP: Preemption and Resource Arbitration Protocol

A protocol and measurement testbed for exclusive writes among multiple LLM agents. An authority ledger, TTL leases, and a write gate mediate commits; the gate compares the cached observation already used for planning with the ledger and can refuse a write before it executes.

This repository is the artifact for a CSCWD 2027 draft. The official counts, seed formula, and replay instructions are in [`ARTIFACT.md`](ARTIFACT.md).

**Official eight-agent result (shared injection set, *n* = 30 per arm).** Success rates do not separate (lease-only 22/30, write gating 21/30, orchestrator 19/30, no protocol 19/30, prompt 16/30; the three named Fisher tests, on this corrective replication, *p* ≥ 0.58). What separates is execution waste: write gating 0.00 (SD 0.00) against lease-only 12.50 (7.13) and orchestrator 3.50 (3.35). Write-gating planning tokens are higher (17043 against the orchestrator's 9408). Do not cite a success-rate win on this cell.

**Topology cell.** The original contrast is orchestrator 27/30 against write gating 16/30 (*p* = 0.003). Later baselines on the same injection reach lease-only 29/30 and no protocol 28/30, nearly even with the orchestrator. That gap is mainly the cost of write gating cancelling rescue, not a unique benefit of the orchestrator relative to running without a lock.

**Later combination (gate + reassignment), not prospectively registered.** On the two-agent map the arm recovers 28/30 with zero waste. On the eight-agent map it matches the orchestrator (18/30 vs 19/30) and wastes 3.37; composition does not both raise success and keep zero waste. Logs are in [`prototype/combo_gate_orch/`](prototype/combo_gate_orch/). See [ARTIFACT.md](ARTIFACT.md).

**Design-file cell (main RQ3 evidence, *n* = 100), not prospectively registered.** On a shared parametric coupling-plate file, lease-only releases a correct revision in 81/100 trials and a geometrically inconsistent one in 2/100 (not significant; both wrong releases are in the original trials 0…29). The gate isolates the biased writer in 100/100, never releases a bad file, and also never finishes the file (0/100). Two recovery arms on the same seeds each finish 100/100 with no wrong release: isolation then reassignment, and pinning without isolation. This cell was added after the grid campaigns and then expanded from *n* = 30; do not cite it as a prospectively registered confirmatory analysis, do not cite a success-rate win for the gate alone, and do not treat the recovery arms as a registered contrast. Logs are in [`prototype/design_doc_rerun/`](prototype/design_doc_rerun/), including `design_gaterassign.json` and `design_gatepin.json`. See [ARTIFACT.md](ARTIFACT.md).

**Later design-file plan-slip cell (descriptive, *n* = 10), not prospectively registered.** With the cache left correct, a persistent +1 mm slip is applied to B's submitted wall and outer values. Both lease-only and write gating finish 0/10 (wrong releases 7/10 and 6/10); the gate grants every write (0 denies). This is an injected write-path fault, not a naturally occurring model error, and not a comparison between the arms. Logs: [`design_leaseonly_planslip.json`](prototype/design_doc_rerun/design_leaseonly_planslip.json) and [`design_gate_planslip.json`](prototype/design_doc_rerun/design_gate_planslip.json). See [ARTIFACT.md](ARTIFACT.md).

An earlier eight-agent campaign whose seed contained the arm name is **not** the official result; see [ARTIFACT.md](ARTIFACT.md).

## Reproduce the tables (no API key)

```bash
cd prototype
python summarize_confirmatory.py
```

This reads `unified_seed_rerun/`, checks that the five eight-agent arms share one injection set, and reprints success rates, Wilson 95% intervals, waste, tokens, and the three Fisher tests named in the preregistration. When `combo_gate_orch/` is present, it also reprints the later combination arm and checks that its eight-agent injection flags match the official write-gating arm.

Offline protocol checks, also without a network:

```bash
cd prototype
python run_e1.py
```

## Optional live rerun

A live rerun calls Qwen-plus and DeepSeek-chat and incurs API cost. Copy `api_keys.json` from the example below, or set `QWEN_API_KEY` and `DEEPSEEK_API_KEY`. That file is gitignored; never commit it.

```bash
cd prototype
python run_unified_confirmatory.py --suite inject   # print the injection plan only
python run_unified_confirmatory.py --suite scale8
python run_unified_confirmatory.py --suite t2c4
```

The official eight-agent seed is `unified-{scale}-{fault}-{trial}` (arm name not included). This is a corrective replication of the seed formula in `PRAP_prereg_R1.md`. Topology T2/C4 injection is deterministic: always agent B, +1 *x*. Existing orchestrator, contract-net, group-chat, write-gating, and OCC+undo topology files were not overwritten.

## Layout

```
ARTIFACT.md                         Official counts and how to check them
PRAP_prereg_R1.md                   Frozen 2026-10-31 preregistration (seed later changed; see ARTIFACT.md)
PRAP_protocol_spec.md               Protocol specification
prototype/
  prap_core.py                      Authority ledger, leases, write gate
  testbed.py                        5x5 grid testbed and discrete-event simulator
  run_unified_confirmatory.py       Official (corrective-replication) runner
  run_combo_gate_orch.py            Later combination-arm runner
  design_doc.py                     Parametric design-file testbed (main RQ3 cell)
  run_design_doc.py                 Design-file cell runner
  summarize_confirmatory.py         Offline reprint of the official tables and the later combination
  unified_seed_rerun/               Official trial logs (do not overwrite)
  combo_gate_orch/                  Later combination trial logs (do not overwrite)
  design_doc_rerun/                 Design-file trial logs (official observation-bias files are *n* = 100; do not overwrite)
  *.json                            Earlier campaign files, kept for the record
```

## 中文说明

本仓库是 CSCWD 2027 稿的可复核材料。正式计数、统一种子和回放方法见 [`ARTIFACT.md`](ARTIFACT.md)。密钥文件 `prototype/api_keys.json` 已被忽略，请勿提交。后加的门控加再分配组合臂日志在 `prototype/combo_gate_orch/`，不是前瞻注册的对比；摘要中的 28/30 与 18/30 可在该目录复核。设计文件实验日志在 `prototype/design_doc_rerun/`。该格是主要的 RQ3 证据，在网格实验之后加入，并在 *n* = 30 之后扩到 *n* = 100，不是前瞻注册的验证分析；只租约 81/100 正确发布、2/100 错误发布（不显著，均在原 0…29 局），门控 100/100 隔离且 0/100 完成；再分配与钉住恢复臂均为 100/100 完成且无错误发布，可在 `design_gaterassign.json` 与 `design_gatepin.json` 复核。后加的描述性计划失误实验（*n* = 10，缓存正确，对 B 提交的壁厚和外径注入 +1 mm）中，仅租约与门控均为 0/10 完成（错误发布分别为 7/10 与 6/10），门控全部放行；这是注入的写路径故障，不是自然出现的模型错误，日志为 `design_leaseonly_planslip.json` 与 `design_gate_planslip.json`。
