

class TestTheRequestSessionOnFailure:
    """A failed request must roll back, and must not eat the error.

    Production, 2026-09-01: `POST /assist/chat/stream` answered 500 with
    `sqlalchemy.exc.PendingRollbackError: Can't reconnect until invalid
    transaction is rolled back` wrapped in a dishka ExitError. That is a
    TEARDOWN error — it replaced whatever actually went wrong in the turn,
    which is the error the user needed to see.

    The cause is a protocol detail worth stating plainly: dishka resumes a
    generator provider with `agen.asend(exception)` (async_container.py),
    it does not `athrow`. So the exception arrives as the RESULT of the
    `yield`, not as something raised at it. A provider written as

        yield session
        await session.commit()
      except Exception:
        await session.rollback()

    therefore commits on failure — the except branch only ever catches
    errors raised by the commit itself. Two consequences: a half-finished
    request gets committed, and when the transaction is already invalid
    the commit's own error masks the real one.
    """

    @staticmethod
    def _run(gen_factory, exception):
        """Drive a provider generator the way dishka does."""
        import asyncio

        async def go():
            agen = gen_factory()
            await agen.asend(None)          # advance to the yield
            try:
                await agen.asend(exception)  # dishka's teardown
            except StopAsyncIteration:
                pass
        asyncio.new_event_loop().run_until_complete(go())

    def test_a_failed_request_rolls_back_and_does_not_commit(self):
        from src.api.di import DatabaseProvider

        calls = []

        class _Session:
            async def commit(self): calls.append("commit")
            async def rollback(self): calls.append("rollback")
            async def close(self): calls.append("close")

        provider = DatabaseProvider("postgresql+asyncpg://unused/unused")
        self._run(lambda: provider.session(lambda: _Session()),
                  RuntimeError("the turn blew up"))
        assert "rollback" in calls, (
            "a failed request must roll back; dishka sends the exception "
            f"into the generator rather than raising it. calls={calls}")
        assert "commit" not in calls, (
            f"a failed request must not be committed. calls={calls}")
        assert calls[-1] == "close"

    def test_a_successful_request_still_commits(self):
        from src.api.di import DatabaseProvider

        calls = []

        class _Session:
            async def commit(self): calls.append("commit")
            async def rollback(self): calls.append("rollback")
            async def close(self): calls.append("close")

        provider = DatabaseProvider("postgresql+asyncpg://unused/unused")
        self._run(lambda: provider.session(lambda: _Session()), None)
        assert calls == ["commit", "close"], calls
