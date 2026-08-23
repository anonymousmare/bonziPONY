# CLAUDE.md — bonziPONY Codebase Guide

AI-powered Windows desktop pet, built as a 4CLOP advisor: voice interaction, autonomous
behavior, screen monitoring, desktop control, and an embedded game monitor. Built on PyQt5,
Whisper STT, ElevenLabs/PVT TTS, and multiple LLM backends.

Single character (Twilight Sparkle, unicorn), pinned to a screen corner. The multi-pony
system it was built on has been removed.

## Architecture Overview

```
main.py (bootstrap + wiring)
  │
  ├─ Activation Thread ─── wake word / PTT / double-click detection
  │       │
  │       └─ Pipeline Thread ─── IDLE → ACK → LISTEN → THINK → SPEAK → convo loop
  │               │
  │               ├─ stt/transcriber.py      Whisper STT
  │               ├─ llm/ providers          LLM call (chat/generate_once)
  │               ├─ llm/response_parser.py  extract tags from LLM output
  │               └─ core/tts_queue.py       enqueue speech (priority-ordered)
  │
  ├─ Agent Loop Thread ─── 1-3s ticks, autonomous behavior
  │       │
  │       ├─ core/screen_monitor.py    free window title polling (no LLM)
  │       ├─ directives.json           persistent tasks with urgency 1-10
  │       ├─ core/routines.py          scheduled recurring actions
  │       ├─ core/clop_thread.py       hourly 4chan read, gated before any LLM call
  │       └─ core/event_timeline.py    shared event log (thread-safe)
  │
  ├─ CLOP Bridge Thread ─── 60s polls of the game, via the bundled monitor
  │       │
  │       ├─ clop_monitor/            vendored; on sys.path, not a package
  │       ├─ core/clop_unread.py       what was missed while the user was away
  │       ├─ core/clop_dossier.py      what she has learned about other nations
  │       └─ PetSink → PetController → NotificationBox
  │
  ├─ TTSQueue Consumer Thread ─── serialized audio playback
  │       │
  │       └─ tts/ engines              ElevenLabs or OpenAI-compatible
  │
  └─ Main Thread (Qt) ─── GUI event loop
          │
          ├─ desktop_pet/pet_window.py     sprite animation (~60fps)
          ├─ desktop_pet/speech_bubble.py  comic-style response display
          ├─ desktop_pet/heard_text.py     STT transcription overlay
          ├─ desktop_pet/notification_box.py  relayed CLOP alerts, clickable
          └─ desktop_pet/context_menu.py   right-click settings UI
```

## Thread Safety Rules

| Component | Thread(s) | Sync mechanism |
|-----------|-----------|----------------|
| Pipeline | Activation thread spawns it | Isolated; one conversation at a time |
| Agent Loop | Own daemon thread | `_conversation_active` flag silences it during user interaction |
| TTSQueue | Any thread enqueues; consumer thread plays | `PriorityQueue` + `_seq_lock` |
| EventTimeline | Pipeline + Agent both write | `threading.Lock` on all reads/writes |
| TTS engine | Pipeline + TTSQueue both call speak() | **`_tts_lock` in main.py** wraps speak() |
| Qt GUI updates | Must happen on main thread | `PetController` uses Qt signals with `QueuedConnection` |

**Critical**: Never call Qt widget methods from background threads. Always go through PetController signals.

## File Ownership Map

### core/ — Brain and coordination
| File | Owns | Key class |
|------|------|-----------|
| `pipeline.py` | Conversation state machine (wake→listen→think→speak) | `Pipeline` |
| `agent_loop.py` | Autonomous behavior, directives, enforcement, AFK mischief | `AgentLoop` |
| `tts_queue.py` | Priority-ordered audio serialization | `TTSQueue` |
| `pony_manager.py` | One-element holder for the active character | `PonyManager` |
| `pony_instance.py` | Per-character state bundle (GUI + LLM + sprites + config) | `PonyInstance` |
| `clop_bridge.py` | Runs the CLOP monitor's poll loop in-process; alerts go to the box | `ClopBridge`, `PetSink` |
| `clop_unread.py` | Unread notifications, deduplicated and persisted, for the catch-up | `UnreadStore` |
| `clop_tools.py` | The lookups she can ask for instead of guessing | `LOOKUPS`, `ToolRegistry` |
| `clop_lore.py` | Game facts auto-injected when she mentions something | `context_for()` |
| `clop_thread.py` | The hourly thread read and the cheap gate in front of it | `ThreadState`, `decide()` |
| `clop_dossier.py` | What she has learned about other nations, persisted and stamped | `DossierStore`, `store()` |
| `warcalc.py` | Battle simulation, ported from the game's own combat loop | `simulate()`, `force_cost()` |
| `routines.py` | Persistent scheduled actions (wake/sleep/daily/weekly/interval) | `RoutineManager` |
| `event_timeline.py` | Shared event log bridging Pipeline and AgentLoop | `EventTimeline` |
| `screen_monitor.py` | Win32 window title polling (free, no API calls) | `ScreenMonitor` |
| `config_loader.py` | YAML config → typed dataclasses | `AppConfig` and sub-configs |
| `character_registry.py` | Scans Ponies/ dirs, maps slugs ↔ display names | `scan_ponies()` |
| `memory.py` | Session summaries persisted across restarts | `save_summary()`, `load_recent()` |
| `user_profile.py` | Extracted user facts (name, interests, events) | `load_profile()`, `update_from_conversation()` |
| `diary.py` | Per-character in-character journal | `write_entry()`, `read_recent()` |
| `monitor_utils.py` | Win32 multi-monitor bounds via ctypes | `get_monitor_rect_for_point()` |
| `audio_utils.py` | Audio device enumeration helpers | `list_pyaudio_devices()` |
| `updater.py` | Git-based self-update from GitHub | `check_for_updates()` |

### llm/ — LLM abstraction
| File | Owns |
|------|------|
| `base.py` | Abstract `LLMProvider` interface: `chat()`, `generate_once()`, `describe_image()` |
| `factory.py` | Provider routing: Anthropic, OpenAI, OpenRouter, DeepSeek, Groq, Ollama, local servers |
| `anthropic_provider.py` | Claude SDK with retry logic and vision support |
| `openai_provider.py` | OpenAI-compatible provider (handles 12+ backends) |
| `ollama_provider.py` | Local Ollama wrapper |
| `vision_provider.py` | Dedicated vision LLM with API key cycling (rate limit distribution) |
| `prompt.py` | System prompt generation from presets + relationship + user profile + desktop commands |
| `response_parser.py` | Tag extraction (`[ACTION]`, `[DESKTOP]`, `[DIRECTIVE]`, etc.) + TTS text sanitization |

### desktop_pet/ — GUI
| File | Owns |
|------|------|
| `pet_window.py` | Main transparent frameless always-on-top window, sprite rendering, roaming, drag |
| `pet_controller.py` | Thread-safe Qt signal bridge: pipeline thread → main thread |
| `sprite_manager.py` | GIF frame extraction, caching, scaling |
| `behavior_manager.py` | Parses `pony.ini` behavior definitions (CSV format from Desktop Ponies) |
| `effect_renderer.py` | Overlay visual effects (Sonic Rainboom, etc.) |
| `speech_bubble.py` | Comic-style bubble with typing animation, auto-hide, position tracking |
| `heard_text.py` | Translucent STT transcription overlay below pony |
| `notification_box.py` | Clickable alert panel above the pony, with a coloured trim and mark-as-read |
| `context_menu.py` | Right-click menu: full in-app settings, directive viewer |
| `countdown_timer.py` | On-screen timer widget for enforcement tasks |

### stt/ — Speech-to-text
| File | Owns |
|------|------|
| `transcriber.py` | Whisper STT with energy-based VAD. Two modes: `listen()` (auto-silence) and `listen_ptt()` (push-to-talk) |
| `mic_lock.py` | Global threading.Lock preventing PyAudio heap corruption from concurrent init/exit |

### tts/ — Text-to-speech
| File | Owns |
|------|------|
| `elevenlabs_tts.py` | ElevenLabs cloud TTS via SDK. PCM playback via sounddevice |
| `openai_compatible_tts.py` | OpenAI-compatible `/v1/audio/speech` endpoint (ponyvoicetool, AllTalk, etc.). Built-in voice map for 25+ MLP characters |

### Other directories
| Directory | Purpose |
|-----------|---------|
| `wake_word/detector.py` | Whisper-based offline keyword spotting for per-character wake phrases |
| `robot/desktop_controller.py` | Windows desktop automation (pyautogui, pywin32). Security: blocked hotkeys, allowlisted apps |
| `robot/actions.py` | `RobotAction` enum (walk, sit, wave, volume, window ops) |
| `vision/screen.py` | Screenshot capture via mss |
| `vision/camera.py` | Webcam capture via OpenCV |
| `vision/watch_mode.py` | CLIP + OCR continuous screen understanding (zero API cost) |
| `acknowledgement/player.py` | Plays per-character beep/chime on wake word detection |
| `clop_monitor/clop_pages.py` | Parsers for `viewnation.php`, `viewalliance.php`, `messages.php`, `news.php` |
| `clop_monitor/fixtures/` | Page HTML rendered by the game's own PHP templates, plus the generators |
| `presets/` | Character personality .txt files (system prompts). `_template.txt` for auto-generation |
| `Ponies/` | 311+ Desktop Ponies sprite packs (pony.ini + GIFs) |
| `memory/` | `user_profile.txt`, `user_events.txt`, `sessions.txt` |
| `diary/` | Per-character journal files |
| `scripts/` | `list_audio_devices.py`, `test_pipeline.py` |

## Key Data Flow

### User speaks → pony responds
```
Wake word detected (wake_word/detector.py)
  → Pipeline.run_conversation()
    → AcknowledgementPlayer.play()           # immediate audio feedback
    → Transcriber.listen()                   # record + Whisper STT
    → LLMProvider.chat(user_text)            # LLM call with history
    → parse_response(raw)                    # extract tags + clean text
    → TTSQueue.enqueue(text, blocking=True)  # blocks until audio done
    → DesktopController.execute(commands)    # run [DESKTOP:...] tags
    → [conversation mode: wait for follow-up speech for timeout_s]
```

### Autonomous speech (agent loop)
```
AgentLoop.tick()
  → check directives, screen changes, idle time
  → LLMProvider.generate_once(context_prompt)
  → parse_response(raw)
  → AgentLoop._speak(text)                   # enqueue with PRIORITY_AUTONOMOUS, blocking=True
  → _listen_for_reply()                      # wait for user response
```

### CLOP alert → notification box
```
ClopBridge poll thread (60s)
  → clop_monitor.check_and_notify(client, previous, PetSink, ...)
    → build_alerts(previous, current)      # pure; no Windows, no I/O
    → PetSink.notify(message, alerts)
      → alert_parts(alert)                 # {title, body, url, category, colour}
      → UnreadStore.add(payload)           # deduplicated; level alerts re-fire every poll
      → PetController.on_notification(payload)
        → notification_received signal (QueuedConnection)
          → NotificationBox.push(payload)  # main thread
  → _notice_market_nations(current)
    → dossier.notice(order.nation_id, ...)   # from the snapshot, not the alert text
```

### Reading another nation
```
[LOOKUP:nation:47]
  → ToolRegistry.dispatch → get_nation("47")
    → dossier fresh?  → render the stored reading, no fetch
    → otherwise ClopBridge.nation(47) → clop_pages.parse_nation(html)
      → dossier.record_nation(nation)
        → Force.as_warcalc() feeds core.warcalc.simulate directly
```

### Hourly thread check
```
AgentLoop.tick() → _check_routines()
  → routine goal "__task:clop_thread"      # not a directive; background work
    → _check_clop_thread() on its own thread
      → clop_thread.decide(state, posts)   # cheap gate: arithmetic, no model
      → (only if it says read) sanitize + render new posts → generate_once
      → speak, or [PASS] and stay silent
```

## LLM Response Tag System

The LLM embeds structured tags in its natural language response. `response_parser.py` extracts them and strips them from TTS text.

| Tag | Purpose | Example |
|-----|---------|---------|
| `[ACTION:name]` | Trigger sprite animation | `[ACTION:WALK_FORWARD]` |
| `[DESKTOP:cmd:args]` | Desktop automation | `[DESKTOP:BROWSE:youtube.com]`, `[DESKTOP:HOTKEY:ctrl:w]` |
| `[DIRECTIVE:goal:urgency]` | Create persistent task | `[DIRECTIVE:go to the gym:7]` |
| `[DIRECTIVE:goal:urgency:delay]` | Delayed directive | `[DIRECTIVE:take meds:8:30]` (30 min delay) |
| `[TIMER:HH:MM:action]` | One-shot scheduled action | `[TIMER:21:00:remind user to sleep]` |
| `[ROUTINE:schedule:goal:urgency]` | Recurring schedule | `[ROUTINE:daily:09:00:check calendar:5]` |
| `[ENFORCE:minutes]` | Monitor task completion | `[ENFORCE:15]` |
| `[DELAY:minutes:keyword]` | Postpone a directive | `[DELAY:30:gym]` |
| `[DONE:keyword]` | Mark directive complete | `[DONE:gym]` |
| `[CONVO:END\|CONTINUE]` | Conversation flow signal | `[CONVO:END]` |
| `[PERSIST:seconds]` | Hold animation N seconds | `[PERSIST:600]` |
| `[MOVETO:region]` | Move pony to screen area | `[MOVETO:top_left]` |
| `[RULE:description]` | Create standing behavioral rule | `[RULE:quit porn]` |
| `[LOOKUP:query]` | Ask for real game numbers | `[LOOKUP:Coffee Farm]`, `[LOOKUP:pollution:Oil Fracker:14]` |
| `[WARCALC:a vs b]` | Simulate a battle | `[WARCALC:40 Unicorns/Grid Squares/Shining/12 vs 60 Pegasi]` |

The lookups themselves are listed by `ToolRegistry.prompt_block()`, generated from `LOOKUPS`
so the prompt can never offer one that is switched off or whose bridge is down. Live ones
(`stockpiles`, `status`, `market`, `thread`, `nation`, `alliance`, `messages`,
`alliance_messages`, `news`) need the bridge connected; `dossier` reads a file, so it still
answers when the game is unreachable — which is when knowing what she already learned is
most useful.

## Directive System

Directives are persistent goals stored in `directives.json`. Created by LLM via `[DIRECTIVE:goal:urgency]` tag.

- **Urgency 1-6**: Verbal nagging at timed intervals
- **Urgency 7-9**: High priority — shorter intervals, window shaking, closing distracting apps
- **Urgency 10**: Burst mode — nags every 15-45 seconds (for demos/presentations)
- Directives survive restarts. Agent loop checks and fires them each tick.
- Standing rules (`[RULE:...]`) are separate — regex patterns auto-matched against window titles every tick (no LLM call for detection).

## Configuration

- **`config.yaml`** — Main config. Sections: `llm`, `tts`, `stt`, `wake_word`, `conversation`, `vision`, `vision_llm`, `agent`, `desktop_control`, `audio`, `logging`
- **`directives.json`** — Active task directives + standing rules (managed by AgentLoop)
- **`routines.json`** — Recurring scheduled actions (managed by RoutineManager)
- **`wake_state.json`** — Tracks wake/sleep state across restarts
- **`presets/*.txt`** — Per-character system prompts. `_template.txt` for auto-generation
- **`memory/`** — User profile, events, session summaries (injected into system prompt at runtime)
- **`.env`** — Optional environment variable overrides for API keys

## Conventions and Patterns

### LLM provider interface
All providers implement `LLMProvider` (llm/base.py):
- `chat(user_message) → str` — Multi-turn with history (conversation mode)
- `generate_once(prompt, max_tokens, system_prompt) → str` — One-shot, no history impact (utility tasks)
- `describe_image(jpeg_bytes) → str | None` — Vision call
- `inject_history(user_msg, assistant_msg)` — Add exchange without API call
- `reset_history()` — Clear conversation state

### TTS blocking semantics
- `blocking=True` → caller blocks until audio finishes (used for user-response path so mic doesn't reopen during speech)
- `blocking=False` (default) → fire-and-forget enqueue
- Pipeline user-response: always blocking
- Agent loop `_speak()`: blocking (to prevent IDLE state race)
- Notification relay: `PRIORITY_NOTIFICATION`, non-blocking (blocking would tie the monitor thread to playback speed)

### Echo detection
Pipeline tracks `_recently_spoken` list. When Whisper transcribes the pony's own TTS output back through the mic, it's filtered by substring match + word overlap (>60% threshold).

### Window title sanitization (prompt injection defense)
Agent loop strips control characters, truncates to 120 chars, and removes bracket expressions from window titles before passing to LLM. This prevents malicious window titles from injecting tags.

### Qt thread marshaling
All GUI updates go through `PetController` Qt signals with `QueuedConnection`. The one exception is `speech_text` which uses `BlockingQueuedConnection` so the pipeline knows when the bubble is shown.

## Gotchas and Warnings

1. **PyAudio heap corruption**: Never open two `sr.Microphone()` contexts simultaneously. `stt/mic_lock.py` exists specifically for this — always use it.

2. **TTS lock**: `main.py` wraps the TTS engine's `speak()` in a `threading.Lock`. If you add a new speech path, make sure it goes through TTSQueue or acquires this lock.

3. **`_current_item` timing in tts_queue.py**: `_current_item` is set BEFORE the breathing pause, not after. This was a bug fix — don't move it back or `is_speaking` will report False during the gap.

4. **Desktop Ponies pony.ini format**: CSV-based, not INI. The `behavior_manager.py` parser is fragile with edge cases. Don't assume standard INI parsing.

5. **Preset files are large**: Character presets (presets/*.txt) are 16-28KB system prompts. They contain the full personality, available commands, relationship framing, and behavioral rules. Changes here affect everything.

6. **`generate_once` vs `chat`**: `generate_once` is for utility tasks (summarization, profile extraction, AFK decisions). It does NOT affect conversation history. `chat` is for actual conversation turns and maintains history.

7. **Standing rules use regex**: When a standing rule is created, the LLM generates regex patterns at creation time. Detection is pure regex matching on window titles — no LLM calls per tick.

8. **Vision key cycling**: `vision_provider.py` distributes requests across multiple API keys to stay under per-key rate limits. The key index rotates on each call.

9. **Wake state detection**: `routines.py` distinguishes program restart (same day or <4 hours gap) from actual wake-up (>4 hours gap). Don't lower the gap threshold or wake routines will fire on every restart.

10. **Agent loop is silenced during conversation**: When `_conversation_active` is True, the agent loop skips all speech. Pipeline sets this flag. If you add a new speech path in agent_loop, check this flag.

11. **Pinning is not "roaming off"**: the behavior-duration check lives inside the roaming
    branch of `PetWindow._on_tick`, so clearing `_roaming` freezes her on one animation
    forever. `_pinned` replaces `_move_tick` instead, and re-asserts every tick because the
    window resizes itself to each animation frame and is anchored top-left.

12. **`_STATE_ANIMATION_MAP` names must exist**: a missing animation silently falls back to
    `stand`, so a map full of animations the character lacks renders every state identically
    without ever erroring.

13. **Two alert kinds are level-triggered**: unread-message counts and market buy orders
    re-fire on *every* monitor poll while the condition holds, not once when it starts.
    Anything accumulating them must deduplicate, and must count distinct items rather than
    arrivals. `core/clop_unread.py` does both.

14. **`PetSink.notify` must return False**: the monitor reads True as "a blocking dialog was
    shown and dismissed" and re-reads its snapshot on that basis.

15. **Interval routines are exempt from the once-a-day guard** in `routines.py`. That guard
    is for wall-clock schedules; applying it to an interval made hourly routines fire once
    near midnight and then die until the next day.

16. **Thread posts are attacker-controlled**: sanitize before they reach a prompt, the same
    way window titles are. `core/clop_thread.sanitize` strips control chars and bracket
    expressions so a tag in a post cannot become a command.

17. **`gamedata.json` is generated, not hand-edited.** Regenerate with
    `python3 tools/export_gamedata.py` in the CLOP checkout and copy it to `data/`.

18. **Lookups are a tag, not an API feature, on purpose.** Native function calling was
    tried and removed. DeepSeek — which this is built to run on, via nano-gpt — emits tool
    calls as plain text in `content` instead of `tool_calls` about 11% of the time, and
    `OpenAIProvider` would swallow that into an empty string and corrupt `_history`. A tag
    cannot fall through to text mode because text is the mode. Do not "upgrade" this to
    `tools=`.

19. **The lorebook guard is deliberate.** `clop_lore.AMBIGUOUS` names match
    case-sensitively because `Nope`, `Wonder`, `Bar`, `Dragon` and `Titan` are all real
    equipment *and* ordinary English. The set is kept tight: a false positive costs ~150
    wasted tokens, a false negative costs her the numbers.

20. **A nested match is skipped.** "coffee farm" is a Coffee Farm, not a Coffee Farm plus
    some Coffee. `find_mentions` tracks claimed spans; sorting by length alone is not enough.

21. **Two lookup paths, two shapes.** `Pipeline._resolve_lookups` rewinds `_history` (two
    pops — `chat()` re-adds the user turn); `AgentLoop._resolve_lookups` has no history to
    rewind because `generate_once` is one prompt. Both are round-bounded.

22. **`messages.php` is safe to read; `myalliance.php` is not.** Personal messages only flip
    `is_read` on a POST (`backend_messages.php:108-112`), so fetching the inbox changes
    nothing. Alliance chat marks itself read on the GET
    (`backend_myalliance.php:231`) — for the account, so it looks read in the user's own
    browser too. That is why `read_alliance_messages` exists as a config flag and why
    `alliance_messages` is the one lookup that can be switched off entirely.

23. **The page fixtures are PHP-generated, not hand-written.** `clop_monitor/fixtures/*.html`
    come from the game's own templates via `gen_nation.php` / `gen_rest.php`; see the
    README there for how to regenerate them. A hand-written fixture only proves the parser
    agrees with whoever wrote the fixture.

24. **Never derive another nation's economy from its building counts.** `viewnation.php`
    renders a Generated / Used / Net table that the game computes itself, government upkeep
    included — which no count of buildings can see. `Nation.economy_rows` is that table.
    The fixture pins `Gasoline (0, 10, -10)`: nothing on the page produces or consumes it.

25. **A hostile force on a nation page is not part of its defence.** `viewnation.php` lists
    occupying armies alongside the garrison. `Force.hostile` separates them, and anything
    feeding `warcalc.simulate` must filter on it or it will count the invaders as defenders.

26. **`clop_dossier.store()` only applies `max_age_hours` when asked.** The bridge sets it
    from config; the lookup layer asks for the store with no opinion. An unconditional
    assignment there quietly reset a configured staleness window back to the default.

## Testing

```bash
python -m unittest discover -s tests    # 58 tests: lorebook, lookups, dossier
cd clop_monitor && python -m unittest   # 615 tests: the monitor's own suite
python -m py_compile <file.py>          # everything else
```
`tests/test_lookup_roundtrip.py` drives the lookup path with a hand-written stub provider
and asserts `anthropic` was never imported — that is the claim it exists to protect.
`tests/test_lookup_reachability.py` is structural: every `LOOKUPS` row must resolve to a real
callable and every tool in `make_live_tools` must have a row. It exists because the same bug
happened twice — tools written, registered in one place, never connected to what calls them.
Its `RenderingTests` class covers the other half: reachable and correct are two claims.
For integration testing, use `scripts/test_pipeline.py` which tests STT → LLM → TTS stages individually.

## Persistent State Files

| File | Written by | Survives restart | Format |
|------|-----------|-----------------|--------|
| `directives.json` | AgentLoop | Yes | `{"directives": [...], "enforcement": null, "standing_rules": [...]}` |
| `routines.json` | RoutineManager | Yes | `[{"id": ..., "schedule": ..., "goal": ..., ...}]` |
| `clop_dossier.json` | DossierStore | Yes | `{"nations": {...}, "alliances": {...}, "seen": {...}}` |
| `wake_state.json` | RoutineManager | Yes | `{"wake_time": ISO, "last_active": ISO}` |
| `memory/sessions.txt` | Pipeline (summarize_session) | Yes | Plain text, last 3 sessions |
| `memory/user_profile.txt` | user_profile.py | Yes | Structured text |
| `memory/user_events.txt` | user_profile.py | Yes | Structured text |
| `diary/*.txt` | diary.py | Yes | Timestamped journal entries |
| `config.yaml` | context_menu.py (settings UI) | Yes | YAML with comments |
