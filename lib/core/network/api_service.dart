import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../config/api_config.dart';
import 'ai_mappers.dart';
import 'models.dart';

// Screens import the models through this file.
export 'models.dart';

/// A failed API call. [statusCode] is 0 for network failures/timeouts and -1 for local
/// configuration problems; otherwise it is the HTTP status. [code] is the machine-readable code
/// of the AWS backend (`{error, code}`) or of dawem-ai (`{detail, code}`), when there is one.
class ApiException implements Exception {
  const ApiException({required this.statusCode, required this.message, this.code});

  final int statusCode;
  final String message;
  final String? code;

  bool get isNetworkError => statusCode == 0;

  @override
  String toString() => message;
}

/// All network access of the app.
///
/// * [ApiMode.backend]: every call goes to the AWS API Gateway (camelCase JSON, Cognito bearer token).
///   Files, plans and quizzes reach dawem-ai through the backend's proxy Lambdas.
/// * [ApiMode.demo]: indexing, plan and quiz calls go straight to dawem-ai (snake_case JSON, converted
///   by [AiMappers]); goals, tasks, XP and streak live in memory for the session because there is no
///   backend to keep them.
class ApiService {
  ApiService({
    ApiConfig? config,
    http.Client? client,
    Future<String?> Function()? authTokenProvider,
  })  : config = config ?? ApiConfig.fromEnvironment(),
        _client = client ?? http.Client(),
        _authTokenProvider = authTokenProvider;

  final ApiConfig config;
  final http.Client _client;

  /// Supplies the Cognito ID token. Falls back to `DEV_AUTH_TOKEN` from the build configuration.
  final Future<String?> Function()? _authTokenProvider;

  bool get _demo => config.isDemo;

  // ------------------------------------------------------------------ transport

  Uri _backendUri(String path, [Map<String, String>? query]) {
    final base = config.backendBaseUrl.trim().replaceAll(RegExp(r'/+$'), '');
    if (base.isEmpty) {
      throw const ApiException(
        statusCode: -1,
        code: 'not_configured',
        message: 'API_BASE_URL is not set. Pass --dart-define=API_BASE_URL=<ApiUrl> or use API_MODE=demo.',
      );
    }
    final uri = Uri.parse('$base$path');
    return (query == null || query.isEmpty) ? uri : uri.replace(queryParameters: query);
  }

  Uri _aiUri(String path, [Map<String, String>? query]) {
    final base = config.aiBaseUrl.trim().replaceAll(RegExp(r'/+$'), '');
    final uri = Uri.parse('$base$path');
    return (query == null || query.isEmpty) ? uri : uri.replace(queryParameters: query);
  }

  static String _seg(String id) => Uri.encodeComponent(id);

  Future<Map<String, String>> _headers({required bool json, required bool toAi}) async {
    final headers = <String, String>{'accept': 'application/json'};
    if (json) headers['content-type'] = 'application/json';
    if (toAi) {
      if (config.aiApiKey.isNotEmpty) headers['x-api-key'] = config.aiApiKey;
    } else {
      final fromProvider = await _authTokenProvider?.call();
      final token = (fromProvider != null && fromProvider.isNotEmpty) ? fromProvider : config.devAuthToken;
      if (token.isNotEmpty) headers['authorization'] = 'Bearer $token';
    }
    return headers;
  }

  /// Sends a JSON request and returns the decoded JSON body (null for an empty body such as 204).
  Future<dynamic> _json(
    String method,
    Uri uri, {
    Object? body,
    required bool toAi,
    Duration? timeout,
  }) async {
    final request = http.Request(method, uri);
    request.headers.addAll(await _headers(json: body != null, toAi: toAi));
    if (body != null) request.body = jsonEncode(body);
    return _execute(request, timeout ?? config.timeout);
  }

  Future<dynamic> _execute(http.BaseRequest request, Duration timeout) async {
    final http.Response response;
    try {
      final streamed = await _client.send(request).timeout(timeout);
      response = await http.Response.fromStream(streamed).timeout(timeout);
    } on TimeoutException {
      throw const ApiException(statusCode: 0, code: 'timeout', message: 'The server took too long to respond.');
    } on http.ClientException catch (e) {
      throw ApiException(statusCode: 0, code: 'network', message: 'Could not reach the server: ${e.message}');
    }
    return _decode(response);
  }

  dynamic _decode(http.Response response) {
    final ok = response.statusCode >= 200 && response.statusCode < 300;
    if (ok) {
      if (response.body.trim().isEmpty) return null;
      try {
        return jsonDecode(utf8.decode(response.bodyBytes));
      } on FormatException {
        throw ApiException(
          statusCode: response.statusCode,
          code: 'bad_response',
          message: 'The server returned an unreadable response.',
        );
      }
    }

    String? code;
    var message = 'Request failed (HTTP ${response.statusCode}).';
    try {
      final body = jsonDecode(utf8.decode(response.bodyBytes));
      if (body is Map) {
        if (body['code'] is String) code = body['code'] as String;
        final text = body['error'] ?? body['detail']; // backend: error, dawem-ai: detail
        if (text is String && text.isNotEmpty) {
          message = text;
        } else if (text is List && text.isNotEmpty) {
          message = 'The request was rejected as invalid.';
        }
      }
    } catch (_) {
      // Non-JSON error body: keep the generic message.
    }
    throw ApiException(statusCode: response.statusCode, code: code, message: message);
  }

  static Map<String, dynamic> _map(dynamic value) {
    if (value is Map) return Map<String, dynamic>.from(value);
    throw const ApiException(statusCode: 502, code: 'bad_response', message: 'The server returned an unexpected response.');
  }

  static List<dynamic> _list(dynamic value, String key) {
    final list = _map(value)[key];
    return list is List ? list : const [];
  }

  // ------------------------------------------------------------------ in-memory demo store

  final List<GoalModel> _demoGoals = [];
  final List<LearningFileModel> _demoFiles = [];
  final List<ChallengeModel> _demoChallenges = [
    ChallengeModel(
      challengeId: 'c1',
      title: 'Morning Reading Club',
      rewardXp: 300,
      goalType: 'Reading',
      participantCount: 15,
      daysLeft: 10,
    ),
    ChallengeModel(
      challengeId: 'c2',
      title: '30-Day Coding Sprint',
      rewardXp: 500,
      goalType: 'Coding',
      participantCount: 32,
      daysLeft: 25,
    ),
  ];
  int _demoXp = 0;
  int _demoStreak = 0;
  int _demoLongestStreak = 0;
  String? _demoLastCompletedDate;

  /// Same curve as the backend: total XP for level n is 100 * n * (n - 1) / 2.
  static int _levelForXp(int xp) {
    var level = 1;
    while (xp >= 100 * (level + 1) * level ~/ 2) {
      level++;
    }
    return level;
  }

  // ------------------------------------------------------------------ profile & progress

  /// Creates/updates the caller's profile. The backend needs it (timezone decides what "today" is)
  /// before it serves tasks, streak or challenges.
  Future<void> ensureProfile({String? displayName}) async {
    if (_demo) return;
    final name = displayName?.trim() ?? '';
    await _json(
      'PUT',
      _backendUri('/me'),
      body: {'timezone': config.timezone, if (name.isNotEmpty) 'displayName': name},
      toAi: false,
    );
  }

  Future<ProgressModel> getProgress() async {
    if (_demo) {
      return ProgressModel(
        currentStreak: _demoStreak,
        longestStreak: _demoLongestStreak,
        totalXp: _demoXp,
        level: _levelForXp(_demoXp),
      );
    }
    final results = await Future.wait([
      _json('GET', _backendUri('/streak'), toAi: false),
      _json('GET', _backendUri('/xp'), toAi: false),
    ]);
    return ProgressModel.fromJson(_map(results[0]), _map(results[1]));
  }

  // ------------------------------------------------------------------ goals & tasks

  Future<List<GoalModel>> getGoals() async {
    if (_demo) return List<GoalModel>.of(_demoGoals);
    final json = await _json('GET', _backendUri('/goals'), toAi: false);
    return _list(json, 'goals').map((e) => GoalModel.fromJson(_map(e))).toList();
  }

  Future<GoalModel> createGoal({
    required String title,
    required int durationDays,
    required int dailyMinutes,
    DateTime? startDate,
    String level = 'Beginner',
    String? description,
    String? learningFile,
  }) async {
    final now = startDate ?? DateTime.now();
    final start = DateTime(now.year, now.month, now.day);
    final userDescription = description?.trim() ?? '';
    final shownDescription = userDescription.isNotEmpty ? userDescription : 'Personalized study plan for $title.';

    if (_demo) {
      final goal = GoalModel(
        goalId: 'g-${DateTime.now().microsecondsSinceEpoch}',
        title: title,
        description: shownDescription,
        durationDays: durationDays,
        dailyMinutes: dailyMinutes,
        startDate: start,
        level: level,
        status: 'PLAN_PENDING',
        attachedPdf: learningFile,
      );
      _demoGoals.insert(0, goal);
      return goal;
    }

    final json = _map(await _json(
      'POST',
      _backendUri('/goals'),
      body: {
        'title': title,
        if (userDescription.isNotEmpty) 'description': userDescription,
        'durationDays': durationDays,
        'dailyMinutes': dailyMinutes,
        'startDate': formatDate(start),
        'level': level.toLowerCase(),
      },
      toAi: false,
    ));
    return GoalModel(
      goalId: json['goalId'] as String? ?? '',
      title: title,
      description: shownDescription,
      durationDays: durationDays,
      dailyMinutes: dailyMinutes,
      startDate: start,
      level: level,
      status: json['status'] as String? ?? 'PLAN_PENDING',
      attachedPdf: learningFile,
    );
  }

  /// Tasks scheduled for today (backend only; demo mode derives them from the in-memory plans).
  Future<List<DailyTaskModel>> getTodayTasks() async {
    if (_demo) {
      final today = formatDate(DateTime.now());
      return [
        for (final goal in _demoGoals)
          for (final task in goal.tasks)
            if (task.date == today) task,
      ];
    }
    final json = await _json('GET', _backendUri('/tasks/today'), toAi: false);
    return _list(json, 'tasks').map((e) => DailyTaskModel.fromJson(_map(e))).toList();
  }

  /// Marks a task done. The backend is authoritative for XP and streak (it rejects future tasks with
  /// 409 FUTURE_TASK and is idempotent per task).
  Future<TaskCompletionModel> completeTask({required String taskId, required String goalId}) async {
    if (_demo) {
      GoalModel? goal;
      for (final g in _demoGoals) {
        if (g.goalId == goalId) goal = g;
      }
      DailyTaskModel? task;
      for (final t in goal?.tasks ?? const <DailyTaskModel>[]) {
        if (t.taskId == taskId) task = t;
      }
      if (task == null) {
        throw const ApiException(statusCode: 404, code: 'not_found', message: 'Task not found');
      }
      var gained = 0;
      if (!task.isCompleted) {
        task.isCompleted = true;
        gained = 10;
        _demoXp += gained;
        final today = formatDate(DateTime.now());
        if (_demoLastCompletedDate != today) {
          _demoStreak += 1;
          _demoLastCompletedDate = today;
          if (_demoStreak > _demoLongestStreak) _demoLongestStreak = _demoStreak;
        }
      }
      return TaskCompletionModel(
        xpGained: gained,
        totalXp: _demoXp,
        level: _levelForXp(_demoXp),
        currentStreak: _demoStreak,
        longestStreak: _demoLongestStreak,
      );
    }
    final json = await _json(
      'POST',
      _backendUri('/tasks/${_seg(taskId)}/complete', {'goalId': goalId}),
      toAi: false,
    );
    return TaskCompletionModel.fromJson(_map(json));
  }

  // ------------------------------------------------------------------ learning files (RAG)

  Future<IndexResultModel> indexNotes({
    required String fileId,
    required String fileName,
    required String text,
  }) async {
    if (_demo) {
      final json = await _json(
        'POST',
        _aiUri('/files/index-notes'),
        body: AiMappers.indexNotesRequest(userId: config.demoUserId, fileId: fileId, fileName: fileName, text: text),
        toAi: true,
        timeout: config.aiTimeout,
      );
      return _rememberFile(AiMappers.indexResult(_map(json)), fileName);
    }
    final json = await _json(
      'POST',
      _backendUri('/files/index-notes'),
      body: {'fileId': fileId, 'fileName': fileName, 'text': text},
      toAi: false,
      timeout: config.aiTimeout,
    );
    return IndexResultModel.fromJson(_map(json));
  }

  /// Uploads a PDF / TXT / MD file for indexing (multipart; `file` is the file part).
  Future<IndexResultModel> indexFile({
    required String fileId,
    required String fileName,
    required Uint8List bytes,
  }) async {
    final request = http.MultipartRequest('POST', _demo ? _aiUri('/files/index') : _backendUri('/files/index'));
    request.headers.addAll(await _headers(json: false, toAi: _demo));
    // The backend takes user_id from the verified JWT, so only demo mode sends it.
    request.fields.addAll(
      _demo
          ? AiMappers.indexFileFields(userId: config.demoUserId, fileId: fileId, fileName: fileName)
          : {'file_id': fileId, 'file_name': fileName},
    );
    request.files.add(http.MultipartFile.fromBytes('file', bytes, filename: fileName));

    final json = _map(await _execute(request, config.aiTimeout));
    if (_demo) return _rememberFile(AiMappers.indexResult(json), fileName);
    return IndexResultModel.fromJson(json);
  }

  /// Indexes an object the app already uploaded to the shared bucket under `uploads/<userId>/`
  /// (backend mode only: the bucket is server-side configuration).
  Future<IndexResultModel> indexS3({required String fileId, required String s3Key, String? fileName}) async {
    if (_demo) {
      throw const ApiException(
        statusCode: -1,
        code: 'not_supported',
        message: 'S3 indexing needs the AWS backend. Use a file upload or notes in demo mode.',
      );
    }
    final json = await _json(
      'POST',
      _backendUri('/files/index-s3'),
      body: {'fileId': fileId, 's3Key': s3Key, if (fileName != null) 'fileName': fileName},
      toAi: false,
      timeout: config.aiTimeout,
    );
    return IndexResultModel.fromJson(_map(json));
  }

  Future<List<LearningFileModel>> listFiles() async {
    if (_demo) return List<LearningFileModel>.of(_demoFiles);
    final json = await _json('GET', _backendUri('/files'), toAi: false);
    return _list(json, 'files').map((e) => LearningFileModel.fromJson(_map(e))).toList();
  }

  Future<void> deleteFile(String fileId) async {
    if (_demo) {
      await _json('DELETE', _aiUri('/files/${_seg(fileId)}', {'user_id': config.demoUserId}), toAi: true);
      _demoFiles.removeWhere((f) => f.fileId == fileId);
      return;
    }
    await _json('DELETE', _backendUri('/files/${_seg(fileId)}'), toAi: false);
  }

  IndexResultModel _rememberFile(IndexResultModel result, String fileName) {
    _demoFiles.removeWhere((f) => f.fileId == result.fileId);
    _demoFiles.insert(0, LearningFileModel(fileId: result.fileId, fileName: fileName, chunkCount: result.chunkCount));
    return result;
  }

  // ------------------------------------------------------------------ AI plan

  /// Generates a grounded study plan for [goal] from the indexed [fileIds].
  ///
  /// Backend mode: the backend also stores the tasks and sets the goal to PLAN_READY.
  /// Demo mode: dawem-ai returns day numbers; tasks are scheduled from the goal's start date here.
  Future<StudyPlanModel> generatePlan({
    required GoalModel goal,
    required List<String> fileIds,
    String? context,
  }) async {
    final note = context?.trim() ?? '';
    if (_demo) {
      final json = await _json(
        'POST',
        _aiUri('/plans/generate'),
        body: AiMappers.studyPlanRequest(
          userId: config.demoUserId,
          fileIds: fileIds,
          goal: goal.title,
          durationDays: goal.durationDays,
          // dawem-ai needs at least 10 minutes a day to fit a task.
          dailyMinutes: goal.dailyMinutes < 10 ? 10 : goal.dailyMinutes,
          level: goal.level.toLowerCase(),
          context: note,
        ),
        toAi: true,
        timeout: config.aiTimeout,
      );
      final plan = AiMappers.studyPlan(_map(json));
      return StudyPlanModel(
        planId: plan.planId,
        title: plan.title,
        goalId: goal.goalId,
        tasks: plan.tasks.map((t) => t.scheduled(goalId: goal.goalId, startDate: goal.startDate)).toList(),
      );
    }
    final json = await _json(
      'POST',
      _backendUri('/plans/generate'),
      body: {'goalId': goal.goalId, 'fileIds': fileIds, if (note.isNotEmpty) 'context': note},
      toAi: false,
      timeout: config.aiTimeout,
    );
    return StudyPlanModel.fromJson(_map(json));
  }

  // ------------------------------------------------------------------ AI quiz

  Future<QuizModel> generateQuiz({
    required List<String> fileIds,
    String? topic,
    int? numMcqs,
    int? numRecall,
  }) async {
    if (_demo) {
      final json = await _json(
        'POST',
        _aiUri('/quizzes/generate'),
        body: AiMappers.quizRequest(
          userId: config.demoUserId,
          fileIds: fileIds,
          topic: topic,
          numMcqs: numMcqs,
          numRecall: numRecall,
        ),
        toAi: true,
        timeout: config.aiTimeout,
      );
      return AiMappers.quiz(_map(json));
    }
    final cleanTopic = topic?.trim() ?? '';
    final json = await _json(
      'POST',
      _backendUri('/quizzes/generate'),
      body: {
        'fileIds': fileIds,
        if (cleanTopic.isNotEmpty) 'topic': cleanTopic,
        if (numMcqs != null) 'numMcqs': numMcqs,
        if (numRecall != null) 'numRecall': numRecall,
      },
      toAi: false,
      timeout: config.aiTimeout,
    );
    return QuizModel.fromJson(_map(json));
  }

  /// Grades a quiz. Backend: server-side (it holds the answer key and stores the attempt).
  /// Demo: on the device, because dawem-ai returns the key with the quiz.
  Future<QuizResultModel> submitQuiz(
    QuizModel quiz,
    Map<String, int> answers, {
    Map<String, String> recallAnswers = const {},
  }) async {
    if (_demo) {
      if (!quiz.hasAnswerKey) {
        throw const ApiException(statusCode: -1, code: 'no_answer_key', message: 'This quiz cannot be graded on the device.');
      }
      return QuizResultModel.gradeLocally(quiz, answers, recallAnswers: recallAnswers);
    }
    final json = await _json(
      'POST',
      _backendUri('/quizzes/${_seg(quiz.quizId)}/submit'),
      body: {
        'answers': [
          for (final e in answers.entries) {'questionId': e.key, 'selectedOptionIndex': e.value},
        ],
        if (recallAnswers.isNotEmpty)
          'recallAnswers': [
            for (final e in recallAnswers.entries) {'questionId': e.key, 'answer': e.value},
          ],
      },
      toAi: false,
    );
    return QuizResultModel.fromJson(_map(json));
  }

  // ------------------------------------------------------------------ challenges & badges

  Future<List<ChallengeModel>> getChallenges() async {
    if (_demo) return List<ChallengeModel>.of(_demoChallenges);
    final json = await _json('GET', _backendUri('/challenges'), toAi: false);
    return _list(json, 'challenges').map((e) => ChallengeModel.fromJson(_map(e))).toList();
  }

  /// Creates a challenge the caller joins automatically. [targetCompletions] is the number of tasks
  /// that finish it; the backend counts completions between [startDate] and [endDate].
  Future<ChallengeModel> createChallenge({
    required String title,
    required String goalType,
    required int rewardXp,
    required int daysLeft,
    String type = 'group',
  }) async {
    if (_demo) {
      final challenge = ChallengeModel(
        challengeId: 'c-${DateTime.now().microsecondsSinceEpoch}',
        title: title,
        goalType: goalType,
        participantCount: 1,
        rewardXp: rewardXp,
        daysLeft: daysLeft,
      );
      _demoChallenges.insert(0, challenge);
      return challenge;
    }
    final today = DateTime.now();
    final start = DateTime(today.year, today.month, today.day);
    final days = daysLeft < 1 ? 1 : daysLeft;
    final end = DateTime(start.year, start.month, start.day + days - 1);
    final json = _map(await _json(
      'POST',
      _backendUri('/challenges'),
      body: {
        'title': title,
        'type': type,
        'targetCompletions': days,
        'startDate': formatDate(start),
        'endDate': formatDate(end),
      },
      toAi: false,
    ));
    return ChallengeModel.fromJson({...json, 'progress': 0});
  }

  Future<void> joinChallenge(String challengeId) async {
    if (_demo) {
      for (final c in _demoChallenges) {
        if (c.challengeId == challengeId) {
          c.isJoined = true;
        }
      }
      return;
    }
    await _json('POST', _backendUri('/challenges/${_seg(challengeId)}/join'), toAi: false);
  }

  Future<List<BadgeModel>> getBadges() async {
    if (_demo) {
      return [
        BadgeModel(
          badgeId: 'daily-starter',
          name: 'Daily Starter',
          description: 'Complete your first task.',
          iconRef: 'rocket',
          isEarned: _demoLastCompletedDate != null,
        ),
        BadgeModel(
          badgeId: '7-day-streak',
          name: '7-Day Streak',
          description: 'Reach a 7-day streak.',
          iconRef: 'fire',
          isEarned: _demoLongestStreak >= 7,
        ),
      ];
    }
    final json = await _json('GET', _backendUri('/badges'), toAi: false);
    return _list(json, 'badges').map((e) => BadgeModel.fromJson(_map(e))).toList();
  }
}
