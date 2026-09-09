import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;

class DestinyAIPlus {
  final String baseUrl;
  final String? apiKey;
  String? accessToken;
  DestinyAIPlus({required this.baseUrl, this.apiKey, this.accessToken});

  Uri _uri(String path) => Uri.parse('${baseUrl.replaceFirst(RegExp(r'/$'), '')}$path');
  Map<String,String> _headers({bool api=true}) => {
    'Content-Type':'application/json',
    if(apiKey != null && apiKey!.isNotEmpty && api) 'Authorization':'Bearer $apiKey',
  };
  Map<String,String> _userHeaders() => {
    'Content-Type':'application/json',
    if(apiKey != null && apiKey!.isNotEmpty) 'Authorization':'Bearer $apiKey',
    if(accessToken != null && accessToken!.isNotEmpty) 'X-User-ID':accessToken!,
  };

  Future<dynamic> _request(String method,String path,{Object? body,bool user=false}) async {
    final h=user ? _userHeaders() : _headers();
    late http.Response r;
    if(method=='GET') r=await http.get(_uri(path),headers:h);
    else if(method=='POST') r=await http.post(_uri(path),headers:h,body:jsonEncode(body));
    else if(method=='DELETE') r=await http.delete(_uri(path),headers:h);
    else throw ArgumentError('Unsupported method');
    dynamic d; try{d=jsonDecode(r.body);}catch(_){d=r.body;}
    if(r.statusCode<200||r.statusCode>=300){throw Exception('DestinyAI HTTP ${r.statusCode}: ${d is Map?(d['detail']??d['error']??'Request failed'):d}');}
    return d;
  }

  Future<Map<String,dynamic>> login(String email,String password) async {
    final d=Map<String,dynamic>.from(await _request('POST','/auth/login',body:{'email':email,'password':password}) as Map);
    accessToken=(d['access_token']??d['token'])?.toString(); return d;
  }
  Future<Map<String,dynamic>> register(String email,String password) async => Map<String,dynamic>.from(await _request('POST','/auth/register',body:{'email':email,'password':password}) as Map);
  Future<Map<String,dynamic>> me() async => Map<String,dynamic>.from(await _request('GET','/auth/me',user:true) as Map);
  Future<Map<String,dynamic>> ask(String text,{String? model}) async => chat([{'role':'user','content':text}],model:model);
  Future<Map<String,dynamic>> chat(List<Map<String,dynamic>> messages,{String? model,double temperature=.7,int? maxTokens,String? conversationId,bool remember=false}) async => Map<String,dynamic>.from(await _request('POST','/v1/chat/completions',body:{'messages':messages,if(model!=null)'model':model,'temperature':temperature,if(maxTokens!=null)'max_tokens':maxTokens,if(conversationId!=null)'conversation_id':conversationId,'remember':remember,'stream':false},user:true) as Map);

  Stream<String> streamChat(List<Map<String,dynamic>> messages,{String? model,double temperature=.7,int? maxTokens,String? conversationId,bool remember=false}) async* {
    final req=http.Request('POST',_uri('/v1/chat/completions')); req.headers.addAll(_userHeaders());
    req.body=jsonEncode({'messages':messages,if(model!=null)'model':model,'temperature':temperature,if(maxTokens!=null)'max_tokens':maxTokens,if(conversationId!=null)'conversation_id':conversationId,'remember':remember,'stream':true});
    final res=await req.send();
    if(res.statusCode<200||res.statusCode>=300) throw Exception('DestinyAI streaming HTTP ${res.statusCode}');
    await for(final line in res.stream.transform(utf8.decoder).transform(const LineSplitter())){
      if(!line.startsWith('data: ')) continue; final raw=line.substring(6); if(raw=='[DONE]') return;
      try{final d=jsonDecode(raw); final text=d['choices']?[0]?['delta']?['content']; if(text is String&&text.isNotEmpty) yield text;}catch(_){ }
    }
  }

  Future<Map<String,dynamic>> createConversation({String title='New chat'}) async => Map<String,dynamic>.from(await _request('POST','/v1/conversations',body:{'title':title},user:true) as Map);
  Future<Map<String,dynamic>> conversations() async => Map<String,dynamic>.from(await _request('GET','/v1/conversations',user:true) as Map);
  Future<Map<String,dynamic>> messages(String conversationId) async => Map<String,dynamic>.from(await _request('GET','/v1/conversations/$conversationId/messages',user:true) as Map);
  Future<Map<String,dynamic>> memories() async => Map<String,dynamic>.from(await _request('GET','/v1/memories',user:true) as Map);
  Future<Map<String,dynamic>> addMemory(String memory) async => Map<String,dynamic>.from(await _request('POST','/v1/memories',body:{'memory':memory},user:true) as Map);
  Future<Map<String,dynamic>> deleteMemory(int id) async => Map<String,dynamic>.from(await _request('DELETE','/v1/memories/$id',user:true) as Map);
  Future<Map<String,dynamic>> files() async => Map<String,dynamic>.from(await _request('GET','/v1/files',user:true) as Map);
  Future<Map<String,dynamic>> deleteFile(String id) async => Map<String,dynamic>.from(await _request('DELETE','/v1/files/$id',user:true) as Map);

  Future<Map<String,dynamic>> uploadBytes(Uint8List bytes,String name) async {
    final req=http.MultipartRequest('POST',_uri('/v1/files')); req.headers.addAll(_userHeaders());
    req.files.add(http.MultipartFile.fromBytes('file',bytes,filename:name));
    final r=await http.Response.fromStream(await req.send()); final d=jsonDecode(r.body);
    if(r.statusCode<200||r.statusCode>=300) throw Exception('Upload failed: $d'); return Map<String,dynamic>.from(d as Map);
  }

  Future<Map<String,dynamic>> generateImage(String prompt,{String size='1024x1024',int n=1}) async => Map<String,dynamic>.from(await _request('POST','/v1/images/generations',body:{'prompt':prompt,'size':size,'n':n}) as Map);
  Future<Map<String,dynamic>> generateVideo(String prompt,{int duration=5,int width=1024,int height=576}) async => Map<String,dynamic>.from(await _request('POST','/v1/videos/generations',body:{'prompt':prompt,'duration':duration,'width':width,'height':height}) as Map);
  Future<Map<String,dynamic>> generateMusic(String prompt,{int duration=30,bool instrumental=true,int? bpm}) async => Map<String,dynamic>.from(await _request('POST','/v1/audio/music/generations',body:{'prompt':prompt,'duration':duration,'instrumental':instrumental,if(bpm!=null)'bpm':bpm}) as Map);
  Future<Map<String,dynamic>> mediaCapabilities() async => Map<String,dynamic>.from(await _request('GET','/v1/media/capabilities') as Map);
  Future<Map<String,dynamic>> models() async => Map<String,dynamic>.from(await _request('GET','/v1/gateway/models',api:true) as Map);
  Future<Map<String,dynamic>> providers() async => Map<String,dynamic>.from(await _request('GET','/v1/gateway/providers',api:true) as Map);
}
