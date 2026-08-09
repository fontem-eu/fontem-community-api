"""Generated schemas must match the route, and the per-turn surface must stay small."""
from src.assistant.generated_tools import (
    MAX_TOOLS_PER_TURN, select, tools_from_spec,
)

SPEC = {"paths": {
    "/atlas/series": {"get": {
        "x-agent-tool": {"name": "get_series", "when": "the user wants values",
                         "group": "statistics",
                         "params": ["dataset", "geo", "start"]},
        "parameters": [
            {"name": "dataset", "required": True, "description": "Dataset code",
             "schema": {"type": "string"}},
            {"name": "geo", "required": False,
             "schema": {"type": "array", "items": {"type": "string"}}},
            {"name": "start", "required": False,
             "schema": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
            {"name": "row_limit", "required": False,
             "schema": {"type": "integer"}},
        ]}},
    "/contracts/sectors": {"get": {
        "x-agent-tool": {"name": "contract_sectors", "when": "sectors are asked about",
                         "group": "contracts"},
        "parameters": [{"name": "country", "required": False,
                        "schema": {"type": "string"}}]}},
    "/internal/debug": {"get": {"parameters": []}},
}}


def _by_name(tools, name):
    return next(t for t in tools if t["function"]["name"] == name)


def test_only_annotated_endpoints_become_tools():
    """84 endpoints exist; exposure is opt-in, never automatic."""
    tools = tools_from_spec(SPEC)
    assert {t["function"]["name"] for t in tools} == {"get_series", "contract_sectors"}


def test_declared_params_are_a_whitelist():
    """row_limit is a knob the model has no basis to set, so it is not offered."""
    props = _by_name(tools_from_spec(SPEC), "get_series")["function"]["parameters"]
    assert set(props["properties"]) == {"dataset", "geo", "start"}
    assert props["required"] == ["dataset"]


def test_optional_int_unwraps_from_anyof():
    """`int | None` renders as anyOf; a raw copy would emit an invalid schema."""
    props = _by_name(tools_from_spec(SPEC), "get_series")["function"]["parameters"]
    assert props["properties"]["start"]["type"] == "integer"
    assert props["properties"]["geo"]["type"] == "array"


def test_description_tells_the_model_when_not_what():
    tool = _by_name(tools_from_spec(SPEC), "get_series")
    assert tool["function"]["description"].startswith("Use when ")


def test_route_metadata_is_not_shown_to_the_model():
    """The model gets a name and a description; paths are the caller's business."""
    tool = _by_name(tools_from_spec(SPEC), "get_series")
    assert "_route" in tool and "path" not in tool["function"]


def test_selection_prefers_the_relevant_group():
    tools = tools_from_spec(SPEC)
    picked = select(tools, groups={"statistics"}, limit=1)
    assert picked[0]["function"]["name"] == "get_series"


def test_per_turn_surface_is_hard_capped():
    """A large registry is fine; a large per-turn surface is not."""
    many = tools_from_spec({"paths": {
        f"/x{i}": {"get": {"x-agent-tool": {"name": f"t{i}", "when": "w",
                                            "group": "g"}, "parameters": []}}
        for i in range(40)}})
    assert len(many) == 40
    assert len(select(many)) == MAX_TOOLS_PER_TURN


# --- core tools -------------------------------------------------------------

CORE_SPEC = {"paths": {
    "/atlas/datasets": {"get": {
        "x-agent-tool": {"name": "list_datasets", "when": "codes are needed",
                         "group": "statistics", "core": True},
        "parameters": []}},
    **{f"/c{i}": {"get": {
        "x-agent-tool": {"name": f"contracts_{i}", "when": "contracts",
                         "group": "contracts"},
        "parameters": []}} for i in range(20)},
}}


def test_core_survives_a_mismatched_group_scope():
    """Scoping to contracts must not hide the way to find a dataset code."""
    picked = select(tools_from_spec(CORE_SPEC), groups={"contracts"})
    assert "list_datasets" in {t["function"]["name"] for t in picked}


def test_core_is_never_the_tool_the_cap_drops():
    picked = select(tools_from_spec(CORE_SPEC), groups={"contracts"}, limit=3)
    names = {t["function"]["name"] for t in picked}
    assert "list_datasets" in names
    assert len(picked) == 3


def test_cap_stretches_rather_than_dropping_core():
    """More core tools than the cap: the cap yields, core does not."""
    many_core = {"paths": {
        f"/x{i}": {"get": {"x-agent-tool": {"name": f"core_{i}", "when": "w",
                                            "group": "g", "core": True},
                           "parameters": []}} for i in range(5)}}
    picked = select(tools_from_spec(many_core), limit=2)
    assert len(picked) == 5
