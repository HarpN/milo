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

## Environment

- `GRPC_PORT`: inbound gRPC port, default `50056`
- `JUDY_GRPC_TARGET`: Judy gRPC endpoint
- `OUTBOUND_SIGNATURE_SECRET`: HMAC secret used for outbound payload signing
- `OUTBOUND_SIGNATURE_HEADER`: metadata header used to carry the signature
- `GUIDE_SOURCE`: label for the origin of scraped content
- `SCRAPE_DB_PATH`: local SQLite path for scrape history

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

## Tests

```bash
pytest
```

## Validation And Conventions

- Local validation passes with `pytest` using the workspace venv Python interpreter.
- gRPC tests use an ephemeral bind port so they remain reliable across machines.
- The guide store uses SQLite only for Milo's own scrape history and chunk records.
- Milo follows the same contract-first, signed-request template as the other agent repos.

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

The current fetcher is deterministic and local, which keeps the service testable without external network dependencies. The interface is ready for a real retrieval layer when that upstream integration is introduced.
