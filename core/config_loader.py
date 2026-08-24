"""Loads config.yaml and exposes typed dataclasses."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class WakeWordConfig:
    enabled: bool = True               # set False for PTT-only mode (no wake words)
    phrases: Dict[str, List[str]] = field(default_factory=dict)  # preset_slug -> wake phrases
    language: str = "en"
    model: str = "base"  # Whisper model for wake word detection


@dataclass
class AudioConfig:
    input_device_index: int = -1
    output_device_index: int = -1
    vad_aggressiveness: int = 2
    silence_duration_ms: int = 800
    ptt_key: str = "f6"  # push-to-talk key (hold to record, release to send)


@dataclass
class WhisperConfig:
    model: str = "tiny"
    language: str = "en"


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    temperature: float = 0.85
    max_tokens: int = 1500
    max_history_turns: int = 10
    base_url: Optional[str] = None
    preset: str = "rainbow_dash"
    prefill: str = ""                    # custom assistant prefill (empty = default)
    relationship: str = "lover"          # lover | best_friend | roommate | caretaker | custom
    relationship_custom: str = ""        # free-text (only when relationship="custom")


@dataclass
class ElevenLabsConfig:
    api_key: str = ""
    voice_id: str = ""
    model: str = "eleven_turbo_v2"
    output_format: str = "pcm_22050"


@dataclass
class TTSConfig:
    enabled: bool = True               # set False to disable TTS entirely
    provider: str = "elevenlabs"       # "elevenlabs" or "openai_compatible"
    base_url: str = "http://localhost:8069/v1"
    model: str = "ponyvoicetool"
    voice: str = "default"
    response_format: str = "pcm"
    sample_rate: int = 24000


@dataclass
class ConversationConfig:
    timeout_s: float = 60.0          # seconds to stay in conversation mode after Dash speaks
    listen_timeout_s: float = 4.0    # seconds to wait for user to START speaking in follow-up
    random_speech_chance: float = 0.001  # probability per second of unprompted speech (0.1%)


@dataclass
class VisionConfig:
    enabled: bool = True
    device_index: int = 0
    screen_capture: bool = True
    screen_max_width: int = 1280
    screen_vision: str = "api"  # "api" = main LLM describe_screen, "moondream" = local model


@dataclass
class VisionLLMConfig:
    """Separate LLM for vision (screen/image description). Top-level config."""
    enabled: bool = False
    provider: str = "gemini"
    model: str = "gemini-2.5-flash"
    api_keys: List[str] = field(default_factory=list)
    base_url: Optional[str] = None
    max_requests_per_key_per_day: int = 100
    temperature: float = 0.3
    max_tokens: int = 2048              # max tokens for describe_screen (high for Gemini thinking overhead)
    locate_max_tokens: int = 200        # max tokens for locate_on_screen output


@dataclass
class DesktopControlConfig:
    enabled: bool = True
    allowed_apps: List[str] = field(default_factory=lambda: ["notepad", "calculator", "explorer"])
    blocked_hotkeys: List[str] = field(default_factory=lambda: ["ctrl:alt:delete"])
    click_enabled: bool = True
    type_enabled: bool = True


@dataclass
class AgentConfig:
    enabled: bool = True
    self_initiate: bool = True
    max_directives: int = 3
    activity_multiplier: float = 1.0          # scales ALL timing (0.1 = hyper, 6.0 = chill)
    base_check_interval_s: float = 300.0
    min_check_interval_s: float = 30.0
    self_initiate_interval_s: float = 300.0
    spontaneous_speech_min_s: float = 120.0   # minimum seconds between random check-ins
    spontaneous_speech_max_s: float = 300.0   # maximum seconds between random check-ins
    sustained_focus_threshold_s: float = 900.0
    #: Let her ask after the user themselves when nothing else is going on -- have you eaten,
    #: had water, been outside, what's on your plate today. Off leaves her the in-character
    #: half of the same idle chatter: what she is thinking about, an opinion, a complaint.
    #: Timing, frequency and every other trigger are unchanged either way; this only decides
    #: which half of the pool an idle remark is drawn from.
    check_ins: bool = True
    distraction_keywords: List[str] = field(default_factory=lambda: [
        "youtube", "reddit", "tiktok", "twitch", "twitter", "instagram", "facebook",
    ])


@dataclass
class WatchModeConfig:
    enabled: bool = False
    capture_interval: float = 2.5
    scene_change_threshold: float = 0.85
    clip_model: str = "openai/clip-vit-base-patch32"
    ocr_engine: str = "winocr"
    subtitle_region_pct: float = 0.20
    use_gpu: bool = False


@dataclass
class RobotConfig:
    enabled: bool = False
    controller: str = "stub"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    log_to_file: bool = True
    log_file: str = "logs/rainbow_dash.log"


@dataclass
class DesktopPetConfig:
    enabled: bool = True
    scale: float = 2.0
    speech_bubble: bool = True
    font_style: str = "default"        # "default" (Segoe UI) | "m5x7" (pixel)
    typewriter_sound: bool = True      # click-click while bubble types
    #: Screen region to hold, or None to let her wander as a desktop pet normally does.
    #: An advisor that relays notifications wants to be findable in the same place every
    #: time, and the notification box is drawn directly above her.
    #: Show what the microphone heard, under her, while she thinks about it. This is the
    #: transcription, not what you typed -- a typed message is never echoed back, because you
    #: watched yourself write it.
    heard_text: bool = True
    pin_to: Optional[str] = "bottom_right"
    pin_margin: int = 12               # pixels from the screen's work-area edge


@dataclass
class ClopConfig:
    """The 4CLOP monitor that runs inside this process.

    ``monitor_path`` points at the bundled ``clop_monitor/`` directory. It is vendored rather
    than imported from a sibling checkout so this is one clone and one thing to run -- but it
    is still a plain directory on ``sys.path`` rather than a package, because its modules
    import each other by bare name and turning that into relative imports would mean rewriting
    every module and all seven test files for no functional gain.

    Point it elsewhere to run against a development checkout. A path that does not resolve is
    not fatal: CLOP features switch off and the pet runs on.
    """
    enabled: bool = False
    monitor_path: str = "clop_monitor"
    base_url: str = "https://4clop.org/"
    #: All default to the monitor checkout's own files when left unset.
    settings_file: Optional[str] = None
    env_file: Optional[str] = None
    state_file: Optional[str] = None
    poll_interval_s: int = 60
    #: never | high | always. The box is silent by default: at a 60s poll, speaking every
    #: alert would mean talking over the user all day. She still speaks the welcome-back
    #: catch-up and her own unprompted remarks regardless of this.
    speak_notifications: str = "never"
    #: off | rare | on. How often she volunteers a read on the user's position.
    strategy_thoughts: str = "rare"
    #: How often she reads the configured 4chan thread, in hours. 0 disables it.
    thread_check_hours: float = 1.0
    #: Let her read the alliance chat. Opening myalliance.php runs
    #: UPDATE users SET alliance_messages_last_checked = NOW(), so reading it marks the
    #: whole chat read for the account -- in your own browser too. On means she can tell
    #: you what the alliance said; off means she can only ever tell you how many there are.
    read_alliance_messages: bool = True
    #: How long a reading of another nation stays trustworthy. Garrisons only change on
    #: war ticks, twelve hours apart, so half that is about its useful life.
    dossier_max_age_hours: float = 6.0
    #: How long the roster of who exists -- every nation, from the four regional rankings
    #: pages -- stays good for. Nations are founded and die on a scale of days, and the
    #: pages are public and unchanged by being read, so this is only about how soon somebody
    #: brand new shows up in her phone book.
    roster_max_age_hours: float = 12.0
    #: Update the shared CLOP planning sheet each poll, the way the standalone monitor does.
    #: The rule is the monitor's: on whenever CLOP_NATION names a tab in that sheet, off when
    #: it does not. The writing is the monitor's own sync_sheet_step, so what lands in the tab
    #: is identical either way. Set this false to keep the sync in your own clop_monitor.py
    #: run instead -- two processes writing one tab is the only way this can go wrong.
    sheet_sync: bool = True
    #: When she runs a [WARCALC:...] during a conversation, also open tools/warcalc.html with
    #: that battle already filled in, so the user can change the numbers themselves instead of
    #: asking her for every variation. Only ever from a conversation -- the agent loop never
    #: opens a window on its own.
    warcalc_page: bool = True
    #: Find the current /mlp/ general from the board catalog instead of being told where
    #: it is. Threads archive in a day or two, so a URL in a settings file is stale more
    #: often than not -- and a stale one reads to the user as "she cannot check the thread".
    thread_auto_find: bool = True
    thread_board: str = "mlp"
    #: Pin her to one thread instead of letting her find it. Empty means find it. Set from
    #: the settings menu; it lives here rather than in the monitor's settings.json so the
    #: vendored checkout stays untouched.
    thread_url: str = ""


@dataclass
class SafetyConfig:
    """Read-only / safety controls. When read_only_mode is True, the agent
    loop skips all invasive behaviors: AFK mischief, force-escalation
    (LOCK_MOUSE/MESS_MOUSE/ALT_TAB), standing-rule window closing, desktop
    command dispatch, updater, and cloud LLM/TTS providers."""
    read_only_mode: bool = False


@dataclass
class AppConfig:
    wake_word: WakeWordConfig
    audio: AudioConfig
    whisper: WhisperConfig
    llm: LLMConfig
    elevenlabs: ElevenLabsConfig
    conversation: ConversationConfig
    vision: VisionConfig
    robot: RobotConfig
    logging: LoggingConfig
    desktop_pet: DesktopPetConfig = None
    desktop_control: DesktopControlConfig = None
    agent: AgentConfig = None
    watch_mode: WatchModeConfig = None
    tts: TTSConfig = None
    vision_llm: VisionLLMConfig = None
    safety: SafetyConfig = None
    clop: ClopConfig = None
    auto_update: bool = False              # auto-pull git updates on launch (off by default)
    presentation_mode: bool = False        # secret: unlocks demo/presentation menu

    def __post_init__(self):
        if self.desktop_pet is None:
            self.desktop_pet = DesktopPetConfig()
        if self.desktop_control is None:
            self.desktop_control = DesktopControlConfig()
        if self.tts is None:
            self.tts = TTSConfig()
        if self.agent is None:
            self.agent = AgentConfig()
        if self.watch_mode is None:
            self.watch_mode = WatchModeConfig()
        if self.vision_llm is None:
            self.vision_llm = VisionLLMConfig()
        if self.safety is None:
            self.safety = SafetyConfig()
        if self.clop is None:
            self.clop = ClopConfig()


def _parse_vision_llm(raw: dict | None) -> VisionLLMConfig | None:
    """Parse the optional vision_llm sub-config."""
    if not raw or not isinstance(raw, dict):
        return None
    keys = raw.get("api_keys", [])
    if not keys:
        # Single key fallback
        single = raw.get("api_key", "")
        if single:
            keys = [single]
    if not keys:
        return None
    return VisionLLMConfig(
        enabled=raw.get("enabled", True),
        provider=raw.get("provider", "gemini"),
        model=raw.get("model", "gemini-2.5-flash"),
        api_keys=keys,
        base_url=raw.get("base_url"),
        max_requests_per_key_per_day=raw.get("max_requests_per_key_per_day", 100),
        temperature=raw.get("temperature", 0.3),
        max_tokens=raw.get("max_tokens", 2048),
        locate_max_tokens=raw.get("locate_max_tokens", 200),
    )


def load_config(path: Path | str = "config.yaml") -> AppConfig:
    """Load and parse config.yaml into AppConfig."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            "Copy config.yaml.example to config.yaml and fill in your keys."
        )

    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    ww_raw = raw.get("wake_word", {})
    audio_raw = raw.get("audio", {})
    whisper_raw = raw.get("whisper", {})
    llm_raw = raw.get("llm", {})
    el_raw = raw.get("elevenlabs", {})
    conv_raw = raw.get("conversation", {})
    vision_raw = raw.get("vision", {})
    robot_raw = raw.get("robot", {})
    log_raw = raw.get("logging", {})
    pet_raw = raw.get("desktop_pet", {})
    dc_raw = raw.get("desktop_control", {})
    agent_raw = raw.get("agent", {})
    wm_raw = raw.get("watch_mode", {})
    tts_raw = raw.get("tts", {})
    vlm_raw = raw.get("vision_llm", {})
    safety_raw = raw.get("safety", {})
    clop_raw = raw.get("clop", {})
    auto_update = bool(raw.get("auto_update", False))
    presentation_mode = raw.get("presentation_mode", False)

    # ── Env var fallbacks for secrets (config.yaml wins, env is backup) ─────
    llm_api_key = llm_raw.get("api_key", "") or os.environ.get("BONZI_LLM_API_KEY", "")
    llm_provider = llm_raw.get("provider", "") or os.environ.get("BONZI_LLM_PROVIDER", "openai")
    llm_base_url = llm_raw.get("base_url") or os.environ.get("BONZI_LLM_BASE_URL") or None
    el_api_key = el_raw.get("api_key", "") or os.environ.get("BONZI_ELEVENLABS_API_KEY", "")
    el_voice_id = el_raw.get("voice_id", "") or os.environ.get("BONZI_ELEVENLABS_VOICE_ID", "")

    return AppConfig(
        wake_word=WakeWordConfig(
            enabled=ww_raw.get("enabled", True),
            phrases=ww_raw.get("phrases", {}),
            language=ww_raw.get("language", "en"),
            model=ww_raw.get("model", "base"),
        ),
        audio=AudioConfig(
            input_device_index=audio_raw.get("input_device_index", -1),
            output_device_index=audio_raw.get("output_device_index", -1),
            vad_aggressiveness=audio_raw.get("vad_aggressiveness", 2),
            silence_duration_ms=audio_raw.get("silence_duration_ms", 800),
            ptt_key=audio_raw.get("ptt_key", "f6"),
        ),
        whisper=WhisperConfig(
            model=whisper_raw.get("model", "tiny"),
            language=whisper_raw.get("language", "en"),
        ),
        llm=LLMConfig(
            provider=llm_provider,
            model=llm_raw.get("model", "gpt-4o"),
            api_key=llm_api_key,
            temperature=llm_raw.get("temperature", 0.85),
            max_tokens=llm_raw.get("max_tokens", 1500),
            max_history_turns=llm_raw.get("max_history_turns", 10),
            base_url=llm_base_url,
            preset=llm_raw.get("preset", "rainbow_dash"),
            prefill=llm_raw.get("prefill", ""),
            relationship=llm_raw.get("relationship", "lover"),
            relationship_custom=llm_raw.get("relationship_custom", ""),
        ),
        elevenlabs=ElevenLabsConfig(
            api_key=el_api_key,
            voice_id=el_voice_id,
            model=el_raw.get("model", "eleven_turbo_v2"),
            output_format=el_raw.get("output_format", "pcm_22050"),
        ),
        conversation=ConversationConfig(
            timeout_s=conv_raw.get("timeout_s", 60.0),
            listen_timeout_s=conv_raw.get("listen_timeout_s", 4.0),
            random_speech_chance=conv_raw.get("random_speech_chance", 0.001),
        ),
        vision=VisionConfig(
            enabled=vision_raw.get("enabled", True),
            device_index=vision_raw.get("device_index", 0),
            screen_capture=vision_raw.get("screen_capture", True),
            screen_max_width=vision_raw.get("screen_max_width", 1280),
            screen_vision=vision_raw.get("screen_vision", "api"),
        ),
        robot=RobotConfig(
            enabled=robot_raw.get("enabled", False),
            controller=robot_raw.get("controller", "stub"),
        ),
        logging=LoggingConfig(
            level=log_raw.get("level", "INFO"),
            log_to_file=log_raw.get("log_to_file", True),
            log_file=log_raw.get("log_file", "logs/rainbow_dash.log"),
        ),
        desktop_pet=DesktopPetConfig(
            enabled=pet_raw.get("enabled", True),
            scale=pet_raw.get("scale", 2.0),
            speech_bubble=pet_raw.get("speech_bubble", True),
            font_style=pet_raw.get("font_style", "default"),
            typewriter_sound=pet_raw.get("typewriter_sound", True),
            heard_text=bool(pet_raw.get("heard_text", True)),
            pin_to=pet_raw.get("pin_to", "bottom_right"),
            pin_margin=pet_raw.get("pin_margin", 12),
        ),
        desktop_control=DesktopControlConfig(
            enabled=dc_raw.get("enabled", True),
            allowed_apps=dc_raw.get("allowed_apps", ["notepad", "calculator", "explorer"]),
            blocked_hotkeys=dc_raw.get("blocked_hotkeys", ["ctrl:alt:delete"]),
            click_enabled=dc_raw.get("click_enabled", True),
            type_enabled=dc_raw.get("type_enabled", True),
        ),
        agent=AgentConfig(
            enabled=agent_raw.get("enabled", True),
            self_initiate=agent_raw.get("self_initiate", True),
            max_directives=agent_raw.get("max_directives", 3),
            base_check_interval_s=agent_raw.get("base_check_interval_s", 120.0),
            min_check_interval_s=agent_raw.get("min_check_interval_s", 30.0),
            self_initiate_interval_s=agent_raw.get("self_initiate_interval_s", 300.0),
            spontaneous_speech_min_s=agent_raw.get("spontaneous_speech_min_s", 120.0),
            spontaneous_speech_max_s=agent_raw.get("spontaneous_speech_max_s", 300.0),
            sustained_focus_threshold_s=agent_raw.get("sustained_focus_threshold_s", 900.0),
            check_ins=bool(agent_raw.get("check_ins", True)),
            distraction_keywords=agent_raw.get("distraction_keywords", [
                "youtube", "reddit", "tiktok", "twitch", "twitter", "instagram", "facebook",
            ]),
        ),
        watch_mode=WatchModeConfig(
            enabled=wm_raw.get("enabled", False),
            capture_interval=wm_raw.get("capture_interval", 2.5),
            scene_change_threshold=wm_raw.get("scene_change_threshold", 0.85),
            clip_model=wm_raw.get("clip_model", "openai/clip-vit-base-patch32"),
            ocr_engine=wm_raw.get("ocr_engine", "winocr"),
            subtitle_region_pct=wm_raw.get("subtitle_region_pct", 0.20),
            use_gpu=wm_raw.get("use_gpu", False),
        ),
        tts=TTSConfig(
            enabled=tts_raw.get("enabled", True),
            provider=tts_raw.get("provider", "elevenlabs"),
            base_url=tts_raw.get("base_url", "http://localhost:8069/v1"),
            model=tts_raw.get("model", "ponyvoicetool"),
            voice=tts_raw.get("voice", "default"),
            response_format=tts_raw.get("response_format", "pcm"),
            sample_rate=tts_raw.get("sample_rate", 24000),
        ),
        vision_llm=_parse_vision_llm(vlm_raw or None),
        safety=SafetyConfig(
            read_only_mode=bool(safety_raw.get("read_only_mode", False)),
        ),
        clop=ClopConfig(
            enabled=bool(clop_raw.get("enabled", False)),
            monitor_path=clop_raw.get("monitor_path", "../clop-alerts-windownotifs"),
            base_url=clop_raw.get("base_url", "https://4clop.org/"),
            settings_file=clop_raw.get("settings_file"),
            env_file=clop_raw.get("env_file"),
            state_file=clop_raw.get("state_file"),
            poll_interval_s=int(clop_raw.get("poll_interval_s", 60)),
            speak_notifications=str(clop_raw.get("speak_notifications", "never")),
            strategy_thoughts=str(clop_raw.get("strategy_thoughts", "rare")),
            thread_check_hours=float(clop_raw.get("thread_check_hours", 1.0)),
            read_alliance_messages=bool(clop_raw.get("read_alliance_messages", True)),
            dossier_max_age_hours=float(clop_raw.get("dossier_max_age_hours", 6.0)),
            roster_max_age_hours=float(clop_raw.get("roster_max_age_hours", 12.0)),
            sheet_sync=bool(clop_raw.get("sheet_sync", True)),
            warcalc_page=bool(clop_raw.get("warcalc_page", True)),
            thread_auto_find=bool(clop_raw.get("thread_auto_find", True)),
            thread_board=str(clop_raw.get("thread_board", "mlp") or "mlp"),
            thread_url=str(clop_raw.get("thread_url", "") or ""),
        ),
        auto_update=auto_update,
        presentation_mode=bool(presentation_mode),
    )
