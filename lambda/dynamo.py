# DynamoDB helpers for the Lambda worker.
#
# The tricky bit here is making retries safe. SQS can deliver the same
# message more than once, so claim_review() uses a conditional put
# (attribute_not_exists) to make sure only one invocation "owns" a given
# review. If the row already exists and is COMPLETED, we just skip - no
# duplicate comments get posted on the PR.
import logging
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

import config

logger = logging.getLogger("pr_reviewer.dynamo")

_dynamodb = boto3.resource("dynamodb", region_name=config.AWS_REGION)
_reviews = _dynamodb.Table(config.REVIEWS_TABLE)
_repos = _dynamodb.Table(config.REPOSITORIES_TABLE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def claim_review(review_id: str, meta: dict) -> str:
    # returns "claimed" / "skip" / "reclaimed"
    item = {
        "reviewId": review_id,
        "repoName": meta["repo_full_name"],
        "prNumber": int(meta["pr_number"]),
        "prTitle": meta.get("pr_title") or "",
        "author": meta.get("author") or "",
        "status": "PENDING",
        "diffSize": 0,
        "commentsPosted": 0,
        "reviewSummary": "",
        "createdAt": _now(),
        "headSha": meta.get("head_sha") or "",
    }
    try:
        _reviews.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(reviewId)",
        )
        return "claimed"
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Already exists — decide based on current status.
        existing = _reviews.get_item(Key={"reviewId": review_id}).get("Item", {})
        status = existing.get("status")
        if status == "COMPLETED":
            logger.info("review %s already COMPLETED; skipping", review_id)
            return "skip"
        # Re-claim a stale/failed/in-progress attempt and reprocess.
        _reviews.update_item(
            Key={"reviewId": review_id},
            UpdateExpression="SET #s = :s, createdAt = if_not_exists(createdAt, :c)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "PENDING", ":c": _now()},
        )
        logger.info("re-claimed review %s (prior status=%s)", review_id, status)
        return "reclaimed"


def set_status(review_id: str, status: str) -> None:
    _reviews.update_item(
        Key={"reviewId": review_id},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status},
    )


def set_diff_size(review_id: str, diff_size: int) -> None:
    _reviews.update_item(
        Key={"reviewId": review_id},
        UpdateExpression="SET diffSize = :d",
        ExpressionAttributeValues={":d": int(diff_size)},
    )


def complete_review(
    review_id: str,
    comments_posted: int,
    summary: str,
    processing_time_ms: int,
) -> None:
    _reviews.update_item(
        Key={"reviewId": review_id},
        UpdateExpression=(
            "SET #s = :s, commentsPosted = :c, reviewSummary = :sum, "
            "processingTimeMs = :p, completedAt = :t"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "COMPLETED",
            ":c": int(comments_posted),
            ":sum": summary[:4000],  # keep item well under the 400KB item limit
            ":p": int(processing_time_ms),
            ":t": _now(),
        },
    )


def fail_review(review_id: str, error: str, processing_time_ms: int) -> None:
    _reviews.update_item(
        Key={"reviewId": review_id},
        UpdateExpression=(
            "SET #s = :s, reviewSummary = :sum, processingTimeMs = :p, completedAt = :t"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "FAILED",
            ":sum": f"ERROR: {error}"[:4000],
            ":p": int(processing_time_ms),
            ":t": _now(),
        },
    )


def register_repo(repo_id: str, repo_name: str, installation_id: str) -> None:
    """Upsert the repositories row and atomically increment totalReviews."""
    _repos.update_item(
        Key={"repoId": repo_id},
        UpdateExpression=(
            "SET repoName = :n, installationId = :i, "
            "registeredAt = if_not_exists(registeredAt, :r) "
            "ADD totalReviews :one"
        ),
        ExpressionAttributeValues={
            ":n": repo_name,
            ":i": installation_id,
            ":r": _now(),
            ":one": Decimal(1),
        },
    )
