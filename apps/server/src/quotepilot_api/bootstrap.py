"""Operator-only bootstrap. Never accepts passwords in arguments or environment."""

import argparse
import getpass
import sys
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from quotepilot_api.auth import AuthError, AuthService
from quotepilot_api.database import database_engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing-tenant", type=UUID, help="Explicit QT-002 tenant with no users")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("Bootstrap requires an interactive terminal for protected password entry.")
    engine = database_engine()
    try:
        locator = input("Organization login code: ")
        login = input("First administrator login: ")
        password = getpass.getpass("Initial password (15–128 characters): ")
        if password != getpass.getpass("Confirm initial password: "):
            raise ValueError("Passwords do not match.")
        context = AuthService(sessionmaker(engine)).bootstrap(
            locator, login, password, existing_tenant=args.existing_tenant
        )
        print(
            f"Administrator created for tenant {context.tenant_id}. "
            "First login requires a password change."
        )
    except AuthError as error:
        raise SystemExit(f"Bootstrap refused: {error.code}. No credentials were changed.") from None
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except SQLAlchemyError:
        raise SystemExit(
            "Bootstrap failed. Check database connectivity and migration state; "
            "no partial bootstrap committed."
        ) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
