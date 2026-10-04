# S14 — Grounded Slack Conversation

Depends on S10. Add signed Slack ingress, prompt acknowledgement, event receipts, identity and
thread mapping, asynchronous dispatch, authorized cited replies, and explicit failed-run messages.
Fake Slack is mandatory in CI and live checks are opt-in. Exclude coding jobs and automations.
Verify signature rejection, retry deduplication, authorization, thread continuation after restart,
same-thread serialization, and bounded user-visible failures.
