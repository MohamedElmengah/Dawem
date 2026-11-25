/// Where the app talks to, chosen at build time with `--dart-define` (or
/// `--dart-define-from-file=.env`, see `.env.example`).
///
/// * [ApiMode.backend]: the AWS API Gateway (`API_BASE_URL`). Goals, tasks, streak, XP,
///   badges, challenges, files, plans and quizzes all go through it; the AI service is only
///   reached by the backend.
/// * [ApiMode.demo]: the app talks straight to the dawem-ai FastAPI service
///   (`AI_BASE_URL`, default `http://localhost:8000`) for indexing, plans and quizzes. There
///   is no backend in this mode, so goals/tasks/XP live in memory for the session.
enum ApiMode { backend, demo }

class ApiConfig {
  const ApiConfig({
    required this.mode,
    this.backendBaseUrl = '',
    this.aiBaseUrl = 'http://localhost:8000',
    this.aiApiKey = '',
    this.devAuthToken = '',
    this.demoUserId = 'demo-user',
    this.timezone = 'UTC',
    this.timeout = const Duration(seconds: 30),
    this.aiTimeout = const Duration(seconds: 120),
  });

  final ApiMode mode;

  /// API Gateway URL (the `ApiUrl` stack output), e.g. `https://abc123.execute-api.eu-north-1.amazonaws.com`.
  final String backendBaseUrl;

  /// dawem-ai base URL for [ApiMode.demo]. On an Android emulator use `http://10.0.2.2:8000`.
  final String aiBaseUrl;

  /// Sent as `X-API-Key` in demo mode when the AI service has `DAWEM_AI_API_KEY` set.
  /// Anything compiled into an app can be extracted: use this for local demos only.
  final String aiApiKey;

  /// A Cognito token for development (backend mode). Real sign-in should replace it.
  final String devAuthToken;

  /// `user_id` sent to the AI service in demo mode.
  final String demoUserId;

  /// IANA timezone stored with the profile (the backend needs it to know what "today" is).
  final String timezone;

  final Duration timeout;

  /// Plan and quiz generation call an LLM and can take a while (a 365-day plan is ~53 calls).
  final Duration aiTimeout;

  bool get isDemo => mode == ApiMode.demo;

  /// Picks the mode from `API_MODE` (`backend` | `demo`); when unset, `backend` if `API_BASE_URL`
  /// is given and `demo` otherwise, so a bare `flutter run` works against a local dawem-ai.
  factory ApiConfig.fromEnvironment() {
    const explicit = String.fromEnvironment('API_MODE');
    const baseUrl = String.fromEnvironment('API_BASE_URL');
    final ApiMode mode;
    if (explicit == 'backend') {
      mode = ApiMode.backend;
    } else if (explicit == 'demo') {
      mode = ApiMode.demo;
    } else {
      mode = baseUrl.isNotEmpty ? ApiMode.backend : ApiMode.demo;
    }
    return ApiConfig(
      mode: mode,
      backendBaseUrl: baseUrl,
      aiBaseUrl: const String.fromEnvironment('AI_BASE_URL', defaultValue: 'http://localhost:8000'),
      aiApiKey: const String.fromEnvironment('AI_API_KEY'),
      devAuthToken: const String.fromEnvironment('DEV_AUTH_TOKEN'),
      demoUserId: const String.fromEnvironment('DEMO_USER_ID', defaultValue: 'demo-user'),
      timezone: const String.fromEnvironment('TIMEZONE', defaultValue: 'UTC'),
    );
  }

  ApiConfig copyWith({
    ApiMode? mode,
    String? backendBaseUrl,
    String? aiBaseUrl,
    String? aiApiKey,
    String? devAuthToken,
    String? demoUserId,
    String? timezone,
    Duration? timeout,
    Duration? aiTimeout,
  }) =>
      ApiConfig(
        mode: mode ?? this.mode,
        backendBaseUrl: backendBaseUrl ?? this.backendBaseUrl,
        aiBaseUrl: aiBaseUrl ?? this.aiBaseUrl,
        aiApiKey: aiApiKey ?? this.aiApiKey,
        devAuthToken: devAuthToken ?? this.devAuthToken,
        demoUserId: demoUserId ?? this.demoUserId,
        timezone: timezone ?? this.timezone,
        timeout: timeout ?? this.timeout,
        aiTimeout: aiTimeout ?? this.aiTimeout,
      );
}
