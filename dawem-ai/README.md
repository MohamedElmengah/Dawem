# dawem-ai

Python AI / RAG backend for Dawem: ingestion -> grounded retrieval -> LLM pipelines -> FastAPI.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env            # set GEMINI_API_KEY and/or GROQ_API_KEY (and DAWEM_AI_API_KEY)
uvicorn dawem_ai.api.app:app --host 0.0.0.0 --port 8000
pytest                          # fully offline
```

## Endpoints

| Method & path | Body | Success | Notes |
|---|---|---|---|
| `GET /health` | - | 200 `{"status":"ok"}` | 503 `{"status":"starting"}` until wired. No API key needed. |
| `POST /files/index` | multipart: `user_id`, `file_id`, `file` (.pdf/.txt/.md), optional `file_name` | 200 `{file_id, chunk_count}` | `file_name` is what citations show (defaults to the upload's name). Re-indexing replaces. |
| `POST /files/index-s3` | JSON `{user_id, file_id, bucket, s3_key}`, optional `file_name` | 200 `{file_id, chunk_count}` | Alternative to the multipart upload: the service reads the object from S3 with its IAM role (needs `s3:GetObject`). `file_name` (the documentation's `LearningFile.filename`) is what citations show; defaults to the key's last segment. 404 object missing, 413 over `MAX_UPLOAD_MB`, 415 bad extension, 503 S3 unreadable. Optional `DAWEM_S3_ALLOWED_BUCKETS` allow-list (other bucket -> 400). |
| `DELETE /files/{file_id}?user_id=...` | - | 204 | Removes every indexed chunk of the file. 403 if it belongs to another user, 404 if it does not exist. |
| `POST /files/index-notes` | JSON `{user_id, file_id, file_name, text}` | 200 `{file_id, chunk_count}` | Notes typed/pasted in the app. |
| `POST /plans/generate` | `StudyPlanRequest`: `user_id`, `file_ids`, `goal`, `duration_days`, `daily_minutes`, `level`, optional `context` (FR-02) | 200 `StudyPlan` | Generated in 7-day batches; every task cited. `level` is a free string; `context` is the learner's optional notes (never treated as source material). |
| `POST /quizzes/generate` | `QuizRequest` (`topic` optional) | 200 `Quiz` | No topic -> broad quiz over the selected files. |

If `DAWEM_AI_API_KEY` is set, send it as `X-API-Key`. The service trusts the calling backend
for `user_id`, so it must not be reachable by end users directly. Every response carries
`X-Request-ID` (an incoming one is echoed).

## Errors (body is always `ErrorResponse`: `{code, detail, request_id}`)

| Status | `code` | When |
|---|---|---|
| 422 | `insufficient_context` | Retrieval found nothing relevant (before any LLM call). **The only 422 we send.** |
| 403 | `forbidden` | The file exists but belongs to another user (also when indexing a `file_id` already owned by someone else). |
| 404 | `file_not_found` | `file_id` does not exist / is not indexed (or the S3 object does not exist). |
| 400 | `invalid_request` | Malformed body/fields (input is never echoed back). |
| 400 / 413 / 415 | `invalid_file` | Unreadable/empty file, too large, unsupported type. |
| 401 | `unauthorized` | Missing/wrong `X-API-Key`. |
| 429 | `rate_limited` | Every AI provider hit its rate limit; client retries with backoff. |
| 502 | `invalid_model_output` | Model could not produce valid, validly-cited output. |
| 503 | `provider_unavailable` / `indexing_failed` | All LLM providers failed (not only rate limits) / embedding, vector-store or S3 read failed after retries. |
| 500 | `internal_error` | Anything unexpected (no details). |

## Behaviour worth knowing

* **Plans**: `ceil(duration_days / 7)` sequential LLM calls (365 days ~ 53 calls, so allow for latency).
  Later batches are told which topics were already covered. A task whose cited sources do not
  resolve to retrieved chunks is dropped; a batch that loses a whole day is regenerated, then 502.
* **Citations** are built from retrieval metadata (`file_name`, `page_number`, `chunk_id`); the model
  only picks labels (`C1`, `C2`...) from the context it was given.
* **Topic-less quizzes** use `Retriever.retrieve_representative`: chunks spread evenly through each
  selected file. There is no query, so no similarity threshold applies; the 422 rule becomes
  "the files must contain at least ~100 characters of indexed text". Citations have `score: null`.
* **DynamoDB** (all of it runs in the backend, because `Decimal` values cannot travel over JSON):
  * Tasks: `StudyPlan.model_validate(response_json).to_task_items(goal_id, start_date)` returns one item
    per `DailyTask` with the documentation's attributes (`taskId, goalId, userId, dayNumber, date,
    title, description, durationMinutes, status, sourceRefs`) plus `kind`. `date = start_date + day - 1`;
    `status` defaults to `"pending"`. Use this, not `to_dynamodb_item()`, for real plans: a whole
    365-day plan in one item can exceed DynamoDB's 400 KB limit.
  * `Quiz.model_validate(response_json).to_dynamodb_item()` stores the quiz (the documentation has no Quiz
    entity). It contains the answers: strip `correct_option_index` / `model_answer` before sending a
    quiz to Flutter and grade submissions on the backend.
  * `pk`/`sk` are still placeholders (`USER#..` / `PLAN#..`, `TASK#..`, `QUIZ#..`) because the documentation
    does not define the key design.
* **Status codes follow the documentation's error table**: 403 foreign file, 404 missing file, 422
  insufficient context, 429 rate limited, 413 too large, 503 provider unavailable.
