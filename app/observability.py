from __future__ import annotations

from uuid import uuid4
import logging
import time

from prometheus_client import Counter, Gauge, Histogram

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


logger = logging.getLogger("github_agent.http")
http_requests_total = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
http_request_duration_seconds = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "path"])
task_success_total = Counter("task_success_total", "Completed tasks", ["task_type"])
task_failure_total = Counter("task_failure_total", "Failed tasks", ["task_type"])
task_retry_total = Counter("task_retry_total", "Retried tasks", ["task_type"])
agent_duration_seconds = Histogram("agent_duration_seconds", "Agent execution duration")
external_errors_total = Counter("external_errors_total", "External dependency errors", ["dependency"])
cache_hits_total = Counter("cache_hits_total", "Cache hits", ["cache"])
cache_misses_total = Counter("cache_misses_total", "Cache misses", ["cache"])
running_tasks = Gauge("running_tasks", "Currently running tasks")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        path = request.url.path
        http_requests_total.labels(request.method, path, str(response.status_code)).inc()
        http_request_duration_seconds.labels(request.method, path).observe(time.perf_counter() - started)
        logger.info(
            "http request",
            extra={"request_id": request_id, "event": "http_request", "path": path},
        )
        response.headers["X-Request-ID"] = request_id
        return response
