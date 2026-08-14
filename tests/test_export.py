"""Unit tests for moo_mcp.tools.export - Issue #7 regression coverage.

Sample @dump text below matches the confirmed real format from a live
server, not the previously-assumed inline `(perms: [owner] "rwc")` format:
- Property overrides (@chmod/@chown, override-only) precede the `;;...`
  value line for that property.
- Verb overrides (@chmod/@chown, override-only) sit between @args and
  @program - NOT inside the verb body.
"""

import pytest

from moo_mcp.tools.export import _parse_dump_output, export_object

SAMPLE_DUMP = """@Dump #52
@chmod #52."you" r
@chown #52."you" #36
;;#52.("you") = #35
;;#52.("login") = #10
@args #52:"has_property" this none this
@chown #52:has_property #36
@program #52:has_property
{object, prop} = args;
return has_property(object, prop);
.
@args #52:"mcd_2" none none none
@chmod #52:mcd_2 rxd
@program #52:mcd_2
return 1;
.
@args #52:"all_properties all_verbs" this none this
@program #52:all_properties
return {};
.
"""


def test_property_with_both_chmod_and_chown():
    result = _parse_dump_output(SAMPLE_DUMP)
    assert result["properties"]["you"] == {"value": "#35", "owner": "#36", "perms": "r"}


def test_property_with_no_override_stays_null():
    result = _parse_dump_output(SAMPLE_DUMP)
    assert result["properties"]["login"] == {"value": "#10", "owner": None, "perms": None}


def test_object_id_extracted_from_header():
    result = _parse_dump_output(SAMPLE_DUMP)
    assert result["object_id"] == "#52"


def test_verb_with_chown_only_gets_owner_no_body_contamination():
    result = _parse_dump_output(SAMPLE_DUMP)
    verb = next(v for v in result["verbs"] if v["name"] == "has_property")
    assert verb["owner"] == "#36"
    assert verb["perms"] is None
    assert verb["lines"] == [
        "{object, prop} = args;",
        "return has_property(object, prop);",
    ]
    assert not any(line.startswith("@chown") for line in verb["lines"])


def test_verb_with_chmod_only_gets_perms_no_body_contamination():
    result = _parse_dump_output(SAMPLE_DUMP)
    verb = next(v for v in result["verbs"] if v["name"] == "mcd_2")
    assert verb["owner"] is None
    assert verb["perms"] == "rxd"
    assert verb["lines"] == ["return 1;"]
    assert not any(line.startswith("@chmod") for line in verb["lines"])


def test_verb_with_no_override_stays_null_and_clean():
    result = _parse_dump_output(SAMPLE_DUMP)
    verb = next(v for v in result["verbs"] if v["name"] == "all_properties all_verbs")
    assert verb["owner"] is None
    assert verb["perms"] is None
    assert verb["lines"] == ["return {};"]
    assert verb["dobj"] == "this"
    assert verb["prep"] == "none"
    assert verb["iobj"] == "this"


def test_all_verb_names_present():
    result = _parse_dump_output(SAMPLE_DUMP)
    names = [v["name"] for v in result["verbs"]]
    assert names == ["has_property", "mcd_2", "all_properties all_verbs"]


class _CaptureConn:
    def __init__(self, response: str) -> None:
        self._response = response
        self.seen = None

    async def send(self, command: str, *, timeout: float | None = None) -> str:
        self.seen = command
        return self._response


@pytest.mark.asyncio
async def test_export_object_end_to_end_with_overrides():
    conn = _CaptureConn(SAMPLE_DUMP)
    result = await export_object(conn, "#52")
    assert conn.seen == "@dump #52"
    assert result["raw"] == SAMPLE_DUMP
    assert result["properties"]["you"]["owner"] == "#36"
    assert result["verbs"][0]["owner"] == "#36"
    assert not any(line.startswith("@ch") for v in result["verbs"] for line in v["lines"])
