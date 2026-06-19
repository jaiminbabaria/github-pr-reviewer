#!/usr/bin/env bash
# Zips up the Lambda code + deps and deploys it, then connects it to the
# SQS queue. Needs LAMBDA_ROLE_ARN, SQS_QUEUE_ARN, and the GitHub/OpenAI
# secrets set as env vars first - see README for where those come from.
# Run from repo root: bash infra/04_lambda_deploy.sh
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
FUNCTION_NAME="${LAMBDA_FUNCTION_NAME:-pr-review-worker}"
RUNTIME="python3.11"
HANDLER="handler.handler"
TIMEOUT="${LAMBDA_TIMEOUT:-120}"     # must be <= SQS VisibilityTimeout (180)
MEMORY="${LAMBDA_MEMORY:-512}"

: "${LAMBDA_ROLE_ARN:?Set LAMBDA_ROLE_ARN}"
: "${SQS_QUEUE_ARN:?Set SQS_QUEUE_ARN}"
: "${GITHUB_APP_ID:?Set GITHUB_APP_ID}"
: "${GITHUB_APP_PRIVATE_KEY:?Set GITHUB_APP_PRIVATE_KEY (PEM, newlines as \\n)}"
: "${OPENAI_API_KEY:?Set OPENAI_API_KEY}"

BUILD_DIR="$(mktemp -d)"
ZIP_PATH="$(pwd)/lambda_deploy.zip"
echo "==> Building package in $BUILD_DIR"

cp lambda/*.py "$BUILD_DIR/"
pip install -r lambda/requirements.txt -t "$BUILD_DIR" --quiet --upgrade

( cd "$BUILD_DIR" && zip -r -q "$ZIP_PATH" . )
echo "    Built $ZIP_PATH"

ENV_VARS="Variables={GITHUB_APP_ID=$GITHUB_APP_ID,OPENAI_API_KEY=$OPENAI_API_KEY,OPENAI_MODEL=${OPENAI_MODEL:-gpt-4o},DYNAMODB_REVIEWS_TABLE=${DYNAMODB_REVIEWS_TABLE:-reviews},DYNAMODB_REPOSITORIES_TABLE=${DYNAMODB_REPOSITORIES_TABLE:-repositories},METRIC_NAMESPACE=${METRIC_NAMESPACE:-PRReviewer}}"

if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "==> Updating existing function code"
  aws lambda update-function-code --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$ZIP_PATH" --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
else
  echo "==> Creating function"
  aws lambda create-function --function-name "$FUNCTION_NAME" \
    --runtime "$RUNTIME" --handler "$HANDLER" --role "$LAMBDA_ROLE_ARN" \
    --timeout "$TIMEOUT" --memory-size "$MEMORY" \
    --zip-file "fileb://$ZIP_PATH" --region "$REGION" >/dev/null
  aws lambda wait function-active --function-name "$FUNCTION_NAME" --region "$REGION"
fi

# The private key is set separately to avoid shell-escaping issues with commas
# in the bulk --environment string above.
echo "==> Setting environment variables"
aws lambda update-function-configuration --function-name "$FUNCTION_NAME" \
  --region "$REGION" --environment "$ENV_VARS" >/dev/null
aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"

# Append the private key via a JSON file to preserve newlines safely.
TMP_ENV="$(mktemp)"
python3 - "$GITHUB_APP_PRIVATE_KEY" > "$TMP_ENV" <<'PY'
import json, sys
existing = {
    "GITHUB_APP_ID": __import__("os").environ.get("GITHUB_APP_ID"),
    "OPENAI_API_KEY": __import__("os").environ.get("OPENAI_API_KEY"),
    "OPENAI_MODEL": __import__("os").environ.get("OPENAI_MODEL", "gpt-4o"),
    "DYNAMODB_REVIEWS_TABLE": __import__("os").environ.get("DYNAMODB_REVIEWS_TABLE", "reviews"),
    "DYNAMODB_REPOSITORIES_TABLE": __import__("os").environ.get("DYNAMODB_REPOSITORIES_TABLE", "repositories"),
    "METRIC_NAMESPACE": __import__("os").environ.get("METRIC_NAMESPACE", "PRReviewer"),
    "GITHUB_APP_PRIVATE_KEY": sys.argv[1],
}
print(json.dumps({"Variables": existing}))
PY
aws lambda update-function-configuration --function-name "$FUNCTION_NAME" \
  --region "$REGION" --environment "file://$TMP_ENV" >/dev/null
aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
rm -f "$TMP_ENV"

# Event source mapping: SQS -> Lambda, with partial batch failure reporting.
echo "==> Wiring SQS event source mapping"
EXISTING_MAPPING="$(aws lambda list-event-source-mappings --function-name "$FUNCTION_NAME" \
  --region "$REGION" --query "EventSourceMappings[?EventSourceArn=='$SQS_QUEUE_ARN'].UUID" --output text)"

if [ -z "$EXISTING_MAPPING" ] || [ "$EXISTING_MAPPING" = "None" ]; then
  aws lambda create-event-source-mapping --function-name "$FUNCTION_NAME" \
    --event-source-arn "$SQS_QUEUE_ARN" --batch-size 5 \
    --function-response-types ReportBatchItemFailures \
    --region "$REGION" >/dev/null
  echo "    Created event source mapping."
else
  echo "    Event source mapping already exists ($EXISTING_MAPPING)."
fi

rm -rf "$BUILD_DIR"
echo "==> Lambda '$FUNCTION_NAME' deployed."
