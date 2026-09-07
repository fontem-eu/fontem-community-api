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

from src.services.studio_validation import CHART_TYPES as VALIDATOR_CHART_TYPES

#: Query engines a source can use. Each runs through a read-only,
#: row-and-timeout-capped proxy; see the Studio documentation for schemas.
QUERY_LANGS = ("cypher", "sql", "sparql")

#: Re-exported from the validator, never redefined. This module used to
#: carry its own copy — ("line", "bar", "area", "scatter", "map", "table")
#: — and the two drifted until only `line` overlapped. The model was
#: offered six chart types, five of which the validator refuses on sight,
#: and none of bar_h, corr_matrix, stat or atlas_map, which work. It cost
#: an iteration run three refused plots before it gave up on charting.
#:
#: The test below pins this against studio_validation, not against itself:
#: the old test asserted the schema matched THIS constant, which is why a
#: drift between the offer and the enforcement was invisible.
CHART_TYPES = VALIDATOR_CHART_TYPES

PROJECT_ID_PARAM = {
    "type": "string",
    "description": "Project id, from studio_list_projects.",
}

#: The description is generated from CHART_TYPES rather than written out,
#: so it cannot drift from the enum beside it. Both were wrong before: the
#: prose and the enum agreed with each other and disagreed with the
#: validator, which is the worst arrangement — nothing looked inconsistent
#: from inside this file.
PLOT_SPEC_PARAM = {
    "type": "object",
    "description": (
        "Chart definition. `sources`: the queries to draw from, as "
        "{name, lang, query} OBJECTS — not ids, not names. `transform`: "
        "optional DuckDB SQL over those sources, run in the browser. "
        "`chart`: one of " + ", ".join(CHART_TYPES) + ". `x`/`y`: column "
        "names in the transformed result. `series`: optional ARRAY of "
        "column names, one drawn per column — it is NOT a column to group "
        "by. To compare two periods, return one column per period "
        "(e.g. RETURN sector, before_eur, after_eur) and pass "
        "series: [\"before_eur\", \"after_eur\"]."
    ),
    "properties": {
        # Objects, because that is what studio_validation accepts. The
        # schema said `string` and the validator answered "source 0 is not
        # an object", which is a refusal the model cannot act on: it was
        # doing exactly what it was told.
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "lang": {"type": "string", "enum": ["sql", "cypher", "sparql"]},
                    "query": {"type": "string"},
                },
                "required": ["name", "lang", "query"],
            },
        },
        "transform": {"type": "string"},
        "chart": {"type": "string", "enum": list(CHART_TYPES)},
        "x": {"type": "string"},
        "y": {"type": "string"},
        # An ARRAY, because the renderer does
        #     series: Array.isArray(spec.series) ? [...spec.series] : []
        # and draws "one line per chosen series column". Declared as a
        # string, it was read as a grouping column — a different idea
        # entirely — and a string given to the validator was iterated
        # CHARACTER BY CHARACTER: series:"period" came back as six errors,
        # "series[0]='p' is not a column", through to 'd'. The model could
        # not act on that, dropped `series`, and shipped a chart with no
        # before/after split — which was the whole point of the article.
        "series": {"type": "array", "items": {"type": "string"}},
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
            "name": "mcp__gmr__studio_run_query",
            "description": (
                "Executes a SAVED query from a project and returns its "
                "rows. This is how you see what a query finds: create or "
                "update it first, then run it here. Execution is read-only, "
                "row-capped and time-limited — the same proxy as the "
                "Studio's Run button — and large results come back "
                "truncated with a marker."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": PROJECT_ID_PARAM,
                    "query_id": {
                        "type": "string",
                        "description": "Query id, from studio_get_project.",
                    },
                },
                "required": ["project_id", "query_id"],
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
                "ownership). Each store speaks only its own language. "
                "The query is checked against the engine before it is "
                "saved: if it does not parse, or names a label or column "
                "that does not exist, NOTHING is saved and you get the "
                "engine's error back — fix the query and call this again."
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
                "text. Omitted fields are left unchanged. A new query text "
                "is checked against the engine first: if it does not work, "
                "the stored query is left alone and you get the error back."
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
                "as a table named after the source. The spec is checked "
                "before it is saved — chart type, each source query against "
                "its engine, and (when there is no transform) that the axes "
                "name columns the sources actually return. Nothing is saved "
                "if it fails; you get the reasons back."
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
                "fields are left unchanged. A new spec is checked first; if "
                "it fails the stored plot is left alone and you get the "
                "reasons back."
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
