"""Flower business rules — Medium-style clap on stories.

Owns the per-user 50-cap and the visibility check (you can only give
flowers to a story that's public_open or public_auth — private
stories never appear in front of a clapper). The repo layer is
race-safe but policy-blind; this service is the only thing that
knows the cap.
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from src.repositories.flower_repository import FlowerRepository
from src.repositories.report_repository import ReportRepository
from src.services.exceptions import InvalidInput, NotFound


# Mirrors the CHECK constraint on the flowers_given table — keep both
# numbers in lock-step if you raise the cap later.
MAX_FLOWERS_PER_USER = 50

# Visibility values that can receive flowers, split by auth state so
# this endpoint mirrors ReportService.get_viewable. Anonymous callers
# can only see public_open stories — public_auth stories are
# login-walled and would otherwise leak their existence + popularity
# via the flower total. Private stories are excluded for everyone so a
# clapper can't probe for sensitive IDs by observing 200 vs 404.
_FLOWERABLE_ANON = {"public_open"}
_FLOWERABLE_AUTH = {"public_open", "public_auth"}


def _allowed_for(user_id: str | None) -> set[str]:
    return _FLOWERABLE_AUTH if user_id is not None else _FLOWERABLE_ANON


class FlowerService:
    def __init__(
        self,
        flowers: FlowerRepository,
        reports: ReportRepository,
    ) -> None:
        self._flowers = flowers
        self._reports = reports

    async def get_state(
        self, user_id: str | None, report_id: str,
    ) -> dict[str, int]:
        """Read-side: total flowers on the report and (optionally) how
        many the calling user has given.

        Anonymous callers (``user_id is None``) get ``mine: 0``;
        signed-in callers get their per-user count.
        """
        report = await self._reports.get_by_id(report_id)
        if report is None or report.visibility not in _allowed_for(user_id):
            # Same response shape for "missing" and "private" so we
            # don't leak the existence of private stories to anonymous
            # probers.
            raise NotFound(f"story {report_id} not found")
        total = await self._flowers.get_total(report_id)
        mine = (
            await self._flowers.get_mine(user_id, report_id)
            if user_id is not None else 0
        )
        return {"total": total, "mine": mine}

    async def give(self, user_id: str, report_id: str) -> dict[str, int]:
        """Write-side: +1 to the caller's row for this report. Returns
        the fresh ``{total, mine}`` so the caller can render without a
        follow-up GET.

        Raises ``NotFound`` if the report doesn't exist or isn't
        publicly visible; raises ``InvalidInput`` if the caller has
        already hit ``MAX_FLOWERS_PER_USER`` on this story.
        """
        report = await self._reports.get_by_id(report_id)
        # `give` requires auth (router uses get_current_user), so the
        # authed allow-set always applies — keep it explicit.
        if report is None or report.visibility not in _FLOWERABLE_AUTH:
            raise NotFound(f"story {report_id} not found")
        # Atomic upsert with cap enforcement in SQL: returns None when
        # the row is already at the cap and the DO UPDATE WHERE clause
        # blocked the write. No TOCTOU between a separate read + write.
        # IntegrityError is the defence-in-depth backstop: if anything
        # bypasses the WHERE (e.g. an out-of-band INSERT, a stale
        # replica, the cap raised in code but not yet in the CHECK
        # constraint) the DB CHECK fires and we surface it as 400, not
        # as a 500 the generic handler would log as an exception.
        try:
            mine = await self._flowers.increment(
                user_id, report_id, cap=MAX_FLOWERS_PER_USER,
            )
        except IntegrityError as e:
            raise InvalidInput(
                f"flower cap of {MAX_FLOWERS_PER_USER} reached for this story",
            ) from e
        if mine is None:
            raise InvalidInput(
                f"flower cap of {MAX_FLOWERS_PER_USER} reached for this story",
            )
        # Re-read total via the aggregate query rather than tracking
        # the delta in-memory — keeps the response consistent with
        # what other concurrent clappers have written.
        total = await self._flowers.get_total(report_id)
        return {"total": total, "mine": mine}
