import frappe
import json
from frappe.utils import now_datetime
from werkzeug.wrappers import Response
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERIFY_TOKEN = "erpnext_whatsapp_2026"
WHATSAPP_SOURCE = "WhatsApp"
WHATSAPP_PHONE_NUMBER_ID = "1269335696258495"
WHATSAPP_ACCESS_TOKEN = "EAAOdxd27ryMBSZAgB8y7Uic0230zYZARa5BeHDVNvsoqEaZAbnoLu2iy6xA7LOO2LTz4VD5R1fPmlfETeJAneXi0e8bEwTqp7ef22RwcP7CQ43cLZCZBzJhuLw0CYhscztUiyhJ33ReozUDOZB9W9vMzXRjGTpTZAyDnZA3LcPIDXVnJWUAGVcktWqn0dROEawZDZD"
# ===========================================================================
# Public API endpoints
# ===========================================================================


# DEPRECATED: Webhook is now handled by frappe_whatsapp app.
def webhook():
    """
    Handles both WhatsApp webhook verification (GET) and
    incoming message events (POST).
    """
    if frappe.request.method == "GET":
        return _handle_webhook_verification()

    elif frappe.request.method == "POST":
        return _handle_incoming_message()


@frappe.whitelist(allow_guest=True)
def create_lead():
    """
    Manually create a CRM Lead from a JSON payload.
    Expected fields: name, phone (required), email, message.
    """
    data = frappe.request.get_json()

    if not data:
        return {"status": "error", "message": "No data received"}

    lead_name = data.get("name")
    mobile = data.get("phone")
    email = data.get("email")

    if not mobile:
        return {"status": "error", "message": "Phone number is required"}

    # Check for a duplicate Lead by mobile number
    existing = frappe.db.exists("CRM Lead", {"mobile_no": mobile})
    if existing:
        return {"status": "exists", "lead": existing}

    lead = frappe.new_doc("CRM Lead")
    lead.first_name = lead_name or mobile
    lead.mobile_no = mobile
    if email:
        lead.email = email
    lead.source = WHATSAPP_SOURCE
    lead.insert(ignore_permissions=True)
    frappe.db.commit()

    return {"status": "success", "lead": lead.name}


@frappe.whitelist(allow_guest=True)
def send_whatsapp_message(
    mobile_no: str,
    message: str,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    create_comment: bool = True
):
    """
    Send an outgoing WhatsApp message using the Meta Cloud API.
    """
    url = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": mobile_no,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message,
        },
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()

        frappe.logger("whatsapp").info(f"WhatsApp message sent to {mobile_no}")

        # Log the outgoing message as a WhatsApp Message so it shows in the CRM UI
        if create_comment:
            if not reference_doctype or not reference_name:
                lead = frappe.db.exists("CRM Lead", {"mobile_no": mobile_no})
                if lead:
                    reference_doctype = "CRM Lead"
                    reference_name = lead

            if reference_doctype and reference_name:
                try:
                    wa_msg = frappe.new_doc("WhatsApp Message")
                    wa_msg.type = "Outgoing"
                    wa_msg.to = mobile_no
                    wa_msg.message = message
                    wa_msg.reference_doctype = reference_doctype
                    wa_msg.reference_name = reference_name
                    wa_msg.insert(ignore_permissions=True)
                    frappe.db.commit()
                except Exception as e:
                    frappe.logger("whatsapp").error(f"Failed to log WhatsApp Message: {e}")

        return {"status": "success", "response": response.json()}

    except requests.exceptions.RequestException as e:
        err_msg = str(e)
        if e.response is not None:
            err_msg += f"\nResponse: {e.response.text}"

        frappe.log_error(title="WhatsApp Send Message Error", message=err_msg)
        return {"status": "error", "message": err_msg}


