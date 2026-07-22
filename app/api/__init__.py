from .dashboard_api import create_app as _create_app
from .eod_review import router as _eod_review_router


def create_app(*args, **kwargs):
    """Create the dashboard API with the read-only end-of-day review mounted."""
    app = _create_app(*args, **kwargs)
    app.include_router(_eod_review_router)
    return app


__all__ = ["create_app"]
