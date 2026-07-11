from __future__ import annotations

import os

import grpc
import pytest
from google.protobuf import empty_pb2, json_format, struct_pb2

os.environ["JUDY_GRPC_TARGET"] = "127.0.0.1:50066"
os.environ["OUTBOUND_SIGNATURE_SECRET"] = "milo-dev-secret"
os.environ["SCRAPE_DB_PATH"] = ":memory:"
os.environ["INBOUND_AUTH_TOKEN"] = "test-inbound-token"

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
