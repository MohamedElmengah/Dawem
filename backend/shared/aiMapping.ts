import type { SourceCitation, StudyPlan, Quiz } from "./aiContracts";

/** snake_case AI citation -> camelCase `sourceRefs` entry used across the app. */
export function toSourceRef(s: SourceCitation) {
  return {
    fileId: s.file_id,
    chunkId: s.chunk_id,
    fileName: s.file_name,
    ...(s.page_number != null ? { pageNumber: s.page_number } : {}),
    ...(s.snippet != null ? { snippet: s.snippet } : {}),
    ...(s.score != null ? { score: s.score } : {}),
  };
}

/** YYYY-MM-DD + n days, calendar arithmetic in UTC so it never depends on the Lambda's timezone. */
export function addDays(isoDate: string, days: number): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * One DynamoDB item per AI task, in the same shape `tasks/handler.ts` writes (PK GOAL#, SK TASK#date-id),
 * so GET /tasks/today and POST /tasks/{id}/complete work on AI tasks without any special casing.
 */
export function planToTaskItems(
  plan: StudyPlan,
  ctx: { userId: string; goalId: string; startDate: string; nowIso: string },
  key: (goalId: string, taskId: string) => { PK: string; SK: string }
): Record<string, unknown>[] {
  return plan.tasks.map((t) => {
    const date = addDays(ctx.startDate, t.day_number - 1);
    const taskId = `${date}-${t.task_id.slice(0, 8)}`;
    return {
      ...key(ctx.goalId, taskId),
      entity: "Task",
      taskId,
      goalId: ctx.goalId,
      userId: ctx.userId,
      planId: plan.plan_id,
      source: "ai",
      dayNumber: t.day_number,
      date,
      title: t.title,
      description: t.description,
      kind: t.kind,
      estimatedMinutes: t.duration_minutes,
      durationMinutes: t.duration_minutes,
      sourceRefs: t.sources.map(toSourceRef),
      createdAt: ctx.nowIso,
    };
  });
}

/** The quiz as the app may see it before answering: no correct answers, no explanations. */
export function quizForClient(quizId: string, title: string, quiz: Quiz) {
  return {
    quizId,
    title,
    mcqs: quiz.mcqs.map((q) => ({
      questionId: q.question_id,
      question: q.question,
      options: q.options,
      difficulty: q.difficulty,
      sourceRefs: q.sources.map(toSourceRef),
    })),
    recallQuestions: quiz.recall_questions.map((q) => ({
      questionId: q.question_id,
      question: q.question,
      sourceRefs: q.sources.map(toSourceRef),
    })),
  };
}

export interface StoredMcq {
  questionId: string;
  correctOptionIndex: number;
  explanation: string;
}

export interface QuizAnswer {
  questionId: string;
  selectedOptionIndex: number;
}

/** Grade MCQ answers. Unanswered questions count as wrong; the percentage is over all questions. */
export function gradeQuiz(mcqs: StoredMcq[], answers: QuizAnswer[]) {
  const byId = new Map(answers.map((a) => [a.questionId, a.selectedOptionIndex]));
  const mcqResults = mcqs.map((q) => {
    const selected = byId.get(q.questionId);
    return {
      questionId: q.questionId,
      selectedOptionIndex: selected ?? null,
      correctOptionIndex: q.correctOptionIndex,
      isCorrect: selected === q.correctOptionIndex,
      explanation: q.explanation,
    };
  });
  const score = mcqResults.filter((r) => r.isCorrect).length;
  const total = mcqs.length;
  return { mcqResults, score, total, percentage: total === 0 ? 0 : Math.round((score / total) * 100) };
}
