"""Authorization service — central policy-decision point.

Every state-mutating router handler in src/api/routers/ must call
``authz.require(principal, action, resource)`` before doing the work.
The handler decides what action to ask for; the policy decides whether
the answer is yes. Routers do not implement policy; services do not
duplicate role checks. There is exactly one place to look when asking
"can this user do this?" — :class:`AuthorizationService`.

Why we wrote this in plain Python instead of pulling Casbin / oso /
OPA: the policy table has ~30 actions and ~6 resource kinds. Plain
Python policy functions are (a) type-checked by mypy, (b) grep-able
when investigating "where can this action happen?", (c) trivially
testable per matrix cell, and (d) reviewable in a code review without
context-switching into a separate policy DSL. The day the policy
matrix outgrows what a senior engineer can hold in their head, we
re-evaluate.

Public surface:

  - :class:`Action` — strongly-typed action enum (str-valued for
    serialisation into the audit log).
  - :class:`Principal` — a snapshot of the caller (id + trust level
    + roles + active sanction).
  - :class:`ResourceRef` — a lightweight view of a resource with the
    attributes the policy needs (owner, visibility). Built by
    classmethod adapters so callers don't have to know the schema.
  - :class:`Decision` — the outcome (allowed + reason). Used for both
    the audit log and the eventual ``PermissionDenied.message``.
  - :class:`AuthorizationService` — the thing routers inject.
"""
from src.services.authz.actions import Action
from src.services.authz.policy import Decision, Principal, ResourceRef
from src.services.authz.service import AuthorizationService

__all__ = [
    "Action",
    "AuthorizationService",
    "Decision",
    "Principal",
    "ResourceRef",
]
