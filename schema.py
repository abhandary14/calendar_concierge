from pydantic import BaseModel, Field


class SecurityAlert(BaseModel):
    from_: str = Field(alias="from")
    subject: str
    date: str
    summary: str  # one-sentence plain-English description of what happened


class ActionableEmail(BaseModel):
    from_: str = Field(alias="from")
    subject: str
    snippet: str
    draft_saved: bool
    draft_text: str | None = None


class NewsletterOrMarketingEmail(BaseModel):
    from_: str = Field(alias="from")
    subject: str
    in_exception_list: bool
    unsubscribe_link: str | None = None


class NotificationEmail(BaseModel):
    from_: str = Field(alias="from")
    subject: str


class ApplicationUpdate(BaseModel):
    from_: str = Field(alias="from")
    subject: str
    date: str


class JobRecommendation(BaseModel):
    from_: str = Field(alias="from")
    subject: str
    date: str


class CalendarEvent(BaseModel):
    summary: str
    start: str
    end: str
    flags: list[str] = []


class PipelineError(BaseModel):
    stage: str
    message: str


class BriefingSchema(BaseModel):
    status: str  # "success" | "partial" | "failed"
    errors: list[PipelineError] = []
    security_alerts: list[SecurityAlert] = []
    actionable_emails: list[ActionableEmail] = []
    newsletters: list[NewsletterOrMarketingEmail] = []
    marketing: list[NewsletterOrMarketingEmail] = []
    notifications: list[NotificationEmail] = []
    application_updates: list[ApplicationUpdate] = []
    job_recommendations: list[JobRecommendation] = []
    calendar_events: list[CalendarEvent] = []
    action_items: list[str] = []
    generated_at: str | None = None

    model_config = {"populate_by_name": True}
