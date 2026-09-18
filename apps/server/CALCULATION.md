# QT-007 deterministic calculation contract

`CalculationService.calculate(context, request, actor=..., pricing_as_of=...)`
is a trusted server application service. It performs read-only evaluation; there
is no new HTTP endpoint or database migration. Callers derive tenant context from
authentication. An optional Principal is used only for tenant-scope validation during
direct trusted calls; it is never treated as the negotiated-price proposer. The optional instant is for trusted historical evaluation and
testing; HTTP/model inputs must never choose it. Otherwise the server captures UTC
once. Current settings and all commercial reads share one PostgreSQL read-only
REPEATABLE READ transaction.

The request identifies a case, immutable revision and unique line IDs. It accepts
exact decimal text or Decimal, never binary floats, including nested inputs made
with Pydantic's unchecked construction/copy methods. Authority retains QT-005's
18 integer / 6 fractional digit storage bounds. Freight allows two fractional
digits. At most 1000 lines are supported per evaluation. Derived quantities and
costs are not constrained to the input scale or rounded before policy decisions.
A private 128-digit decimal context covers the bounded products, sums and policy
operands independently of caller precision, rounding, exponent limits and traps.
Only line net, quote tax and display margin are rounded at their specified stages.
Every decimal in JSON output is text.

Normal pricebook, tier and discount selection uses QT-005's exact precedence and
half-open windows. The pricing UOM is an explicit selection, verified against a
current price; no arbitrary price/UOM is inferred. Quantity conversion uses only
a directed product-specific record. Cost conversion similarly uses a direct
pricing-UOM → cost-UOM factor, never an inferred inverse or chain. Missing cost
remains unknown, including when controls are disabled. Enabled line and quote
margin controls fail closed on missing, ambiguous or undefined margin. Threshold
decisions compare exact products, never divided or displayed margin.

Negotiated pricing is a provisional result for review. QT-008's authenticated sales
edit workflow must explicitly adopt a nonnegative unit price with a nonblank reason
and bind the immutable proposer identity and proposal timestamp to the quote revision
before calling this evaluator. Product identity and normal price/policy must resolve
first. Customer/model candidate text is not part of this input contract; it cannot
create an adoption. Recalculation by a manager or background process preserves the
original proposer/reason/timestamp rather than replacing them with the recalculating
actor or evaluation instant. The result records that proposal evidence, normal
reference, absolute deviation and exact rational deviation operands. A zero reference
has no percentage deviation. No normal discount is reapplied to a
negotiated unit price, and every admitted proposal emits `NEGOTIATED_UNIT_PRICE`.
The normal policy rate remains evidence for its configured discount authority
check. An explicitly disallowed discount policy emits `DISCOUNT_NOT_PERMITTED`.

An explicit substitute is supplied only after selection by the product-resolution
workflow. Both the selected and requested products must exist and be active within
the tenant before it emits `SUBSTITUTION_PROPOSED`. Both identities are captured
as evidence. Ambiguous identity stays clarification;
missing identity stays a hard block. A manager cannot turn either into product
truth. This service never grants approval and exposes no READY flag. Output state
is CALCULATED, APPROVAL_REQUIRED, CLARIFICATION_REQUIRED or HARD_BLOCK; all findings
are retained, with hard blocks taking precedence. A calculated state alone is not
permission to publish. Missing selling authority prevents a partial quote total;
unknown cost or stale stock may coexist with calculated selling totals.

Inventory output is aggregate quantity in its recorded UOM only when fresh. There
are no warehouse, allocation, shipment or delivery promises. `availability_required`
turns missing, stale or invalid inventory into a hard block. Otherwise price
calculation continues and no stale quantity is presented as current availability.

Results retain selected record IDs (including pricebook assignment authority), source/version fields, effective windows,
conversion evidence, full settings snapshot/revision and the explicit
NO_DISCOUNT_POLICY result. `commercial_fingerprint` binds tenant, case/revision,
inputs, settings, selected authority, immutable proposal provenance and all findings. Recalculation at
a later instant produces a different fingerprint when material authority or
required availability changes. Time alone and inventory not required by the
revision are excluded. Consumers must compare freshly calculated results; a hash
is not an authentication token or a substitute for current reads.

QT-008 owns persistence and creation of a new immutable revision after material
edits. QT-020 owns revalidation before READY/publication and exact exception-set
approval/rejection. No old approval is accepted as calculation input; repeated
calculation never makes an exception approved. QT-004/QT-022 own number allocation
and document generation. These lifecycle behaviors are not implemented by QT-007.

## Acceptance trace and verification

Sources: [QT-007](https://app.notion.com/p/5f4de723e8a18294b3b7016b4ac3e869),
[COM-001](https://app.notion.com/p/dd4de723e8a183f5aa3381969f359c04),
[COM-002](https://app.notion.com/p/d36de723e8a182e58f8701271295246c),
[QA-001](https://app.notion.com/p/b5fde723e8a1838984a501c4b6708767),
[ADR-005](https://app.notion.com/p/b56de723e8a182b09cbc01fe9f5b19ac).
QA-001's live Status is Done; older review-history notes are historical.

| Rules / scenarios | Observable verification |
| --- | --- |
| COM-R01–05; GQ-002–011, 039 | Real pricebook/customer precedence, no cross-book fallback, tier/converted quantity and effective-window selection; runtime ambiguity and invalid-input tests; QT-005 overlap/storage tests remain in root gate |
| COM-R04/06; GQ-001, 012, 018–020 | Independent exact money oracles, line/tax rounding, freight/taxability, decimal JSON, ambient-context isolation, 300 seeded cases checked against integer arithmetic |
| COM-R07/09; GQ-013–016, 018–020, 024–025, 036, 038 | Line/quote strict inequalities, equality, display-hidden breach, unknown/ambiguous/zero margin, final-total authority, real versioned settings |
| COM-R08; GQ-017, 034 | Exact freshness boundary, missing/future/stale evidence, aggregate-only output and no unsupported delivery fields |
| COM2-R01–05/09; GQ-021–027, 040–041 | Authenticated adoption provenance preserved across recalculation, no double discount, exact deviation, zero reference, multiple exceptions, clarification/substitution, unresolved authority and invalid admission |
| COM-R11/12, COM2-R06–08; GQ-028–031, 033, 035 | Material fingerprints, explicit absent-policy evidence, authority rollover, real concurrent settings/policy changes in one MVCC snapshot; approval/revision lifecycle remains QT-008/QT-020 |
| GQ-032, 037 | Number allocation and durable rejection/retry belong to QT-004/QT-008/QT-020/QT-022; no claim of implementing those lifecycle scenarios here |

Run `node scripts/server.mjs pytest tests/test_calculation.py tests/test_calculation_database.py`,
then `npm run check`. Integration tests use disposable databases and do not modify
the operational database. No dependency was added. Relevant implementation
references: [Python Decimal](https://docs.python.org/3.13/library/decimal.html),
[SQLAlchemy transaction isolation](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html).
