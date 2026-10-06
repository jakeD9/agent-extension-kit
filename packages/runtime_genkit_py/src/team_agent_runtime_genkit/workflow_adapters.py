"""Concrete service adapters used by the application-owned workflow."""

from __future__ import annotations

from typing import Any

from team_agent_context import ContextClient
from team_agent_contracts import SharedMemoryCreateRequest


class ContextSupplementalMemoryWriter:
    """Create idempotent supplemental memory through the bounded context API."""

    def __init__(self, base_url: str, token: str, *, transport: Any | None = None) -> None:
        self._base_url = base_url
        self._token = token
        self._transport = transport

    async def create_memory(
        self, request: SharedMemoryCreateRequest, *, idempotency_key: str
    ) -> str:
        async with ContextClient(self._base_url, self._token, transport=self._transport) as client:
            response = await client.create_memory(request, idempotency_key=idempotency_key)
        return response.memory.id


__all__ = ["ContextSupplementalMemoryWriter"]
