"""The catalogue queries emit facets — the parts a card lays out.

Until now the three curated queries composed one sentence per finding and
the card had to print it. fontem-community-api #272 gave feed items an
optional `facets` map so a query can emit the parts — headline, buyer →
supplier, value, integrity count — and the card gives each its place.

These rows were authored in the admin UI, which is why no migration seeded
them and why 023 could not change them. That was the wrong place for them
to live: a query the product depends on is code, and it must reach every
environment the same way the schema does — through this job, as a PreSync
hook, before the new app starts. From here the text is owned here.

Updates by slug, never inserts: a fresh environment gets its catalogue from
the admin UI or a seed, and a slug that is absent is not this migration's
to invent. Idempotent: a row already emitting facets is left alone, so an
admin edit made after this ships is not silently reverted on the next
deploy. The runtime tolerates the extra column either way — `_to_items`
reads facets when present and ignores it when not — so app and data may
roll in either order.

Editing a query through the service demotes it to draft until re-validated;
this writes the column directly and leaves `status` alone, because the
contract columns are unchanged and the new one is optional. The next
feed refresh materialises facets for new rows and fills them into rows
that have none.

Revision ID: 024
Revises: 023
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
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
    procedure: c.procedure_type
  } AS facets
ORDER BY item_time DESC, rank_value DESC""",
    "eu-lobbying": """MATCH (l:Lobbyist)
WHERE l.detail_last_updated > left($since, 10)
  AND l.detail_last_updated <= toString(date())
  AND l.detail_name IS NOT NULL
RETURN
  'lobby:' + l.disclosure_id + ':' + l.detail_last_updated AS item_id,
  l.detail_last_updated AS item_time,
  ['EU'] AS nuts,
  l.detail_cost_max AS rank_value,
  l.detail_name +
    CASE WHEN l.detail_cost_max IS NULL
         THEN ' updated its EU lobbying declaration'
         ELSE ' updated its EU lobbying declaration — up to ' +
              toString(toInteger(round(l.detail_cost_max))) + ' EUR declared'
    END AS title,
  'https://dargle.eu/lobbyist/' + l.disclosure_id AS link,
  coalesce(l.detail_category, '') AS summary,
  {
    kind: 'lobby',
    headline: l.detail_name,
    from: l.detail_name,
    to: [],
    value_eur: l.detail_cost_max
  } AS facets
ORDER BY item_time DESC, rank_value DESC""",
    "cohesion-projects": """MATCH (p:CohesionProject)
WHERE p.detail_start_date > left($since, 10)
  AND p.detail_start_date <= toString(date())
  AND p.detail_eu_contribution IS NOT NULL
  AND p.detail_eu_contribution >= $min_value
  AND p.detail_nuts_code IS NOT NULL
  AND ('EU' IN $nuts OR any(pre IN $nuts WHERE p.detail_nuts_code STARTS WITH pre))
RETURN
  'cohesion:' + p.disclosure_id AS item_id,
  p.detail_start_date AS item_time,
  [p.detail_nuts_code] AS nuts,
  p.detail_eu_contribution AS rank_value,
  coalesce(p.title, 'An EU-funded project') + ' — ' +
    toString(toInteger(round(p.detail_eu_contribution))) + ' EUR from ' +
    coalesce(p.detail_fund, 'EU cohesion funds') AS title,
  'https://fontem.eu/company/' + coalesce(p.company_gmr_id, '') AS link,
  coalesce(p.detail_programme, '') AS summary,
  {
    kind: 'cohesion',
    headline: coalesce(p.title, 'An EU-funded project'),
    from: coalesce(p.detail_fund, 'EU cohesion funds'),
    to: [],
    value_eur: p.detail_eu_contribution
  } AS facets
ORDER BY item_time DESC, rank_value DESC""",
}


def upgrade() -> None:
    conn = op.get_bind()
    for slug, query in QUERIES.items():
        conn.execute(
            sa.text(
                "UPDATE named_queries SET query = :query, updated_at = NOW() "
                "WHERE slug = :slug AND query NOT LIKE '%AS facets%'"
            ),
            {"slug": slug, "query": query},
        )


def downgrade() -> None:
    # The sentence-only text is not kept: the facets column is optional and
    # the card renders without it, so the old query has nothing the new one
    # lacks. Rolling the app back leaves these rows valid.
    pass
