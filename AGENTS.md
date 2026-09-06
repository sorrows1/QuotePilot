# QuotePilot engineering policy

This file is the canonical, vendor-neutral implementation-worker policy for this repository. Task-specific scope and acceptance criteria belong in the current implementation packet, not here.

## Working rules

- Inspect the current repository and applicable task/ADR context before changing code.
- Make the smallest coherent change that satisfies current requirements. Prefer KISS and YAGNI over speculative layers, services, extension points, or dependencies.
- Apply SOLID pragmatically: optimize for cohesion and clear boundaries, not mechanical indirection. Remove duplicated business knowledge; tolerate small local repetition when an abstraction is not yet stable.
- Keep unrelated refactors out of task diffs. Before completion, inspect the entire diff for accidental files, debug code, dead code, secrets, and unnecessary complexity.
- Never commit credentials, tokens, personal paths, caches, local agent state, or generated artifacts that are not intentionally part of the product.

## Architecture boundaries

- `apps/web` owns presentation and browser interaction. React components must not become the authoritative home of pricing, authorization, workflow, or other business invariants.
- `apps/server` owns HTTP boundaries and backend application behavior. Keep transport handlers thin as domain behavior grows.
- PostgreSQL is the authoritative operational datastore for persistent business truth. Derived indexes or caches must not become an independent source of truth.
- Introduce shared packages or interfaces only for a demonstrated current boundary. External/replaceable dependencies may use ports when substitution or volatility is real; do not create interfaces for every class.
- Validate and type external, model, tool, and HTTP inputs at trust boundaries.

## Correctness and security invariants

- Tenant context must be explicit in service/repository APIs and derived server-side at trust boundaries when tenant-aware features are introduced.
- Money and commercial calculations use decimal/fixed-point database types and deterministic rules; never binary floating point.
- Authorization, policy, state transitions, and commercial rules must remain explicit and testable.
- Do not expose raw SQL or unrestricted shell execution to an LLM.
- Do not silently catch and ignore operational, security, or commercial errors. Errors should be actionable without leaking secrets or unnecessary PII.

## Dependencies and migrations

- Prefer maintained, stable libraries and commit lockfiles. Add a dependency only when it removes more complexity or risk than it adds.
- Verify version-sensitive framework/provider behavior against current authoritative documentation before relying on it.
- Use versioned database migrations. Do not edit an applied production migration in place.
- Destructive schema changes require an explicit migration/backout plan and appropriate backup; prefer expand/backfill/contract for risky changes.

## Verification

- Run `npm run check` from the repository root for the complete local frontend/server quality gate.
- Frontend focused checks live under `apps/web`; server focused checks live under `apps/server`.
- Run focused checks during implementation and the complete affected quality gate before handoff.
- Test observable behavior and invariants rather than private implementation details. Bugs should receive regression coverage where practical.
- Auth, tenant isolation, concurrency, migrations, commercial calculations, and data-integrity behavior require dedicated tests when affected.

## Review and completion

- Implementation success is not final acceptance. Deterministic gates must pass, then a fresh independent reviewer must inspect the completed diff and evidence.
- Review prioritizes correctness, regressions, security, tenant isolation, money/data integrity, concurrency/state behavior, error handling, meaningful test gaps, and unjustified architecture drift.
- Repair review findings in the same implementation task context, rerun affected gates, and obtain fresh review before merge.
- Do not claim required behavior complete while it remains unverified.
