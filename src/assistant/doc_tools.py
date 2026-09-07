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
#:
#: A Studio plot is deliberately NOT in here. Every type in this tuple is
#: addressed by an entity id, and insert_widget requires one; a plot is
#: addressed by (project_id, plot_id) and has no entity at all. Adding it
#: would make `entityId` conditionally required on a flag — the exact shape
#: that got propose_edit retired (see PROPOSAL_TOOL_ACTIONS below). It gets
#: its own verb, `insert_studio_plot`, with required params only.
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
    "mcp__gmr__replace_part": "replace_body",
    "mcp__gmr__insert_widget": "insert_widget",
    "mcp__gmr__insert_studio_plot": "insert_studio_plot",
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
                    "after_text": {
                        "type": "string",
                        "description": (
                            "PREFERRED. A short phrase from the paragraph "
                            "this should follow, quoted exactly; it lands "
                            "immediately after that paragraph. Use this "
                            "whenever you have proposed body text in this "
                            "same turn: your proposal is a card the user has "
                            "not accepted yet, so read_document still "
                            "returns the last SAVED text and there are no "
                            "offsets to measure. The phrase is matched when "
                            "the edit is applied, against the article as it "
                            "then stands. Not found means it appends."
                        ),
                    },
                    "at_char": {
                        "type": "integer",
                        "description": (
                            "A character offset into body_text from "
                            "read_document; the widget lands after the "
                            "paragraph that offset falls in. Only meaningful "
                            "for text ALREADY SAVED — prefer after_text. "
                            "Ignored when after_text is given. Omit both to "
                            "append at the end."
                        ),
                    },
                },
                "required": ["widget_type", "entityId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__insert_studio_plot",
            "description": (
                "Proposes inserting a chart you built in Data Studio into "
                "the report. Give it the project and plot ids from "
                "studio_list_projects / studio_get_project. The chart is "
                "embedded as a live recipe — its queries and transform re-run "
                "when a reader opens the article, so it follows the data "
                "rather than freezing a picture of it. Validated server-side: "
                "the plot must exist, belong to you, and have a chart "
                "configured. Renders as an Apply/Reject card."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Studio project id owning the plot.",
                    },
                    "plot_id": {
                        "type": "string",
                        "description": "Plot id from studio_get_project.",
                    },
                    "after_text": {
                        "type": "string",
                        "description": (
                            "PREFERRED. A short phrase from the paragraph "
                            "this should follow, quoted exactly; it lands "
                            "immediately after that paragraph. Use this "
                            "whenever you have proposed body text in this "
                            "same turn: your proposal is a card the user has "
                            "not accepted yet, so read_document still "
                            "returns the last SAVED text and there are no "
                            "offsets to measure. The phrase is matched when "
                            "the edit is applied, against the article as it "
                            "then stands. Not found means it appends."
                        ),
                    },
                    "at_char": {
                        "type": "integer",
                        "description": (
                            "A character offset into body_text from "
                            "read_document; the chart lands after the "
                            "paragraph that offset falls in. Only meaningful "
                            "for text ALREADY SAVED — prefer after_text. "
                            "Ignored when after_text is given. Omit both to "
                            "append at the end."
                        ),
                    },
                },
                "required": ["project_id", "plot_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__find_in_document",
            "description": (
                "Finds a substring in the article body and returns its "
                "character offsets, for use with replace_part or at_char. "
                "Also returns how many times it occurs: if that is more "
                "than 1, quote a longer, unique phrase before editing, or "
                "you will change the wrong one. Reads only — proposes "
                "nothing and changes nothing."
            ),
            "parameters": {
                "type": "object",
                "properties": {"substring": {
                    "type": "string",
                    "description": (
                        "Text to locate, exactly as it appears in "
                        "body_text from read_document."
                    ),
                }},
                "required": ["substring"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__replace_part",
            "description": (
                "Proposes replacing ONE PASSAGE of the body, addressed by "
                "character offsets into body_text from read_document. Use "
                "this, not replace_body, to change a sentence, fix a "
                "figure or add a paragraph: replace_body costs the whole "
                "article and risks dropping text you were not asked to "
                "touch. Get offsets from find_in_document. Within one "
                "paragraph the replacement is exact; a span crossing "
                "paragraphs replaces every paragraph it touches. Blank "
                "lines in new_text start new paragraphs. Renders as one "
                "Apply/Reject card showing the whole revised body."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer",
                              "description": "First character to replace."},
                    "end": {"type": "integer",
                            "description": (
                                "One past the last character to replace. "
                                "start == end inserts without deleting."
                            )},
                    "new_text": {"type": "string",
                                 "description": (
                                     "Replacement text. Empty deletes the "
                                     "span."
                                 )},
                },
                "required": ["start", "end", "new_text"],
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
