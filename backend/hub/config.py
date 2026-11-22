"""Runtime configuration.

All settings come from environment variables (prefix ``HUB_``) or a local ``.env`` file.
Secrets (tokens) are optional and never logged. Zero-cost mode is a hard invariant:
it cannot be switched off in this release (see SECURITY.md / API_INTEGRATIONS.md).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HUB_",
        env_file=(str(REPO_ROOT / ".env"),),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://hub:hub@127.0.0.1:5432/hub"

    # --- Network exposure (local-only by default) ---
    bind_host: str = "127.0.0.1"
    port: int = 8000
    # Host header allowlist (defends against DNS-rebinding attacks on the local server).
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])
    # Registration stays disabled until the owner explicitly approves public access.
    registration_enabled: bool = False
    public_access_approved: bool = False
    # Phone access (off by default). "local": loopback clients only. "lan": also clients on private networks
    # (home Wi-Fi). "tailnet": also Tailscale addresses (100.64.0.0/10, fd7a:115c:a1e0::/48). Public IPs are
    # always refused unless HUB_PUBLIC_ACCESS_APPROVED=true.
    network_mode: str = "local"
    extra_hosts: list[str] = Field(default_factory=list)  # e.g. ["mybox.local", "mybox.tail1234.ts.net"]
    allowed_client_networks: list[str] = Field(default_factory=list)  # extra CIDRs for unusual LAN ranges
    tls_cert_file: Path | None = None  # optional HTTPS (needed for installable PWA over LAN)
    tls_key_file: Path | None = None
    device_session_days: int = 30
    rate_limit_auth_per_min: int = 10
    rate_limit_api_per_min: int = 600

    # --- Cost & privacy invariants ---
    zero_cost_mode: bool = True
    cloud_ai_enabled: bool = False  # optional free-tier cloud providers (none verified yet)
    allow_private_content_to_cloud: bool = False

    # --- Identification sent to public APIs (no personal data by default) ---
    user_agent: str = "AI-Intelligence-Hub/0.1 (self-hosted personal research tool)"

    # --- Optional free credentials (never required) ---
    github_token: SecretStr | None = None
    hf_token: SecretStr | None = None
    youtube_api_key: SecretStr | None = None  # free key; only for the optional YouTube Data API search source

    # --- Local AI (Ollama) ---
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""  # empty = auto-pick an installed local model
    ollama_embed_model: str = ""  # empty = auto-pick (e.g. all-minilm / nomic-embed-text)
    ollama_allow_lan: bool = False  # allow a private-network (non-loopback) Ollama host
    # Explicit container-network hostnames that are local to this installation (e.g. "ollama" in Docker Compose,
    # "host.docker.internal"). Never put public hostnames here.
    ollama_trusted_hosts: list[str] = Field(default_factory=list)
    ai_num_ctx: int = 4096
    ai_timeout_s: float = 180.0
    ai_max_concurrency: int = 1

    # --- Workers & resources ---
    worker_concurrency: int = 2
    low_resource_mode: bool = False
    scheduler_tick_s: float = 15.0
    job_lock_timeout_s: int = 900
    max_items_per_run: int = 200
    http_max_response_bytes: int = 15 * 1024 * 1024
    http_log_retention_days: int = 14

    # --- Sessions ---
    session_days: int = 14
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    # --- Paths ---
    data_dir: Path = REPO_ROOT / "data"
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    @field_validator("zero_cost_mode")
    @classmethod
    def _zero_cost_is_mandatory(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "HUB_ZERO_COST_MODE cannot be disabled: this release contains no paid integrations "
                "and refuses to run in a mode that could incur charges."
            )
        return v

    @field_validator("network_mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"local", "lan", "tailnet"}:
            raise ValueError("HUB_NETWORK_MODE must be local, lan or tailnet")
        return v

    @field_validator("bind_host")
    @classmethod
    def _bind_local(cls, v: str) -> str:
        # Binding to a non-loopback interface exposes the instance; require explicit approval
        # via HUB_PUBLIC_ACCESS_APPROVED (checked in check_exposure()).
        return v.strip()

    def check_exposure(self) -> None:
        """Refuse to expose the instance or enable registration without explicit approval.

        A non-loopback bind is allowed without public approval only in lan/tailnet mode, where the client-IP
        allowlist middleware rejects every non-private address.
        """
        loopback = self.bind_host in {"127.0.0.1", "localhost", "::1"}
        if self.registration_enabled and not self.public_access_approved:
            raise RuntimeError("Refusing to start: registration requires HUB_PUBLIC_ACCESS_APPROVED=true (SECURITY.md).")
        if not loopback and self.network_mode == "local" and not self.public_access_approved:
            raise RuntimeError(
                "Refusing to start: a non-loopback bind needs HUB_NETWORK_MODE=lan|tailnet (private networks only) "
                "or HUB_PUBLIC_ACCESS_APPROVED=true (see SECURITY.md 'Before any public deployment')."
            )
        if bool(self.tls_cert_file) != bool(self.tls_key_file):
            raise RuntimeError("Set both HUB_TLS_CERT_FILE and HUB_TLS_KEY_FILE, or neither.")

    @property
    def scheme(self) -> str:
        return "https" if self.tls_cert_file and self.tls_key_file else "http"

    @property
    def is_test(self) -> bool:
        return self.env == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
