from pathlib import Path

import pytest
from pydantic import ValidationError

from starkbank_trial.config import Settings
from starkbank_trial.domain.errors import MissingProviderConfigurationError


def test_settings_rejects_any_environment_other_than_sandbox() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "DATABASE_URL": "sqlite+pysqlite:///:memory:",
                "STARKBANK_ENVIRONMENT": "production",
            }
        )


def test_settings_requires_provider_credentials_only_at_provider_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    monkeypatch.chdir(tmp_path)
    settings = Settings.model_validate({"DATABASE_URL": "sqlite+pysqlite:///:memory:"})

    # When / Then
    with pytest.raises(MissingProviderConfigurationError):
        settings.provider_credentials()


def test_settings_builds_provider_credentials_without_public_url(tmp_path: Path) -> None:
    # Given
    key_file = tmp_path / "private-key.pem"
    settings = Settings.model_validate(
        {
            "DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "STARKBANK_PROJECT_ID": "project-1",
            "STARKBANK_PRIVATE_KEY_FILE": key_file,
        }
    )

    # When
    result = settings.provider_credentials()

    # Then
    assert result.project_id == "project-1"
    assert result.private_key_file == key_file


def test_settings_requires_public_url_only_for_webhook_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    monkeypatch.chdir(tmp_path)
    key_file = tmp_path / "private-key.pem"
    settings = Settings.model_validate(
        {
            "DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "STARKBANK_PROJECT_ID": "project-1",
            "STARKBANK_PRIVATE_KEY_FILE": key_file,
        }
    )

    # When / Then
    with pytest.raises(MissingProviderConfigurationError) as captured:
        settings.webhook_config()

    assert captured.value.missing_fields == ("PUBLIC_BASE_URL",)


def test_settings_builds_webhook_configuration_from_public_url() -> None:
    # Given
    settings = Settings.model_validate(
        {
            "DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "PUBLIC_BASE_URL": "https://trial.example.com",
        }
    )

    # When
    result = settings.webhook_config()

    # Then
    assert str(result.public_base_url) == "https://trial.example.com/"


def test_settings_treats_empty_optional_env_values_as_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("PUBLIC_BASE_URL=\n", encoding="utf-8")

    # When
    settings = Settings()

    # Then
    assert settings.public_base_url is None


def test_settings_disables_live_sandbox_calls_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    monkeypatch.chdir(tmp_path)

    # When
    settings = Settings.model_validate({"DATABASE_URL": "sqlite+pysqlite:///:memory:"})

    # Then
    assert settings.starkbank_sandbox_live_enabled is False
