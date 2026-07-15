from __future__ import annotations

from concurrent import futures
import logging
from typing import Any

import grpc
from google.protobuf import empty_pb2, json_format, struct_pb2

from .config import settings
from .guide_client import GuideClient
from .judy_client import JudyClient
from .models import JudyProposal, ScrapeRequest
from .storage import GuideStore

metrics: dict[str, int] = {
    "requests_total": 0,
    "scrapes_total": 0,
    "judge_only_total": 0,
    "commit_total": 0,
}

store = GuideStore()
guide_client = GuideClient(store=store)
judy_client = JudyClient()
logger = logging.getLogger(__name__)


def _dict_to_struct(payload: dict[str, Any]) -> struct_pb2.Struct:
    message = struct_pb2.Struct()
    json_format.ParseDict(payload, message)
    return message


def _struct_to_dict(message: struct_pb2.Struct) -> dict[str, Any]:
    return json_format.MessageToDict(message)


def _health(_: empty_pb2.Empty, context: grpc.ServicerContext) -> struct_pb2.Struct:
    del context
    payload = {
        "status": "ok",
        "service": settings.service_name,
        "transport": "grpc",
        "scrape_db_path": settings.scrape_db_path,
    }
    return _dict_to_struct(payload)


def _is_authorized(context: grpc.ServicerContext) -> bool:
    if not settings.inbound_auth_enabled:
        return True

    if not settings.inbound_auth_token:
        return False

    metadata = dict(context.invocation_metadata())
    token = metadata.get(settings.inbound_auth_header.lower())
    return token == settings.inbound_auth_token


def _scrape_guide(request_message: struct_pb2.Struct, context: grpc.ServicerContext) -> struct_pb2.Struct:
    metrics["requests_total"] += 1

    if not _is_authorized(context):
        context.set_code(grpc.StatusCode.UNAUTHENTICATED)
        context.set_details("Missing or invalid inbound authentication metadata")
        return struct_pb2.Struct()

    try:
        request = ScrapeRequest.model_validate(_struct_to_dict(request_message))
    except Exception as exc:
        context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
        context.set_details(f"Invalid scrape request: {exc}")
        return struct_pb2.Struct()

    try:
        guide_document = guide_client.fetch_guide(request)
    except ValueError as exc:
        logger.error("Milo rejected scrape request for %s (%s): %s", request.guide_url, request.game_title, exc)
        context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
        context.set_details(str(exc))
        return _dict_to_struct(
            {
                "error": "SCRAPE_REJECTED",
                "reason": str(exc),
                "store_summary": store.summary(),
            }
        )
    proposal = JudyProposal(
        transaction_metadata={
            "agent_id": settings.service_name,
            "timestamp": guide_document.fetched_at,
            "correlation_id": guide_document.correlation_id,
        },
        proposed_action={
            "target_table": "guide_library",
            "action_type": "UPSERT_GUIDE",
            "entity_id": request.guide_url,
            "payload": {
                "game_title": request.game_title,
                "platform": request.platform,
                "source": request.source,
                "chunk_count": len(guide_document.chunks),
                "summary": guide_document.summary,
                "quality_views": request.quality_views,
                "quality_age_days": request.quality_age_days,
                "quality_score": guide_document.quality_score,
            },
        },
        agent_rationale="Guide extraction and normalization for strategy retrieval",
        guide_document=guide_document,
    )

    judy_response = judy_client.send_scrape(proposal, commit=request.commit)

    metrics["scrapes_total"] += 1
    if request.commit:
        metrics["commit_total"] += 1
    else:
        metrics["judge_only_total"] += 1

    return _dict_to_struct(
        {
            "correlation_id": guide_document.correlation_id,
            "commit": request.commit,
            "guide_document": guide_document.model_dump(),
            "proposal": proposal.model_dump(),
            "judy_response": judy_response,
            "store_summary": store.summary(),
        }
    )


def _list_security_events(request_message: struct_pb2.Struct, context: grpc.ServicerContext) -> struct_pb2.Struct:
    if not _is_authorized(context):
        context.set_code(grpc.StatusCode.UNAUTHENTICATED)
        context.set_details("Missing or invalid inbound authentication metadata")
        return struct_pb2.Struct()
    payload = _struct_to_dict(request_message)
    limit = int(payload.get("limit", 50))
    limit = max(1, min(limit, 500))
    events = store.list_security_events(limit=limit)
    return _dict_to_struct({"events": events, "count": len(events)})


def _review_security_event(request_message: struct_pb2.Struct, context: grpc.ServicerContext) -> struct_pb2.Struct:
    if not _is_authorized(context):
        context.set_code(grpc.StatusCode.UNAUTHENTICATED)
        context.set_details("Missing or invalid inbound authentication metadata")
        return struct_pb2.Struct()

    payload = _struct_to_dict(request_message)
    event_id = int(payload.get("event_id", 0))
    review_status = str(payload.get("review_status", "")).strip().upper()
    admin_notes = str(payload.get("admin_notes", ""))
    actor = str(payload.get("actor", "admin"))

    if event_id <= 0 or not review_status:
        context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
        context.set_details("event_id and review_status are required")
        return struct_pb2.Struct()

    try:
        updated = store.review_security_event(
            event_id=event_id,
            review_status=review_status,
            admin_notes=admin_notes,
            actor=actor,
        )
    except ValueError as exc:
        context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
        context.set_details(str(exc))
        return struct_pb2.Struct()

    if not updated:
        context.set_code(grpc.StatusCode.NOT_FOUND)
        context.set_details("Security event not found")
        return struct_pb2.Struct()

    return _dict_to_struct({"updated": True, "event_id": event_id, "review_status": review_status})


def _list_security_event_audit(request_message: struct_pb2.Struct, context: grpc.ServicerContext) -> struct_pb2.Struct:
    if not _is_authorized(context):
        context.set_code(grpc.StatusCode.UNAUTHENTICATED)
        context.set_details("Missing or invalid inbound authentication metadata")
        return struct_pb2.Struct()

    payload = _struct_to_dict(request_message)
    limit = int(payload.get("limit", 20))
    limit = max(1, min(limit, 200))
    audit = store.list_security_event_audit(limit=limit)
    return _dict_to_struct({"audit": audit, "count": len(audit)})


def _apply_security_event_retention(request_message: struct_pb2.Struct, context: grpc.ServicerContext) -> struct_pb2.Struct:
    if not _is_authorized(context):
        context.set_code(grpc.StatusCode.UNAUTHENTICATED)
        context.set_details("Missing or invalid inbound authentication metadata")
        return struct_pb2.Struct()

    payload = _struct_to_dict(request_message)
    requested_days = int(payload.get("retention_days", settings.resolved_security_event_retention_days))
    dry_run = bool(payload.get("dry_run", False))
    actor = str(payload.get("actor", "admin"))
    deleted = store.apply_security_event_retention(requested_days, dry_run=dry_run, actor=actor)
    return _dict_to_struct(
        {
            "deleted_count": deleted,
            "dry_run": dry_run,
            "retention_days": max(1, int(requested_days)),
            "store_summary": store.summary(),
        }
    )


def create_server(bind_address: str | None = None) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=settings.grpc_max_workers))
    handlers = {
        "Health": grpc.unary_unary_rpc_method_handler(
            _health,
            request_deserializer=empty_pb2.Empty.FromString,
            response_serializer=struct_pb2.Struct.SerializeToString,
        ),
        "ScrapeGuide": grpc.unary_unary_rpc_method_handler(
            _scrape_guide,
            request_deserializer=struct_pb2.Struct.FromString,
            response_serializer=struct_pb2.Struct.SerializeToString,
        ),
        "ListSecurityEvents": grpc.unary_unary_rpc_method_handler(
            _list_security_events,
            request_deserializer=struct_pb2.Struct.FromString,
            response_serializer=struct_pb2.Struct.SerializeToString,
        ),
        "ReviewSecurityEvent": grpc.unary_unary_rpc_method_handler(
            _review_security_event,
            request_deserializer=struct_pb2.Struct.FromString,
            response_serializer=struct_pb2.Struct.SerializeToString,
        ),
        "ListSecurityEventAudit": grpc.unary_unary_rpc_method_handler(
            _list_security_event_audit,
            request_deserializer=struct_pb2.Struct.FromString,
            response_serializer=struct_pb2.Struct.SerializeToString,
        ),
        "ApplySecurityEventRetention": grpc.unary_unary_rpc_method_handler(
            _apply_security_event_retention,
            request_deserializer=struct_pb2.Struct.FromString,
            response_serializer=struct_pb2.Struct.SerializeToString,
        ),
    }
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("milo.MiloService", handlers),))

    bind_target = bind_address or f"{settings.host}:{settings.grpc_port}"
    if settings.grpc_tls_enabled:
        with open(settings.grpc_tls_server_key_path, "rb") as key_file:
            private_key = key_file.read()
        with open(settings.grpc_tls_server_cert_path, "rb") as cert_file:
            certificate_chain = cert_file.read()

        root_certificates = None
        if settings.grpc_tls_client_ca_cert_path:
            with open(settings.grpc_tls_client_ca_cert_path, "rb") as ca_file:
                root_certificates = ca_file.read()

        credentials = grpc.ssl_server_credentials(
            [(private_key, certificate_chain)],
            root_certificates=root_certificates,
            require_client_auth=settings.grpc_tls_require_client_auth,
        )
        bound_port = server.add_secure_port(bind_target, credentials)
    else:
        bound_port = server.add_insecure_port(bind_target)

    if not bound_port:
        raise RuntimeError("Failed to bind Milo gRPC server")
    server.bound_port = bound_port  # type: ignore[attr-defined]
    return server


def serve() -> None:
    server = create_server()
    server.start()
    server.wait_for_termination()
