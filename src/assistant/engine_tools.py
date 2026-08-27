"""The per-turn tool surface, defined once for every executor.

Three engines now build a turn: the native loop, LangGraph, and PydanticAI.
Which tools the model is offered -- and in what order -- is a product
decision, not an implementation detail of whichever harness is driving:

  * ``navigate`` leads the array when the client sent a site map. With it
    appended last the 4B stopped calling it at all -- no error, just
    silence. Position is behaviour here, not style.
  * ``propose_edit`` is present only when the caller has an editing surface.
    Deriving that from the article's TEXT instead of the client's own state
    withdrew the tool at exactly the moment a user asked for their first
    paragraph, and cost a day to find.
  * The generated tools follow, core first.

If two engines disagree about any of that, running the same battery against
both measures the tool list rather than the loop, which is the only reason
to have more than one.
"""
from __future__ import annotations

from src.assistant import generated_tools, navigation, studio_tools


def _builtin_tools() -> list[dict]:
    """Imported lazily: tool_runtime imports this module's siblings, and a
    module-level import here would close the cycle."""
    # pylint: disable=import-outside-toplevel
    from src.assistant.tool_runtime import _TOOLS
    return list(_TOOLS)


#: Built-in tools the model is offered. `find_paths` is deliberately absent:
#: it is the least-used tool, it needs two resolved entity ids before it can
#: be called at all, and on a 4B it was being chosen over search_entities for
#: questions search answers — a wrong turn the model cannot recover from
#: because the tool then errors on ids it never had. It stays implemented for
#: saved conversations; it is simply not advertised.
OFFERED_BUILTINS = (
    "mcp__gmr__search_entities",
    "mcp__gmr__investigate_entity",
    # The document surface. read_document sees the report; the four
    # proposal verbs each carry required params only. propose_edit — one
    # required field and six optional flags whose validity depended on it —
    # joins find_paths: implemented for stored conversations, no longer
    # advertised. In the sessions that motivated the split it was never
    # called once.
    "mcp__gmr__read_document",
    "mcp__gmr__set_title",
    "mcp__gmr__set_abstract",
    "mcp__gmr__replace_body",
    "mcp__gmr__insert_widget",
    # The guarded probe: read-only, capped, same proxies as the Run button.
    # For one-off counts and keys checks; the prompt sends anything worth
    # keeping to a Studio project instead.
    "mcp__gmr__query_graph",
    # Arithmetic with a witness: a number computed here is a tool result
    # the grounding check can see the provenance of; the same number
    # computed in the model's head reads as invented.
    "mcp__gmr__calculate",
)

#: Generated tools (from fontem-api's annotated endpoints) the model is
#: offered. Deliberately one.
#:
#: The generated set had grown to eleven narrow endpoints — contract_sectors,
#: dataset_year_coverage, company_cohesion_grants and so on — that between
#: them still could not answer "which contracts involve Israeli companies",
#: while crowding the array a small model has to choose from. A wide surface
#: of near-misses is worse than a narrow one: it costs tokens on every turn
#: and it gives a 4B more ways to pick wrong.
#:
#: `get_doc` earns its place because the documentation index rides in the
#: prefill, so the model already knows what exists and needs only a way to
#: read it. `get_schema` earns its place the hard way: without it the model
#: guessed the graph's edge direction and got zero rows where the data
#: lives. Models whose context clears the schema_context threshold carry
#: the same payload in prefill and rarely need the tool; everyone else
#: pays a turn for it instead of prefix.
OFFERED_GENERATED = ("get_doc", "get_schema")


#: The only tool a signed-out visitor is offered. Everything else either
#: writes to an account (Studio, propose_edit) or spends fontem-api calls on
#: an unauthenticated caller's behalf (search, investigate, get_doc).
#: Navigating is the one thing that needs no account and costs us nothing:
#: the site map came from the client and the answer goes back to the client.
ANONYMOUS_TOOLS = frozenset({navigation.NAVIGATE_TOOL_NAME})


def turn_tool_specs(gen_tools: list[dict], has_editor: bool,
                    nav_routes: list, *, anonymous: bool = False) -> list[dict]:
    """Tool schemas for one turn, in the order the model should see them.

    The Studio tools are unconditional. They run server-side against the
    asking user's own account, so there is nothing to be "open" — and
    requiring an open project was backwards: the agent has a tool to create
    one, and gating on the UI meant it could not use it until the user had
    already done the thing they were asking for.

    The document tools stay gated on the editor, because they genuinely
    need a surface to read from and propose into. That is the difference:
    approval and application are UI concerns, reading and writing the
    user's own projects are not.

    `anonymous` collapses all of that to navigate alone. It is an allowlist
    rather than a set of subtractions on purpose: a tool added later is
    withheld from signed-out callers until someone decides otherwise, which
    is the direction an unauthenticated surface should fail in.
    """
    if anonymous:
        # No site map, no tools: navigate is meaningless without routes to
        # validate against, and offering a tool that cannot succeed only
        # gives a small model something to fail at.
        return [navigation.navigate_tool_schema()] if nav_routes else []

    builtins = [t for t in _builtin_tools()
                if t["function"]["name"] in OFFERED_BUILTINS]
    specs = list(navigation.scope_tools(builtins, has_editor=has_editor))
    specs = specs + list(studio_tools.STUDIO_TOOLS)
    if nav_routes:
        specs = [navigation.navigate_tool_schema()] + specs
    docs = [t for t in gen_tools
            if t["function"]["name"] in OFFERED_GENERATED]
    return specs + docs
