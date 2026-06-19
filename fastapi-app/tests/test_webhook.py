"""Tests for the webhook endpoint.

Run from fastapi-app/:
    pip install pytest
    GITHUB_WEBHOOK_SECRET=testsecret SNS_TOPIC_ARN=arn:aws:sns:us-east-1:000:test \
      pytest -q

The SNS publish is monkeypatched so the test needs no AWS access.
"""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import get_settings

client = TestClient(app)


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


SAMPLE_PR = {
    "action": "opened",
    "pull_request": {
        "number": 7,
        "title": "Add divide()",
        "user": {"login": "octocat"},
        "head": {"sha": "abc123"},
        "base": {"sha": "def456"},
    },
    "repository": {"full_name": "octocat/hello-world", "id": 99},
    "installation": {"id": 12345},
}


@pytest.fixture(autouse=True)
def _patch_sns(monkeypatch):
    published = {}

    class FakeSNS:
        def publish(self, **kwargs):
            published.update(kwargs)
            return {"MessageId": "fake"}

    monkeypatch.setattr("app.routers.webhook.sns_client", lambda: FakeSNS())
    return published


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_webhook_rejects_bad_signature():
    body = json.dumps(SAMPLE_PR).encode()
    r = client.post(
        "/webhook/github",
        data=body,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeef",
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "d1",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401


def test_webhook_accepts_valid_signature():
    secret = get_settings().github_webhook_secret
    body = json.dumps(SAMPLE_PR).encode()
    r = client.post(
        "/webhook/github",
        data=body,
        headers={
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "d2",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"


def test_webhook_ignores_unhandled_action():
    secret = get_settings().github_webhook_secret
    payload = dict(SAMPLE_PR, action="labeled")
    body = json.dumps(payload).encode()
    r = client.post(
        "/webhook/github",
        data=body,
        headers={
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "d3",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


def test_ping_event():
    secret = get_settings().github_webhook_secret
    body = json.dumps({"zen": "hi"}).encode()
    r = client.post(
        "/webhook/github",
        data=body,
        headers={
            "X-Hub-Signature-256": _sign(body, secret),
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "d4",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200
    assert r.json()["detail"] == "pong"
