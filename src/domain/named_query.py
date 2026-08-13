"""The feed-query catalogue — named queries and the groups they sit in.

A *named query* is an editorially-curated query against one of the platform's
stores, authored and reviewed by site admins. It is what a feed subscription
points at: one curated query serves many subscribers by varying its bind
parameters (region, watermark) rather than being forked per subscriber.

This is deliberately NOT the Data Studio's ``data_queries``. That table is
project-scoped and owner-private — a personal workspace artifact. A named
query is platform content: it has a stable slug, a publication state, and a
recorded verdict on whether it satisfies the feed contract.

A *query group* is an ordered set of named queries — "Public investment",
"Corporate influence". Groups are what the public picker shows. Membership is
many-to-many: a query about energy-sector lobbying belongs in both a
"Corporate influence" group and an "Energy" one, without being duplicated.
Adding a group is authoring content, not a schema migration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# Publication states. ``draft`` is the authoring bench; ``published`` is
# visible to the public catalogue and subscribable; ``retired`` keeps the row
# (existing subscriptions still resolve their history) but hides it from the
# picker. Publishing is gated on the contract verdict — see NamedQueryService.
STATUSES = ("draft", "published", "retired")

# Engines we can execute. SPARQL is accepted and storable, but has no native
# bind-parameter protocol, so a SPARQL query cannot satisfy the bind checks
# and will not become subscribable until that is solved. Storing it anyway
# means the catalogue can hold the work-in-progress instead of losing it.
LANGS = ("sql", "cypher", "sparql")

PARAM_TYPES = ("text", "text[]", "number", "timestamp", "boolean")


@dataclass
class QueryParam:
    """A declared bind of a named query.

    Declaring parameters is what lets one query serve many subscriptions: the
    UI renders the declaration, the subscriber fills it in, and nobody edits
    SQL. ``name`` is the bind name as it appears in the query text, in that
    engine's own syntax (``%(nuts)s`` for SQL, ``$nuts`` for Cypher).
    """

    name: str = ""
    type: str = "text"
    label: str = ""
    required: bool = False
    default: Any = None


@dataclass
class ContractCheck:
    """One line of the contract verdict.

    ``reason`` is populated whether the check passed or failed. A failing
    check without a reason would be exactly the silence the contract forbids.
    """

    id: str = ""
    passed: bool = False
    reason: str = ""
    waived: bool = False


@dataclass
class ContractReport:
    """The stored outcome of validating a named query against the contract.

    Carries the cost signal (``duration_ms`` / ``row_count``) alongside the
    verdict, because "does it work" and "what does it cost to run this on a
    schedule for every subscriber" are the same review.
    """

    subscribable: bool = False
    checks: list[ContractCheck] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    duration_ms: int = 0
    error: str | None = None
    checked_at: datetime | None = None

    def failures(self) -> list[ContractCheck]:
        return [c for c in self.checks if not c.passed and not c.waived]


@dataclass
class NamedQuery:  # pylint: disable=too-many-instance-attributes
    id: str | None = None
    slug: str = ""
    name: str = ""
    description: str = ""
    lang: str = "sql"
    query: str = ""
    params: list[QueryParam] = field(default_factory=list)
    status: str = "draft"
    # Waived contract checks: check id -> the admin's written reason. Only the
    # bind checks are waivable (a genuinely EU-level or snapshot query has no
    # region or no watermark), and a waiver always costs a sentence — you
    # cannot wave something through in silence.
    waivers: dict[str, str] = field(default_factory=dict)
    contract_ok: bool = False
    contract_report: ContractReport | None = None
    validated_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class QueryGroup:  # pylint: disable=too-many-instance-attributes
    id: str | None = None
    slug: str = ""
    name: str = ""
    description: str = ""
    sort_order: int = 0
    # 'public' shows in the catalogue picker; 'admin' keeps a group as a
    # staging shelf while its queries are still being reviewed.
    visibility: str = "public"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Populated on read; ordered by the membership row's sort_order, so the
    # same query can sit at a different position in each group it belongs to.
    queries: list[NamedQuery] = field(default_factory=list)
