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
WHATSAPP_ACCESS_TOKEN = "EAAOdxd27ryMBSIHizRynHVzBhPZARbAXjkUiVB9GfiytEGJeoKwQSWuI4wMkcCwCFKsrZBzN6grT2krMFfW1EKLkCCnUykpjIqRWL4ZAYMmZC0OS7JyqhUvNXYcNSLcSx4qOLnJyDhds7rnjxXBlIRGSLlPdUrqVZCUPH3ZCyGSH01BnBDOYeH5MrcxKfUhAZDZD"


# ===========================================================================
# Public API endpoints
# ===========================================================================


@frappe.whitelist(allow_guest=True)
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

        # Log the outgoing message as a comment
        if create_comment:
            if not reference_doctype or not reference_name:
                lead = frappe.db.exists("CRM Lead", {"mobile_no": mobile_no})
                if lead:
                    reference_doctype = "CRM Lead"
                    reference_name = lead

            if reference_doctype and reference_name:
                comment = frappe.new_doc("Comment")
                comment.comment_type = "Comment"
                comment.reference_doctype = reference_doctype
                comment.reference_name = reference_name
                comment.content = f"<strong>Outgoing WhatsApp:</strong><br>{message}"
                comment.insert(ignore_permissions=True)
                frappe.db.commit()

        return {"status": "success", "response": response.json()}

    except requests.exceptions.RequestException as e:
        err_msg = str(e)
        if e.response is not None:
            err_msg += f"\nResponse: {e.response.text}"

        frappe.log_error(title="WhatsApp Send Message Error", message=err_msg)
        return {"status": "error", "message": err_msg}


# ===========================================================================
# Private helpers — webhook handling
# ===========================================================================

def handle_outgoing_comment(doc, method):

    if doc.reference_doctype != "CRM Lead":
        return

    if getattr(doc, "comment_type", "") != "Comment":
        return

    from frappe.utils import strip_html
    plain_text = strip_html(doc.content).strip()

    # Avoid infinite loops from our own auto-generated comments
    if plain_text.startswith("Outgoing WhatsApp:") or plain_text.startswith("WhatsApp Message:") or plain_text.startswith("Incoming WhatsApp"):
        return

    lead = frappe.get_doc("CRM Lead", doc.reference_name)
    if not lead.mobile_no:
        return

    if plain_text:
        # Send without duplicating the comment
        send_whatsapp_message(lead.mobile_no, plain_text, create_comment=False)


def _handle_webhook_verification():
    """
    Respond to WhatsApp's GET challenge to verify this endpoint.
    Returns the challenge string as plain text on success, 403 on failure.
    """
    mode = frappe.form_dict.get("hub.mode")
    token = frappe.form_dict.get("hub.verify_token")
    challenge = frappe.form_dict.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(challenge, status=200, mimetype="text/plain")

    return Response("Invalid Verify Token", status=403, mimetype="text/plain")


def _handle_incoming_message():
    """
    Process an incoming WhatsApp Cloud API POST payload.

    Handles two event types:
      • messages  — Incoming texts from users → create/update Lead + comment.
      • statuses  — Delivery receipts when YOU send a message to someone.
                    If no Lead exists for that recipient, one is created.
                    This covers the case where your Meta number initiates
                    the conversation (e.g. sending a template/QR message).

    Payload shape (simplified):
    {
      "entry": [{
        "changes": [{
          "value": {
            "contacts": [{"profile": {"name": "..."}, "wa_id": "..."}],
            "messages": [{"from": "...", "text": {"body": "..."}, "timestamp": "..."}],
            "statuses": [{"id": "...", "status": "sent", "recipient_id": "919...", "timestamp": "..."}]
          }
        }]
      }]
    }
    """
    data = frappe.request.get_json(silent=True) or {}
    print("\n===== Incoming WhatsApp Payload =====")
    print(json.dumps(data, indent=4))
    frappe.logger("whatsapp").info(f"WhatsApp POST received: {data}")

    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                # ── Incoming messages from users ──────────────────────────
                if "messages" in value:
                    messages = value.get("messages", [])
                    contacts = value.get("contacts", [])
                    for message in messages:
                        _process_whatsapp_message(message, contacts)

                # ── Outgoing delivery status → create Lead for recipient ──
                # When your Meta number sends a message to someone,
                # WhatsApp fires a status event (sent/delivered/read).
                # We create a Lead on the FIRST status so the contact
                # appears in CRM immediately — even before they reply.
                if "statuses" in value:
                    for status in value.get("statuses", []):
                        status_type = status.get("status", "")
                        recipient = status.get("recipient_id", "").strip()

                        print(f"\n[WhatsApp Status] {status_type} → {recipient}")

                        # Only act on the first event (sent), not repeated delivered/read
                        if status_type == "sent" and recipient:
                            existing = frappe.db.exists("CRM Lead", {"mobile_no": recipient})
                            if not existing:
                                print(f"[WhatsApp] Creating Lead for outbound recipient: {recipient}")
                                _create_lead_from_whatsapp(
                                    phone=recipient,
                                    name=None,   # No profile name available from status
                                    first_message="",
                                    message_datetime=_parse_whatsapp_timestamp(
                                        status.get("timestamp")
                                    ),
                                )
                            else:
                                print(f"[WhatsApp] Lead already exists for recipient: {recipient}")

    except Exception:
        frappe.log_error(
            title="WhatsApp Webhook Processing Error",
            message=frappe.get_traceback(),
        )

    # Always return 200 so Meta doesn't retry the delivery
    frappe.local.response["http_status_code"] = 200
    return {"status": "received"}


# ===========================================================================
# Private helpers — Lead auto-creation
# ===========================================================================


def _process_whatsapp_message(message: dict, contacts: list):
    # ── 1. Extract fields ─────────────────────────────────────────────────
    sender_phone = message.get("from", "").strip()
    if not sender_phone:
        frappe.logger("whatsapp").warning("WhatsApp message missing 'from' field, skipping.")
        return

    # The WhatsApp contacts array is indexed in the same order as messages
    sender_name = _extract_sender_name(contacts, sender_phone)

    # Message body — only "text" type supported for now; extend as needed
    msg_type = message.get("type", "")
    if msg_type == "text":
        body = message.get("text", {}).get("body", "")
        print("\n===== WhatsApp Message =====")
        print("Sender Name :", sender_name)
        print("Phone       :", sender_phone)
        print("Message     :", body)
        print("===========================\n")
    else:
        # Non-text messages (image, audio, etc.) — record type instead
        body = f"[{msg_type} message]"

    # WhatsApp unique message ID — used for deduplication
    message_id = message.get("id", "")

    # Unix timestamp from WhatsApp → Frappe datetime string
    raw_ts = message.get("timestamp")
    message_datetime = _parse_whatsapp_timestamp(raw_ts)

    # ── 2. Check for existing Lead ────────────────────────────────────────
    existing_lead = frappe.db.exists("CRM Lead", {"mobile_no": sender_phone})

    if existing_lead:
        frappe.logger("whatsapp").info(
            f"WhatsApp: Lead already exists for {sender_phone} → {existing_lead}"
        )
        # Every message from this contact — first or repeat — is saved as a comment
        _save_incoming_comment(
            lead_name=existing_lead,
            body=body,
            sender_name=sender_name,
            message_id=message_id,
        )
        return

    # ── 3. Create a new Lead ──────────────────────────────────────────────
    _create_lead_from_whatsapp(
        phone=sender_phone,
        name=sender_name,
        first_message=body,
        message_datetime=message_datetime,
    )


def _create_lead_from_whatsapp(
    phone: str,
    name: str | None,
    first_message: str,
    message_datetime,
):
    print("\n===== Creating New Lead from WhatsApp =====")
    print(f"Phone       : {phone}")
    print(f"Name        : {name}")
    print(f"First Msg   : {first_message}")
    print(f"Datetime    : {message_datetime}")
    print("===========================================\n")

    try:
        # Ensure the "WhatsApp" source exists (idempotent)
        _ensure_whatsapp_source()

        lead = frappe.new_doc("CRM Lead")

        # Identity
        lead.first_name = name or phone
        lead.lead_name = name or phone

        # Contact info
        lead.mobile_no = phone

        # Origin
        lead.source = WHATSAPP_SOURCE

        # Status: CRMLead.validate_status() sets it automatically on insert,
        # but we set it explicitly so it's clear in the code.
        lead.status = _get_default_open_status()

        # WhatsApp-specific custom fields
        # (safe-set: won't crash if these custom fields don't exist in DocType)
        try:
            lead.first_message = first_message or ""
        except Exception:
            pass
        try:
            if message_datetime:
                lead.whatsapp_message_timestamp = message_datetime
        except Exception:
            pass

        # ignore_mandatory ensures insertion even if non-required fields are blank.
        lead.flags.ignore_mandatory = True
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        frappe.logger("whatsapp").info(
            f"WhatsApp Lead created: {lead.name} for {phone}"
        )

        # ✅ FIX: Pass first_message directly so email shows the actual message,
        #         not N/A (lead.first_message wasn't committed to DB yet at this point)
        _notify_telecaller_new_lead(lead, first_message=first_message)

        if first_message:
            _save_incoming_comment(lead_name=lead.name, body=first_message, sender_name=name)

        # Auto-confirmation message to the customer
        _send_lead_confirmation(phone=phone, customer_name=name, lead_id=lead.name)

    except Exception:
        frappe.db.rollback()
        frappe.log_error(
            title=f"WhatsApp Lead Creation Failed [{phone}]",
            message=frappe.get_traceback(),
        )


# ===========================================================================
# Lead confirmation message
# ===========================================================================


def _send_lead_confirmation(phone: str, customer_name: str = None, lead_id: str = None):
    """
    Send an automatic WhatsApp confirmation message to the customer
    immediately after their CRM Lead is created.
    """
    display_name = customer_name if customer_name and customer_name != phone else "there"
    ref_line = f" Your reference number is *{lead_id}*." if lead_id else ""

    confirmation_msg = (
        f"Hi {display_name}, thank you for contacting us! "
        f"We have received your enquiry.{ref_line} "
        f"Our team will get back to you shortly."
    )

    try:
        url = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": confirmation_msg,
            },
        }
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        frappe.logger("whatsapp").info(f"Lead confirmation sent to {phone}")
        print(f"[WhatsApp] Confirmation sent to {phone}: {confirmation_msg}")

    except Exception as e:
        # Don't block lead creation if confirmation fails — just log it
        frappe.log_error(
            title=f"WhatsApp Confirmation Send Failed [{phone}]",
            message=str(e),
        )


# ===========================================================================
# Shared comment helper
# ===========================================================================


def _save_incoming_comment(
    lead_name: str,
    body: str,
    sender_name: str = None,
    message_id: str = None,
):
    """
    Save every incoming WhatsApp message as a comment on the CRM Lead.
    Called for BOTH new leads (first message) and existing leads (all repeat messages).

    Deduplication uses WhatsApp's unique message_id when available,
    falling back to content match — prevents duplicate comments on webhook retries
    while correctly allowing the same text sent twice by the user.

    Args:
        lead_name:   The CRM Lead document name to attach the comment to.
        body:        The WhatsApp message text.
        sender_name: Sender's display name (shown in comment for clarity).
        message_id:  WhatsApp's unique message ID (wamid...).
    """
    try:
        display_name = sender_name if sender_name else "Unknown"
        content = f"<strong>Incoming WhatsApp from {display_name}:</strong><br>{body}"

        # Deduplicate by WhatsApp message ID if available (reliable unique key).
        # Falls back to content match only when message_id is absent.
        if message_id:
            existing = frappe.db.exists("Comment", {
                "reference_doctype": "CRM Lead",
                "reference_name": lead_name,
                "comment_email": message_id,   # reuse comment_email field to store wamid
            })
        else:
            existing = frappe.db.exists("Comment", {
                "reference_doctype": "CRM Lead",
                "reference_name": lead_name,
                "content": content,
            })

        if existing:
            frappe.logger("whatsapp").info(f"Duplicate comment skipped for Lead {lead_name}")
            return

        comment = frappe.new_doc("Comment")
        comment.comment_type = "Comment"
        comment.reference_doctype = "CRM Lead"
        comment.reference_name = lead_name
        comment.content = content
        # Store WhatsApp message ID for deduplication on future retries
        if message_id:
            comment.comment_email = message_id
        comment.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.logger("whatsapp").info(f"Comment saved on Lead {lead_name}: {body[:60]}")

    except Exception:
        frappe.log_error(
            title="WhatsApp Comment Insert Error",
            message=frappe.get_traceback(),
        )


# ===========================================================================
# Utility helpers
# ===========================================================================


def _extract_sender_name(contacts: list, wa_id: str) -> str | None:
    for contact in contacts:
        if contact.get("wa_id") == wa_id:
            return contact.get("profile", {}).get("name") or wa_id
    return wa_id


def _parse_whatsapp_timestamp(raw_ts) -> str | None:
    """
    Convert a Unix epoch timestamp (string or int) from the WhatsApp payload
    to a Frappe-compatible datetime string ("YYYY-MM-DD HH:MM:SS").

    Returns None if the value is missing or invalid.
    """
    if not raw_ts:
        return None
    try:
        import datetime

        epoch = int(raw_ts)
        dt = datetime.datetime.utcfromtimestamp(epoch)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        frappe.logger("whatsapp").warning(
            f"WhatsApp: could not parse timestamp '{raw_ts}'"
        )
        return None


def _get_default_open_status() -> str:
    """
    Return the name of the first Open-type CRM Lead Status.
    Prefers "New" if it exists, otherwise falls back to the first Open status.
    """
    if frappe.db.exists("CRM Lead Status", "New"):
        return "New"

    open_statuses = frappe.get_all(
        "CRM Lead Status",
        filters={"type": "Open"},
        pluck="name",
        limit=1,
    )
    return open_statuses[0] if open_statuses else "New"


def _ensure_whatsapp_source():
    """
    Create the "WhatsApp" CRM Lead Source record if it doesn't exist yet.
    Safe to call on every webhook — frappe.db.exists is fast (cached).
    """
    if frappe.db.exists("CRM Lead Source", WHATSAPP_SOURCE):
        return

    doc = frappe.new_doc("CRM Lead Source")
    doc.source_name = WHATSAPP_SOURCE
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.logger("whatsapp").info("Created 'WhatsApp' CRM Lead Source")


# ===========================================================================
# Telecaller helpers
# ===========================================================================


def _get_telecaller_emails() -> list:
    """
    Dynamically fetch email addresses of all active Frappe users
    who have the 'Telecaller' role assigned.

    Setup in Frappe:
      1. Go to Setup → Role → New → create role named "Telecaller"
      2. Open each telecaller's User record → add "Telecaller" under Roles table
      3. No email address needed in this file — it's read from Frappe users.

    Returns a list of email strings, excluding system accounts.
    """
    rows = frappe.get_all(
        "Has Role",
        filters={
            "role": "TP Agent",
            "parenttype": "User",
        },
        fields=["parent"],   # parent = the User's email / name
    )

    emails = [
        row["parent"]
        for row in rows
        if row.get("parent") not in ("Administrator", "Guest", "", None)
    ]

    if not emails:
        frappe.logger("whatsapp").warning(
            "No active users found with the 'Telecaller' role. "
            "Assign the role in Setup → User to receive lead notifications."
        )

    return emails


# ===========================================================================
# Telecaller email notification
# ===========================================================================


def _notify_telecaller_new_lead(lead, first_message: str = ""):
   
    try:
        lead_id       = lead.name
        customer_name = lead.first_name or lead.mobile_no
        mobile_no     = lead.mobile_no or "N/A"
        # Use the passed-in value directly — never read from lead doc here
        msg           = first_message.strip() if first_message and first_message.strip() else "N/A"

        recipients = _get_telecaller_emails()
        if not recipients:
            return   # warning already logged inside _get_telecaller_emails

        subject = f"📲 New WhatsApp Lead: {customer_name} [{lead_id}]"

        frappe.sendmail(
            recipients=recipients,
            subject=subject,
            template="WhatsApp Lead Notification",  # Email Template must exist in Frappe
            args={
                "lead_id": lead_id,
                "customer_name": customer_name,
                "mobile_no": mobile_no,
                "first_message": msg,
            },
            now=True,   # Send immediately, not via the email queue
        )

        frappe.logger("whatsapp").info(
            f"Telecaller(s) notified for new Lead {lead_id} → {recipients}"
        )

    except Exception:
        # Never block lead creation if email fails
        frappe.log_error(
            title=f"Telecaller Email Notification Failed [{lead.name}]",
            message=frappe.get_traceback(),
        )