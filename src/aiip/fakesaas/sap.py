"""SAP S/4HANA OData stand-in: OAuth client credentials, CSRF token fetch, sales order read /
create (Repeatability-Request-ID idempotency), sales order simulation, outbound delivery, purchase
order read, and a customer-namespace AP service (ZAP_INVOICE_SRV) whose function imports answer
HTTP 200 with BAPI-style RETURN messages, including functional errors (Type "E")."""

from __future__ import annotations

import secrets
from decimal import Decimal

from fastapi import APIRouter, Form, Header, Request, Response
from fastapi.responses import JSONResponse

from aiip.fakesaas import state
from aiip.shared.secrets import RESOLVER

router = APIRouter(prefix="/sap")
ODATA = "/opu/odata/sap"
CLIENT_SECRET_REF = "kv://aiip-local-kv/sap-oauth-client-secret"


def _sap() -> dict:
    return state.DATA["sap"]


def _odata_error(status: int, code: str, msg: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": {"lang": "en", "value": msg}}}, status)


def _auth(authorization: str | None, x_csrf_token: str | None, response: Response) -> dict:
    info = state.token_info("sap", authorization)
    if x_csrf_token and x_csrf_token.lower() == "fetch":
        tok = secrets.token_urlsafe(16)
        _sap()["csrf"].add(tok)
        response.headers["x-csrf-token"] = tok
    return info


def _csrf_ok(x_csrf_token: str | None) -> bool:
    return bool(x_csrf_token) and x_csrf_token in _sap()["csrf"]


def _ret(msg_type: str, msg_id: str, number: str, text: str) -> dict:
    return {"Type": msg_type, "Id": msg_id, "Number": number, "Message": text}


@router.post("/oauth/token")
async def token(grant_type: str = Form(...), client_id: str = Form(...), client_secret: str = Form(...)):
    await state.apply_fault("sap")
    if grant_type != "client_credentials" or client_secret != RESOLVER.resolve(CLIENT_SECRET_REF):
        return JSONResponse({"error": "invalid_client"}, 401)
    return {"access_token": state.issue_token("sap", client_id), "token_type": "bearer", "expires_in": 3600}


@router.get(ODATA + "/API_SALES_ORDER_SRV/A_SalesOrder('{so}')")
async def get_sales_order(
    so: str,
    response: Response,
    authorization: str | None = Header(default=None),
    x_csrf_token: str | None = Header(default=None),
):
    await state.apply_fault("sap")
    _auth(authorization, x_csrf_token, response)
    order = _sap()["sales_orders"].get(so)
    if not order:
        return _odata_error(404, "SALES_ORDER/004", f"Sales order {so} does not exist")
    return {"d": {"__metadata": {"type": "API_SALES_ORDER_SRV.A_SalesOrderType"}, **order}}


@router.post(ODATA + "/API_SALES_ORDER_SRV/A_SalesOrder")
async def create_sales_order(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    x_csrf_token: str | None = Header(default=None),
    repeatability_request_id: str | None = Header(default=None),
):
    await state.apply_fault("sap")
    _auth(authorization, None, response)
    if not _csrf_ok(x_csrf_token):
        return JSONResponse(
            {"error": "CSRF token validation failed"}, 403, headers={"x-csrf-token": "Required"}
        )
    idem = _sap()["idempotency"]
    if repeatability_request_id and repeatability_request_id in idem:
        response.status_code = 201
        response.headers["repeatability-result"] = "accepted"  # replayed, not re-executed
        return idem[repeatability_request_id]
    body = await request.json()
    items = body.get("to_Item", {}).get("results", [])
    if not items:
        return _odata_error(400, "V1/320", "No items in sales order")
    so = str(4500001 + len(_sap()["sales_orders"]))
    total = sum(
        Decimal(_sap()["materials"].get(i["Material"], "0")) * Decimal(i["RequestedQuantity"]) for i in items
    )
    order = {
        "SalesOrder": so,
        "SoldToParty": body.get("SoldToParty"),
        "SalesOrderType": body.get("SalesOrderType", "OR"),
        "TotalNetAmount": f"{total:.2f}",
        "TransactionCurrency": "USD",
        "OverallSDProcessStatus": "A",
        "OverallDeliveryStatus": "A",
        "RequestedDeliveryDate": body.get("RequestedDeliveryDate", ""),
        "PurchaseOrderByCustomer": body.get("PurchaseOrderByCustomer", ""),
    }
    _sap()["sales_orders"][so] = order
    result = {"d": order}
    if repeatability_request_id:
        idem[repeatability_request_id] = result
    response.status_code = 201
    return result


@router.post(ODATA + "/API_SALES_ORDER_SIMULATION_SRV/A_SalesOrderSimulation")
async def simulate_sales_order(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    x_csrf_token: str | None = Header(default=None),
):
    await state.apply_fault("sap")
    _auth(authorization, None, response)
    if not _csrf_ok(x_csrf_token):
        return JSONResponse(
            {"error": "CSRF token validation failed"}, 403, headers={"x-csrf-token": "Required"}
        )
    body = await request.json()
    lines, total = [], Decimal("0")
    for i in body.get("to_Item", {}).get("results", []):
        price = Decimal(_sap()["materials"].get(i["Material"], "0"))
        net = price * Decimal(i["RequestedQuantity"])
        total += net
        lines.append(
            {
                "Material": i["Material"],
                "RequestedQuantity": i["RequestedQuantity"],
                "NetAmount": f"{net:.2f}",
            }
        )
    response.status_code = 201
    return {
        "d": {
            "SoldToParty": body.get("SoldToParty"),
            "TotalNetAmount": f"{total:.2f}",
            "to_Item": {"results": lines},
            "Simulated": True,
        }
    }


@router.get(ODATA + "/API_OUTBOUND_DELIVERY_SRV;v=0002/A_OutbDeliveryHeader('{doc}')")
async def get_delivery(doc: str, response: Response, authorization: str | None = Header(default=None)):
    await state.apply_fault("sap")
    _auth(authorization, None, response)
    d = _sap()["deliveries"].get(doc)
    if not d:
        return _odata_error(404, "VL/001", f"Delivery {doc} does not exist")
    return {"d": d}


@router.get(ODATA + "/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder('{po}')")
async def get_purchase_order(po: str, response: Response, authorization: str | None = Header(default=None)):
    await state.apply_fault("sap")
    _auth(authorization, None, response)
    d = _sap()["purchase_orders"].get(po)
    if not d:
        return _odata_error(404, "ME/006", f"Purchase order {po} does not exist")
    return {"d": d}


# ---- ZAP_INVOICE_SRV: function imports, BAPI-style RETURN table, always HTTP 200 on functional errors
@router.post(ODATA + "/ZAP_INVOICE_SRV/{function}")
async def invoice_function(
    function: str,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    x_csrf_token: str | None = Header(default=None),
    repeatability_request_id: str | None = Header(default=None),
):
    await state.apply_fault("sap")
    _auth(authorization, None, response)
    if not _csrf_ok(x_csrf_token):
        return JSONResponse(
            {"error": "CSRF token validation failed"}, 403, headers={"x-csrf-token": "Required"}
        )
    idem = _sap()["idempotency"]
    key = f"{function}:{repeatability_request_id}" if repeatability_request_id else None
    if key and key in idem:
        response.headers["repeatability-result"] = "accepted"
        return idem[key]
    body = await request.json()
    inv = _sap()["invoices"]
    doc = body.get("InvoiceDocument", "")
    ret: list[dict] = []
    out: dict = {}
    if function == "ParkInvoice":
        if body.get("TaxCode") not in _sap()["valid_tax_codes"]:
            ret.append(_ret("E", "FTAX", "002", f"Tax code {body.get('TaxCode')} not defined for country US"))
        elif body.get("PurchaseOrder") not in _sap()["purchase_orders"]:
            ret.append(_ret("E", "M8", "181", f"Purchase order {body.get('PurchaseOrder')} does not exist"))
        else:
            doc = f"51056{len(inv) + 1:05d}"
            inv[doc] = {**body, "InvoiceDocument": doc, "Status": "PARKED"}
            ret.append(_ret("S", "M8", "060", f"Document {doc} was parked"))
            out = {"InvoiceDocument": doc, "Status": "PARKED"}
    elif function in {"PostParkedInvoice", "DeleteParkedInvoice", "ReverseInvoice", "SchedulePayment"}:
        rec = inv.get(doc)
        wanted = {
            "PostParkedInvoice": ("PARKED", "POSTED"),
            "DeleteParkedInvoice": ("PARKED", "DELETED"),
            "ReverseInvoice": ("POSTED", "REVERSED"),
            "SchedulePayment": ("POSTED", "PAYMENT_SCHEDULED"),
        }[function]
        if rec is None:
            ret.append(_ret("E", "M8", "123", f"Invoice document {doc} not found"))
        elif rec["Status"] != wanted[0]:
            ret.append(
                _ret("E", "M8", "401", f"Invoice {doc} has status {rec['Status']}; expected {wanted[0]}")
            )
        elif (
            function == "PostParkedInvoice"
            and body.get("GrossAmount")
            and Decimal(body["GrossAmount"]) != Decimal(rec.get("GrossAmount") or "0")
        ):
            ret.append(
                _ret(
                    "E",
                    "M8",
                    "108",
                    f"Stated amount {body['GrossAmount']} does not match parked amount of invoice {doc}",
                )
            )
        elif function == "SchedulePayment" and rec.get("PaymentBlock"):
            ret.append(_ret("E", "F5", "702", f"Payment block set on supplier {rec.get('Supplier')}"))
        else:
            rec["Status"] = wanted[1]
            ret.append(_ret("S", "M8", "075", f"Invoice {doc} {wanted[1].lower()}"))
            out = {"InvoiceDocument": doc, "Status": wanted[1]}
    else:
        return _odata_error(404, "FUNC/404", f"Function import {function} not found")
    result = {"d": {**out, "Return": {"results": ret}}}
    if key and not any(r["Type"] == "E" for r in ret):
        idem[key] = result
    return result  # HTTP 200 even when RETURN carries Type "E"
