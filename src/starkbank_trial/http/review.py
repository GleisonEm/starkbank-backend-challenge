from collections.abc import Callable
from datetime import datetime
from functools import wraps
from hmac import compare_digest
from http import HTTPStatus

from flask import Flask, Response, jsonify, request
from flask_limiter import Limiter
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from starkbank_trial.bootstrap import Services
from starkbank_trial.config import Settings
from starkbank_trial.domain.provider import ProviderError, ProviderTransientError
from starkbank_trial.persistence.review_store import JsonValue, ReviewPage, ReviewQuery, ReviewStore

ReviewView = Callable[..., Response | tuple[Response, int]]
ReviewListLoader = Callable[[ReviewQuery], ReviewPage]
ReviewDetailLoader = Callable[[str], dict[str, JsonValue] | None]


class ReviewQueryParams(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = None
    trial_id: str | None = None
    status: list[str] = Field(default_factory=list)
    from_at: datetime | None = None
    to_at: datetime | None = None
    batch_id: str | None = None
    slot_index: int | None = Field(default=None, ge=0, le=7)
    credit_status: str | None = None
    provider_invoice_id: str | None = None
    tag: str | None = None
    dispatch_status: str | None = None
    provider_status: str | None = None
    provider_log_type: str | None = None
    invoice_id: str | None = None
    provider_transfer_id: str | None = None
    external_id: str | None = None
    subscription: str | None = None
    log_type: str | None = None
    outcome: str | None = None
    resource_id: str | None = None

    def to_store_query(self) -> ReviewQuery:
        return ReviewQuery(
            limit=self.limit,
            cursor=self.cursor,
            trial_id=self.trial_id,
            statuses=tuple(self.status),
            from_at=self.from_at,
            to_at=self.to_at,
            batch_id=self.batch_id,
            slot_index=self.slot_index,
            credit_status=self.credit_status,
            provider_invoice_id=self.provider_invoice_id,
            tag=self.tag,
            dispatch_status=self.dispatch_status,
            provider_status=self.provider_status,
            provider_log_type=self.provider_log_type,
            invoice_id=self.invoice_id,
            provider_transfer_id=self.provider_transfer_id,
            external_id=self.external_id,
            subscription=self.subscription,
            log_type=self.log_type,
            outcome=self.outcome,
            resource_id=self.resource_id,
        )


def register_review_routes(  # noqa: C901, PLR0915
    app: Flask,
    settings: Settings,
    services: Services,
    store: ReviewStore,
    limiter: Limiter,
) -> None:
    config = settings.review_api_config()
    if config is None:
        return

    def protected(view: ReviewView) -> ReviewView:
        @wraps(view)
        def authenticated(*args: object, **kwargs: object) -> Response | tuple[Response, int]:
            authorization = request.headers.get("Authorization", "")
            expected = f"Bearer {config.token.get_secret_value()}"
            if not compare_digest(authorization, expected):
                return jsonify(error="review_authentication_required"), HTTPStatus.UNAUTHORIZED
            try:
                return view(*args, **kwargs)
            except ValueError:
                return jsonify(error="invalid_review_query"), HTTPStatus.BAD_REQUEST

        return authenticated

    def limited(view: ReviewView) -> ReviewView:
        return limiter.limit(config.rate_limit)(view)

    def query() -> ReviewQuery:
        values: dict[str, str | list[str]] = dict(request.args.items())
        values["status"] = request.args.getlist("status")
        try:
            return ReviewQueryParams.model_validate(values).to_store_query()
        except (ValidationError, ValueError):
            message = "invalid_review_query"
            raise ValueError(message) from None

    def page_response(page: ReviewPage) -> Response:
        return jsonify(
            data=list(page.items),
            pagination={
                "limit": len(page.items),
                "total": page.total,
                "next_cursor": page.next_cursor,
            },
        )

    def add_list_route(path: str, loader: ReviewListLoader) -> None:
        def route() -> Response:
            return page_response(loader(query()))

        endpoint = path.replace("/", "_")
        app.add_url_rule(
            path,
            endpoint=endpoint,
            view_func=limited(protected(route)),
            methods=["GET"],
        )

    def add_detail_route(path: str, loader: ReviewDetailLoader) -> None:
        def route(**kwargs: str) -> Response | tuple[Response, int]:
            resource_id = next(iter(kwargs.values()))
            result = loader(resource_id)
            if result is None:
                return jsonify(error="review_resource_not_found"), HTTPStatus.NOT_FOUND
            return jsonify(data=result)

        endpoint = path.replace("/", "_").replace("<", "").replace(">", "")
        app.add_url_rule(
            path,
            endpoint=endpoint,
            view_func=limited(protected(route)),
            methods=["GET"],
        )

    def review_overview() -> Response:
        return jsonify(data=store.overview(query().trial_id))

    def review_configuration() -> Response:
        return jsonify(data=_configuration(settings))

    def review_schedule() -> Response:
        review_query = query()
        overview = store.overview(review_query.trial_id)
        trial = overview["trial"]
        trial_id = trial.get("id") if isinstance(trial, dict) else None
        batches = (
            store.batches(ReviewQuery(trial_id=str(trial_id), limit=100)).items
            if trial_id is not None
            else ()
        )
        return jsonify(
            data={"trial": trial, "policy": overview["schedule"], "batches": list(batches)}
        )

    def review_provider_webhooks() -> Response | tuple[Response, int]:
        provider = services.provider
        if provider is None:
            return jsonify(error="provider_unavailable"), HTTPStatus.SERVICE_UNAVAILABLE
        try:
            webhooks = provider.list_webhooks()
        except ProviderTransientError:
            return jsonify(error="provider_unavailable"), HTTPStatus.SERVICE_UNAVAILABLE
        except ProviderError:
            return jsonify(error="provider_query_failed"), HTTPStatus.BAD_GATEWAY
        return jsonify(
            data={
                "webhooks": [
                    {"id": item.id, "url": item.url, "subscriptions": list(item.subscriptions)}
                    for item in webhooks
                ]
            }
        )

    app.add_url_rule(
        "/api/v1/review/overview",
        view_func=limited(protected(review_overview)),
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/v1/review/configuration",
        view_func=limited(protected(review_configuration)),
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/v1/review/schedule",
        view_func=limited(protected(review_schedule)),
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/v1/review/provider/webhooks",
        view_func=limited(protected(review_provider_webhooks)),
        methods=["GET"],
    )
    add_list_route("/api/v1/review/trials", store.trials)
    add_list_route("/api/v1/review/batches", store.batches)
    add_list_route("/api/v1/review/invoices", store.invoices)
    add_list_route("/api/v1/review/transfers", store.transfers)
    add_list_route("/api/v1/review/webhook-events", store.events)
    add_detail_route("/api/v1/review/trials/<trial_id>", store.trial)
    add_detail_route("/api/v1/review/batches/<batch_id>", store.batch)
    add_detail_route("/api/v1/review/invoices/<draft_id>", store.invoice)
    add_detail_route("/api/v1/review/transfers/<job_id>", store.transfer)
    add_detail_route("/api/v1/review/webhook-events/<event_id>", store.event)


def _configuration(settings: Settings) -> dict[str, JsonValue]:
    return {
        "environment": settings.starkbank_environment,
        "live_operations_enabled": settings.starkbank_sandbox_live_enabled,
        "webhook_subscriptions": ["invoice", "transfer"],
        "schedule": {
            "slot_count": 8,
            "interval_hours": 3,
            "duration_hours": 24,
            "batch_size": {"min": 8, "max": 12},
            "tick_seconds": 60,
            "reconciliation_seconds": 120,
            "worker_poll_seconds": 1,
        },
        "retries": {
            "base_seconds": settings.retry_base_seconds,
            "invoice_max_attempts": settings.invoice_max_attempts,
            "invoice_reconciliation_max_attempts": settings.invoice_reconciliation_max_attempts,
            "transfer_max_attempts": settings.transfer_max_attempts,
        },
    }
