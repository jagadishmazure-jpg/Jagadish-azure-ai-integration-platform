"""AgentSpec -> A2A 1.0 AgentCard (a2a-sdk protobuf types).

The card has no free-form metadata field, so the integration contract (owner, side-effect class,
SLA, eval score, allowed callers, input schemas, identity mode) travels in a capabilities extension."""

from __future__ import annotations

from a2a.types import AgentCapabilities, AgentCard, AgentExtension, AgentInterface, AgentProvider, AgentSkill
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

from aiip.a2a.specs import AgentSpec
from aiip.shared.http import service_url

CONTRACT_EXT = "urn:aiip:integration-contract:v1"
A2A_PROTOCOL_VERSION = "1.0"


def endpoint(spec: AgentSpec) -> str:
    base = service_url(spec.service) or f"http://{spec.service}.inproc"
    return base + spec.rpc_path


def contract(spec: AgentSpec) -> dict:
    return {
        "agent_id": spec.id,
        "owner": spec.owner,
        "version": spec.version,
        "side_effect_class": spec.side_effect_class,
        "identity_mode": spec.identity_mode,
        "sla": {"p95_ms": spec.sla_p95_ms, "availability": spec.sla_availability},
        "eval_score": spec.eval_score,
        "allowed_callers": list(spec.allowed_callers),
        "system_of_record": spec.system_of_record,
        "skills": {
            s.id: {"side_effect": s.side_effect, "input_schema": s.input_schema, "hitl": s.hitl}
            for s in spec.skills
        },
        "requires_headers": ["authorization", "traceparent", "x-tenant-id", "A2A-Version"],
    }


def to_agent_card(spec: AgentSpec) -> AgentCard:
    params = Struct()
    params.update(contract(spec))
    return AgentCard(
        name=spec.name,
        description=spec.description,
        version=spec.version,
        provider=AgentProvider(
            organization="AI Integration Platform (demo)", url="https://github.com/jagadishmazure-jpg"
        ),
        supported_interfaces=[
            AgentInterface(
                url=endpoint(spec), protocol_binding="JSONRPC", protocol_version=A2A_PROTOCOL_VERSION
            )
        ],
        capabilities=AgentCapabilities(
            streaming=False,
            extensions=[
                AgentExtension(
                    uri=CONTRACT_EXT,
                    description="Integration contract: owner, SLA, side effects, callers, schemas, eval score",
                    required=False,
                    params=params,
                )
            ],
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id=s.id,
                name=s.name,
                description=f"{s.description} [side-effect: {s.side_effect}]",
                tags=[s.side_effect],
                input_modes=["application/json"],
                output_modes=["application/json"],
            )
            for s in spec.skills
        ],
    )


def card_json(spec: AgentSpec) -> dict:
    return MessageToDict(to_agent_card(spec))
