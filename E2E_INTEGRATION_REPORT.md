# Dawem E2E and Integration Audit

## Wiring (current state)

Flutter → API Gateway (Cognito JWT) → Lambda proxy handlers → dawem-ai (`X-API-Key`), with plans, files
and quizzes persisted in the existing DynamoDB single table.

- **CDK** (`infrastructure/lib/streakly-stack.ts`): 8 new Lambdas from `backend/functions/ai/handler.ts`
  (`POST /files/index`, `/files/index-s3`, `/files/index-notes`, `GET /files`, `DELETE /files/{fileId}`,
  `POST /plans/generate`, `/quizzes/generate`, `/quizzes/{quizId}/submit`), behind the Cognito authorizer, 29 s
  timeout (API Gateway's limit is 30 s). `AI_SERVICE_URL` / `AI_SERVICE_API_KEY` come from CDK context or the
  deploy-time environment; `FILES_BUCKET` and `TABLE_NAME` are set by the stack. Table access is least-privilege
  per function; the Lambdas have no bucket access (dawem-ai reads S3 itself).
- **Backend**: `shared/keys.ts` gained `learningFile` (`FILE#`), `quiz` (`QUIZ#`) and `quizResult`
  (`QUIZRESULT#quizId#resultId`), which the AI handler and its tests already expected. `GET /tasks/today` now also
  returns `description`, `dayNumber`, `kind`, `durationMinutes` and `sourceRefs`, so AI tasks keep their citations
  after an app restart.
- **Flutter**: `ApiService` makes real HTTP calls (previously an in-memory simulation). `API_MODE=backend` uses API
  Gateway; `API_MODE=demo` talks to dawem-ai directly (`http://localhost:8000`). `AppState` takes XP and streak from
  the backend response, uploads and indexes the picked file, and generates the plan from the indexed files.
  The snake_case ↔ camelCase conversion is `json_case.dart` + `ai_mappers.dart`.
- Legacy "Streakly" strings were renamed to "Dawem" where the user sees them (app names, window titles, the push
  notification title, the web manifest, docs). Package names, application ids and CDK resource names are unchanged.

## Verification performed in this environment

| Check | Result |
|---|---|
| `dawem-ai`: `python -m pytest -q` | 95 passed, 16 skipped. The skipped tests need FastAPI, which is not installed here (the previous audit, with FastAPI available, reported 134 passed) |
| Cross-layer contract: a `StudyPlan` and a `Quiz` built with dawem-ai's real Pydantic models, parsed by the backend's zod schemas (`aiContracts.ts`), mapped by `aiMapping.ts` | Pass: 3 task items with `GOAL#`/`TASK#date-id` keys, null `page_number`/`snippet`/`score` handled, the client quiz contains no answers or explanations, grading works |
| Backend TypeScript (`tsc --strict`) over `shared/` and `functions/ai/` | No errors in the changed code; remaining errors are only the missing AWS SDK typings in this environment |

## Not verified here (no package registry or SDK access)

`npm ci`, so `backend` vitest, `infrastructure` jest/`cdk synth`, and `npm audit fix` could not run. There is no
Flutter/Dart SDK, so `flutter analyze` / `flutter test` could not run, and the Dart code was reviewed by reading
only. The new infrastructure tests (routes, env vars, 29 s timeout, no wildcard permissions) and the existing Dart tests
(`test/core/*`, which define the `ApiService` contract) have not been executed. Run `npm test` in `backend/` and
`infrastructure/`, `flutter analyze` and `flutter test` before deploying. The live deployment (Cognito, API Gateway,
DynamoDB, a real LLM provider) has not been exercised.

## Known gaps

See "Known limitations" in `README.md`: synchronous plan generation (30 s ceiling), no quiz screen, Cognito sign-in
not wired into the app (backend mode needs `DEV_AUTH_TOKEN`), partial per-goal progress after a restart.
