import 'dart:convert';
import 'package:dio/dio.dart';

class AiService {
  AiService({required this.baseUrl, this.apiKey, Dio? dio}) : _dio = dio ?? Dio();

  final String baseUrl;
  final String? apiKey;
  final Dio _dio;

  Map<String, String> _headers() {
    final headers = <String, String>{'Content-Type': 'application/json'};
    if (apiKey != null && apiKey!.isNotEmpty) {
      headers['Authorization'] = 'Bearer $apiKey';
    }
    return headers;
  }

  Future<String> chat({
    required List<Map<String, String>> messages,
    String model = 'default',
    double temperature = 0.7,
    int? maxTokens,
  }) async {
    final data = <String, dynamic>{
      'model': model,
      'messages': messages,
      'temperature': temperature,
    };
    if (maxTokens != null) data['max_tokens'] = maxTokens;

    final response = await _dio.post(
      '$baseUrl/v1/chat/completions',
      options: Options(headers: _headers()),
      data: data,
    );
    return response.data['choices'][0]['message']['content'] as String;
  }

  Future<List<String>> models() async {
    final response = await _dio.get(
      '$baseUrl/v1/models',
      options: Options(headers: _headers()),
    );
    final items = (response.data['data'] as List).cast<Map<String, dynamic>>();
    return items.map((item) => item['id'] as String).toList();
  }

  Stream<String> chatStream({
    required List<Map<String, String>> messages,
    String model = 'default',
    double temperature = 0.7,
    int? maxTokens,
    CancelToken? cancelToken,
  }) async* {
    final data = <String, dynamic>{
      'model': model,
      'messages': messages,
      'temperature': temperature,
      'stream': true,
    };
    if (maxTokens != null) data['max_tokens'] = maxTokens;

    final response = await _dio.post<ResponseBody>(
      '$baseUrl/v1/chat/completions',
      options: Options(
        headers: _headers(),
        responseType: ResponseType.stream,
      ),
      data: data,
      cancelToken: cancelToken,
    );

    final stream = response.data!.stream;
    var buffer = '';
    await for (final bytes in stream) {
      buffer += utf8.decode(bytes, allowMalformed: true);
      final parts = buffer.split('\n');
      buffer = parts.removeLast();
      for (final line in parts) {
        if (!line.startsWith('data: ')) continue;
        final payload = line.substring(6).trim();
        if (payload == '[DONE]' || payload.isEmpty) continue;
        final json = jsonDecode(payload) as Map<String, dynamic>;
        final choices = json['choices'] as List?;
        if (choices == null || choices.isEmpty) continue;
        final delta = choices.first['delta'] as Map<String, dynamic>?;
        final text = delta?['content'] as String?;
        if (text != null && text.isNotEmpty) yield text;
      }
    }
  }
}

// Examples:
// final ai = AiService(baseUrl: 'https://your-domain.com');
// final reply = await ai.chat(
//   model: 'reasoning',
//   messages: [{'role': 'user', 'content': 'Explain quantum computing'}],
// );
// await for (final token in ai.chatStream(messages: messages)) { print(token); }
