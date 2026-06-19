# Receives the GitHub webhook POST.
#
# Steps: verify the signature first (reject early if it's bad), ignore
# anything that's not a pull_request event we care about, then build a job
# and push it to SNS for the Lambda worker to pick up later.
#
# Note: we read request.body() as raw bytes before parsing JSON, because the
# signature has to be computed over the exact bytes GitHub sent - if you
# parse to JSON and re-serialize it first, the signature check breaks.
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from ..aws_clients import sns_client
from ..config import get_settings
from ..github_verify import verify_signature
from ..models import WebhookAccepted

logger = logging.getLogger("pr_reviewer.webhook")
router = APIRouter(tags=["webhook"])


@router.post("/webhook/github", response_model=WebhookAccepted)
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
):
    settings = get_settings()
    raw_body = await request.body()

    # --- 1 & 2: signature validation (mandatory) ---
    if not verify_signature(raw_body, settings.github_webhook_secret, x_hub_signature_256):
        logger.warning("Rejected webhook delivery=%s: bad signature", x_github_delivery)
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # GitHub sends a `ping` event when the webhook is first configured.
    if x_github_event == "ping":
        return WebhookAccepted(status="ok", detail="pong", delivery_id=x_github_delivery)

    if x_github_event != "pull_request":
        return WebhookAccepted(
            status="ignored",
            detail=f"event '{x_github_event}' not handled",
            delivery_id=x_github_delivery,
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Malformed JSON body")

    action = payload.get("action")
    if action not in settings.handled_actions_set:
        return WebhookAccepted(
            status="ignored",
            detail=f"pull_request action '{action}' not handled",
            delivery_id=x_github_delivery,
        )

    # --- 3: build the job envelope ---
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    envelope = {
        "delivery_id": x_github_delivery,
        "event": x_github_event,
        "action": action,
        "installation_id": str(installation.get("id")) if installation.get("id") else None,
        "repo_full_name": repo.get("full_name"),
        "repo_id": str(repo.get("id")) if repo.get("id") else None,
        "pr_number": pr.get("number"),
        "pr_title": pr.get("title"),
        "author": (pr.get("user") or {}).get("login"),
        "head_sha": (pr.get("head") or {}).get("sha"),
        "base_sha": (pr.get("base") or {}).get("sha"),
    }

    missing = [k for k in ("repo_full_name", "pr_number", "installation_id", "head_sha") if not envelope.get(k)]
    if missing:
        logger.error("Webhook delivery=%s missing fields: %s", x_github_delivery, missing)
        raise HTTPException(status_code=422, detail=f"Payload missing fields: {missing}")

    # --- 4: publish to SNS ---
    try:
        sns_client().publish(
            TopicArn=settings.sns_topic_arn,
            Message=json.dumps(envelope),
            MessageAttributes={
                "event": {"DataType": "String", "StringValue": x_github_event},
                "action": {"DataType": "String", "StringValue": action},
            },
        )
    except Exception:  # noqa: BLE001 - we want to log any boto error and surface 502
        logger.exception("Failed to publish to SNS for delivery=%s", x_github_delivery)
        raise HTTPException(status_code=502, detail="Failed to enqueue review job")

    logger.info(
        "Enqueued review job delivery=%s repo=%s pr=%s sha=%s",
        x_github_delivery, envelope["repo_full_name"], envelope["pr_number"], envelope["head_sha"],
    )
    return WebhookAccepted(status="accepted", detail="review job enqueued", delivery_id=x_github_delivery)
