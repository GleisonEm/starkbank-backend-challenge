from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from starkbank_trial.domain.errors import MissingProviderConfigurationError

MIN_REVIEW_TOKEN_LENGTH = 32


class ProviderCredentials(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    workspace_id: str
    private_key_file: Path


class WebhookConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    public_base_url: HttpUrl


class ReviewApiConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    token: SecretStr
    rate_limit: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    database_url: str = Field(
        default="sqlite+pysqlite:///starkbank_trial.db",
        alias="DATABASE_URL",
    )
    compose_project_name: str | None = Field(default=None, alias="COMPOSE_PROJECT_NAME")
    starkbank_environment: Literal["sandbox"] = Field(
        default="sandbox", alias="STARKBANK_ENVIRONMENT"
    )
    starkbank_project_id: str | None = Field(default=None, alias="STARKBANK_PROJECT_ID")
    starkbank_workspace_id: str | None = Field(default=None, alias="STARKBANK_WORKSPACE_ID")
    starkbank_private_key_file: Path | None = Field(
        default=None, alias="STARKBANK_PRIVATE_KEY_FILE"
    )
    public_base_url: HttpUrl | None = Field(default=None, alias="PUBLIC_BASE_URL")
    starkbank_sandbox_live_enabled: bool = Field(
        default=False,
        alias="STARKBANK_SANDBOX_LIVE_ENABLED",
    )
    log_level: Literal["debug", "info", "warning", "error"] = Field(
        default="info", alias="LOG_LEVEL"
    )
    log_file: Path | None = Field(default=None, alias="LOG_FILE")
    max_content_length: int = Field(default=1_048_576, gt=0, alias="MAX_CONTENT_LENGTH")
    worker_lease_seconds: int = Field(default=120, gt=0, alias="WORKER_LEASE_SECONDS")
    batch_lease_seconds: int = Field(default=300, gt=0, alias="BATCH_LEASE_SECONDS")
    retry_base_seconds: int = Field(default=5, gt=0, alias="RETRY_BASE_SECONDS")
    invoice_max_attempts: int = Field(default=5, ge=1, alias="INVOICE_MAX_ATTEMPTS")
    invoice_reconciliation_max_attempts: int = Field(
        default=5, ge=1, alias="INVOICE_RECONCILIATION_MAX_ATTEMPTS"
    )
    transfer_max_attempts: int = Field(default=10, ge=1, alias="TRANSFER_MAX_ATTEMPTS")
    batch_max_attempts: int = Field(default=15, ge=1, alias="BATCH_MAX_ATTEMPTS")
    review_api_enabled: bool = Field(default=False, alias="REVIEW_API_ENABLED")
    review_api_token: SecretStr | None = Field(default=None, alias="REVIEW_API_TOKEN")
    review_api_rate_limit: str = Field(
        default="10 per minute;100 per hour",
        alias="REVIEW_API_RATE_LIMIT",
    )

    @model_validator(mode="after")
    def validate_review_api(self) -> "Settings":
        if self.review_api_enabled:
            token = self.review_api_token
            if token is None or len(token.get_secret_value()) < MIN_REVIEW_TOKEN_LENGTH:
                message = "REVIEW_API_TOKEN must contain at least 32 characters"
                raise ValueError(message)
        return self

    def review_api_config(self) -> ReviewApiConfig | None:
        if not self.review_api_enabled:
            return None
        token = self.review_api_token
        if token is None:
            message = "REVIEW_API_TOKEN is required when review API is enabled"
            raise ValueError(message)
        return ReviewApiConfig(token=token, rate_limit=self.review_api_rate_limit)

    def provider_credentials(self) -> ProviderCredentials:
        project_id = self.starkbank_project_id
        private_key_file = self.starkbank_private_key_file
        missing = tuple(
            name
            for name, value in (
                ("STARKBANK_PROJECT_ID", project_id),
                ("STARKBANK_WORKSPACE_ID", self.starkbank_workspace_id),
                ("STARKBANK_PRIVATE_KEY_FILE", private_key_file),
            )
            if value is None
        )
        workspace_id = self.starkbank_workspace_id
        if project_id is None or workspace_id is None or private_key_file is None:
            raise MissingProviderConfigurationError(missing_fields=missing)
        return ProviderCredentials(
            project_id=project_id,
            workspace_id=workspace_id,
            private_key_file=private_key_file,
        )

    def webhook_config(self) -> WebhookConfig:
        public_base_url = self.public_base_url
        if public_base_url is None:
            raise MissingProviderConfigurationError(missing_fields=("PUBLIC_BASE_URL",))
        return WebhookConfig(public_base_url=public_base_url)

    def smoke_namespace(self) -> str:
        if self.compose_project_name is None:
            raise MissingProviderConfigurationError(missing_fields=("COMPOSE_PROJECT_NAME",))
        return self.compose_project_name
