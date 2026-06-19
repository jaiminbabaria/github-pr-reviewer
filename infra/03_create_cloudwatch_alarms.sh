#!/usr/bin/env bash
# Creates the 3 CloudWatch alarms: queue backlog, Lambda error rate, slow
# processing. If you set ALARM_SNS_ARN it'll notify that topic too,
# otherwise alarms just show up in the console.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
QUEUE_NAME="${SQS_QUEUE_NAME:-pr-review-queue}"
FUNCTION_NAME="${LAMBDA_FUNCTION_NAME:-pr-review-worker}"
METRIC_NAMESPACE="${METRIC_NAMESPACE:-PRReviewer}"
ALARM_SNS_ARN="${ALARM_SNS_ARN:-}"

ALARM_ACTIONS=()
if [ -n "$ALARM_SNS_ARN" ]; then
  ALARM_ACTIONS=(--alarm-actions "$ALARM_SNS_ARN" --ok-actions "$ALARM_SNS_ARN")
fi

echo "==> Alarm 1: SQS queue depth > 10"
aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "pr-review-queue-depth-high" \
  --alarm-description "SQS visible messages > 10 (consumer falling behind)" \
  --namespace "AWS/SQS" --metric-name "ApproximateNumberOfMessagesVisible" \
  --dimensions Name=QueueName,Value="$QUEUE_NAME" \
  --statistic Maximum --period 60 --evaluation-periods 1 \
  --threshold 10 --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching \
  "${ALARM_ACTIONS[@]}"

echo "==> Alarm 2: Lambda error rate > 5% (metric math)"
aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "pr-review-lambda-error-rate-high" \
  --alarm-description "Lambda error rate over 5% across 5 minutes" \
  --evaluation-periods 1 --threshold 5 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching \
  "${ALARM_ACTIONS[@]}" \
  --metrics '[
    {
      "Id": "errorRate",
      "Expression": "100 * (errors / invocations)",
      "Label": "ErrorRatePct",
      "ReturnData": true
    },
    {
      "Id": "errors",
      "MetricStat": {
        "Metric": {"Namespace": "AWS/Lambda", "MetricName": "Errors",
          "Dimensions": [{"Name": "FunctionName", "Value": "'"$FUNCTION_NAME"'"}]},
        "Period": 300, "Stat": "Sum"
      },
      "ReturnData": false
    },
    {
      "Id": "invocations",
      "MetricStat": {
        "Metric": {"Namespace": "AWS/Lambda", "MetricName": "Invocations",
          "Dimensions": [{"Name": "FunctionName", "Value": "'"$FUNCTION_NAME"'"}]},
        "Period": 300, "Stat": "Sum"
      },
      "ReturnData": false
    }
  ]'

echo "==> Alarm 3: Review processing time > 45s (custom metric)"
aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "pr-review-processing-time-high" \
  --alarm-description "Average review processing time exceeded 45 seconds" \
  --namespace "$METRIC_NAMESPACE" --metric-name "ReviewProcessingTimeMs" \
  --statistic Average --period 300 --evaluation-periods 1 \
  --threshold 45000 --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching \
  "${ALARM_ACTIONS[@]}"

echo "==> All three alarms created/updated."
