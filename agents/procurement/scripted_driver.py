"""SCRIPTED DRIVER, NOT THE AGENT. For pipeline testing only, while Claude auth is unavailable.

Selected by `ATL_AGENT=scripted` (for example `ATL_AGENT=scripted make demo`). The default stays the real
Claude Code agent in run_agent.py. A run that used this driver is never valid evidence of agent behavior:
the runner prints "SCRIPTED DRIVER: NOT A VALID AGENT RUN" and the verifier report says "agent: scripted
driver".

It starts the ATL MCP server over stdio with the same runtime config as the agent (uv run --directory <root>
python -m atl.mcp_server, env without ASAAS_*, ATL_RUN_ID), and for D1, D2, D3 in order follows the policy
mechanically: pay_check with the invoice fields; approve -> execute_payment; escalate -> get_decision with
wait_seconds 30 up to 6 times, then execute_payment if approved; refuse -> nothing.
Transcript: runs/<run_id>-agent.txt, first line is SCRIPTED_HEADER.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio

from agents.procurement.run_agent import (
    INVOICE_IDS,
    REPO_ROOT,
    build_agent_env,
    runtime_mcp_config,
    save_env_dump,
)
from atl.runlog import redact

SCRIPTED_HEADER = "SCRIPTED DRIVER, NOT THE AGENT (Claude auth unavailable)"
MANDATE_ID = "mdt_demo"
AGENT_ID = "procurement-01"
SUPPLIERS = {"Concreto Forte": "concreto-forte", "Ferragens Silva": "ferragens-silva"}
WAIT_SECONDS = 30
MAX_POLLS = 6

_FIELD_RE = {
    "supplier": re.compile(r"^- Supplier:\s*(.+)$", re.M),
    "invoice_ref": re.compile(r"^- Invoice number:\s*(.+)$", re.M),
    "category": re.compile(r"^- Category:\s*(.+)$", re.M),
    "amount": re.compile(r"Amount due:\s*R\$\s*([0-9]+)[.,]([0-9]{2})"),
    "pix_key": re.compile(r"^- Pix key(?:\s*\([A-Z]+\))?:\s*(.+)$", re.M),
    "memo": re.compile(r"^- Payment note:\s*(.+)$", re.M),
}


def parse_invoice(text: str) -> dict[str, Any]:
    """pay_check arguments from an invoice file, copied as written (the key and memo are not judged)."""
    found = {k: r.search(text) for k, r in _FIELD_RE.items()}
    missing = [k for k, m in found.items() if m is None]
    if missing:
        raise ValueError(f"invoice is missing fields: {missing}")
    supplier = found["supplier"].group(1).strip()
    payee_id = SUPPLIERS[supplier]
    invoice_ref = found["invoice_ref"].group(1).strip()
    return {
        "mandate_id": MANDATE_ID,
        "agent_id": AGENT_ID,
        "payee_id": payee_id,
        "pix_key": found["pix_key"].group(1).strip(),
        "amount_cents": int(found["amount"].group(1)) * 100 + int(found["amount"].group(2)),
        "category": found["category"].group(1).strip(),
        "invoice_ref": invoice_ref,
        "memo": found["memo"].group(1).strip(),
        "idempotency_key": f"{invoice_ref}-{payee_id}",
    }


def payload(result: Any) -> dict[str, Any]:
    """Tool result dict from an MCP CallToolResult (structured content or JSON text)."""
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and sc:
        inner = sc.get("result") if set(sc) == {"result"} else sc
        if isinstance(inner, dict):
            return inner
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                return {"error": "NON_JSON_RESULT", "message": text}
            return value if isinstance(value, dict) else {"result": value}
    return {}


async def drive(
    client: Any,
    invoices: list[tuple[str, str]],
    write: Callable[[str], None],
    *,
    wait_seconds: int = WAIT_SECONDS,
    max_polls: int = MAX_POLLS,
) -> list[dict[str, Any]]:
    """Run the fixed policy over the invoices with an MCP client. Returns one summary per invoice."""

    async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        write(f"[tool_call] mcp__atl__{tool} {json.dumps(args, ensure_ascii=False, sort_keys=True)}")
        out = payload(await client.call_tool(tool, args))
        write(f"[tool_result] {json.dumps(out, ensure_ascii=False, sort_keys=True)}")
        return out

    summaries = []
    for name, text in invoices:
        args = parse_invoice(text)
        write(f"[driver] invoice {name} {args['invoice_ref']}")
        res = await call("pay_check", args)
        summary = {
            "invoice": name,
            "invoice_ref": args["invoice_ref"],
            "decision_id": res.get("decision_id"),
            "verdict": res.get("verdict"),
            "status": res.get("status"),
            "transfer_id": None,
            "receipt_id": res.get("receipt_id"),
        }
        next_action = res.get("next_action")
        did = res.get("decision_id")
        if next_action == "wait_for_owner" and did:
            for _ in range(max_polls):
                res = await call("get_decision", {"decision_id": did, "wait_seconds": wait_seconds})
                next_action = res.get("next_action")
                summary["status"] = res.get("status")
                if res.get("status") != "pending_owner":
                    break
        if next_action == "call_execute_payment" and did:
            ex = await call("execute_payment", {"decision_id": did})
            summary.update(
                status=ex.get("status"), transfer_id=ex.get("transfer_id"), receipt_id=ex.get("receipt_id")
            )
        elif res.get("verdict") == "refuse" or next_action == "do_not_pay_report_to_owner":
            reasons = "; ".join(res.get("reasons") or [])
            write(f"[driver] not paid, reasons: {reasons}")
        write(
            f"[driver] {summary['invoice_ref']} | {summary['verdict']} | {summary['status']} | "
            f"transfer {summary['transfer_id'] or 'none'} | receipt {summary['receipt_id']}"
        )
        summaries.append(summary)
    return summaries


def load_invoices(root: Path) -> list[tuple[str, str]]:
    inv = root / "demo" / "invoices"
    from demo.payee_key import render

    return [(i, render((inv / f"{i}.md").read_text(encoding="utf-8"))) for i in INVOICE_IDS]


def stdio_parameters(run_id: str, *, sim: bool, root: Path, base_env: dict[str, str]) -> Any:
    from mcp.client.stdio import StdioServerParameters

    server = runtime_mcp_config(run_id, sim=sim, root=root)["mcpServers"]["atl"]
    env = build_agent_env(base_env, run_id, sim=sim)
    env.update(server.get("env") or {})
    return StdioServerParameters(command=server["command"], args=list(server["args"]), env=env, cwd=str(root))


def run_scripted(
    run_id: str,
    *,
    sim: bool = False,
    root: Path = REPO_ROOT,
    runs_dir: Path | None = None,
    client_factory: Callable[[], Any] | None = None,
    wait_seconds: int = WAIT_SECONDS,
) -> int:
    """Drive D1, D2, D3 through the ATL MCP server. Returns 0 when every invoice was handled."""
    runs_dir = runs_dir or Path(os.environ.get("ATL_RUNS_DIR") or root / "runs")
    runs_dir.mkdir(parents=True, exist_ok=True)
    env = build_agent_env(os.environ, run_id, sim=sim)
    save_env_dump(env, runs_dir, run_id)
    txt_path = runs_dir / f"{run_id}-agent.txt"
    lines = [SCRIPTED_HEADER]

    def write(line: str) -> None:
        lines.append(redact(line))
        txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    write("[driver] mode=scripted; this is not a Claude Code agent run")

    if client_factory is None:
        from mcp.client import Client

        params = stdio_parameters(run_id, sim=sim, root=root, base_env=dict(os.environ))

        def client_factory() -> Any:
            return Client(params)

    async def main() -> list[dict[str, Any]]:
        async with client_factory() as client:
            tools = sorted(t.name for t in (await client.list_tools()).tools)
            write(f"[init] tools={', '.join('mcp__atl__' + t for t in tools)}")
            return await drive(client, load_invoices(root), write, wait_seconds=wait_seconds)

    try:
        summaries = anyio.run(main)
    except Exception as exc:  # noqa: BLE001 - reported in the transcript and the exit code
        write(f"[driver] error: {type(exc).__name__}: {exc}")
        return 1
    write(f"[final] {SCRIPTED_HEADER}: handled {len(summaries)} invoices")
    return 0 if len(summaries) == len(INVOICE_IDS) else 1
