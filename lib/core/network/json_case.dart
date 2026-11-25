/// snake_case <-> camelCase conversion for JSON payloads.
///
/// dawem-ai speaks snake_case (`plan_id`, `duration_minutes`, `file_id`); the app's models and the
/// AWS backend speak camelCase (`planId`, `durationMinutes`, `fileId`). Responses from the AI
/// service go through [JsonCase.toCamel] before they reach a model. Requests are built explicitly
/// in `ai_mappers.dart` (acronym keys such as `totalXP` do not round-trip through a generic
/// converter, and an explicit request builder is a contract you can read).
class JsonCase {
  JsonCase._();

  static final RegExp _underscoreLetter = RegExp(r'_([a-zA-Z0-9])');
  static final RegExp _upper = RegExp(r'[A-Z]');

  /// `plan_id` -> `planId`. Keys without an underscore are unchanged.
  static String keyToCamel(String key) =>
      key.replaceAllMapped(_underscoreLetter, (m) => m.group(1)!.toUpperCase());

  /// `planId` -> `plan_id`.
  static String keyToSnake(String key) =>
      key.replaceAllMapped(_upper, (m) => '_${m.group(0)!.toLowerCase()}');

  /// Recursively converts every map key. Values (including strings that look like keys) are untouched.
  static dynamic toCamel(dynamic json) => _convert(json, keyToCamel);

  static dynamic toSnake(dynamic json) => _convert(json, keyToSnake);

  static dynamic _convert(dynamic json, String Function(String) convertKey) {
    if (json is Map) {
      return <String, dynamic>{
        for (final entry in json.entries) convertKey(entry.key.toString()): _convert(entry.value, convertKey),
      };
    }
    if (json is List) {
      return json.map((e) => _convert(e, convertKey)).toList();
    }
    return json;
  }
}
