import json
from pathlib import Path

import jinja2
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from starlette.requests import Request

RUNS_DIR = Path(__file__).parent.parent / "runs"
TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Calendar Concierge")

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=jinja2.select_autoescape(["html"]),
)


def _render(template_name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(_env.get_template(template_name).render(**ctx))


def _load_runs() -> list[dict]:
    """Return all run manifests sorted newest-first (status + generated_at only)."""
    runs = []
    for path in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            runs.append({
                "slug": path.stem,
                "generated_at": data.get("generated_at", path.stem),
                "status": data.get("status", "unknown"),
                "errors": data.get("errors", []),
            })
        except Exception:
            pass
    return runs


def _load_run(slug: str) -> dict:
    path = RUNS_DIR / f"{slug}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@app.get("/", response_class=HTMLResponse)
def index():
    runs = _load_runs()
    briefing = _load_run(runs[0]["slug"]) if runs else {}
    return _render("index.html", briefing=briefing, runs=runs)


@app.get("/history", response_class=HTMLResponse)
def history():
    runs = _load_runs()
    return _render("history.html", runs=runs)


@app.get("/run/{slug}", response_class=HTMLResponse)
def run_detail(slug: str):
    briefing = _load_run(slug)
    runs = _load_runs()
    return _render("index.html", briefing=briefing, runs=runs)
