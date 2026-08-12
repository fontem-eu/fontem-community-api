"""Data Studio actions the model can propose, executed by the browser.

The Studio is where a user builds an analysis: a project holds source
queries (Cypher against the graph, SQL against the stats warehouse, SPARQL
against the RDF store) and plots that combine those sources through DuckDB
in the browser and chart the result. Until now the assistant could describe
all of that and touch none of it.

These are proposals, not writes. The tool executor talks to fontem-api over
plain GET with no user identity — by design, since it is a read-only
surface — so a tool that created a project server-side would either need the
user's credentials or act as nobody. Instead the model emits an intent, the
panel performs it with the session already in the browser, and the user sees
what is about to happen. `propose_edit` and `navigate` work the same way and
for the same reason.

The schemas carry more description than usual on purpose. A model that has
never seen the Studio cannot guess that `lang` is one of three fixed values,
that a plot's `sources` name queries by id, or that `transform` is DuckDB
SQL rather than the query language of whatever produced the rows. Every one
of those is a 400 the model cannot diagnose from the error alone.
"""
from __future__ import annotations

#: Query engines a source can use. Each runs through a read-only,
#: row-and-timeout-capped proxy; see the Studio documentation for schemas.
QUERY_LANGS = ("cypher", "sql", "sparql")

#: Chart types the plot renderer understands.
CHART_TYPES = ("line", "bar", "area", "scatter", "map", "table")

PROJECT_ID_PARAM = {
    "type": "string",
    "description": "Project id, from studio_list_projects.",
}

PLOT_SPEC_PARAM = {
    "type": "object",
    "description": (
        "Chart definition. `sources`: query ids to load. `transform`: "
        "optional DuckDB SQL over those sources, run in the browser. "
        "`chart`: one of line, bar, area, scatter, map, table. `x`/`y`: "
        "column names in the transformed result. `series`: optional column "
        "to split lines or bar groups by."
    ),
    "properties": {
        "sources": {"type": "array", "items": {"type": "string"}},
        "transform": {"type": "string"},
        "chart": {"type": "string", "enum": list(CHART_TYPES)},
        "x": {"type": "string"},
        "y": {"type": "string"},
        "series": {"type": "string"},
    },
}

STUDIO_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__studio_list_projects",
            "description": (
                "Lists the user's Data Studio projects: id, name, and how "
                "many source queries and plots each holds."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__studio_get_project",
            "description": (
                "Returns one project in full: its source queries (id, name, "
                "language, text) and its plots (id, name, chart spec). Query "
                "text is abbreviated; `query_id` returns that one whole."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": PROJECT_ID_PARAM,
                    "query_id": {
                        "type": "string",
                        "description": (
                            "Optional. Return this query's text in full "
                            "instead of abbreviated."
                        ),
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__studio_create_project",
            "description": (
                "Creates a Data Studio project — the container for an "
                "analysis, holding source queries and the plots built from "
                "them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Short title describing the analysis, e.g. "
                            "'Single-bidder rates in Hungary'."
                        ),
                    },
                    "investigation_id": {
                        "type": "string",
                        "description": (
                            "Optional. Attach to an investigation so the "
                            "project sits alongside its articles."
                        ),
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__studio_rename_project",
            "description": "Rename a Data Studio project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": PROJECT_ID_PARAM,
                    "name": {"type": "string"},
                },
                "required": ["project_id", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__studio_add_query",
            "description": (
                "Adds a source query to a project. A source produces a "
                "table of rows that plots combine. `lang` selects the store "
                "the query runs against: 'cypher' is the knowledge graph "
                "(companies, contracts, ownership), 'sql' is the statistics "
                "warehouse (Eurostat observations by region and year), "
                "'sparql' is the RDF store (ontology and transitive "
                "ownership). Each store speaks only its own language."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": PROJECT_ID_PARAM,
                    "name": {
                        "type": "string",
                        "description": (
                            "Short descriptive name; plots refer to it."
                        ),
                    },
                    "lang": {
                        "type": "string",
                        "enum": list(QUERY_LANGS),
                        "description": (
                            "cypher = Neo4j graph, sql = Eurostat/stats "
                            "warehouse, sparql = Virtuoso RDF."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Query text in the language named by `lang`. "
                            "Max 8000 characters."
                        ),
                    },
                },
                "required": ["project_id", "name", "lang", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__studio_update_query",
            "description": (
                "Changes an existing source query's name, language or "
                "text. Omitted fields are left unchanged."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": PROJECT_ID_PARAM,
                    "query_id": {
                        "type": "string",
                        "description": "From studio_get_project.",
                    },
                    "name": {"type": "string"},
                    "lang": {"type": "string", "enum": list(QUERY_LANGS)},
                    "query": {"type": "string"},
                },
                "required": ["project_id", "query_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__studio_add_plot",
            "description": (
                "Adds a plot: one or more source queries, an optional "
                "DuckDB SQL transform that runs in the browser, and the "
                "chart settings. The transform is DuckDB SQL whatever "
                "language produced the sources; each source arrives in it "
                "as a table named after the source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": PROJECT_ID_PARAM,
                    "name": {"type": "string", "description": "Chart title."},
                    "spec": PLOT_SPEC_PARAM,
                },
                "required": ["project_id", "name", "spec"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__studio_update_plot",
            "description": (
                "Changes an existing plot's name or chart spec. Omitted "
                "fields are left unchanged."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": PROJECT_ID_PARAM,
                    "plot_id": {
                        "type": "string",
                        "description": "From studio_get_project.",
                    },
                    "name": {"type": "string"},
                    "spec": PLOT_SPEC_PARAM,
                },
                "required": ["project_id", "plot_id"],
            },
        },
    },
]


#: The tool names the executor dispatches on. Nothing in the browser needs
#: to recognise these any more — they run server-side.
STUDIO_ACTIONS = tuple(t["function"]["name"] for t in STUDIO_TOOLS)
