/// App models. `fromJson` always reads camelCase: the AWS backend already returns camelCase, and
/// dawem-ai's snake_case responses are converted first (see `ai_mappers.dart`).

Map<String, dynamic> _asMap(dynamic value) => Map<String, dynamic>.from(value as Map);

int? _asInt(dynamic value) => (value as num?)?.toInt();

/// One citation: which file, which page, which passage a task or question is based on.
class SourceRefModel {
  const SourceRefModel({
    required this.fileName,
    this.fileId = '',
    this.chunkId = '',
    this.pageNumber,
    this.snippet,
    this.score,
  });

  final String fileId;
  final String chunkId;
  final String fileName;
  final int? pageNumber;
  final String? snippet;
  final double? score;

  /// Short text for the UI, e.g. `Chapter 1.pdf, p. 12`.
  String get label => pageNumber != null ? '$fileName, p. $pageNumber' : fileName;

  factory SourceRefModel.fromJson(Map<String, dynamic> json) => SourceRefModel(
        fileId: json['fileId'] as String? ?? '',
        chunkId: json['chunkId'] as String? ?? '',
        fileName: json['fileName'] as String? ?? '',
        pageNumber: _asInt(json['pageNumber']),
        snippet: json['snippet'] as String?,
        score: (json['score'] as num?)?.toDouble(),
      );

  Map<String, dynamic> toJson() => {
        'fileId': fileId,
        'chunkId': chunkId,
        'fileName': fileName,
        if (pageNumber != null) 'pageNumber': pageNumber,
        if (snippet != null) 'snippet': snippet,
        if (score != null) 'score': score,
      };
}

/// Accepts a list of citation objects, or legacy plain strings.
List<SourceRefModel> parseSources(dynamic raw) {
  if (raw is! List) return const [];
  return raw
      .map((e) => e is Map ? SourceRefModel.fromJson(_asMap(e)) : SourceRefModel(fileName: e.toString()))
      .toList();
}

class UserModel {
  final String userId;
  final String name;
  final String email;
  final String timezone;
  final Map<String, dynamic> notificationPreferences;

  UserModel({
    required this.userId,
    required this.name,
    required this.email,
    this.timezone = 'UTC',
    this.notificationPreferences = const {'reminders': true, 'achievements': true, 'challenges': true},
  });

  factory UserModel.fromJson(Map<String, dynamic> json) => UserModel(
        userId: json['userId'] ?? '',
        name: json['name'] ?? '',
        email: json['email'] ?? '',
        timezone: json['timezone'] ?? 'UTC',
        notificationPreferences: json['notificationPreferences'] ?? {},
      );

  Map<String, dynamic> toJson() => {
        'userId': userId,
        'name': name,
        'email': email,
        'timezone': timezone,
        'notificationPreferences': notificationPreferences,
      };
}

class DailyTaskModel {
  final String taskId;
  final String goalId;
  final int dayNumber;
  final String title;
  final String description;
  final int durationMinutes;
  bool isCompleted;
  final List<SourceRefModel> sources;

  /// `read` | `practice` | `review` | `quiz` for AI tasks.
  final String? kind;

  /// Scheduled day as `YYYY-MM-DD` (null for tasks without a schedule).
  final String? date;

  DailyTaskModel({
    required this.taskId,
    required this.goalId,
    required this.dayNumber,
    required this.title,
    required this.description,
    required this.durationMinutes,
    this.isCompleted = false,
    this.sources = const [],
    this.kind,
    this.date,
  });

  /// Display strings for the citations ("file.pdf, p. 12").
  List<String> get sourceRefs => sources.map((s) => s.label).toList();

  /// Reads both task shapes: a plan task (`durationMinutes`, `sourceRefs` or `sources`) and the
  /// backend's `GET /tasks/today` item (`estimatedMinutes`, `completed`).
  factory DailyTaskModel.fromJson(Map<String, dynamic> json) => DailyTaskModel(
        taskId: json['taskId'] ?? '',
        goalId: json['goalId'] ?? '',
        dayNumber: _asInt(json['dayNumber']) ?? 1,
        title: json['title'] ?? '',
        description: json['description'] ?? '',
        durationMinutes: _asInt(json['durationMinutes'] ?? json['estimatedMinutes']) ?? 20,
        isCompleted: (json['isCompleted'] ?? json['completed'] ?? false) as bool,
        sources: parseSources(json['sourceRefs'] ?? json['sources']),
        kind: json['kind'] as String?,
        date: json['date'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'taskId': taskId,
        'goalId': goalId,
        'dayNumber': dayNumber,
        'title': title,
        'description': description,
        'durationMinutes': durationMinutes,
        'isCompleted': isCompleted,
        'sourceRefs': sources.map((s) => s.toJson()).toList(),
        if (kind != null) 'kind': kind,
        if (date != null) 'date': date,
      };

  /// The same task placed on a goal and a calendar day (AI plans know days, not dates).
  DailyTaskModel scheduled({required String goalId, required DateTime startDate}) => DailyTaskModel(
        taskId: taskId,
        goalId: goalId,
        dayNumber: dayNumber,
        title: title,
        description: description,
        durationMinutes: durationMinutes,
        isCompleted: isCompleted,
        sources: sources,
        kind: kind,
        date: formatDate(DateTime(startDate.year, startDate.month, startDate.day + dayNumber - 1)),
      );
}

/// `YYYY-MM-DD` (the date format used by the backend).
String formatDate(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

/// `beginner` -> `Beginner`.
String capitalizeFirst(String s) => s.isEmpty ? s : '${s[0].toUpperCase()}${s.substring(1)}';

class GoalModel {
  final String goalId;
  final String title;
  final String description;
  final int durationDays;
  final int dailyMinutes;
  final DateTime startDate;
  final String level;
  int completedDays;
  String status;
  final String? attachedPdf;
  List<DailyTaskModel> tasks;

  /// Set once an AI plan has been generated for this goal.
  String? planId;

  /// Indexed learning files the plan is generated from.
  List<String> learningFileIds;

  GoalModel({
    required this.goalId,
    required this.title,
    required this.description,
    required this.durationDays,
    required this.dailyMinutes,
    required this.startDate,
    this.level = 'Beginner',
    this.completedDays = 0,
    this.status = 'IN_PROGRESS',
    this.attachedPdf,
    List<DailyTaskModel>? tasks,
    this.planId,
    List<String>? learningFileIds,
  })  : tasks = tasks ?? <DailyTaskModel>[],
        learningFileIds = learningFileIds ?? <String>[];

  double get progressPercentage {
    if (durationDays == 0) return 0.0;
    return (completedDays / durationDays).clamp(0.0, 1.0);
  }

  /// Days on which every scheduled task is done. Only meaningful for scheduled (dated) tasks.
  int get fullyCompletedDays {
    final byDate = <String, List<DailyTaskModel>>{};
    for (final t in tasks) {
      byDate.putIfAbsent(t.date ?? 'day-${t.dayNumber}', () => []).add(t);
    }
    return byDate.values.where((day) => day.every((t) => t.isCompleted)).length;
  }

  void recomputeCompletedDays() {
    completedDays = fullyCompletedDays.clamp(0, durationDays);
  }

  /// Backend goal item (`GET /goals`, `GET /goals/{id}`). `level` is stored lowercase by the backend.
  factory GoalModel.fromJson(Map<String, dynamic> json) {
    final goal = GoalModel(
      goalId: json['goalId'] ?? '',
      title: json['title'] ?? '',
      description: json['description'] ?? '',
      durationDays: _asInt(json['durationDays']) ?? 30,
      dailyMinutes: _asInt(json['dailyMinutes']) ?? 60,
      startDate: DateTime.tryParse(json['startDate'] ?? '') ?? DateTime.now(),
      level: capitalizeFirst(json['level'] as String? ?? 'beginner'),
      completedDays: _asInt(json['completedDays']) ?? 0,
      status: json['status'] ?? 'IN_PROGRESS',
      attachedPdf: json['attachedPdf'],
      planId: json['planId'] as String?,
      learningFileIds: List<String>.from(json['fileIds'] ?? const []),
      tasks: (json['tasks'] as List<dynamic>?)?.map((e) => DailyTaskModel.fromJson(_asMap(e))).toList(),
    );
    if (json['completedDays'] == null && goal.tasks.isNotEmpty) goal.recomputeCompletedDays();
    return goal;
  }
}

class BadgeModel {
  final String badgeId;
  final String name;
  final String description;
  final String iconRef;
  final bool isEarned;
  final DateTime? awardedAt;

  BadgeModel({
    required this.badgeId,
    required this.name,
    required this.description,
    required this.iconRef,
    this.isEarned = false,
    this.awardedAt,
  });

  static const Map<String, String> _icons = {
    'daily-starter': 'rocket',
    '7-day-streak': 'fire',
  };

  /// Backend badge (`GET /badges`): `earned` / `awardedAt`.
  factory BadgeModel.fromJson(Map<String, dynamic> json) => BadgeModel(
        badgeId: json['badgeId'] ?? '',
        name: json['name'] ?? '',
        description: json['description'] ?? '',
        iconRef: _icons[json['badgeId']] ?? 'trophy',
        isEarned: json['earned'] == true,
        awardedAt: DateTime.tryParse(json['awardedAt'] ?? ''),
      );
}

class ChallengeModel {
  final String challengeId;
  final String title;
  final String goalType;
  final int participantCount;
  final int rewardXp;
  final int daysLeft;
  bool isJoined;
  int userProgress;

  ChallengeModel({
    required this.challengeId,
    required this.title,
    required this.goalType,
    required this.participantCount,
    required this.rewardXp,
    required this.daysLeft,
    this.isJoined = false,
    this.userProgress = 0,
  });

  /// Backend challenge of the caller (`GET /challenges`). The backend has no participant counts or
  /// XP rewards for challenges, so those read 1 / 0; `userProgress` is a percentage of the target.
  factory ChallengeModel.fromJson(Map<String, dynamic> json, {DateTime? now}) {
    final target = _asInt(json['targetCompletions']) ?? 0;
    final progress = _asInt(json['progress']) ?? 0;
    final end = DateTime.tryParse(json['endDate'] ?? '');
    final today = now ?? DateTime.now();
    final left = end == null ? 0 : end.difference(DateTime(today.year, today.month, today.day)).inDays;
    return ChallengeModel(
      challengeId: json['challengeId'] ?? '',
      title: json['title'] ?? '',
      goalType: capitalizeFirst(json['type'] as String? ?? 'individual'),
      participantCount: 1,
      rewardXp: 0,
      daysLeft: left < 0 ? 0 : left,
      isJoined: true,
      userProgress: target == 0 ? 0 : ((progress / target) * 100).round().clamp(0, 100),
    );
  }
}

// ----------------------------------------------------------------------------- AI layer

class IndexResultModel {
  const IndexResultModel({required this.fileId, required this.chunkCount});

  final String fileId;
  final int chunkCount;

  factory IndexResultModel.fromJson(Map<String, dynamic> json) => IndexResultModel(
        fileId: json['fileId'] ?? '',
        chunkCount: _asInt(json['chunkCount']) ?? 0,
      );
}

class LearningFileModel {
  const LearningFileModel({required this.fileId, required this.fileName, this.chunkCount = 0});

  final String fileId;
  final String fileName;
  final int chunkCount;

  factory LearningFileModel.fromJson(Map<String, dynamic> json) => LearningFileModel(
        fileId: json['fileId'] ?? '',
        fileName: json['fileName'] ?? '',
        chunkCount: _asInt(json['chunkCount']) ?? 0,
      );
}

class StudyPlanModel {
  const StudyPlanModel({required this.planId, required this.title, required this.tasks, this.goalId});

  final String planId;
  final String title;
  final String? goalId;
  final List<DailyTaskModel> tasks;

  /// Reads the backend's `POST /plans/generate` response and dawem-ai's `StudyPlan` (after case conversion).
  factory StudyPlanModel.fromJson(Map<String, dynamic> json) => StudyPlanModel(
        planId: json['planId'] ?? '',
        title: json['title'] ?? '',
        goalId: json['goalId'] as String?,
        tasks: ((json['tasks'] as List<dynamic>?) ?? const [])
            .map((e) => DailyTaskModel.fromJson(_asMap(e)))
            .toList(),
      );
}

class McqModel {
  const McqModel({
    required this.questionId,
    required this.question,
    required this.options,
    this.difficulty = 'medium',
    this.correctOptionIndex,
    this.explanation,
    this.sources = const [],
  });

  final String questionId;
  final String question;
  final List<String> options;
  final String difficulty;

  /// Only present when the quiz came straight from dawem-ai (demo mode). The backend withholds it
  /// until the quiz is submitted.
  final int? correctOptionIndex;
  final String? explanation;
  final List<SourceRefModel> sources;

  factory McqModel.fromJson(Map<String, dynamic> json) => McqModel(
        questionId: json['questionId'] ?? '',
        question: json['question'] ?? '',
        options: List<String>.from(json['options'] ?? const []),
        difficulty: json['difficulty'] ?? 'medium',
        correctOptionIndex: _asInt(json['correctOptionIndex']),
        explanation: json['explanation'] as String?,
        sources: parseSources(json['sourceRefs'] ?? json['sources']),
      );
}

class RecallQuestionModel {
  const RecallQuestionModel({
    required this.questionId,
    required this.question,
    this.modelAnswer,
    this.sources = const [],
  });

  final String questionId;
  final String question;
  final String? modelAnswer;
  final List<SourceRefModel> sources;

  factory RecallQuestionModel.fromJson(Map<String, dynamic> json) => RecallQuestionModel(
        questionId: json['questionId'] ?? '',
        question: json['question'] ?? '',
        modelAnswer: json['modelAnswer'] as String?,
        sources: parseSources(json['sourceRefs'] ?? json['sources']),
      );
}

class QuizModel {
  const QuizModel({
    required this.quizId,
    required this.title,
    required this.mcqs,
    this.recallQuestions = const [],
  });

  final String quizId;
  final String title;
  final List<McqModel> mcqs;
  final List<RecallQuestionModel> recallQuestions;

  /// True when answers can be graded on the device (demo mode only).
  bool get hasAnswerKey => mcqs.isNotEmpty && mcqs.every((q) => q.correctOptionIndex != null);

  factory QuizModel.fromJson(Map<String, dynamic> json) => QuizModel(
        quizId: json['quizId'] ?? '',
        title: json['title'] ?? '',
        mcqs: ((json['mcqs'] as List<dynamic>?) ?? const []).map((e) => McqModel.fromJson(_asMap(e))).toList(),
        recallQuestions: ((json['recallQuestions'] as List<dynamic>?) ?? const [])
            .map((e) => RecallQuestionModel.fromJson(_asMap(e)))
            .toList(),
      );
}

class McqResultModel {
  const McqResultModel({
    required this.questionId,
    required this.selectedOptionIndex,
    required this.correctOptionIndex,
    required this.isCorrect,
    required this.explanation,
  });

  final String questionId;
  final int? selectedOptionIndex;
  final int correctOptionIndex;
  final bool isCorrect;
  final String explanation;

  factory McqResultModel.fromJson(Map<String, dynamic> json) => McqResultModel(
        questionId: json['questionId'] ?? '',
        selectedOptionIndex: _asInt(json['selectedOptionIndex']),
        correctOptionIndex: _asInt(json['correctOptionIndex']) ?? 0,
        isCorrect: json['isCorrect'] == true,
        explanation: json['explanation'] ?? '',
      );
}

class RecallReviewModel {
  const RecallReviewModel({required this.questionId, required this.modelAnswer, this.userAnswer});

  final String questionId;
  final String modelAnswer;
  final String? userAnswer;

  factory RecallReviewModel.fromJson(Map<String, dynamic> json) => RecallReviewModel(
        questionId: json['questionId'] ?? '',
        modelAnswer: json['modelAnswer'] ?? '',
        userAnswer: json['userAnswer'] as String?,
      );
}

class QuizResultModel {
  const QuizResultModel({
    required this.quizId,
    required this.score,
    required this.total,
    required this.percentage,
    required this.mcqResults,
    this.resultId = '',
    this.recallReview = const [],
  });

  final String quizId;
  final String resultId;
  final int score;
  final int total;
  final int percentage;
  final List<McqResultModel> mcqResults;
  final List<RecallReviewModel> recallReview;

  factory QuizResultModel.fromJson(Map<String, dynamic> json) => QuizResultModel(
        quizId: json['quizId'] ?? '',
        resultId: json['resultId'] ?? '',
        score: _asInt(json['score']) ?? 0,
        total: _asInt(json['total']) ?? 0,
        percentage: _asInt(json['percentage']) ?? 0,
        mcqResults: ((json['mcqResults'] as List<dynamic>?) ?? const [])
            .map((e) => McqResultModel.fromJson(_asMap(e)))
            .toList(),
        recallReview: ((json['recallReview'] as List<dynamic>?) ?? const [])
            .map((e) => RecallReviewModel.fromJson(_asMap(e)))
            .toList(),
      );

  /// Same rules as the backend (`gradeQuiz`): unanswered counts as wrong, percentage over all questions.
  /// Only for quizzes that carry their answer key (demo mode).
  factory QuizResultModel.gradeLocally(
    QuizModel quiz,
    Map<String, int> answers, {
    Map<String, String> recallAnswers = const {},
  }) {
    final results = quiz.mcqs.map((q) {
      final selected = answers[q.questionId];
      final correct = q.correctOptionIndex ?? -1;
      return McqResultModel(
        questionId: q.questionId,
        selectedOptionIndex: selected,
        correctOptionIndex: correct,
        isCorrect: selected != null && selected == correct,
        explanation: q.explanation ?? '',
      );
    }).toList();
    final score = results.where((r) => r.isCorrect).length;
    final total = results.length;
    return QuizResultModel(
      quizId: quiz.quizId,
      score: score,
      total: total,
      percentage: total == 0 ? 0 : ((score / total) * 100).round(),
      mcqResults: results,
      recallReview: quiz.recallQuestions
          .map((q) => RecallReviewModel(
                questionId: q.questionId,
                modelAnswer: q.modelAnswer ?? '',
                userAnswer: recallAnswers[q.questionId],
              ))
          .toList(),
    );
  }
}

// ----------------------------------------------------------------------------- progress

class TaskCompletionModel {
  const TaskCompletionModel({
    required this.xpGained,
    required this.totalXp,
    required this.level,
    required this.currentStreak,
    required this.longestStreak,
  });

  final int xpGained;
  final int totalXp;
  final int level;
  final int currentStreak;
  final int longestStreak;

  /// `POST /tasks/{id}/complete` response.
  factory TaskCompletionModel.fromJson(Map<String, dynamic> json) {
    final streak = json['streak'] is Map ? _asMap(json['streak']) : const <String, dynamic>{};
    return TaskCompletionModel(
      xpGained: _asInt(json['xpGained']) ?? 0,
      totalXp: _asInt(json['totalXP']) ?? 0,
      level: _asInt(json['level']) ?? 1,
      currentStreak: _asInt(streak['currentStreak']) ?? 0,
      longestStreak: _asInt(streak['longestStreak']) ?? 0,
    );
  }
}

class ProgressModel {
  const ProgressModel({
    this.currentStreak = 0,
    this.longestStreak = 0,
    this.totalXp = 0,
    this.level = 1,
  });

  final int currentStreak;
  final int longestStreak;
  final int totalXp;
  final int level;

  /// `GET /streak` and `GET /xp` responses combined.
  factory ProgressModel.fromJson(Map<String, dynamic> streak, Map<String, dynamic> xp) => ProgressModel(
        currentStreak: _asInt(streak['currentStreak']) ?? 0,
        longestStreak: _asInt(streak['longestStreak']) ?? 0,
        totalXp: _asInt(xp['totalXP']) ?? 0,
        level: _asInt(xp['level']) ?? 1,
      );
}
