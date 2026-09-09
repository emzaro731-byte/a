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

  Future<List<Map<String, dynamic>>> imageStyles() async {
    final response = await _dio.get('$baseUrl/v1/images/styles', options: Options(headers: _headers()));
    return List<Map<String, dynamic>>.from(response.data['data'] as List);
  }

  Future<List<Map<String, dynamic>>> imageModels() async {
    final response = await _dio.get('$baseUrl/v1/images/models', options: Options(headers: _headers()));
    return List<Map<String, dynamic>>.from(response.data['data'] as List);
  }

  Future<Map<String, dynamic>> generateImage({required String prompt, String? negativePrompt, String size = '1024x1024', int n = 1, int? seed, String style = 'none', int steps = 30, double guidanceScale = 7.5, String? model}) async {
    return _generate('/v1/images/generations', {
      'prompt': prompt,
      if (negativePrompt != null) 'negative_prompt': negativePrompt,
      'size': size,
      'n': n,
      'style': style,
      'steps': steps,
      'guidance_scale': guidanceScale,
      if (seed != null) 'seed': seed,
      if (model != null) 'model': model,
    });
  }

  Future<Map<String, dynamic>> editImage({required Uint8List imageBytes, required String prompt, String? negativePrompt, double strength = 0.65, String style = 'none', int n = 1, int? seed, int steps = 30, double guidanceScale = 7.5, String? model}) async {
    return _generate('/v1/images/edits', {
      'prompt': prompt,
      'image': base64Encode(imageBytes),
      if (negativePrompt != null) 'negative_prompt': negativePrompt,
      'strength': strength,
      'style': style,
      'n': n,
      'steps': steps,
      'guidance_scale': guidanceScale,
      if (seed != null) 'seed': seed,
      if (model != null) 'model': model,
    });
  }

  Future<Map<String, dynamic>> createVariation({required Uint8List imageBytes, String prompt = 'high quality variation of the source image', String style = 'none', int? seed, String? model}) async {
    return _generate('/v1/images/variations', {
      'image': base64Encode(imageBytes),
      'prompt': prompt,
      'style': style,
      'strength': 0.45,
      if (seed != null) 'seed': seed,
      if (model != null) 'model': model,
    });
  }

  Future<Map<String, dynamic>> generateVideo({required String prompt, int duration = 5, int width = 1024, int height = 576, int? seed}) async {
    return _generate('/v1/videos/generations', {'prompt': prompt, 'duration': duration, 'width': width, 'height': height, if (seed != null) 'seed': seed});
  }

  Future<Map<String, dynamic>> generateMusic({required String prompt, int duration = 30, bool instrumental = true, int? bpm, int? seed}) async {
    return _generate('/v1/audio/music/generations', {'prompt': prompt, 'duration': duration, 'instrumental': instrumental, if (bpm != null) 'bpm': bpm, if (seed != null) 'seed': seed});
  }

  String mediaUrl(String path) => path.startsWith('http') ? path : '$baseUrl${path.startsWith('/') ? '' : '/'}$path';

  Future<Map<String, dynamic>> _generate(String path, Map<String, dynamic> data) async {
    final response = await _dio.post('$baseUrl$path', options: Options(headers: _headers()), data: data);
    return Map<String, dynamic>.from(response.data as Map);
  }
}
