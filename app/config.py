from __future__ import annotations

import os


class Settings:
    service_name: str = os.getenv("SERVICE_NAME", "milo-guide-scraper")
    service_version: str = os.getenv("SERVICE_VERSION", "0.1.0")
    host: str = os.getenv("HOST", "0.0.0.0")
    grpc_port: int = int(os.getenv("GRPC_PORT", "50056"))

    judy_grpc_target: str = os.getenv("JUDY_GRPC_TARGET", "judy-council:50052")
    judy_timeout_seconds: float = float(os.getenv("JUDY_TIMEOUT_SECONDS", "10"))

    outbound_signature_header: str = os.getenv("OUTBOUND_SIGNATURE_HEADER", "X-Milo-Signature")
    outbound_signature_secret: str = os.getenv("OUTBOUND_SIGNATURE_SECRET", "milo-dev-secret")
    outbound_key_id: str = os.getenv("OUTBOUND_KEY_ID", "milo-k1")

    scrape_db_path: str = os.getenv("SCRAPE_DB_PATH", "data/milo.sqlite3")
    default_source: str = os.getenv("GUIDE_SOURCE", "web")


settings = Settings()
