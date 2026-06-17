import json
import logging
from datetime import datetime, timedelta

import yaml
from langchain_core.prompts import ChatPromptTemplate
from tenacity import retry, stop_after_attempt, wait_exponential

from auth import AuthError
from filters import split_emails
from models import summary_model
from prompts import SUMMARY_PROMPT
from schema import (
    ApplicationUpdate,
    BriefingSchema,
    CalendarEvent,
    JobRecommendation,
    PipelineError,
)
from state_store import get_last_success, get_processed_ids, mark_processed
from tools.calendar_tools import get_upcoming_events
from tools.gmail_tools import fetch_recent_emails, save_draft

logger = logging.getLogger("concierge")

_DEFAULT_FETCH_QUERY = "newer_than:1d -in:sent -in:drafts -in:spam -in:trash"
_BODY_CAP = 500   # chars of body sent to the LLM per email
_BATCH_SIZE = 6   # emails per LLM call, to stay under the 12K tokens-per-minute limit


def _build_fetch_query() -> str:
    last_success = get_last_success()
    if last_success and (datetime.now() - last_success) > timedelta(days=1):
        return (
            f"after:{last_success.strftime('%Y/%m/%d')}"
            " -in:sent -in:drafts -in:spam -in:trash"
        )
    return _DEFAULT_FETCH_QUERY


def _load_exceptions() -> list[str]:
    with open("config/exceptions.yaml") as f:
        return yaml.safe_load(f).get("keep", [])


def _load_profile() -> dict:
    with open("config/profile.yaml") as f:
        return yaml.safe_load(f)


def _slim_email(e: dict) -> dict:
    """Trim an email to the fields the LLM needs, capping the body to control tokens."""
    return {
        "id": e.get("id"),
        "from": e.get("from"),
        "subject": e.get("subject"),
        "date": e.get("date"),
        "body": (e.get("body") or e.get("snippet") or "")[:_BODY_CAP],
        "list_unsubscribe": e.get("list_unsubscribe"),
    }


# ---------------------------------------------------------------------------
# Stage 1 — fetch (no LLM)
# ---------------------------------------------------------------------------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def fetch_data() -> dict:
    """Call Gmail and Calendar APIs directly — no LLM needed for deterministic fetching."""
    query = _build_fetch_query()
    emails = json.loads(fetch_recent_emails.invoke({"query": query, "max_results": 20}))
    events = json.loads(get_upcoming_events.invoke({"days": 3}))
    return {"emails": emails, "events": events}


# ---------------------------------------------------------------------------
# Stage 2 — classify + draft (batched LLM calls)
# ---------------------------------------------------------------------------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=65))
def _classify_batch(emails_batch: list[dict]) -> BriefingSchema:
    """One 70B structured call over a small batch of emails."""
    exceptions = _load_exceptions()
    profile = _load_profile()
    slim = [_slim_email(e) for e in emails_batch]
    context = (
        f"Exception list (keep these newsletter/marketing senders): {exceptions}\n\n"
        f"User profile:\n{json.dumps(profile)}\n\n"
        f"Emails to classify:\n{json.dumps(slim)}"
    )
    structured_model = summary_model.with_structured_output(BriefingSchema)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SUMMARY_PROMPT),
        ("human", "{input}"),
    ])
    return (prompt | structured_model).invoke({"input": context})


def classify_and_draft(remaining_emails: list[dict]) -> BriefingSchema:
    """Classify all emails in batches and merge the per-batch briefings into one."""
    merged = BriefingSchema()
    total_batches = (len(remaining_emails) + _BATCH_SIZE - 1) // _BATCH_SIZE

    for i in range(0, len(remaining_emails), _BATCH_SIZE):
        batch = remaining_emails[i:i + _BATCH_SIZE]
        n = i // _BATCH_SIZE + 1
        logger.info("Classifying batch %d/%d (%d emails)", n, total_batches, len(batch))
        b = _classify_batch(batch)
        merged.security_alerts += b.security_alerts
        merged.actionable_emails += b.actionable_emails
        merged.newsletters += b.newsletters
        merged.marketing += b.marketing
        merged.notifications += b.notifications
        merged.action_items += b.action_items

    return merged


# ---------------------------------------------------------------------------
# Stage 3 — save drafts (no LLM)
# ---------------------------------------------------------------------------
def _draft_has_body(draft_text: str | None, signature: str) -> bool:
    """True only if the draft has real content beyond the signature/greeting."""
    if not draft_text:
        return False
    body = draft_text
    for sig_line in signature.splitlines():
        body = body.replace(sig_line.strip(), "")
    # Strip common throwaway greetings/closings so signature-only drafts read as empty
    for filler in ("hi", "hello", "dear", "best regards", "regards", "thanks", "thank you"):
        body = body.lower().replace(filler, "")
    return len(body.strip()) >= 20


def save_drafts(briefing: BriefingSchema, raw_emails: list[dict]) -> None:
    """Save a Gmail draft for each actionable email, threaded via the original email id."""
    raw_by_id = {e["id"]: e for e in raw_emails}
    signature = _load_profile().get("signature", "")

    for ae in briefing.actionable_emails:
        if not _draft_has_body(ae.draft_text, signature):
            logger.info("Skipping empty/signature-only draft for: %s", ae.subject)
            ae.draft_saved = False
            continue

        raw = raw_by_id.get(ae.email_id)
        if raw is None:
            # Fallback join by subject if the model didn't echo the id cleanly
            raw = next((e for e in raw_emails if e["subject"] == ae.subject), None)

        to_addr = raw["from"] if raw else ae.from_
        thread_id = raw.get("thread_id") if raw else None
        subj = ae.subject if ae.subject.lower().startswith("re:") else f"Re: {ae.subject}"

        try:
            save_draft.invoke({
                "to": to_addr,
                "subject": subj,
                "body": ae.draft_text,
                "thread_id": thread_id,
            })
            ae.draft_saved = True
            logger.info("Draft saved for: %s", ae.subject)
        except Exception:
            logger.exception("Failed to save draft for: %s", ae.subject)
            ae.draft_saved = False


# ---------------------------------------------------------------------------
# Calendar flagging (deterministic, no LLM)
# ---------------------------------------------------------------------------
def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def flag_events(events: list[dict]) -> list[CalendarEvent]:
    """Compute back_to_back (<15 min gap) and no_agenda (empty description) flags."""
    ordered = sorted(events, key=lambda e: e.get("start", ""))
    flagged: list[CalendarEvent] = []

    for idx, e in enumerate(ordered):
        flags: list[str] = []
        if not (e.get("description") or "").strip():
            flags.append("no_agenda")

        if idx > 0:
            prev = ordered[idx - 1]
            # Only compare timed events (all-day events use a bare date)
            if "T" in prev.get("end", "") and "T" in e.get("start", ""):
                try:
                    gap = _parse_dt(e["start"]) - _parse_dt(prev["end"])
                    if timedelta(0) <= gap < timedelta(minutes=15):
                        flags.append("back_to_back")
                except ValueError:
                    pass

        flagged.append(CalendarEvent(
            summary=e.get("summary", "(no title)"),
            start=e["start"],
            end=e["end"],
            flags=flags,
        ))

    return flagged


def calendar_action_items(flagged: list[CalendarEvent]) -> list[str]:
    """Natural-language follow-ups derived from calendar flags."""
    items: list[str] = []
    for e in flagged:
        if "back_to_back" in e.flags:
            items.append(
                f"'{e.summary}' starts within 15 minutes of the previous event — "
                "consider adding a buffer."
            )
        if "no_agenda" in e.flags:
            items.append(f"No agenda set for '{e.summary}' — add one before the meeting.")
    return items


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_pipeline() -> BriefingSchema:
    """Full pipeline: fetch → pre-filter → classify/draft → save drafts → persist state."""
    errors: list[PipelineError] = []

    # Stage 1 — fetch directly (no LLM)
    logger.info("Stage 1: fetching emails and calendar events")
    try:
        raw_data = fetch_data()
        logger.info(
            "Fetched %d emails, %d events",
            len(raw_data.get("emails", [])),
            len(raw_data.get("events", [])),
        )
    except AuthError as e:
        logger.error("Auth failed: %s", e)
        return BriefingSchema(
            status="failed",
            errors=[PipelineError(stage="auth", message=str(e))],
        )
    except Exception as e:
        logger.exception("Fetch failed after retries")
        return BriefingSchema(
            status="failed",
            errors=[PipelineError(stage="fetch", message=str(e))],
        )

    # Pre-filter (deterministic, no LLM)
    try:
        processed_ids = get_processed_ids()
        filtered = split_emails(raw_data.get("emails", []), processed_ids)
        logger.info(
            "Pre-filter: %d remaining, %d app updates, %d job recs",
            len(filtered["remaining"]),
            len(filtered["application_updates"]),
            len(filtered["job_recommendations"]),
        )
    except Exception as e:
        logger.exception("Pre-filter failed")
        filtered = {
            "remaining": raw_data.get("emails", []),
            "application_updates": [],
            "job_recommendations": [],
        }
        errors.append(PipelineError(stage="prefilter", message=str(e)))

    # Stage 2 — classify, draft, summarise (batched LLM calls)
    logger.info("Stage 2: classifying emails and drafting replies")
    try:
        briefing = classify_and_draft(filtered["remaining"])
        logger.info("Classification complete")
    except Exception as e:
        logger.exception("Classify/draft failed after retries")
        return BriefingSchema(
            status="partial",
            errors=errors + [PipelineError(stage="classify_draft", message=str(e))],
            application_updates=[
                ApplicationUpdate(**u) for u in filtered["application_updates"]
            ],
            job_recommendations=[
                JobRecommendation(**r) for r in filtered["job_recommendations"]
            ],
            calendar_events=flag_events(raw_data.get("events", [])),
        )

    # Stage 3 — save drafts deterministically (no LLM)
    logger.info("Stage 3: saving drafts")
    try:
        save_drafts(briefing, raw_data.get("emails", []))
    except Exception as e:
        logger.exception("Draft saving failed")
        errors.append(PipelineError(stage="save_drafts", message=str(e)))

    # Calendar flagging + merge pre-filtered lists (deterministic)
    briefing.calendar_events = flag_events(raw_data.get("events", []))
    briefing.action_items += calendar_action_items(briefing.calendar_events)
    briefing.application_updates = [
        ApplicationUpdate(**u) for u in filtered["application_updates"]
    ]
    briefing.job_recommendations = [
        JobRecommendation(**r) for r in filtered["job_recommendations"]
    ]

    briefing.status = "success" if not errors else "partial"
    briefing.errors = errors
    logger.info("Pipeline complete — status: %s", briefing.status)

    # Mark all remaining (non-pre-filtered) emails as processed
    mark_processed([e["id"] for e in filtered["remaining"]])

    return briefing
