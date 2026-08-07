from collections.abc import Callable
from hmac import compare_digest
from http import HTTPStatus

import structlog
from flask import Flask, Response, jsonify, request
from flask_limiter import Limiter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from starkbank_trial.bootstrap import Services, build_services
from starkbank_trial.config import Settings
from starkbank_trial.domain.provider import (
    InvalidWebhookError,
    ProviderTransientError,
    UnexpectedWorkspaceError,
)
from starkbank_trial.http.review import register_review_routes
from starkbank_trial.logging import configure_logging
from starkbank_trial.persistence.review_store import ReviewStore

logger = structlog.get_logger()


def _no_store_review_response(response: Response) -> Response:
    if request.path.startswith("/api/v1/review/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def create_app(settings: Settings | None = None, services: Services | None = None) -> Flask:
    resolved_settings = settings if settings is not None else Settings()
    configure_logging(resolved_settings.log_level, resolved_settings.log_file)
    resolved_services = services if services is not None else build_services(resolved_settings)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = int(resolved_settings.max_content_length)

    review_limiter = Limiter(
        key_func=_review_key(resolved_settings),
        storage_uri="memory://",
        default_limits=[],
        headers_enabled=True,
    )
    review_limiter.init_app(app)
    register_review_routes(
        app,
        resolved_settings,
        resolved_services,
        store=ReviewStore(resolved_services.engine),
        limiter=review_limiter,
    )

    def live() -> Response:
        return jsonify(status="ok")

    def ready() -> Response | tuple[Response, int]:
        try:
            with resolved_services.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.exception("readiness_check_failed")
            return jsonify(status="unavailable"), HTTPStatus.SERVICE_UNAVAILABLE
        return jsonify(status="ready")

    def starkbank_webhook() -> Response | tuple[Response, int]:
        signature = request.headers.get("Digital-Signature")
        if signature is None:
            logger.warning("webhook_rejected", reason="missing_signature")
            return jsonify(error="missing Digital-Signature header"), HTTPStatus.BAD_REQUEST
        content = request.get_data(cache=False, as_text=False)
        logger.info("webhook_received", content_length=len(content))
        try:
            outcome = resolved_services.webhook.receive(content, signature)
        except InvalidWebhookError:
            logger.warning("webhook_rejected", reason="invalid_signature_or_payload")
            return jsonify(error="invalid webhook"), HTTPStatus.BAD_REQUEST
        except UnexpectedWorkspaceError as error:
            logger.warning(
                "webhook_ignored_workspace",
                reason="unexpected_workspace",
                workspace_id=error.workspace_id,
            )
            return jsonify(status="ignored_workspace")
        except ProviderTransientError:
            logger.exception("webhook_verification_unavailable")
            return jsonify(error="verification unavailable"), HTTPStatus.SERVICE_UNAVAILABLE
        logger.info("webhook_recorded", outcome=outcome.value)
        return jsonify(status=outcome.value)

    def review_rate_limit_exceeded(_error: object) -> tuple[Response, int]:
        response = jsonify(error="review_rate_limit_exceeded")
        response.headers["Cache-Control"] = "no-store"
        return response, HTTPStatus.TOO_MANY_REQUESTS

    app.add_url_rule("/health/live", view_func=live, methods=["GET"])
    app.add_url_rule("/health/ready", view_func=ready, methods=["GET"])
    app.add_url_rule("/webhooks/starkbank", view_func=starkbank_webhook, methods=["POST"])
    app.register_error_handler(429, review_rate_limit_exceeded)
    app.after_request(_no_store_review_response)
    return app


def _review_key(settings: Settings) -> Callable[[], str]:
    def key() -> str:
        config = settings.review_api_config()
        authorization = request.headers.get("Authorization", "")
        token = authorization.removeprefix("Bearer ").strip()
        if config is not None and compare_digest(token, config.token.get_secret_value()):
            return "authorized"
        return "anonymous"

    return key
