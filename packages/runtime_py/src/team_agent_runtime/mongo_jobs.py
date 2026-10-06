"""MongoDB persistence for durable coding jobs supervised outside disposable runners."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from pymongo import ASCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from team_agent_runtime.jobs import (
    CodingJobClaim,
    CodingJobConflictError,
    CodingJobFailure,
    CodingJobInputRequest,
    CodingJobNotFoundError,
    CodingJobRequest,
    CodingJobResult,
    CodingJobStatus,
    CodingRunSnapshot,
    Document,
    MockPublicationOutcome,
    RunConsumptionClaim,
    RunStatus,
    StaleCodingJobClaimError,
    StaleRunConsumptionClaimError,
    _assert_document_size,
    _claim_from,
    _job_document,
    _run_document,
    _snapshot,
    _validate_consumption_result,
    _validate_consumption_window,
    _validate_lease_window,
    _validate_timestamp,
    coding_job_request_fingerprint,
    coding_job_result_fingerprint,
)


def _object_schema(required: list[str], properties: Document) -> Document:
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", *required],
            "additionalProperties": False,
            "properties": {"_id": {"bsonType": "string"}, **properties},
        }
    }


JOB_COLLECTION_VALIDATORS: dict[str, Document] = {
    "runs": _object_schema(
        [
            "schema_version",
            "project",
            "conversation_id",
            "source_turn_id",
            "status",
            "created_at",
            "updated_at",
            "claim_generation",
            "claim_expires_at",
            "completion_job_id",
            "completion_attempt",
            "completion_fingerprint",
            "resume_turn_id",
            "memory_id",
            "publication_outcome",
        ],
        {
            "schema_version": {"enum": ["2"]},
            "project": {"bsonType": "string"},
            "conversation_id": {"bsonType": "string"},
            "source_turn_id": {"bsonType": "string"},
            "status": {
                "enum": [
                    "waiting_for_jobs",
                    "running",
                    "completed",
                    "failed",
                    "timed_out",
                    "cancelled",
                    "needs_input",
                ]
            },
            "claim_generation": {"bsonType": "int", "minimum": 0},
            "claim_expires_at": {"bsonType": ["date", "null"]},
            "completion_job_id": {"bsonType": ["string", "null"]},
            "completion_attempt": {"bsonType": ["int", "null"], "minimum": 0},
            "completion_fingerprint": {"bsonType": ["string", "null"]},
            "resume_turn_id": {"bsonType": ["string", "null"]},
            "memory_id": {"bsonType": ["string", "null"]},
            "publication_outcome": {"bsonType": ["object", "null"]},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
    ),
    "coding_jobs": _object_schema(
        [
            "schema_version",
            "run_id",
            "project",
            "submission_idempotency_key",
            "status",
            "request_fingerprint",
            "request",
            "deadline_at",
            "attempt",
            "worker_id",
            "lease_expires_at",
            "cancel_requested_at",
            "result",
            "outcome",
            "result_fingerprint",
            "failure",
            "input_request",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "consumed_at",
            "consumed_by_turn_id",
        ],
        {
            "schema_version": {"enum": ["2"]},
            "run_id": {"bsonType": "string"},
            "project": {"bsonType": "string"},
            "submission_idempotency_key": {"bsonType": "string", "maxLength": 256},
            "status": {
                "enum": [
                    "queued",
                    "running",
                    "completed",
                    "failed",
                    "timed_out",
                    "cancelled",
                    "needs_input",
                ]
            },
            "request_fingerprint": {"bsonType": "string"},
            "request": {
                "bsonType": "object",
                "required": [
                    "schema_version",
                    "job_id",
                    "run_id",
                    "conversation_id",
                    "source_turn_id",
                    "submission_idempotency_key",
                    "project",
                    "repository",
                    "repository_revision",
                    "objective",
                    "mode",
                    "harness",
                    "content_revision",
                    "selected_skill_lock",
                    "deadline_at",
                ],
                "additionalProperties": False,
                "properties": {
                    "schema_version": {"enum": ["1"]},
                    "job_id": {"bsonType": "string"},
                    "run_id": {"bsonType": "string"},
                    "conversation_id": {"bsonType": "string"},
                    "source_turn_id": {"bsonType": "string"},
                    "submission_idempotency_key": {"bsonType": "string"},
                    "project": {"bsonType": "string"},
                    "repository": {"bsonType": "string"},
                    "repository_revision": {"bsonType": "string"},
                    "objective": {"bsonType": "string"},
                    "mode": {"enum": ["fix"]},
                    "harness": {"enum": ["mock"]},
                    "content_revision": {"bsonType": "string"},
                    "selected_skill_lock": {"bsonType": "object"},
                    "planning_citations": {"bsonType": "array", "maxItems": 20},
                    "deadline_at": {"bsonType": "string"},
                },
            },
            "deadline_at": {"bsonType": "date"},
            "attempt": {"bsonType": "int", "minimum": 0},
            "worker_id": {"bsonType": ["string", "null"], "maxLength": 128},
            "lease_expires_at": {"bsonType": ["date", "null"]},
            "cancel_requested_at": {"bsonType": ["date", "null"]},
            "result": {"bsonType": ["object", "null"]},
            "outcome": {"enum": [None, "fixed", "no_fix_found", "unsafe_to_proceed"]},
            "result_fingerprint": {"bsonType": ["string", "null"]},
            "failure": {"bsonType": ["object", "null"]},
            "input_request": {"bsonType": ["object", "null"]},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
            "started_at": {"bsonType": ["date", "null"]},
            "completed_at": {"bsonType": ["date", "null"]},
            "consumed_at": {"bsonType": ["date", "null"]},
            "consumed_by_turn_id": {"bsonType": ["string", "null"]},
        },
    ),
    "schema_migrations": _object_schema(
        ["schema_version", "applied_at"],
        {
            "schema_version": {"bsonType": "int"},
            "applied_at": {"bsonType": "date"},
        },
    ),
}

_NULL = {"bsonType": "null"}
JOB_COLLECTION_VALIDATORS["runs"]["$jsonSchema"]["oneOf"] = [
    {
        "properties": {
            "status": {"enum": ["waiting_for_jobs"]},
            "claim_expires_at": _NULL,
            "completion_job_id": _NULL,
            "completion_attempt": _NULL,
            "completion_fingerprint": _NULL,
            "resume_turn_id": _NULL,
            "memory_id": _NULL,
            "publication_outcome": _NULL,
        }
    },
    {
        "properties": {
            "status": {"enum": ["running"]},
            "claim_generation": {"bsonType": "int", "minimum": 1},
            "claim_expires_at": {"bsonType": "date"},
            "completion_job_id": {"bsonType": "string"},
            "completion_attempt": {"bsonType": "int", "minimum": 0},
            "completion_fingerprint": _NULL,
            "resume_turn_id": _NULL,
            "memory_id": _NULL,
            "publication_outcome": _NULL,
        }
    },
    {
        "properties": {
            "status": {"enum": ["completed", "failed", "timed_out", "cancelled", "needs_input"]},
            "claim_generation": {"bsonType": "int", "minimum": 1},
            "claim_expires_at": _NULL,
            "completion_job_id": {"bsonType": "string"},
            "completion_attempt": {"bsonType": "int", "minimum": 0},
            "completion_fingerprint": {"bsonType": "string"},
            "resume_turn_id": {"bsonType": "string"},
            "publication_outcome": {"bsonType": "object"},
        }
    },
]
_TERMINAL_BASE = {
    "worker_id": _NULL,
    "lease_expires_at": _NULL,
    "completed_at": {"bsonType": "date"},
}
_UNCONSUMED = {"consumed_at": _NULL, "consumed_by_turn_id": _NULL}
JOB_COLLECTION_VALIDATORS["coding_jobs"]["$jsonSchema"]["oneOf"] = [
    {
        "properties": {
            "status": {"enum": ["queued"]},
            "worker_id": _NULL,
            "lease_expires_at": _NULL,
            "result": _NULL,
            "outcome": _NULL,
            "result_fingerprint": _NULL,
            "failure": _NULL,
            "input_request": _NULL,
            "completed_at": _NULL,
            **_UNCONSUMED,
        }
    },
    {
        "properties": {
            "status": {"enum": ["running"]},
            "attempt": {"bsonType": "int", "minimum": 1},
            "worker_id": {"bsonType": "string"},
            "lease_expires_at": {"bsonType": "date"},
            "result": _NULL,
            "outcome": _NULL,
            "result_fingerprint": _NULL,
            "failure": _NULL,
            "input_request": _NULL,
            "completed_at": _NULL,
            **_UNCONSUMED,
        }
    },
    {
        "properties": {
            "status": {"enum": ["completed"]},
            **_TERMINAL_BASE,
            "result": {"bsonType": "object"},
            "outcome": {"enum": ["fixed", "no_fix_found", "unsafe_to_proceed"]},
            "result_fingerprint": {"bsonType": "string"},
            "failure": _NULL,
            "input_request": _NULL,
        }
    },
    {
        "properties": {
            "status": {"enum": ["failed"]},
            **_TERMINAL_BASE,
            "result": _NULL,
            "outcome": _NULL,
            "result_fingerprint": _NULL,
            "failure": {"bsonType": "object"},
            "input_request": _NULL,
        }
    },
    {
        "properties": {
            "status": {"enum": ["needs_input"]},
            **_TERMINAL_BASE,
            "result": _NULL,
            "outcome": _NULL,
            "result_fingerprint": _NULL,
            "failure": _NULL,
            "input_request": {"bsonType": "object"},
        }
    },
    {
        "properties": {
            "status": {"enum": ["timed_out", "cancelled"]},
            **_TERMINAL_BASE,
            "result": _NULL,
            "outcome": _NULL,
            "result_fingerprint": _NULL,
            "failure": _NULL,
            "input_request": _NULL,
        }
    },
]
JOB_COLLECTION_VALIDATORS["coding_jobs"]["$jsonSchema"]["allOf"] = [
    {
        "oneOf": [
            {"properties": _UNCONSUMED},
            {
                "properties": {
                    "consumed_at": {"bsonType": "date"},
                    "consumed_by_turn_id": {"bsonType": "string"},
                }
            },
        ]
    }
]


class MongoCodingJobRepository:
    """Transactional submissions and fenced one-document job transitions."""

    def __init__(self, database: AsyncDatabase[Document]) -> None:
        self._database = database

    async def migrate(self, now: datetime | None = None) -> None:
        applied_at = now or datetime.now(UTC)
        existing = set(await self._database.list_collection_names())
        for name, validator in JOB_COLLECTION_VALIDATORS.items():
            if name not in existing:
                await self._database.create_collection(
                    name,
                    validator=validator,
                    validationLevel="strict",
                    validationAction="error",
                )
            else:
                if name in {"runs", "coding_jobs"}:
                    await self._database.command(
                        {
                            "collMod": name,
                            "validator": {"$jsonSchema": {"bsonType": "object"}},
                            "validationLevel": "strict",
                            "validationAction": "error",
                        }
                    )
                    if name == "runs":
                        await self._database[name].update_many(
                            {"schema_version": "1"},
                            {
                                "$set": {
                                    "schema_version": "2",
                                    "claim_generation": 0,
                                    "claim_expires_at": None,
                                    "completion_job_id": None,
                                    "completion_attempt": None,
                                    "completion_fingerprint": None,
                                    "resume_turn_id": None,
                                    "memory_id": None,
                                    "publication_outcome": None,
                                }
                            },
                        )
                    else:
                        await self._database[name].update_many(
                            {"schema_version": "1"},
                            {
                                "$set": {
                                    "schema_version": "2",
                                    "consumed_at": None,
                                    "consumed_by_turn_id": None,
                                }
                            },
                        )
                await self._database.command(
                    {
                        "collMod": name,
                        "validator": validator,
                        "validationLevel": "strict",
                        "validationAction": "error",
                    }
                )
        await self._database["coding_jobs"].create_index(
            [("project", ASCENDING), ("submission_idempotency_key", ASCENDING)],
            name="project_submission_unique",
            unique=True,
        )
        await self._database["runs"].create_index(
            [("conversation_id", ASCENDING), ("source_turn_id", ASCENDING)],
            name="conversation_source_turn",
        )
        await self._database["coding_jobs"].create_index(
            [("run_id", ASCENDING), ("created_at", ASCENDING)],
            name="run_created",
        )
        await self._database["coding_jobs"].create_index(
            [("status", ASCENDING), ("deadline_at", ASCENDING), ("created_at", ASCENDING)],
            name="claimable_deadline_created",
        )
        await self._database["coding_jobs"].create_index(
            [("status", ASCENDING), ("lease_expires_at", ASCENDING)],
            name="running_lease_expiration",
        )
        await self._database["coding_jobs"].create_index(
            [
                ("status", ASCENDING),
                ("consumed_at", ASCENDING),
                ("completed_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="terminal_unconsumed_completion",
        )
        await self._database["schema_migrations"].update_one(
            {"_id": "runtime-jobs-schema-v1"},
            {"$setOnInsert": {"schema_version": 1, "applied_at": applied_at}},
            upsert=True,
        )
        await self._database["schema_migrations"].update_one(
            {"_id": "runtime-jobs-schema-v2"},
            {"$setOnInsert": {"schema_version": 2, "applied_at": applied_at}},
            upsert=True,
        )

    async def submit(self, request: CodingJobRequest, now: datetime) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        fingerprint = coding_job_request_fingerprint(request)
        run_document = _run_document(request, fingerprint, now)
        job_document = _job_document(request, fingerprint, now)
        _assert_document_size(run_document)
        _assert_document_size(job_document)

        async def operation(session: Any) -> CodingRunSnapshot:
            existing_job = await self._database["coding_jobs"].find_one(
                {
                    "project": request.project,
                    "submission_idempotency_key": request.submission_idempotency_key,
                },
                session=session,
            )
            if existing_job is not None:
                return await self._replay_submission(existing_job, fingerprint, session)
            conflicting_job = await self._database["coding_jobs"].find_one(
                {"_id": request.job_id}, session=session
            )
            if conflicting_job is not None:
                raise CodingJobConflictError("job_id was already used")
            run = await self._database["runs"].find_one({"_id": request.run_id}, session=session)
            if run is None:
                await self._database["runs"].insert_one(run_document, session=session)
                run = run_document
            elif (
                run.get("project") != request.project
                or run.get("conversation_id") != request.conversation_id
                or run.get("source_turn_id") != request.source_turn_id
            ):
                raise CodingJobConflictError("run_id belongs to a different workflow source")
            await self._database["coding_jobs"].insert_one(job_document, session=session)
            persisted_run = await self._database["runs"].find_one(
                {"_id": request.run_id}, session=session
            )
            persisted_job = await self._database["coding_jobs"].find_one(
                {"_id": request.job_id}, session=session
            )
            if persisted_run is None or persisted_job is None:
                raise RuntimeError("submitted coding job was not readable in its transaction")
            return _snapshot(persisted_run, persisted_job)

        for retry in range(2):
            try:
                async with self._database.client.start_session() as session:
                    return cast(CodingRunSnapshot, await session.with_transaction(operation))
            except DuplicateKeyError:
                existing = await self._database["coding_jobs"].find_one(
                    {
                        "project": request.project,
                        "submission_idempotency_key": request.submission_idempotency_key,
                    }
                )
                if existing is not None:
                    return await self._replay_submission(existing, fingerprint, None)
                if retry == 1:
                    raise CodingJobConflictError("job_id was already used") from None
        raise AssertionError("unreachable")

    async def claim(
        self, worker_id: str, now: datetime, expires_at: datetime
    ) -> CodingJobClaim | None:
        _validate_lease_window(worker_id, now, expires_at)
        await self.reconcile(now)
        job = await self._database["coding_jobs"].find_one_and_update(
            {
                "status": CodingJobStatus.QUEUED.value,
                "cancel_requested_at": None,
                "deadline_at": {"$gt": now},
            },
            {
                "$set": {
                    "status": CodingJobStatus.RUNNING.value,
                    "worker_id": worker_id,
                    "lease_expires_at": expires_at,
                    "started_at": now,
                    "updated_at": now,
                },
                "$inc": {"attempt": 1},
            },
            sort=[("created_at", ASCENDING), ("_id", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        if job is None:
            return None
        deadline = cast(datetime, job["deadline_at"])
        if cast(datetime, job["lease_expires_at"]) > deadline:
            shortened = await self._database["coding_jobs"].find_one_and_update(
                {"_id": job["_id"], "status": "running", "attempt": job["attempt"]},
                {"$set": {"lease_expires_at": deadline}},
                return_document=ReturnDocument.AFTER,
            )
            if shortened is not None:
                job = shortened
        return _claim_from(job)

    async def renew(
        self, claim: CodingJobClaim, now: datetime, expires_at: datetime
    ) -> CodingJobClaim:
        _validate_lease_window(claim.worker_id, now, expires_at)
        bounded_expiry = min(expires_at, claim.request.deadline_at)
        job = await self._database["coding_jobs"].find_one_and_update(
            self._active_claim_query(claim, now),
            {"$set": {"lease_expires_at": bounded_expiry, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if job is None:
            await self.reconcile(now)
            raise StaleCodingJobClaimError("coding job claim is stale or expired")
        return _claim_from(job)

    async def complete(
        self, claim: CodingJobClaim, result: CodingJobResult, now: datetime
    ) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        if result.job_id != claim.job_id:
            raise CodingJobConflictError("result belongs to a different job")
        result_document = result.model_dump(mode="json")
        _assert_document_size(result_document)
        fingerprint = coding_job_result_fingerprint(result)
        existing_for_size = await self._database["coding_jobs"].find_one({"_id": claim.job_id})
        if existing_for_size is None:
            raise CodingJobNotFoundError(claim.job_id)
        prospective = dict(existing_for_size)
        prospective.update(
            {
                "result": result_document,
                "outcome": result.outcome.value,
                "result_fingerprint": fingerprint,
            }
        )
        _assert_document_size(prospective)
        job = await self._database["coding_jobs"].find_one_and_update(
            self._active_claim_query(claim, now),
            {
                "$set": {
                    "status": CodingJobStatus.COMPLETED.value,
                    "worker_id": None,
                    "lease_expires_at": None,
                    "result": result_document,
                    "outcome": result.outcome.value,
                    "result_fingerprint": fingerprint,
                    "updated_at": now,
                    "completed_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if job is None:
            existing = await self._database["coding_jobs"].find_one({"_id": claim.job_id})
            if (
                existing is not None
                and existing.get("status") == CodingJobStatus.COMPLETED.value
                and existing.get("attempt") == claim.attempt
            ):
                if existing.get("result_fingerprint") != fingerprint:
                    raise CodingJobConflictError("completed attempt has a different result")
                return await self._snapshot_for(existing)
            await self.reconcile(now)
            raise StaleCodingJobClaimError("coding job claim is stale or expired")
        return await self._snapshot_for(job)

    async def fail(
        self, claim: CodingJobClaim, failure: CodingJobFailure, now: datetime
    ) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        return await self._terminal_claim_update(
            claim,
            now,
            CodingJobStatus.FAILED,
            {"failure": failure.model_dump(mode="json")},
        )

    async def request_input(
        self, claim: CodingJobClaim, request: CodingJobInputRequest, now: datetime
    ) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        return await self._terminal_claim_update(
            claim,
            now,
            CodingJobStatus.NEEDS_INPUT,
            {"input_request": request.model_dump(mode="json")},
        )

    async def cancel(self, job_id: str, now: datetime) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        job = await self._database["coding_jobs"].find_one_and_update(
            {"_id": job_id, "status": {"$in": ["queued", "running"]}},
            {
                "$set": {
                    "status": CodingJobStatus.CANCELLED.value,
                    "cancel_requested_at": now,
                    "worker_id": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                    "completed_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if job is None:
            existing = await self._database["coding_jobs"].find_one({"_id": job_id})
            if existing is None:
                raise CodingJobNotFoundError(job_id)
            return await self._snapshot_for(existing)
        return await self._snapshot_for(job)

    async def reconcile(self, now: datetime, *, limit: int = 100) -> int:
        _validate_timestamp("now", now)
        if limit < 1 or limit > 1_000:
            raise ValueError("reconcile limit must be between 1 and 1000")
        candidates = (
            await self._database["coding_jobs"]
            .find(
                {
                    "status": {"$in": ["queued", "running"]},
                    "$or": [
                        {"cancel_requested_at": {"$ne": None}},
                        {"deadline_at": {"$lte": now}},
                        {"status": "running", "lease_expires_at": {"$lte": now}},
                    ],
                }
            )
            .sort([("updated_at", ASCENDING), ("_id", ASCENDING)])
            .limit(limit)
            .to_list(length=limit)
        )
        changed = 0
        for candidate in candidates:
            status: CodingJobStatus
            terminal = False
            if candidate.get("cancel_requested_at") is not None:
                status = CodingJobStatus.CANCELLED
                terminal = True
            elif cast(datetime, candidate["deadline_at"]) <= now:
                status = CodingJobStatus.TIMED_OUT
                terminal = True
            else:
                status = CodingJobStatus.QUEUED
            update: Document = {
                "status": status.value,
                "worker_id": None,
                "lease_expires_at": None,
                "updated_at": now,
            }
            if terminal:
                update["completed_at"] = now
            result = await self._database["coding_jobs"].update_one(
                {
                    "_id": candidate["_id"],
                    "status": candidate["status"],
                    "attempt": candidate["attempt"],
                    "updated_at": candidate["updated_at"],
                },
                {"$set": update},
            )
            changed += int(result.matched_count)
        return changed

    async def read(self, job_id: str) -> CodingRunSnapshot | None:
        job = await self._database["coding_jobs"].find_one({"_id": job_id})
        if job is None:
            return None
        return await self._snapshot_for(job)

    async def claim_terminal_run(
        self, now: datetime, expires_at: datetime, *, limit: int = 100
    ) -> RunConsumptionClaim | None:
        _validate_consumption_window(now, expires_at, limit)
        cursor = await self._database["coding_jobs"].aggregate(
            [
                {
                    "$match": {
                        "status": {
                            "$in": [
                                "completed",
                                "failed",
                                "timed_out",
                                "cancelled",
                                "needs_input",
                            ]
                        },
                        "consumed_at": None,
                    }
                },
                {"$sort": {"completed_at": 1, "_id": 1}},
                # Bound server-side work before joining against runs. A live claim in this small
                # page may delay later work only until its short non-renewed expiry.
                {"$limit": limit},
                {
                    "$lookup": {
                        "from": "runs",
                        "localField": "run_id",
                        "foreignField": "_id",
                        "as": "eligible_run",
                    }
                },
                {"$unwind": "$eligible_run"},
                {
                    "$match": {
                        "$or": [
                            {"eligible_run.status": RunStatus.WAITING_FOR_JOBS.value},
                            {
                                "eligible_run.status": RunStatus.RUNNING.value,
                                "eligible_run.claim_expires_at": {"$lte": now},
                            },
                        ]
                    }
                },
                {
                    "$lookup": {
                        "from": "coding_jobs",
                        "let": {"candidate_run_id": "$run_id"},
                        "pipeline": [
                            {"$match": {"$expr": {"$eq": ["$run_id", "$$candidate_run_id"]}}},
                            {"$limit": 2},
                            {"$project": {"_id": 1}},
                        ],
                        "as": "run_jobs",
                    }
                },
                {"$project": {"eligible_run": 0}},
            ]
        )
        candidates = await cursor.to_list(length=limit)
        for candidate in candidates:
            if len(candidate.pop("run_jobs", [])) != 1:
                continue
            candidate_id = candidate["_id"]

            async def operation(
                session: Any, candidate_id: object = candidate_id
            ) -> RunConsumptionClaim | None:
                job = await self._database["coding_jobs"].find_one(
                    {"_id": candidate_id, "consumed_at": None}, session=session
                )
                if job is None or job["status"] not in {
                    "completed",
                    "failed",
                    "timed_out",
                    "cancelled",
                    "needs_input",
                }:
                    return None
                run = await self._database["runs"].find_one_and_update(
                    {
                        "_id": job["run_id"],
                        "$or": [
                            {"status": RunStatus.WAITING_FOR_JOBS.value},
                            {
                                "status": RunStatus.RUNNING.value,
                                "claim_expires_at": {"$lte": now},
                            },
                        ],
                    },
                    {
                        "$set": {
                            "status": RunStatus.RUNNING.value,
                            "claim_expires_at": expires_at,
                            "completion_job_id": job["_id"],
                            "completion_attempt": job["attempt"],
                            "updated_at": now,
                        },
                        "$inc": {"claim_generation": 1},
                    },
                    session=session,
                    return_document=ReturnDocument.AFTER,
                )
                if run is None:
                    return None
                return RunConsumptionClaim(
                    run_id=str(run["_id"]),
                    job=_snapshot(run, job).job,
                    generation=int(run["claim_generation"]),
                    expires_at=expires_at,
                )

            async with self._database.client.start_session() as session:
                claim = cast(RunConsumptionClaim | None, await session.with_transaction(operation))
            if claim is not None:
                return claim
        return None

    async def finish_consumption(
        self,
        claim: RunConsumptionClaim,
        *,
        status: RunStatus,
        resume_turn_id: str,
        completion_fingerprint: str,
        publication_outcome: MockPublicationOutcome,
        memory_id: str | None,
        now: datetime,
    ) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        _validate_consumption_result(claim, status, completion_fingerprint, publication_outcome)

        async def operation(session: Any) -> CodingRunSnapshot:
            run = await self._database["runs"].find_one({"_id": claim.run_id}, session=session)
            job = await self._database["coding_jobs"].find_one(
                {"_id": claim.job.job_id}, session=session
            )
            if run is None or job is None:
                raise CodingJobNotFoundError(claim.job.job_id)
            if run["status"] not in {
                RunStatus.WAITING_FOR_JOBS.value,
                RunStatus.RUNNING.value,
            }:
                expected_publication = publication_outcome.model_dump(mode="json")
                if (
                    run.get("status") == status.value
                    and run.get("resume_turn_id") == resume_turn_id
                    and run.get("completion_fingerprint") == completion_fingerprint
                    and run.get("publication_outcome") == expected_publication
                    and run.get("memory_id") == memory_id
                    and job.get("consumed_by_turn_id") == resume_turn_id
                ):
                    return _snapshot(run, job)
                raise StaleRunConsumptionClaimError("run completion was already consumed")
            if (
                run["status"] != RunStatus.RUNNING.value
                or run["claim_generation"] != claim.generation
                or run["claim_expires_at"] <= now
                or run["completion_job_id"] != job["_id"]
                or run["completion_attempt"] != job["attempt"]
                or job["consumed_at"] is not None
            ):
                raise StaleRunConsumptionClaimError("run consumption claim is stale or expired")
            job_result = await self._database["coding_jobs"].update_one(
                {"_id": job["_id"], "consumed_at": None},
                {"$set": {"consumed_at": now, "consumed_by_turn_id": resume_turn_id}},
                session=session,
            )
            run_result = await self._database["runs"].update_one(
                {
                    "_id": claim.run_id,
                    "status": RunStatus.RUNNING.value,
                    "claim_generation": claim.generation,
                },
                {
                    "$set": {
                        "status": status.value,
                        "claim_expires_at": None,
                        "completion_fingerprint": completion_fingerprint,
                        "resume_turn_id": resume_turn_id,
                        "memory_id": memory_id,
                        "publication_outcome": publication_outcome.model_dump(mode="json"),
                        "updated_at": now,
                    }
                },
                session=session,
            )
            if job_result.modified_count != 1 or run_result.modified_count != 1:
                raise StaleRunConsumptionClaimError("completion consumption lost its claim")
            stored_run = await self._database["runs"].find_one(
                {"_id": claim.run_id}, session=session
            )
            stored_job = await self._database["coding_jobs"].find_one(
                {"_id": claim.job.job_id}, session=session
            )
            if stored_run is None or stored_job is None:
                raise RuntimeError("consumed completion was not readable")
            return _snapshot(stored_run, stored_job)

        async with self._database.client.start_session() as session:
            return cast(CodingRunSnapshot, await session.with_transaction(operation))

    async def _terminal_claim_update(
        self,
        claim: CodingJobClaim,
        now: datetime,
        status: CodingJobStatus,
        fields: Document,
    ) -> CodingRunSnapshot:
        existing = await self._database["coding_jobs"].find_one({"_id": claim.job_id})
        if existing is None:
            raise CodingJobNotFoundError(claim.job_id)
        prospective = dict(existing)
        prospective.update(fields)
        _assert_document_size(prospective)
        job = await self._database["coding_jobs"].find_one_and_update(
            self._active_claim_query(claim, now),
            {
                "$set": {
                    "status": status.value,
                    "worker_id": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                    "completed_at": now,
                    **fields,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if job is None:
            await self.reconcile(now)
            raise StaleCodingJobClaimError("coding job claim is stale or expired")
        return await self._snapshot_for(job)

    @staticmethod
    def _active_claim_query(claim: CodingJobClaim, now: datetime) -> Document:
        return {
            "_id": claim.job_id,
            "run_id": claim.run_id,
            "status": CodingJobStatus.RUNNING.value,
            "worker_id": claim.worker_id,
            "attempt": claim.attempt,
            "cancel_requested_at": None,
            "lease_expires_at": {"$gt": now},
            "deadline_at": {"$gt": now},
        }

    async def _replay_submission(
        self, existing_job: Document, fingerprint: str, session: Any
    ) -> CodingRunSnapshot:
        if existing_job.get("request_fingerprint") != fingerprint:
            raise CodingJobConflictError(
                "submission_idempotency_key was already used for another request"
            )
        run = await self._database["runs"].find_one(
            {"_id": existing_job["run_id"]}, session=session
        )
        if run is None:
            raise RuntimeError("coding job references a missing run")
        return _snapshot(run, existing_job)

    async def _snapshot_for(self, job: Document) -> CodingRunSnapshot:
        run = await self._database["runs"].find_one({"_id": job["run_id"]})
        if run is None:
            raise RuntimeError("coding job references a missing run")
        return _snapshot(run, job)
