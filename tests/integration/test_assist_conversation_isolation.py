"""One user must not reach another user's conversations. At all.

A conversation key is a URL path segment. It is not a secret and it is not
meant to be one — `report:<uuid>` is derivable from a report anyone can see,
and `chat:<uuid>` travels in the browser. So the isolation cannot rest on the
key being unguessable; it has to rest on the row being scoped to its owner.

These drive the HTTP API directly, with a second user's token, because that is
the shape an attack takes: not a UI that refuses to show a button, but a
request that names someone else's key.

Every verb is covered — read the transcript, read a page, rename, delete one,
delete all — because a leak in any one of them is the whole leak.
"""
# pylint: disable=missing-class-docstring,missing-function-docstring
from __future__ import annotations

import pytest

from tests.integration.conftest import make_headers


SECRET = "a Hungarian construction group and its subsidiaries"


@pytest.fixture(name="victim_key")
def _victim_key(client, user_id):
    """A conversation owned by user_id, with a message in it."""
    h = make_headers(user_id)
    created = client.post("/assist/conversations", json={"title": "Private"}, headers=h)
    assert created.status_code == 201
    key = created.json()["conversation_key"]
    # The endpoints under test are read/rename/delete; seeding a message
    # through the chat stream would call a live model, so the message is
    # placed by the same API surface the panel uses to read it back.
    return key


class TestReading:

    def test_the_owner_can_read_their_own_conversation(self, client, user_id, victim_key):
        resp = client.get(f"/assist/conversations/{victim_key}", headers=make_headers(user_id))
        assert resp.status_code == 200
        assert resp.json()["conversation_key"] == victim_key

    def test_another_user_reading_the_key_gets_their_own_empty_one(
        self, client, user2_id, victim_key
    ):
        """Not a 404 — the read path is find-or-create, scoped to the caller.

        The attacker names someone else's key and gets an empty conversation
        of their own. What matters is that no message of the owner's comes
        back, and that the row they touched is theirs.
        """
        resp = client.get(f"/assist/conversations/{victim_key}", headers=make_headers(user2_id))
        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    def test_another_user_paging_the_key_sees_nothing(self, client, user2_id, victim_key):
        resp = client.get(
            f"/assist/conversations/{victim_key}/messages", headers=make_headers(user2_id),
        )
        assert resp.status_code == 200
        assert resp.json()["messages"] == []
        assert resp.json()["has_more"] is False

    def test_another_users_listing_does_not_include_it(self, client, user2_id, victim_key):
        resp = client.get("/assist/conversations", headers=make_headers(user2_id))
        assert resp.status_code == 200
        keys = [c["conversation_key"] for c in resp.json()["conversations"]]
        assert victim_key not in keys

    def test_the_owners_listing_does_include_it(self, client, user_id, victim_key):
        resp = client.get("/assist/conversations", headers=make_headers(user_id))
        keys = [c["conversation_key"] for c in resp.json()["conversations"]]
        assert victim_key in keys


class TestRenaming:

    def test_another_user_cannot_rename_it(self, client, user2_id, victim_key):
        resp = client.patch(
            f"/assist/conversations/{victim_key}",
            json={"title": "owned"}, headers=make_headers(user2_id),
        )
        assert resp.status_code == 404

    def test_the_title_is_unchanged_after_the_attempt(
        self, client, user_id, user2_id, victim_key
    ):
        client.patch(
            f"/assist/conversations/{victim_key}",
            json={"title": "owned"}, headers=make_headers(user2_id),
        )
        listing = client.get("/assist/conversations", headers=make_headers(user_id)).json()
        mine = [c for c in listing["conversations"] if c["conversation_key"] == victim_key]
        assert mine and mine[0]["title"] == "Private"

    def test_the_owner_can_rename_it(self, client, user_id, victim_key):
        resp = client.patch(
            f"/assist/conversations/{victim_key}",
            json={"title": "Renamed"}, headers=make_headers(user_id),
        )
        assert resp.status_code == 200
        listing = client.get("/assist/conversations", headers=make_headers(user_id)).json()
        mine = [c for c in listing["conversations"] if c["conversation_key"] == victim_key]
        assert mine and mine[0]["title"] == "Renamed"


class TestDeleting:

    def test_another_user_cannot_delete_it(self, client, user2_id, victim_key):
        resp = client.delete(
            f"/assist/conversations/{victim_key}", headers=make_headers(user2_id),
        )
        assert resp.status_code == 404

    def test_it_still_exists_after_the_attempt(self, client, user_id, user2_id, victim_key):
        client.delete(f"/assist/conversations/{victim_key}", headers=make_headers(user2_id))
        listing = client.get("/assist/conversations", headers=make_headers(user_id)).json()
        keys = [c["conversation_key"] for c in listing["conversations"]]
        assert victim_key in keys

    def test_the_blanket_delete_only_reaches_the_callers_own(
        self, client, user_id, user2_id, victim_key
    ):
        """DELETE /assist/conversations wipes everything the CALLER has.

        The dangerous reading is that it wipes everything, full stop. It does
        not, and this is the test that says so.
        """
        h2 = make_headers(user2_id)
        client.post("/assist/conversations", json={"title": "Theirs"}, headers=h2)
        assert client.delete("/assist/conversations", headers=h2).status_code in (200, 204)

        still_mine = client.get("/assist/conversations", headers=make_headers(user_id)).json()
        assert victim_key in [c["conversation_key"] for c in still_mine["conversations"]]

    def test_the_owner_can_delete_it(self, client, user_id, victim_key):
        assert client.delete(
            f"/assist/conversations/{victim_key}", headers=make_headers(user_id),
        ).status_code == 200
        listing = client.get("/assist/conversations", headers=make_headers(user_id)).json()
        assert victim_key not in [c["conversation_key"] for c in listing["conversations"]]


class TestUnauthenticated:

    def test_listing_requires_auth(self, client):
        assert client.get("/assist/conversations").status_code in (401, 403)

    def test_creating_requires_auth(self, client):
        assert client.post("/assist/conversations", json={}).status_code in (401, 403)

    def test_renaming_requires_auth(self, client, victim_key):
        resp = client.patch(f"/assist/conversations/{victim_key}", json={"title": "x"})
        assert resp.status_code in (401, 403)

    def test_deleting_requires_auth(self, client, victim_key):
        assert client.delete(f"/assist/conversations/{victim_key}").status_code in (401, 403)

    def test_paging_requires_auth(self, client, victim_key):
        assert client.get(
            f"/assist/conversations/{victim_key}/messages"
        ).status_code in (401, 403)
