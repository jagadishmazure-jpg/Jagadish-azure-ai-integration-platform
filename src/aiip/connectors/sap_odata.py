"""SAP S/4HANA OData connector pack (sandbox stand-in target).

auth         OAuth client credentials (technical communication user); CSRF token fetch before writes
user-scoped  production uses principal propagation (OAuth SAML bearer); sandbox logs the subject only
idempotency  Repeatability-Request-ID header (SAP's RAP idempotency) = the gateway idempotency key
errors       OData error JSON by status; HTTP 200 with BAPI RETURN Type E/A -> business_reject"""

from __future__ import annotations

from datetime import date

import httpx

from aiip.connectors.auth import OAuthClientCredentials
from aiip.connectors.base import (
    AUTH_EXPIRED,
    BUSINESS_REJECT,
    ConnectorContext,
    ConnectorError,
    ConnectorPack,
    ConnectorResult,
    retry_after,
    send,
    status_class,
)
from aiip.connectors.canonical import Delivery, InvoiceResult, OrderSimulation, PurchaseOrder, SalesOrder

ODATA = "/opu/odata/sap"
STATUS = {"A": "open", "B": "in_process", "C": "completed"}


class SapODataPack(ConnectorPack):
    name = "sap_odata"
    vendor = "sap"
    auth_strategy = "OAuth 2.0 client credentials (secret in Key Vault) + x-csrf-token fetch"
    user_scoped_strategy = (
        "principal propagation via OAuth SAML bearer in production; subject logged in sandbox"
    )
    idempotency_strategy = "Repeatability-Request-ID header"
    rate_limit_headers = ()
    stored_fields = {
        "A_SalesOrder": (
            "SalesOrder",
            "SoldToParty",
            "TotalNetAmount",
            "TransactionCurrency",
            "OverallSDProcessStatus",
            "OverallDeliveryStatus",
            "RequestedDeliveryDate",
        ),
        "A_OutbDeliveryHeader": (
            "DeliveryDocument",
            "ReferenceSDDocument",
            "DeliveryDate",
            "RevisedDeliveryDate",
            "Carrier",
        ),
        "A_PurchaseOrder": (
            "PurchaseOrder",
            "Supplier",
            "DocumentCurrency",
            "CostCenter",
            "to_PurchaseOrderItem",
        ),
    }

    def __init__(self) -> None:
        super().__init__()
        self.auth = OAuthClientCredentials(
            self.http, "/oauth/token", "AIIP_COMM_USER", "kv://aiip-local-kv/sap-oauth-client-secret"
        )
        self.operations = {
            "get_sales_order": self.get_sales_order,
            "simulate_sales_order": self.simulate_sales_order,
            "create_sales_order": self.create_sales_order,
            "get_delivery": self.get_delivery,
            "get_purchase_order": self.get_purchase_order,
            "park_invoice": self._fn("ParkInvoice"),
            "post_parked_invoice": self._fn("PostParkedInvoice"),
            "delete_parked_invoice": self._fn("DeleteParkedInvoice"),
            "reverse_invoice": self._fn("ReverseInvoice"),
            "schedule_payment": self._fn("SchedulePayment"),
        }

    def map_error(self, resp: httpx.Response) -> ConnectorError:
        try:
            err = resp.json().get("error", {})
            code = err.get("code", "") if isinstance(err, dict) else ""
            msg = err.get("message", {}).get("value", "") if isinstance(err, dict) else str(err)
        except Exception:
            code, msg = "", ""
        return ConnectorError(
            status_class(resp.status_code) or AUTH_EXPIRED,
            msg or f"sap HTTP {resp.status_code}",
            vendor_code=code,
            retry_after=retry_after(resp.headers),
        )

    async def _csrf(self, ctx: ConnectorContext) -> str:
        r = await send(
            self,
            self.auth,
            "GET",
            f"{ODATA}/API_SALES_ORDER_SRV/A_SalesOrder('4500001')",
            ctx,
            headers={"x-csrf-token": "Fetch"},
        )
        return r.headers.get("x-csrf-token", "")

    async def _write(self, path: str, body: dict, ctx: ConnectorContext) -> httpx.Response:
        headers = {"x-csrf-token": await self._csrf(ctx)}
        if ctx.idempotency_key:
            headers["Repeatability-Request-ID"] = ctx.idempotency_key
        return await send(self, self.auth, "POST", path, ctx, json=body, headers=headers)

    @staticmethod
    def to_order(d: dict) -> dict:
        return SalesOrder(
            source_system="sap",
            order_id=d["SalesOrder"],
            customer_ref=d.get("SoldToParty") or "",
            net_amount=d.get("TotalNetAmount") or "0",
            currency=d.get("TransactionCurrency") or "",
            status=STATUS.get(d.get("OverallSDProcessStatus", ""), "unknown"),
            delivery_status=STATUS.get(d.get("OverallDeliveryStatus", ""), "unknown"),
            requested_delivery_date=d.get("RequestedDeliveryDate") or "",
        ).model_dump()

    async def get_sales_order(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        r = await send(
            self, self.auth, "GET", f"{ODATA}/API_SALES_ORDER_SRV/A_SalesOrder('{args['order_id']}')", ctx
        )
        return ConnectorResult(self.to_order(r.json()["d"]))

    @staticmethod
    def _items(args: dict) -> dict:
        return {
            "results": [
                {"Material": i["material"], "RequestedQuantity": str(i["quantity"])} for i in args["items"]
            ]
        }

    async def simulate_sales_order(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        r = await self._write(
            f"{ODATA}/API_SALES_ORDER_SIMULATION_SRV/A_SalesOrderSimulation",
            {"SoldToParty": args["customer_ref"], "to_Item": self._items(args)},
            ctx,
        )
        d = r.json()["d"]
        sim = OrderSimulation(
            source_system="sap",
            customer_ref=d["SoldToParty"],
            net_amount=d["TotalNetAmount"],
            lines=len(d["to_Item"]["results"]),
            simulated=True,
        )
        return ConnectorResult(sim.model_dump())

    async def create_sales_order(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        body = {
            "SalesOrderType": "OR",
            "SoldToParty": args["customer_ref"],
            "PurchaseOrderByCustomer": args.get("customer_po", ""),
            "to_Item": self._items(args),
        }
        r = await self._write(f"{ODATA}/API_SALES_ORDER_SRV/A_SalesOrder", body, ctx)
        return ConnectorResult(
            self.to_order(r.json()["d"]),
            replayed=r.headers.get("repeatability-result") == "accepted",
            vendor_status=r.status_code,
        )

    async def get_delivery(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        r = await send(
            self,
            self.auth,
            "GET",
            f"{ODATA}/API_OUTBOUND_DELIVERY_SRV;v=0002/A_OutbDeliveryHeader('{args['delivery_id']}')",
            ctx,
        )
        d = r.json()["d"]
        late = (date.fromisoformat(d["RevisedDeliveryDate"]) - date.fromisoformat(d["DeliveryDate"])).days
        return ConnectorResult(
            Delivery(
                source_system="sap",
                delivery_id=d["DeliveryDocument"],
                order_id=d["ReferenceSDDocument"],
                planned_date=d["DeliveryDate"],
                revised_date=d["RevisedDeliveryDate"],
                carrier=d.get("Carrier", ""),
                days_late=max(0, late),
            ).model_dump()
        )

    async def get_purchase_order(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        r = await send(
            self,
            self.auth,
            "GET",
            f"{ODATA}/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder('{args['po_id']}')",
            ctx,
            params={"$expand": "to_PurchaseOrderItem"},
        )
        d = r.json()["d"]
        total = sum(
            float(i["NetPriceAmount"]) * float(i["OrderQuantity"])
            for i in d["to_PurchaseOrderItem"]["results"]
        )
        return ConnectorResult(
            PurchaseOrder(
                source_system="sap",
                po_id=d["PurchaseOrder"],
                supplier_id=d["Supplier"],
                currency=d["DocumentCurrency"],
                cost_center=d["CostCenter"],
                total=f"{total:.2f}",
            ).model_dump()
        )

    def _fn(self, function: str):
        async def op(args: dict, ctx: ConnectorContext) -> ConnectorResult:
            body = {
                "InvoiceDocument": args.get("invoice_document", ""),
                "PurchaseOrder": args.get("po_id", ""),
                "Supplier": args.get("supplier_id", ""),
                "GrossAmount": str(args.get("amount", "")),
                "TaxCode": args.get("tax_code", ""),
                "SupplierInvoiceIDByInvcgParty": args.get("vendor_invoice_no", ""),
            }
            r = await self._write(f"{ODATA}/ZAP_INVOICE_SRV/{function}", body, ctx)
            d = r.json()["d"]
            msgs = d.get("Return", {}).get("results", [])
            errors = [m for m in msgs if m.get("Type") in {"E", "A"}]
            if errors:  # HTTP 200, functional failure
                e = errors[0]
                raise ConnectorError(BUSINESS_REJECT, e["Message"], vendor_code=f"{e['Id']}/{e['Number']}")
            res = InvoiceResult(
                source_system="sap",
                invoice_document=d.get("InvoiceDocument", ""),
                status=d.get("Status", ""),
                messages=[m["Message"] for m in msgs],
            )
            return ConnectorResult(
                res.model_dump(), replayed=r.headers.get("repeatability-result") == "accepted"
            )

        return op
