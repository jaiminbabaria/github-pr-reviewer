"""FastAPI entrypoint for the GitHub PR Automated Code Reviewer.

Run locally:
    uvicorn app.main:app --reload --port 8000
In production it runs under uvicorn as a systemd service behind nginx
(see deploy/). Logging is sent to stdout so journald/CloudWatch agent can
collect it.
"""
import logging

from fastapi import FastAPI

from .config import get_settings
from .routers import health, repos, reviews, stats, webhook


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(
    title="GitHub PR Automated Code Reviewer",
    description="Receives GitHub PR webhooks, fans them out via SNS/SQS, and "
    "serves review history from DynamoDB.",
    version="1.0.0",
)

app.include_router(webhook.router)
app.include_router(reviews.router)
app.include_router(repos.router)
app.include_router(stats.router)
app.include_router(health.router)


@app.get("/", tags=["root"])
def root():
    return {"service": "github-pr-reviewer", "docs": "/docs"}
