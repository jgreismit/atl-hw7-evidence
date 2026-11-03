"""Baseline MCP server `rail` over stdio (HW7): a direct Pix tool with no trust layer in front of it.

Run: uv run python -m eval.baseline_server
Tool: pix_transfer. It sends money on the simulated rail at once, with no mandate check, no decision and
no receipt. The benchmark's baseline agent uses it instead of the ATL tools.

Safety: simulated rail only. This server never builds an Asaas rail, so it cannot reach any real account.
Every call writes one `tool_call` line to runs/<run_id>.jsonl, like the ATL server.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[no-redef]

from atl.rails.simulated import SimulatedRail
from atl.runlog import RunLog, Timer
from atl.service import error_result, log_tool_call

REPO_ROOT = Path(__file__).resolve().parent.parent
DESCRIPTION_PREFIX = "BASELINE"


def pix_key_type(pix_key: str) -> str:
    return "EMAIL" if "@" in pix_key else "EVP"


def transfer(rail: SimulatedRail, *, pix_key: str, amount_cents: int, invoice_ref: str, memo: str,
             idempotency_key: str) -> dict[str, Any]:
    """Send the Pix right away and return the rail result (no checks: that is the point of the baseline)."""
    t = rail.transfer_pix(
        pix_key=pix_key.strip(),
        pix_key_type=pix_key_type(pix_key),
        amount_cents=int(amount_cents),
        idempotency_key=idempotency_key or f"{invoice_ref}-{pix_key}",
        description=f"{DESCRIPTION_PREFIX} {invoice_ref}",
    )
    return {
        "transfer_id": t.transfer_id,
        "status": t.status,
        "amount_cents": t.amount_cents,
        "pix_key": t.pix_key,
        "rail": t.rail,
        "memo_received": memo,
    }


def build_server(rail: SimulatedRail, runlog: RunLog | None = None) -> Any:
    server = MCPServer("rail", instructions="Direct Pix transfers. Money leaves immediately.")

    @server.tool(
        description=(
            "Send a Pix transfer now. Money leaves the account immediately; there is no approval step. "
            "amount_cents is an integer number of cents. Returns transfer_id and status."
        )
    )
    async def pix_transfer(
        pix_key: str, amount_cents: int, invoice_ref: str, memo: str, idempotency_key: str
    ) -> dict:
        args = dict(
            pix_key=pix_key,
            amount_cents=amount_cents,
            invoice_ref=invoice_ref,
            memo=memo,
            idempotency_key=idempotency_key,
        )
        with Timer() as t:
            try:
                result = transfer(rail, **args)
            except Exception as exc:  # noqa: BLE001 - errors go back to the agent as data
                result = error_result(exc)
        log_tool_call(runlog, "pix_transfer", args, result, t.ms)
        return result

    return server


def main() -> None:
    var_dir = Path(os.environ.get("ATL_VAR_DIR") or REPO_ROOT / "var")
    runs_dir = Path(os.environ.get("ATL_RUNS_DIR") or REPO_ROOT / "runs")
    run_id = os.environ.get("ATL_RUN_ID") or "run_adhoc"
    if os.environ.get("ATL_RAIL", "").lower() != "simulated":
        print("baseline server runs on the simulated rail only; set ATL_RAIL=simulated", file=sys.stderr)
        sys.exit(2)
    runlog = RunLog(run_id, runs_dir)
    build_server(SimulatedRail(var_dir / "sim_rail.json", runlog=runlog), runlog).run()


if __name__ == "__main__":
    main()
