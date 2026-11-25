import 'json_case.dart';
import 'models.dart';

/// Contract between the app's camelCase models and dawem-ai's snake_case JSON
/// (`dawem-ai/dawem_ai/schemas/contracts.py`, `dawem-ai/dawem_ai/api/schemas.py`).
///
/// Requests: explicit builders, so every key sent to the AI service is visible here.
/// Responses: [JsonCase.toCamel] and then the models' `fromJson`.
class AiMappers {
  AiMappers._();

  // ---- requests (camelCase app values -> snake_case AI body) ----

  static Map<String, dynamic> indexNotesRequest({
    required String userId,
    required String fileId,
    required String fileName,
    required String text,
  }) =>
      {'user_id': userId, 'file_id': fileId, 'file_name': fileName, 'text': text};

  /// Text fields of the multipart upload to `POST /files/index` (the file itself is the `file` part).
  static Map<String, String> indexFileFields({
    required String userId,
    required String fileId,
    required String fileName,
  }) =>
      {'user_id': userId, 'file_id': fileId, 'file_name': fileName};

  static Map<String, dynamic> studyPlanRequest({
    required String userId,
    required List<String> fileIds,
    required String goal,
    required int durationDays,
    required int dailyMinutes,
    required String level,
    String? context,
  }) =>
      {
        'user_id': userId,
        'file_ids': fileIds,
        'goal': goal,
        'duration_days': durationDays,
        'daily_minutes': dailyMinutes,
        'level': level,
        if (context != null && context.trim().isNotEmpty) 'context': context.trim(),
      };

  static Map<String, dynamic> quizRequest({
    required String userId,
    required List<String> fileIds,
    String? topic,
    int? numMcqs,
    int? numRecall,
  }) =>
      {
        'user_id': userId,
        'file_ids': fileIds,
        if (topic != null && topic.trim().isNotEmpty) 'topic': topic.trim(),
        if (numMcqs != null) 'num_mcqs': numMcqs,
        if (numRecall != null) 'num_recall': numRecall,
      };

  // ---- responses (snake_case AI JSON -> app models) ----

  static IndexResultModel indexResult(Map<String, dynamic> aiJson) =>
      IndexResultModel.fromJson(JsonCase.toCamel(aiJson) as Map<String, dynamic>);

  static StudyPlanModel studyPlan(Map<String, dynamic> aiJson) =>
      StudyPlanModel.fromJson(JsonCase.toCamel(aiJson) as Map<String, dynamic>);

  static QuizModel quiz(Map<String, dynamic> aiJson) =>
      QuizModel.fromJson(JsonCase.toCamel(aiJson) as Map<String, dynamic>);
}
