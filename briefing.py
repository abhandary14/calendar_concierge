import logging
import os
from datetime import datetime

from schema import BriefingSchema

logger = logging.getLogger("concierge")

RUNS_DIR = "runs"


def save_briefing(briefing: BriefingSchema) -> str:
    """Stamp generated_at, persist to runs/YYYY-MM-DD_HH-MM.json, return the path."""
    os.makedirs(RUNS_DIR, exist_ok=True)

    now = datetime.now()
    briefing.generated_at = now.isoformat()

    filename = now.strftime("%Y-%m-%d_%H-%M") + ".json"
    path = os.path.join(RUNS_DIR, filename)

    with open(path, "w", encoding="utf-8") as f:
        f.write(briefing.model_dump_json(by_alias=True, indent=2))

    logger.info("Briefing saved to %s", path)
    return path
