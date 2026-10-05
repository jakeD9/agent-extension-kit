---
title: Duplicate events caused by unstable keys
project: event-ingestion
---
# Duplicate events caused by unstable keys

The sample service once included receipt time in its idempotency key. Retries therefore created duplicate processing records. The remediation removed receipt time and added replay coverage.
