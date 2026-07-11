from __future__ import annotations

from datetime import datetime, timezone

import grpc
from google.protobuf import json_format, struct_pb2

from .config import settings
from .models import JudyProposal
from .signer import build_replay_metadata, sign_payload


class JudyClient:
    def _validate_fetched_at(self, payload: dict) -> None:
        fetched_at_raw = payload.get("guide_document", {}).get("fetched_at")
        if not fetched_at_raw:
            raise ValueError("guide_document.fetched_at is required for replay protection")

        fetched_at = datetime.fromisoformat(fetched_at_raw.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_seconds = abs((now - fetched_at).total_seconds())
        if age_seconds > settings.replay_ttl_seconds:
            raise ValueError("Guide payload is outside replay TTL window")

    def _create_channel(self):
        if not settings.judy_tls_enabled:
            return grpc.insecure_channel(settings.judy_grpc_target)

        root_certificates = None
        if settings.judy_tls_ca_cert_path:
            with open(settings.judy_tls_ca_cert_path, "rb") as cert_file:
                root_certificates = cert_file.read()

        private_key = None
        certificate_chain = None
        if settings.judy_mtls_enabled:
            with open(settings.judy_tls_client_key_path, "rb") as key_file:
                private_key = key_file.read()
            with open(settings.judy_tls_client_cert_path, "rb") as cert_file:
                certificate_chain = cert_file.read()

        credentials = grpc.ssl_channel_credentials(
            root_certificates=root_certificates,
            private_key=private_key,
            certificate_chain=certificate_chain,
        )
        return grpc.secure_channel(settings.judy_grpc_target, credentials)

    def send_scrape(self, proposal: JudyProposal, commit: bool) -> dict:
        payload = proposal.model_dump()
        self._validate_fetched_at(payload)

        issued_at, nonce = build_replay_metadata()
        signed_envelope = {
            "payload": payload,
            "issued_at": issued_at,
            "nonce": nonce,
            "key_id": settings.outbound_key_id,
        }

        request_message = struct_pb2.Struct()
        json_format.ParseDict(signed_envelope, request_message)
        normalized_payload = json_format.MessageToDict(request_message)
        signature = sign_payload(settings.outbound_signature_secret, normalized_payload)
        method = "/judy.JudyCouncil/CommitProposal" if commit else "/judy.JudyCouncil/JudgeProposal"

        with self._create_channel() as channel:
            rpc = channel.unary_unary(
                method,
                request_serializer=struct_pb2.Struct.SerializeToString,
                response_deserializer=struct_pb2.Struct.FromString,
            )
            response = rpc(
                request_message,
                timeout=settings.judy_timeout_seconds,
                metadata=(
                    (settings.outbound_signature_header, signature),
                    ("x-milo-key-id", settings.outbound_key_id),
                    ("x-milo-issued-at", issued_at),
                    ("x-milo-nonce", nonce),
                ),
            )
            return json_format.MessageToDict(response)
