/** Single-table key builders. Keep every key format in one place. */

export const keys = {
  user: (userId: string) => ({ PK: `USER#${userId}`, SK: "PROFILE" }),
  goal: (userId: string, goalId: string) => ({ PK: `USER#${userId}`, SK: `GOAL#${goalId}` }),
  // taskId looks like "2026-11-20-a1b2c3d4": the date prefix lets us query a day's tasks.
  task: (goalId: string, taskId: string) => ({ PK: `GOAL#${goalId}`, SK: `TASK#${taskId}` }),
  completion: (userId: string, goalId: string, taskId: string) => ({
    PK: `USER#${userId}`,
    SK: `COMPLETION#${goalId}#${taskId}`,
  }),
  streak: (userId: string) => ({ PK: `USER#${userId}`, SK: "STREAK" }),
  xp: (userId: string) => ({ PK: `USER#${userId}`, SK: "XP" }),
  xpLedger: (userId: string, eventKey: string) => ({ PK: `USER#${userId}`, SK: `XPLEDGER#${eventKey}` }),
  userBadge: (userId: string, badgeId: string) => ({ PK: `USER#${userId}`, SK: `BADGE#${badgeId}` }),
  device: (userId: string, deviceId: string) => ({ PK: `USER#${userId}`, SK: `DEVICE#${deviceId}` }),
  challenge: (challengeId: string) => ({ PK: `CHALLENGE#${challengeId}`, SK: "META" }),
  challengeMember: (challengeId: string, userId: string) => ({
    PK: `CHALLENGE#${challengeId}`,
    SK: `MEMBER#${userId}`,
  }),
  // Pointer on the user's side so the stream consumer can find a user's challenges.
  userChallenge: (userId: string, challengeId: string) => ({
    PK: `USER#${userId}`,
    SK: `CHALLENGE#${challengeId}`,
  }),
  // One marker per (completion, challenge): makes stream retries idempotent.
  // AI layer: indexed learning material, generated quizzes (answers included) and graded attempts.
  learningFile: (userId: string, fileId: string) => ({ PK: `USER#${userId}`, SK: `FILE#${fileId}` }),
  quiz: (userId: string, quizId: string) => ({ PK: `USER#${userId}`, SK: `QUIZ#${quizId}` }),
  quizResult: (userId: string, quizId: string, resultId: string) => ({
    PK: `USER#${userId}`,
    SK: `QUIZRESULT#${quizId}#${resultId}`,
  }),
  challengeProgress: (userId: string, challengeId: string, goalId: string, taskId: string) => ({
    PK: `USER#${userId}`,
    SK: `CPROG#${challengeId}#${goalId}#${taskId}`,
  }),
};

export const TABLE_NAME = () => {
  const name = process.env.TABLE_NAME;
  if (!name) throw new Error("TABLE_NAME is not set");
  return name;
};
