#!/usr/bin/env bash
# Creates the reviews + repositories tables. Using on-demand billing so I
# don't have to think about capacity units. Safe to re-run, skips tables
# that already exist.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
REVIEWS_TABLE="${DYNAMODB_REVIEWS_TABLE:-reviews}"
REPOS_TABLE="${DYNAMODB_REPOSITORIES_TABLE:-repositories}"

create_table () {
  local name="$1"; shift
  if aws dynamodb describe-table --table-name "$name" --region "$REGION" >/dev/null 2>&1; then
    echo "Table '$name' already exists; skipping."
  else
    echo "Creating table '$name'..."
    aws dynamodb create-table --region "$REGION" "$@"
    aws dynamodb wait table-exists --table-name "$name" --region "$REGION"
    echo "Table '$name' is ACTIVE."
  fi
}

# reviews: PK = reviewId (String UUID)
create_table "$REVIEWS_TABLE" \
  --table-name "$REVIEWS_TABLE" \
  --attribute-definitions AttributeName=reviewId,AttributeType=S \
  --key-schema AttributeName=reviewId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

# repositories: PK = repoId (String)
create_table "$REPOS_TABLE" \
  --table-name "$REPOS_TABLE" \
  --attribute-definitions AttributeName=repoId,AttributeType=S \
  --key-schema AttributeName=repoId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

echo "Done. Tables: $REVIEWS_TABLE, $REPOS_TABLE"
