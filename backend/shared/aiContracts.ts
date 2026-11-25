import { z } from "zod";

/** Response shapes of dawem-ai (snake_case). Validated so a drifting AI service fails loudly as 502. */

export const SourceCitation = z.object({
  chunk_id: z.string(),
  file_id: z.string(),
  file_name: z.string(),
  page_number: z.number().int().nullish(),
  snippet: z.string().nullish(),
  score: z.number().nullish(),
});
export type SourceCitation = z.infer<typeof SourceCitation>;

export const IndexResponse = z.object({ file_id: z.string(), chunk_count: z.number().int() });

export const StudyPlan = z.object({
  plan_id: z.string(),
  title: z.string(),
  tasks: z
    .array(
      z.object({
        task_id: z.string(),
        day_number: z.number().int().min(1),
        title: z.string(),
        description: z.string(),
        kind: z.string(),
        duration_minutes: z.number().int(),
        sources: z.array(SourceCitation),
      })
    )
    .min(1),
});
export type StudyPlan = z.infer<typeof StudyPlan>;

export const Quiz = z.object({
  quiz_id: z.string(),
  title: z.string(),
  mcqs: z.array(
    z.object({
      question_id: z.string(),
      question: z.string(),
      options: z.array(z.string()),
      correct_option_index: z.number().int(),
      explanation: z.string(),
      difficulty: z.string(),
      sources: z.array(SourceCitation),
    })
  ),
  recall_questions: z.array(
    z.object({
      question_id: z.string(),
      question: z.string(),
      model_answer: z.string(),
      sources: z.array(SourceCitation),
    })
  ),
});
export type Quiz = z.infer<typeof Quiz>;
