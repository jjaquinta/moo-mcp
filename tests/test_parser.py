"""Unit tests for moo_mcp.parser - covers literal types, nested structures,
strings with embedded punctuation, and traceback parsing."""

import pytest

from moo_mcp.parser import parse_response
from moo_mcp.tools.eval import eval_expression


def test_int_zero():
    val, err = parse_response("=> 0")
    assert val == 0
    assert err is None


def test_int_positive():
    val, err = parse_response("=> 51")
    assert val == 51
    assert err is None


def test_int_negative():
    val, err = parse_response("=> -1")
    assert val == -1


def test_float():
    val, _ = parse_response("=> 3.14")
    assert val == 3.14


def test_string_simple():
    val, _ = parse_response('=> "hello"')
    assert val == "hello"


def test_string_with_escaped_quote():
    val, _ = parse_response('=> "he said \\"hi\\""')
    assert val == 'he said "hi"'


def test_object_ref():
    val, _ = parse_response("=> #42")
    assert val == "#42"


def test_object_ref_with_name():
    val, _ = parse_response("=> #42  (Generic Room)")
    assert val == "#42"


def test_corified():
    val, _ = parse_response("=> $string_utils")
    assert val == "$string_utils"


def test_empty_list():
    val, _ = parse_response("=> {}")
    assert val == []


def test_flat_list_of_ints():
    val, _ = parse_response("=> {1, 2, 3}")
    assert val == [1, 2, 3]


def test_list_of_strings():
    val, _ = parse_response('=> {"a", "b", "c"}')
    assert val == ["a", "b", "c"]


def test_list_of_object_refs():
    val, _ = parse_response("=> {#42}")
    assert val == ["#42"]


def test_nested_list():
    val, _ = parse_response("=> {1, {2, 3}, 4}")
    assert val == [1, [2, 3], 4]


def test_list_of_lists_with_strings_containing_commas():
    """Nested lists of strings, where the strings themselves contain commas,
    spaces, and punctuation that could confuse a naive splitter."""
    raw = '=> {{"first item", "with, comma"}, {"second", "punct: yes!"}}'
    val, err = parse_response(raw)
    assert err is None
    assert len(val) == 2
    assert val[0] == ["first item", "with, comma"]
    assert val[1][1] == "punct: yes!"


def test_real_properties_list():
    raw = '=> {"name", "description", "location", "contents", "owner"}'
    val, _ = parse_response(raw)
    assert val == ["name", "description", "location", "contents", "owner"]


def test_error_property_not_found():
    raw = (
        "eval input(3): Property not found\n"
        "Via BF eval()\n"
        "Via $prog:eval_cmd_string(19) [T=#2]\n"
        "Via $prog:eval(13) [T=#2]\n"
        "(EOT)"
    )
    val, err = parse_response(raw)
    assert val is None
    assert err is not None
    assert err["message"] == "Property not found"
    assert len(err["traceback"]) == 3
    assert "BF eval()" in err["traceback"][0]


def test_error_constant_e_propnf():
    val, _ = parse_response("=> E_PROPNF")
    assert val == "E_PROPNF"


class _CaptureConn:
    def __init__(self, response: str = "=> 0"):
        self.seen = []
        self._response = response

    async def send(self, command: str, *, timeout: float | None = None) -> str:
        self.seen.append(command)
        return self._response


@pytest.mark.asyncio
async def test_eval_expression_single_value_uses_return_wrapper():
    conn = _CaptureConn()
    await eval_expression(conn, "2 + 2")
    assert conn.seen == [";return 2 + 2"]


@pytest.mark.asyncio
async def test_eval_expression_block_code_routes_through_eval_builtin():
    # Regression test for Issue #6: this server's interactive `;` command
    # silently truncates multi-statement input at the first statement, so
    # block-shaped payloads must go through the eval() builtin instead of
    # being sent as a raw `;<code>` line.
    conn = _CaptureConn(response='=> {1, 3}')
    block = "x = 1;\nwhile (x < 3) {\n    x = x + 1;\n}\nx;"
    from moo_mcp.tools.eval import _moo_string_literal

    result = await eval_expression(conn, block)
    assert conn.seen == [f";return eval({_moo_string_literal(block)})"]
    assert result == {"value": 3, "raw": '=> {1, 3}'}


@pytest.mark.asyncio
async def test_eval_expression_explicit_leading_semicolon_block_also_fixed():
    # The original bug report's raw-protocol repro included a leading `;`
    # (`; trek_player = create(#-1); ...`) - that must be fixed the same way
    # as the unprefixed form used in the reopening retest.
    conn = _CaptureConn(response='=> {1, 10}')
    from moo_mcp.tools.eval import _moo_string_literal

    result = await eval_expression(conn, "; a = 5; return a * 2;")
    assert conn.seen == [f';return eval({_moo_string_literal("a = 5; return a * 2;")})']
    assert result["value"] == 10
