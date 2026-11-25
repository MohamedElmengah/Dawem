# Streakly Operations Runbook

Operational guide for monitoring, troubleshooting, and deploying the **Streakly** serverless backend in `eu-north-1`.

---

## 1. Monitoring & Dashboards

- **CloudWatch Dashboard:** Open `Streakly-<stage>` (e.g., `Streakly-dev`) in AWS Console (`eu-north-1`) to view:
  - API Gateway request count, 4xx/5xx errors, and Average / p99 latency.
  - Lambda errors and invocations across all 18 functions.
  - DynamoDB consumed read/write capacity units.
  - SQS `StreamDlq` visible message count.
- **API Access Logs:** Structured JSON logs (`requestId`, `ip`, `method`, `path`, `status`, `latency`, `error`) are stored in the `ApiAccessLogs` CloudWatch Log Group (30-day retention).
- **Distributed Tracing:** All 18 Lambda functions have **AWS X-Ray** (`Tracing.ACTIVE`) enabled. Use CloudWatch Service Map / X-Ray Traces to inspect slow requests or downstream DynamoDB errors.

---

## 2. Alarm Triage & Incident Response

All 4 CloudWatch Alarms publish notifications to the SNS `AlertsTopic` (email subscription must be confirmed after first deploy).

### `Api5xxErrors` (Threshold: >= 5 errors in 5 min)
1. Query the `ApiAccessLogs` log group in CloudWatch Logs Insights for `status >= 500` to identify the failing `path` and `requestId`.
2. Open the corresponding Lambda function's log group (`/aws/lambda/Streakly-<stage>-<FunctionName>`) and inspect the stack trace.
3. Check X-Ray traces if the error is a timeout (default Lambda timeout is 10s).

### `CompleteErrors` (Threshold: >= 3 errors in 5 min)
1. Open the `TasksComplete` Lambda logs in CloudWatch.
2. Verify DynamoDB table health and check for conditional check failures, validation errors, or malformed client payloads.
3. Confirm `TABLE_NAME` environment variable matches the active DynamoDB table.

### `StreamDlqNotEmpty` (Threshold: >= 1 message in 5 min)
Messages land here when `StreamConsumer` fails to process a `Completion` event after 5 retries (with batch bisection enabled).
1. Check `StreamConsumer` CloudWatch Logs for the exact exception.
2. Inspect the failed event payload in `StreamDlq` (do not delete until root cause is fixed):
   ```bash
   aws sqs receive-message \
     --queue-url <STREAM_DLQ_URL> \
     --max-number-of-messages 5 \
     --region eu-north-1