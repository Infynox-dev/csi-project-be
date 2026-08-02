"""Sentry (Urgentry) + OpenTelemetry bootstrap.

Call ``init_telemetry()`` once **before** ``FastAPI()`` is constructed, then
``instrument_fastapi_app(app)`` after the app exists.
No-op when SENTRY_DSN / OTEL_EXPORTER_OTLP_ENDPOINT are unset (local quiet).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from app.common.config import Settings

logger = logging.getLogger(__name__)

_OTEL_PROVIDER_READY = False

_SENSITIVE_HEADER_RE = re.compile(
    r"^(authorization|cookie|set-cookie|x-api-key|x-auth-token)$",
    re.I,
)
_BODY_DROP_PATH_RE = re.compile(
    r"^/api/(auth|files|yuvalokham/auth)(/|$)"
    r"|^/api/.*/(payment|payments|proof|upload|uploads)(/|$)",
    re.I,
)
_HEALTH_PATH_RE = re.compile(r"^/api/health/?$", re.I)


def _request_path(event: dict[str, Any]) -> str:
    req = event.get("request") or {}
    url = req.get("url") or ""
    if not url:
        return ""
    try:
        return urlparse(url).path or ""
    except Exception:
        return ""


def scrub_sentry_event(event: dict[str, Any], hint: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Allowlist-style scrubber: drop health noise; strip bodies on auth/upload/payment."""
    del hint  # unused; kept for sentry_sdk before_send signature
    path = _request_path(event)
    if path and _HEALTH_PATH_RE.match(path):
        return None

    req = event.get("request")
    if isinstance(req, dict):
        headers = req.get("headers")
        if isinstance(headers, dict):
            req["headers"] = {
                k: "[Filtered]" if _SENSITIVE_HEADER_RE.match(str(k)) else v
                for k, v in headers.items()
            }
        elif isinstance(headers, list):
            req["headers"] = [
                [k, "[Filtered]"] if _SENSITIVE_HEADER_RE.match(str(k)) else [k, v]
                for k, v in headers
            ]

        if path and _BODY_DROP_PATH_RE.search(path):
            req.pop("data", None)
            req.pop("body", None)
            env = req.get("env")
            if isinstance(env, dict):
                for key in ("HTTP_AUTHORIZATION", "HTTP_COOKIE"):
                    if key in env:
                        env[key] = "[Filtered]"

    user = event.get("user")
    if isinstance(user, dict):
        for key in ("email", "ip_address", "username"):
            user.pop(key, None)

    return event


def init_telemetry(settings: Settings) -> None:
    """Init OTel TracerProvider (if endpoint set) then Sentry/Urgentry (if DSN set)."""
    _init_otel(settings)
    _init_sentry(settings)


def _init_otel(settings: Settings) -> None:
    global _OTEL_PROVIDER_READY
    endpoint = (settings.otel_exporter_otlp_endpoint or "").strip()
    if not endpoint:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTel packages missing; skipping OTel init")
        return

    attrs: dict[str, str] = {
        "service.name": settings.otel_service_name or "csi-api",
    }
    raw = (settings.otel_resource_attributes or "").strip()
    if raw:
        for part in raw.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                attrs[k.strip()] = v.strip()

    provider = TracerProvider(resource=Resource.create(attrs))
    # Alloy on the app node speaks plain gRPC OTLP (no TLS).
    # Exporter wants host:port (scheme optional); normalize either form.
    grpc_endpoint = endpoint.replace("https://", "").replace("http://", "")
    exporter = OTLPSpanExporter(endpoint=grpc_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _OTEL_PROVIDER_READY = True
    logger.info("OpenTelemetry tracing enabled → %s", endpoint)


def instrument_fastapi_app(app: Any) -> None:
    """Attach FastAPI instrumentation after ``app = FastAPI(...)``."""
    if not _OTEL_PROVIDER_READY:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,/api/health")
    except Exception:
        logger.exception("Failed to instrument FastAPI for OTel")


def _init_sentry(settings: Settings) -> None:
    dsn = (settings.sentry_dsn or "").strip()
    if not dsn:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning("sentry-sdk missing; skipping Sentry init")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.environment or "development",
        release=settings.sentry_release or None,
        send_default_pii=False,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=scrub_sentry_event,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
    )
    logger.info(
        "Sentry/Urgentry enabled env=%s traces_sample_rate=%s",
        settings.environment,
        settings.sentry_traces_sample_rate,
    )
