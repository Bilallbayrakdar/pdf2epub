#!/usr/bin/env python3
"""
Anchor Map MCP Server

Maps spec component IDs to pseudocode and file locations.
Each record links a spec component (SPEC.md section) to its code segment
(@start/@end block) and a plain-English pseudocode description.

Storage: <server_dir>/anchors.jsonl — one JSON object per line, sorted by id.

Usage:
    stdio mode  (Claude Code / MCP host manages lifecycle):
        python server.py

    HTTP/SSE mode  (standalone, agents start manually):
        python server.py --port 8765 [--host 127.0.0.1]
"""

import argparse
import json
import os
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

_env_dir = os.environ.get("ANCHOR_MCP_DIR")
ANCHORS_DIR = Path(_env_dir) if _env_dir else Path(__file__).parent
ANCHORS_FILE = ANCHORS_DIR / "anchors.jsonl"

mcp = FastMCP("anchor-map")

_anchors: dict[str, dict] | None = None
_lock = threading.Lock()


def _load() -> dict[str, dict]:
    global _anchors
    if _anchors is not None:
        return _anchors
    data: dict[str, dict] = {}
    if ANCHORS_FILE.exists():
        with open(ANCHORS_FILE, encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                    data[record["id"]] = record
                except (json.JSONDecodeError, KeyError) as exc:
                    print(
                        f"[anchor-map] Warning: skipped line {lineno}: {exc}",
                        flush=True,
                    )
    _anchors = data
    return data


def _save() -> None:
    if _anchors is None:
        return
    ANCHORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ANCHORS_FILE, "w", encoding="utf-8") as fh:
        for record in sorted(_anchors.values(), key=lambda r: r["id"]):
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


@mcp.tool()
def anchor_get(id: str) -> dict | None:
    """Return one anchor record by component ID, or null if not found."""
    with _lock:
        return _load().get(id)


@mcp.tool()
def anchor_set(id: str, pseudocode: str, code_file: str, spec_file: str | None = None) -> bool:
    """Create or update an anchor record. Returns True on success.
    spec_file is optional — omit for inline anchors that have no corresponding spec entry."""
    with _lock:
        anchors = _load()
        anchors[id] = {
            "id": id,
            "pseudocode": pseudocode,
            "spec_file": spec_file,
            "code_file": code_file,
        }
        _save()
    return True


@mcp.tool()
def anchor_delete(id: str) -> bool:
    """Delete an anchor record. Raises ValueError if not found."""
    with _lock:
        anchors = _load()
        if id not in anchors:
            raise ValueError(f"Anchor '{id}' not found.")
        del anchors[id]
        _save()
    return True


@mcp.tool()
def anchor_exists(id: str) -> bool:
    """Return True if an anchor with the given ID exists, False otherwise."""
    with _lock:
        return id in _load()


@mcp.tool()
def anchor_list() -> list[str]:
    """Return all anchor IDs in sorted order."""
    with _lock:
        return sorted(_load().keys())


@mcp.tool()
def anchor_search(query: str) -> list[dict]:
    """Search anchor records by keyword across id, pseudocode, spec_file, and code_file."""
    with _lock:
        anchors = _load()
        q = query.lower()
        return [
            r for r in anchors.values()
            if q in " ".join([
                r.get("id", ""),
                r.get("pseudocode", ""),
                r.get("spec_file", ""),
                r.get("code_file", ""),
            ]).lower()
        ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Anchor Map MCP Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  stdio mode (Claude Code):      python server.py
  HTTP/SSE mode (standalone):    python server.py --port 8765
  HTTP on all interfaces:        python server.py --port 8765 --host 0.0.0.0
""",
    )
    parser.add_argument(
        "--port", type=int, default=None, metavar="PORT",
        help="Run as HTTP/SSE server on this port. Omit for stdio mode.",
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1", metavar="HOST",
        help="Bind host for HTTP mode (default: 127.0.0.1).",
    )
    args = parser.parse_args()

    if args.port:
        os.environ.setdefault("MCP_HOST", args.host)
        os.environ.setdefault("MCP_PORT", str(args.port))
        print(
            f"[anchor-map] Starting HTTP/SSE server on {args.host}:{args.port}",
            flush=True,
        )
        mcp.run(transport="sse")
    else:
        mcp.run()
