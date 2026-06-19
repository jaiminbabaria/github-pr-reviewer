# Lambda entrypoint - triggered by SQS, runs one PR review per message.
#
# Order of operations: read the envelope off the queue, figure out a stable
# review id, check if we've already handled this exact commit, then do the
# actual work (fetch diff -> OpenAI -> post comments -> update DynamoDB).
#
# On any failure I re-raise instead of swallowing the error - that way SQS
# retries the message automatically, and if it keeps failing it eventually
# falls into the DLQ instead of disappearing silently.
#
# Used ReportBatchItemFailures so a single bad message in a batch doesn't
# cause the whole batch to be retried (learned this the hard way - without
# it, one broken PR can hold up everything else in the same poll).
import json
import logging
import time
import uuid

import boto3

import config
import dynamo
import github_client
import openai_reviewer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pr_reviewer.handler")
logger.setLevel(logging.INFO)

_cloudwatch = boto3.client("cloudwatch", region_name=config.AWS_REGION)
_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # URL namespace


def _emit_metric(name: str, value: float, unit: str = "None") -> None:
    """Best-effort custom CloudWatch metric. Never let metrics break the job."""
    try:
        _cloudwatch.put_metric_data(
            Namespace=config.METRIC_NAMESPACE,
            MetricData=[{"MetricName": name, "Value": value, "Unit": unit}],
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to emit metric %s", name, exc_info=True)


def _parse_envelope(record: dict) -> dict:
    # If raw message delivery is on, the SQS body IS our JSON envelope.
    # If it's off, SQS wraps it in an SNS envelope and our payload is
    # nested inside body["Message"] as a string - handle both.
    body = json.loads(record["body"])
    if isinstance(body, dict) and "Message" in body and "TopicArn" in body:
        # SNS envelope (raw delivery disabled)
        return json.loads(body["Message"])
    return body


def _compute_review_id(env: dict) -> str:
    key = f"{env['repo_full_name']}#{env['pr_number']}#{env['head_sha']}"
    return str(uuid.uuid5(_NAMESPACE, key))


def _process_one(env: dict) -> None:
    start = time.time()
    repo_full_name = env["repo_full_name"]
    pr_number = int(env["pr_number"])
    installation_id = env["installation_id"]

    review_id = _compute_review_id(env)
    logger.info("Processing review_id=%s repo=%s pr=%s", review_id, repo_full_name, pr_number)

    outcome = dynamo.claim_review(review_id, env)
    if outcome == "skip":
        _emit_metric("ReviewSkippedDuplicate", 1, "Count")
        return

    # Register/track the repository (atomic totalReviews increment).
    if env.get("repo_id"):
        dynamo.register_repo(env["repo_id"], repo_full_name, installation_id)

    try:
        dynamo.set_status(review_id, "PROCESSING")

        token = github_client.installation_token(installation_id)
        diff_text = github_client.fetch_pr_diff(token, repo_full_name, pr_number)
        dynamo.set_diff_size(review_id, len(diff_text))

        commentable = github_client.parse_commentable_lines(diff_text)
        result = openai_reviewer.review_diff(diff_text, commentable)

        comments_posted = github_client.post_review(
            token=token,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            commit_sha=env["head_sha"],
            summary=result["summary"],
            comments=result["comments"],
        )

        elapsed_ms = int((time.time() - start) * 1000)
        dynamo.complete_review(review_id, comments_posted, result["summary"], elapsed_ms)

        _emit_metric("ReviewProcessingTimeMs", elapsed_ms, "Milliseconds")
        _emit_metric("ReviewCompleted", 1, "Count")
        _emit_metric("CommentsPosted", comments_posted, "Count")
        logger.info(
            "Completed review_id=%s comments=%s elapsed_ms=%s",
            review_id, comments_posted, elapsed_ms,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.time() - start) * 1000)
        logger.exception("Review failed review_id=%s", review_id)
        dynamo.fail_review(review_id, str(exc), elapsed_ms)
        _emit_metric("ReviewFailed", 1, "Count")
        raise  # re-raise so SQS retries -> eventually DLQ


def handler(event: dict, context) -> dict:
    # Returning batchItemFailures tells Lambda which specific messages failed
    # so only those get retried, not the whole batch. Needs
    # ReportBatchItemFailures enabled on the event source mapping.
    failures: list[dict] = []
    records = event.get("Records", [])
    logger.info("Received %d SQS record(s)", len(records))

    for record in records:
        message_id = record.get("messageId")
        try:
            env = _parse_envelope(record)
            _process_one(env)
        except Exception:  # noqa: BLE001
            logger.exception("Failed processing messageId=%s", message_id)
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}
