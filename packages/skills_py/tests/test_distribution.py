import base64
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import team_agent_skills.cli as skills_cli
import team_agent_skills.distribution as distribution
from team_agent_contracts import (
    Citation,
    ImmutableSkillPackageManifest,
    SkillFileManifest,
    SkillLock,
    SkillLockPackage,
)
from team_agent_skills import (
    SkillClient,
    SkillDistributionError,
    SkillInstaller,
    harness_cache_directory,
    harness_skill_directory,
)
from team_agent_skills.cli import run


def _manifest(
    name: str = "diagnose-and-fix", content: bytes = b"instructions"
) -> ImmutableSkillPackageManifest:
    file = SkillFileManifest(
        path="SKILL.md", sha256=hashlib.sha256(content).hexdigest(), size=len(content)
    )
    citation = Citation(repository="org/context", path=f"skills/{name}/SKILL.md", revision="rev-1")
    identity = json.dumps(
        {
            "repository": citation.repository,
            "source_revision": "rev-1",
            "name": name,
            "version": "1.0.0",
            "files": [file.model_dump(mode="json")],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return ImmutableSkillPackageManifest(
        package_id=f"sha256:{hashlib.sha256(identity).hexdigest()}",
        name=name,
        description=f"{name} skill",
        version="1.0.0",
        source_revision="rev-1",
        files=[file],
        citation=citation,
    )


def _replace_inventory(
    manifest: ImmutableSkillPackageManifest,
    files: list[SkillFileManifest],
    *,
    resources: list[str] | None = None,
) -> ImmutableSkillPackageManifest:
    identity = json.dumps(
        {
            "repository": manifest.citation.repository,
            "source_revision": manifest.source_revision,
            "name": manifest.name,
            "version": manifest.version,
            "files": [item.model_dump(mode="json") for item in files],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return manifest.model_copy(
        update={
            "package_id": f"sha256:{hashlib.sha256(identity).hexdigest()}",
            "files": files,
            "resources": resources if resources is not None else manifest.resources,
        }
    )


def _handler(
    manifests: list[ImmutableSkillPackageManifest], contents: dict[str, bytes] | None = None
) -> httpx.MockTransport:
    by_id = {manifest.package_id: manifest for manifest in manifests}

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        if request.url.path == "/v1/skills":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "name": item.name,
                            "description": item.description,
                            "version": item.version,
                            "projects": ["platform"],
                            "access_groups": ["engineering"],
                            "allowed_tools": [],
                            "citation": item.citation.model_dump(mode="json"),
                        }
                        for item in manifests
                    ],
                    "request_id": "request-1",
                },
            )
        if request.url.path == "/v1/skills:resolve":
            body = json.loads(request.content)
            selected = (
                manifests
                if body.get("all")
                else [
                    by_id[next(item.package_id for item in manifests if item.name == name)]
                    for name in body["names"]
                ]
            )
            return httpx.Response(
                200,
                json={
                    "manifest": {
                        "schema_version": "1",
                        "catalog_revision": "rev-1",
                        "project": "platform",
                        "selected_names": [item.name for item in selected],
                        "packages": [item.model_dump(mode="json") for item in selected],
                    },
                    "request_id": "request-2",
                },
            )
        package_id = request.url.path.removeprefix("/v1/skill-packages/")
        manifest = by_id[package_id]
        content = (contents or {}).get(manifest.name, b"instructions")
        return httpx.Response(
            200,
            json={
                "manifest": manifest.model_dump(mode="json"),
                "files": [
                    {
                        **manifest.files[0].model_dump(mode="json"),
                        "content_base64": base64.b64encode(content).decode(),
                    }
                ],
                "request_id": "request-3",
            },
        )

    return httpx.MockTransport(handle)


def _client(
    manifests: list[ImmutableSkillPackageManifest], contents: dict[str, bytes] | None = None
) -> SkillClient:
    return SkillClient(
        "https://context.example",
        "secret",
        transport=_handler(manifests, contents),
    )


def _mutating_client(
    manifest: ImmutableSkillPackageManifest,
    mutate: Callable[[dict[str, Any]], None],
) -> SkillClient:
    base = _handler([manifest])

    def handle(request: httpx.Request) -> httpx.Response:
        response = base.handle_request(request)
        if request.url.path.startswith("/v1/skill-packages/"):
            payload: dict[str, Any] = json.loads(response.content)
            mutate(payload)
            return httpx.Response(200, json=payload)
        return response

    return SkillClient("https://context.example", "secret", transport=httpx.MockTransport(handle))


def test_targeted_pull_installs_verified_package_and_writes_lock(tmp_path: Path) -> None:
    manifest = _manifest()
    installer = SkillInstaller(tmp_path / "skills", tmp_path / "cache")

    result = installer.pull(_client([manifest]), project="platform", names=[manifest.name])

    assert (tmp_path / "skills" / manifest.name / "SKILL.md").read_bytes() == b"instructions"
    lock = SkillLock.model_validate_json((tmp_path / "skills" / "skills.lock.json").read_text())
    assert lock.project == "platform"
    assert [package.package_id for package in lock.packages] == [manifest.package_id]
    assert result.installed == [manifest.name]


def test_client_lists_authorized_skills() -> None:
    manifests = [_manifest("first"), _manifest("second")]

    items = _client(manifests).list_skills("platform")

    assert [item.name for item in items] == ["first", "second"]


def test_all_pull_installs_every_resolved_package(tmp_path: Path) -> None:
    manifests = [_manifest("first"), _manifest("second")]

    result = SkillInstaller(tmp_path / "skills", tmp_path / "cache").pull(
        _client(manifests), project="platform", all_skills=True
    )

    assert result.installed == ["first", "second"]
    assert (tmp_path / "skills" / "first" / "SKILL.md").exists()
    assert (tmp_path / "skills" / "second" / "SKILL.md").exists()


def test_all_pull_allows_an_empty_authorized_catalog(tmp_path: Path) -> None:
    destination = tmp_path / "skills"

    result = SkillInstaller(destination, tmp_path / "cache").pull(
        _client([]), project="platform", all_skills=True
    )

    assert result.installed == []
    assert (
        SkillLock.model_validate_json(destination.joinpath("skills.lock.json").read_text()).packages
        == []
    )


def test_skill_lock_is_snake_case_and_rejects_unknown_schema() -> None:
    manifest = _manifest()
    lock = SkillLock(
        catalog_revision="rev-1",
        project="platform",
        packages=[SkillLockPackage(**manifest.model_dump())],
    )

    assert "catalog_revision" in lock.model_dump_json()
    invalid = lock.model_dump(mode="json") | {"schema_version": "2"}
    with pytest.raises(ValueError):
        SkillLock.model_validate(invalid)


def test_pull_rejects_unsafe_package_paths_before_install(tmp_path: Path) -> None:
    manifest = _manifest().model_copy(
        update={
            "files": [
                _manifest().files[0],
                SkillFileManifest(
                    path="../escape",
                    sha256=hashlib.sha256(b"instructions").hexdigest(),
                    size=len(b"instructions"),
                ),
            ]
        }
    )

    with pytest.raises(SkillDistributionError, match="unsafe path"):
        SkillInstaller(tmp_path / "skills", tmp_path / "cache").pull(
            _client([manifest]), project="platform", names=[manifest.name]
        )
    assert not (tmp_path / "escape").exists()


def test_pull_rejects_package_id_not_derived_from_manifest(tmp_path: Path) -> None:
    manifest = _manifest().model_copy(update={"package_id": f"sha256:{'0' * 64}"})

    with pytest.raises(SkillDistributionError, match="package ID"):
        SkillInstaller(tmp_path / "skills", tmp_path / "cache").pull(
            _client([manifest]), project="platform", names=[manifest.name]
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload["files"][0].update(content_base64="%%%"), "base64"),
        (lambda payload: payload["files"][0].update(size=999), "metadata"),
        (
            lambda payload: payload["files"][0].update(content_base64="YQ=="),
            "Size mismatch",
        ),
    ],
)
def test_pull_rejects_invalid_file_encoding_and_metadata(
    tmp_path: Path, mutation: Callable[[dict[str, Any]], None], message: str
) -> None:
    manifest = _manifest()

    with pytest.raises(SkillDistributionError, match=message):
        SkillInstaller(tmp_path / "skills", tmp_path / "cache").pull(
            _mutating_client(manifest, mutation),
            project="platform",
            names=[manifest.name],
        )


@pytest.mark.parametrize(
    ("resources", "message"),
    [
        (["../escape"], "unsafe path"),
        (["missing.md"], "Undeclared resource"),
        (["SKILL.md", "SKILL.md"], "Duplicate resources"),
    ],
)
def test_pull_rejects_invalid_resources(tmp_path: Path, resources: list[str], message: str) -> None:
    manifest = _manifest().model_copy(update={"resources": resources})

    with pytest.raises(SkillDistributionError, match=message):
        SkillInstaller(tmp_path / "skills", tmp_path / "cache").pull(
            _client([manifest]), project="platform", names=[manifest.name]
        )


def test_pull_rejects_path_prefix_collision(tmp_path: Path) -> None:
    base = _manifest()
    colliding = SkillFileManifest(
        path="SKILL.md/child",
        sha256=hashlib.sha256(b"child").hexdigest(),
        size=5,
    )
    manifest = _replace_inventory(base, [base.files[0], colliding])

    with pytest.raises(SkillDistributionError, match="collide"):
        SkillInstaller(tmp_path / "skills", tmp_path / "cache").pull(
            _client([manifest]), project="platform", names=[manifest.name]
        )


def test_invalid_download_does_not_replace_existing_package(tmp_path: Path) -> None:
    manifest = _manifest()
    installer = SkillInstaller(tmp_path / "skills", tmp_path / "cache")
    installer.pull(_client([manifest]), project="platform", names=[manifest.name])
    existing = tmp_path / "skills" / manifest.name / "SKILL.md"

    def corrupt(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/skills:resolve":
            return _handler([manifest]).handle_request(request)
        body = {
            "manifest": manifest.model_dump(mode="json"),
            "files": [{**manifest.files[0].model_dump(), "content_base64": "aW5zdHJ1Y3Rpb254"}],
            "request_id": "request-4",
        }
        return httpx.Response(200, json=body)

    client = SkillClient(
        "https://context.example", "secret", transport=httpx.MockTransport(corrupt)
    )
    with pytest.raises(SkillDistributionError, match=r"[Hh]ash"):
        SkillInstaller(tmp_path / "skills", tmp_path / "corrupt-cache").pull(
            client, project="platform", names=[manifest.name]
        )
    assert existing.read_bytes() == b"instructions"


def test_frozen_pull_uses_verified_cache_without_resolving(tmp_path: Path) -> None:
    manifest = _manifest()
    installer = SkillInstaller(tmp_path / "first", tmp_path / "cache")
    installer.pull(_client([manifest]), project="platform", names=[manifest.name])
    lock_path = tmp_path / "first" / "skills.lock.json"

    def unavailable(request: httpx.Request) -> httpx.Response:
        assert request.url.path != "/v1/skills:resolve"
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    offline = SkillClient(
        "https://context.example", "secret", transport=httpx.MockTransport(unavailable)
    )
    second = SkillInstaller(tmp_path / "second", tmp_path / "cache")
    result = second.pull(offline, project="platform", lock_path=lock_path, frozen=True)

    assert result.installed == [manifest.name]
    assert (tmp_path / "second" / manifest.name / "SKILL.md").read_bytes() == b"instructions"


def test_corrupt_cache_falls_back_to_exact_service_package(tmp_path: Path) -> None:
    manifest = _manifest()
    cache = tmp_path / "cache"
    SkillInstaller(tmp_path / "first", cache).pull(
        _client([manifest]), project="platform", names=[manifest.name]
    )
    bundle_path = cache / manifest.package_id.removeprefix("sha256:") / "bundle.json"
    bundle_path.write_text("not json")
    requests = 0
    base = _handler([manifest])

    def count(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        if request.url.path.startswith("/v1/skill-packages/"):
            requests += 1
        return base.handle_request(request)

    client = SkillClient("https://context.example", "secret", transport=httpx.MockTransport(count))
    SkillInstaller(tmp_path / "second", cache).pull(
        client, project="platform", names=[manifest.name]
    )

    assert requests == 1


def test_unmanaged_collision_fails_without_modifying_it(tmp_path: Path) -> None:
    manifest = _manifest()
    existing = tmp_path / "skills" / manifest.name
    existing.mkdir(parents=True)
    (existing / "mine.txt").write_text("mine")

    with pytest.raises(SkillDistributionError, match="unmanaged"):
        SkillInstaller(tmp_path / "skills", tmp_path / "cache").pull(
            _client([manifest]), project="platform", names=[manifest.name]
        )
    assert (existing / "mine.txt").read_text() == "mine"


def test_unmanaged_preexisting_lockfile_is_not_overwritten(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    destination.mkdir()
    lock = destination / "skills.lock.json"
    lock.write_text('{"user":"owned"}')

    with pytest.raises(SkillDistributionError, match="unmanaged metadata"):
        SkillInstaller(destination, tmp_path / "cache").pull(
            _client([_manifest()]), project="platform", names=[_manifest().name]
        )
    assert lock.read_text() == '{"user":"owned"}'


def test_unmanaged_preexisting_ownership_is_not_overwritten(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    destination.mkdir()
    ownership = destination / ".team-agent-ownership.json"
    ownership.write_text('{"user":"owned"}')

    with pytest.raises(SkillDistributionError, match="unmanaged metadata"):
        SkillInstaller(destination, tmp_path / "cache").pull(
            _client([_manifest()]), project="platform", names=[_manifest().name]
        )
    assert ownership.read_text() == '{"user":"owned"}'


def test_symlink_destination_and_package_targets_are_rejected(tmp_path: Path) -> None:
    manifest = _manifest()
    real = tmp_path / "real"
    real.mkdir()
    destination_link = tmp_path / "destination-link"
    destination_link.symlink_to(real, target_is_directory=True)
    with pytest.raises(SkillDistributionError, match="symbolic link"):
        SkillInstaller(destination_link, tmp_path / "cache").pull(
            _client([manifest]), project="platform", names=[manifest.name]
        )

    destination = tmp_path / "skills"
    destination.mkdir()
    (destination / manifest.name).symlink_to(real, target_is_directory=True)
    with pytest.raises(SkillDistributionError, match="symbolic-link package"):
        SkillInstaller(destination, tmp_path / "cache").pull(
            _client([manifest]), project="platform", names=[manifest.name]
        )


def test_targeted_pull_preserves_unselected_managed_and_unmanaged_files(tmp_path: Path) -> None:
    first = _manifest("first")
    second = _manifest("second")
    installer = SkillInstaller(tmp_path / "skills", tmp_path / "cache")
    installer.pull(_client([first, second]), project="platform", names=[first.name])
    unmanaged = tmp_path / "skills" / "notes.txt"
    unmanaged.write_text("keep")

    installer.pull(_client([first, second]), project="platform", names=[second.name])

    assert (tmp_path / "skills" / first.name / "SKILL.md").exists()
    assert (tmp_path / "skills" / second.name / "SKILL.md").exists()
    assert unmanaged.read_text() == "keep"


@pytest.mark.parametrize("change", ["modify", "unexpected"])
def test_replacement_rejects_changes_inside_managed_package(tmp_path: Path, change: str) -> None:
    manifest = _manifest()
    installer = SkillInstaller(tmp_path / "skills", tmp_path / "cache")
    installer.pull(_client([manifest]), project="platform", names=[manifest.name])
    package = tmp_path / "skills" / manifest.name
    if change == "modify":
        (package / "SKILL.md").write_text("user edit")
    else:
        (package / "notes.txt").write_text("user file")

    with pytest.raises(SkillDistributionError, match="managed package"):
        installer.pull(_client([manifest]), project="platform", names=[manifest.name])
    assert (package / ("SKILL.md" if change == "modify" else "notes.txt")).exists()


def test_mid_package_commit_failure_rolls_back_packages_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifests = [_manifest("first"), _manifest("second")]
    destination = tmp_path / "skills"
    installer = SkillInstaller(destination, tmp_path / "cache")
    installer.pull(_client(manifests), project="platform", all_skills=True)
    old_lock = destination.joinpath("skills.lock.json").read_bytes()
    old_ownership = destination.joinpath(".team-agent-ownership.json").read_bytes()
    real_replace = os.replace
    failed = False

    def fail_second_package(source: Any, target: Any) -> None:
        nonlocal failed
        source_path = Path(source)
        target_path = Path(target)
        if (
            not failed
            and target_path == destination / "second"
            and source_path.parent.name == "new"
        ):
            failed = True
            raise OSError("simulated package commit interruption")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_second_package)
    with pytest.raises(SkillDistributionError, match="filesystem operation failed"):
        installer.pull(_client(manifests), project="platform", all_skills=True)

    assert destination.joinpath("first", "SKILL.md").read_bytes() == b"instructions"
    assert destination.joinpath("second", "SKILL.md").read_bytes() == b"instructions"
    assert destination.joinpath("skills.lock.json").read_bytes() == old_lock
    assert destination.joinpath(".team-agent-ownership.json").read_bytes() == old_ownership
    assert not destination.joinpath(".team-agent-transaction").exists()


def test_next_pull_recovers_mid_metadata_interruption_then_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _manifest(content=b"instructions")
    new_content = b"replacement!"
    new = _manifest(content=new_content)
    destination = tmp_path / "skills"
    installer = SkillInstaller(destination, tmp_path / "cache")
    installer.pull(
        _client([old], {old.name: b"instructions"}),
        project="platform",
        names=[old.name],
    )
    old_lock = destination.joinpath("skills.lock.json").read_bytes()
    real_replace = os.replace
    failed = False

    def fail_ownership_metadata(source: Any, target: Any) -> None:
        nonlocal failed
        source_path = Path(source)
        target_path = Path(target)
        if (
            not failed
            and target_path == destination / ".team-agent-ownership.json"
            and source_path.parent == destination
        ):
            failed = True
            raise OSError("simulated metadata interruption")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_ownership_metadata)

    def simulate_process_death(_root: Path, _transaction: object) -> None:
        raise RuntimeError("simulated process death before rollback")

    monkeypatch.setattr(installer, "_rollback_transaction", simulate_process_death)
    with pytest.raises(RuntimeError, match="process death"):
        installer.pull(
            _client([new], {new.name: new_content}),
            project="platform",
            names=[new.name],
        )
    assert destination.joinpath(".team-agent-transaction").is_dir()

    monkeypatch.undo()
    recovery_failed = False
    real_replace = os.replace

    def interrupt_recovery_after_lock(source: Any, target: Any) -> None:
        nonlocal recovery_failed
        source_path = Path(source)
        target_path = Path(target)
        if (
            not recovery_failed
            and target_path == destination / ".team-agent-ownership.json"
            and source_path.parent == destination
        ):
            recovery_failed = True
            raise OSError("simulated recovery interruption after lock restore")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", interrupt_recovery_after_lock)
    with pytest.raises(SkillDistributionError, match="recover skill metadata"):
        SkillInstaller(destination, tmp_path / "cache").pull(
            _client([new], {new.name: new_content}),
            project="platform",
            names=[new.name],
        )
    transaction = destination / ".team-agent-transaction"
    assert transaction.is_dir()
    assert transaction.joinpath("metadata", "skills.lock.json").is_file()
    assert destination.joinpath("skills.lock.json").read_bytes() == old_lock

    monkeypatch.undo()
    SkillInstaller(destination, tmp_path / "cache").pull(
        _client([new], {new.name: new_content}),
        project="platform",
        names=[new.name],
    )

    assert destination.joinpath(new.name, "SKILL.md").read_bytes() == new_content
    assert not destination.joinpath(".team-agent-transaction").exists()


def test_frozen_pull_fails_when_exact_package_is_not_cached_or_served(tmp_path: Path) -> None:
    package = SkillLockPackage(**_manifest().model_dump())
    lock = SkillLock(catalog_revision="rev-1", project="platform", packages=[package])
    lock_path = tmp_path / "input.lock.json"
    lock_path.write_text(lock.model_dump_json())

    def unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "skill_package_not_found"}})

    client = SkillClient(
        "https://context.example", "secret", transport=httpx.MockTransport(unavailable)
    )
    with pytest.raises(SkillDistributionError, match="unavailable"):
        SkillInstaller(tmp_path / "skills", tmp_path / "cache").pull(
            client, project="platform", lock_path=lock_path, frozen=True
        )


def test_lock_pull_derives_project_when_not_explicit(tmp_path: Path) -> None:
    manifest = _manifest()
    first = SkillInstaller(tmp_path / "first", tmp_path / "cache")
    first.pull(_client([manifest]), project="platform", names=[manifest.name])

    result = SkillInstaller(tmp_path / "second", tmp_path / "cache").pull(
        _client([manifest]), project=None, lock_path=tmp_path / "first" / "skills.lock.json"
    )

    assert result.installed == [manifest.name]

    with pytest.raises(SkillDistributionError, match="project or target"):
        SkillInstaller(tmp_path / "third", tmp_path / "cache").pull(
            _client([manifest]),
            project="different-project",
            lock_path=tmp_path / "first" / "skills.lock.json",
        )


def test_cli_lock_mode_derives_project_and_emits_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest()
    first = SkillInstaller(tmp_path / "first", tmp_path / "cache")
    first.pull(_client([manifest]), project="platform", names=[manifest.name])
    monkeypatch.setattr(skills_cli, "SkillClient", lambda _url, _token: _client([manifest]))

    status = run(
        [
            "skills",
            "pull",
            "--lock",
            str(tmp_path / "first" / "skills.lock.json"),
            "--dest",
            str(tmp_path / "second"),
            "--frozen",
            "--non-interactive",
            "--json",
        ],
        environ={"TEAM_AGENT_TOKEN": "secret"},
    )

    assert status == 0
    assert json.loads(capsys.readouterr().out)["installed"] == [manifest.name]


def test_oversized_lock_is_rejected_before_network_use(tmp_path: Path) -> None:
    lock_path = tmp_path / "oversized.lock.json"
    with lock_path.open("wb") as output:
        output.truncate(distribution.MAX_LOCK_BYTES + 1)

    with pytest.raises(SkillDistributionError, match="oversized"):
        SkillInstaller(tmp_path / "skills", tmp_path / "cache").pull(
            _client([_manifest()]), project=None, lock_path=lock_path
        )


def test_skill_list_rejects_cursor_cycle() -> None:
    def cyclic(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/skills"
        return httpx.Response(
            200,
            json={
                "items": [],
                "next_cursor": "same-cursor",
                "request_id": "request-cycle",
            },
        )

    client = SkillClient("https://context.example", "secret", transport=httpx.MockTransport(cyclic))
    with pytest.raises(SkillDistributionError, match="cyclic"):
        client.list_skills("platform")


def test_skill_list_rejects_excess_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(distribution, "MAX_LIST_ITEMS", 1)
    client = _client([_manifest("first"), _manifest("second")])

    with pytest.raises(SkillDistributionError, match="item limit"):
        client.list_skills("platform")


def test_client_errors_do_not_disclose_bearer_token() -> None:
    token = "super-secret-token-value"

    def unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": "service_unavailable"}})

    client = SkillClient(
        "https://context.example", token, transport=httpx.MockTransport(unavailable)
    )
    with pytest.raises(SkillDistributionError) as captured:
        client.list_skills("platform")
    assert token not in str(captured.value)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://context.example",
        "https://user:secret@context.example",
        "https://context.example/prefix",
        "https://context.example?tenant=secret",
        "https://context.example#fragment",
    ],
)
def test_skill_client_rejects_unsafe_service_url_before_attaching_bearer(url: str) -> None:
    with pytest.raises(SkillDistributionError, match="root HTTP") as captured:
        SkillClient(url, "secret")

    assert url not in str(captured.value)


def test_cli_rejects_ambiguous_selection_before_connecting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = run(
        [
            "skills",
            "pull",
            "diagnose-and-fix",
            "--all",
            "--project",
            "platform",
            "--dest",
            str(tmp_path),
        ],
        environ={
            "TEAM_AGENT_CONTEXT_URL": "https://context.example",
            "TEAM_AGENT_TOKEN": "super-secret-token-value",
        },
    )

    assert status == 2
    error = capsys.readouterr().err
    assert "Exactly one" in error
    assert "TEAM_AGENT_TOKEN" not in error
    assert "super-secret-token-value" not in error


@pytest.mark.parametrize(
    ("target", "relative"),
    [("codex", ".agents/skills"), ("claude", ".claude/skills")],
)
def test_harness_targets_install_in_project_discovery_layout_and_preserve_lock(
    tmp_path: Path, target: str, relative: str
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    destination = harness_skill_directory(project_root, target)  # type: ignore[arg-type]
    manifest = _manifest()

    result = SkillInstaller(
        destination,
        project_root / ".team-agent/cache",
        project_root=project_root,
    ).pull(
        _client([manifest]),
        project="platform",
        names=[manifest.name],
        target=target,  # type: ignore[arg-type]
    )

    assert destination == project_root / relative
    assert destination.joinpath(manifest.name, "SKILL.md").read_bytes() == b"instructions"
    lock = SkillLock.model_validate_json(destination.joinpath("skills.lock.json").read_text())
    assert lock.target == target
    assert lock.packages[0].package_id == manifest.package_id
    assert lock.packages[0].source_revision == manifest.source_revision
    assert lock.packages[0].citation == manifest.citation
    assert result.destination == str(destination)


@pytest.mark.parametrize("target", ["codex", "claude"])
def test_harness_pull_requires_project_root_through_public_installer_interface(
    tmp_path: Path, target: distribution.SkillTarget
) -> None:
    manifest = _manifest()
    relative = ".agents/skills" if target == "codex" else ".claude/skills"

    with pytest.raises(SkillDistributionError, match="project root"):
        SkillInstaller(tmp_path / relative, tmp_path / ".team-agent/cache").pull(
            _client([manifest]),
            project="platform",
            names=[manifest.name],
            target=target,
        )


@pytest.mark.parametrize("target", ["codex", "claude"])
@pytest.mark.parametrize("mismatch", ["destination", "cache"])
def test_harness_pull_rejects_noncanonical_paths_through_public_installer_interface(
    tmp_path: Path, mismatch: str, target: distribution.SkillTarget
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    destination = harness_skill_directory(project_root, target)
    cache = harness_cache_directory(project_root)
    if mismatch == "destination":
        destination = project_root / "skills"
    else:
        cache = project_root / "cache"

    with pytest.raises(SkillDistributionError, match=r"harness (destination|cache)"):
        SkillInstaller(destination, cache, project_root=project_root).pull(
            _client([_manifest()]),
            project="platform",
            names=["diagnose-and-fix"],
            target=target,
        )


def test_generic_pull_rejects_project_root_through_public_installer_interface(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()

    with pytest.raises(SkillDistributionError, match="only valid for codex and claude"):
        SkillInstaller(
            tmp_path / "skills",
            tmp_path / "cache",
            project_root=project_root,
        ).pull(
            _client([_manifest()]),
            project="platform",
            names=["diagnose-and-fix"],
        )


def test_harness_target_rejects_symlinked_project_discovery_parent(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    project_root.joinpath(".agents").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SkillDistributionError, match="symbolic link"):
        harness_skill_directory(project_root, "codex")


@pytest.mark.parametrize("dangling", [False, True])
def test_harness_cache_rejects_team_agent_symlink_without_writing_outside(
    tmp_path: Path, dangling: bool
) -> None:
    project_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    project_root.mkdir()
    if not dangling:
        outside.mkdir()
    project_root.joinpath(".team-agent").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SkillDistributionError, match="symbolic link"):
        harness_cache_directory(project_root)

    assert not outside.joinpath("cache").exists()


def test_harness_installer_rechecks_cache_path_before_read_or_write(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    destination = harness_skill_directory(project_root, "codex")
    cache = harness_cache_directory(project_root)
    installer = SkillInstaller(destination, cache, project_root=project_root)
    project_root.joinpath(".team-agent").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SkillDistributionError, match="symbolic link"):
        installer.pull(
            _client([_manifest()]),
            project="platform",
            names=["diagnose-and-fix"],
            target="codex",
        )

    assert not outside.joinpath("cache").exists()


def test_harness_cache_rejects_preexisting_package_directory_symlink(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    manifest = _manifest()
    destination = harness_skill_directory(project_root, "codex")
    cache = harness_cache_directory(project_root)
    package_cache = cache / manifest.package_id.removeprefix("sha256:")
    cache.mkdir(parents=True)
    package_cache.symlink_to(outside, target_is_directory=True)
    base = _handler([manifest])
    downloads = 0

    def count_downloads(request: httpx.Request) -> httpx.Response:
        nonlocal downloads
        if request.url.path.startswith("/v1/skill-packages/"):
            downloads += 1
        return base.handle_request(request)

    client = SkillClient(
        "https://context.example",
        "secret",
        transport=httpx.MockTransport(count_downloads),
    )

    with pytest.raises(SkillDistributionError, match="cache package path"):
        SkillInstaller(destination, cache, project_root=project_root).pull(
            client,
            project="platform",
            names=[manifest.name],
            target="codex",
        )

    assert downloads == 0
    assert not outside.joinpath("bundle.json").exists()


def test_harness_cache_rejects_symlinked_bundle_without_downloading(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    manifest = _manifest()
    destination = harness_skill_directory(project_root, "codex")
    cache = harness_cache_directory(project_root)
    package_cache = cache / manifest.package_id.removeprefix("sha256:")
    package_cache.mkdir(parents=True)
    outside_bundle = outside / "bundle.json"
    outside_bundle.write_text("outside sentinel")
    package_cache.joinpath("bundle.json").symlink_to(outside_bundle)
    base = _handler([manifest])
    downloads = 0

    def count_downloads(request: httpx.Request) -> httpx.Response:
        nonlocal downloads
        if request.url.path.startswith("/v1/skill-packages/"):
            downloads += 1
        return base.handle_request(request)

    client = SkillClient(
        "https://context.example",
        "secret",
        transport=httpx.MockTransport(count_downloads),
    )

    with pytest.raises(SkillDistributionError, match="cache bundle path"):
        SkillInstaller(destination, cache, project_root=project_root).pull(
            client,
            project="platform",
            names=[manifest.name],
            target="codex",
        )

    assert downloads == 0
    assert outside_bundle.read_text() == "outside sentinel"


def test_harness_pull_rechecks_destination_after_resolution_before_writes(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    manifest = _manifest()
    base = _handler([manifest])
    destination = harness_skill_directory(project_root, "codex")
    cache = harness_cache_directory(project_root)

    def swap_destination_then_respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/skills:resolve":
            destination.rmdir()
            destination.parent.rmdir()
            destination.parent.symlink_to(outside, target_is_directory=True)
        return base.handle_request(request)

    client = SkillClient(
        "https://context.example",
        "secret",
        transport=httpx.MockTransport(swap_destination_then_respond),
    )

    with pytest.raises(SkillDistributionError, match="symbolic link"):
        SkillInstaller(destination, cache, project_root=project_root).pull(
            client,
            project="platform",
            names=[manifest.name],
            target="codex",
        )

    assert not outside.joinpath("skills").exists()


def test_harness_installer_rechecks_cache_after_download_before_write(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    manifest = _manifest()
    base = _handler([manifest])

    def swap_cache_then_respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/skill-packages/"):
            project_root.joinpath(".team-agent").symlink_to(outside, target_is_directory=True)
        return base.handle_request(request)

    client = SkillClient(
        "https://context.example",
        "secret",
        transport=httpx.MockTransport(swap_cache_then_respond),
    )
    installer = SkillInstaller(
        harness_skill_directory(project_root, "codex"),
        harness_cache_directory(project_root),
        project_root=project_root,
    )

    with pytest.raises(SkillDistributionError, match="symbolic link"):
        installer.pull(
            client,
            project="platform",
            names=[manifest.name],
            target="codex",
        )

    assert not outside.joinpath("cache").exists()


def test_harness_cache_rejects_package_directory_symlink_created_during_download(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    manifest = _manifest()
    destination = harness_skill_directory(project_root, "codex")
    cache = harness_cache_directory(project_root)
    package_cache = cache / manifest.package_id.removeprefix("sha256:")
    base = _handler([manifest])

    def add_package_symlink_then_respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/skill-packages/"):
            cache.mkdir(parents=True)
            package_cache.symlink_to(outside, target_is_directory=True)
        return base.handle_request(request)

    client = SkillClient(
        "https://context.example",
        "secret",
        transport=httpx.MockTransport(add_package_symlink_then_respond),
    )

    with pytest.raises(SkillDistributionError, match="cache package path"):
        SkillInstaller(destination, cache, project_root=project_root).pull(
            client,
            project="platform",
            names=[manifest.name],
            target="codex",
        )

    assert not outside.joinpath("bundle.json").exists()


def test_harness_pull_rechecks_destination_after_download_before_writes(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    manifest = _manifest()
    base = _handler([manifest])
    destination = harness_skill_directory(project_root, "codex")
    cache = harness_cache_directory(project_root)

    def swap_destination_then_respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/skill-packages/"):
            destination.rmdir()
            destination.symlink_to(outside, target_is_directory=True)
        return base.handle_request(request)

    client = SkillClient(
        "https://context.example",
        "secret",
        transport=httpx.MockTransport(swap_destination_then_respond),
    )

    with pytest.raises(SkillDistributionError, match="symbolic link"):
        SkillInstaller(destination, cache, project_root=project_root).pull(
            client,
            project="platform",
            names=[manifest.name],
            target="codex",
        )

    assert not outside.joinpath(manifest.name).exists()


def test_harness_commit_failure_rolls_back_with_shared_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    destination = harness_skill_directory(project_root, "codex")
    cache = harness_cache_directory(project_root)
    original = _manifest(content=b"original")
    replacement = _manifest(content=b"replacement")
    installer = SkillInstaller(destination, cache, project_root=project_root)
    installer.pull(
        _client([original], {original.name: b"original"}),
        project="platform",
        names=[original.name],
        target="codex",
    )
    old_lock = destination.joinpath("skills.lock.json").read_bytes()
    old_ownership = destination.joinpath(".team-agent-ownership.json").read_bytes()
    real_replace = os.replace
    failed = False

    def fail_new_ownership(source: Any, target: Any) -> None:
        nonlocal failed
        source_path = Path(source)
        target_path = Path(target)
        if (
            not failed
            and target_path == destination / ".team-agent-ownership.json"
            and source_path.parent == destination
        ):
            failed = True
            raise OSError("simulated metadata interruption")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_new_ownership)

    with pytest.raises(SkillDistributionError, match="filesystem operation failed"):
        installer.pull(
            _client([replacement], {replacement.name: b"replacement"}),
            project="platform",
            names=[replacement.name],
            target="codex",
        )

    assert destination.joinpath(original.name, "SKILL.md").read_bytes() == b"original"
    assert destination.joinpath("skills.lock.json").read_bytes() == old_lock
    assert destination.joinpath(".team-agent-ownership.json").read_bytes() == old_ownership
    assert not destination.joinpath(".team-agent-transaction").exists()


def test_harness_commit_rejects_managed_tree_changed_at_commit_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    destination = harness_skill_directory(project_root, "codex")
    cache = harness_cache_directory(project_root)
    original = _manifest(content=b"original")
    replacement = _manifest(content=b"replacement")
    installer = SkillInstaller(destination, cache, project_root=project_root)
    installer.pull(
        _client([original], {original.name: b"original"}),
        project="platform",
        names=[original.name],
        target="codex",
    )
    original_commit = SkillInstaller._commit

    def change_tree_then_commit(
        current: SkillInstaller,
        staging: Path,
        lock: SkillLock,
        ownership: Any,
    ) -> None:
        destination.joinpath(original.name, "unmanaged.txt").write_text("keep me")
        original_commit(current, staging, lock, ownership)

    monkeypatch.setattr(SkillInstaller, "_commit", change_tree_then_commit)

    with pytest.raises(SkillDistributionError, match="Modified managed package"):
        installer.pull(
            _client([replacement], {replacement.name: b"replacement"}),
            project="platform",
            names=[replacement.name],
            target="codex",
        )

    assert destination.joinpath(original.name, "unmanaged.txt").read_text() == "keep me"


def test_cli_requires_explicit_project_root_for_harness_targets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = run(
        [
            "skills",
            "pull",
            "diagnose-and-fix",
            "--project",
            "platform",
            "--target",
            "codex",
        ],
        environ={
            "TEAM_AGENT_CONTEXT_URL": "https://context.example",
            "TEAM_AGENT_TOKEN": "secret",
        },
    )

    assert status == 2
    assert "--project-root is required" in capsys.readouterr().err
