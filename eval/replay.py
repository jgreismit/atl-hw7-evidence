"""Policy replay (HW7): ATL's verdict for each scenario invoice, with no agent and no model cost.

Run: uv run python -m eval.replay [--split tuning|fresh|all]

Each episode gets a fresh database and simulated ledger. Every invoice is sent to pay_check exactly as a
faithful agent would copy it; approved decisions are executed and escalations are refused, like the scripted
owner in the harness. This isolates the rules from the agent: it shows what ATL decides when the agent
behaves perfectly, and it is the cheap check used before and after a rule change.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

from eval.harness import (
    build_mandate,
    load_scenarios,
    read_ledger,
    registered_keys,
    run_settings,
    score_episode,
    select_episodes,
)

PAYEE_IDS = {"Concreto Forte": "concreto-forte", "Ferragens Silva": "ferragens-silva"}


def payee_id(supplier: str) -> str:
    return PAYEE_IDS.get(supplier) or supplier.lower().replace(" ", "-")


def replay_episode(episode: dict[str, Any], work: Path) -> list[dict[str, Any]]:
    from atl.runlog import RunLog
    from atl.service import ATLService

    run_id = f"replay_{episode['id']}"
    var_dir = work / run_id
    settings = run_settings(run_id, var_dir, work / "runs")
    service = ATLService.from_settings(settings, runlog=RunLog(run_id, work / "runs"))
    mandate = build_mandate()
    service.register_mandate(service.sign_mandate(mandate))
    verdicts = []
    for inv in episode["invoices"]:
        pid = payee_id(inv["supplier"])
        out = service.pay_check(
            mandate_id=mandate["mandate_id"],
            agent_id=mandate["agent_id"],
            payee_id=pid,
            pix_key=inv["pix_key"],
            amount_cents=inv["amount_cents"],
            category=inv["category"],
            invoice_ref=inv["number"],
            memo=inv["note"],
            idempotency_key=f"{inv['number']}-{pid}",
        )
        verdict = out.get("verdict") or out.get("error")
        if verdict == "approve":
            service.execute_payment(out["decision_id"])
        elif verdict == "escalate":
            service.refuse(out["decision_id"])
        verdicts.append((verdict, out.get("rule_hits") or []))
    rows, rogue = score_episode(episode, read_ledger(var_dir), registered_keys(mandate))
    assert not rogue, rogue
    return [{**r, "verdict": v, "rule_hits": h} for r, (v, h) in zip(rows, verdicts, strict=True)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval.replay")
    p.add_argument("--split", default="all", choices=["all", "tuning", "fresh", "stress"])
    a = p.parse_args(argv)
    episodes = select_episodes(load_scenarios(), a.split)
    errors = 0
    print("| Invoice | Expected | ATL verdict | Rule hits | Result |")
    print("|---|---|---|---|---|")
    with tempfile.TemporaryDirectory(prefix="atl-replay-") as tmp:
        for e in episodes:
            for r in replay_episode(e, Path(tmp)):
                errors += not r["correct"]
                result = "ok" if r["correct"] else r["error"]
                print(f"| {r['invoice_id']} | {r['expected']} | {r['verdict']} | "
                      f"{', '.join(r['rule_hits']) or '-'} | {result} |")
    print(f"\n{errors} invoice(s) where the rules disagree with the owner policy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
