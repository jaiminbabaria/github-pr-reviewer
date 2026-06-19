"""Lambda configuration, read from environment variables set on the function.

We deliberately read env vars at import time and fail loudly if a required one
is missing, so a misconfigured deployment surfaces immediately in CloudWatch
rather than silently mis-behaving per-invocation.
"""
import os


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# GitHub App credentials
GITHUB_APP_ID = _required("GITHUB_APP_ID")
# Private key is the full PEM. Newlines may be escaped as \n in the env var;
# we normalize them below.
GITHUB_APP_PRIVATE_KEY = _required("GITHUB_APP_PRIVATE_KEY").replace("\\n", "\n")

# OpenAI
OPENAI_API_KEY = _required("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

# DynamoDB
REVIEWS_TABLE = os.environ.get("DYNAMODB_REVIEWS_TABLE", "reviews")
REPOSITORIES_TABLE = os.environ.get("DYNAMODB_REPOSITORIES_TABLE", "repositories")

# CloudWatch custom metric namespace
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "PRReviewer")

# Safety limits
MAX_DIFF_CHARS = int(os.environ.get("MAX_DIFF_CHARS", "60000"))  # chars sent to OpenAI
MAX_COMMENTS = int(os.environ.get("MAX_COMMENTS", "25"))  # cap inline comments per PR
