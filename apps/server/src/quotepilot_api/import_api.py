"""Admin-only browser import adapter. AuthService owns the atomic transaction."""

import base64
import binascii
import csv
import io
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from quotepilot_api import imports
from quotepilot_api.auth import Capability
from quotepilot_api.auth_api import Config, Service, credentials
from quotepilot_api.import_contract import CommitInput, PreviewInput, UploadInput
from quotepilot_api.import_models import ImportJob
from quotepilot_api.import_parser import ImportError

router = APIRouter(prefix="/api/admin/imports", tags=["imports"])


@router.get("")
def list_jobs(request: Request, service: Service, config: Config) -> list[dict[str, Any]]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        principal.require(Capability.MANAGE_IMPORTS)
        jobs = session.scalars(
            select(ImportJob)
            .where(ImportJob.tenant_id == principal.context.tenant_id)
            .order_by(ImportJob.created_at.desc(), ImportJob.id)
            .limit(50)
        )
        return [
            {
                "id": str(j.id),
                "filename": j.filename,
                "kind": j.kind,
                "created_at": j.created_at.isoformat(),
                "status": "committed" if j.result else "previewed" if j.preview else "uploaded",
            }
            for j in jobs
        ]


@router.post("", status_code=201)
def upload(body: UploadInput, request: Request, service: Service, config: Config) -> dict[str, Any]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        principal.require(Capability.MANAGE_IMPORTS)
        try:
            data = base64.b64decode(body.data_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ImportError("Invalid file encoding. Choose the file again.") from None
        return imports.output(imports.upload(session, principal, body.filename, body.kind, data))


@router.get("/{job_id}")
def get_job(job_id: UUID, request: Request, service: Service, config: Config) -> dict[str, Any]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return imports.output(imports.load(session, principal, job_id))


@router.post("/{job_id}/preview")
def preview(
    job_id: UUID, body: PreviewInput, request: Request, service: Service, config: Config
) -> dict[str, Any]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return imports.output(imports.preview(session, principal, job_id, body))


@router.post("/{job_id}/commit")
def commit(
    job_id: UUID, body: CommitInput, request: Request, service: Service, config: Config
) -> dict[str, Any]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return imports.commit(session, principal, job_id, body)


@router.get("/{job_id}/errors")
def errors(job_id: UUID, request: Request, service: Service, config: Config) -> Response:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        job = imports.load(session, principal, job_id)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["row", "field", "reason"])
        for error in (job.preview or {}).get("errors", []):
            # Neutralize spreadsheet formula prefixes; source file values are not exported.
            writer.writerow(
                [str(error["row"]), safe_cell(error["field"]), safe_cell(error["message"])]
            )
        return Response(
            stream.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="import-errors.csv"'},
        )


def safe_cell(value: str) -> str:
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
