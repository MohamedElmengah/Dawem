import { describe, expect, it } from "vitest";
import { addDays, gradeQuiz, planToTaskItems, quizForClient, toSourceRef } from "../shared/aiMapping";
import { keys } from "../shared/keys";
import type { Quiz, StudyPlan } from "../shared/aiContracts";

const src = { chunk_id: "c1", file_id: "f1", file_name: "book.pdf", page_number: 12, snippet: "s", score: 0.9 };

describe("toSourceRef", () => {
  it("converts snake_case citations to camelCase and omits missing optionals", () => {
    expect(toSourceRef(src)).toEqual({
      fileId: "f1", chunkId: "c1", fileName: "book.pdf", pageNumber: 12, snippet: "s", score: 0.9,
    });
    expect(toSourceRef({ chunk_id: "c", file_id: "f", file_name: "n", page_number: null })).toEqual({
      fileId: "f", chunkId: "c", fileName: "n",
    });
  });
});

describe("addDays", () => {
  it("does calendar arithmetic across month and year ends", () => {
    expect(addDays("2026-11-28", 3)).toBe("2026-12-01");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2028-02-28", 1)).toBe("2028-02-29");
  });
});

describe("planToTaskItems", () => {
  const plan: StudyPlan = {
    plan_id: "plan1",
    title: "Plan",
    tasks: [
      { task_id: "aaaaaaaabbbb", day_number: 1, title: "T1", description: "d", kind: "read", duration_minutes: 20, sources: [src] },
      { task_id: "ccccccccdddd", day_number: 3, title: "T3", description: "d", kind: "quiz", duration_minutes: 15, sources: [src] },
    ],
  };
  const items = planToTaskItems(plan, { userId: "u1", goalId: "g1", startDate: "2026-11-30", nowIso: "now" }, keys.task);

  it("uses the same item shape as manual tasks so today/complete work unchanged", () => {
    expect(items[0]).toMatchObject({
      PK: "GOAL#g1", SK: "TASK#2026-11-30-aaaaaaaa", taskId: "2026-11-30-aaaaaaaa",
      entity: "Task", goalId: "g1", userId: "u1", date: "2026-11-30", estimatedMinutes: 20,
    });
  });
  it("places each task on startDate + day_number - 1 and keeps citations as sourceRefs", () => {
    expect(items[1]).toMatchObject({ date: "2026-12-02", dayNumber: 3, kind: "quiz", durationMinutes: 15 });
    expect(items[1]!.sourceRefs).toEqual([toSourceRef(src)]);
  });
});

describe("quiz", () => {
  const quiz: Quiz = {
    quiz_id: "q1",
    title: "Quiz",
    mcqs: [
      { question_id: "m1", question: "Q1?", options: ["a", "b", "c", "d"], correct_option_index: 2, explanation: "because", difficulty: "easy", sources: [src] },
      { question_id: "m2", question: "Q2?", options: ["a", "b", "c", "d"], correct_option_index: 0, explanation: "since", difficulty: "hard", sources: [src] },
    ],
    recall_questions: [{ question_id: "r1", question: "Recall?", model_answer: "secret answer", sources: [src] }],
  };

  it("never sends answers or explanations to the client before submission", () => {
    const text = JSON.stringify(quizForClient("q1", "Quiz", quiz));
    expect(text).not.toContain("correct");
    expect(text).not.toContain("because");
    expect(text).not.toContain("secret answer");
    expect(quizForClient("q1", "Quiz", quiz).mcqs[0]).toMatchObject({ questionId: "m1", options: ["a", "b", "c", "d"] });
  });

  const stored = [
    { questionId: "m1", correctOptionIndex: 2, explanation: "because" },
    { questionId: "m2", correctOptionIndex: 0, explanation: "since" },
  ];
  it("grades answers and counts unanswered questions as wrong", () => {
    const g = gradeQuiz(stored, [{ questionId: "m1", selectedOptionIndex: 2 }]);
    expect(g).toMatchObject({ score: 1, total: 2, percentage: 50 });
    expect(g.mcqResults[1]).toMatchObject({ selectedOptionIndex: null, isCorrect: false, correctOptionIndex: 0 });
  });
  it("handles a perfect score and an empty quiz", () => {
    expect(gradeQuiz(stored, [{ questionId: "m1", selectedOptionIndex: 2 }, { questionId: "m2", selectedOptionIndex: 0 }]).percentage).toBe(100);
    expect(gradeQuiz([], []).percentage).toBe(0);
  });
});
