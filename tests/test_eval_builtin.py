"""Unit tests for the Issue #6 fix: routing multi-statement eval payloads
through the eval() builtin instead of a raw `;<code>` line, which this
server's interactive `;` command silently truncates at the first statement.
"""

import pytest

from moo_mcp.tools.eval import _moo_string_literal, _unpack_eval_builtin_response, eval_expression


def test_moo_string_literal_escapes_backslash_and_quote():
    assert _moo_string_literal('a\\b"c') == '"a\\\\b\\"c"'


def test_moo_string_literal_collapses_newlines_to_spaces():
    # This server's string literals don't decode \n as an escape (confirmed
    # against a live server: it comes through as a bare "n" with the
    # backslash swallowed) - and MOO's grammar doesn't need real newlines
    # between statements, so they're collapsed rather than escaped.
    assert _moo_string_literal("a;\nb;\r\nc;") == '"a; b; c;"'


def test_moo_string_literal_collapses_a_run_of_newlines_to_one_space():
    assert _moo_string_literal("a;\n\n\nb;") == '"a; b;"'


def test_unpack_eval_builtin_response_success():
    result = _unpack_eval_builtin_response("=> {1, 10}")
    assert result == {"value": 10, "raw": "=> {1, 10}"}


def test_unpack_eval_builtin_response_success_no_return_defaults_to_zero():
    result = _unpack_eval_builtin_response("=> {1, 0}")
    assert result["value"] == 0
    assert "error" not in result


def test_unpack_eval_builtin_response_compile_error():
    result = _unpack_eval_builtin_response('=> {0, {"Line 1:  syntax error"}}')
    assert result["value"] is None
    assert result["error"] == {
        "message": "Line 1:  syntax error",
        "traceback": ["Line 1:  syntax error"],
    }


def test_unpack_eval_builtin_response_compile_error_multiple_messages():
    result = _unpack_eval_builtin_response('=> {0, {"Line 1:  bad", "Line 2:  also bad"}}')
    assert result["error"]["message"] == "Line 1:  bad"
    assert result["error"]["traceback"] == ["Line 1:  bad", "Line 2:  also bad"]


def test_unpack_eval_builtin_response_unexpected_shape_is_treated_as_error():
    # A runtime error escaping uncaught from eval()-wrapped code doesn't
    # come back as a {success, result} pair - it's a bare traceback. Must
    # not be silently reported as a successful value.
    weird_raw = "#-1:Input to EVAL, line 1: Division by zero\n(End of traceback)"
    result = _unpack_eval_builtin_response(weird_raw)
    assert result["value"] is None
    assert "error" in result
    assert result["raw"] == weird_raw


class _CaptureConn:
    def __init__(self, response: str = "=> 0"):
        self.seen = []
        self._response = response

    async def send(self, command: str, *, timeout: float | None = None) -> str:
        self.seen.append(command)
        return self._response


@pytest.mark.asyncio
async def test_eval_expression_multi_statement_actually_runs_both_statements():
    conn = _CaptureConn(response="=> {1, 10}")
    result = await eval_expression(conn, "a = 5; return a * 2;")
    assert result["value"] == 10
    assert "error" not in result


@pytest.mark.asyncio
async def test_eval_expression_surfaces_compile_error_from_block():
    conn = _CaptureConn(response='=> {0, {"Line 1:  syntax error"}}')
    result = await eval_expression(conn, "if (1 return 5; endif")
    assert result["value"] is None
    assert result["error"]["message"] == "Line 1:  syntax error"


@pytest.mark.asyncio
async def test_eval_expression_single_statement_unaffected():
    # Non-block input keeps using the original fast path, unchanged.
    conn = _CaptureConn(response="=> 4")
    result = await eval_expression(conn, "2 + 2")
    assert conn.seen == [";return 2 + 2"]
    assert result == {"value": 4, "raw": "=> 4"}
