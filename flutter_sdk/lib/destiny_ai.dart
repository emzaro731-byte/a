library destiny_ai;

import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;

class DestinyAIException implements Exception {
  final String message;
  final int? statusCode;
  DestinyAIException(this.message, [this.statusCode]);
  @override
  String toString() => 'DestinyAIException($statusCode): $message';
}

class DestinyAI {
  final String baseUrl;
  final String? apiKey;
  String? accessToken;

  DestinyAI({required this.baseUrl, this.apiKey, this.accessToken});

  Map<String, String> _headers({bool json = true}) => {
        if (json) 'Content-Type': 'application/json',
        if (apiKey != null && apiKey!.isNotEmpty) 'X-API-Key': apiKey!,
        if (accessToken != null && accessToken!.isNotEmpty)
          'Authorization': 'Bearer $accessToken',
      };

  Uri _uri(String path) => Uri.parse('${baseUrl.replaceFirst(RegExp(r'/$'), '')}$path');

  Future<dynamic> _request(String method, String path, {Object? body}) async {
    final uri = _uri(path);
    final headers = _headers();
    late http.Response response;
    if (method == 'GET') {
      response = await http.get(uri, headers: headers);
    } else if (method == 'POST') {
      response = await http.post(uri, headers: headers, body: jsonEncode(body));
    } else {
      throw ArgumentError('Unsupported method');
    }
    dynamic data;
    try { data = jsonDecode(response.body); } catch (_) { data = response.body; }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      final message = data is Map ? (data['detail'] ?? data['message'] ?? 'Request failed') : 'Request failed';
      throw DestinyAIException(message.toString(), response.statusCode);
    }
    return data;
  }

  Future<Map<String, dynamic>> register({required String email, required String password}) async {
    final data = await _request('POST', '/auth/register', body: {'email': email, 'password': password});
    return Map<String, dynamic>.from(data as Map);
  }

  Future<Map<String, dynamic>> login({required String email, required String password}) async {
    final data = await _request('POST', '/auth/login', body: {'email': email, 'password': password});
    final result = Map<String, dynamic>.from(data as Map);
    accessToken = (result['access_token'] ?? result['token'])?.toString();
    return result;
  }

  Future<Map<String, dynamic>> me() async => Map<String, dynamic>.from(await _request('GET', '/auth/me') as Map);

  Future<Map<String, dynamic>> models() async => Map<String, dynamic>.from(await _request('GET', '/v1/gateway/models') as Map);

  Future<Map<String, dynamic>> providers() async => Map<String, dynamic>.from(await _request('GET', '/v1/gateway/providers') as Map);

  Future<Map<String, dynamic>> chat({required List<Map<String, dynamic>> messages, String? model, double temperature = 0.7, int? maxTokens, bool fallback = true, bool autoRoute = true}) async {
    final data = await _request('POST', '/v1/gateway/chat/completions', body: {
      if (model != null) 'model': model,
      'messages': messages,
      'temperature': temperature,
      if (maxTokens != null) 'max_tokens': maxTokens,
      'fallback': fallback,
      'auto_route': autoRoute,
      'stream': false,
    });
    return Map<String, dynamic>.from(data as Map);
  }

  Future<Map<String, dynamic>> ask(String text, {String? model}) => chat(messages: [
        {'role': 'user', 'content': text}
      ], model: model);

  Future<Map<String, dynamic>> health() async => Map<String, dynamic>.from(await _request('GET', '/health') as Map);

  Future<Map<String, dynamic>> supabaseSession() async => Map<String, dynamic>.from(await _request('GET', '/supabase/auth/session') as Map);

  Future<Map<String, dynamic>> storageList(String bucket) async => Map<String, dynamic>.from(await _request('GET', '/supabase/storage/$bucket') as Map);

  Future<Map<String, dynamic>> invokeFunction(String name, Map<String, dynamic> arguments) async => Map<String, dynamic>.from(await _request('POST', '/supabase/functions/$name/invoke', body: arguments) as Map);

  /// Helper for Flutter multipart uploads to the self-hosted storage gateway.
  Future<Map<String, dynamic>> uploadBytes({required String bucket, required String path, required Uint8List bytes, String contentType = 'application/octet-stream'}) async {
    final request = http.MultipartRequest('POST', _uri('/supabase/storage/$bucket/$path'));
    request.headers.addAll(_headers(json: false));
    request.files.add(http.MultipartFile.fromBytes('file', bytes, filename: path));
    request.headers['Content-Type'] = contentType;
    final response = await http.Response.fromStream(await request.send());
    dynamic data;
    try { data = jsonDecode(response.body); } catch (_) { data = response.body; }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw DestinyAIException(data is Map ? (data['detail'] ?? 'Upload failed').toString() : 'Upload failed', response.statusCode);
    }
    return Map<String, dynamic>.from(data as Map);
  }
}
