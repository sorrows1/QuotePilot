from alembic import context
from sqlalchemy import Connection

from quotepilot_api import auth_models  # noqa: F401
from quotepilot_api.tenants import Base

connection = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise RuntimeError(
        "Use python -m quotepilot_api.migrate; migrations require the serialized runner."
    )

context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()
