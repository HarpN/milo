from __future__ import annotations

import grpc
from google.protobuf import json_format, struct_pb2

from .config import settings
from .models import JudyProposal
from .signer import sign_payload


class JudyClient:
    def send_scrape(self, proposal: JudyProposal, commit: bool) -> dict:
        payload = proposal.model_dump()
        request_message = struct_pb2.Struct()
        json_format.ParseDict(payload, request_message)
        normalized_payload = json_format.MessageToDict(request_message)
        signature = sign_payload(settings.outbound_signature_secret, normalized_payload)
        method = "/judy.JudyCouncil/CommitProposal" if commit else "/judy.JudyCouncil/JudgeProposal"

        with grpc.insecure_channel(settings.judy_grpc_target) as channel:
            rpc = channel.unary_unary(
                method,
                request_serializer=struct_pb2.Struct.SerializeToString,
                response_deserializer=struct_pb2.Struct.FromString,
            )
            response = rpc(
                request_message,
                timeout=settings.judy_timeout_seconds,
                metadata=((settings.outbound_signature_header, signature),),
            )
            return json_format.MessageToDict(response)
