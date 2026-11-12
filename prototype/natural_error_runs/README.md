# Natural-read experiment logs

Frozen protocol: `prototype/NATURAL_ERROR_PROTOCOL.md` (2026-11-12 16:26 Asia/Shanghai).
Runners: `prototype/natural_doc.py`, `prototype/run_natural_error.py`.

- `stage_a.jsonl`, `stage_a_run.log`: 100 read-only extracts. Primary mismatches 0/100.
- `stage_b_leaseonly.json`, `stage_b_gate.json`, `stage_b_run.log`: later closed-loop follow-on, n=30 each.
- `summary.txt`: printed by the runner, including official-log TPR/FPR.

Do not cite the official-log TPR/FPR in `summary.txt` as detection rates. The checkout note is rebuilt from ledger bore and wall only, so `Instance.outer` is always `bore+2*wall` and the note does not display the live ledger outer after a partial or wrong outer write. See `ARTIFACT.md`.
