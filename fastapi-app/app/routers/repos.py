"""Reviews scoped to a single repository.

`repo` is URL-encoded as owner__name (double underscore) OR owner/name. We
accept the path with a wildcard so `octocat/hello-world` works directly.
"""
import logging
from decimal import Decimal

from fastapi import APIRouter

from ..aws_clients import reviews_table
from ..models import ReviewList, Review

logger = logging.getLogger("pr_reviewer.repos")
router = APIRouter(tags=["repos"])


def _coerce(item: dict) -> dict:
    out = {}
    for k, v in item.items():
        if isinstance(v, Decimal):
            out[k] = int(v) if v % 1 == 0 else float(v)
        else:
            out[k] = v
    return out


@router.get("/repos/{repo:path}/reviews", response_model=ReviewList)
def reviews_for_repo(repo: str, limit: int = 100):
    # Accept owner__name as an alias for owner/name (avoids slash-in-path issues
    # for clients that prefer not to URL-encode).
    repo_name = repo.replace("__", "/")

    items: list[dict] = []
    kwargs: dict = {
        "FilterExpression": "repoName = :r",
        "ExpressionAttributeValues": {":r": repo_name},
    }
    table = reviews_table()
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    items = sorted(items, key=lambda i: i.get("createdAt", ""), reverse=True)[:limit]
    reviews = [Review(**_coerce(i)) for i in items]
    return ReviewList(count=len(reviews), reviews=reviews)
