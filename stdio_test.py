"""Synthetic stdio test: launches the server, sends MCP initialize +
tools/list, prints responses, exits. Confirms the protocol layer is wired
correctly without needing Claude Code to restart.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys


async def main() -> int:
    env = os.environ.copy()
    if not all(env.get(k) for k in ("MOO_HOST", "MOO_PORT", "MOO_USER", "MOO_PASS")):
        print("set MOO_HOST/MOO_PORT/MOO_USER/MOO_PASS first", file=sys.stderr)
        return 1
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "moo_mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    async def send(payload: dict) -> None:
        line = json.dumps(payload) + "\n"
        proc.stdin.write(line.encode())
        await proc.stdin.drain()

    async def recv() -> dict:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
        return json.loads(line)

    await send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "stdio_test", "version": "0.0.1"},
        },
    })
    init = await recv()
    print("initialize:", json.dumps(init, indent=2)[:400])

    await send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    await send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = await recv()
    tool_names = [t["name"] for t in tools.get("result", {}).get("tools", [])]
    print(f"\ntools/list ({len(tool_names)}):")
    for n in tool_names:
        print(f"  - {n}")

    await send({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "eval_moo", "arguments": {"expression": "1+1"}},
    })
    call = await recv()
    print("\neval_moo 1+1:", json.dumps(call.get("result"), indent=2)[:400])

    test_obj = os.environ.get("TEST_OBJECT", "$wiz")
    await send({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "parent", "arguments": {"object": test_obj}},
    })
    call2 = await recv()
    print(f"\nparent {test_obj}:", json.dumps(call2.get("result"), indent=2)[:400])

    proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    err_out = (await proc.stderr.read()).decode("utf-8", errors="replace")
    if err_out.strip():
        print("\nstderr:")
        print(err_out[:1000])

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
