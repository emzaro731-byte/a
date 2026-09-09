import 'package:flutter/material.dart';

import 'ai_service.dart';

/// Drop-in Flutter UI for chat-adjacent image, video and music generation.
///
/// The widget discovers the backend capabilities at runtime, so unavailable
/// generators are hidden automatically.
class AiGenerationPanel extends StatefulWidget {
  const AiGenerationPanel({super.key, required this.ai});

  final AiService ai;

  @override
  State<AiGenerationPanel> createState() => _AiGenerationPanelState();
}

class _AiGenerationPanelState extends State<AiGenerationPanel> {
  final _promptController = TextEditingController();
  Map<String, dynamic>? _capabilities;
  Map<String, dynamic>? _result;
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadCapabilities();
  }

  @override
  void dispose() {
    _promptController.dispose();
    super.dispose();
  }

  Future<void> _loadCapabilities() async {
    try {
      final value = await widget.ai.mediaCapabilities();
      if (!mounted) return;
      setState(() => _capabilities = value);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = _friendlyError(e));
    }
  }

  bool _enabled(String key) {
    final value = _capabilities?[key];
    if (value is bool) return value;
    final nested = _capabilities?['capabilities'];
    if (nested is Map && nested[key] is bool) return nested[key] as bool;
    return true;
  }

  Future<void> _generate(String type) async {
    final prompt = _promptController.text.trim();
    if (prompt.isEmpty) {
      setState(() => _error = 'Enter a prompt first.');
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
      _result = null;
    });

    try {
      late final Map<String, dynamic> result;
      switch (type) {
        case 'image':
          result = await widget.ai.generateImage(prompt: prompt);
          break;
        case 'video':
          result = await widget.ai.generateVideo(prompt: prompt);
          break;
        case 'music':
          result = await widget.ai.generateMusic(prompt: prompt);
          break;
      }
      if (!mounted) return;
      setState(() => _result = result);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = _friendlyError(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _friendlyError(Object error) {
    final text = error.toString();
    if (text.contains('SocketException') || text.contains('Connection')) {
      return 'Cannot reach the AI server. Check the API URL and server status.';
    }
    return text.replaceFirst('Exception: ', '');
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      elevation: 0,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Create with AI', style: theme.textTheme.titleLarge),
            const SizedBox(height: 6),
            Text(
              'Generate images, videos and music from one prompt.',
              style: theme.textTheme.bodyMedium,
            ),
            const SizedBox(height: 14),
            TextField(
              controller: _promptController,
              minLines: 2,
              maxLines: 5,
              textInputAction: TextInputAction.newline,
              decoration: const InputDecoration(
                hintText: 'Describe what you want to create…',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (_enabled('image')) _button(Icons.image_outlined, 'Image', 'image'),
                if (_enabled('video')) _button(Icons.movie_creation_outlined, 'Video', 'video'),
                if (_enabled('music')) _button(Icons.music_note_outlined, 'Music', 'music'),
              ],
            ),
            if (_loading) ...[
              const SizedBox(height: 16),
              const LinearProgressIndicator(),
              const SizedBox(height: 8),
              const Text('Creating… the first generation can take longer while the model loads.'),
            ],
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
            ],
            if (_result != null) ...[
              const SizedBox(height: 16),
              _ResultView(result: _result!),
            ],
          ],
        ),
      ),
    );
  }

  Widget _button(IconData icon, String label, String type) {
    return FilledButton.icon(
      onPressed: _loading ? null : () => _generate(type),
      icon: Icon(icon),
      label: Text(label),
    );
  }
}

class _ResultView extends StatelessWidget {
  const _ResultView({required this.result});

  final Map<String, dynamic> result;

  String? get _url {
    final direct = result['url'];
    if (direct is String && direct.isNotEmpty) return direct;
    final data = result['data'];
    if (data is List && data.isNotEmpty && data.first is Map) {
      final item = Map<String, dynamic>.from(data.first as Map);
      final value = item['url'] ?? item['audio_url'] ?? item['video_url'];
      if (value is String && value.isNotEmpty) return value;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final url = _url;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: Theme.of(context).dividerColor),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Generation complete'),
          if (url != null) ...[
            const SizedBox(height: 6),
            SelectableText(url),
          ] else ...[
            const SizedBox(height: 6),
            SelectableText(result.toString()),
          ],
        ],
      ),
    );
  }
}
