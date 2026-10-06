import asyncio
import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from team_agent_contracts import Citation, SkillFileManifest, SkillLock, SkillLockPackage
from team_agent_runtime import (
    AgentDecision,
    AgentTurnRequest,
    AgentTurnResult,
    AgentTurnStatus,
    AgentUsage,
    DecisionKind,
    UsageSource,
)
from team_agent_runtime.conversations import (
    ConversationBusyError,
    ConversationRequestConflictError,
    DurableConversationRuntime,
    InMemoryConversationRepository,
    StaleConversationClaimError,
    request_fingerprint,
)


def _request(run_id: str, objective: str = "Recover the worker") -> AgentTurnRequest:
    return AgentTurnRequest(
        run_id=run_id,
        project="platform",
        objective=objective,
        skill_names=["incident-guide"],
    )


def _result(request: AgentTurnRequest, revision: str, text: str) -> AgentTurnResult:
    citation = Citation(repository="company/runbooks", path="worker.md", revision=revision)
    package = SkillLockPackage(
        package_id=f"sha256:{'a' * 64}",
        name="incident-guide",
        description="Recover workers.",
        version="1.0.0",
        source_revision=revision,
        files=[SkillFileManifest(path="SKILL.md", sha256="b" * 64, size=10)],
        citation=citation,
    )
    return AgentTurnResult(
        run_id=request.run_id,
        status=AgentTurnStatus.COMPLETED,
        text=text,
        canonical_knowledge_citations=[citation],
        supplemental_memory_citations=[],
        content_revision=revision,
        selected_skill_lock=SkillLock(
            schema_version="1",
            project=request.project,
            target="generic",
            catalog_revision=revision,
            packages=[package],
        ),
        decisions=[
            AgentDecision(
                kind=DecisionKind.SOURCE_PRECEDENCE,
                name="canonical_knowledge_over_memory",
                detail="Git is canonical.",
            )
        ],
        usage=AgentUsage(source=UsageSource.SYNTHETIC, total_tokens=3),
        failures=[],
    )


class _RecordingRuntime:
    def __init__(self) -> None:
        self.requests: list[AgentTurnRequest] = []

    async def execute(self, request: AgentTurnRequest) -> AgentTurnResult:
        self.requests.append(request)
        return _result(request, f"revision-{len(self.requests)}", f"answer-{len(self.requests)}")


def test_continuation_uses_persisted_bounded_context_after_restart() -> None:
    async def check() -> None:
        repository = InMemoryConversationRepository()
        first_runtime = _RecordingRuntime()
        first = await DurableConversationRuntime(first_runtime, repository).execute(
            "conversation-1", _request("run-1")
        )
        second_runtime = _RecordingRuntime()
        second = await DurableConversationRuntime(second_runtime, repository).execute(
            "conversation-1", _request("run-2", "Did it recover?")
        )

        assert first.content_revision == "revision-1"
        assert second.content_revision == "revision-1"
        context = second_runtime.requests[0].conversation_context
        assert context is not None
        assert [(turn.user, turn.assistant) for turn in context.recent_turns] == [
            ("Recover the worker", "answer-1")
        ]
        stored = await repository.read_turn("conversation-1", "run-2")
        assert stored is not None
        assert stored["request"]["objective"] == "Did it recover?"
        assert stored["result"]["content_revision"] == "revision-1"

    asyncio.run(check())


def test_retry_replays_terminal_result_and_rejects_changed_request() -> None:
    async def check() -> None:
        repository = InMemoryConversationRepository()
        inner = _RecordingRuntime()
        runtime = DurableConversationRuntime(inner, repository)

        first = await runtime.execute("conversation-1", _request("run-1"))
        replay = await runtime.execute("conversation-1", _request("run-1"))

        assert replay == first
        assert len(inner.requests) == 1
        with pytest.raises(ConversationRequestConflictError):
            await runtime.execute("conversation-1", _request("run-1", "A different request"))

    asyncio.run(check())


def test_compaction_keeps_summary_and_recent_context_bounded() -> None:
    async def check() -> None:
        repository = InMemoryConversationRepository()
        inner = _RecordingRuntime()
        runtime = DurableConversationRuntime(inner, repository)
        for sequence in range(1, 9):
            await runtime.execute(
                "conversation-1", _request(f"run-{sequence}", f"question-{sequence}")
            )

        await runtime.execute("conversation-1", _request("run-9", "question-9"))
        context = inner.requests[-1].conversation_context
        assert context is not None
        assert len(context.summary) <= 12_000
        assert [turn.sequence for turn in context.recent_turns] == [3, 4, 5, 6, 7, 8]
        assert "question-1" in context.summary
        assert "question-2" in context.summary
        conversation = await repository.read_conversation("conversation-1")
        assert conversation is not None
        assert conversation["summary_through_sequence"] == 3

    asyncio.run(check())


class _ConcurrentRuntime:
    def __init__(self, expected_concurrency: int) -> None:
        self.expected_concurrency = expected_concurrency
        self.active = 0
        self.maximum_active = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, request: AgentTurnRequest) -> AgentTurnResult:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.active >= self.expected_concurrency:
            self.started.set()
        await self.release.wait()
        self.active -= 1
        return _result(request, "revision-1", f"answer-{request.run_id}")


def test_same_conversation_serializes_while_different_conversations_run_concurrently() -> None:
    async def check_same_conversation() -> None:
        repository = InMemoryConversationRepository()
        inner = _ConcurrentRuntime(expected_concurrency=1)
        runtime = DurableConversationRuntime(inner, repository)
        first = asyncio.create_task(runtime.execute("same", _request("run-1")))
        await inner.started.wait()
        second = asyncio.create_task(runtime.execute("same", _request("run-2")))
        await asyncio.sleep(0)
        assert inner.maximum_active == 1
        inner.release.set()
        await asyncio.gather(first, second)
        assert inner.maximum_active == 1

    async def check_different_conversations() -> None:
        repository = InMemoryConversationRepository()
        inner = _ConcurrentRuntime(expected_concurrency=2)
        runtime = DurableConversationRuntime(inner, repository)
        first = asyncio.create_task(runtime.execute("first", _request("run-1")))
        second = asyncio.create_task(runtime.execute("second", _request("run-2")))
        await asyncio.wait_for(inner.started.wait(), timeout=1)
        assert inner.maximum_active == 2
        inner.release.set()
        await asyncio.gather(first, second)

    asyncio.run(check_same_conversation())
    asyncio.run(check_different_conversations())


def test_expired_claim_is_failed_and_old_generation_cannot_complete() -> None:
    async def check() -> None:
        repository = InMemoryConversationRepository()
        started = datetime(2026, 1, 1, tzinfo=UTC)
        first_request = _request("run-1")
        first_claim = await repository.claim(
            "conversation-1",
            first_request,
            request_fingerprint(first_request),
            started,
            started + timedelta(seconds=10),
        )
        with pytest.raises(ConversationBusyError):
            second_request = _request("run-2")
            await repository.claim(
                "conversation-1",
                second_request,
                request_fingerprint(second_request),
                started + timedelta(seconds=5),
                started + timedelta(seconds=15),
            )

        second_request = _request("run-2")
        second_claim = await repository.claim(
            "conversation-1",
            second_request,
            request_fingerprint(second_request),
            started + timedelta(seconds=11),
            started + timedelta(seconds=21),
        )
        with pytest.raises(StaleConversationClaimError):
            await repository.complete(
                first_claim,
                _result(first_request, "revision-1", "stale answer"),
                started + timedelta(seconds=12),
            )
        expired = await repository.read_turn("conversation-1", "run-1")
        assert expired is not None
        assert expired["status"] == "failed"
        assert expired["result"]["failures"][0]["code"] == "claim_expired"
        await repository.complete(
            second_claim,
            _result(second_request, "revision-2", "fresh answer"),
            started + timedelta(seconds=12),
        )

    asyncio.run(check())


def test_runtime_failure_and_each_turn_revision_are_persisted() -> None:
    class _RevisionRuntime:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, request: AgentTurnRequest) -> AgentTurnResult:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("private provider failure")
            return _result(request, f"revision-{self.calls}", f"answer-{self.calls}")

    async def check() -> None:
        repository = InMemoryConversationRepository()
        runtime = DurableConversationRuntime(_RevisionRuntime(), repository)
        first = await runtime.execute("conversation-1", _request("run-1"))
        failed = await runtime.execute("conversation-1", _request("run-2"))
        third = await runtime.execute("conversation-1", _request("run-3"))

        assert first.content_revision == "revision-1"
        assert failed.status == AgentTurnStatus.FAILED
        assert failed.failures[0].code == "runtime_failed"
        assert "private provider failure" not in failed.failures[0].message
        assert third.content_revision == "revision-3"
        stored_first = await repository.read_turn("conversation-1", "run-1")
        stored_failed = await repository.read_turn("conversation-1", "run-2")
        stored_third = await repository.read_turn("conversation-1", "run-3")
        assert stored_first is not None and stored_failed is not None and stored_third is not None
        assert stored_first["result"]["selected_skill_lock"]["catalog_revision"] == "revision-1"
        assert stored_failed["status"] == "failed"
        assert stored_third["result"]["selected_skill_lock"]["catalog_revision"] == "revision-3"

    asyncio.run(check())


def test_result_for_a_different_run_is_replaced_with_bounded_failure() -> None:
    class _MismatchedRuntime:
        async def execute(self, request: AgentTurnRequest) -> AgentTurnResult:
            return _result(request.model_copy(update={"run_id": "wrong-run"}), "revision-1", "x")

    async def check() -> None:
        repository = InMemoryConversationRepository()
        result = await DurableConversationRuntime(_MismatchedRuntime(), repository).execute(
            "conversation-1", _request("run-1")
        )

        assert result.run_id == "run-1"
        assert result.status == AgentTurnStatus.FAILED
        assert result.failures[0].code == "runtime_result_mismatch"
        stored = await repository.read_turn("conversation-1", "run-1")
        assert stored is not None
        assert stored["result"]["run_id"] == "run-1"

    asyncio.run(check())


def test_cancellation_persists_bounded_failure_before_propagating() -> None:
    class _CancelledRuntime:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def execute(self, request: AgentTurnRequest) -> AgentTurnResult:
            del request
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def check() -> None:
        repository = InMemoryConversationRepository()
        inner = _CancelledRuntime()
        task = asyncio.create_task(
            DurableConversationRuntime(inner, repository).execute(
                "conversation-1", _request("run-1")
            )
        )
        await inner.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        stored = await repository.read_turn("conversation-1", "run-1")
        assert stored is not None
        assert stored["status"] == "failed"
        assert stored["result"]["failures"][0]["code"] == "turn_cancelled"
        conversation = await repository.read_conversation("conversation-1")
        assert conversation is not None
        assert conversation["active_turn_id"] is None

    asyncio.run(check())


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, candidate) for candidate in expected):
                return False
            continue
        actual = document.get(key)
        if isinstance(expected, dict) and "$gt" in expected:
            if actual is None or actual <= expected["$gt"]:
                return False
        elif isinstance(expected, dict) and "$lte" in expected:
            if actual is None or actual > expected["$lte"]:
                return False
        elif actual != expected:
            return False
    return True


class _MongoCursor:
    def __init__(self, documents: list[dict[str, Any]], lengths: list[int]) -> None:
        self.documents = documents
        self.lengths = lengths

    def sort(self, key: str, direction: int) -> "_MongoCursor":
        self.documents.sort(key=lambda document: document[key], reverse=direction < 0)
        return self

    def limit(self, value: int) -> "_MongoCursor":
        self.documents = self.documents[:value]
        return self

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        self.lengths.append(length)
        return self.documents[:length]


class _MongoCollection:
    def __init__(self, lengths: list[int]) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.indexes: list[tuple[list[tuple[str, int]], str, bool]] = []
        self.lengths = lengths
        self.fail_active_insert = False
        self.fail_claim_release = False

    async def create_index(
        self, keys: list[tuple[str, int]], *, name: str, unique: bool = False
    ) -> str:
        self.indexes.append((keys, name, unique))
        return name

    async def find_one(
        self, query: dict[str, Any], *, session: object | None = None
    ) -> dict[str, Any] | None:
        del session
        return next(
            (document for document in self.documents.values() if _matches(document, query)),
            None,
        )

    def find(self, query: dict[str, Any], *, session: object | None = None) -> _MongoCursor:
        del session
        return _MongoCursor(
            [document for document in self.documents.values() if _matches(document, query)],
            self.lengths,
        )

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
        session: object | None = None,
    ) -> Any:
        if self.fail_active_insert and update.get("$setOnInsert", {}).get("conversation_id"):
            self.fail_active_insert = False
            raise RuntimeError("injected active-turn insert failure")
        if (
            self.fail_claim_release
            and update.get("$set", {}).get("active_turn_id", object()) is None
            and "active_turn_id" in query
        ):
            self.fail_claim_release = False
            raise RuntimeError("injected claim-release failure")
        document = await self.find_one(query, session=session)
        inserted = False
        if document is None and upsert:
            inserted = True
            document = {
                key: value
                for key, value in query.items()
                if not key.startswith("$") and not isinstance(value, dict)
            }
            document.update(update.get("$setOnInsert", {}))
            self.documents[str(document["_id"])] = document
        if document is not None:
            document.update(update.get("$set", {}))
            for key, value in update.get("$inc", {}).items():
                document[key] = int(document.get(key, 0)) + value
            for key, value in update.get("$max", {}).items():
                if document.get(key) is None or document[key] < value:
                    document[key] = value
        return type(
            "UpdateResult",
            (),
            {"matched_count": int(document is not None and not inserted)},
        )()

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        return_document: object,
        session: object | None = None,
    ) -> dict[str, Any] | None:
        del return_document
        document = await self.find_one(query, session=session)
        if document is None:
            return None
        document.update(update.get("$set", {}))
        for key, value in update.get("$inc", {}).items():
            document[key] = int(document.get(key, 0)) + value
        for key, value in update.get("$max", {}).items():
            if document.get(key) is None or document[key] < value:
                document[key] = value
        return document


class _MongoDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _MongoCollection] = {}
        self.validators: dict[str, dict[str, Any]] = {}
        self.lengths: list[int] = []
        self.client = self
        self.transaction_count = 0

    def start_session(self) -> "_MongoSession":
        return _MongoSession(self)

    async def list_collection_names(self) -> list[str]:
        return list(self.collections)

    async def create_collection(
        self,
        name: str,
        *,
        validator: dict[str, Any],
        validationLevel: str,
        validationAction: str,
    ) -> None:
        assert validationLevel == "strict"
        assert validationAction == "error"
        self.collections[name] = _MongoCollection(self.lengths)
        self.validators[name] = validator

    async def command(self, command: dict[str, Any]) -> dict[str, int]:
        self.validators[command["collMod"]] = command["validator"]
        return {"ok": 1}

    def __getitem__(self, name: str) -> _MongoCollection:
        return self.collections[name]


class _MongoSession:
    def __init__(self, database: _MongoDatabase) -> None:
        self.database = database

    async def __aenter__(self) -> "_MongoSession":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc, traceback

    async def with_transaction(self, operation: Any) -> Any:
        self.database.transaction_count += 1
        snapshots = {
            name: copy.deepcopy(collection.documents)
            for name, collection in self.database.collections.items()
        }
        try:
            return await operation(self)
        except BaseException:
            for name, documents in snapshots.items():
                self.database.collections[name].documents = documents
            raise


def test_mongo_repository_migrates_snake_case_schema_and_uses_only_bounded_reads() -> None:
    from team_agent_runtime.mongo_conversations import MongoConversationRepository

    async def check() -> None:
        database = _MongoDatabase()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        repository = MongoConversationRepository(database)  # type: ignore[arg-type]
        await repository.migrate(now)
        runtime = DurableConversationRuntime(_RecordingRuntime(), repository)
        for sequence in range(1, 9):
            await runtime.execute(
                "conversation-1", _request(f"run-{sequence}", f"question-{sequence}")
            )

        assert set(database.validators) == {
            "conversations",
            "conversation_turns",
            "schema_migrations",
        }
        turn_properties = database.validators["conversation_turns"]["$jsonSchema"]["properties"]
        assert "request_fingerprint" in turn_properties
        assert "claim_generation" in turn_properties
        assert not any(any(character.isupper() for character in key) for key in turn_properties)
        assert database.lengths
        assert max(database.lengths) == 7
        assert all(length <= 7 for length in database.lengths)
        assert database.transaction_count == 16
        assert (
            [
                ("conversation_id", 1),
                ("status", 1),
                ("sequence", 1),
            ],
            "conversation_status_sequence",
            False,
        ) in database["conversation_turns"].indexes
        stored = await repository.read_turn("conversation-1", "run-8")
        assert stored is not None
        assert stored["status"] == "completed"
        assert stored["result"]["selected_skill_lock"]["catalog_revision"] == "revision-8"
        conversation = await repository.read_conversation("conversation-1")
        assert conversation is not None
        assert conversation["summary_through_sequence"] == 2
        assert conversation["active_turn_id"] is None
        assert conversation["claim_expires_at"] is None

    asyncio.run(check())


def test_mongo_claim_transaction_rolls_back_if_turn_insert_fails() -> None:
    from team_agent_runtime.mongo_conversations import MongoConversationRepository

    async def check() -> None:
        database = _MongoDatabase()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        repository = MongoConversationRepository(database)  # type: ignore[arg-type]
        await repository.migrate(now)
        database["conversation_turns"].fail_active_insert = True
        request = _request("run-1")

        with pytest.raises(RuntimeError, match="active-turn insert failure"):
            await repository.claim(
                "conversation-1",
                request,
                request_fingerprint(request),
                now,
                now + timedelta(seconds=60),
            )

        assert await repository.read_conversation("conversation-1") is None
        assert await repository.read_turn("conversation-1", "run-1") is None
        assert database.transaction_count == 1

    asyncio.run(check())


def test_mongo_completion_transaction_rolls_back_terminal_turn_if_release_fails() -> None:
    from team_agent_runtime.mongo_conversations import MongoConversationRepository

    async def check() -> None:
        database = _MongoDatabase()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        repository = MongoConversationRepository(database)  # type: ignore[arg-type]
        await repository.migrate(now)
        request = _request("run-1")
        claim = await repository.claim(
            "conversation-1",
            request,
            request_fingerprint(request),
            now,
            now + timedelta(seconds=60),
        )
        database["conversations"].fail_claim_release = True

        with pytest.raises(RuntimeError, match="claim-release failure"):
            await repository.complete(
                claim,
                _result(request, "revision-1", "answer"),
                now + timedelta(seconds=1),
            )

        turn = await repository.read_turn("conversation-1", "run-1")
        conversation = await repository.read_conversation("conversation-1")
        assert turn is not None and conversation is not None
        assert turn["status"] == "active"
        assert turn["result"] is None
        assert conversation["active_turn_id"] == "run-1"
        assert database.transaction_count == 2

    asyncio.run(check())


def test_oversized_turn_result_is_replaced_with_bounded_persisted_failure() -> None:
    class _OversizedRuntime:
        async def execute(self, request: AgentTurnRequest) -> AgentTurnResult:
            result = _result(request, "revision-1", "answer")
            result.text = "x" * (8 * 1024 * 1024)
            return result

    async def check() -> None:
        repository = InMemoryConversationRepository()
        result = await DurableConversationRuntime(_OversizedRuntime(), repository).execute(
            "conversation-1", _request("run-1")
        )

        assert result.status == AgentTurnStatus.FAILED
        assert result.failures[0].code == "turn_result_too_large"
        stored = await repository.read_turn("conversation-1", "run-1")
        assert stored is not None
        assert stored["status"] == "failed"
        assert len(str(stored["result"])) < 2_000

    asyncio.run(check())


def test_mongo_replay_clears_claim_left_after_terminal_turn_write() -> None:
    from team_agent_runtime.mongo_conversations import MongoConversationRepository

    async def check() -> None:
        database = _MongoDatabase()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        repository = MongoConversationRepository(database)  # type: ignore[arg-type]
        await repository.migrate(now)
        request = _request("run-1")
        claim = await repository.claim(
            "conversation-1",
            request,
            request_fingerprint(request),
            now,
            now + timedelta(seconds=60),
        )
        turn = database["conversation_turns"].documents["conversation-1:run-1"]
        terminal = _result(request, "revision-1", "persisted before crash")
        turn.update(
            {
                "status": "completed",
                "result": terminal.model_dump(mode="json"),
                "completed_at": now + timedelta(seconds=1),
            }
        )

        replay = await repository.claim(
            "conversation-1",
            request,
            request_fingerprint(request),
            now + timedelta(seconds=2),
            now + timedelta(seconds=62),
        )

        assert replay.replay_result == terminal
        assert replay.generation == claim.generation
        conversation = await repository.read_conversation("conversation-1")
        assert conversation is not None
        assert conversation["active_turn_id"] is None
        assert conversation["claim_expires_at"] is None

    asyncio.run(check())


def test_mongo_claim_repairs_missed_compaction_before_loading_context() -> None:
    from team_agent_runtime.mongo_conversations import MongoConversationRepository

    async def check() -> None:
        database = _MongoDatabase()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        repository = MongoConversationRepository(database)  # type: ignore[arg-type]
        await repository.migrate(now)
        runtime = DurableConversationRuntime(_RecordingRuntime(), repository)
        for sequence in range(1, 8):
            await runtime.execute(
                "conversation-1", _request(f"run-{sequence}", f"question-{sequence}")
            )
        conversation = database["conversations"].documents["conversation-1"]
        conversation["summary"] = ""
        conversation["summary_through_sequence"] = 0

        request = _request("run-8", "question-8")
        claim = await repository.claim(
            "conversation-1",
            request,
            request_fingerprint(request),
            now + timedelta(minutes=5),
            now + timedelta(minutes=6),
        )

        assert "question-1" in claim.context.summary
        assert [turn.sequence for turn in claim.context.recent_turns] == [2, 3, 4, 5, 6, 7]
        assert max(database.lengths) == 7

    asyncio.run(check())


def test_mongo_expiry_fences_stale_generation_and_persists_expired_failure() -> None:
    from team_agent_runtime.mongo_conversations import MongoConversationRepository

    async def check() -> None:
        database = _MongoDatabase()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        repository = MongoConversationRepository(database)  # type: ignore[arg-type]
        await repository.migrate(now)
        first_request = _request("run-1")
        first = await repository.claim(
            "conversation-1",
            first_request,
            request_fingerprint(first_request),
            now,
            now + timedelta(seconds=10),
        )
        second_request = _request("run-2")
        second = await repository.claim(
            "conversation-1",
            second_request,
            request_fingerprint(second_request),
            now + timedelta(seconds=11),
            now + timedelta(seconds=21),
        )

        with pytest.raises(StaleConversationClaimError):
            await repository.complete(
                first,
                _result(first_request, "revision-1", "stale"),
                now + timedelta(seconds=12),
            )
        expired = await repository.read_turn("conversation-1", "run-1")
        assert expired is not None
        assert expired["status"] == "failed"
        assert expired["result"]["failures"][0]["code"] == "claim_expired"
        await repository.complete(
            second,
            _result(second_request, "revision-2", "fresh"),
            now + timedelta(seconds=12),
        )

    asyncio.run(check())
