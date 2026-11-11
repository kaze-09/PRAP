# PRAP: Preemption and Resource Arbitration Protocol

A protocol and measurement testbed for exclusive writes among multiple LLM agents. An authority ledger, TTL leases, and a write gate mediate commits; the gate compares the cached observation already used for planning with the ledger and can refuse a write before it executes.

This repository is the artifact for the CSCWD 2027 draft in [`cscwd2027/`](cscwd2027/). The official confirmatory counts, seed formula, and replay instructions are in [`ARTIFACT.md`](ARTIFACT.md).

**Official eight-agent result (shared injection set, *n* = 30 per arm).** Success rates do not separate (lease-only 22/30, write gating 21/30, orchestrator 19/30, no protocol 19/30, prompt 16/30; all three preregistered Fisher tests *p* ≥ 0.58). What separates is execution waste: write gating 0.00 (SD 0.00) against lease-only 12.50 (7.13) and orchestrator 3.50 (3.35). Write-gating planning tokens are higher (17043 against the orchestrator's 9408). Do not cite a success-rate win on this cell.

**Topology cell.** The preregistered contrast remains orchestrator 27/30 against write gating 16/30 (*p* = 0.003). Later baselines on the same injection reach lease-only 29/30 and no protocol 28/30, nearly even with the orchestrator. That gap is mainly the cost of write gating cancelling rescue, not a unique benefit of the orchestrator relative to running without a lock.

An earlier eight-agent campaign whose seed contained the arm name is **not** the confirmatory result; see [ARTIFACT.md](ARTIFACT.md).

## Reproduce the tables (no API key)

```bash
cd prototype
python summarize_confirmatory.py
```

This reads `unified_seed_rerun/`, checks that the five eight-agent arms share one injection set, and reprints success rates, Wilson 95% intervals, waste, tokens, and the preregistered Fisher tests.

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

The confirmatory eight-agent seed is `unified-{scale}-{fault}-{trial}` (arm name not included). Topology T2/C4 injection is deterministic: always agent B, +1 *x*. Existing orchestrator, contract-net, group-chat, write-gating, and OCC+undo topology files were not overwritten.

## Layout

```
ARTIFACT.md                         Official counts and how to check them
PRAP_prereg_R1.md                   Frozen 2026-10-31 preregistration (seed later changed; see ARTIFACT.md)
PRAP_protocol_spec.md               Protocol specification
prototype/
  prap_core.py                      Authority ledger, leases, write gate
  testbed.py                        5x5 grid testbed and discrete-event simulator
  run_unified_confirmatory.py       Confirmatory runner
  summarize_confirmatory.py         Offline reprint of the official tables
  unified_seed_rerun/               Official trial logs (do not overwrite)
  *.json                            Earlier campaign files, kept for the record
```

## 中文说明

本仓库是 CSCWD 2027 稿的可复核材料。正式计数、统一种子和回放方法见 [`ARTIFACT.md`](ARTIFACT.md)。密钥文件 `prototype/api_keys.json` 已被忽略，请勿提交。
