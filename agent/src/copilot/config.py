"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    openemr_base_url: str = Field(
        default="https://openemr-production-c5b4.up.railway.app",
        alias="OPENEMR_BASE_URL",
    )
    openemr_fhir_base: str = Field(
        default="https://openemr-production-c5b4.up.railway.app/apis/default/fhir",
        alias="OPENEMR_FHIR_BASE",
    )
    openemr_fhir_token: str = Field(default="", alias="OPENEMR_FHIR_TOKEN")
    use_fixture_fhir: bool = Field(default=True, alias="USE_FIXTURE_FHIR")

    smart_client_id: str = Field(default="", alias="SMART_CLIENT_ID")
    smart_client_secret: str = Field(default="", alias="SMART_CLIENT_SECRET")
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

    # Langfuse self-hosted observability + eval scoring (see EVAL.md §5).
    langfuse_host: str = Field(default="", alias="LANGFUSE_HOST")
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_project: str = Field(default="copilot", alias="LANGFUSE_PROJECT")

    eval_experiment_name: str = Field(default="", alias="EVAL_EXPERIMENT_NAME")
    eval_fail_fast: bool = Field(default=False, alias="EVAL_FAIL_FAST")

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_host and self.langfuse_public_key and self.langfuse_secret_key)


def get_settings() -> Settings:
    return Settings()
