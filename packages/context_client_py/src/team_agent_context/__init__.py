"""Local CLI and MCP adapters for the authenticated team context service."""

from team_agent_context.client import ContextClient, ContextClientError

__all__ = ["ContextClient", "ContextClientError"]
