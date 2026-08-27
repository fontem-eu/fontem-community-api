"""The document tool surface: read the report, propose with required params.

Split out of tool_runtime for the same reason studio_tools was: the schemas
are prompt material with tuned wording, and the runtime is dispatch logic.
propose_edit's replacement lives here — four verbs with required params
only, where the old tool had one required field and six optional flags
whose validity depended on it.
"""
from __future__ import annotations

#: Widget types the editor can render. Shared by the legacy propose_edit
#: enum and the first-class insert_widget tool below.
WIDGET_TYPES = ("graph_explorer", "contracts_table", "entity_profile")

#: The split proposal tools that replaced the four-action propose_edit, and
#: the frontend action each one maps to. propose_edit offered one required
#: field (`action`) and six optional flags whose validity depended on it —
#: callable in shapes that meant nothing, and never called at all in the
#: sessions that motivated this. Each of these has required params only.
PROPOSAL_TOOL_ACTIONS = {
    "mcp__gmr__set_title": "set_title",
    "mcp__gmr__set_abstract": "set_abstract",
    "mcp__gmr__replace_body": "replace_body",
    "mcp__gmr__insert_widget": "insert_widget",
}

DOC_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__read_document",
            "description": (
                "Reads the report this conversation is about: title, "
                "abstract and body (TipTap document JSON; the prose is in "
                "the `text` fields). Returns the last SAVED version — the "
                "user's editor buffer may be newer. Call this BEFORE "
                "proposing any edit; you cannot revise what you have not "
                "read."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__set_title",
            "description": (
                "Proposes a new title for the report. Renders as an "
                "Apply/Reject card; nothing changes until the user applies."
            ),
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__set_abstract",
            "description": (
                "Proposes a new abstract for the report. Renders as an "
                "Apply/Reject card; nothing changes until the user applies."
            ),
            "parameters": {
                "type": "object",
                "properties": {"abstract": {"type": "string"}},
                "required": ["abstract"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__replace_body",
            "description": (
                "Proposes a replacement for the WHOLE article body, as "
                "HTML. One card, one review: read the document first, "
                "produce the complete revised text, and propose it in a "
                "single call — not paragraph by paragraph."
            ),
            "parameters": {
                "type": "object",
                "properties": {"content": {
                    "type": "string",
                    "description": "The complete new body, HTML.",
                }},
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__insert_widget",
            "description": (
                "Proposes inserting an interactive widget into the report. "
                "Validated server-side before it is proposed: the widget "
                "type must exist and the entity must resolve, so a card "
                "that reaches the user is one that will render."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "widget_type": {"type": "string",
                                    "enum": list(WIDGET_TYPES)},
                    "entityId": {
                        "type": "string",
                        "description": "GMR UUID from search_entities.",
                    },
                    "depth": {"type": "integer",
                              "description": "Graph depth, 1-3."},
                },
                "required": ["widget_type", "entityId"],
            },
        },
    },
]


#: The three field proposals and the one required field each validates.
FIELD_PROPOSALS = {
    "mcp__gmr__set_title": "title",
    "mcp__gmr__set_abstract": "abstract",
    "mcp__gmr__replace_body": "content",
}
