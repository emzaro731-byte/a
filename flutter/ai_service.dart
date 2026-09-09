import 'package:dio/dio.dart';

class AiService {
  AiService({required this.baseUrl, this.apiKey});

  final String baseUrl;
  final String? apiKey;
  final Dio _dio = Dio();

  Future<String> chat({required List<Map<String, String>> messages}) async {
    final headers = <String, String>{'Content-Type': 'application/json'};
    if (apiKey != null && apiKey!.isNotEmpty) {
      headers['Authorization'] = 'Bearer $apiKey';
    }

    final response = await _dio.post(
      '$baseUrl/v1/chat/completions',
      options: Options(headers: headers),
      data: {
        'model': 'qwen2.5:3b',
        'messages': messages,
        'temperature': 0.7,
      },
    );

    return response.data['choices'][0]['message']['content'] as String;
  }
}

// Example:
// final ai = AiService(baseUrl: 'https://api.example.com');
// final reply = await ai.chat(messages: [
//   {'role': 'user', 'content': 'Hello!'},
// ]);
