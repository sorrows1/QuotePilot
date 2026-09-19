"""Manual quote application behavior; callers commit one authenticated transaction."""

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import String, Table, insert, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from quotepilot_api import commercial_schema as commercial
from quotepilot_api import quote_schema as schema
from quotepilot_api.auth import Capability, Principal, audit, tenant_lock
from quotepilot_api.calculation import CalculationService, canonical
from quotepilot_api.calculation_models import CalculationLine, CalculationRequest, NegotiatedPrice
from quotepilot_api.quote_contract import Candidate, CustomerCreate, RetryInput, SaveInput
from quotepilot_api.settings import current, setup_complete


class QuoteError(Exception):
    def __init__(self, message: str, status: int = 422):
        self.message, self.status = message, status
        super().__init__(message)


def authorize(session: Session, principal: Principal) -> None:
    principal.require(Capability.EDIT_DRAFT)
    tenant_lock(session, principal.context)
    if not setup_complete(session, principal.context):
        raise QuoteError("Company setup pending; contact your administrator.", 403)


def scope(table: Table, principal: Principal) -> Any:
    return table.c.tenant_id == principal.context.tenant_id


def load_case(session: Session, principal: Principal, case_id: UUID) -> Any:
    row = (
        session.execute(
            select(schema.cases)
            .where(scope(schema.cases, principal), schema.cases.c.id == case_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise QuoteError("Draft unavailable.", 404)
    return row


def retry(
    session: Session, principal: Principal, body: RetryInput, operation: str
) -> tuple[str, Any]:
    fingerprint = hashlib.sha256(
        canonical(
            {
                "operation": operation,
                "actor": principal.user_id,
                "payload": body.model_dump(mode="json"),
            }
        ).encode()
    ).hexdigest()
    row = (
        session.execute(
            select(schema.receipts).where(
                scope(schema.receipts, principal),
                schema.receipts.c.request_key == body.request_key,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is not None and row["payload_hash"] != fingerprint:
        raise QuoteError("This retry key was already used for a different request.", 409)
    return fingerprint, row["response"] if row is not None else None


def receipt(
    session: Session,
    principal: Principal,
    body: RetryInput,
    fingerprint: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    session.execute(
        insert(schema.receipts).values(
            tenant_id=principal.context.tenant_id,
            request_key=body.request_key,
            payload_hash=fingerprint,
            response=response,
        )
    )
    return response


def create_case(session: Session, principal: Principal, body: RetryInput) -> dict[str, Any]:
    authorize(session, principal)
    fingerprint, previous = retry(session, principal, body, "create_case")
    if previous is not None:
        return dict(previous)
    case_id, timestamp = uuid4(), datetime.now(UTC)
    session.execute(
        insert(schema.cases).values(
            tenant_id=principal.context.tenant_id,
            id=case_id,
            creator_id=principal.user_id,
            created_at=timestamp,
            version=0,
        )
    )
    audit(session, principal.context, principal.user_id, "quote_case_created", case_id)
    return receipt(
        session,
        principal,
        body,
        fingerprint,
        {
            "id": str(case_id),
            "version": 0,
            "created_at": timestamp.isoformat(),
            "revisions": [],
        },
    )


def list_cases(session: Session, principal: Principal) -> list[dict[str, Any]]:
    authorize(session, principal)
    rows = session.execute(
        select(schema.cases)
        .where(scope(schema.cases, principal))
        .order_by(schema.cases.c.created_at.desc(), schema.cases.c.id)
        .limit(50)
    ).mappings()
    return [
        {"id": str(r["id"]), "version": r["version"], "created_at": r["created_at"].isoformat()}
        for r in rows
    ]


def revision_output(row: Any, active: int | None) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "revision": row["revision"],
        "created_at": row["created_at"].isoformat(),
        "inputs": row["inputs"],
        "result": row["snapshot"],
        "exception_set": row["exception_set"],
        "settings_stale": active != row["settings_revision"],
    }


def read_case(session: Session, principal: Principal, case_id: UUID) -> dict[str, Any]:
    authorize(session, principal)
    case = load_case(session, principal, case_id)
    active = current(session, principal.context).active_settings_revision
    rows = session.execute(
        select(schema.revisions)
        .where(
            scope(schema.revisions, principal),
            schema.revisions.c.case_id == case_id,
        )
        .order_by(schema.revisions.c.revision.desc())
    ).mappings()
    return {
        "id": str(case_id),
        "version": case["version"],
        "created_at": case["created_at"].isoformat(),
        "revisions": [revision_output(r, active) for r in rows],
    }


def lookup(session: Session, principal: Principal, kind: str, query: str) -> list[dict[str, Any]]:
    authorize(session, principal)
    table = commercial.customers if kind == "customers" else commercial.products
    conditions = [table.c.name.icontains(query, autoescape=True), table.c.id.cast(String) == query]
    if kind == "products":
        conditions.append(table.c.sku.icontains(query, autoescape=True))
    rows = session.execute(
        select(table)
        .where(
            scope(table, principal),
            table.c.archived.is_(False),
            or_(*conditions),
        )
        .order_by(table.c.name, table.c.id)
        .limit(50)
    ).mappings()
    return [
        {"id": str(r["id"]), "name": r["name"], **({"sku": r["sku"]} if kind == "products" else {})}
        for r in rows
    ]


def create_customer(session: Session, principal: Principal, body: CustomerCreate) -> dict[str, Any]:
    authorize(session, principal)
    fingerprint, previous = retry(session, principal, body, "create_customer")
    if previous is not None:
        return dict(previous)
    customer_id = uuid4()
    session.execute(
        insert(commercial.customers).values(
            tenant_id=principal.context.tenant_id,
            id=customer_id,
            name=body.name,
        )
    )
    audit(session, principal.context, principal.user_id, "quote_customer_created", customer_id)
    return receipt(
        session, principal, body, fingerprint, {"id": str(customer_id), "name": body.name}
    )


def evaluate(
    session: Session,
    sessions: sessionmaker[Session],
    principal: Principal,
    case_id: UUID,
    revision: int,
    body: Candidate,
) -> tuple[CalculationRequest, Any]:
    # Reference checks are tenant-local and uniform, including explicit substitution evidence.
    refs = [(commercial.customers, body.customer_id)]
    refs.extend((commercial.products, line.product_id) for line in body.lines)
    refs.extend(
        (commercial.products, line.substitute_for) for line in body.lines if line.substitute_for
    )
    for table, identity in refs:
        if (
            session.scalar(
                select(table.c.id)
                .where(
                    scope(table, principal),
                    table.c.id == identity,
                    table.c.archived.is_(False),
                )
                .with_for_update(read=True)
            )
            is None
        ):
            raise QuoteError("Selected customer or product is unavailable.", 404)
    timestamp = datetime.now(UTC)
    request = CalculationRequest(
        case_id=case_id,
        revision=revision,
        customer_id=body.customer_id,
        freight=body.freight,
        lines=tuple(
            CalculationLine(
                **line.model_dump(exclude={"negotiated"}),
                negotiated=NegotiatedPrice(
                    **line.negotiated.model_dump(),
                    proposer_id=principal.user_id,
                    proposed_at=timestamp,
                )
                if line.negotiated
                else None,
            )
            for line in body.lines
        ),
    )
    result = CalculationService(sessions).calculate(principal.context, request, actor=principal)
    if result.settings_revision is None:
        raise QuoteError("Company setup pending; contact your administrator.", 403)
    return request, result


def calculate(
    session: Session,
    sessions: sessionmaker[Session],
    principal: Principal,
    case_id: UUID,
    body: Candidate,
) -> dict[str, Any]:
    authorize(session, principal)
    case = load_case(session, principal, case_id)
    _, result = evaluate(session, sessions, principal, case_id, case["version"] + 1, body)
    return dict(result.model_dump(mode="json"))


def save(
    session: Session,
    sessions: sessionmaker[Session],
    principal: Principal,
    case_id: UUID,
    body: SaveInput,
) -> dict[str, Any]:
    authorize(session, principal)
    case = load_case(session, principal, case_id)
    fingerprint, previous = retry(session, principal, body, "save:" + str(case_id))
    if previous is not None:
        return dict(previous)
    if case["version"] != body.expected_version:
        raise QuoteError(
            "Draft changed elsewhere. Reload the latest revision and reconcile your edits.", 409
        )
    request, result = evaluate(session, sessions, principal, case_id, case["version"] + 1, body)
    revision_id, timestamp = uuid4(), datetime.now(UTC)
    exception_set = {
        "id": str(uuid4()),
        "members": [
            {"id": str(uuid4()), "code": f.code, "line_id": f.line_id}
            for f in result.findings
            if f.kind == "approval"
        ],
    }
    values = dict(
        tenant_id=principal.context.tenant_id,
        id=revision_id,
        case_id=case_id,
        revision=request.revision,
        customer_id=body.customer_id,
        creator_id=principal.user_id,
        created_at=timestamp,
        settings_revision=result.settings_revision,
        pricing_as_of=result.pricing_as_of,
        state=result.state,
        commercial_fingerprint=result.commercial_fingerprint,
        freight=result.freight,
        total=result.total,
        snapshot=result.model_dump(mode="json"),
        inputs=request.model_dump(mode="json"),
        exception_set=exception_set,
    )
    session.execute(insert(schema.revisions).values(**values))
    session.execute(
        insert(schema.lines),
        [
            dict(
                tenant_id=principal.context.tenant_id,
                revision_id=revision_id,
                line_id=line.line_id,
                product_id=line.product_id,
                quantity=line.quantity,
                quote_uom=line.quote_uom,
                pricing_uom=line.pricing_uom,
                negotiated_unit_price=line.negotiated.unit_price if line.negotiated else None,
            )
            for line in request.lines
        ],
    )
    session.execute(
        update(schema.cases)
        .where(
            scope(schema.cases, principal),
            schema.cases.c.id == case_id,
        )
        .values(version=request.revision)
    )
    audit(
        session,
        principal.context,
        principal.user_id,
        "quote_revision_saved",
        revision_id,
        evidence=canonical(
            {
                "case_id": case_id,
                "revision": request.revision,
                "exception_set_id": exception_set["id"],
            }
        ),
    )
    return receipt(
        session,
        principal,
        body,
        fingerprint,
        {
            "id": str(case_id),
            "version": request.revision,
            "revision": revision_output(values, result.settings_revision),
        },
    )
