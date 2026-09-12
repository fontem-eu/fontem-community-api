"""The contract card says when a contract has been modified.

A contract's entity in the graph carries its whole chain — the award and
every modification notice — and the neo4j sink now maintains that on
write (P4 of gitops/docs/roadmap/contract-modifications-single-path.md).
Two facets let the card show it: `modified` (the chain has more than one
notice, or the contract is known only through a modification) and
`award_ingested` (false when the award itself was never loaded — the
card can then say the figure is a restated one).

Same rules as 024: update by slug, never insert; idempotent — a row that
already emits `modified:` is left alone, so an admin edit made after
this ships is not reverted on the next deploy; the card ignores facets
it does not know, so app and data may roll in either order.

Revision ID: 025
Revises: 024
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

QUERIES = {
    "public-contracts": """MATCH (a:Authority)-[:AWARDED]->(c:Contract)
WHERE c.is_current = true
  AND c.value_quality_flag = 'ok'
  AND c.current_value IS NOT NULL
  AND c.current_value >= $min_value
  AND c.publication_date > left($since, 10)
WITH c, a ORDER BY a.name
WITH c, collect(DISTINCT a.name) AS buyers, collect(DISTINCT a.nuts) AS regions
WHERE 'EU' IN $nuts
   OR any(r IN regions WHERE any(p IN $nuts WHERE r STARTS WITH p))
OPTIONAL MATCH (c)-[:AWARDED_TO]->(w:Company)
WITH c, buyers, regions, w ORDER BY w.name
WITH c, buyers, regions, collect(DISTINCT w.name) AS winners
RETURN
  c.contract_key AS item_id,
  c.publication_date AS item_time,
  CASE WHEN size(regions) = 0 THEN ['EU'] ELSE regions END AS nuts,
  c.current_value AS rank_value,
  CASE size(buyers)
    WHEN 0 THEN 'A public body'
    WHEN 1 THEN buyers[0]
    WHEN 2 THEN buyers[0] + ' and 1 other buyer'
    ELSE buyers[0] + ' and ' + toString(size(buyers) - 1) + ' other buyers'
  END + ' awarded ' + toString(toInteger(round(c.current_value))) + ' EUR to ' +
  CASE size(winners)
    WHEN 0 THEN 'an undisclosed supplier'
    WHEN 1 THEN winners[0]
    WHEN 2 THEN winners[0] + ' and 1 other'
    ELSE winners[0] + ' and ' + toString(size(winners) - 1) + ' others'
  END AS title,
  'https://dargle.eu/contract/' + c.ted_notice_id AS link,
  coalesce(c.title, '') AS summary,
  {
    kind: 'contract',
    headline: coalesce(c.title, ''),
    from: CASE WHEN size(buyers) = 0 THEN null ELSE buyers[0] END,
    from_more: CASE WHEN size(buyers) > 1 THEN size(buyers) - 1 ELSE 0 END,
    to: winners[0..2],
    to_more: CASE WHEN size(winners) > 2 THEN size(winners) - 2 ELSE 0 END,
    value_eur: c.current_value,
    red_flags: c.integrity_red_flags,
    single_bidder: c.is_single_bidder,
    tenders: c.tenders_received,
    procedure: c.procedure_type,
    modified: coalesce(c.notice_count, 1) > 1 OR c.notice_kind = 'modification',
    award_ingested: c.award_ingested
  } AS facets
ORDER BY item_time DESC, rank_value DESC""",
}


def upgrade() -> None:
    conn = op.get_bind()
    for slug, query in QUERIES.items():
        conn.execute(
            sa.text(
                "UPDATE named_queries SET query = :query, updated_at = NOW() "
                "WHERE slug = :slug AND query NOT LIKE '%modified:%'"
            ),
            {"slug": slug, "query": query},
        )


def downgrade() -> None:
    # The two facets are optional and the card renders without them, so
    # the previous text has nothing this one lacks. Rolling the app back
    # leaves the row valid.
    pass
