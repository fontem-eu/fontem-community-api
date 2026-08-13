from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Entity types that produce activity, and the CUD verbs recorded.
ENTITY_TYPES = ("story", "dossier", "investigation", "issue")
ACTIONS = ("created", "updated", "deleted")


@dataclass
# An audit entry genuinely carries this much: what happened, to what, who
# did it, on whose behalf, from where, and when. Splitting it would only
# move the count.
# pylint: disable-next=too-many-instance-attributes
class ActivityEvent:
    """One create/update/delete a user performed, for their activity feed."""
    id: str | None = None
    actor_id: str = ""        # the user who performed the action
    entity_type: str = ""     # story | dossier | investigation | issue
    entity_id: str = ""
    action: str = ""          # created | updated | deleted
    summary: str = ""         # title/name captured at event time (for display)
    created_at: datetime | None = None
    # ── Provenance ────────────────────────────────────────────
    # Who caused this, and on whose behalf. "user" for anything a person did
    # directly; "agent" when the assistant did it while acting for them —
    # actor_id stays the user either way, because the agent has no standing
    # of its own and the permission that allowed it was theirs.
    actor_kind: str = "user"
    # Where to look it up. Set only for agent-caused entries: the
    # conversation, and the specific tool call inside it. The reference is
    # allowed to dangle — deleting a conversation unlinks the activity, it
    # does not delete it.
    conversation_id: str | None = None
    message_id: str | None = None
    # Correlates everything written while serving one request.
    request_id: str | None = None
