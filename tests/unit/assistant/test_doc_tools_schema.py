"""The DOC_TOOLS schema is a contract with the model, so pin its shape.

The existing doc-tool tests drive behaviour through the runtime; none of
them assert the tool definitions the model actually receives. Mutation
testing made that concrete: rename a parameter, drop a `required` entry,
or change the widget enum and 96 of 123 mutants survived — the assistant
would start calling tools with parameters that no longer exist, and no
test would notice.

Descriptions are deliberately NOT pinned: they are prompt material and
should stay free to tune. What is pinned is everything a caller binds
to — names, parameter names, types, required-ness, and the enum.
"""
import pytest

from src.assistant.doc_tools import (
    DOC_TOOLS,
    PROPOSAL_TOOL_ACTIONS,
    WIDGET_TYPES,
)

# name -> (required params, all params, {param: type})
EXPECTED_TOOLS = {
    "mcp__gmr__read_document": ([], [], {}),
    "mcp__gmr__set_title": (["title"], ["title"], {"title": "string"}),
    "mcp__gmr__set_abstract": (["abstract"], ["abstract"], {"abstract": "string"}),
    "mcp__gmr__replace_body": (["content"], ["content"], {"content": "string"}),
    "mcp__gmr__insert_widget": (
        ["widget_type", "entityId"],
        ["widget_type", "entityId", "depth"],
        {"widget_type": "string", "entityId": "string", "depth": "integer"},
    ),
    # Ids only, both required. A Studio plot has no entity to hang off, so
    # it is its own verb rather than a widget_type with a conditionally
    # required entityId — the shape that got propose_edit retired.
    "mcp__gmr__insert_studio_plot": (
        ["project_id", "plot_id"],
        ["project_id", "plot_id"],
        {"project_id": "string", "plot_id": "string"},
    ),
}


def _by_name():
    return {t["function"]["name"]: t for t in DOC_TOOLS}


def test_exactly_these_tools_are_advertised():
    assert set(_by_name()) == set(EXPECTED_TOOLS)
    assert len(DOC_TOOLS) == len(EXPECTED_TOOLS), "a tool is defined twice"


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_tool_shape(name):
    tool = _by_name()[name]
    assert tool["type"] == "function"
    fn = tool["function"]
    params = fn.get("parameters", {})
    required, props, types = EXPECTED_TOOLS[name]

    assert params.get("type") == "object"
    assert "properties" in params, f"{name} declares no properties key"
    assert sorted(params.get("required", [])) == sorted(required)
    assert sorted(params["properties"]) == sorted(props)
    for pname, ptype in types.items():
        assert params["properties"][pname]["type"] == ptype

    # Prompt material must exist, but its wording stays free to tune.
    assert fn.get("description", "").strip(), f"{name} has no description"


def test_every_required_param_is_also_declared():
    for tool in DOC_TOOLS:
        params = tool["function"].get("parameters", {})
        declared = set(params.get("properties", {}))
        for req in params.get("required", []):
            assert req in declared, (
                f"{tool['function']['name']} requires {req!r} but never declares it"
            )


def test_widget_type_enum_matches_the_supported_widgets():
    widget = _by_name()["mcp__gmr__insert_widget"]
    enum = widget["function"]["parameters"]["properties"]["widget_type"]["enum"]
    assert list(enum) == list(WIDGET_TYPES)
    assert WIDGET_TYPES == ("graph_explorer", "contracts_table", "entity_profile")


def test_proposal_actions_map_advertised_tools_to_frontend_actions():
    # Every proposal tool must be a real advertised tool, and the mapping
    # is what the panel matches a tool_result back to its card with.
    assert PROPOSAL_TOOL_ACTIONS == {
        "mcp__gmr__set_title": "set_title",
        "mcp__gmr__set_abstract": "set_abstract",
        "mcp__gmr__replace_body": "replace_body",
        "mcp__gmr__insert_widget": "insert_widget",
        "mcp__gmr__insert_studio_plot": "insert_studio_plot",
    }
    for tool_name in PROPOSAL_TOOL_ACTIONS:
        assert tool_name in _by_name(), f"{tool_name} is mapped but not advertised"


def test_read_document_takes_no_parameters():
    # It reads the bound document; taking arguments would let the model
    # aim it somewhere else.
    params = _by_name()["mcp__gmr__read_document"]["function"]["parameters"]
    # `in` before value: .get(..., {}) would let a renamed key pass.
    assert "properties" in params and params["properties"] == {}
    assert params.get("required", []) == []


def test_tool_names_are_mcp_namespaced():
    for name in _by_name():
        assert name.startswith("mcp__gmr__"), f"{name} is not MCP-namespaced"
