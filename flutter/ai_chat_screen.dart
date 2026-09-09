import 'package:flutter/material.dart';

import 'ai_service.dart';

/// A complete chat-first Flutter screen for the local AI API.
///
/// The composer can switch between Chat, Image, Video and Music without
/// leaving the conversation. It only depends on Flutter and AiService.
class AiChatScreen extends StatefulWidget {
  const AiChatScreen({super.key, required this.ai});

  final AiService ai;

  @override
  State<AiChatScreen> createState() => _AiChatScreenState();
}

enum _Mode { chat, image, video, music }

class _ChatItem {
  const _ChatItem({required this.role, required this.text});
  final String role;
  final String text;
}

class _AiChatScreenState extends State<AiChatScreen> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();
  final List<_ChatItem> _messages = [];
  _Mode _mode = _Mode.chat;
  String _model = 'default';
  bool _busy = false;
  String? _error;
  Map<String, dynamic>? _capabilities;

  @override
  void initState() {
    super.initState();
    _loadCapabilities();
  }

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  Future<void> _loadCapabilities() async {
    try {
      final caps = await widget.ai.mediaCapabilities();
      if (mounted) setState(() => _capabilities = caps);
    } catch (_) {
      // Chat remains usable even if capability discovery is unavailable.
    }
  }

  bool _enabled(String key) {
    final value = _capabilities?[key];
    return value is bool ? value : true;
  }

  String get _modeLabel {
    switch (_mode) {
      case _Mode.chat:
        return 'Chat';
      case _Mode.image:
        return 'Image';
      case _Mode.video:
        return 'Video';
      case _Mode.music:
        return 'Music';
    }
  }

  IconData get _modeIcon {
    switch (_mode) {
      case _Mode.chat:
        return Icons.auto_awesome;
      case _Mode.image:
        return Icons.image_outlined;
      case _Mode.video:
        return Icons.movie_creation_outlined;
      case _Mode.music:
        return Icons.music_note_outlined;
    }
  }

  Future<void> _send() async {
    final prompt = _controller.text.trim();
    if (prompt.isEmpty || _busy) return;
    _controller.clear();
    setState(() {
      _error = null;
      _busy = true;
      _messages.add(_ChatItem(role: 'user', text: prompt));
    });
    _scrollToBottom();

    try {
      if (_mode == _Mode.chat) {
        await _sendChat(prompt);
      } else {
        await _generateMedia(prompt);
      }
    } catch (e) {
      if (mounted) setState(() => _error = _friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
      _scrollToBottom();
    }
  }

  Future<void> _sendChat(String prompt) async {
    final history = _messages
        .map((m) => <String, String>{'role': m.role, 'content': m.text})
        .toList();
    final answer = await widget.ai.chat(
      messages: history,
      model: _model,
      temperature: 0.7,
    );
    if (mounted) {
      setState(() => _messages.add(_ChatItem(role: 'assistant', text: answer)));
    }
  }

  Future<void> _generateMedia(String prompt) async {
    late final Map<String, dynamic> result;
    switch (_mode) {
      case _Mode.image:
        result = await widget.ai.generateImage(prompt: prompt);
        break;
      case _Mode.video:
        result = await widget.ai.generateVideo(prompt: prompt);
        break;
      case _Mode.music:
        result = await widget.ai.generateMusic(prompt: prompt);
        break;
      case _Mode.chat:
        return;
    }
    final url = _extractUrl(result);
    final label = url == null ? result.toString() : '$_modeLabel created\n$url';
    if (mounted) {
      setState(() => _messages.add(_ChatItem(role: 'assistant', text: label)));
    }
  }

  String? _extractUrl(Map<String, dynamic> result) {
    final direct = result['url'];
    if (direct is String && direct.isNotEmpty) return _absoluteUrl(direct);
    final data = result['data'];
    if (data is List && data.isNotEmpty && data.first is Map) {
      final item = Map<String, dynamic>.from(data.first as Map);
      final value = item['url'] ?? item['audio_url'] ?? item['video_url'];
      if (value is String && value.isNotEmpty) return _absoluteUrl(value);
    }
    return null;
  }

  String _absoluteUrl(String value) {
    if (value.startsWith('http://') || value.startsWith('https://')) return value;
    final base = widget.ai.baseUrl.replaceFirst(RegExp(r'/$'), '');
    return value.startsWith('/') ? '$base$value' : '$base/$value';
  }

  String _friendlyError(Object error) {
    final text = error.toString();
    if (text.contains('SocketException') || text.contains('Connection')) {
      return 'Cannot reach the AI server. Check your API URL and server status.';
    }
    return text.replaceFirst('Exception: ', '');
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    });
  }

  void _newChat() {
    setState(() {
      _messages.clear();
      _error = null;
      _mode = _Mode.chat;
    });
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            const Icon(Icons.auto_awesome),
            const SizedBox(width: 10),
            const Expanded(child: Text('My AI')),
            PopupMenuButton<String>(
              tooltip: 'Model',
              onSelected: (value) => setState(() => _model = value),
              itemBuilder: (_) => const [
                PopupMenuItem(value: 'default', child: Text('Default')),
                PopupMenuItem(value: 'fast', child: Text('Fast')),
                PopupMenuItem(value: 'reasoning', child: Text('Reasoning')),
                PopupMenuItem(value: 'coding', child: Text('Coding')),
              ],
              child: const Icon(Icons.tune),
            ),
            IconButton(
              tooltip: 'New chat',
              onPressed: _busy ? null : _newChat,
              icon: const Icon(Icons.add_comment_outlined),
            ),
          ],
        ),
      ),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: _messages.isEmpty
                  ? _Welcome(mode: _modeLabel)
                  : ListView.builder(
                      controller: _scrollController,
                      padding: const EdgeInsets.fromLTRB(16, 20, 16, 12),
                      itemCount: _messages.length,
                      itemBuilder: (_, index) => _Bubble(item: _messages[index]),
                    ),
            ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
                ),
              ),
            _Composer(
              controller: _controller,
              mode: _mode,
              modeLabel: _modeLabel,
              modeIcon: _modeIcon,
              busy: _busy,
              onModeChanged: (mode) => setState(() => _mode = mode),
              onSend: _send,
              imageEnabled: _enabled('image'),
              videoEnabled: _enabled('video'),
              musicEnabled: _enabled('music'),
            ),
          ],
        ),
      ),
    );
  }
}

class _Welcome extends StatelessWidget {
  const _Welcome({required this.mode});
  final String mode;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.auto_awesome, size: 54, color: theme.colorScheme.primary),
            const SizedBox(height: 18),
            Text('What can I create for you?', style: theme.textTheme.headlineSmall, textAlign: TextAlign.center),
            const SizedBox(height: 8),
            Text('Currently in $mode mode. Type a prompt below.', textAlign: TextAlign.center),
          ],
        ),
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.item});
  final _ChatItem item;

  @override
  Widget build(BuildContext context) {
    final user = item.role == 'user';
    final theme = Theme.of(context);
    return Align(
      alignment: user ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 720),
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 12),
        decoration: BoxDecoration(
          color: user ? theme.colorScheme.primaryContainer : theme.colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(20),
        ),
        child: SelectableText(item.text),
      ),
    );
  }
}

class _Composer extends StatelessWidget {
  const _Composer({
    required this.controller,
    required this.mode,
    required this.modeLabel,
    required this.modeIcon,
    required this.busy,
    required this.onModeChanged,
    required this.onSend,
    required this.imageEnabled,
    required this.videoEnabled,
    required this.musicEnabled,
  });

  final TextEditingController controller;
  final _Mode mode;
  final String modeLabel;
  final IconData modeIcon;
  final bool busy;
  final ValueChanged<_Mode> onModeChanged;
  final VoidCallback onSend;
  final bool imageEnabled;
  final bool videoEnabled;
  final bool musicEnabled;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 12),
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: theme.colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(26),
          border: Border.all(color: theme.colorScheme.outlineVariant),
        ),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(8, 8, 8, 8),
          child: Column(
            children: [
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    _ModeChip(icon: Icons.auto_awesome, label: 'Chat', selected: mode == _Mode.chat, onTap: () => onModeChanged(_Mode.chat)),
                    if (imageEnabled) _ModeChip(icon: Icons.image_outlined, label: 'Image', selected: mode == _Mode.image, onTap: () => onModeChanged(_Mode.image)),
                    if (videoEnabled) _ModeChip(icon: Icons.movie_creation_outlined, label: 'Video', selected: mode == _Mode.video, onTap: () => onModeChanged(_Mode.video)),
                    if (musicEnabled) _ModeChip(icon: Icons.music_note_outlined, label: 'Music', selected: mode == _Mode.music, onTap: () => onModeChanged(_Mode.music)),
                  ],
                ),
              ),
              Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Padding(padding: const EdgeInsets.all(10), child: Icon(modeIcon)),
                  Expanded(
                    child: TextField(
                      controller: controller,
                      minLines: 1,
                      maxLines: 6,
                      enabled: !busy,
                      textCapitalization: TextCapitalization.sentences,
                      decoration: InputDecoration(
                        hintText: 'Message in $modeLabel mode…',
                        border: InputBorder.none,
                      ),
                      onSubmitted: (_) => onSend(),
                    ),
                  ),
                  IconButton.filled(
                    tooltip: 'Send',
                    onPressed: busy ? null : onSend,
                    icon: busy
                        ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.arrow_upward),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ModeChip extends StatelessWidget {
  const _ModeChip({required this.icon, required this.label, required this.selected, required this.onTap});
  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: FilterChip(
        selected: selected,
        avatar: Icon(icon, size: 18),
        label: Text(label),
        onSelected: (_) => onTap(),
      ),
    );
  }
}
