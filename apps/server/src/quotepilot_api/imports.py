"""Atomic import application service. Preview plans never mutate commercial records."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quotepilot_api import commercial_schema as schema
from quotepilot_api.auth import AuthError, Capability, Principal, audit
from quotepilot_api.commercial import CommercialRepository, RecordKind
from quotepilot_api.commercial_inputs import CommercialInput
from quotepilot_api.import_contract import FIELDS, MODELS, CommitInput, PreviewInput, normalize
from quotepilot_api.import_models import ImportJob
from quotepilot_api.import_parser import ImportError, parse
from quotepilot_api.tenants import TenantContext, require_context

SCOPES = {
    "customers": ["external_key"],
    "products": ["sku"],
    "pricebooks": ["key"],
    "prices": ["product_id", "pricebook_id", "uom"],
    "inventory": ["product_id", "observed_at"],
    "pricebook_assignments": ["customer_id"],
    "uom_conversions": ["product_id", "from_uom", "to_uom"],
    "product_costs": ["product_id"],
}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def lock(session: Session, context: TenantContext) -> None:
    tenant = require_context(context)
    guard = schema.commercial_write_guards
    session.execute(insert(guard).values(tenant_id=tenant).on_conflict_do_nothing())
    session.execute(
        update(guard).where(guard.c.tenant_id == tenant).values(generation=guard.c.generation + 1)
    )


def load(session: Session, principal: Principal, job_id: UUID) -> ImportJob:
    principal.require(Capability.MANAGE_IMPORTS)
    job = session.scalar(
        select(ImportJob).where(
            ImportJob.tenant_id == principal.context.tenant_id, ImportJob.id == job_id
        )
    )
    if job is None:
        raise AuthError("TENANT_SCOPE_VIOLATION", 404)
    return job


def upload(
    session: Session, principal: Principal, filename: str, kind: str, data: bytes
) -> ImportJob:
    principal.require(Capability.MANAGE_IMPORTS)
    if kind not in MODELS:
        raise ImportError("Unsupported import type.")
    headers, rows = parse(filename, data)
    database_now = session.scalar(select(func.clock_timestamp()))
    assert isinstance(database_now, datetime)
    job = ImportJob(
        id=uuid4(),
        tenant_id=principal.context.tenant_id,
        actor_id=principal.user_id,
        created_at=min(database_now, datetime.now(UTC)),
        filename=filename,
        kind=kind,
        file_hash=hashlib.sha256(data).hexdigest(),
        headers=headers,
        rows=rows,
    )
    session.add(job)
    audit(
        session,
        principal.context,
        principal.user_id,
        "import_uploaded",
        job.id,
        evidence=job.file_hash,
    )
    return job


def output(job: ImportJob) -> dict[str, Any]:
    required, optional = FIELDS[job.kind]
    return {
        "id": str(job.id),
        "filename": job.filename,
        "kind": job.kind,
        "file_hash": job.file_hash,
        "headers": job.headers,
        "row_count": len(job.rows),
        "sample": job.rows[:10],
        "required_fields": required,
        "optional_fields": optional,
        "mapping": job.mapping,
        "mode": job.mode,
        "preview_token": job.preview_token,
        "preview": job.preview,
        "result": job.result,
    }


def overlap(a: dict[str, Any], b: dict[str, Any], low: str, high: str) -> bool:
    return (a[high] is None or b[low] < a[high]) and (b[high] is None or a[low] < b[high])


def plan(
    session: Session, context: TenantContext, job: ImportJob, mapping: dict[str, str], mode: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if require_context(context) != job.tenant_id:
        raise AuthError("TENANT_SCOPE_VIOLATION", 404)
    required, optional = FIELDS[job.kind]
    errors: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    summary = {"created": 0, "updated": 0, "skipped": 0}
    if (
        set(mapping) - set(required + optional)
        or set(required) - set(mapping)
        or any(column not in job.headers for column in mapping.values())
        or len(set(mapping.values())) != len(mapping)
    ):
        raise ImportError("Map every required field to a different existing column.")
    if mode == "replace_effective" and job.kind not in {"prices", "product_costs"}:
        raise ImportError("Effective replacement is supported only for prices and costs.")
    table = schema.TABLES[job.kind]
    existing = [
        dict(row)
        for row in session.execute(
            select(table).where(table.c.tenant_id == job.tenant_id)
        ).mappings()
    ]
    references: dict[str, list[dict[str, Any]]] = {}
    for name in ("products", "customers", "pricebooks"):
        t = schema.TABLES[name]
        references[name] = [
            dict(row)
            for row in session.execute(
                select(t).where(t.c.tenant_id == job.tenant_id, t.c.archived.is_(False))
            ).mappings()
        ]
    seen: set[str] = set()
    for number, row in enumerate(job.rows, 2):
        field = "row"
        try:
            values: dict[str, Any] = {}
            for field, header in mapping.items():
                raw = row[job.headers.index(header)]
                if field in required and not raw.strip():
                    raise ValueError("Required value is missing")
                values.update(normalize({field: raw}))
            field = "row"
            refs = []
            for source, target, name, key in (
                ("sku", "product_id", "products", "sku"),
                ("customer_key", "customer_id", "customers", "external_key"),
                ("pricebook_key", "pricebook_id", "pricebooks", "key"),
            ):
                if source not in values or (source == "sku" and job.kind == "products"):
                    continue
                field = source
                wanted = values.pop(source)
                version = values.pop("pricebook_version", None) if name == "pricebooks" else None
                found = [
                    r
                    for r in references[name]
                    if r[key] == wanted and (name != "pricebooks" or r["version"] == version)
                ]
                if len(found) != 1:
                    raise ValueError(
                        "Reference must identify one active record in this organization"
                    )
                values[target] = found[0]["id"]
                refs.append(found[0])
            field = "row"
            values["id"] = uuid5(job.id, str(number))
            if "source" in MODELS[job.kind].model_fields:
                values.setdefault("source", f"import:{job.file_hash}:row:{number}")
            if job.kind == "inventory":
                values["imported_at"] = job.created_at
            model = MODELS[job.kind].model_validate(values)
            data = model.model_dump()
            if job.kind == "uom_conversions" and data["from_uom"] == data["to_uom"]:
                raise ValueError("Conversion must use different source and destination UOM")
            scope = SCOPES[job.kind]
            identity = {k: data[k] for k in scope}
            for key in ("version", "valid_from", "quantity_min", "quantity_max"):
                if key in data:
                    identity[key] = data[key]
            identity_hash = fingerprint(identity)
            if identity_hash in seen:
                raise ValueError(
                    "Duplicate key or authority in this file; remove the duplicate row"
                )
            seen.add(identity_hash)
            candidates = [r for r in existing if all(r[k] == data[k] for k in scope)]
            identical = [
                r
                for r in candidates
                if not r["archived"]
                and all(
                    r[k] == v for k, v in data.items() if k not in {"id", "source", "imported_at"}
                )
            ]
            if identical and mode != "insert":
                summary["skipped"] += 1
                operations.append(
                    {"action": "skip", "row": number, "existing": identical[0], "references": refs}
                )
                continue
            conflicts = candidates
            if "valid_from" in data:
                conflicts = [
                    r
                    for r in candidates
                    if not r["archived"] and overlap(r, data, "valid_from", "valid_to")
                ]
                if job.kind == "prices":
                    conflicts = [
                        r for r in conflicts if overlap(r, data, "quantity_min", "quantity_max")
                    ]
            if job.kind == "pricebooks" and any(
                r["version"] == data["version"] for r in candidates
            ):
                raise ValueError("Pricebook key/version already exists; create a new version")
            previous = None
            if conflicts:
                if (
                    mode == "replace_effective"
                    and len(conflicts) == 1
                    and not conflicts[0]["archived"]
                    and conflicts[0]["valid_from"] < data["valid_from"]
                    and conflicts[0]["valid_to"] is None
                    and (
                        job.kind == "product_costs"
                        or all(
                            conflicts[0].get(k) == data.get(k)
                            for k in ("quantity_min", "quantity_max", "uom")
                        )
                    )
                ):
                    previous = dict(conflicts[0])
                    conflicts[0]["valid_to"] = data["valid_from"]
                else:
                    raise ValueError(
                        "Existing key or overlapping authority; "
                        "use a new key/window or skip identical records"
                    )
            summary["updated" if previous else "created"] += 1
            operations.append(
                {
                    "action": "replace" if previous else "create",
                    "row": number,
                    "data": data,
                    "previous": previous,
                    "references": refs,
                }
            )
            existing.append({**data, "archived": False})
        except ValidationError as error:
            for issue in error.errors(include_input=False):
                errors.append(
                    {
                        "row": number,
                        "field": str(issue["loc"][0]) if issue["loc"] else "row",
                        "message": issue["msg"],
                    }
                )
        except ValueError as error:
            errors.append({"row": number, "field": field, "message": str(error)})
    return {"valid": not errors, "errors": errors, "summary": summary}, operations


def preview(session: Session, principal: Principal, job_id: UUID, body: PreviewInput) -> ImportJob:
    principal.require(Capability.MANAGE_IMPORTS)
    lock(session, principal.context)
    job = load(session, principal, job_id)
    if job.result is not None:
        raise ImportError("This import is already committed. Upload another file.", 409)
    report, operations = plan(session, principal.context, job, body.mapping, body.mode)
    job.mapping, job.mode, job.preview = body.mapping, body.mode, report
    job.preview_token = fingerprint(
        [job.file_hash, job.kind, body.model_dump(), report, operations]
    )
    audit(
        session,
        principal.context,
        principal.user_id,
        "import_previewed",
        job.id,
        outcome="success" if report["valid"] else "invalid",
        evidence=job.preview_token,
    )
    return job


def commit(
    session: Session, principal: Principal, job_id: UUID, body: CommitInput
) -> dict[str, Any]:
    principal.require(Capability.MANAGE_IMPORTS)
    lock(session, principal.context)
    job = load(session, principal, job_id)
    used = session.scalar(
        select(ImportJob).where(
            ImportJob.tenant_id == principal.context.tenant_id,
            ImportJob.commit_key == body.idempotency_key,
        )
    )
    if used is not None and (used.id != job.id or used.preview_token != body.preview_token):
        raise ImportError("Idempotency key is already bound to a different import.", 409)
    if job.preview_token != body.preview_token or job.mapping is None or job.mode is None:
        raise ImportError("Preview changed. Validate and review this file again.", 409)
    if job.result is not None:
        if job.commit_key != body.idempotency_key:
            raise ImportError("This import is already committed. Retry with its original key.", 409)
        return job.result
    report, operations = plan(session, principal.context, job, job.mapping, job.mode)
    token = fingerprint(
        [job.file_hash, job.kind, {"mapping": job.mapping, "mode": job.mode}, report, operations]
    )
    if not report["valid"] or token != body.preview_token:
        raise ImportError(
            "Data changed or validation failed. Preview again before committing.", 409
        )
    repo = CommercialRepository(session)
    for operation in operations:
        if operation["action"] == "skip":
            continue
        if operation["previous"]:
            repo.close_window(
                principal.context,
                cast(RecordKind, job.kind),
                operation["previous"]["id"],
                operation["data"]["valid_from"],
            )
        values = dict(operation["data"])
        if job.kind == "inventory":
            # Both the model and database reject future evidence. Keep the import
            # instant within both clocks when host/container clocks differ slightly.
            database_now = session.scalar(select(func.clock_timestamp()))
            assert isinstance(database_now, datetime)
            values["imported_at"] = min(database_now, datetime.now(UTC))
        record = cast(CommercialInput, MODELS[job.kind].model_validate(values))
        repo.add(principal.context, record)
    job.commit_key = body.idempotency_key
    job.result = {"job_id": str(job.id), "status": "committed", **report["summary"]}
    audit(
        session,
        principal.context,
        principal.user_id,
        "import_committed",
        job.id,
        evidence=fingerprint(job.result),
    )
    # AuthService owns one transaction, including records, receipt and audit.
    # No provider/embedding execution participates in authoritative import.
    return job.result
