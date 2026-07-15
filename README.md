# Milo

Milo is the guide-scraping and normalization service for the Command Center. It runs as a gRPC service, extracts guide content into structured chunks, stores local scrape history in SQLite, and forwards signed proposals to Judy for governance review or commit.

## Architecture

Milo follows the same contract-first pattern as the other agents in this stack.

- `agent-zone`: Milo receives scrape requests and produces structured guide documents.
- `governance-zone`: Judy receives signed proposals from Milo for review or commit.
- `transport`: gRPC with protobuf `Struct` payloads for portable cross-service contracts.
- `integrity`: HMAC-SHA256 signatures protect outbound requests.
- `storage`: SQLite is used for Milo's own scrape ledger and chunk history.
- `runtime`: Python 3.12 on a slim container image.

## Service Flow

1. A client calls `ScrapeGuide` with a URL, title, and commit flag.
2. Milo fetches or synthesizes guide content.
3. Milo chunks and normalizes the guide text.
4. Milo records the scrape in its local SQLite store.
5. Milo builds a proposal envelope and signs it.
6. Milo sends the proposal to Judy over gRPC.
7. Judy returns the review or commit response.

## Consolidated Delivery Summary

Recent work was grouped into four coherent tracks:

1. Secure ingest and trust metadata: allowlist enforcement, sanitizer gating, and trust fields propagated to Keeper.
2. Moderation lifecycle: security-event review statuses plus retention dry-run/apply workflows.
3. Boundary cleanup and auditability: dashboard writes moved to Milo APIs, plus actor-attributed audit trail.
4. Runtime guardrails: per-domain fetch cooldown and response-size caps to reduce scrape abuse and payload risk.

This keeps behavior changes aligned with a single security and operability objective rather than separate one-off patches.

## gRPC Methods

- `Health`
- `ScrapeGuide`
- `ListSecurityEvents`
- `ReviewSecurityEvent`
- `ApplySecurityEventRetention`
- `ListSecurityEventAudit`

## Environment

- `GRPC_PORT`: inbound gRPC port, default `50056`
- `GRPC_MAX_WORKERS`: gRPC thread pool size for concurrent request handling (default `32`)
- `JUDY_GRPC_TARGET`: Judy gRPC endpoint
- `JUDY_TLS_ENABLED`: use TLS for outbound Judy calls when set to `true`
- `JUDY_TLS_CA_CERT_PATH`: optional CA bundle path for Judy TLS validation
- `JUDY_MTLS_ENABLED`: enable client certificate auth for outbound Judy calls
- `JUDY_TLS_CLIENT_CERT_PATH`: client certificate file path for Judy mTLS
- `JUDY_TLS_CLIENT_KEY_PATH`: client private key file path for Judy mTLS
- `GRPC_TLS_ENABLED`: enable TLS listener for inbound Milo gRPC server
- `GRPC_TLS_SERVER_CERT_PATH`: server certificate file path for Milo TLS listener
- `GRPC_TLS_SERVER_KEY_PATH`: server private key file path for Milo TLS listener
- `GRPC_TLS_REQUIRE_CLIENT_AUTH`: require client certificate auth on inbound Milo listener
- `GRPC_TLS_CLIENT_CA_CERT_PATH`: trusted client CA bundle path for inbound client cert validation
- `OUTBOUND_SIGNATURE_SECRET`: HMAC secret used for outbound payload signing
- `OUTBOUND_SIGNATURE_HEADER`: metadata header used to carry the signature
- `REPLAY_TTL_SECONDS`: replay protection window in seconds (default `300`)
- `INBOUND_AUTH_ENABLED`: enforce inbound gRPC auth metadata check
- `INBOUND_AUTH_HEADER`: inbound metadata header key (default `x-milo-auth`)
- `INBOUND_AUTH_TOKEN`: required inbound metadata token value
- `GUIDE_SOURCE`: label for the origin of scraped content
- `SCRAPE_DB_PATH`: local SQLite path for scrape history
- `SQLITE_BUSY_TIMEOUT_MS`: busy timeout for SQLite lock contention control
- `ALLOWED_GUIDE_DOMAINS`: comma-delimited source-domain allowlist for guide ingestion
- `SANITIZER_VERSION`: label recorded in trust metadata for approved guide chunks
- `MIN_TRUST_CONFIDENCE`: threshold below which sanitized content is rejected
- `MAX_CHUNK_CHARS`: maximum sanitizer text window for normalization/chunking
- `MAX_RESPONSE_BYTES`: hard cap for fetched guide response payload size
- `DOMAIN_REQUEST_COOLDOWN_SECONDS`: minimum interval between same-domain fetches per Milo runtime
- `RESOLVED_SECURITY_EVENT_RETENTION_DAYS`: default retention window for resolved security events

## Local Run

```bash
python -m app.main
```

## Docker

```bash
docker build -t milo-guide-scraper .
docker run --rm -p 50056:50056 milo-guide-scraper
```

## Compose

```bash
docker compose up --build
```

For unified workspace startup via `scripts/launch_all.ps1`, Milo compose now ships with local inbound auth defaults:

- `INBOUND_AUTH_ENABLED=true`
- `INBOUND_AUTH_HEADER=x-milo-auth`
- `INBOUND_AUTH_TOKEN=local-dev-milo-token`

These match scraper dashboard defaults from `scraper/.env.example` for local development.

### Compose mTLS Profile

Use the mTLS override file to start Milo with inbound TLS + client-auth and outbound mTLS to Judy.

Generate local dev certificates first:

```powershell
./scripts/generate-dev-certs.ps1 -Force
```

```bash
docker compose -f docker-compose.yml -f docker-compose.mtls.yml up --build
```

Place local certificates under `certs/` using the layout documented in `certs/README.md`.

Verify certificate chains and mTLS handshakes:

```powershell
./scripts/verify-mtls.ps1
```

If Milo/Judy are not currently running, verify cert trust only:

```powershell
./scripts/verify-mtls.ps1 -SkipHandshake
```

## Tests

```bash
pytest
```

## Security Smoke Validation

From repository root:

```powershell
$env:MILO_GRPC_TARGET="127.0.0.1:50056"
$env:MILO_AUTH_HEADER="x-milo-auth"
$env:MILO_AUTH_TOKEN="<token>"
$env:MILO_ADMIN_ACTOR="security-smoke-script"
.\.venv\Scripts\python.exe scripts/security_v2_smoke.py
```

The smoke script verifies end-to-end moderation behavior through service APIs (not direct DB writes): rejection, event listing, moderation updates, retention preview/apply, and audit listing.

## Validation And Conventions

- Local validation passes with `pytest` using the workspace venv Python interpreter.
- gRPC tests use an ephemeral bind port so they remain reliable across machines.
- The guide store uses SQLite only for Milo's own scrape history and chunk records.
- Milo follows the same contract-first, signed-request template as the other agent repos.
- Outbound Judy calls include nonce and issued-at metadata with TTL validation.
- Inbound scrape requests are rejected unless auth metadata matches configured token.
- mTLS can be enabled for Milo to Judy calls and for inbound Milo listeners when cert paths are configured.

## Updating This Repo

1. Keep changes scoped to the guide-scraping boundary; do not merge Sly telemetry concerns into Milo.
2. Update the service contract, tests, and README together when behavior changes.
3. Re-run `pytest` after each implementation pass and confirm `git diff --check` stays clean.
4. Preserve the SQLite scrape ledger and the Judy gRPC signing flow when extending the service.

## Helm

The Helm chart lives under `charts/milo` and deploys:

- a gRPC `Service`
- a `Deployment` with liveness/readiness TCP probes
- a `Secret` for the outbound signing key
- an egress `NetworkPolicy` that only allows traffic to Judy

## Storage Boundary

Milo keeps its own scrape history and chunk records in SQLite. It should not share a database with Sly, because the two agents have different data shapes and retention needs. Sly handles PSN telemetry; Milo handles guide extraction.

## Notes

Guide retrieval now uses live HTML extraction with semantic-content filtering (main/article-focused and nav/footer/script stripping), then sentence-aware chunking to preserve context.

## Changelog

### v0.3.0 - 2026-07-14

Added:

- Secure-ingest controls: HTTPS allowlist, sanitizer gating, response-size caps, and per-domain cooldown.
- Moderation lifecycle APIs and workflows: event list, review status updates, retention dry-run/apply.
- Actor-attributed moderation audit trail and list-audit API.

Changed:

- Dashboard boundary cleanup so admin moderation flows through Milo gRPC instead of direct DB writes.
- Added local compose auth-token defaults so monorepo one-command launch works without extra Milo auth wiring.
