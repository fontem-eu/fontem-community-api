# Authorization service

The `src/services/authz/` package is the single decision point for
"can this user do this?" across the API. Every state-mutating router
handler MUST call `await authz.require(principal, action, resource)`
before mutating anything. Read-side handlers are free to call
`await authz.decide(...)` instead when they want the verdict but not
an exception (e.g. for UI gating).

This document covers (a) the architecture (b) how to add a new
action (c) how to add a new resource kind.

## Architecture

Three building blocks:

- **`Action`** (`actions.py`) — strongly-typed enum of every operation
  the API can authorize. String values land in the `authz_audit`
  table, so renaming an enum *member* is free but renaming the
  *string value* is a data-migration event.

- **`Principal`** + **`ResourceRef`** (`policy.py`) — value-object
  snapshots passed into every decision. The principal carries the
  three things the policy needs about the caller (trust level, role
  set, active sanction); the ResourceRef carries the resource's
  owner and visibility. Snapshots so the same user's state can't
  flip mid-handler.

- **`policy.py`** — a dict mapping `Action → check function`. Each
  check is a pure function `(Principal, ResourceRef | None) -> Decision`
  with no I/O. New action = new dict entry. This is the load-bearing
  table; if a check is missing, the default is **deny** (fail closed).

- **`AuthorizationService`** (`service.py`) — the thing routers
  inject. Provides:
  - `await authz.principal(user_id)` to build the Principal snapshot.
  - `await authz.require(principal, action, resource)` to enforce
    (raises `PermissionDenied` on deny).
  - `await authz.decide(...)` to compute + audit without raising.

  Every call writes to `authz_audit` so we have a complete record of
  every authorization decision, including the allow paths.

## How to add a new action

1. Add the enum member to `Action`:

   ```python
   class Action(StrEnum):
       # ... existing ...
       WIDGETS_CREATE = "widgets:create"
   ```

2. Add the policy clause to `POLICY`:

   ```python
   POLICY = {
       # ... existing ...
       Action.WIDGETS_CREATE: _trust_at_least_factory("contributor"),
   }
   ```

3. Add a matrix test row in `tests/unit/test_authz.py`:

   ```python
   def test_widgets_create_requires_contributor(self):
       assert not evaluate(_principal(trust_level="new_user"), Action.WIDGETS_CREATE, None).allowed
       assert evaluate(_principal(trust_level="contributor"), Action.WIDGETS_CREATE, None).allowed
   ```

4. Call it from your service / router:

   ```python
   principal = await self._authz.principal(user_id)
   await self._authz.require(principal, Action.WIDGETS_CREATE)
   ```

## How to add a new resource kind

1. Add a `created_by` (or whatever ownership anchor) to the domain
   class + repo + Postgres model + alembic migration.

2. Add a `ResourceRef.for_widget` classmethod adapter so callers
   don't need to know the schema:

   ```python
   @classmethod
   def for_widget(cls, widget) -> "ResourceRef":
       return cls(
           kind="widget",
           id=widget.id,
           owner_id=getattr(widget, "created_by", None),
           visibility=None,
       )
   ```

3. Write the per-action check functions (or reuse `_owner_only` /
   `_trust_at_least_factory` if they fit).

## Sanctions

Sanctions take precedence over every other gate, including the admin
role. A `ban` blocks everything. A `suspend` blocks state-mutating
actions but allows reads (so a suspended user can still see why
they're suspended). A `mute` is enforced inside specific actions
(currently `ISSUES_COMMENT`) rather than globally.

## Why not Casbin / oso / OPA

The policy table has ~30 actions and ~6 resource kinds. Plain Python
policy functions are (a) type-checked by mypy, (b) grep-able when
investigating "where can this action happen?", (c) trivially testable
per matrix cell, (d) reviewable in a code review without
context-switching into a separate DSL. The day the matrix outgrows
what a senior engineer can hold in their head, we re-evaluate.

## Audit log

`authz_audit` rows have:

| column         | type        | note                                    |
|----------------|-------------|-----------------------------------------|
| `id`           | UUID PK     | random                                  |
| `timestamp`    | TIMESTAMPTZ | server-side default                     |
| `user_id`      | UUID null   | NOT a FK — survives user deletion       |
| `action`       | TEXT        | the Action enum's string value          |
| `resource_kind`| TEXT null   | from ResourceRef.kind                   |
| `resource_id`  | UUID null   | from ResourceRef.id                     |
| `allowed`      | BOOL        | the verdict                             |
| `reason`       | TEXT        | from the Decision (human-readable)      |

Indexes:
- `(user_id, timestamp)` — "what did Alice do?"
- `(action, timestamp)`  — "all moderation activity last week"

The repo commits each write eagerly so a request crash after the
authz check still leaves the audit row in place.
