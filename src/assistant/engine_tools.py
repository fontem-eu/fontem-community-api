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

from src.assistant import generated_tools, navigation


def _builtin_tools() -> list[dict]:
    """Imported lazily: mistral_client imports this module's siblings, and a
    module-level import here would close the cycle."""
    # pylint: disable=import-outside-toplevel
    from src.assistant.mistral_client import _TOOLS
    return list(_TOOLS)


def turn_tool_specs(gen_tools: list[dict], has_editor: bool,
                    nav_routes: list) -> list[dict]:
    """Tool schemas for one turn, in the order the model should see them."""
    specs = list(navigation.scope_tools(_builtin_tools(), has_editor=has_editor))
    if nav_routes:
        specs = [navigation.navigate_tool_schema()] + specs
    return specs + list(generated_tools.select(gen_tools))
