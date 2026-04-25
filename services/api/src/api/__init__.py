"""Expose API routers and main app for the Polypredictor backend."""

from fastapi import APIRouter, FastAPI

# Import sub-routers here. For now only the auth router is included.
from .auth import router as auth_router  # noqa: F401

__all__ = ["create_app", "auth_router"]


def create_app() -> FastAPI:
    """Construct a minimal FastAPI app including all routers.

    This is a convenience function for local development and testing. In
    production the service may assemble routers differently.
    """
    app = FastAPI(title="PolyPredictor API Stub")
    app.include_router(auth_router)
    return app
