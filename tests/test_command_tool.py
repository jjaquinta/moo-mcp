"""Unit tests for moo_mcp.tools.command.send_command - tool-layer plumbing
only, faked at the MOOConnection.send_raw boundary (mirrors _CaptureConn in
test_parser.py)."""

import pytest

from moo_mcp.tools import command as t_command


class _CaptureRawConn:
    def __init__(self, response: str = "") -> None:
        self._response = response
        self.seen: tuple | None = None

    async def send_raw(self, commands, *, idle=1.0, max_wait=8.0) -> str:
        self.seen = (commands, idle, max_wait)
        return self._response


@pytest.mark.asyncio
async def test_send_command_shapes_raw_and_lines():
    conn = _CaptureRawConn(response="line1\nline2")
    result = await t_command.send_command(conn, ["look"])
    assert result == {"raw": "line1\nline2", "lines": ["line1", "line2"]}


@pytest.mark.asyncio
async def test_send_command_empty_response_gives_empty_lines():
    conn = _CaptureRawConn(response="")
    result = await t_command.send_command(conn, ["look"])
    assert result == {"raw": "", "lines": []}


@pytest.mark.asyncio
async def test_send_command_passes_commands_and_timing_through_unchanged():
    conn = _CaptureRawConn()
    await t_command.send_command(conn, ["south", "north", "look"], idle=2.5, max_wait=10.0)
    assert conn.seen == (["south", "north", "look"], 2.5, 10.0)
