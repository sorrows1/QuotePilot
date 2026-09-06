from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["quotepilot-api"] = "quotepilot-api"


app = FastAPI(title="QuotePilot API", version="0.1.0")


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """Return a stable process-readiness response for local and CI smoke checks."""
    return HealthResponse()
