"""QuotePilot API package."""

# Tenant settings now reference auth actors and immutable settings history. Register
# that model graph for API, trusted bootstrap and standalone tenant services alike.
from quotepilot_api import auth_models, settings_models  # noqa: F401
