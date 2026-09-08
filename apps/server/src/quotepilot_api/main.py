from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from quotepilot_api.auth_api import router
from quotepilot_api.auth_http import BrowserBoundary, install_errors


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["quotepilot-api"] = "quotepilot-api"


app = FastAPI(title="QuotePilot API", version="0.1.0")
app.add_middleware(BrowserBoundary)
app.include_router(router)
install_errors(app)


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """Return a stable process-liveness response for local and CI smoke checks."""
    return HealthResponse()
