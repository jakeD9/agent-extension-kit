import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from team_agent_skills.distribution import (
    SkillClient,
    SkillDistributionError,
    SkillInstaller,
    harness_cache_directory,
    harness_skill_directory,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="team-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    skills = commands.add_parser("skills", help="Discover and install canonical Git team skills")
    skill_commands = skills.add_subparsers(dest="skills_command", required=True)

    list_parser = skill_commands.add_parser("list", help="List authorized skills")
    list_parser.add_argument("--project", required=True)
    list_parser.add_argument("--json", action="store_true", dest="json_output")

    pull = skill_commands.add_parser("pull", help="Pull verified skill packages")
    pull.add_argument("names", nargs="*")
    pull.add_argument("--all", action="store_true", dest="all_skills")
    pull.add_argument("--lock", type=Path)
    pull.add_argument("--project")
    pull.add_argument("--dest", type=Path)
    pull.add_argument("--project-root", type=Path)
    pull.add_argument("--revision")
    pull.add_argument("--target", choices=["generic", "codex", "claude"], default="generic")
    pull.add_argument("--frozen", action="store_true")
    pull.add_argument("--non-interactive", action="store_true")
    pull.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _settings(environ: Mapping[str, str]) -> tuple[str, str]:
    url = environ.get("TEAM_AGENT_CONTEXT_URL", "http://127.0.0.1:3000")
    token = environ.get("TEAM_AGENT_TOKEN", "")
    if not token:
        raise SkillDistributionError("TEAM_AGENT_TOKEN is required")
    return url, token


def run(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    args = _parser().parse_args(argv)
    environment = environ if environ is not None else os.environ
    try:
        url, token = _settings(environment)
        with SkillClient(url, token) as client:
            if args.skills_command == "list":
                items = client.list_skills(args.project)
                if args.json_output:
                    print(
                        json.dumps(
                            {"items": [item.model_dump(mode="json") for item in items]},
                            sort_keys=True,
                        ),
                        file=output,
                    )
                else:
                    for item in items:
                        print(f"{item.name}\t{item.version}\t{item.description}", file=output)
                return 0

            modes = int(bool(args.names)) + int(args.all_skills) + int(args.lock is not None)
            if modes != 1:
                raise SkillDistributionError(
                    "Exactly one of skill names, --all, or --lock is required"
                )
            if args.target == "generic":
                if args.project_root is not None:
                    raise SkillDistributionError(
                        "--project-root is only valid for codex and claude targets"
                    )
                destination = args.dest or Path(".team-agent/skills")
                cache = destination.parent / "cache"
            else:
                if args.project_root is None:
                    raise SkillDistributionError(
                        "--project-root is required for codex and claude targets"
                    )
                if args.dest is not None:
                    raise SkillDistributionError(
                        "--dest cannot be combined with codex or claude targets"
                    )
                destination = harness_skill_directory(args.project_root, args.target)
                cache = harness_cache_directory(args.project_root)
            result = SkillInstaller(
                destination,
                cache,
                project_root=args.project_root if args.target != "generic" else None,
            ).pull(
                client,
                project=args.project,
                names=args.names or None,
                all_skills=args.all_skills,
                lock_path=args.lock,
                revision=args.revision,
                target=args.target,
                frozen=args.frozen,
            )
            if args.json_output:
                print(result.model_dump_json(), file=output)
            else:
                installed = ", ".join(result.installed) if result.installed else "no skills"
                print(f"Installed {installed} into {result.destination}", file=output)
            return 0
    except SkillDistributionError as error:
        if getattr(args, "json_output", False):
            print(
                json.dumps({"error": {"code": "skill_pull_failed", "message": str(error)}}),
                file=errors,
            )
        else:
            print(f"team-agent: {error}", file=errors)
        return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
