from pathlib import Path

import pytest
from pydantic import ValidationError
from team_context_service import load_content_pack

EXTENSION_PATH = Path(__file__).parents[3] / "extension"


def test_loads_revision_pinned_knowledge() -> None:
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision")

    assert pack.manifest.id == "agent-extension-kit-sample"
    assert pack.chunks[0].citation.revision == "fixture-revision"
    assert {chunk.citation.path for chunk in pack.chunks} == {
        "knowledge/architecture/idempotency.md",
        "knowledge/domain/glossary.md",
        "knowledge/incidents/unstable-key.md",
    }


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
