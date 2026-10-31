"""E6 进度/汇总监控（零 API）。用法：python comp_status.py"""
import glob
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

rows = []
for f in sorted(glob.glob("comp_*_T*.json")):
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception as e:
        print(f"{f}: 解析失败（可能正在写入）{e}")
        continue
    if not d:
        continue
    n = len(d)
    sr = sum(r["success"] for r in d)
    waste = sum(v["failed"] for r in d for v in r["agent_ops"].values()) / n
    toks = sum(r["tokens"] for r in d) / n
    calls = sum(r["llm_calls"] for r in d) / n
    rows.append((f, n, sr, waste, toks, calls))

print(f"{'文件':<28}{'局':>3}{'成功':>4}{'浪费算子':>8}{'tokens':>8}{'LLM调用':>7}")
for f, n, sr, waste, toks, calls in rows:
    print(f"{f:<28}{n:>3}{sr:>4}{waste:>8.1f}{toks:>8.0f}{calls:>7.1f}")
total = sum(n for _, n, *_ in rows)
print(f"\n总进度: {total}/120 局")
