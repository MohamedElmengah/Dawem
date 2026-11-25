import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../core/network/api_service.dart';
import 'package:audioplayers/audioplayers.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Central Reactive State Provider managing authentication, gamification, goals, tasks, and streaks.
class AppState extends ChangeNotifier {
  final ApiService _apiService;

  /// [apiService] is injectable for tests; the app uses the build-time configuration
  /// (`API_MODE`, `API_BASE_URL`, `AI_BASE_URL`, see `lib/core/config/api_config.dart`).
  AppState({ApiService? apiService}) : _apiService = apiService ?? ApiService() {
    _initializeData();
  }

  /// Last API error message (cleared by the next successful sync).
  String? _lastError;
  String? get lastError => _lastError;
  
  // ==========================================
  // 🔊 إعدادات الصوتيات والاهتزاز واللغة
  // ==========================================
  bool isSoundEnabled = true;
  bool isHapticEnabled = true;
  String _currentLanguage = 'English'; // القيمة الافتراضية
  
  String get currentLanguage => _currentLanguage;

  final AudioPlayer _audioPlayer = AudioPlayer();

  // دالة تشغيل صوت النجاح
  Future<void> playSuccessSound() async {
    if (isSoundEnabled) {
      try {
        await _audioPlayer.play(AssetSource('sounds/success.mp3'));
      } catch (e) {
        print("Error playing sound: $e");
      }
    }
  }

  // دالة تشغيل الاهتزاز اللمسي
  void triggerHaptic() {
    if (isHapticEnabled) {
      HapticFeedback.lightImpact();
    }
  }

  // التبديل بين تشغيل/إيقاف الأصوات
  void toggleSoundEffects(bool value) {
    isSoundEnabled = value;
    notifyListeners();
  }

  // التبديل بين تشغيل/إيقاف الاهتزاز
  void toggleHapticFeedback(bool value) {
    isHapticEnabled = value;
    notifyListeners();
  }

  // تحديث لغة التطبيق
  void setLanguage(String lang) {
    _currentLanguage = lang;
    notifyListeners();
  }
  // ==========================================

  /// مسح تحدي بواسطة الـ ID
  void deleteChallenge(String challengeId) {
    _challenges.removeWhere((c) => c.challengeId == challengeId);
    notifyListeners();
  }

  UserModel _user = UserModel(
    userId: '',
    name: '',
    email: '',
  );

  bool _isLoggedIn = false;
  ThemeMode _themeMode = ThemeMode.dark; 
  int _currentStreak = 0; // يبدأ من الصفر
  int _longestStreak = 0;
  int _totalXP = 0; // يبدأ من الصفر
  bool _isLoading = false;

  List<GoalModel> _goals = []; // قائمة أهداف فارغة تماماً
  GoalModel? _activeGoal;
  
  // Challenges come from the API (own challenges in backend mode, sample challenges in demo mode).
  List<ChallengeModel> _challenges = [];

  List<BadgeModel> _badges = [];

  // متغيرات خاصة بالبحث والفلترة في صفحة التحديات
  String _challengeSearchQuery = '';
  String _selectedChallengeFilter = 'All';

  // Getters
  UserModel get user => _user;
  bool get isLoggedIn => _isLoggedIn;
  ThemeMode get themeMode => _themeMode;
  bool get isDarkMode => _themeMode == ThemeMode.dark;
  int get currentStreak => _currentStreak;
  int get longestStreak => _longestStreak;
  int get totalXP => _totalXP;
  bool get isLoading => _isLoading;
  List<GoalModel> get goals => _goals;
  GoalModel? get activeGoal => _activeGoal;
  List<BadgeModel> get badges => _badges;
  List<DailyTaskModel> get todayTasks => activeGoal?.tasks ?? [];

  // Getters للبحث والفلترة
  String get challengeSearchQuery => _challengeSearchQuery;
  String get selectedChallengeFilter => _selectedChallengeFilter;

  // قائمة التحديات المفلترة والمعربة ديناميكياً بناءً على اللغة المختارة
  List<ChallengeModel> get challenges {
    final isArabic = _currentLanguage == 'Arabic';

    return _challenges.where((c) {
      final matchesSearch = c.title.toLowerCase().contains(_challengeSearchQuery.toLowerCase()) ||
          (isArabic && _getTranslatedTitle(c.title).toLowerCase().contains(_challengeSearchQuery.toLowerCase()));
      
      final matchesFilter = _selectedChallengeFilter == 'All' || 
          c.goalType.toLowerCase() == _selectedChallengeFilter.toLowerCase() ||
          (isArabic && _translateCategoryToEnglish(_selectedChallengeFilter).toLowerCase() == c.goalType.toLowerCase());
      
      return matchesSearch && matchesFilter;
    }).map((c) {
      if (isArabic) {
        return ChallengeModel(
          challengeId: c.challengeId,
          title: _getTranslatedTitle(c.title),
          rewardXp: c.rewardXp,
          isJoined: c.isJoined,
          goalType: _getTranslatedCategory(c.goalType),
          participantCount: c.participantCount,
          daysLeft: c.daysLeft,
        );
      }
      return c;
    }).toList();
  }

  // دوال مساعدة لترجمة العناوين والتصنيفات للعربية
  String _getTranslatedTitle(String englishTitle) {
    if (englishTitle == 'Morning Reading Club') return 'نادي القراءة الصباحي';
    if (englishTitle == '30-Day Coding Sprint') return 'سباق البرمجة لـ 30 يوماً';
    return englishTitle;
  }

  String _getTranslatedCategory(String englishCategory) {
    if (englishCategory.toLowerCase() == 'reading') return 'قراءة';
    if (englishCategory.toLowerCase() == 'coding') return 'برمجة';
    if (englishCategory.toLowerCase() == 'general') return 'عام';
    return englishCategory;
  }

  String _translateCategoryToEnglish(String arabicCategory) {
    if (arabicCategory == 'قراءة') return 'Reading';
    if (arabicCategory == 'برمجة') return 'Coding';
    if (arabicCategory == 'عام') return 'General';
    if (arabicCategory == 'الكل') return 'All';
    return arabicCategory;
  }

  // تحديث نص البحث
  void setChallengeSearchQuery(String query) {
    _challengeSearchQuery = query;
    notifyListeners();
  }

  // تحديث التصنيف المختار
  void setChallengeFilter(String filter) {
    _selectedChallengeFilter = filter;
    notifyListeners();
  }

  void toggleTheme() {
    _themeMode = _themeMode == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark;
    notifyListeners();
  }

  void setThemeMode(ThemeMode mode) {
    _themeMode = mode;
    notifyListeners();
  }

  Future<void> _initializeData() async {
    _setLoading(true);
    _goals = [];
    _activeGoal = null;

    // استرجاع حالة تسجيل الدخول المخزنة محلياً عند بدء التطبيق
    final prefs = await SharedPreferences.getInstance();
    _isLoggedIn = prefs.getBool('isLoggedIn') ?? false;
    if (_isLoggedIn) {
      final storedEmail = prefs.getString('userEmail') ?? '';
      final storedName = prefs.getString('userName') ?? (storedEmail.contains('@') ? storedEmail.split('@').first.capitalize() : 'User');
      _user = UserModel(
        userId: prefs.getString('userId') ?? 'u-${DateTime.now().millisecondsSinceEpoch}',
        name: storedName,
        email: storedEmail,
      );
    }

    if (_isLoggedIn) await _syncFromApi();
    _setLoading(false);
  }

  /// Reloads goals, today's tasks, streak/XP, badges and challenges. Each step is independent, so one
  /// failing endpoint does not blank the rest of the screen; the first error is kept in [lastError].
  Future<void> refresh() async {
    await _syncFromApi();
    notifyListeners();
  }

  Future<void> _syncFromApi() async {
    String? firstError;
    Future<void> step(Future<void> Function() run) async {
      try {
        await run();
      } on ApiException catch (e) {
        firstError ??= e.message;
      }
    }

    await step(() => _apiService.ensureProfile(displayName: _user.name));
    await step(() async {
      final goals = await _apiService.getGoals();
      final today = await _apiService.getTodayTasks();
      for (final task in today) {
        for (final goal in goals) {
          if (goal.goalId == task.goalId && !goal.tasks.any((t) => t.taskId == task.taskId)) {
            goal.tasks.add(task);
          }
        }
      }
      for (final goal in goals) {
        if (goal.tasks.isNotEmpty) goal.recomputeCompletedDays();
      }
      _goals = goals;
      final activeId = _activeGoal?.goalId;
      _activeGoal = goals.isEmpty
          ? null
          : goals.firstWhere((g) => g.goalId == activeId, orElse: () => goals.first);
    });
    await step(() async {
      final progress = await _apiService.getProgress();
      _currentStreak = progress.currentStreak;
      _longestStreak = progress.longestStreak;
      _totalXP = progress.totalXp;
    });
    await step(() async => _badges = await _apiService.getBadges());
    await step(() async => _challenges = await _apiService.getChallenges());
    _lastError = firstError;
  }

  void _setLoading(bool value) {
    _isLoading = value;
    notifyListeners();
  }

  /// Sign In with Email & Password (مع التحقق من وجود الحساب مسجلاً مسبقاً)
  Future<bool> login(String email, String password) async {
    _setLoading(true);
    await Future.delayed(const Duration(milliseconds: 700)); 

    final prefs = await SharedPreferences.getInstance();
    final registeredEmail = prefs.getString('registeredEmail');

    // إذا لم يكن الإيميل مسجلاً من قبل، نمنع الدخول ونظهر خطأ
    if (registeredEmail == null || registeredEmail != email) {
      _setLoading(false);
      throw Exception('Account not found. Please sign up first!');
    }

    final userId = prefs.getString('userId') ?? 'u-${DateTime.now().millisecondsSinceEpoch}';
    final userName = prefs.getString('userName') ?? email.split('@').first.capitalize();

    _user = UserModel(
      userId: userId,
      name: userName,
      email: email,
    );
    _isLoggedIn = true;

    // حفظ حالة الدخول الحالية
    await prefs.setBool('isLoggedIn', true);
    await prefs.setString('userEmail', email);

    await _syncFromApi();
    _setLoading(false);
    notifyListeners();
    return true;
  }

  /// Sign Up / Register
  Future<bool> register(String name, String email, String password) async {
    _setLoading(true);
    await Future.delayed(const Duration(milliseconds: 800)); 
    final userId = 'u-${DateTime.now().millisecondsSinceEpoch}';

    _user = UserModel(
      userId: userId,
      name: name,
      email: email,
    );
    _isLoggedIn = true;

    // حفظ البيانات وتسجيل الإيميل كحساب رسمي جديد
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('isLoggedIn', true);
    await prefs.setString('registeredEmail', email); // حفظ الإيميل المسجل للتحقق منه في Login
    await prefs.setString('userEmail', email);
    await prefs.setString('userName', name);
    await prefs.setString('userId', userId);

    await _syncFromApi();
    _setLoading(false);
    notifyListeners();
    return true;
  }

  /// Logout
  Future<void> logout() async {
    _isLoggedIn = false;
    _user = UserModel(userId: '', name: '', email: '');
    _goals = [];
    _activeGoal = null;
    _currentStreak = 0;
    _longestStreak = 0;
    _totalXP = 0;
    _challenges = [];
    _badges = [];

    // مسح البيانات المخزنة محلياً عند الخروج
    final prefs = await SharedPreferences.getInstance();
    await prefs.clear();

    notifyListeners();
  }

  /// Completes a daily task. XP and streak come from the backend response (it is authoritative and
  /// idempotent); a completion cannot be undone. Returns an error message, or null on success.
  Future<String?> toggleTask(String taskId) async {
    GoalModel? goal;
    DailyTaskModel? task;
    for (final g in _goals) {
      for (final t in g.tasks) {
        if (t.taskId == taskId) {
          goal = g;
          task = t;
        }
      }
    }
    if (goal == null || task == null || task.isCompleted) return null;

    task.isCompleted = true; // optimistic; reverted below if the API refuses
    notifyListeners();
    try {
      final result = await _apiService.completeTask(taskId: taskId, goalId: goal.goalId);
      _totalXP = result.totalXp;
      _currentStreak = result.currentStreak;
      if (result.longestStreak > _longestStreak) _longestStreak = result.longestStreak;
      goal.recomputeCompletedDays();
      _lastError = null;
      playSuccessSound();
      triggerHaptic();
      try {
        _badges = await _apiService.getBadges(); // the stream consumer may have awarded one
      } on ApiException {
        // Badges refresh on the next sync.
      }
      notifyListeners();
      return null;
    } on ApiException catch (e) {
      task.isCompleted = false;
      _lastError = e.message;
      notifyListeners();
      return e.message;
    }
  }

  /// Create a new Goal. When a study file is attached it is indexed first (dawem-ai), so the AI plan
  /// can be generated from it right away; if indexing fails nothing is created.
  Future<GoalModel> createGoal({
    required String title,
    required int durationDays,
    required int dailyMinutes,
    String? learningFile,
    Uint8List? learningFileBytes,
  }) async {
    _setLoading(true);
    try {
      String? fileId;
      if (learningFile != null) {
        if (learningFileBytes == null || learningFileBytes.isEmpty) {
          throw const ApiException(statusCode: -1, code: 'file_unreadable', message: 'The selected file could not be read.');
        }
        fileId = 'f-${DateTime.now().microsecondsSinceEpoch}';
        await _apiService.indexFile(fileId: fileId, fileName: learningFile, bytes: learningFileBytes);
      }
      final newGoal = await _apiService.createGoal(
        title: title,
        durationDays: durationDays,
        dailyMinutes: dailyMinutes,
        learningFile: learningFile,
      );
      if (fileId != null) newGoal.learningFileIds = [fileId];
      _goals.insert(0, newGoal);
      _activeGoal = newGoal;
      return newGoal;
    } finally {
      _setLoading(false);
    }
  }

  /// Index pasted notes so they can be used for plans and quizzes. Returns the new file id.
  Future<String> addNotes({required String fileName, required String text}) async {
    final fileId = 'n-${DateTime.now().microsecondsSinceEpoch}';
    await _apiService.indexNotes(fileId: fileId, fileName: fileName, text: text);
    return fileId;
  }

  /// Delete a Goal by ID and clear active goal if deleted (the backend has no goal-delete endpoint
  /// yet, so this only removes it from the app until the next sync).
  void deleteGoal(String goalId) {
    _goals.removeWhere((goal) => goal.goalId == goalId);
    if (_activeGoal?.goalId == goalId) {
      _activeGoal = _goals.isNotEmpty ? _goals.first : null;
    }
    notifyListeners();
  }

  /// Select active goal
  void selectGoal(GoalModel goal) {
    _activeGoal = goal;
    notifyListeners();
  }

  /// Generates a plan grounded in the goal's indexed study files (dawem-ai RAG) and stores its tasks.
  /// Falls back to the caller's most recent indexed files when the goal has none attached.
  Future<void> generateAiPlan(String goalId) async {
    final goal = _goals.firstWhere((g) => g.goalId == goalId);
    _setLoading(true);
    try {
      var fileIds = List<String>.of(goal.learningFileIds);
      if (fileIds.isEmpty) {
        final files = await _apiService.listFiles();
        fileIds = files.map((f) => f.fileId).take(20).toList();
      }
      if (fileIds.isEmpty) {
        throw const ApiException(
          statusCode: -1,
          code: 'no_files',
          message: 'Attach a PDF, TXT or MD study file before generating an AI plan.',
        );
      }
      final plan = await _apiService.generatePlan(goal: goal, fileIds: fileIds);
      goal.tasks = plan.tasks;
      goal.planId = plan.planId;
      goal.status = 'PLAN_READY';
      goal.learningFileIds = fileIds;
      goal.recomputeCompletedDays();
    } finally {
      _setLoading(false);
    }
  }

  /// Generates a quiz from indexed files (answers are only revealed by [submitQuiz] in backend mode).
  Future<QuizModel> generateQuiz({required List<String> fileIds, String? topic, int? numMcqs, int? numRecall}) =>
      _apiService.generateQuiz(fileIds: fileIds, topic: topic, numMcqs: numMcqs, numRecall: numRecall);

  Future<QuizResultModel> submitQuiz(
    QuizModel quiz,
    Map<String, int> answers, {
    Map<String, String> recallAnswers = const {},
  }) =>
      _apiService.submitQuiz(quiz, answers, recallAnswers: recallAnswers);

  /// Join a challenge. Returns an error message, or null on success.
  Future<String?> joinChallenge(String challengeId) async {
    try {
      await _apiService.joinChallenge(challengeId);
      for (final c in _challenges) {
        if (c.challengeId == challengeId) c.isJoined = true;
      }
      _lastError = null;
      notifyListeners();
      return null;
    } on ApiException catch (e) {
      _lastError = e.message;
      notifyListeners();
      return e.message;
    }
  }

  /// Creates a challenge the user joins automatically. Returns an error message, or null on success.
  Future<String?> createChallenge({
    required String title,
    required String goalType,
    required int rewardXp,
    required int daysLeft,
  }) async {
    try {
      final challenge = await _apiService.createChallenge(
        title: title,
        goalType: goalType,
        rewardXp: rewardXp,
        daysLeft: daysLeft,
      );
      _challenges.insert(0, challenge);
      _lastError = null;
      notifyListeners();
      return null;
    } on ApiException catch (e) {
      _lastError = e.message;
      notifyListeners();
      return e.message;
    }
  }

  // متغيرات حالة الإشعارات صريحة لتجنب أي مشاكل في التحديث
  bool _notifReminders = true;
  bool _notifAchievements = true;
  bool _notifChallenges = true;

  bool get notifReminders => _notifReminders;
  bool get notifAchievements => _notifAchievements;
  bool get notifChallenges => _notifChallenges;

  void updateNotificationSetting(String key, bool value) {
    if (key == 'reminders') _notifReminders = value;
    if (key == 'achievements') _notifAchievements = value;
    if (key == 'challenges') _notifChallenges = value;
    notifyListeners(); 
  }

  /// Update notification preferences
  void updateNotificationPref(String key, bool value) {
    _user.notificationPreferences[key] = value;
    notifyListeners();
  }

  // متغيرات إعدادات الخصوصية
  bool _privacyPublicProfile = true;
  bool _privacyShareActivity = true;
  bool _privacyShowStreak = true;

  bool get privacyPublicProfile => _privacyPublicProfile;
  bool get privacyShareActivity => _privacyShareActivity;
  bool get privacyShowStreak => _privacyShowStreak;

  void updatePrivacySetting(String key, bool value) {
    if (key == 'publicProfile') _privacyPublicProfile = value;
    if (key == 'shareActivity') _privacyShareActivity = value;
    if (key == 'showStreak') _privacyShowStreak = value;
    notifyListeners();
  }
}

extension StringExtension on String {
  String capitalize() {
    if (isEmpty) return this;
    return '${this[0].toUpperCase()}${substring(1)}';
  }
}