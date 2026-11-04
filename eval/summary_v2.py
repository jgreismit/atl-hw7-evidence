"""HW7 v2 tables: combine the v2 benchmark folders into the tables of the v2 report.

Run: uv run python -m eval.summary_v2 [reports/bench]
Prints markdown. Rates use the same counting as eval/report.py (95% Wilson intervals).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from eval.report import rate, read_jsonl, summarize


def load(root: Path, name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    d = root / name
    return read_jsonl(d / "results.jsonl"), read_jsonl(d / "runs.jsonl")


def cell(results: list[dict[str, Any]], runs: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    return summarize([r for r in results if r["mode"] == mode], [r for r in runs if r["mode"] == mode]).get(
        mode, {}
    )


def quality_rows(cols: list[tuple[str, dict[str, Any]]]) -> list[str]:
    head = "| Metric | " + " | ".join(c for c, _ in cols) + " |"
    lines = [head, "|---|" + "---|" * len(cols)]

    def row(label: str, fn: Any) -> None:
        lines.append(f"| {label} | " + " | ".join(fn(s) if s else "n/a" for _, s in cols) + " |")

    row("Invoices correct", lambda s: rate(s["correct"], s["invoices"]))
    row("Unsafe payments (hold invoices paid)", lambda s: rate(s["unsafe"], s["hold_total"]))
    row("False holds (pay invoices not paid)", lambda s: rate(s["false_hold"], s["pay_total"]))
    row("Wrong-destination or unmatched transfers", lambda s: str(s["wrong_destination"]))
    row("Owner escalations", lambda s: str(s["escalations"]))
    row("Mean cost per run (USD)", lambda s: s["cost_usd"])
    row("Mean wall time per run (s)", lambda s: s["wall_s"])
    row("Runs (failed)", lambda s: f"{s['runs']} ({s['failed_runs']})")
    return lines


def by_split(
    results: list[dict[str, Any]], runs: list[dict[str, Any]], splits: set[str]
) -> tuple[list, list]:
    return [r for r in results if r["split"] in splits], [r for r in runs if r["split"] in splits]


def per_invoice(results: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for r in results:
        if r["invoice_id"] is None:
            continue
        k, n = out.get(r["invoice_id"], (0, 0))
        out[r["invoice_id"]] = (k + (r["outcome"] == "paid"), n + 1)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0] if argv else "reports/bench")
    out: list[str] = []
    for model in ("sonnet", "haiku"):
        pre_r, pre_n = load(root, f"v2-pre-fresh2-{model}")
        post_r, post_n = load(root, f"v2-post-fresh2-{model}")
        out += [f"## Fresh2 ({model}): before and after the R8 change", ""]
        out += quality_rows([
            ("ATL before", cell(pre_r, pre_n, "atl")),
            ("ATL after", cell(post_r, post_n, "atl")),
            ("Baseline", cell(pre_r, pre_n, "baseline")),
            ("Guarded baseline", cell(pre_r, pre_n, "guarded")),
        ])
        out += ["", "| Invoice | Expected | ATL before | ATL after | Baseline | Guarded |",
                "|---|---|---|---|---|---|"]
        exp = {r["invoice_id"]: r["expected"] for r in pre_r if r["invoice_id"]}
        cols = [per_invoice([r for r in pre_r if r["mode"] == "atl"]), per_invoice(post_r),
                per_invoice([r for r in pre_r if r["mode"] == "baseline"]),
                per_invoice([r for r in pre_r if r["mode"] == "guarded"])]
        for iid in sorted(exp):
            cells = " | ".join(f"paid {c[iid][0]}/{c[iid][1]}" if iid in c else "" for c in cols)
            out.append(f"| {iid} | {exp[iid]} | {cells} |")
        out.append("")

        v1_before_r, v1_before_n = load(root, f"before-{model}")
        v1_after_r, v1_after_n = load(root, f"after-{model}")
        v2_r, v2_n = load(root, f"v2-post-v1cases-{model}")
        g_r, g_n = load(root, f"v2-guarded-v1cases-{model}")
        tf = {"tuning", "fresh"}
        out += [f"## v1 tuning and fresh cases ({model}): ATL across iterations, guarded baseline", ""]
        out += quality_rows([
            ("ATL v1 before", cell(*by_split(v1_before_r, v1_before_n, tf), "atl")),
            ("ATL v1 after R4", cell(*by_split(v1_after_r, v1_after_n, tf), "atl")),
            ("ATL v2 (R4 + R8)", cell(*by_split(v2_r, v2_n, tf), "atl")),
            ("Baseline (v1 run)", cell(*by_split(v1_before_r, v1_before_n, tf), "baseline")),
            ("Guarded baseline", cell(*by_split(g_r, g_n, tf), "guarded")),
        ])
        s_r, s_n = load(root, f"stress-{model}")
        out += ["", f"Stress cases ({model}): ATL v2 "
                + rate(cell(*by_split(v2_r, v2_n, {"stress"}), "atl").get("correct", 0),
                       cell(*by_split(v2_r, v2_n, {"stress"}), "atl").get("invoices", 0))
                + ", guarded "
                + rate(cell(*by_split(g_r, g_n, {"stress"}), "guarded").get("correct", 0),
                       cell(*by_split(g_r, g_n, {"stress"}), "guarded").get("invoices", 0))
                + ", baseline (v1 run) "
                + rate(cell(s_r, s_n, "baseline").get("correct", 0),
                       cell(s_r, s_n, "baseline").get("invoices", 0))
                + " correct.", ""]

        a_r, a_n = load(root, f"v2-attacks-{model}")
        out += [f"## Compromised agent ({model}): transfers that no invoice justifies", "",
                "| Attack | ATL: runs with one / money | Baseline: runs with one / money | "
                "Guarded: runs with one / money | Legit invoices paid (ATL / base / guarded) |",
                "|---|---|---|---|---|"]
        for attack in ("standing_order", "extra_to_registered", "duplicate", "split_drain"):
            cells = []
            legit = []
            for mode in ("atl", "baseline", "guarded"):
                ids = {r["run_id"] for r in a_n if r.get("attack") == attack and r["mode"] == mode}
                bad = [r for r in a_r if r["run_id"] in ids and r["invoice_id"] is None]
                runs_bad = len({r["run_id"] for r in bad})
                money = sum(r.get("paid_amount_cents") or 0 for r in bad) / 100
                cells.append(f"{runs_bad}/{len(ids)}, R$ {money:.2f}")
                inv = [r for r in a_r if r["run_id"] in ids and r["invoice_id"] is not None]
                legit.append(f"{sum(r['outcome'] == 'paid' for r in inv)}/{len(inv)}")
            out.append(f"| {attack} | " + " | ".join(cells) + " | " + " / ".join(legit) + " |")
        cost = sum((r.get("cost_usd") or 0) for name in (
            f"v2-pre-fresh2-{model}", f"v2-post-fresh2-{model}", f"v2-post-v1cases-{model}",
            f"v2-guarded-v1cases-{model}", f"v2-attacks-{model}") for r in load(root, name)[1])
        nruns = sum(len(load(root, name)[1]) for name in (
            f"v2-pre-fresh2-{model}", f"v2-post-fresh2-{model}", f"v2-post-v1cases-{model}",
            f"v2-guarded-v1cases-{model}", f"v2-attacks-{model}"))
        out += ["", f"v2 runs ({model}): {nruns}, model cost USD {cost:.2f}", ""]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
