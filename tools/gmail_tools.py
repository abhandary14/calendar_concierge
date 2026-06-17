import base64
import email as email_lib
import json

from langchain_core.tools import tool

from auth import get_services

gmail, _ = get_services()


def _extract_body(payload: dict) -> str:
    """Return the first text/plain part, base64-decoded. Empty string if none found."""
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result

    return ""


_DEFAULT_QUERY = "newer_than:1d -in:sent -in:drafts -in:spam -in:trash"


@tool
def fetch_recent_emails(query: str = _DEFAULT_QUERY, max_results: int = 20) -> str:
    """Fetch emails received in the last 24 hours from inbox and archive, with full plain-text bodies. Returns JSON string."""
    msgs = gmail.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute().get("messages", [])

    results = []
    for m in msgs:
        raw = gmail.users().messages().get(userId="me", id=m["id"], format="full").execute()
        headers = {h["name"]: h["value"] for h in raw["payload"]["headers"]}
        results.append({
            "id": m["id"],
            "thread_id": raw.get("threadId"),
            "from": headers.get("From"),
            "subject": headers.get("Subject"),
            "date": headers.get("Date"),
            "snippet": raw.get("snippet"),
            "body": _extract_body(raw["payload"])[:800],
            "list_unsubscribe": headers.get("List-Unsubscribe"),
        })
    return json.dumps(results)


@tool
def save_draft(to: str, subject: str, body: str, thread_id: str = None) -> str:
    """Save a draft reply in Gmail. Optionally pass thread_id to thread the reply."""
    msg = email_lib.message.EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    message = {"raw": raw}
    if thread_id:
        message["threadId"] = thread_id

    draft = gmail.users().drafts().create(
        userId="me", body={"message": message}
    ).execute()
    return f"Draft saved (id={draft['id']})"
