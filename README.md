# Dawem (داوم)

AI-powered habit tracking and study management. Users upload study material (PDF / TXT / MD or pasted
notes); a RAG pipeline indexes it and generates a cited daily study plan and quizzes, while the app
tracks streaks, XP, badges and challenges.

```
Flutter app ──HTTPS + Cognito JWT──▶ API Gateway ──▶ Lambda (backend/) ──▶ DynamoDB
   (lib/)                                               │
                                                        └─ X-API-Key ─▶ dawem-ai (FastAPI RAG, dawem-ai/)
```

| Layer | Folder | Stack |
|---|---|---|
| Mobile / web app | `lib/`, `test/` | Flutter, `provider`, `http` |
| Application API | `backend/` | TypeScript Lambdas, DynamoDB single table, zod |
| Infrastructure | `infrastructure/` | AWS CDK (Cognito, HTTP API, Lambda, DynamoDB, S3, SQS, EventBridge, CloudWatch) |
| AI service | `dawem-ai/` | Python FastAPI, ChromaDB, Gemini/Groq with fallback |

The app never calls dawem-ai directly in production: the AI endpoints are proxied by Lambdas that take the
user id from the verified JWT, forward `X-API-Key`, validate the AI response, and persist plans, files and
quizzes in DynamoDB. **Demo mode** (below) skips the backend and talks straight to dawem-ai for local work.

## Setup and local run

### 1. dawem-ai (Python 3.11+)

```bash
cd dawem-ai
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set GEMINI_API_KEY and/or GROQ_API_KEY; optionally DAWEM_AI_API_KEY
uvicorn dawem_ai.api.app:app --host 0.0.0.0 --port 8000
python -m pytest -q             # fully offline
```

Accepts `.pdf`, `.txt`, `.md`. If `DAWEM_AI_API_KEY` is set, every call except `/health` needs it as `X-API-Key`.

### 2. Backend and infrastructure (Node 24)

```bash
cd backend && npm ci && npm run typecheck && npm test
cd ../infrastructure && npm ci && npm test
npx cdk synth -c stage=dev
```

Deploy (needs AWS credentials and a one-time `cdk bootstrap`). The AI service must be reachable from the
Lambdas over public HTTPS (they run outside a VPC):

```bash
cd infrastructure
AI_SERVICE_URL=https://ai.example.com AI_SERVICE_API_KEY=<same as DAWEM_AI_API_KEY> \
  npx cdk deploy -c stage=dev          # or -c aiServiceUrl=... -c aiServiceApiKey=...
```

Without `AI_SERVICE_URL` the AI routes are deployed but answer `503 AI_NOT_CONFIGURED`. Other deploy context:
`allowedOrigin` (CORS, default `*`), `alertEmail`, `platformApplicationArn` (push). `backend/.env.example`
documents every Lambda environment variable; they are set by the stack, not by a `.env` in AWS.

> `AI_SERVICE_API_KEY` ends up as a Lambda environment variable (and in the CloudFormation template). For
> production, move it to Secrets Manager / SSM and read it at cold start.

### 3. Flutter app

Configuration is compile-time, from `--dart-define` values (see `.env.example`; never put secrets in it, the
values can be read from the app binary).

```bash
cp .env.example .env
flutter pub get
flutter run --dart-define-from-file=.env
flutter analyze && flutter test
```

| Mode | When | What happens |
|---|---|---|
| **demo** (`API_MODE=demo`, the default without `API_BASE_URL`) | Local work | Files, plans and quizzes go straight to dawem-ai at `AI_BASE_URL` (default `http://localhost:8000`; Android emulator: `http://10.0.2.2:8000`). Goals, tasks, XP and streak live in memory for the session. |
| **backend** (`API_MODE=backend`, `API_BASE_URL=<ApiUrl output>`) | Against a deployed stack | Everything goes through API Gateway with `Authorization: Bearer <token>`. |

**Authentication:** the app's sign-in screens are still local-only; Cognito sign-in is not wired. For backend
mode, create a user in the stack's user pool and pass a token as `DEV_AUTH_TOKEN` (dev stages enable
password auth for this):

```bash
aws cognito-idp admin-create-user --user-pool-id <UserPoolId> --username you@example.com \
  --message-action SUPPRESS
aws cognito-idp admin-set-user-password --user-pool-id <UserPoolId> --username you@example.com \
  --password '<Passw0rd>' --permanent
aws cognito-idp initiate-auth --client-id <UserPoolClientId> --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=you@example.com,PASSWORD='<Passw0rd>' --query AuthenticationResult.IdToken
```

ID tokens expire after an hour. `ApiService` also takes an `authTokenProvider` for when real sign-in lands.

### JSON conventions

dawem-ai speaks `snake_case` (`plan_id`, `day_number`, `duration_minutes`, `sources`, `file_id`,
`chunk_count`); the backend and the app's models speak `camelCase` (`planId`, `dayNumber`, `durationMinutes`,
`sourceRefs`, `fileId`, `chunkCount`).

* Lambda side: `backend/shared/aiContracts.ts` validates AI responses (a shape mismatch is a 502) and
  `aiMapping.ts` converts them.
* Flutter side: `lib/core/network/json_case.dart` + `ai_mappers.dart`. Requests are explicit builders; responses
  are converted and then read by the models.

## API (all routes need a Cognito JWT)

Existing: `/me`, `/goals`, `/goals/{goalId}`, `/goals/{goalId}/tasks`, `/tasks/today`,
`/tasks/{taskId}/complete?goalId=`, `/streak`, `/xp`, `/badges`, `/challenges`, `/challenges/{id}/join`,
`/devices/{deviceId}`; see `infrastructure/README.md`.

AI routes (proxy to dawem-ai):

| Method | Path | Purpose |
|---|---|---|
| POST | `/files/index` | Multipart upload (`file`, `file_id`, optional `file_name`); stored as a `FILE#` record |
| POST | `/files/index-s3` | `{fileId, s3Key, fileName?}`; key must be under `uploads/<your user id>/` of the stack's bucket |
| POST | `/files/index-notes` | `{fileId, fileName, text}` |
| GET | `/files` | The caller's indexed files |
| DELETE | `/files/{fileId}` | Removes the index and the record (idempotent) |
| POST | `/plans/generate` | `{goalId, fileIds, context?}` generates a cited plan and stores its tasks (`TASK#` items, so `/tasks/today` and `/tasks/{id}/complete` work on them). `409 PLAN_EXISTS` if the goal already has one |
| POST | `/quizzes/generate` | `{fileIds, topic?, numMcqs?, numRecall?}`; the quiz is stored with answers, the response has none |
| POST | `/quizzes/{quizId}/submit` | `{answers:[{questionId, selectedOptionIndex}], recallAnswers?}` graded server-side; the attempt is stored |

## Known limitations

* **Plan generation is synchronous.** API Gateway HTTP APIs cut integrations off at 30 s (the Lambda stops
  waiting at 25 s and answers `504 AI_TIMEOUT`). dawem-ai makes one LLM call per 7 days of plan, so long plans
  (several weeks and up) can exceed that. The stack already has an unused `AiPlanQueue` for moving this to an
  async job.
* No quiz screen exists yet; `AppState.generateQuiz` / `submitQuiz` and the API are ready for one.
* After an app restart, backend mode only knows today's tasks per goal (there is no "list a goal's tasks"
  endpoint), so per-goal progress is partial until a plan is generated again in the session.
* Goals cannot be deleted server-side yet, and challenge discovery (browsing others' challenges) has no endpoint:
  backend mode lists the caller's own challenges.
* Flutter package, Android/iOS application ids and the CDK stack/resource names still use the original
  `streakly` identifiers. Renaming them would change the app's store identity and replace deployed resources,
  so only user-facing strings were renamed.
* CORS is `*` by default; set `allowedOrigin` before production.
