from http import HTTPStatus

import structlog
from flask import Flask, Response, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from starkbank_trial.bootstrap import Services, build_services
from starkbank_trial.config import Settings
from starkbank_trial.domain.provider import InvalidWebhookError, ProviderTransientError
from starkbank_trial.logging import configure_logging

logger = structlog.get_logger()


def create_app(settings: Settings | None = None, services: Services | None = None) -> Flask:
    resolved_settings = settings if settings is not None else Settings()
    configure_logging(resolved_settings.log_level, resolved_settings.log_file)
    resolved_services = services if services is not None else build_services(resolved_settings)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = resolved_settings.max_content_length

    def live() -> Response:
        return jsonify(status="ok")

    def ready() -> tuple[Response, int] | Response:
        try:
            with resolved_services.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.exception("readiness_check_failed")
            return jsonify(status="unavailable"), HTTPStatus.SERVICE_UNAVAILABLE
        return jsonify(status="ready")

    def starkbank_webhook() -> tuple[Response, int] | Response:
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
        except ProviderTransientError:
            logger.exception("webhook_verification_unavailable")
            return jsonify(error="verification unavailable"), HTTPStatus.SERVICE_UNAVAILABLE
        logger.info("webhook_recorded", outcome=outcome.value)
        return jsonify(status=outcome.value)

    app.add_url_rule("/health/live", view_func=live, methods=["GET"])
    app.add_url_rule("/health/ready", view_func=ready, methods=["GET"])
    app.add_url_rule(
        "/webhooks/starkbank",
        view_func=starkbank_webhook,
        methods=["POST"],
    )
    return app
