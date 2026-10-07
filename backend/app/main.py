import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import (
    domain_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.modules.audit.routes import router as audit_router
from app.modules.model_discovery.routes import router as model_discovery_router
from app.modules.model_inventory.api import (
    agent_model_router,
    agent_router,
    application_router,
    deployment_router,
)
from app.modules.model_inventory.api import router as model_inventory_router
from app.modules.model_inventory.domain.errors import DomainError
from app.modules.model_usage.routes import router as model_usage_router

logger = logging.getLogger("auditra.http")


def _request_id(scope: Scope) -> str:
    for name, value in scope["headers"]:
        if name.lower() == b"x-request-id":
            inbound = value.decode("latin-1").strip()
            if inbound:
                return inbound
    return str(uuid4())


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.monotonic()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                message["headers"] = [
                    *message["headers"],
                    (b"x-request-id", request_id.encode("latin-1")),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            if response_started:
                raise
            await unhandled_error_handler(Request(scope), exc)(scope, receive, send_with_request_id)
        finally:
            duration_ms = (time.monotonic() - started) * 1000
            logger.info(
                "%s %s %s %s %.2f",
                scope["method"],
                scope["path"],
                status_code,
                request_id,
                duration_ms,
            )


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    app = FastAPI(title="Auditra")
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(model_inventory_router)
    app.include_router(application_router)
    app.include_router(agent_router)
    app.include_router(agent_model_router)
    app.include_router(deployment_router)
    app.include_router(model_usage_router)
    app.include_router(model_discovery_router)
    app.include_router(audit_router)
    return app


app = create_app()
