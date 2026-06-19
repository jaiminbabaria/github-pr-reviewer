"""Read endpoints for reviews.

DynamoDB scans are used here for simplicity (portfolio scale). At production
scale you would add a GSI on createdAt and Query instead of Scan — this is
called out in the README's "production hardening" section.
"""
import logging
from decimal import Decimal

from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, HTTPException

from ..aws_clients import reviews_table
from ..models import Review, ReviewList

logger = logging.getLogger("pr_reviewer.reviews")
router = APIRouter(tags=["reviews"])


def _coerce(item: dict) -> dict:
    """Convert DynamoDB Decimal values into plain int/float for JSON output."""
    out = {}
    for k, v in item.items():
        if isinstance(v, Decimal):
            out[k] = int(v) if v % 1 == 0 else float(v)
        else:
            out[k] = v
    return out


@router.get("/reviews", response_model=ReviewList)
def list_reviews(limit: int = 100):
    items: list[dict] = []
    kwargs: dict = {}
    table = reviews_table()
    # Paginate through the scan until we hit the requested limit.
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp or len(items) >= limit:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    items = sorted(items, key=lambda i: i.get("createdAt", ""), reverse=True)[:limit]
    reviews = [Review(**_coerce(i)) for i in items]
    return ReviewList(count=len(reviews), reviews=reviews)


@router.get("/reviews/{review_id}", response_model=Review)
def get_review(review_id: str):
    resp = reviews_table().get_item(Key={"reviewId": review_id})
    item = resp.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Review not found")
    return Review(**_coerce(item))
