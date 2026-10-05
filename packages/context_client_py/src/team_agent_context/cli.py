import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Never, TextIO

import httpx
from pydantic import BaseModel, ValidationError
from team_agent_contracts import (
    MemoryExpireRequest,
    SharedMemoryCreateRequest,
    SharedMemoryUpdateRequest,
)

from team_agent_context.client import ContextClient, ContextClientError

_TEST_TRANSPORT: httpx.AsyncBaseTransport | None = None


class _ArgumentError(ValueError):
    pass


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise _ArgumentError(message)


def _parser() -> _JsonArgumentParser:
    parser = _JsonArgumentParser(
        prog="team-context", description="Query authorized team context as JSON"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    search = commands.add_parser("search", help="Search canonical Git team knowledge")
    search.add_argument("query")
    search.add_argument("--project", required=True)
    search.add_argument("--limit", type=int, default=8)

    skill = commands.add_parser("skill", help="Discover canonical Git team skills")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    skill_list = skill_commands.add_parser("list", help="List authorized skill metadata")
    skill_list.add_argument("--project", required=True)
    skill_list.add_argument("--limit", type=int, default=50)
    skill_list.add_argument("--cursor")

    skill_get = skill_commands.add_parser("get", help="Load an authorized skill body")
    skill_get.add_argument("name")
    skill_get.add_argument("--project", required=True)
    skill_get.add_argument("--revision")

    memory = commands.add_parser("memory", help="Search and contribute shared team memory")
    memory_commands = memory.add_subparsers(dest="memory_command", required=True)
    memory_search = memory_commands.add_parser("search", help="Search supplemental team memory")
    memory_search.add_argument("query")
    memory_search.add_argument("--project", required=True)
    memory_search.add_argument("--limit", type=int, default=20)
    memory_search.add_argument("--cursor")

    memory_create = memory_commands.add_parser("create", help="Create sourced supplemental memory")
    memory_create.add_argument("--memory-json", required=True)
    memory_create.add_argument("--idempotency-key", required=True)

    memory_update = memory_commands.add_parser("update", help="Update current supplemental memory")
    memory_update.add_argument("memory_id")
    memory_update.add_argument("--memory-json", required=True)
    memory_update.add_argument("--idempotency-key", required=True)

    memory_expire = memory_commands.add_parser("expire", help="Expire supplemental memory")
    memory_expire.add_argument("memory_id")
    memory_expire.add_argument("--expected-revision", type=int, required=True)
    memory_expire.add_argument("--reason", required=True)
    memory_expire.add_argument("--idempotency-key", required=True)

    memory_audit = memory_commands.add_parser("audit", help="Inspect immutable memory audit")
    memory_audit.add_argument("memory_id")
    memory_audit.add_argument("--project", required=True)
    memory_audit.add_argument("--limit", type=int, default=50)
    memory_audit.add_argument("--cursor")
    return parser


async def _execute(args: argparse.Namespace, environment: Mapping[str, str]) -> BaseModel:
    url = environment.get("TEAM_AGENT_CONTEXT_URL", "http://127.0.0.1:3000")
    token = environment.get("TEAM_AGENT_TOKEN", "")
    async with ContextClient(url, token, transport=_TEST_TRANSPORT) as client:
        if args.command == "search":
            return await client.search(args.query, args.project, limit=args.limit)
        if args.command == "memory":
            if args.memory_command == "search":
                return await client.search_memories(
                    args.query, args.project, limit=args.limit, cursor=args.cursor
                )
            if args.memory_command == "create":
                memory = SharedMemoryCreateRequest.model_validate_json(args.memory_json)
                return await client.create_memory(memory, idempotency_key=args.idempotency_key)
            if args.memory_command == "update":
                update = SharedMemoryUpdateRequest.model_validate_json(args.memory_json)
                return await client.update_memory(
                    args.memory_id,
                    update,
                    idempotency_key=args.idempotency_key,
                )
            if args.memory_command == "expire":
                decision = MemoryExpireRequest(
                    expected_revision=args.expected_revision, reason=args.reason
                )
                return await client.expire_memory(
                    args.memory_id, decision, idempotency_key=args.idempotency_key
                )
            return await client.list_memory_audit(
                args.memory_id,
                args.project,
                limit=args.limit,
                cursor=args.cursor,
            )
        if args.skill_command == "list":
            return await client.list_skills(args.project, limit=args.limit, cursor=args.cursor)
        return await client.get_skill(args.name, args.project, revision=args.revision)


def run(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        args = _parser().parse_args(argv)
        result = asyncio.run(_execute(args, environ if environ is not None else os.environ))
        print(result.model_dump_json(exclude_none=True), file=output)
        return 0
    except (_ArgumentError, ContextClientError, ValidationError) as error:
        if isinstance(error, _ArgumentError):
            code = "invalid_arguments"
            message = "The command arguments are invalid; use --help for supported options."
        elif isinstance(error, ContextClientError):
            code = error.code
            message = str(error)
        else:
            code = "invalid_request"
            message = "The context request is invalid; check its project, cursor, and limits."
        print(json.dumps({"error": {"code": code, "message": message}}), file=errors)
        return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
