"""Benchmark skeleton (HW7 stretch): decisions by verdict and rule id across run logs.

Run: uv run python -m eval.bench [run_id ...]   (default: every runs/run_*.jsonl)
Reuses the verifier's run log parser so the counts match reports/verification-*.md.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from verify.verify_run import REPO_ROOT, count_decisions, load_run_log


def bench(run_ids: list[str], runs_dir: Path) -> dict:
    verdicts: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    per_run = {}
    for run_id in run_ids:
        counts = count_decisions(load_run_log(runs_dir / f"{run_id}.jsonl"), {})
        per_run[run_id] = counts
        verdicts.update(counts["by_verdict"])
        rules.update(counts["by_rule"])
    return {"runs": per_run, "by_verdict": dict(verdicts), "by_rule": dict(rules)}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    runs_dir = REPO_ROOT / "runs"
    run_ids = args or sorted(p.stem for p in runs_dir.glob("run_*.jsonl"))
    result = bench(run_ids, runs_dir)
    for run_id, counts in result["runs"].items():
        print(f"{run_id}: {counts['total']} decisions {counts['by_verdict']}")
    print(f"total by verdict: {result['by_verdict']}")
    print(f"total by rule: {result['by_rule']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
