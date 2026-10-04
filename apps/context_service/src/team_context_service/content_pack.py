import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from team_agent_contracts import (
    Authority,
    Citation,
    ImmutableSkillPackageManifest,
    SkillFileManifest,
    SkillPackageBundle,
    SkillPackageFile,
)
from team_context_core import KnowledgeChunk, SkillPackage


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


class SkillMetadata(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=1)
    version: str = Field(min_length=1)
    projects: list[str] = Field(min_length=1)
    access_groups: list[str] = Field(min_length=1)
    allowed_tools: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)


class ContentPack(BaseModel):
    manifest: ExtensionManifest
    revision: str = Field(min_length=1)
    chunks: list[KnowledgeChunk]
    skills: list[SkillPackage]


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    return _parse_markdown(path.read_text(encoding="utf-8"), path)


def _parse_markdown(text: str, path: Path) -> tuple[dict[str, Any], str]:
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


MAX_SKILL_FILES = 128
MAX_SKILL_FILE_BYTES = 2 * 1024 * 1024
MAX_SKILL_PACKAGE_BYTES = 10 * 1024 * 1024


def _read_bounded_skill_file(path: Path) -> bytes:
    if path.stat().st_size > MAX_SKILL_FILE_BYTES:
        raise ValueError(f"Skill package file exceeds the size limit: {path.name}")
    content = bytearray()
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            content.extend(chunk)
            if len(content) > MAX_SKILL_FILE_BYTES:
                raise ValueError(f"Skill package file exceeds the size limit: {path.name}")
    return bytes(content)


def _safe_package_path(package_root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if posix.is_absolute() or not posix.parts or ".." in posix.parts or "." in posix.parts:
        raise ValueError(f"Unsafe skill package path: {relative}")
    path = package_root.joinpath(*posix.parts)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(package_root):
        raise ValueError(f"Invalid skill package resource: {relative}")
    return path


def _build_skill_bundle(
    package_root: Path,
    skill_document: bytes,
    metadata: SkillMetadata,
    repository: str,
    citation_path: str,
    revision: str,
) -> tuple[ImmutableSkillPackageManifest, SkillPackageBundle]:
    if len(set(metadata.resources)) != len(metadata.resources):
        raise ValueError(f"Skill {metadata.name} has duplicate resources")
    for resource in metadata.resources:
        _safe_package_path(package_root, resource)

    paths: list[Path] = []
    for path in sorted(package_root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Skill package cannot contain symlinks: {path}")
        if path.is_file():
            paths.append(path)
    if len(paths) > MAX_SKILL_FILES:
        raise ValueError(f"Skill {metadata.name} exceeds the file count limit")

    manifests: list[SkillFileManifest] = []
    bundle_files: list[SkillPackageFile] = []
    total_size = 0
    for path in paths:
        content = (
            skill_document if path.name == "SKILL.md" and path.parent == package_root else None
        )
        if content is None:
            content = _read_bounded_skill_file(path)
        size = len(content)
        total_size += size
        if total_size > MAX_SKILL_PACKAGE_BYTES:
            raise ValueError(f"Skill {metadata.name} exceeds the package size limit")
        relative = path.relative_to(package_root).as_posix()
        digest = hashlib.sha256(content).hexdigest()
        manifests.append(SkillFileManifest(path=relative, sha256=digest, size=size))
        bundle_files.append(
            SkillPackageFile(
                path=relative,
                sha256=digest,
                size=size,
                content_base64=base64.b64encode(content).decode("ascii"),
            )
        )
    if (not manifests or manifests[0].path != "SKILL.md") and not any(
        item.path == "SKILL.md" for item in manifests
    ):
        raise ValueError(f"Skill {metadata.name} has no SKILL.md")

    identity = json.dumps(
        {
            "repository": repository,
            "source_revision": revision,
            "name": metadata.name,
            "version": metadata.version,
            "files": [
                {"path": item.path, "sha256": item.sha256, "size": item.size} for item in manifests
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    package_id = f"sha256:{hashlib.sha256(identity).hexdigest()}"
    manifest = ImmutableSkillPackageManifest(
        package_id=package_id,
        name=metadata.name,
        description=metadata.description,
        version=metadata.version,
        source_revision=revision,
        files=manifests,
        resources=metadata.resources,
        citation=Citation(repository=repository, path=citation_path, revision=revision),
    )
    return manifest, SkillPackageBundle(manifest=manifest, files=bundle_files)


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
            knowledge_metadata = KnowledgeMetadata.model_validate(metadata_raw)
            relative_path = path.relative_to(resolved_root).as_posix()
            heading = next(
                (line.removeprefix("# ") for line in body.splitlines() if line.startswith("# ")),
                None,
            )
            chunks.append(
                KnowledgeChunk(
                    id=f"{manifest.id}:{relative_path}",
                    project=knowledge_metadata.project,
                    access_groups=knowledge_metadata.access_groups,
                    authority=knowledge_metadata.authority,
                    title=knowledge_metadata.title,
                    body=body,
                    citation=Citation(
                        repository=manifest.repository,
                        path=relative_path,
                        revision=revision,
                        heading=heading,
                    ),
                )
            )

    configured_skill_roots = manifest.skills.roots or (
        [manifest.skills.root] if manifest.skills.root else []
    )
    skills: list[SkillPackage] = []
    skill_names: set[str] = set()
    manifest_projects = {project.id: set(project.access_groups) for project in manifest.projects}
    for configured_root in configured_skill_roots:
        skill_root = _safe_child(resolved_root, configured_root)
        for path in sorted(skill_root.rglob("SKILL.md")):
            if path.is_symlink():
                raise ValueError(f"Skill package cannot contain symlinks: {path}")
            skill_document = _read_bounded_skill_file(path)
            metadata_raw, body = _parse_markdown(skill_document.decode("utf-8"), path)
            skill_metadata = SkillMetadata.model_validate(metadata_raw)
            if path.parent.parent != skill_root or path.parent.name != skill_metadata.name:
                raise ValueError(
                    f"Skill {skill_metadata.name} must use a direct package directory "
                    "with the same name"
                )
            for project in skill_metadata.projects:
                if project not in manifest_projects:
                    raise ValueError(
                        f"Skill {skill_metadata.name} references undeclared project: {project}"
                    )
                undeclared_groups = set(skill_metadata.access_groups) - manifest_projects[project]
                if undeclared_groups:
                    raise ValueError(
                        f"Skill {skill_metadata.name} references undeclared access groups for "
                        f"project {project}: {sorted(undeclared_groups)}"
                    )
            if skill_metadata.name in skill_names:
                raise ValueError(f"Duplicate skill name: {skill_metadata.name}")
            skill_names.add(skill_metadata.name)
            relative_path = path.relative_to(resolved_root).as_posix()
            package_manifest, bundle = _build_skill_bundle(
                path.parent,
                skill_document,
                skill_metadata,
                manifest.repository,
                relative_path,
                revision,
            )
            skills.append(
                SkillPackage(
                    id=package_manifest.package_id,
                    name=skill_metadata.name,
                    description=skill_metadata.description,
                    version=skill_metadata.version,
                    projects=skill_metadata.projects,
                    access_groups=skill_metadata.access_groups,
                    allowed_tools=skill_metadata.allowed_tools,
                    body=body,
                    citation=Citation(
                        repository=manifest.repository,
                        path=relative_path,
                        revision=revision,
                    ),
                    package_manifest=package_manifest,
                    bundle=bundle,
                )
            )
    return ContentPack(manifest=manifest, revision=revision, chunks=chunks, skills=skills)


__all__ = ["ContentPack", "ExtensionManifest", "load_content_pack"]
