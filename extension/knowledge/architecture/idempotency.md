---
title: Stable event identity
project: event-ingestion
accessGroups: [engineering]
authority: approved
---
# Stable event identity

Event idempotency keys must use the immutable vendor event identifier. Receipt timestamps and retry counters are unstable and must never contribute to event identity.

Consumers may safely replay the same vendor event. A repeated identifier must select the original processing record rather than create a duplicate.
