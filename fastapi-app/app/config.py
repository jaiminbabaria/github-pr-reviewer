"""Application configuration loaded from environment variables.

Uses pydantic-settings so every value is validated at startup. If a required
variable is missing the app fails fast instead of erroring on the first request.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- GitHub ---
    github_webhook_secret: str  # used to validate the X-Hub-Signature-256 header

    # --- AWS ---
    aws_region: str = "us-east-1"
    sns_topic_arn: str  # ARN of the SNS topic the webhook publishes to
    dynamodb_reviews_table: str = "reviews"
    dynamodb_repositories_table: str = "repositories"

    # --- App ---
    log_level: str = "INFO"
    # GitHub PR actions we actually act on. "opened" is the core requirement;
    # "reopened" and "synchronize" (new commits pushed) are included so the
    # reviewer stays useful across the PR lifecycle.
    handled_pr_actions: str = "opened,reopened,synchronize"

    @property
    def handled_actions_set(self) -> set[str]:
        return {a.strip() for a in self.handled_pr_actions.split(",") if a.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
