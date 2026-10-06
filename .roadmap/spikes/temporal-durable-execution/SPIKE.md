# Spike — Temporal Durable Execution

## Status

- Type: parallel investigation; not an implementation slice
- Decision state: open
- Does not block S13
- Owner: a separate Codex session
- Output: evidence-backed adopt, defer, or reject recommendation

## Reader and expected outcome

This brief is for an engineer or Codex session with no prior conversation history. After reading it,
the investigator should be able to compare the current MongoDB-owned workflow with a small Temporal
prototype and recommend whether this project should adopt Temporal, reconsider it at a named future
boundary, or keep the existing approach.

Do not replace the current implementation during this spike. Keep experimental code isolated from
production modules and do not change the roadmap status to claim adoption.

## The problem, simply

The agent performs work that may take minutes or hours:

1. Ask Genkit to plan a cited coding task.
2. Start a disposable coding job.
3. Wait while another worker runs it.
4. Recover if a process or container stops unexpectedly.
5. Resume the conversation once, even if completion is delivered more than once.
6. Sometimes wait for developer input, cancellation, or a later retry.

S12 implements this safely with MongoDB records, polling, expiring claims, generation fencing,
deterministic identifiers, idempotency keys, and final transactions. That works, but each new kind
of waiting, retry, parallel branch, or human interaction adds orchestration code that this project
must design and maintain.

Temporal may solve that orchestration problem. It records workflow history and replays deterministic
workflow code after worker failure. External operations run as Activities with explicit retry and
timeout policies. Signals and Updates can deliver events such as job completion, cancellation, or
developer input. The question is whether those benefits justify another service, worker runtime,
deployment model, and workflow-versioning discipline for this team-sized agent kit.

## Current S12 state machine

The workflow and coding job have separate lifecycles.

```text
cited planning turn
        |
        v
run: waiting_for_jobs
        |
        | terminal coding job becomes available
        v
run: running               short completion-consumer claim
        |
        | memory suggestion, deterministic conversation resume,
        | atomic job-consumed/run-final transaction
        v
run: completed | failed | timed_out | cancelled | needs_input
```

The coding job independently moves through `queued`, `running`, and a terminal state. `run: running`
does not mean the coding container is running; it means a consumer is projecting an already-terminal
job back into the conversation.

Important implemented behavior:

- MongoDB is the durable execution authority for runs and coding jobs.
- Coding-job execution and completion consumption use separate claims.
- Job completion identity includes job ID, attempt, lifecycle, outcome, and result payload.
- The conversation resume turn has a deterministic ID.
- S12 may explicitly retry a retriable failed resume under that same ID with a fresh sequence.
- The final transaction marks the job consumed and the run terminal together.
- Supplemental memory and the mock publication result use deterministic identities.
- The S12 consumer handles one job per run. Multi-job joins are deliberately deferred.
- Genkit performs reasoning; it does not own durable workflow state.

## What a Temporal version might look like

The prototype should express the same observable behavior, not invent a new product flow.

```text
Temporal Workflow
  -> Activity: execute cited Genkit planning turn
  -> Activity: submit or start one pinned coding job
  -> wait: coding-job completion Signal, Update, or bounded polling Activity
  -> Activity: validate completion and optionally create supplemental memory
  -> Activity: resume the durable conversation
  -> Activity: persist the final searchable business projection
  -> complete workflow
```

Temporal would be the execution authority. MongoDB would remain useful for conversations, coding-job
artifacts, shared memory, operational queries, and business projections, but it should not run a
second competing workflow state machine.

Genkit remains the reasoning layer. Git remains the canonical source of team knowledge and skills.
Disposable coding environments remain isolated and do not receive MongoDB or publication
credentials.

## Hypothesis

Temporal is valuable if it materially reduces custom recovery and coordination code for future
long-running jobs, human-input waits, cancellation, schedules, and parallel joins without obscuring
the simple team-owned development model.

Temporal is not valuable yet if the prototype merely moves the existing state machine into another
platform while MongoDB, idempotency handling, container supervision, and operational complexity all
remain unchanged.

## Required context and reading order

Read these project artifacts before experimenting:

1. `docs/domain-knowledge-authority.md` — non-negotiable Git-authority and trusted-team dogma.
2. `.roadmap/team-agent-architecture-brief-genkit.md` — canonical architecture, especially durable
   coding jobs, container lifecycle, completion/resume, and bounded workflows.
3. `.roadmap/workstreams/README.md` — session and decision protocol.
4. `.roadmap/workstreams/S10-durable-genkit-conversations/SUMMARY.md` — application-owned
   conversation history and replay behavior.
5. `.roadmap/workstreams/S11-durable-mock-coding-jobs/SUMMARY.md` — job leases, attempts, terminal
   records, and the runner/supervisor boundary.
6. `.roadmap/workstreams/S12-mock-diagnose-fix/BRIEF.md` and `SUMMARY.md` — current workflow contract
   and verified implementation.
7. `.roadmap/workstreams/S13-supervised-codex/BRIEF.md` — the next executor boundary that Temporal
   must not accidentally broaden.
8. `docs/data-model.md` — current MongoDB records and field purposes.
9. `.agents/skills/developing-genkit-python/SKILL.md` — Genkit Python guidance. Temporal must
   complement Genkit rather than replace it.

Inspect the runtime conversation, coding-job, MongoDB repository, and diagnose-and-fix modules after
reading the documents. Follow their public contracts rather than copying internal implementation
details into the prototype.

Use current official Temporal documentation as the external source of truth:

- [Temporal overview](https://docs.temporal.io/temporal)
- [Workflow definitions and determinism](https://docs.temporal.io/workflow-definition)
- [Tasks, replay, Activities, and retries](https://docs.temporal.io/tasks)
- [Python SDK guide](https://docs.temporal.io/develop/python)
- [Signals, Queries, and Updates](https://docs.temporal.io/encyclopedia/workflow-message-passing)
- [Durable AI patterns](https://docs.temporal.io/ai)

## Questions the spike must answer

1. Which existing mechanisms disappear if Temporal becomes the execution authority?
2. Which mechanisms remain necessary, especially external-effect idempotency and container
   supervision?
3. Can one workflow survive worker termination at every S12 persisted boundary without duplicate
   conversation turns, memory, job submission, or publication outcome?
4. How should a terminal coding job notify the workflow: Signal, Update, asynchronous Activity
   completion, or a polling Activity? Why?
5. How are `needs_input`, cancellation, timeout, and later developer responses represented?
6. How would S13 supervise a Docker or Kubernetes job without putting Docker, Kubernetes, MongoDB,
   or Git credentials inside deterministic workflow code?
7. What becomes the authoritative execution record, and what MongoDB data remains a projection?
8. How are existing open workflows upgraded when workflow code changes?
9. What are the self-hosted and managed-service operational requirements for a small team?
10. At what roadmap boundary would adoption save more complexity than it introduces?

## Required experiment

Keep the investigation bounded to one focused Codex session. Build the thinnest possible proof in a
spike-only directory rather than adding Temporal to production package entry points:

- One Temporal workflow representing the S12 sequence.
- Activities may use fakes or adapters around current public contracts.
- One simulated coding job with a deterministic terminal result.
- One `needs_input` path resumed by a Temporal message.
- One worker-crash/restart demonstration while waiting.
- One Activity retry demonstration proving that a repeated attempt does not duplicate an external
  effect.
- One workflow-replay or versioning test that shows what happens after workflow code changes.
- A clear MongoDB ownership sketch for a Temporal-backed design.

Do not build a real Codex container runner, Git publisher, Slack integration, generalized agent
framework, or production deployment in this spike.

If a runnable Temporal service or SDK cannot be used in the session, stop after documenting the
exact blocker and complete the ownership model, API sketch, failure comparison, and recommendation.
Do not turn environment setup into an unbounded infrastructure project.

## Evaluation criteria

Compare the current implementation and the prototype using concrete evidence:

| Criterion | What to measure |
|---|---|
| Recovery correctness | Crash points recovered without missing or duplicate visible effects |
| Code and concepts | Custom claim, polling, retry, timer, and join logic removed versus added adapters |
| Operational burden | Services, workers, persistence, local development, CI, upgrades, and observability |
| Data ownership | One clear execution authority; no ambiguous MongoDB/Temporal dual truth |
| Change safety | Workflow versioning and replay compatibility requirements |
| Portability | Local Docker, Kubernetes, GitHub/GitLab neutrality, and self-hosted feasibility |
| Team usability | Ability for a small domain team to understand, debug, and contribute |
| Future fit | Human waits, cancellation, schedules, parallel review, and multi-job joins |

## Deliverables

Return all of the following:

1. A short `FINDINGS.md` beside this brief.
2. A state-machine comparison showing Mongo-owned S12 and Temporal-owned S12.
3. A responsibility table for Genkit, Temporal, MongoDB, Git, the executor, and runner containers.
4. Prototype code and tests isolated from production code, or a written explanation if a runnable
   proof is blocked.
5. Verification commands and observed results.
6. An explicit recommendation: `adopt now`, `defer until <named slice/condition>`, or `reject`.
7. If adoption is recommended, a migration sequence that preserves the current public contracts and
   avoids running two authoritative state machines.

## Decision guardrails

- Do not treat Temporal as an agent framework or substitute for Genkit.
- Do not move canonical knowledge or skills out of Git.
- Do not give coding containers direct access to Temporal, MongoDB, or publication credentials.
- Do not claim exactly-once external execution. Activities and external APIs still require
  idempotency.
- Do not retain both MongoDB and Temporal as independent workflow authorities.
- Do not recommend adoption solely because the prototype is elegant; include deployment and team
  costs.
- Do not modify S13 or later production slices as part of the spike.

## Copy/paste kickoff for a separate session

> Run the Temporal durable-execution spike in
> `.roadmap/spikes/temporal-durable-execution/SPIKE.md`. Read every required project artifact in the
> listed order, inspect the current public runtime contracts, and use current official Temporal
> documentation. Keep experiments isolated from production modules. Produce the required
> `FINDINGS.md`, comparison artifacts, verification evidence, and an adopt/defer/reject decision.
> Do not change the active S13 roadmap or implement a production Temporal migration.
