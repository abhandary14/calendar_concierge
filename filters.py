import yaml


def _load_job_filters() -> dict:
    with open("config/job_filters.yaml") as f:
        return yaml.safe_load(f)


def split_emails(emails: list[dict], processed_ids: set[str]) -> dict:
    """
    Split raw emails into three buckets, dropping already-processed IDs.

    Returns:
        {
            "remaining":            emails that go to the summary agent,
            "application_updates":  ATS acks/rejections (from/subject/date only),
            "job_recommendations":  LinkedIn job alerts (from/subject/date only),
        }
    """
    filters = _load_job_filters()

    ats_senders = [s.lower() for s in filters.get("ats_senders", [])]
    ats_keywords = [k.lower() for k in filters.get("ats_keywords", [])]
    linkedin_senders = [s.lower() for s in filters.get("linkedin_job_senders", [])]
    linkedin_keywords = [k.lower() for k in filters.get("linkedin_job_keywords", [])]

    remaining, app_updates, job_recs = [], [], []

    for email in emails:
        if email["id"] in processed_ids:
            continue

        sender = (email.get("from") or "").lower()
        text = f"{email.get('subject', '')} {email.get('snippet', '')}".lower()
        slim = {"from": email["from"], "subject": email["subject"], "date": email["date"]}

        if any(s in sender for s in linkedin_senders) or \
           any(k in text for k in linkedin_keywords):
            job_recs.append(slim)
            continue

        if any(s in sender for s in ats_senders) or \
           any(k in text for k in ats_keywords):
            app_updates.append(slim)
            continue

        remaining.append(email)

    return {
        "remaining": remaining,
        "application_updates": app_updates,
        "job_recommendations": job_recs,
    }
