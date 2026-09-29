"""Connector registry: `<pack>.<operation>` -> pack instance. One instance per process."""

from __future__ import annotations

from aiip.connectors.base import ConnectorPack
from aiip.connectors.dataverse import DataversePack
from aiip.connectors.jira import JiraPack
from aiip.connectors.salesforce import SalesforcePack
from aiip.connectors.sap_odata import SapODataPack
from aiip.connectors.servicenow import ServiceNowPack
from aiip.connectors.workday import WorkdayPack

PACKS: dict[str, ConnectorPack] = {}


def packs() -> dict[str, ConnectorPack]:
    if not PACKS:
        for cls in (SalesforcePack, ServiceNowPack, WorkdayPack, DataversePack, SapODataPack, JiraPack):
            p = cls()
            PACKS[p.name] = p
    return PACKS


def resolve(connector: str) -> tuple[ConnectorPack, str]:
    pack, _, op = connector.partition(".")
    p = packs().get(pack)
    if p is None or op not in p.operations:
        raise KeyError(connector)
    return p, op


def reset() -> None:
    PACKS.clear()
