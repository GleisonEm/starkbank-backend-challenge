from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from starkbank_trial.domain.errors import MissingProviderConfigurationError


class ProviderCredentials(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    workspace_id: str
    private_key_file: Path


class WebhookConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    public_base_url: HttpUrl


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
