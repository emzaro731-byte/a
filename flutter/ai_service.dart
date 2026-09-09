import 'dart:convert';
import 'dart:typed_data';
import 'package:dio/dio.dart';

class AiService {
  AiService({required this.baseUrl, this.apiKey, Dio? dio}) : _dio = dio ?? Dio();

  final String baseUrl;
  final String? apiKey;
  final Dio _dio;

  Map<String, String> _headers() {
    final headers = <String, String>{'Content-Type': 'application/json'};
    if (apiKey != null && apiKey!.isNotEmpty) headers['Authorization'] = 'Bearer $apiKey';
    return headers;
  }

  Future<String> chat({required List<Map<String, String>> messages, String model = 'default', double temperature = 0.7, int? maxTokens}) async {
    final data = <String, dynamic>{'model': model, 'messages': messages, 'temperature': temperature};
    if (maxTokens != null) data['max_tokens'] = maxTokens;
    final response = await _dio.post('$baseUrl/v1/chat/completions', options: Options(headers: _headers()), data: data);
    return response.data['choices'][0]['message']['content'] as String;
  }

  Future<List<String>> models() async {
    final response = await _dio.get('$baseUrl/v1/models', options: Options(headers: _headers()));
    final items = (response.data['data'] as List).cast<Map<String, dynamic>>();
    return items.map((item) => item['id'] as String).toList();
  }

  Stream<String> chatStream({required List<Map<String, String>> messages, String model = 'default', double temperature = 0.7, int? maxTokens, CancelToken? cancelToken}) async* {
    final data = <String, dynamic>{'model': model, 'messages': messages, 'temperature': temperature, 'stream': true};
    if (maxTokens != null) data['max_tokens'] = maxTokens;
    final response = await _dio.post<ResponseBody>('$baseUrl/v1/chat/completions', options: Options(headers: _headers(), responseType: ResponseType.stream), data: data, cancelToken: cancelToken);
    var buffer = '';
    await for (final bytes in response.data!.stream) {
      buffer += utf8.decode(bytes, allowMalformed: true);
      final parts = buffer.split('\n');
      buffer = parts.removeLast();
      for (final line in parts) {
        if (!line.startsWith('data: ')) continue;
        final payload = line.substring(6).trim();
        if (payload.isEmpty || payload == '[DONE]') continue;
        final json = jsonDecode(payload) as Map<String, dynamic>;
        final choices = json['choices'] as List?;
        if (choices == null || choices.isEmpty) continue;
        final delta = choices.first['delta'] as Map<String, dynamic>?;
        final text = delta?['content'] as String?;
        if (text != null && text.isNotEmpty) yield text;
      }
    }
  }

  Future<Map<String, dynamic>> mediaCapabilities() async {
    final response = await _dio.get('$baseUrl/v1/media/capabilities', options: Options(headers: _headers()));
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> imageStyles() async {
    final response = await _dio.get('$baseUrl/v1/images/styles', options: Options(headers: _headers()));
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> generateImage({required String prompt, String? negativePrompt, String size = '1024x1024', int n = 1, int? seed, String style = 'none'}) async {
    return _generate('/v1/images/generations', {
      'prompt': prompt,
      if (negativePrompt != null) 'negative_prompt': negativePrompt,
      'size': size,
      'n': n,
      'style': style,
      if (seed != null) 'seed': seed,
    });
  }

  Future<Map<String, dynamic>> editImage({required Uint8List imageBytes, required String prompt, String? negativePrompt, double strength = 0.65, String style = 'none', int? seed}) async {
    return _generate('/v1/images/edits', {
      'prompt': prompt,
      'image': base64Encode(imageBytes),
      if (negativePrompt != null) 'negative_prompt': negativePrompt,
      'strength': strength,
      'style': style,
      if (seed != null) 'seed': seed,
    });
  }

  Future<Map<String, dynamic>> generateVideo({required String prompt, int duration = 5, int width = 1024, int height = 576, int? seed}) async {
    return _generate('/v1/videos/generations', {'prompt': prompt, 'duration': duration, 'width': width, 'height': height, if (seed != null) 'seed': seed});
  }

  Future<Map<String, dynamic>> generateMusic({required String prompt, int duration = 30, bool instrumental = true, int? bpm, int? seed}) async {
    return _generate('/v1/audio/music/generations', {'prompt': prompt, 'duration': duration, 'instrumental': instrumental, if (bpm != null) 'bpm': bpm, if (seed != null) 'seed': seed});
  }

  Future<Map<String, dynamic>> _generate(String path, Map<String, dynamic> data) async {
    final response = await _dio.post('$baseUrl$path', options: Options(headers: _headers()), data: data);
    return Map<String, dynamic>.from(response.data as Map);
  }
}

// Examples:
// await ai.generateImage(prompt: 'cinematic Lagos skyline at sunset', style: 'cinematic');
// await ai.editImage(imageBytes: bytes, prompt: 'turn this into a cinematic poster', style: 'cinematic');
// await ai.generateVideo(prompt: 'a futuristic city flying through clouds', duration: 8);
// await ai.generateMusic(prompt: 'Afrobeats instrumental with warm guitar and deep bass', duration: 30);
