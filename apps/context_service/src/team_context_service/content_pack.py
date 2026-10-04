from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from team_agent_contracts import Authority, Citation
from team_context_core import KnowledgeChunk


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ProjectManifest(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str = Field(min_length=1)
    access_groups: list[str]


class KnowledgeManifest(BaseModel):
    roots: list[str]


class SkillsManifest(BaseModel):
    root: str | None = None
    roots: list[str] | None = None


class ExtensionManifest(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="allow")

    schema_version: str
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    kit_compatibility: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    projects: list[ProjectManifest]
    knowledge: KnowledgeManifest
    skills: SkillsManifest


class KnowledgeMetadata(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    title: str
    project: str
    access_groups: list[str]
    authority: Authority


class ContentPack(BaseModel):
    manifest: ExtensionManifest
    chunks: list[KnowledgeChunk]


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"Markdown file has no YAML frontmatter: {path}")
    try:
        raw_metadata, body = text[4:].split("\n---\n", maxsplit=1)
    except ValueError as error:
        raise ValueError(f"Markdown frontmatter is not terminated: {path}") from error
    metadata = yaml.safe_load(raw_metadata)
    if not isinstance(metadata, dict):
        raise ValueError(f"Markdown frontmatter must be an object: {path}")
    return metadata, body.strip()


def _safe_child(root: Path, relative: str) -> Path:
    child = (root / relative).resolve()
    if not child.is_relative_to(root):
        raise ValueError(f"Content root escapes the extension directory: {relative}")
    return child


def load_content_pack(root: Path, revision: str) -> ContentPack:
    resolved_root = root.resolve()
    manifest = ExtensionManifest.model_validate(_read_yaml(resolved_root / "manifest.yaml"))
    if manifest.schema_version != "1":
        raise ValueError(f"Unsupported extension schema version: {manifest.schema_version}")

    chunks: list[KnowledgeChunk] = []
    for configured_root in manifest.knowledge.roots:
        knowledge_root = _safe_child(resolved_root, configured_root)
        for path in sorted(knowledge_root.rglob("*.md")):
            metadata_raw, body = _read_markdown(path)
            metadata = KnowledgeMetadata.model_validate(metadata_raw)
            relative_path = path.relative_to(resolved_root).as_posix()
            heading = next(
                (line.removeprefix("# ") for line in body.splitlines() if line.startswith("# ")),
                None,
            )
            chunks.append(
                KnowledgeChunk(
                    id=f"{manifest.id}:{relative_path}",
                    project=metadata.project,
                    access_groups=metadata.access_groups,
                    authority=metadata.authority,
                    title=metadata.title,
                    body=body,
                    citation=Citation(
                        repository=manifest.repository,
                        path=relative_path,
                        revision=revision,
                        heading=heading,
                    ),
                )
            )
    return ContentPack(manifest=manifest, chunks=chunks)


__all__ = ["ContentPack", "ExtensionManifest", "load_content_pack"]
