import os
import subprocess
import sys

from sqlalchemy import Engine, inspect, text

from quotepilot_api.migrate import MIGRATION_LOCK


def test_concurrent_runners_wait_for_lock_and_reach_head(empty_database: Engine) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": empty_database.url.render_as_string(hide_password=False),
        "PGAPPNAME": "qt002_migration_test",
    }
    children: list[subprocess.Popen[str]] = []
    try:
        with empty_database.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
            for _ in range(2):
                children.append(
                    subprocess.Popen(
                        [sys.executable, "-m", "quotepilot_api.migrate"],
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                )
            # Observe both runners blocked on our lock, not a timing-based success assertion.
            with empty_database.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as observer:
                for _ in range(100):
                    waiting = observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND application_name = 'qt002_migration_test' "
                            "AND wait_event = 'advisory'"
                        )
                    )
                    if waiting == 2:
                        break
                    observer.execute(text("SELECT pg_sleep(0.05)"))
                assert waiting == 2
                assert not inspect(observer).has_table("tenants")
        for child in children:
            _, stderr = child.communicate(timeout=15)
            assert child.returncode == 0, stderr
        with empty_database.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0002_commercial"
            )
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
