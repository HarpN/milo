from __future__ import annotations

import os


class Settings:
    environment: str = os.getenv("ENVIRONMENT", "dev")
    service_name: str = os.getenv("SERVICE_NAME", "milo-guide-scraper")
    service_version: str = os.getenv("SERVICE_VERSION", "0.1.0")
    host: str = os.getenv("HOST", "0.0.0.0")
    grpc_port: int = int(os.getenv("GRPC_PORT", "50056"))
    grpc_max_workers: int = int(os.getenv("GRPC_MAX_WORKERS", "32"))

    judy_grpc_target: str = os.getenv("JUDY_GRPC_TARGET", "judy-council:50052")
    judy_timeout_seconds: float = float(os.getenv("JUDY_TIMEOUT_SECONDS", "10"))
    judy_tls_enabled: bool = os.getenv("JUDY_TLS_ENABLED", "false").lower() == "true"
    judy_tls_ca_cert_path: str = os.getenv("JUDY_TLS_CA_CERT_PATH", "")
    judy_mtls_enabled: bool = os.getenv("JUDY_MTLS_ENABLED", "false").lower() == "true"
    judy_tls_client_cert_path: str = os.getenv("JUDY_TLS_CLIENT_CERT_PATH", "")
    judy_tls_client_key_path: str = os.getenv("JUDY_TLS_CLIENT_KEY_PATH", "")

    grpc_tls_enabled: bool = os.getenv("GRPC_TLS_ENABLED", "false").lower() == "true"
    grpc_tls_server_cert_path: str = os.getenv("GRPC_TLS_SERVER_CERT_PATH", "")
    grpc_tls_server_key_path: str = os.getenv("GRPC_TLS_SERVER_KEY_PATH", "")
    grpc_tls_client_ca_cert_path: str = os.getenv("GRPC_TLS_CLIENT_CA_CERT_PATH", "")
    grpc_tls_require_client_auth: bool = os.getenv("GRPC_TLS_REQUIRE_CLIENT_AUTH", "false").lower() == "true"

    outbound_signature_header: str = os.getenv("OUTBOUND_SIGNATURE_HEADER", "X-Milo-Signature")
    outbound_signature_secret: str = os.getenv("OUTBOUND_SIGNATURE_SECRET", "")
    outbound_key_id: str = os.getenv("OUTBOUND_KEY_ID", "milo-k1")
    outbound_signature_dev_fallback: str = "milo-dev-secret"

    replay_ttl_seconds: int = int(os.getenv("REPLAY_TTL_SECONDS", "300"))

    inbound_auth_enabled: bool = os.getenv("INBOUND_AUTH_ENABLED", "true").lower() == "true"
    inbound_auth_token: str = os.getenv("INBOUND_AUTH_TOKEN", "")
    inbound_auth_header: str = os.getenv("INBOUND_AUTH_HEADER", "x-milo-auth")

    scrape_db_path: str = os.getenv("SCRAPE_DB_PATH", "data/milo.sqlite3")
    keeper_db_path: str = os.getenv("KEEPER_DB_PATH", "../TheKeeper/keeper_blended.db")
    keeper_export_enabled: bool = os.getenv("KEEPER_EXPORT_ENABLED", "true").lower() == "true"
    sqlite_busy_timeout_ms: int = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "5000"))
    default_source: str = os.getenv("GUIDE_SOURCE", "web")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "256"))
    sqlite_vss_enabled: bool = os.getenv("SQLITE_VSS_ENABLED", "false").lower() == "true"
    sqlite_vss_extension_path: str = os.getenv("SQLITE_VSS_EXTENSION_PATH", "")

    allowed_guide_domains: tuple[str, ...] = tuple(
        domain.strip().lower()
        for domain in os.getenv(
            "ALLOWED_GUIDE_DOMAINS",
            "psnprofiles.com,www.psnprofiles.com,ign.com,www.ign.com,powerpyx.com,www.powerpyx.com",
        ).split(",")
        if domain.strip()
    )
    sanitizer_version: str = os.getenv("SANITIZER_VERSION", "milo-sanitizer-v1")
    min_trust_confidence: float = float(os.getenv("MIN_TRUST_CONFIDENCE", "0.65"))
    max_chunk_chars: int = int(os.getenv("MAX_CHUNK_CHARS", "2200"))
    max_response_bytes: int = int(os.getenv("MAX_RESPONSE_BYTES", "3000000"))
    domain_request_cooldown_seconds: float = float(os.getenv("DOMAIN_REQUEST_COOLDOWN_SECONDS", "2.0"))
    resolved_security_event_retention_days: int = int(os.getenv("RESOLVED_SECURITY_EVENT_RETENTION_DAYS", "30"))


settings = Settings()
