"""Aggregate statistics across all reviews.

Computed by scanning the reviews table and reducing in memory. Fine for
portfolio scale; at large scale you would maintain running counters in the
repositories table or a dedicated metrics store.
"""
import logging
from decimal import Decimal

from fastapi import APIRouter

from ..aws_clients import reviews_table
from ..models import Stats

logger = logging.getLogger("pr_reviewer.stats")
router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=Stats)
def stats():
    items: list[dict] = []
    kwargs: dict = {}
    table = reviews_table()
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    counts = {"COMPLETED": 0, "FAILED": 0, "PENDING": 0, "PROCESSING": 0}
    proc_times: list[float] = []
    total_comments = 0

    for it in items:
        status = it.get("status", "")
        if status in counts:
            counts[status] += 1
        pt = it.get("processingTimeMs")
        if isinstance(pt, Decimal):
            proc_times.append(float(pt))
        cp = it.get("commentsPosted")
        if isinstance(cp, Decimal):
            total_comments += int(cp)

    avg = round(sum(proc_times) / len(proc_times), 2) if proc_times else None

    return Stats(
        totalReviews=len(items),
        completed=counts["COMPLETED"],
        failed=counts["FAILED"],
        pending=counts["PENDING"],
        processing=counts["PROCESSING"],
        averageProcessingTimeMs=avg,
        totalCommentsPosted=total_comments,
    )
