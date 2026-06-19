#!/usr/bin/env bash
# Sets up the messaging layer - SNS topic, SQS queue + DLQ, and wires them
# together. Prints out the ARNs at the end, you'll need those for the
# EC2 .env file and the Lambda deploy script.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
TOPIC_NAME="${SNS_TOPIC_NAME:-pr-review-events}"
QUEUE_NAME="${SQS_QUEUE_NAME:-pr-review-queue}"
DLQ_NAME="${SQS_DLQ_NAME:-pr-review-dlq}"
MAX_RECEIVE_COUNT="${MAX_RECEIVE_COUNT:-5}"   # retries before a message goes to DLQ

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

echo "==> Creating SNS topic '$TOPIC_NAME'"
TOPIC_ARN="$(aws sns create-topic --name "$TOPIC_NAME" --region "$REGION" --query TopicArn --output text)"
echo "    TOPIC_ARN=$TOPIC_ARN"

echo "==> Creating DLQ '$DLQ_NAME'"
DLQ_URL="$(aws sqs create-queue --queue-name "$DLQ_NAME" --region "$REGION" \
  --attributes MessageRetentionPeriod=1209600 \
  --query QueueUrl --output text)"
DLQ_ARN="$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" --region "$REGION" \
  --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)"
echo "    DLQ_ARN=$DLQ_ARN"

echo "==> Creating main queue '$QUEUE_NAME' with redrive policy"
REDRIVE_POLICY="{\"deadLetterTargetArn\":\"$DLQ_ARN\",\"maxReceiveCount\":\"$MAX_RECEIVE_COUNT\"}"
QUEUE_URL="$(aws sqs create-queue --queue-name "$QUEUE_NAME" --region "$REGION" \
  --attributes VisibilityTimeout=180,MessageRetentionPeriod=345600,RedrivePolicy="$REDRIVE_POLICY" \
  --query QueueUrl --output text)"
QUEUE_ARN="$(aws sqs get-queue-attributes --queue-url "$QUEUE_URL" --region "$REGION" \
  --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)"
echo "    QUEUE_ARN=$QUEUE_ARN"

echo "==> Allowing SNS to send to the SQS queue"
POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "sns.amazonaws.com"},
    "Action": "sqs:SendMessage",
    "Resource": "$QUEUE_ARN",
    "Condition": {"ArnEquals": {"aws:SourceArn": "$TOPIC_ARN"}}
  }]
}
JSON
)
aws sqs set-queue-attributes --queue-url "$QUEUE_URL" --region "$REGION" \
  --attributes Policy="$POLICY"

echo "==> Subscribing SQS to SNS with RAW message delivery"
SUB_ARN="$(aws sns subscribe --topic-arn "$TOPIC_ARN" --protocol sqs \
  --notification-endpoint "$QUEUE_ARN" --region "$REGION" \
  --attributes RawMessageDelivery=true --return-subscription-arn \
  --query SubscriptionArn --output text)"
echo "    SUBSCRIPTION_ARN=$SUB_ARN"

cat <<EOF

============================================================
Messaging layer ready. Record these values:

  SNS_TOPIC_ARN = $TOPIC_ARN
  SQS_QUEUE_ARN = $QUEUE_ARN
  SQS_QUEUE_URL = $QUEUE_URL
  SQS_DLQ_ARN   = $DLQ_ARN

Put SNS_TOPIC_ARN into the FastAPI .env (EC2).
Put SQS_QUEUE_ARN into infra/04_lambda_deploy.sh (event source mapping).
============================================================
EOF
