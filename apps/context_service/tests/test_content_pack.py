import io
from pathlib import Path

import pytest
from pydantic import ValidationError
from team_agent_contracts import ImmutableSkillPackageManifest
from team_context_service import load_content_pack

EXTENSION_PATH = Path(__file__).parents[3] / "extension"


def _write_pack(tmp_path: Path, skills: dict[str, str]) -> None:
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "manifest.yaml").write_text(
        """
schemaVersion: "1"
id: test
displayName: Test
kitCompatibility: ">=0.1.0"
repository: test
projects:
  - id: project
knowledge: {roots: [knowledge]}
skills: {root: skills}
""".strip(),
        encoding="utf-8",
    )
    for name, extra in skills.items():
        package = tmp_path / "skills" / name
        package.mkdir(parents=True)
        package.joinpath("SKILL.md").write_text(
            f"""---
name: {name}
description: {name}
version: "1"
projects: [project]
allowedTools: []
{extra}
---
# {name}
""",
            encoding="utf-8",
        )


def test_loads_revision_pinned_knowledge() -> None:
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision")

    assert pack.manifest.id == "agent-extension-kit-sample"
    assert pack.chunks[0].citation.revision == "fixture-revision"
    assert {chunk.citation.path for chunk in pack.chunks} == {
        "knowledge/architecture/idempotency.md",
        "knowledge/domain/glossary.md",
        "knowledge/incidents/unstable-key.md",
    }


def test_loads_revision_pinned_skill_packages() -> None:
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision")

    assert len(pack.skills) == 1
    skill = pack.skills[0]
    assert skill.name == "diagnose-and-fix"
    assert skill.description.startswith("Use when an error")
    assert skill.projects == ["event-ingestion"]
    assert skill.allowed_tools == ["search_team_knowledge", "get_team_document"]
    assert skill.body.startswith("# Diagnose and fix")
    assert skill.citation.model_dump(exclude_none=True) == {
        "repository": "agent-extension-kit-sample",
        "path": "skills/diagnose-and-fix/SKILL.md",
        "revision": "fixture-revision",
    }


def test_inventories_complete_skill_package_with_raw_byte_hashes(tmp_path: Path) -> None:
    (tmp_path / "knowledge").mkdir()
    skill_root = tmp_path / "skills" / "review"
    (skill_root / "references").mkdir(parents=True)
    (skill_root / "references" / "guide.md").write_bytes(b"review guide\r\n")
    (tmp_path / "manifest.yaml").write_text(
        """
schemaVersion: "1"
id: test
displayName: Test
kitCompatibility: ">=0.1.0"
repository: test-repository
projects:
  - id: project
knowledge: {roots: [knowledge]}
skills: {root: skills}
""".strip(),
        encoding="utf-8",
    )
    (skill_root / "SKILL.md").write_text(
        """---
name: review
description: Review changes
version: "1"
projects: [project]
allowedTools: []
resources: [references/guide.md]
---
# Review
""",
        encoding="utf-8",
    )

    skill = load_content_pack(tmp_path, "revision-1").skills[0]

    assert skill.package_manifest is not None
    assert skill.bundle is not None
    assert skill.package_manifest.source_revision == "revision-1"
    assert skill.package_manifest.package_id.startswith("sha256:")
    assert [item.path for item in skill.package_manifest.files] == [
        "SKILL.md",
        "references/guide.md",
    ]
    resource = skill.package_manifest.files[1]
    assert resource.sha256 == "1fc11fefa0ac719c9d03a9142933d9ef12be64b44d520b49e64eab104bb24d08"
    assert resource.size == 14
    assert skill.package_manifest.resources == ["references/guide.md"]
    assert skill.bundle.files[1].content_base64 == "cmV2aWV3IGd1aWRlDQo="
    later = load_content_pack(tmp_path, "revision-2").skills[0]
    assert later.package_manifest is not None
    assert later.package_manifest.package_id != skill.package_manifest.package_id

    unsupported = skill.package_manifest.model_dump(mode="json")
    unsupported["schema_version"] = "2"
    with pytest.raises(ValidationError):
        ImmutableSkillPackageManifest.model_validate(unsupported)


def test_rejects_invalid_knowledge_metadata(tmp_path: Path) -> None:
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "manifest.yaml").write_text(
        """
schemaVersion: "1"
id: test
displayName: Test
kitCompatibility: ">=0.1.0"
repository: test
projects: []
knowledge: {roots: [knowledge]}
skills: {root: skills}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "knowledge" / "bad.md").write_text(
        "---\ntitle: Missing scope\n---\n# Invalid", encoding="utf-8"
    )

    with pytest.raises(ValidationError):
        load_content_pack(tmp_path, "revision")


def test_rejects_skill_scope_not_declared_by_manifest(tmp_path: Path) -> None:
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "skills" / "unsafe").mkdir(parents=True)
    (tmp_path / "manifest.yaml").write_text(
        """
schemaVersion: "1"
id: test
displayName: Test
kitCompatibility: ">=0.1.0"
repository: test
projects:
  - id: declared
knowledge: {roots: [knowledge]}
skills: {root: skills}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "skills" / "unsafe" / "SKILL.md").write_text(
        """---
name: unsafe
description: Invalid scope
version: "1"
projects: [undeclared]
allowedTools: []
---
# Unsafe
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="undeclared project"):
        load_content_pack(tmp_path, "revision")


@pytest.mark.parametrize("resource", ["../secret", "/etc/passwd", "references/missing.md"])
def test_rejects_unsafe_or_missing_explicit_resources(tmp_path: Path, resource: str) -> None:
    _write_pack(tmp_path, {"review": f"resources: [{resource}]"})

    with pytest.raises(ValueError, match=r"skill package|package resource"):
        load_content_pack(tmp_path, "revision")


def test_rejects_skill_dependency_declarations_in_v1(tmp_path: Path) -> None:
    _write_pack(tmp_path, {"review": "dependencies: [policy]"})

    with pytest.raises(ValidationError, match="dependencies"):
        load_content_pack(tmp_path, "revision")


def test_rejects_symlinks_anywhere_in_skill_package(tmp_path: Path) -> None:
    _write_pack(tmp_path, {"review": "resources: []"})
    package = tmp_path / "skills" / "review"
    package.joinpath("linked.md").symlink_to(package / "SKILL.md")

    with pytest.raises(ValueError, match="symlinks"):
        load_content_pack(tmp_path, "revision")


def test_rejects_skill_package_exceeding_file_count_bound(tmp_path: Path) -> None:
    _write_pack(tmp_path, {"review": "resources: []"})
    package = tmp_path / "skills" / "review"
    for index in range(128):
        package.joinpath(f"resource-{index}.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="file count limit"):
        load_content_pack(tmp_path, "revision")


def test_rejects_skill_package_file_exceeding_size_bound(tmp_path: Path) -> None:
    _write_pack(tmp_path, {"review": "resources: []"})
    (tmp_path / "skills" / "review" / "large.bin").write_bytes(b"x" * (2 * 1024 * 1024 + 1))

    with pytest.raises(ValueError, match="file exceeds the size limit"):
        load_content_pack(tmp_path, "revision")


def test_rejects_skill_file_that_grows_after_initial_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pack(tmp_path, {"review": "resources: []"})
    growing = tmp_path / "skills" / "review" / "growing.bin"
    growing.write_bytes(b"x")
    original_open = Path.open

    def open_with_growth(path: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        if path == growing and mode == "rb":
            return io.BytesIO(b"x" * (2 * 1024 * 1024 + 1))
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_with_growth)

    with pytest.raises(ValueError, match="file exceeds the size limit"):
        load_content_pack(tmp_path, "revision")


def test_rejects_skill_package_exceeding_total_size_bound(tmp_path: Path) -> None:
    _write_pack(tmp_path, {"review": "resources: []"})
    package = tmp_path / "skills" / "review"
    for index in range(6):
        package.joinpath(f"large-{index}.bin").write_bytes(b"x" * (2 * 1024 * 1024))

    with pytest.raises(ValueError, match="package size limit"):
        load_content_pack(tmp_path, "revision")


def test_rejects_skill_package_directory_name_mismatch(tmp_path: Path) -> None:
    _write_pack(tmp_path, {"review": "resources: []"})
    skill_path = tmp_path / "skills" / "review" / "SKILL.md"
    skill_path.write_text(skill_path.read_text().replace("name: review", "name: policy"))

    with pytest.raises(ValueError, match="direct package directory with the same name"):
        load_content_pack(tmp_path, "revision")
