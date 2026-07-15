from __future__ import annotations

import os

import grpc
import pytest
from google.protobuf import empty_pb2, json_format, struct_pb2

os.environ["JUDY_GRPC_TARGET"] = "127.0.0.1:50066"
os.environ["OUTBOUND_SIGNATURE_SECRET"] = "milo-dev-secret"
os.environ["SCRAPE_DB_PATH"] = ":memory:"
os.environ["INBOUND_AUTH_TOKEN"] = "test-inbound-token"
os.environ["ALLOWED_GUIDE_DOMAINS"] = "example.com"
os.environ["DOMAIN_REQUEST_COOLDOWN_SECONDS"] = "0"

from app.grpc_server import create_server, guide_client, judy_client


@pytest.fixture(scope="module")
def channel() -> grpc.Channel:
    server = create_server(bind_address="127.0.0.1:0")
    server.start()

    grpc_channel = grpc.insecure_channel(f"127.0.0.1:{server.bound_port}")
    grpc.channel_ready_future(grpc_channel).result(timeout=5)

    yield grpc_channel

    grpc_channel.close()
    server.stop(None)


def _scrape_call(channel: grpc.Channel):
    return channel.unary_unary(
        "/milo.MiloService/ScrapeGuide",
        request_serializer=struct_pb2.Struct.SerializeToString,
        response_deserializer=struct_pb2.Struct.FromString,
    )


def _security_events_call(channel: grpc.Channel):
    return channel.unary_unary(
        "/milo.MiloService/ListSecurityEvents",
        request_serializer=struct_pb2.Struct.SerializeToString,
        response_deserializer=struct_pb2.Struct.FromString,
    )


def _review_security_event_call(channel: grpc.Channel):
    return channel.unary_unary(
        "/milo.MiloService/ReviewSecurityEvent",
        request_serializer=struct_pb2.Struct.SerializeToString,
        response_deserializer=struct_pb2.Struct.FromString,
    )


def _apply_security_retention_call(channel: grpc.Channel):
    return channel.unary_unary(
        "/milo.MiloService/ApplySecurityEventRetention",
        request_serializer=struct_pb2.Struct.SerializeToString,
        response_deserializer=struct_pb2.Struct.FromString,
    )


def _security_audit_call(channel: grpc.Channel):
    return channel.unary_unary(
        "/milo.MiloService/ListSecurityEventAudit",
        request_serializer=struct_pb2.Struct.SerializeToString,
        response_deserializer=struct_pb2.Struct.FromString,
    )


def _scrape_metadata() -> tuple[tuple[str, str], ...]:
    return (("x-milo-auth", "test-inbound-token"),)


def test_health(channel: grpc.Channel) -> None:
    response = channel.unary_unary(
        "/milo.MiloService/Health",
        request_serializer=empty_pb2.Empty.SerializeToString,
        response_deserializer=struct_pb2.Struct.FromString,
    )(empty_pb2.Empty())
    payload = json_format.MessageToDict(response)
    assert payload["status"] == "ok"
    assert payload["transport"] == "grpc"


def test_scrape_judge_mode(channel: grpc.Channel, monkeypatch) -> None:
    monkeypatch.setattr(
        guide_client,
        "_fetch_html",
        lambda _: "<html><body><main><h1>Guide</h1><p>Step one. Step two. Step three.</p></main></body></html>",
    )
    monkeypatch.setattr(judy_client, "send_scrape", lambda proposal, commit: {"final_verdict": "APPROVED", "council_id": "cncl-1"})

    request = struct_pb2.Struct()
    json_format.ParseDict({"guide_url": "https://example.com/guide", "game_title": "Test Game", "commit": False}, request)
    response = json_format.MessageToDict(_scrape_call(channel)(request, metadata=_scrape_metadata()))

    assert response["commit"] is False
    assert response["judy_response"]["final_verdict"] == "APPROVED"
    assert response["guide_document"]["game_title"] == "Test Game"
    assert response["store_summary"]["scrape_jobs"] >= 1


def test_scrape_commit_mode(channel: grpc.Channel, monkeypatch) -> None:
    monkeypatch.setattr(
        guide_client,
        "_fetch_html",
        lambda _: "<html><body><main><h2>Guide</h2><p>Alpha beta. Gamma delta. Epsilon zeta.</p></main></body></html>",
    )
    monkeypatch.setattr(judy_client, "send_scrape", lambda proposal, commit: {"committed": True, "decision": {"final_verdict": "APPROVED"}})

    request = struct_pb2.Struct()
    json_format.ParseDict({"guide_url": "https://example.com/guide", "game_title": "Test Game", "commit": True}, request)
    response = json_format.MessageToDict(_scrape_call(channel)(request, metadata=_scrape_metadata()))

    assert response["commit"] is True
    assert response["judy_response"]["committed"] is True


def test_scrape_requires_auth(channel: grpc.Channel) -> None:
    request = struct_pb2.Struct()
    json_format.ParseDict({"guide_url": "https://example.com/guide", "game_title": "Test Game", "commit": False}, request)

    with pytest.raises(grpc.RpcError) as rpc_error:
        _scrape_call(channel)(request)

    assert rpc_error.value.code() == grpc.StatusCode.UNAUTHENTICATED


def test_scrape_rejects_non_allowlisted_domain(channel: grpc.Channel) -> None:
    request = struct_pb2.Struct()
    json_format.ParseDict({"guide_url": "https://notallowed.example.org/guide", "game_title": "Blocked Test", "commit": False}, request)

    with pytest.raises(grpc.RpcError) as rpc_error:
        _scrape_call(channel)(request, metadata=_scrape_metadata())

    assert rpc_error.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert "Source rejected" in str(rpc_error.value.details())


def test_list_security_events(channel: grpc.Channel) -> None:
    request = struct_pb2.Struct()
    json_format.ParseDict({"limit": 20}, request)

    response = json_format.MessageToDict(_security_events_call(channel)(request, metadata=_scrape_metadata()))
    assert int(response["count"]) >= 1
    assert isinstance(response["events"], list)


def test_list_security_audit(channel: grpc.Channel) -> None:
    list_request = struct_pb2.Struct()
    json_format.ParseDict({"limit": 1}, list_request)
    list_response = json_format.MessageToDict(_security_events_call(channel)(list_request, metadata=_scrape_metadata()))
    event_id = int(list_response["events"][0]["id"])

    review_request = struct_pb2.Struct()
    json_format.ParseDict(
        {
            "event_id": event_id,
            "review_status": "ACKNOWLEDGED",
            "admin_notes": "Audit visibility test",
            "actor": "pytest-admin",
        },
        review_request,
    )
    _review_security_event_call(channel)(review_request, metadata=_scrape_metadata())

    audit_request = struct_pb2.Struct()
    json_format.ParseDict({"limit": 20}, audit_request)
    audit_response = json_format.MessageToDict(_security_audit_call(channel)(audit_request, metadata=_scrape_metadata()))

    assert int(audit_response["count"]) >= 1
    assert isinstance(audit_response["audit"], list)
    assert any(item.get("action_type") == "REVIEW_STATUS_UPDATE" for item in audit_response["audit"])


def test_review_security_event(channel: grpc.Channel) -> None:
    list_request = struct_pb2.Struct()
    json_format.ParseDict({"limit": 1}, list_request)
    list_response = json_format.MessageToDict(_security_events_call(channel)(list_request, metadata=_scrape_metadata()))
    event_id = int(list_response["events"][0]["id"])

    review_request = struct_pb2.Struct()
    json_format.ParseDict(
        {
            "event_id": event_id,
            "review_status": "ACKNOWLEDGED",
            "admin_notes": "Reviewed for V2 moderation",
        },
        review_request,
    )

    review_response = json_format.MessageToDict(
        _review_security_event_call(channel)(review_request, metadata=_scrape_metadata())
    )
    assert review_response["updated"] is True
    assert review_response["review_status"] == "ACKNOWLEDGED"

    from app.grpc_server import store

    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM scrape_security_event_audit
            WHERE event_id = ? AND action_type = 'REVIEW_STATUS_UPDATE'
            """,
            (event_id,),
        ).fetchone()
    assert int(row["total"]) >= 1


def test_apply_security_event_retention(channel: grpc.Channel) -> None:
    list_request = struct_pb2.Struct()
    json_format.ParseDict({"limit": 1}, list_request)
    list_response = json_format.MessageToDict(_security_events_call(channel)(list_request, metadata=_scrape_metadata()))
    event_id = int(list_response["events"][0]["id"])

    resolve_request = struct_pb2.Struct()
    json_format.ParseDict(
        {
            "event_id": event_id,
            "review_status": "RESOLVED",
            "admin_notes": "Resolved before retention run",
        },
        resolve_request,
    )
    _review_security_event_call(channel)(resolve_request, metadata=_scrape_metadata())

    from app.grpc_server import store  # local import avoids widening module-level imports in tests

    with store.connection() as connection:
        connection.execute(
            """
            UPDATE scrape_security_events
            SET reviewed_at = datetime('now', '-40 days')
            WHERE id = ?
            """,
            (event_id,),
        )

    retention_request = struct_pb2.Struct()
    json_format.ParseDict({"retention_days": 30}, retention_request)
    retention_response = json_format.MessageToDict(
        _apply_security_retention_call(channel)(retention_request, metadata=_scrape_metadata())
    )

    assert int(retention_response["deleted_count"]) >= 1

    from app.grpc_server import store

    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM scrape_security_event_audit
            WHERE action_type = 'RETENTION_APPLY'
            """
        ).fetchone()
    assert int(row["total"]) >= 1


def test_apply_security_event_retention_dry_run(channel: grpc.Channel) -> None:
    request = struct_pb2.Struct()
    json_format.ParseDict(
        {
            "guide_url": "https://notallowed.example.org/guide-dry-run",
            "game_title": "Blocked Dry Run",
            "commit": False,
        },
        request,
    )

    with pytest.raises(grpc.RpcError):
        _scrape_call(channel)(request, metadata=_scrape_metadata())

    list_request = struct_pb2.Struct()
    json_format.ParseDict({"limit": 1}, list_request)
    list_response = json_format.MessageToDict(_security_events_call(channel)(list_request, metadata=_scrape_metadata()))
    event_id = int(list_response["events"][0]["id"])

    resolve_request = struct_pb2.Struct()
    json_format.ParseDict(
        {
            "event_id": event_id,
            "review_status": "RESOLVED",
            "admin_notes": "Dry-run retention candidate",
        },
        resolve_request,
    )
    _review_security_event_call(channel)(resolve_request, metadata=_scrape_metadata())

    from app.grpc_server import store

    with store.connection() as connection:
        connection.execute(
            """
            UPDATE scrape_security_events
            SET reviewed_at = datetime('now', '-45 days')
            WHERE id = ?
            """,
            (event_id,),
        )

    retention_request = struct_pb2.Struct()
    json_format.ParseDict({"retention_days": 30, "dry_run": True}, retention_request)
    retention_response = json_format.MessageToDict(
        _apply_security_retention_call(channel)(retention_request, metadata=_scrape_metadata())
    )

    assert retention_response["dry_run"] is True
    assert int(retention_response["deleted_count"]) >= 1

    verify_request = struct_pb2.Struct()
    json_format.ParseDict({"limit": 200}, verify_request)
    verify_response = json_format.MessageToDict(_security_events_call(channel)(verify_request, metadata=_scrape_metadata()))
    remaining_ids = {int(item["id"]) for item in verify_response["events"]}
    assert event_id in remaining_ids

    from app.grpc_server import store

    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM scrape_security_event_audit
            WHERE action_type = 'RETENTION_DRY_RUN'
            """
        ).fetchone()
    assert int(row["total"]) >= 1


def test_domain_cooldown_enforced(channel: grpc.Channel, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "domain_request_cooldown_seconds", 120.0)
    monkeypatch.setattr(
        guide_client,
        "_fetch_html",
        lambda _: "<html><body><main><h1>Guide</h1><p>A. B. C.</p></main></body></html>",
    )
    monkeypatch.setattr(judy_client, "send_scrape", lambda proposal, commit: {"final_verdict": "APPROVED"})
    guide_client._domain_last_fetch.clear()

    request = struct_pb2.Struct()
    json_format.ParseDict({"guide_url": "https://example.com/cooldown", "game_title": "Cooldown Test", "commit": False}, request)
    _scrape_call(channel)(request, metadata=_scrape_metadata())

    with pytest.raises(grpc.RpcError) as rpc_error:
        _scrape_call(channel)(request, metadata=_scrape_metadata())

    assert rpc_error.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert "Domain cooldown active" in str(rpc_error.value.details())

    monkeypatch.setattr(settings, "domain_request_cooldown_seconds", 0.0)
