"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Sensitive fields are wrapped in ``SecretStr`` so a stray ``repr(settings)``
    or ``%s`` log line surfaces ``"**********"`` instead of the raw secret. Call
    ``.get_secret_value()`` at the boundary where you actually need the string
    (never log that result).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")

    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), alias="ANTHROPIC_API_KEY")

    openemr_base_url: str = Field(
        default="https://openemr-production-c5b4.up.railway.app",
        alias="OPENEMR_BASE_URL",
    )
    openemr_fhir_base: str = Field(
        default="https://openemr-production-c5b4.up.railway.app/apis/default/fhir",
        alias="OPENEMR_FHIR_BASE",
    )
    openemr_fhir_token: SecretStr = Field(default=SecretStr(""), alias="OPENEMR_FHIR_TOKEN")
    use_fixture_fhir: bool = Field(default=True, alias="USE_FIXTURE_FHIR")

    smart_client_id: str = Field(default="", alias="SMART_CLIENT_ID")
    smart_client_secret: SecretStr = Field(default=SecretStr(""), alias="SMART_CLIENT_SECRET")
    smart_redirect_uri: str = Field(
        default="http://localhost:8000/smart/callback",
        alias="SMART_REDIRECT_URI",
    )
    smart_scopes: str = Field(
        default="launch openid fhirUser patient/*.read offline_access",
        alias="SMART_SCOPES",
    )

    checkpointer_dsn: str = Field(default="", alias="CHECKPOINTER_DSN")

    agent_audit_log_path: str = Field(
        default="./logs/agent_audit.jsonl",
        alias="AGENT_AUDIT_LOG_PATH",
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # CORS allow-list. ``NoDecode`` opts out of pydantic-settings' default
    # JSON-decode for list[str] fields, so the env value can be either a
    # comma-separated string or a JSON array — the validator below normalizes
    # both. Without ``NoDecode``, pydantic-settings tries to JSON-parse the
    # raw env string before the validator runs, and a non-JSON value crashes
    # app startup with ``json.JSONDecodeError`` (this happened on Railway
    # with ``ALLOWED_ORIGINS=https://...,https://...``).
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        alias="ALLOWED_ORIGINS",
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept a list, a JSON array string, or a comma-separated string."""

        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(o).strip() for o in value if str(o).strip()]
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                # JSON array — let json.loads do the parse.
                import json
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return [str(o).strip() for o in parsed if str(o).strip()]
                except json.JSONDecodeError:
                    pass  # fall through to CSV
            return [origin.strip() for origin in stripped.split(",") if origin.strip()]
        return value

    # Langfuse self-hosted observability + eval scoring (see EVAL.md §5).
    langfuse_host: str = Field(default="", alias="LANGFUSE_HOST")
    langfuse_public_key: SecretStr = Field(default=SecretStr(""), alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: SecretStr = Field(default=SecretStr(""), alias="LANGFUSE_SECRET_KEY")
    langfuse_project: str = Field(default="copilot", alias="LANGFUSE_PROJECT")

    eval_experiment_name: str = Field(default="", alias="EVAL_EXPERIMENT_NAME")
    eval_fail_fast: bool = Field(default=False, alias="EVAL_FAIL_FAST")

    @property
    def langfuse_enabled(self) -> bool:
        return bool(
            self.langfuse_host
            and self.langfuse_public_key.get_secret_value()
            and self.langfuse_secret_key.get_secret_value()
        )


def get_settings() -> Settings:
    return Settings()
