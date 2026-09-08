"""Explicit browser gate: pytest tests/browser_e2e.py (not mocked UI tests)."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from quotepilot_api.auth import AuthService


def test_browser_authentication(database: Engine) -> None:
    root = Path(__file__).resolve().parents[3]
    AuthService(sessionmaker(database)).bootstrap(
        "browser-test", "admin", "initial-browser-password-839!"
    )
    env = {
        **os.environ,
        "DATABASE_URL": database.url.render_as_string(hide_password=False),
        "AUTH_ORIGIN": "http://127.0.0.1:5174",
        "AUTH_LOCAL_HTTP": "1",
        "API_PROXY_TARGET": "http://127.0.0.1:8001",
    }
    processes: list[subprocess.Popen[bytes]] = []
    try:
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "quotepilot_api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8001",
                    "--no-proxy-headers",
                    "--no-access-log",
                ],
                env=env,
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    "node",
                    str(root / "apps/web/node_modules/vite/bin/vite.js"),
                    str(root / "apps/web"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "5174",
                    "--strictPort",
                ],
                cwd=root,
                env=env,
            )
        )
        result = subprocess.run(
            [
                "node",
                str(root / "apps/web/node_modules/@playwright/test/cli.js"),
                "test",
                "--config",
                str(root / "apps/web/playwright.config.ts"),
            ],
            cwd=root,
            env=env,
            timeout=180,
            check=False,
        )
        assert result.returncode == 0
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=10)
