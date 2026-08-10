"""Tests for the login-handler timing oracle fix (security review
2026-06-11 finding #2).

Pre-fix the handler short-circuited on `user is None` before running
bcrypt, so a missing-email login returned ~13 ms while a real-but-
wrong-password login took ~220 ms. That ~200 ms gap, combined with
the 10/min per-IP rate limit, let an attacker confirm ~7,200 email
registrations/day per IP — a botnet trivially parallelised that to
full enumeration.

We pin three properties:

1. **Logic** — the handler must run a bcrypt verify even when the user
   doesn't exist. Asserted by patching ``_check_password`` and counting
   calls. Deterministic, runs in milliseconds, doesn't depend on
   wall-clock noise. This is the test that *prevents the regression*.

2. **Wall-clock** — the missing-user response time must be in the same
   order of magnitude as a real-but-wrong-password response. A loose
   ratio (≤ 3×) — tight enough to fail the original ~17× gap, loose
   enough to survive CI scheduler jitter on a contended runner.

3. **Response shape** — same status, same body. The pre-fix handler
   already matched on body; the regression-prone bit was the
   distinguishable *latency*. Pinning shape too means a future refactor
   can't accidentally split the two paths back apart.
"""
from __future__ import annotations

import asyncio
import time
import uuid as _uuid
from unittest.mock import patch

import bcrypt

from src.domain.user import User


def _register_user(user_repo, email: str, password: str) -> str:
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    uid = str(_uuid.uuid4())
    user = User(id=uid, email=email, name="Test", password_hash=pw_hash)
    asyncio.get_event_loop().run_until_complete(user_repo.upsert(user))
    return uid


class TestLoginTimingOracle:
    """SEC-2026-06-11 #2 — login timing oracle."""

    def test_bcrypt_runs_when_user_does_not_exist(self, client, services):
        """The missing-user path must invoke ``_check_password`` so the
        bcrypt round happens. Patching the helper and asserting
        ``called_once`` keeps the test deterministic — no wall-clock
        measurement, no flakiness."""
        _register_user(services["user_repo"], "real@test.com", "pw")

        with patch(
            "src.api.routers.auth._check_password",
            wraps=lambda _pw, _h: False,
        ) as mock_check:
            resp = client.post("/auth/login", json={
                "email": "ghost-no-account@test.com",
                "password": "anything",
            })

        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid email or password"
        assert mock_check.call_count == 1, (
            "login handler short-circuited on missing user without running "
            "bcrypt — the timing oracle is back. See the file docstring."
        )
        # And the dummy hash, not a real one, was the second arg.
        called_with_hash = mock_check.call_args.args[1]
        assert called_with_hash.startswith("$2"), (
            "bcrypt hash format expected, got: " + called_with_hash[:10]
        )

    def test_wallclock_parity_between_missing_and_wrong_password(
        self, client, services,
    ):
        """Real-but-wrong-password and missing-user paths must take wall-
        clock times in the same order of magnitude.

        Loose ratio (<= 3x) so a contended CI runner doesn't flake — the
        pre-fix gap was ~17x and the post-fix gap on a quiet machine is
        ~1.1x. Either end of that band is dispositive."""
        _register_user(services["user_repo"], "real-wallclock@test.com", "correct-pw")

        # Warm-up — the first POST through TestClient pays the FastAPI
        # cold-start cost. We don't want that landing on either side.
        client.post("/auth/login", json={
            "email": "warmup@test.com", "password": "wrong",
        })

        def time_login(email: str) -> float:
            t0 = time.perf_counter()
            resp = client.post("/auth/login", json={
                "email": email, "password": "wrong-pw",
            })
            elapsed = time.perf_counter() - t0
            assert resp.status_code == 401
            return elapsed

        # 5 samples each, interleaved so any background load hits both.
        # Take the median to drop the slowest GC-pause sample on each
        # side; means are skewed by occasional 10x-slow samples on
        # contended runners.
        real_samples, missing_samples = [], []
        for _ in range(5):
            real_samples.append(time_login("real-wallclock@test.com"))
            missing_samples.append(time_login(f"missing-{_uuid.uuid4()}@test.com"))

        real_samples.sort()
        missing_samples.sort()
        real_median = real_samples[len(real_samples) // 2]
        missing_median = missing_samples[len(missing_samples) // 2]

        ratio = max(real_median, missing_median) / min(real_median, missing_median)
        assert ratio <= 3.0, (
            f"Login wall-clock ratio (real={real_median*1000:.1f} ms, "
            f"missing={missing_median*1000:.1f} ms, ratio={ratio:.2f}x) "
            f"exceeded the 3x ceiling. The pre-fix ratio was ~17x; "
            f"anything > 3 suggests the constant-time bcrypt path "
            f"regressed. Full samples: real={real_samples}, "
            f"missing={missing_samples}."
        )
