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

STUDIO_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__studio_create_project",
            "description": (
                "Create a Data Studio project — the container for an "
                "analysis. A project holds source queries and the plots "
                "built from them. Call this first when the user asks for a "
                "chart or an exploration and there is no project open yet; "
                "if one is already open its id is in the studio context and "
                "you should add to it instead of starting another."
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
                            "Optional. Attach the project to an existing "
                            "investigation so it appears alongside its "
                            "articles."
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
            "name": "mcp__gmr__studio_add_query",
            "description": (
                "Add a source query to a Data Studio project. A source "
                "produces a table of rows that plots then combine. Pick "
                "`lang` by which store holds the data: 'cypher' for the "
                "knowledge graph (companies, contracts, ownership), 'sql' "
                "for the statistics warehouse (Eurostat observations by "
                "region and year), 'sparql' for the RDF store (ontology "
                "queries, transitive ownership chains). Write the query in "
                "that language — they are not interchangeable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Project to add the source to.",
                    },
                    "name": {
                        "type": "string",
                        "description": (
                            "Short name for the source; plots reference it, "
                            "so make it descriptive."
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
                            "The query text, in the language named by "
                            "`lang`. Max 8000 characters."
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
            "name": "mcp__gmr__studio_add_plot",
            "description": (
                "Add a plot to a Data Studio project. A plot names one or "
                "more source queries, optionally combines them with a "
                "DuckDB SQL transform that runs in the browser, and charts "
                "the result. The transform is always DuckDB SQL regardless "
                "of which language produced the sources — each source is "
                "available as a table named after the source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "name": {
                        "type": "string",
                        "description": "Title shown above the chart.",
                    },
                    "spec": {
                        "type": "object",
                        "description": (
                            "Chart definition. Keys: `sources` (list of "
                            "query ids to load), `transform` (optional "
                            "DuckDB SQL over those sources), `chart` (one "
                            "of line, bar, area, scatter, map, table), `x` "
                            "and `y` (column names from the transformed "
                            "result), `series` (optional column to split "
                            "lines by)."
                        ),
                        "properties": {
                            "sources": {"type": "array", "items": {"type": "string"}},
                            "transform": {"type": "string"},
                            "chart": {"type": "string", "enum": list(CHART_TYPES)},
                            "x": {"type": "string"},
                            "y": {"type": "string"},
                            "series": {"type": "string"},
                        },
                    },
                },
                "required": ["project_id", "name", "spec"],
            },
        },
    },
]

#: Names the frontend must recognise to execute an action. Kept here rather
#: than duplicated in the panel so the two cannot drift; a parity test pins
#: it against the JS list.
STUDIO_ACTIONS = tuple(t["function"]["name"] for t in STUDIO_TOOLS)


def scope_studio(tools: list[dict], *, has_studio: bool) -> list[dict]:
    """Offer the Studio tools only where they can actually be executed.

    Same rule as `propose_edit` and for the same reason: a tool whose result
    nothing can apply produces a proposal the user cannot accept, and the
    model has no way to know why. `has_studio` is the client's own answer —
    it knows whether the Studio is reachable for this user — rather than
    something inferred here, which is the mistake that cost a day on
    `has_editor`.
    """
    if has_studio:
        return list(tools) + list(STUDIO_TOOLS)
    return list(tools)
