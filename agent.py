import json
import logging
import re
from datetime import datetime, timedelta

import yaml
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate
from tenacity import retry, stop_after_attempt, wait_exponential

from auth import AuthError
from filters import split_emails
from models import summary_model, tool_model
from prompts import (
    SUMMARY_AGENT_STEP1_PROMPT,
    SUMMARY_AGENT_STEP2_PROMPT,
    TOOL_AGENT_PROMPT,
)
from schema import BriefingSchema, CalendarEvent, PipelineError
from state_store import get_last_success, get_processed_ids, mark_processed
from tools.calendar_tools import get_upcoming_events
from tools.gmail_tools import fetch_recent_emails, save_draft

logger = logging.getLogger("concierge")

fetch_tools = [fetch_recent_emails, get_upcoming_events]
draft_tools = [save_draft]

_DEFAULT_FETCH_QUERY = "newer_than:1d -in:sent -in:drafts -in:spam -in:trash"


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


def _parse_json_output(text: str) -> dict:
    """
    Extract a JSON object from an LLM output string.
    Tries clean parse first, then strips markdown fences, then regex-extracts.
    Raises ValueError if no valid JSON can be found.
    """
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    stripped = re.sub(r"^```(?:json)?\s*", "", text)
    stripped = re.sub(r"\s*```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from agent output: {text[:300]!r}")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def run_tool_agent() -> dict:
    """8B model: fetch emails and calendar events. Returns raw data dict."""
    query = _build_fetch_query()
    human_msg = f"Fetch all data now. Use query='{query}' and max_results=20."

    agent = create_agent(tool_model, fetch_tools, system_prompt=TOOL_AGENT_PROMPT)
    result = agent.invoke({"messages": [("human", human_msg)]})
    output = result["messages"][-1].content
    return _parse_json_output(output)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def run_summary_agent_step1(remaining_emails: list[dict], events: list[dict]) -> str:
    """70B model step 1: classify emails, draft replies, call save_draft, flag calendar events."""
    exceptions = _load_exceptions()
    profile = _load_profile()
    context = (
        f"Exception list (keep these newsletter/marketing senders): {exceptions}\n\n"
        f"User profile:\n{json.dumps(profile, indent=2)}\n\n"
        f"Emails to classify:\n{json.dumps(remaining_emails, indent=2)}\n\n"
        f"Calendar events:\n{json.dumps(events, indent=2)}"
    )
    agent = create_agent(
        summary_model, draft_tools, system_prompt=SUMMARY_AGENT_STEP1_PROMPT
    )
    result = agent.invoke({"messages": [("human", context)]})
    return result["messages"][-1].content


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def run_summary_agent_step2(
    step1_output: str,
    raw_data: dict,
    filtered: dict,
) -> BriefingSchema:
    """70B model step 2: coerce step-1 reasoning into a validated BriefingSchema."""
    structured_model = summary_model.with_structured_output(BriefingSchema)
    context = (
        f"Reasoning and actions from step 1:\n{step1_output}\n\n"
        f"Original raw emails (for reference):\n"
        f"{json.dumps(raw_data.get('emails', []), indent=2)}\n\n"
        f"Application updates (pre-filtered, include as-is):\n"
        f"{json.dumps(filtered['application_updates'], indent=2)}\n\n"
        f"Job recommendations (pre-filtered, include as-is):\n"
        f"{json.dumps(filtered['job_recommendations'], indent=2)}\n\n"
        f"Calendar events:\n{json.dumps(raw_data.get('events', []), indent=2)}"
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", SUMMARY_AGENT_STEP2_PROMPT),
        ("human", "{input}"),
    ])
    return (prompt | structured_model).invoke({"input": context})


def run_pipeline() -> BriefingSchema:
    """Full pipeline: fetch → pre-filter → classify/draft → structure → persist state."""
    errors: list[PipelineError] = []

    # Stage 1 — fetch
    logger.info("Stage 1: fetching emails and calendar events")
    try:
        raw_data = run_tool_agent()
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
        logger.exception("Tool agent failed after retries")
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

    # Stage 2 — classify and draft
    logger.info("Stage 2: classifying emails and drafting replies")
    try:
        step1_output = run_summary_agent_step1(
            filtered["remaining"], raw_data.get("events", [])
        )
        logger.info("Step 1 complete")
    except Exception as e:
        logger.exception("Summary agent step 1 failed after retries")
        return BriefingSchema(
            status="partial",
            errors=errors + [PipelineError(stage="classify_draft", message=str(e))],
            application_updates=filtered["application_updates"],
            job_recommendations=filtered["job_recommendations"],
            calendar_events=[
                CalendarEvent(
                    summary=ev.get("summary", "(no title)"),
                    start=ev["start"],
                    end=ev["end"],
                )
                for ev in raw_data.get("events", [])
            ],
        )

    # Stage 3 — structured output
    logger.info("Stage 3: structuring briefing output")
    try:
        briefing = run_summary_agent_step2(step1_output, raw_data, filtered)
        briefing.status = "success" if not errors else "partial"
        briefing.errors = errors
        logger.info("Pipeline complete — status: %s", briefing.status)
    except Exception as e:
        logger.exception("Summary agent step 2 failed after retries")
        return BriefingSchema(
            status="partial",
            errors=errors + [PipelineError(stage="structure_output", message=str(e))],
            application_updates=filtered["application_updates"],
            job_recommendations=filtered["job_recommendations"],
        )

    # Mark all remaining (non-pre-filtered) emails as processed
    mark_processed([e["id"] for e in filtered["remaining"]])

    return briefing
