"""Which built-in model a user chose.

Separate from CredentialRepository on purpose: that one guards secrets and
refuses to operate without a master key, and a model preference must keep
working when encryption is unavailable — otherwise a Vault hiccup would
take the assistant's model selector down with it.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from src.assistant.local_models import DEFAULT_MODEL_ID, is_known
from src.infra.postgres.models import AssistantModelPrefModel


class ModelPreferenceRepository:
    """Read and write one row per user."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: str) -> str:
        """The user's chosen model id, or the default when unset.

        Never returns something the caller has to validate — an id that is
        no longer offered resolves to the default rather than surfacing.
        """
        row = await self._session.get(AssistantModelPrefModel, user_id)
        if row is None or not is_known(row.model_id):
            return DEFAULT_MODEL_ID
        return row.model_id

    async def set(self, user_id: str, model_id: str) -> str:
        """Store a choice. Rejects ids we do not offer rather than saving
        one that would silently fall back at every turn."""
        if not is_known(model_id):
            raise ValueError(f"unknown model {model_id!r}")
        model_id = model_id.strip().lower()
        row = await self._session.get(AssistantModelPrefModel, user_id)
        if row is None:
            self._session.add(
                AssistantModelPrefModel(user_id=user_id, model_id=model_id)
            )
        else:
            row.model_id = model_id
        await self._session.commit()
        return model_id

    async def clear(self, user_id: str) -> None:
        """Drop the row, returning the user to the default."""
        row = await self._session.get(AssistantModelPrefModel, user_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.commit()
