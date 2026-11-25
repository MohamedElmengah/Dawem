import 'package:flutter_test/flutter_test.dart';
import 'package:streakly_app/core/network/ai_mappers.dart';
import 'package:streakly_app/core/network/models.dart';

/// A dawem-ai `StudyPlan` response (snake_case), as returned by POST /plans/generate.
Map<String, dynamic> aiPlanJson() => {
      'plan_id': 'plan-1',
      'user_id': 'u1',
      'file_ids': ['f1'],
      'goal': 'Learn Python',
      'title': 'Python in 3 days',
      'duration_days': 3,
      'daily_minutes': 60,
      'created_at': '2026-11-25T01:00:00Z',
      'tasks': [
        {
          'task_id': 't1',
          'day_number': 1,
          'title': 'Read chapter 1',
          'description': 'Variables and types',
          'kind': 'read',
          'duration_minutes': 30,
          'sources': [
            {
              'chunk_id': 'c1',
              'file_id': 'f1',
              'file_name': 'book.pdf',
              'page_number': 12,
              'snippet': 'A variable is...',
              'score': 0.82,
            },
          ],
        },
        {
          'task_id': 't2',
          'day_number': 3,
          'title': 'Quiz yourself',
          'description': 'Check your understanding',
          'kind': 'quiz',
          'duration_minutes': 20,
          'sources': [
            {'chunk_id': 'c2', 'file_id': 'f1', 'file_name': 'notes.txt', 'page_number': null},
          ],
        },
      ],
    };

Map<String, dynamic> aiQuizJson() => {
      'quiz_id': 'quiz-1',
      'title': 'Basics quiz',
      'mcqs': [
        {
          'question_id': 'm1',
          'question': 'What is a variable?',
          'options': ['a', 'b', 'c', 'd'],
          'correct_option_index': 2,
          'explanation': 'Because c.',
          'difficulty': 'easy',
          'sources': [
            {'chunk_id': 'c1', 'file_id': 'f1', 'file_name': 'book.pdf', 'page_number': 3},
          ],
        },
        {
          'question_id': 'm2',
          'question': 'What is a loop?',
          'options': ['a', 'b', 'c', 'd'],
          'correct_option_index': 0,
          'explanation': 'Because a.',
          'difficulty': 'hard',
          'sources': [
            {'chunk_id': 'c3', 'file_id': 'f1', 'file_name': 'book.pdf', 'page_number': 4},
          ],
        },
      ],
      'recall_questions': [
        {
          'question_id': 'r1',
          'question': 'Explain what a variable is.',
          'model_answer': 'A named value.',
          'sources': [
            {'chunk_id': 'c1', 'file_id': 'f1', 'file_name': 'book.pdf', 'page_number': 3},
          ],
        },
      ],
    };

void main() {
  group('requests: camelCase app values -> snake_case AI JSON', () {
    test('study plan request uses the AI contract keys', () {
      final json = AiMappers.studyPlanRequest(
        userId: 'u1',
        fileIds: ['f1', 'f2'],
        goal: 'Learn Python',
        durationDays: 7,
        dailyMinutes: 45,
        level: 'beginner',
        context: '  exam in May  ',
      );
      expect(json, {
        'user_id': 'u1',
        'file_ids': ['f1', 'f2'],
        'goal': 'Learn Python',
        'duration_days': 7,
        'daily_minutes': 45,
        'level': 'beginner',
        'context': 'exam in May',
      });
    });

    test('blank optional fields are omitted', () {
      final plan = AiMappers.studyPlanRequest(
        userId: 'u', fileIds: ['f'], goal: 'goal', durationDays: 1, dailyMinutes: 10, level: 'x', context: '  ');
      expect(plan.containsKey('context'), isFalse);
      final quiz = AiMappers.quizRequest(userId: 'u', fileIds: ['f'], topic: '');
      expect(quiz, {'user_id': 'u', 'file_ids': ['f']});
    });

    test('quiz and notes requests', () {
      expect(
        AiMappers.quizRequest(userId: 'u', fileIds: ['f'], topic: 'loops', numMcqs: 5, numRecall: 2),
        {'user_id': 'u', 'file_ids': ['f'], 'topic': 'loops', 'num_mcqs': 5, 'num_recall': 2},
      );
      expect(
        AiMappers.indexNotesRequest(userId: 'u', fileId: 'f', fileName: 'n', text: 't'),
        {'user_id': 'u', 'file_id': 'f', 'file_name': 'n', 'text': 't'},
      );
      expect(AiMappers.indexFileFields(userId: 'u', fileId: 'f', fileName: 'n'),
          {'user_id': 'u', 'file_id': 'f', 'file_name': 'n'});
    });
  });

  group('responses: snake_case AI JSON -> camelCase models', () {
    test('index response (file_id, chunk_count)', () {
      final r = AiMappers.indexResult({'file_id': 'f1', 'chunk_count': 7});
      expect(r.fileId, 'f1');
      expect(r.chunkCount, 7);
    });

    test('study plan: plan_id, day_number, duration_minutes, sources', () {
      final plan = AiMappers.studyPlan(aiPlanJson());
      expect(plan.planId, 'plan-1');
      expect(plan.title, 'Python in 3 days');
      expect(plan.tasks, hasLength(2));

      final t = plan.tasks.first;
      expect(t.taskId, 't1');
      expect(t.dayNumber, 1);
      expect(t.durationMinutes, 30);
      expect(t.kind, 'read');
      expect(t.sources.single.fileId, 'f1');
      expect(t.sources.single.pageNumber, 12);
      expect(t.sources.single.score, 0.82);
      expect(t.sourceRefs, ['book.pdf, p. 12']);
      expect(plan.tasks.last.sourceRefs, ['notes.txt']); // no page number
    });

    test('scheduling places each task on startDate + day_number - 1', () {
      final plan = AiMappers.studyPlan(aiPlanJson());
      final scheduled =
          plan.tasks.map((t) => t.scheduled(goalId: 'g1', startDate: DateTime(2026, 11, 29))).toList();
      expect(scheduled[0].date, '2026-11-29');
      expect(scheduled[1].date, '2026-12-01');
      expect(scheduled.every((t) => t.goalId == 'g1'), isTrue);
    });

    test('quiz keeps the answer key from dawem-ai (demo mode)', () {
      final quiz = AiMappers.quiz(aiQuizJson());
      expect(quiz.quizId, 'quiz-1');
      expect(quiz.mcqs.first.correctOptionIndex, 2);
      expect(quiz.mcqs.first.sources.single.label, 'book.pdf, p. 3');
      expect(quiz.recallQuestions.single.modelAnswer, 'A named value.');
      expect(quiz.hasAnswerKey, isTrue);
    });
  });

  group('backend (camelCase) payloads', () {
    test('quiz without answers has no answer key', () {
      final quiz = QuizModel.fromJson({
        'quizId': 'q',
        'title': 'T',
        'mcqs': [
          {
            'questionId': 'm1',
            'question': 'Q?',
            'options': ['a', 'b', 'c', 'd'],
            'sourceRefs': [
              {'fileId': 'f', 'chunkId': 'c', 'fileName': 'x.pdf', 'pageNumber': 1},
            ],
          },
        ],
        'recallQuestions': [],
      });
      expect(quiz.hasAnswerKey, isFalse);
      expect(quiz.mcqs.single.sources.single.fileName, 'x.pdf');
    });

    test('plan from POST /plans/generate, and the GET /tasks/today task shape', () {
      final plan = StudyPlanModel.fromJson({
        'goalId': 'g1',
        'planId': 'p1',
        'title': 'Plan',
        'tasks': [
          {
            'taskId': '2026-11-30-aaaaaaaa',
            'goalId': 'g1',
            'dayNumber': 1,
            'date': '2026-11-30',
            'title': 'Read',
            'description': 'd',
            'kind': 'read',
            'durationMinutes': 25,
            'sourceRefs': [
              {'fileId': 'f', 'chunkId': 'c', 'fileName': 'a.pdf', 'pageNumber': 2},
            ],
          },
        ],
      });
      expect(plan.goalId, 'g1');
      expect(plan.tasks.single.date, '2026-11-30');
      expect(plan.tasks.single.sourceRefs, ['a.pdf, p. 2']);

      final today = DailyTaskModel.fromJson({
        'taskId': 'x',
        'goalId': 'g1',
        'title': 'T',
        'date': '2026-11-30',
        'estimatedMinutes': 15,
        'completed': true,
      });
      expect(today.durationMinutes, 15);
      expect(today.isCompleted, isTrue);
    });

    test('legacy string sourceRefs still parse', () {
      final t = DailyTaskModel.fromJson({
        'taskId': 'x', 'goalId': 'g', 'title': 'T', 'sourceRefs': ['Chapter 1, p. 3'],
      });
      expect(t.sourceRefs, ['Chapter 1, p. 3']);
    });

    test('goal: level is capitalised and completed days derive from tasks', () {
      final goal = GoalModel.fromJson({
        'goalId': 'g1',
        'title': 'Learn',
        'description': 'd',
        'durationDays': 3,
        'dailyMinutes': 60,
        'startDate': '2026-11-30',
        'level': 'intermediate',
        'planId': 'p1',
        'fileIds': ['f1'],
        'tasks': [
          {'taskId': 'a', 'goalId': 'g1', 'title': 'A', 'date': '2026-11-30', 'completed': true},
          {'taskId': 'b', 'goalId': 'g1', 'title': 'B', 'date': '2026-11-30', 'completed': true},
          {'taskId': 'c', 'goalId': 'g1', 'title': 'C', 'date': '2026-12-01', 'completed': false},
        ],
      });
      expect(goal.level, 'Intermediate');
      expect(goal.planId, 'p1');
      expect(goal.learningFileIds, ['f1']);
      expect(goal.completedDays, 1);
    });
  });

  group('local quiz grading (demo mode)', () {
    final quiz = AiMappers.quiz(aiQuizJson());

    test('matches the backend rules', () {
      final r = QuizResultModel.gradeLocally(quiz, {'m1': 2}, recallAnswers: {'r1': 'mine'});
      expect(r.score, 1);
      expect(r.total, 2);
      expect(r.percentage, 50);
      expect(r.mcqResults[1].selectedOptionIndex, isNull); // unanswered counts as wrong
      expect(r.mcqResults[1].isCorrect, isFalse);
      expect(r.recallReview.single.userAnswer, 'mine');
    });

    test('perfect score', () {
      expect(QuizResultModel.gradeLocally(quiz, {'m1': 2, 'm2': 0}).percentage, 100);
    });
  });

  group('progress and completion payloads', () {
    test('task completion', () {
      final r = TaskCompletionModel.fromJson({
        'xpGained': 10,
        'totalXP': 110,
        'level': 2,
        'streak': {'currentStreak': 3, 'longestStreak': 5},
      });
      expect(r.totalXp, 110);
      expect(r.currentStreak, 3);
      expect(r.longestStreak, 5);
    });

    test('badge and challenge from the backend', () {
      final b = BadgeModel.fromJson({
        'badgeId': '7-day-streak', 'name': 'N', 'description': 'D', 'earned': true, 'awardedAt': '2026-11-20T10:00:00Z',
      });
      expect(b.isEarned, isTrue);
      expect(b.iconRef, 'fire');
      final c = ChallengeModel.fromJson(
        {'challengeId': 'c', 'title': 'T', 'type': 'group', 'targetCompletions': 10, 'progress': 4, 'endDate': '2026-12-05'},
        now: DateTime(2026, 11, 25),
      );
      expect(c.daysLeft, 10);
      expect(c.userProgress, 40);
    });
  });
}
