# S25 — Repository Structure Cleanup

Depends on S23 and S24. Perform a behavior-preserving structural pass after the core implementation,
hardening, and adoption documentation are complete. Separate versioned models/contracts from
orchestration, persistence, provider adapters, and executable entrypoints. Move mocks, fakes,
deterministic harnesses, fixture builders, and other test-only support into explicit test-support or
fixture directories. Reduce package `__init__.py` files to narrow stable re-exports; do not keep
substantial model definitions or runtime logic at package top level.

Begin with an import and ownership inventory. Define the desired package/module layout before moving
files, preserve intentional public imports through explicit compatibility re-exports, and update
internal imports mechanically in small batches. Apply the same organizing rules across comparable
subsystems—especially conversations and coding jobs—rather than cleaning only the newest module.

This slice must not change wire schemas, Mongo document shapes, lifecycle behavior, authorization,
provider behavior, or roadmap scope. Do not retain production-selectable mock implementations merely
for tests. Verify the full deterministic and live integration gates, package builds, entrypoints,
Docker/Compose behavior, public import compatibility, and that production packages no longer own
test fixtures or mock executors.
