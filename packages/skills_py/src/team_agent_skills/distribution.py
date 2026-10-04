import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from team_agent_contracts import (
    ImmutableSkillPackageManifest,
    SkillFileManifest,
    SkillListResponse,
    SkillLock,
    SkillLockPackage,
    SkillPackageBundle,
    SkillPackageResponse,
    SkillResolutionManifest,
    SkillResolveResponse,
    SkillSummary,
)

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_LOCK_BYTES = 16 * 1024 * 1024
MAX_OWNERSHIP_BYTES = 1024 * 1024
MAX_LIST_PAGES = 200
MAX_LIST_ITEMS = 10_000
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SkillTarget = Literal["generic", "codex", "claude"]
_HARNESS_SKILL_PATHS: dict[SkillTarget, PurePosixPath] = {
    "generic": PurePosixPath(".team-agent/skills"),
    "codex": PurePosixPath(".agents/skills"),
    "claude": PurePosixPath(".claude/skills"),
}
_HARNESS_CACHE_PATH = PurePosixPath(".team-agent/cache")


class SkillDistributionError(RuntimeError):
    """An actionable, safe-to-display skill distribution failure."""


def _project_scoped_directory(project_root: Path, relative: PurePosixPath) -> Path:
    if project_root.is_symlink() or not project_root.is_dir():
        raise SkillDistributionError(
            "The project root must be an existing non-symbolic-link directory"
        )
    current = project_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SkillDistributionError(
                f"The project harness path must not contain a symbolic link: {current}"
            )
    return current


def harness_skill_directory(project_root: Path, target: SkillTarget) -> Path:
    """Resolve a fixed project-scoped harness discovery directory without following symlinks."""

    if target == "generic":
        raise SkillDistributionError("The generic target uses --dest, not --project-root")
    return _project_scoped_directory(project_root, _HARNESS_SKILL_PATHS[target])


def harness_cache_directory(project_root: Path) -> Path:
    """Resolve the fixed project cache while rejecting existing or dangling symlink ancestors."""

    return _project_scoped_directory(project_root, _HARNESS_CACHE_PATH)


class PullResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_revision: str
    installed: list[str]
    destination: str
    lock_path: str


class _OwnedPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    path: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    files: list[SkillFileManifest] = Field(min_length=1)


class _Ownership(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    packages: dict[str, _OwnedPackage] = Field(default_factory=dict)

    @model_validator(mode="after")
    def matching_names(self) -> "_Ownership":
        if any(
            name != package.path or _NAME.fullmatch(name) is None
            for name, package in self.packages.items()
        ):
            raise ValueError("Ownership package names and paths must match")
        return self


class _TransactionPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    had_existing: bool
    previous_files: list[SkillFileManifest]
    new_files: list[SkillFileManifest] = Field(min_length=1)


class _Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    state: Literal["prepared", "committed"] = "prepared"
    had_lock: bool
    had_ownership: bool
    packages: list[_TransactionPackage]


def _response_bytes(response: httpx.Response, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    data = bytearray()
    for chunk in response.iter_bytes():
        data.extend(chunk)
        if len(data) > limit:
            raise SkillDistributionError("Context service response exceeds the size limit")
    return bytes(data)


class SkillClient:
    """Authenticated context-service client shared by the CLI and future executors."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not token:
            raise SkillDistributionError("Context service URL and bearer token are required")
        try:
            url = httpx.URL(base_url)
        except httpx.InvalidURL as error:
            raise SkillDistributionError(
                "Context service URL must be a root HTTP or HTTPS service URL"
            ) from error
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise SkillDistributionError(
                "Context service URL must be a root HTTP or HTTPS service URL"
            )
        self._client = httpx.Client(
            base_url=str(url).rstrip("/"),
            headers={"authorization": f"Bearer {token}"},
            transport=transport,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SkillClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: object | None = None,
    ) -> object:
        try:
            with self._client.stream(method, path, params=params, json=json_body) as response:
                payload = _response_bytes(response)
                if response.is_error:
                    code = f"HTTP {response.status_code}"
                    try:
                        raw_error = json.loads(payload)
                        code = raw_error.get("error", {}).get("code", code)
                    except (json.JSONDecodeError, AttributeError):
                        pass
                    raise SkillDistributionError(f"Context service request failed: {code}")
        except httpx.HTTPError as error:
            raise SkillDistributionError(f"Context service request failed: {error}") from error
        try:
            return json.loads(payload)
        except json.JSONDecodeError as error:
            raise SkillDistributionError("Context service returned invalid JSON") from error

    def list_skills(self, project: str) -> list[SkillSummary]:
        items: list[SkillSummary] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _page in range(MAX_LIST_PAGES):
            params = {"project": project, "limit": "50"}
            if cursor is not None:
                params["cursor"] = cursor
            try:
                response = SkillListResponse.model_validate(
                    self._json("GET", "/v1/skills", params=params)
                )
            except ValidationError as error:
                raise SkillDistributionError(
                    "Context service returned an invalid skill list"
                ) from error
            items.extend(response.items)
            if len(items) > MAX_LIST_ITEMS:
                raise SkillDistributionError("Skill list exceeds the item limit")
            cursor = response.next_cursor
            if cursor is None:
                return items
            if cursor in seen_cursors:
                raise SkillDistributionError("Context service returned a cyclic skill cursor")
            seen_cursors.add(cursor)
        raise SkillDistributionError("Skill list exceeds the page limit")

    def resolve(
        self,
        project: str,
        *,
        names: Sequence[str] | None = None,
        all_skills: bool = False,
        revision: str | None = None,
    ) -> SkillResolutionManifest:
        body: dict[str, object] = {"project": project, "all": all_skills}
        if names is not None:
            body["names"] = list(names)
        if revision is not None:
            body["revision"] = revision
        try:
            response = SkillResolveResponse.model_validate(
                self._json("POST", "/v1/skills:resolve", json_body=body)
            )
        except ValidationError as error:
            raise SkillDistributionError(
                "Context service returned an invalid resolution"
            ) from error
        return response.manifest

    def download(self, package_id: str, project: str) -> SkillPackageBundle:
        try:
            response = SkillPackageResponse.model_validate(
                self._json(
                    "GET",
                    f"/v1/skill-packages/{package_id}",
                    params={"project": project},
                )
            )
        except ValidationError as error:
            raise SkillDistributionError(
                "Context service returned an invalid skill package"
            ) from error
        return SkillPackageBundle(manifest=response.manifest, files=response.files)


def _safe_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SkillDistributionError(f"Skill package contains unsafe path: {value!r}")
    return path


def _canonical_package_id(manifest: ImmutableSkillPackageManifest) -> str:
    identity = json.dumps(
        {
            "repository": manifest.citation.repository,
            "source_revision": manifest.source_revision,
            "name": manifest.name,
            "version": manifest.version,
            "files": [
                {"path": item.path, "sha256": item.sha256, "size": item.size}
                for item in manifest.files
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(identity).hexdigest()}"


def _validate_manifest(manifest: ImmutableSkillPackageManifest) -> None:
    declared = [item.path for item in manifest.files]
    if len(set(declared)) != len(declared) or "SKILL.md" not in declared:
        raise SkillDistributionError(f"Invalid file inventory for {manifest.name}")
    for path in declared:
        _safe_path(path)
    parsed_paths = {value: PurePosixPath(value) for value in declared}
    for path_value, posix_path in parsed_paths.items():
        if any(
            other != path_value
            and posix_path == PurePosixPath(*PurePosixPath(other).parts[: len(posix_path.parts)])
            for other in parsed_paths
        ):
            raise SkillDistributionError(f"Package paths collide at {path_value}")
    if len(set(manifest.resources)) != len(manifest.resources):
        raise SkillDistributionError(f"Duplicate resources for {manifest.name}")
    for resource in manifest.resources:
        _safe_path(resource)
        if resource not in declared:
            raise SkillDistributionError(f"Undeclared resource for {manifest.name}: {resource}")
    if manifest.package_id != _canonical_package_id(manifest):
        raise SkillDistributionError(f"Canonical package ID mismatch for {manifest.name}")
    if manifest.citation.revision != manifest.source_revision:
        raise SkillDistributionError(f"Package revision mismatch for {manifest.name}")


def _verified_files(
    bundle: SkillPackageBundle, expected: ImmutableSkillPackageManifest
) -> dict[str, bytes]:
    _validate_manifest(expected)
    _validate_manifest(bundle.manifest)
    if bundle.manifest.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise SkillDistributionError(f"Package identity mismatch for {expected.name}")
    declared = {item.path: item for item in expected.files}
    if len(declared) != len(expected.files):
        raise SkillDistributionError(f"Package {expected.name} has duplicate declared paths")
    received = {item.path: item for item in bundle.files}
    if len(received) != len(bundle.files) or set(received) != set(declared):
        raise SkillDistributionError(f"Package file inventory mismatch for {expected.name}")
    result: dict[str, bytes] = {}
    total = 0
    for path_value, expected_file in declared.items():
        file = received[path_value]
        if (
            file.sha256 != expected_file.sha256
            or file.size != expected_file.size
            or file.path != expected_file.path
        ):
            raise SkillDistributionError(f"File metadata mismatch for {path_value}")
        try:
            content = base64.b64decode(file.content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise SkillDistributionError(f"Invalid base64 content for {path_value}") from error
        total += len(content)
        if total > MAX_RESPONSE_BYTES:
            raise SkillDistributionError("Decoded skill package exceeds the size limit")
        if len(content) != expected_file.size:
            raise SkillDistributionError(f"Size mismatch for {path_value}")
        if hashlib.sha256(content).hexdigest() != expected_file.sha256:
            raise SkillDistributionError(f"Hash mismatch for {path_value}")
        result[path_value] = content
    return result


def _verify_owned_directory(root: Path, expected: Sequence[SkillFileManifest]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise SkillDistributionError(f"Modified managed package: {root}")
    expected_by_path = {item.path: item for item in expected}
    expected_directories = {
        PurePosixPath(*path.parts[:index]).as_posix()
        for value in expected_by_path
        for path in [_safe_path(value)]
        for index in range(1, len(path.parts))
    }
    actual_files: set[str] = set()
    try:
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise SkillDistributionError(f"Modified managed package: {root}")
            if path.is_dir():
                if relative not in expected_directories:
                    raise SkillDistributionError(f"Modified managed package: {root}")
                continue
            if not path.is_file() or relative not in expected_by_path:
                raise SkillDistributionError(f"Modified managed package: {root}")
            expected_file = expected_by_path[relative]
            if path.stat().st_size != expected_file.size:
                raise SkillDistributionError(f"Modified managed package: {root}")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as source:
                while chunk := source.read(64 * 1024):
                    size += len(chunk)
                    if size > expected_file.size:
                        raise SkillDistributionError(f"Modified managed package: {root}")
                    digest.update(chunk)
            if size != expected_file.size or digest.hexdigest() != expected_file.sha256:
                raise SkillDistributionError(f"Modified managed package: {root}")
            actual_files.add(relative)
    except OSError as error:
        raise SkillDistributionError(f"Could not verify managed package {root}: {error}") from error
    if actual_files != set(expected_by_path):
        raise SkillDistributionError(f"Modified managed package: {root}")


def _bounded_json(path: Path, limit: int) -> object:
    try:
        if path.is_symlink() or path.stat().st_size > limit:
            raise SkillDistributionError(f"Refusing unsafe or oversized file: {path}")
        with path.open("rb") as source:
            payload = source.read(limit + 1)
    except OSError as error:
        raise SkillDistributionError(f"Could not read {path}: {error}") from error
    if len(payload) > limit:
        raise SkillDistributionError(f"Refusing oversized file: {path}")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise SkillDistributionError(f"Invalid JSON in {path}") from error


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path, limit: int) -> None:
    try:
        if source.is_symlink() or not source.is_file() or source.stat().st_size > limit:
            raise SkillDistributionError(f"Refusing unsafe recovery metadata: {source}")
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            size = 0
            with os.fdopen(handle, "wb") as output_file:
                with source.open("rb") as input_file:
                    while chunk := input_file.read(64 * 1024):
                        size += len(chunk)
                        if size > limit:
                            raise SkillDistributionError(
                                f"Refusing oversized recovery metadata: {source}"
                            )
                        output_file.write(chunk)
                output_file.flush()
                os.fsync(output_file.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as error:
        raise SkillDistributionError(f"Could not recover skill metadata: {error}") from error


def _remove_verified_directory(path: Path, files: Sequence[SkillFileManifest]) -> None:
    _verify_owned_directory(path, files)
    shutil.rmtree(path)


class SkillInstaller:
    """Verifies, caches, and atomically installs self-contained skill packages."""

    def __init__(
        self,
        destination: Path,
        cache_directory: Path,
        *,
        project_root: Path | None = None,
    ) -> None:
        self.destination = destination
        self.cache_directory = cache_directory
        self.project_root = project_root

    def _verify_cache_confinement(self) -> None:
        if self.project_root is None:
            return
        expected = harness_cache_directory(self.project_root)
        if self.cache_directory.absolute() != expected.absolute():
            raise SkillDistributionError("The harness cache must remain inside the project root")

    def _verify_target_paths(self, target: SkillTarget) -> None:
        if target == "generic":
            if self.project_root is not None:
                raise SkillDistributionError(
                    "A project root is only valid for codex and claude targets"
                )
            return
        if self.project_root is None:
            raise SkillDistributionError("A project root is required for codex and claude targets")
        expected_destination = harness_skill_directory(self.project_root, target)
        if self.destination.absolute() != expected_destination.absolute():
            raise SkillDistributionError(
                "The harness destination must use the target's project discovery directory"
            )
        self._verify_cache_confinement()

    def _ownership(self) -> _Ownership:
        path = self.destination / ".team-agent-ownership.json"
        if not path.exists():
            return _Ownership()
        try:
            return _Ownership.model_validate(_bounded_json(path, MAX_OWNERSHIP_BYTES))
        except ValidationError as error:
            raise SkillDistributionError("The ownership manifest is invalid") from error

    def _cache_package_directory(self, package_id: str) -> Path:
        package_directory = self.cache_directory / package_id.removeprefix("sha256:")
        if self.project_root is not None and (
            package_directory.is_symlink()
            or (package_directory.exists() and not package_directory.is_dir())
        ):
            raise SkillDistributionError(
                f"The harness cache package path must be a directory: {package_directory}"
            )
        return package_directory

    def _cached(self, expected: SkillLockPackage) -> SkillPackageBundle | None:
        self._verify_cache_confinement()
        package_directory = self._cache_package_directory(expected.package_id)
        path = package_directory / "bundle.json"
        if self.project_root is not None and (
            path.is_symlink() or (path.exists() and not path.is_file())
        ):
            raise SkillDistributionError(
                f"The harness cache bundle path must be a regular file: {path}"
            )
        if not path.exists():
            return None
        try:
            bundle = SkillPackageBundle.model_validate(_bounded_json(path, MAX_RESPONSE_BYTES))
            _verified_files(bundle, expected)
            return bundle
        except (ValidationError, SkillDistributionError):
            return None

    def _cache(self, bundle: SkillPackageBundle) -> None:
        self._verify_cache_confinement()
        package_directory = self._cache_package_directory(bundle.manifest.package_id)
        path = package_directory / "bundle.json"
        _atomic_json(path, bundle.model_dump(mode="json"))

    def _load_lock(self, lock_path: Path) -> SkillLock:
        try:
            return SkillLock.model_validate(_bounded_json(lock_path, MAX_LOCK_BYTES))
        except ValidationError as error:
            raise SkillDistributionError(f"Invalid skill lock: {lock_path}") from error

    @property
    def _transaction_path(self) -> Path:
        return self.destination / ".team-agent-transaction"

    def _load_transaction(self, path: Path) -> _Transaction:
        try:
            return _Transaction.model_validate(
                _bounded_json(path / "journal.json", MAX_OWNERSHIP_BYTES)
            )
        except ValidationError as error:
            raise SkillDistributionError("The skill transaction journal is invalid") from error

    def _rollback_transaction(self, root: Path, transaction: _Transaction) -> None:
        for package in reversed(transaction.packages):
            target = self.destination / package.name
            backup = root / "backups" / package.name
            if backup.exists():
                _verify_owned_directory(backup, package.previous_files)
                if target.exists():
                    _remove_verified_directory(target, package.new_files)
                os.replace(backup, target)
            elif package.had_existing:
                if not target.exists():
                    raise SkillDistributionError(
                        f"Cannot recover missing managed package: {package.name}"
                    )
                _verify_owned_directory(target, package.previous_files)
            elif target.exists():
                _remove_verified_directory(target, package.new_files)

        metadata = (
            ("skills.lock.json", transaction.had_lock),
            (".team-agent-ownership.json", transaction.had_ownership),
        )
        for name, had_previous in metadata:
            target = self.destination / name
            backup = root / "metadata" / name
            if had_previous:
                if not backup.is_file() or backup.is_symlink():
                    raise SkillDistributionError(f"Cannot recover skill metadata: {name}")
                if name == "skills.lock.json":
                    self._load_lock(backup)
                    limit = MAX_LOCK_BYTES
                else:
                    try:
                        _Ownership.model_validate(_bounded_json(backup, MAX_OWNERSHIP_BYTES))
                    except ValidationError as error:
                        raise SkillDistributionError(
                            "Cannot recover invalid ownership metadata"
                        ) from error
                    limit = MAX_OWNERSHIP_BYTES
                _atomic_copy(backup, target, limit)
            elif target.exists():
                if target.is_symlink() or not target.is_file():
                    raise SkillDistributionError(f"Cannot recover skill metadata: {name}")
                target.unlink()
        shutil.rmtree(root)

    def _recover_transaction(self) -> None:
        root = self._transaction_path
        if not root.exists():
            return
        if root.is_symlink() or not root.is_dir():
            raise SkillDistributionError("The skill transaction path is unsafe")
        transaction = self._load_transaction(root)
        if transaction.state == "committed":
            shutil.rmtree(root)
            return
        self._rollback_transaction(root, transaction)

    def _commit(
        self,
        staging: Path,
        lock: SkillLock,
        ownership: _Ownership,
    ) -> None:
        self._verify_target_paths(lock.target)
        candidate = Path(
            tempfile.mkdtemp(
                prefix=".team-agent-transaction-preparing-", dir=self.destination.parent
            )
        )
        transaction_root = self._transaction_path
        try:
            (candidate / "new").mkdir()
            (candidate / "backups").mkdir()
            (candidate / "metadata").mkdir()
            transaction_packages: list[_TransactionPackage] = []
            prior_ownership = self._ownership()
            for package in lock.packages:
                target = self.destination / package.name
                previous = prior_ownership.packages.get(package.name)
                if target.exists():
                    if previous is None:
                        raise SkillDistributionError(
                            f"Refusing to replace unmanaged package path: {target}"
                        )
                    _verify_owned_directory(target, previous.files)
                transaction_packages.append(
                    _TransactionPackage(
                        name=package.name,
                        had_existing=target.exists(),
                        previous_files=previous.files if previous is not None else [],
                        new_files=package.files,
                    )
                )
                os.replace(staging / package.name, candidate / "new" / package.name)

            lock_path = self.destination / "skills.lock.json"
            ownership_path = self.destination / ".team-agent-ownership.json"
            for path in (lock_path, ownership_path):
                if path.exists():
                    if path.is_symlink() or not path.is_file():
                        raise SkillDistributionError(f"Refusing unsafe managed metadata: {path}")
                    shutil.copy2(path, candidate / "metadata" / path.name)
            transaction = _Transaction(
                had_lock=lock_path.exists(),
                had_ownership=ownership_path.exists(),
                packages=transaction_packages,
            )
            _atomic_json(candidate / "journal.json", transaction.model_dump(mode="json"))
            os.replace(candidate, transaction_root)

            try:
                for transaction_package in transaction.packages:
                    target = self.destination / transaction_package.name
                    backup = transaction_root / "backups" / transaction_package.name
                    if target.exists():
                        os.replace(target, backup)
                    os.replace(transaction_root / "new" / transaction_package.name, target)

                _atomic_json(lock_path, lock.model_dump(mode="json"))
                _atomic_json(ownership_path, ownership.model_dump(mode="json"))
                committed = transaction.model_copy(update={"state": "committed"})
                _atomic_json(transaction_root / "journal.json", committed.model_dump(mode="json"))
            except BaseException:
                self._rollback_transaction(transaction_root, transaction)
                raise
            shutil.rmtree(transaction_root)
        finally:
            if candidate.exists():
                shutil.rmtree(candidate)

    def pull(
        self,
        client: SkillClient,
        *,
        project: str | None,
        names: Sequence[str] | None = None,
        all_skills: bool = False,
        lock_path: Path | None = None,
        revision: str | None = None,
        target: SkillTarget = "generic",
        frozen: bool = False,
    ) -> PullResult:
        try:
            return self._pull(
                client,
                project=project,
                names=names,
                all_skills=all_skills,
                lock_path=lock_path,
                revision=revision,
                target=target,
                frozen=frozen,
            )
        except SkillDistributionError:
            raise
        except ValidationError as error:
            raise SkillDistributionError(f"Skill pull data is invalid: {error}") from error
        except OSError as error:
            raise SkillDistributionError(
                f"Skill pull filesystem operation failed: {error}"
            ) from error

    def _pull(
        self,
        client: SkillClient,
        *,
        project: str | None,
        names: Sequence[str] | None = None,
        all_skills: bool = False,
        lock_path: Path | None = None,
        revision: str | None = None,
        target: SkillTarget = "generic",
        frozen: bool = False,
    ) -> PullResult:
        self._verify_target_paths(target)
        modes = int(names is not None) + int(all_skills) + int(lock_path is not None)
        if modes != 1:
            raise SkillDistributionError("Exactly one of skill names, --all, or --lock is required")
        if frozen and lock_path is None:
            raise SkillDistributionError("--frozen requires --lock")
        if lock_path is not None and revision is not None:
            raise SkillDistributionError("--revision cannot be combined with --lock")
        if self.destination.exists() and self.destination.is_symlink():
            raise SkillDistributionError("The destination must not be a symbolic link")
        self._verify_target_paths(target)
        self.destination.mkdir(parents=True, exist_ok=True)
        self._verify_target_paths(target)
        self._recover_transaction()
        ownership_path = self.destination / ".team-agent-ownership.json"
        output_lock = self.destination / "skills.lock.json"
        if output_lock.exists() != ownership_path.exists():
            unmanaged = output_lock if output_lock.exists() else ownership_path
            raise SkillDistributionError(f"Refusing to replace unmanaged metadata: {unmanaged}")
        ownership = self._ownership()
        if output_lock.exists():
            self._load_lock(output_lock)

        if lock_path is not None:
            lock = self._load_lock(lock_path)
            if (project is not None and lock.project != project) or lock.target != target:
                raise SkillDistributionError("The lock project or target does not match this pull")
            project = lock.project
        else:
            if not project:
                raise SkillDistributionError("A project is required unless --lock is used")
            if names is not None and (
                not names
                or len(names) > 100
                or len(set(names)) != len(names)
                or any(_NAME.fullmatch(name) is None for name in names)
            ):
                raise SkillDistributionError("Skill names must be 1-100 unique kebab-case names")
            resolution = client.resolve(
                project, names=names, all_skills=all_skills, revision=revision
            )
            resolved_names = [package.name for package in resolution.packages]
            if (
                resolution.project != project
                or resolution.selected_names != resolved_names
                or (names is not None and resolved_names != list(names))
                or (revision is not None and resolution.catalog_revision != revision)
            ):
                raise SkillDistributionError(
                    "Context service resolution does not match the request"
                )
            lock = SkillLock(
                catalog_revision=resolution.catalog_revision,
                project=resolution.project,
                target=target,
                packages=[
                    SkillLockPackage(**package.model_dump()) for package in resolution.packages
                ],
            )

        self._verify_target_paths(target)
        for package in lock.packages:
            _validate_manifest(package)
            target_path = self.destination / package.name
            if target_path.is_symlink():
                raise SkillDistributionError(f"Refusing symbolic-link package path: {target_path}")
            if target_path.exists() and package.name not in ownership.packages:
                raise SkillDistributionError(
                    f"Refusing to replace unmanaged package path: {target_path}"
                )
            if target_path.exists():
                _verify_owned_directory(target_path, ownership.packages[package.name].files)

        self._verify_target_paths(target)
        staging = Path(tempfile.mkdtemp(prefix=".team-agent-stage-", dir=self.destination.parent))
        try:
            for package in lock.packages:
                self._verify_target_paths(target)
                bundle = self._cached(package)
                if bundle is None:
                    try:
                        bundle = client.download(package.package_id, project)
                    except SkillDistributionError as error:
                        if frozen:
                            raise SkillDistributionError(
                                f"Frozen package {package.name} is unavailable "
                                "from cache and service"
                            ) from error
                        raise
                    _verified_files(bundle, package)
                    self._verify_target_paths(target)
                    self._cache(bundle)
                self._verify_target_paths(target)
                files = _verified_files(bundle, package)
                package_stage = staging / package.name
                package_stage.mkdir()
                for relative, content in files.items():
                    output = package_stage.joinpath(*_safe_path(relative).parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(content)

            for package in lock.packages:
                ownership.packages[package.name] = _OwnedPackage(
                    package_id=package.package_id,
                    path=package.name,
                    files=package.files,
                )

            self._verify_target_paths(target)
            self._commit(staging, lock, ownership)
            return PullResult(
                catalog_revision=lock.catalog_revision,
                installed=[package.name for package in lock.packages],
                destination=str(self.destination),
                lock_path=str(self.destination / "skills.lock.json"),
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)
