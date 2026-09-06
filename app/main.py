from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.auth import router as auth_router
from app.api.health import metrics_router, router as health_router
from app.api.documents import router as documents_router
from app.api.search import router as search_router
from app.api.tasks import router as tasks_router
from app.errors import ApiError, api_error_handler, unhandled_error_handler, validation_error_handler
from app.observability import RequestIdMiddleware


def create_app() -> FastAPI:
    application = FastAPI(title="GitHub Agent API")
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(ApiError, api_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(Exception, unhandled_error_handler)
    application.include_router(auth_router)
    application.include_router(health_router)
    application.include_router(metrics_router)
    application.include_router(documents_router)
    application.include_router(search_router)
    application.include_router(tasks_router)
    return application


app = create_app()
