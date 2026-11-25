import 'package:flutter_test/flutter_test.dart';
import 'package:streakly_app/core/network/json_case.dart';

void main() {
  group('JsonCase keys', () {
    test('snake_case -> camelCase', () {
      expect(JsonCase.keyToCamel('plan_id'), 'planId');
      expect(JsonCase.keyToCamel('duration_minutes'), 'durationMinutes');
      expect(JsonCase.keyToCamel('source_chunk_ids'), 'sourceChunkIds');
      expect(JsonCase.keyToCamel('title'), 'title');
    });

    test('camelCase -> snake_case', () {
      expect(JsonCase.keyToSnake('planId'), 'plan_id');
      expect(JsonCase.keyToSnake('durationMinutes'), 'duration_minutes');
      expect(JsonCase.keyToSnake('title'), 'title');
    });
  });

  group('JsonCase payloads', () {
    test('converts nested maps and lists, leaving values untouched', () {
      final out = JsonCase.toCamel({
        'plan_id': 'snake_value_stays',
        'tasks': [
          {'day_number': 1, 'sources': [{'file_id': 'f1', 'page_number': null}]},
        ],
      }) as Map<String, dynamic>;
      expect(out['planId'], 'snake_value_stays');
      final task = (out['tasks'] as List).first as Map<String, dynamic>;
      expect(task['dayNumber'], 1);
      final source = (task['sources'] as List).first as Map<String, dynamic>;
      expect(source['fileId'], 'f1');
      expect(source.containsKey('pageNumber'), isTrue);
    });

    test('camelCase -> snake_case round trip', () {
      final original = {'planId': 'p', 'tasks': [{'dayNumber': 2}]};
      expect(JsonCase.toCamel(JsonCase.toSnake(original)), original);
    });
  });
}
