import json
from collections.abc import Mapping
from typing import Protocol

from pydantic import TypeAdapter
from team_agent_contracts import Principal


class Authenticator(Protocol):
    async def authenticate(self, authorization: str | None) -> Principal | None: ...


class StaticBearerAuthenticator:
    """Development-only bearer authentication adapter."""

    def __init__(self, principals_by_token: Mapping[str, Principal]) -> None:
        self._principals_by_token = dict(principals_by_token)

    async def authenticate(self, authorization: str | None) -> Principal | None:
        if authorization is None or not authorization.startswith("Bearer "):
            return None
        return self._principals_by_token.get(authorization.removeprefix("Bearer "))

    @classmethod
    def from_json(cls, value: str) -> "StaticBearerAuthenticator":
        raw = json.loads(value)
        principals = TypeAdapter(dict[str, Principal]).validate_python(raw)
        return cls(principals)


__all__ = ["Authenticator", "StaticBearerAuthenticator"]
