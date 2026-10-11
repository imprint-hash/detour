"""Refunds. Dodo Payments test mode when a key is set; a local ledger otherwise,
labelled as such, so a demo never claims a payment that did not happen."""
import os, uuid
import httpx
from neatlogs.decorators import span

DODO = os.environ.get("DODO_BASE_URL", "https://test.dodopayments.com")


@span(kind="TOOL", tool_name="issue_refund")
def refund(order, amount, *, reason):
    key = os.environ.get("DODO_API_KEY")
    pay = order.get("dodo_payment_id")
    if key and pay:
        r = httpx.post(f"{DODO}/refunds", headers={"Authorization": f"Bearer {key}"},
                       json={"payment_id": pay, "reason": reason}, timeout=30)
        if r.status_code < 300:
            body = r.json()
            return {"amount": round(amount, 2), "via": "dodo-test", "id": body.get("refund_id") or body.get("id")}
        return {"amount": 0, "via": "dodo-test", "error": f"{r.status_code} {r.text[:200]}"}
    return {"amount": round(amount, 2), "via": "local-ledger", "id": f"local-{uuid.uuid4().hex[:8]}"}
