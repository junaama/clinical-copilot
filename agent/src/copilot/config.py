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
    # Cohere is used by the hybrid retriever (issue 008) for query embeddings
    # (``embed-english-v3.0``) and reranking (``rerank-english-v3.0``). Empty
    # = retriever runs in mock-only mode and ``retrieve_evidence`` returns a
    # ``no_cohere_key`` error rather than crashing at import time.
    cohere_api_key: SecretStr = Field(default=SecretStr(""), alias="COHERE_API_KEY")

    openemr_base_url: str = Field(
        default="https://openemr-production-c5b4.up.railway.app",
        alias="OPENEMR_BASE_URL",
    )
    openemr_fhir_base: str = Field(
        default="https://openemr-production-c5b4.up.railway.app/apis/default/fhir",
        alias="OPENEMR_FHIR_BASE",
    )
    openemr_fhir_token: SecretStr = Field(default=SecretStr(""), alias="OPENEMR_FHIR_TOKEN")
    # Default off so that production deploys never silently serve fixtures.
    # Dev environments must opt in explicitly via ``USE_FIXTURE_FHIR=1`` in
    # their ``.env`` (see ``agent/.env.example``).
    use_fixture_fhir: bool = Field(default=False, alias="USE_FIXTURE_FHIR")

    smart_client_id: str = Field(default="", alias="SMART_CLIENT_ID")
    smart_client_secret: SecretStr = Field(default=SecretStr(""), alias="SMART_CLIENT_SECRET")
    smart_redirect_uri: str = Field(
        default="http://localhost:8000/smart/callback",
        alias="SMART_REDIRECT_URI",
    )
    smart_scopes: str = Field(
        default=(
            "openid fhirUser launch launch/patient offline_access "
            "patient/Patient.read patient/Observation.read "
            "patient/Condition.read patient/MedicationRequest.read "
            "patient/MedicationAdministration.read patient/Encounter.read "
            "patient/AllergyIntolerance.read patient/DocumentReference.read "
            "patient/DiagnosticReport.read patient/ServiceRequest.read"
        ),
        alias="SMART_SCOPES",
    )

    # --- Standalone OAuth (copilot-standalone client) ---
    smart_standalone_client_id: str = Field(default="", alias="SMART_STANDALONE_CLIENT_ID")
    smart_standalone_client_secret: SecretStr = Field(
        default=SecretStr(""), alias="SMART_STANDALONE_CLIENT_SECRET"
    )
    smart_standalone_redirect_uri: str = Field(
        default="http://localhost:8000/auth/smart/callback",
        alias="SMART_STANDALONE_REDIRECT_URI",
    )
    smart_standalone_scopes: str = Field(
        # Must match REQUESTED_SCOPES in
        # agent/scripts/seed/bootstrap_standalone_oauth.py — OpenEMR
        # silently drops scopes from the issued token that the client
        # wasn't registered for. Without api:oemr the Standard REST API
        # (used for document upload, allergy/medication/problem writes)
        # responds 403 "insufficient permissions for the requested
        # resource" on every request.
        default=(
            "openid fhirUser offline_access profile email "
            "api:oemr api:fhir "
            "user/Patient.rs user/Observation.rs "
            "user/Condition.rs user/MedicationRequest.rs "
            "user/Encounter.rs "
            "user/AllergyIntolerance.rs user/DocumentReference.rs "
            "user/DiagnosticReport.rs user/ServiceRequest.rs "
            "user/CareTeam.rs user/Practitioner.rs "
            "user/document.crs user/allergy.cruds "
            "user/medication.cruds user/medical_problem.cruds "
            "user/patient.rs"
        ),
        alias="SMART_STANDALONE_SCOPES",
    )

    # Secret used to sign session cookies. Required for standalone auth.
    session_secret: SecretStr = Field(default=SecretStr(""), alias="SESSION_SECRET")

    # AES-256 key (base64-encoded, 32 bytes raw) used to encrypt token
    # columns in ``copilot_token_bundle`` at rest. Required when
    # ``CHECKPOINTER_DSN`` is set; the agent fails to start otherwise. Not
    # consulted in fixture/dev mode (in-memory store doesn't persist
    # to disk). See ``copilot.token_crypto``.
    token_enc_key: SecretStr = Field(default=SecretStr(""), alias="COPILOT_TOKEN_ENC_KEY")

    # Session lifetime in seconds (default 8 hours).
    session_ttl_seconds: int = Field(default=28800, alias="SESSION_TTL_SECONDS")

    # Where to send the user after /smart/callback completes the OAuth dance.
    # The frontend reads conversation_id (and patient_id, scope, etc.) off the
    # URL and bootstraps the chat panel from there.
    copilot_ui_url: str = Field(default="", alias="COPILOT_UI_URL")

    checkpointer_dsn: str = Field(default="", alias="CHECKPOINTER_DSN")

    agent_audit_log_path: str = Field(
        default="./logs/agent_audit.jsonl",
        alias="AGENT_AUDIT_LOG_PATH",
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # User ids (FHIR Practitioner UUIDs) that bypass the CareTeam gate.
    # The PRD calls this the deliberate week-1 admin backdoor: ACL-bound
    # users in OpenEMR are mirrored here as a flat allow-list rather than
    # round-tripping through OpenEMR's ACL framework on every tool call.
    # Driven by env (CSV or JSON-array) — the validator below normalizes
    # both like ``allowed_origins``.
    admin_user_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        alias="COPILOT_ADMIN_USER_IDS",
    )

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _split_admin_csv(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                import json
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return [str(v).strip() for v in parsed if str(v).strip()]
                except json.JSONDecodeError:
                    pass
            return [v.strip() for v in stripped.split(",") if v.strip()]
        return value

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

    # Faithfulness judge (issue 011). Defaults to Anthropic Haiku 4.5 so the
    # judge is independent from the synthesizer (which the agent runs on
    # gpt-4o-mini / Sonnet). Setting this to ``""`` disables the judge — the
    # runner skips the dimension and the scoreboard column simply doesn't
    # appear, useful when running in environments without ANTHROPIC_API_KEY.
    eval_judge_model: str = Field(default="claude-haiku-4-5", alias="EVAL_JUDGE_MODEL")

    # Cohere API key — used by the Week 2 retrieval pipeline (issues 007 +
    # 008) for embeddings (``embed-english-v3.0``) and reranking
    # (``rerank-english-v3.0``). Empty string disables the retrieval path
    # cleanly so the agent boots in environments without Cohere; the
    # hybrid retriever will fall back to RRF-only and the indexer refuses
    # to populate vectors.
    cohere_api_key: SecretStr = Field(default=SecretStr(""), alias="COHERE_API_KEY")

    # Vision-capable Anthropic model for VLM extraction (issue 004). Kept
    # separate from ``llm_model`` so the agent's chat model can be a cheaper
    # text-only model while extraction runs on Sonnet vision.
    vlm_model: str = Field(default="claude-sonnet-4-6", alias="VLM_MODEL")

    @property
    def langfuse_enabled(self) -> bool:
        return bool(
            self.langfuse_host
            and self.langfuse_public_key.get_secret_value()
            and self.langfuse_secret_key.get_secret_value()
        )


def get_settings() -> Settings:
    return Settings()
