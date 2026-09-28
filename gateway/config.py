"""Gateway configuration: connected platforms, home channels, session reset
policies and delivery preferences, loaded from config.yaml / gateway.json / env.
"""

import contextlib
import logging
import math
import os
import re
from pathlib import Path
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Dict, List, Optional, Any, Callable
from enum import Enum

from openagents_cli.config import get_openagents_home
from agent.secret_scope import current_secret_scope, get_secret as _get_secret
from gateway.shutdown_watchdog import (
    DEFAULT_LOOP_WATCHDOG_INTERVAL_S,
    DEFAULT_LOOP_WATCHDOG_MAX_STRIKES,
    DEFAULT_LOOP_WATCHDOG_TIMEOUT_S,
)
from utils import fast_safe_load, is_truthy_value

logger = logging.getLogger(__name__)

_TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSY_STRINGS = frozenset({"0", "false", "no", "off"})


def _bool_token(value: Any) -> Optional[bool]:
    """True/False for a recognized truthy/falsy token, else None."""
    token = str(value).strip().lower()
    return True if token in _TRUTHY_STRINGS else False if token in _FALSY_STRINGS else None


def _coerce_bool(value: Any, default: bool = True) -> bool:
    """Coerce bool-ish config values, preserving a caller-provided default."""
    if value is None:
        return default
    if isinstance(value, str):
        parsed = _bool_token(value)
        return default if parsed is None else parsed
    return is_truthy_value(value, default=default)


def _env_multiplex_profiles_override() -> "bool | None":
    """GATEWAY_MULTIPLEX_PROFILES operator override: True/False for a recognized token.

    ``None`` when unset, blank, or unrecognized so the caller keeps the config.yaml
    value (env > config > default). Blank is deliberately ``None``, not ``False``:
    a provisioned-but-unpopulated Fly secret arrives as ``""`` and must NOT shadow
    a config.yaml opt-in.
    """
    raw = os.getenv("GATEWAY_MULTIPLEX_PROFILES")
    if not (raw or "").strip():
        return None
    parsed = _bool_token(raw)
    if parsed is None:
        logger.warning(
            "Ignoring unrecognized GATEWAY_MULTIPLEX_PROFILES=%r "
            "(expected one of %s or %s); falling back to config.yaml.",
            raw, sorted(_TRUTHY_STRINGS), sorted(_FALSY_STRINGS),
        )
    return parsed


def _normalize_transport_token(value: Any) -> str:
    """Canonical streaming transport token. YAML 1.1 parses bare ``on``/``off`` as
    booleans (``mode: off`` → ``False`` → ``"false"`` would ENABLE streaming), so
    booleans map to ``"auto"``/``"off"``; anything else lower-cases, default ``"auto"``."""
    if value is None:
        return "auto"
    if isinstance(value, bool):
        return "auto" if value else "off"
    return str(value).strip().lower() or "auto"


def _coerce_num(cast, value: Any, default):
    # OverflowError: ``int(float("inf"))`` — non-finite YAML must degrade, not abort loading.
    try:
        return default if value is None else cast(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    return _coerce_num(float, value, default)


def _coerce_int(value: Any, default: int) -> int:
    return _coerce_num(int, value, default)


def _coerce_optional_positive_int(value: Any, key: str) -> Optional[int]:
    """``None``/0/negative disable; malformed values are ignored with a warning so a typo never blocks startup."""
    if value is None:
        return None
    try:
        if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
            raise ValueError(value)
        parsed = int(value.strip(), 10) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s=%r (expected a positive integer; 0/null disables)", key, value)
        return None
    return parsed if parsed > 0 else None


_SYSTEMD_WATCHDOG_MAX_SECONDS = 2_147_483_647


def coerce_systemd_watchdog_seconds(
    value: Any, key: str = "gateway.systemd_watchdog_seconds"
) -> int:
    """Bounded positive watchdog interval, or zero when disabled/invalid. Shared by runtime
    and service generation so a value can never enable ``Type=notify`` without heartbeats."""
    if value is None:
        return 0
    parsed: Optional[int] = None
    if isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdecimal():
        with contextlib.suppress(TypeError, ValueError, OverflowError):  # int() digit limit
            parsed = int(value.strip(), 10)
    if parsed is None:
        logger.warning("Ignoring invalid %s (expected a positive integer)", key)
        return 0
    if parsed and not 0 < parsed <= _SYSTEMD_WATCHDOG_MAX_SECONDS:
        logger.warning("Ignoring invalid %s (expected an integer from 1 to %d)", key, _SYSTEMD_WATCHDOG_MAX_SECONDS)
        return 0
    return parsed


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


# "pair" DMs a pairing code, "ignore" drops silently, "decline" sends one polite refusal then goes
# silent toward that sender for gateway.pairing.DECLINE_DEDUPE_SECONDS (#88028).
UNAUTHORIZED_DM_BEHAVIORS = {"pair", "ignore", "decline"}
DEFAULT_UNAUTHORIZED_DM_DECLINE_MESSAGE = (
    "Hi! I'm a personal assistant and can only chat with my owner, so I can't help you directly. Sorry!"
)


def _normalize_choice(value: Any, choices: set, default: str) -> str:
    """Lower-cased *value* when it is one of *choices*, else *default*."""
    normalized = value.strip().lower() if isinstance(value, str) else None
    return normalized if normalized in choices else default


def _dict_slot(container: dict, key: str) -> dict:
    """Get-or-create ``container[key]`` as a dict, replacing a non-dict value with ``{}``."""
    value = container.setdefault(key, {})
    if not isinstance(value, dict):
        value = {}
        container[key] = value
    return value


def _getenv(name: str, default: Optional[str] = None) -> Optional[str]:
    """Env read through the active profile secret scope when present (multiplexed
    per-profile secrets must win); otherwise legacy ``os.getenv``."""
    if current_secret_scope() is not None:
        scope_val = _get_secret(name, None)
        return scope_val if scope_val is not None else default
    return os.environ.get(name, default)


def _getenv_str(name: str, default: str = "") -> str:
    return val if (val := _getenv(name, default)) is not None else default


_Platform__bundled_plugin_names: Optional[set] = None  # cached outside the enum: never a member
_Platform__bundled_plugin_aliases: Optional[dict] = None  # manifest ``name:`` (lower) -> directory name


def _bundled_platform_manifest_name(plugin_dir: Path) -> Optional[str]:
    """Lowercased ``name:`` from a bundled platform's plugin manifest (None when absent/unreadable)."""
    try:
        manifest_file = next(
            (plugin_dir / m for m in ("plugin.yaml", "plugin.yml") if (plugin_dir / m).exists()), None)
        if manifest_file is None:
            return None
        data = fast_safe_load(manifest_file.read_text(encoding="utf-8")) or {}
        name = data.get("name") if isinstance(data, dict) else None
        return str(name).strip().lower() or None
    except Exception:
        return None


class Platform(Enum):
    """Supported messaging platforms. Plugin platforms are dynamic members created on
    demand by ``_missing_`` and cached so ``Platform("irc") is Platform("irc")`` holds."""
    LOCAL = "local"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WHATSAPP = "whatsapp"
    WHATSAPP_CLOUD = "whatsapp_cloud"
    SLACK = "slack"
    SIGNAL = "signal"
    MATTERMOST = "mattermost"
    MATRIX = "matrix"
    HOMEASSISTANT = "homeassistant"
    EMAIL = "email"
    SMS = "sms"
    DINGTALK = "dingtalk"
    API_SERVER = "api_server"
    WEBHOOK = "webhook"
    MSGRAPH_WEBHOOK = "msgraph_webhook"
    FEISHU = "feishu"
    WECOM = "wecom"
    WECOM_CALLBACK = "wecom_callback"
    WEIXIN = "weixin"
    BLUEBUBBLES = "bluebubbles"
    QQBOT = "qqbot"
    YUANBAO = "yuanbao"
    RELAY = "relay"  # generic relay adapter fronted by the connector (EXPERIMENTAL)

    @classmethod
    def _missing_(cls, value):
        """Accept unknown names only for bundled or runtime-registered plugin adapters (no enum pollution)."""
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip().lower()
        if value in cls._value2member_map_:
            return cls._value2member_map_[value]
        global _Platform__bundled_plugin_names, _Platform__bundled_plugin_aliases
        if _Platform__bundled_plugin_names is None:
            _Platform__bundled_plugin_names, _Platform__bundled_plugin_aliases = cls._scan_bundled_plugin_platforms()
        registered = value in _Platform__bundled_plugin_names
        if not registered:
            alias = _Platform__bundled_plugin_aliases.get(value)
            if alias is not None:
                # A bundled platform whose plugin.yaml ``name:`` differs from its directory (e.g. dir
                # "a2a", name "a2a-platform") is configured under the manifest name — ``plugins
                # enable`` writes that key — so resolve it to the directory-name member that the
                # registry and every value-based consumer key on.
                return cls._value2member_map_.get(alias) or cls._add_pseudo_member(alias)
            with contextlib.suppress(Exception):
                from gateway.platform_registry import platform_registry
                registered = platform_registry.is_registered(value)
        return cls._add_pseudo_member(value) if registered else None

    @classmethod
    def _add_pseudo_member(cls, value: str) -> "Platform":
        pseudo = object.__new__(cls)
        pseudo._value_ = value
        pseudo._name_ = value.upper().replace("-", "_").replace(" ", "_")
        cls._value2member_map_[value] = pseudo
        cls._member_map_[pseudo._name_] = pseudo
        return pseudo

    @classmethod
    def _scan_bundled_plugin_platforms(cls) -> "tuple[set, dict]":
        """Directory names of bundled platform plugins under ``plugins/platforms/``, plus a map of
        manifest ``name:`` keys that differ from their directory (alias -> directory name). Aliases
        never shadow a directory name, so the directory stays the canonical platform value."""
        try:
            platforms_dir = Path(__file__).parent.parent / "plugins" / "platforms"
            dirs = [
                child for child in (platforms_dir.iterdir() if platforms_dir.is_dir() else ())
                if child.is_dir() and (child / "__init__.py").exists()
                and ((child / "plugin.yaml").exists() or (child / "plugin.yml").exists())
            ]
            names = {child.name.lower() for child in dirs}
            aliases = {}
            for child in dirs:
                manifest_name = _bundled_platform_manifest_name(child)
                if manifest_name and manifest_name not in names and manifest_name not in aliases:
                    aliases[manifest_name] = child.name.lower()
            return names, aliases
        except Exception:
            return set(), {}


# Built-in values snapshotted before any dynamic _missing_ lookup.
_BUILTIN_PLATFORM_VALUES = frozenset(m.value for m in Platform.__members__.values())

# Platforms that bind a host TCP port. In a multiplexer only the default profile binds: a SECONDARY
# profile's port-binder is built in shared-listener mode and served at /p/<profile>/<path> on the
# default's listener (gateway/platforms/shared_ingress.py); api_server/webhook are mirrored there.
PORT_BINDING_PLATFORM_VALUES = frozenset({
    "webhook", "api_server", "msgraph_webhook", "feishu", "wecom_callback",
    "bluebubbles", "sms", "whatsapp_cloud", "line", "teams",
})
# Platforms that only bind in one connection mode (Feishu's default websocket mode is outbound).
PORT_BINDING_CONDITIONAL_MODES: dict[str, str] = {"feishu": "webhook"}
# Port-binders whose /p/<profile>/ surface is a MIRROR served by the default's own adapter; a secondary
# never gets an instance of these (api_server: /p/<profile>/v1/..., webhook: profile-bound routes).
SHARED_LISTENER_MIRROR_PLATFORMS = frozenset({"api_server", "webhook"})
# Path a client appends to ``<default listener>/p/<profile>`` to reach each mirror.
SHARED_LISTENER_MIRROR_PATHS: dict[str, str] = {"api_server": "/v1", "webhook": "/webhooks/<route>"}


def platform_binds_port(platform_value: str, extra: Optional[dict] = None) -> bool:
    """True when *platform_value* actually binds a port for *extra* config."""
    if platform_value not in PORT_BINDING_PLATFORM_VALUES:
        return False
    expected_mode = PORT_BINDING_CONDITIONAL_MODES.get(platform_value)
    return expected_mode is None or str((extra or {}).get("connection_mode", "websocket")).strip().lower() == expected_mode

_DISCORD_CHANNEL_LINK_RE = re.compile(
    r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/(?:[0-9]+|@me)/([0-9]+)/?")


def discord_channel_id_from_link(value: str) -> Optional[str]:
    """Channel id from a pasted Discord channel link (``https://discord.com/channels/<guild>/<channel>``),
    else None. Message links (a third path segment) and anything that is not a channel link are
    left alone so callers keep their own error path."""
    match = _DISCORD_CHANNEL_LINK_RE.fullmatch(value)
    return match.group(1) if match else None


@dataclass
class HomeChannel:
    """Default destination for a platform (``deliver="telegram"`` without a chat ID);
    ``thread_id`` routes the bare target to the topic where /sethome was run."""
    platform: Platform
    chat_id: str
    name: str
    thread_id: Optional[str] = None
    # Authenticated logical-target provenance (relay egress re-attaches; connector stays the authz boundary).
    user_id: Optional[str] = None
    scope_id: Optional[str] = None

    def __post_init__(self) -> None:
        # Copy Link is next to Copy Channel ID in Discord. Normalize at the
        # shared home boundary so env, YAML and plugin-seeded homes agree.
        if self.platform == Platform.DISCORD and isinstance(self.chat_id, str):
            self.chat_id = discord_channel_id_from_link(self.chat_id.strip()) or self.chat_id

    def to_dict(self) -> Dict[str, Any]:
        optional = {k: v for k in ("thread_id", "user_id", "scope_id") if (v := getattr(self, k))}
        return {"platform": self.platform.value, "chat_id": self.chat_id, "name": self.name, **optional}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HomeChannel":
        optional = {k: str(data[k]) if data.get(k) else None for k in ("thread_id", "user_id", "scope_id")}
        return cls(platform=Platform(data["platform"]), chat_id=str(data["chat_id"]), name=data.get("name", "Home"), **optional)


def persist_home_channel(home: HomeChannel, *, enabled_if_new: bool = False) -> None:
    """Persist a logical home without falsely enabling a Relay-fronted adapter."""
    from openagents_cli.config import load_config, save_config
    config = load_config()
    platform_config = _dict_slot(_dict_slot(config, "platforms"), home.platform.value)
    if enabled_if_new:
        platform_config.setdefault("enabled", True)
    platform_config["home_channel"] = home.to_dict()
    save_config(config)


@dataclass
class SessionResetPolicy:
    """Inert legacy value type retained solely for the scheduled plugin-compat window.

    Gateway configuration and session lifecycle do not consume this datatype.
    """
    mode: str = "none"
    at_hour: int = 4  # 0-23, local time
    idle_minutes: int = 1440
    notify: bool = True  # Notify the user when auto-reset occurs
    notify_exclude_platforms: tuple = ("api_server", "webhook")
    bg_process_max_age_hours: int = 24

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "notify_exclude_platforms": list(self.notify_exclude_platforms)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionResetPolicy":
        data = _coerce_dict(data)
        exclude = data.get("notify_exclude_platforms")
        # Missing keys and explicit YAML nulls both take the field default.
        plain = {
            f.name: f.default if data.get(f.name) is None else data[f.name]
            for f in fields(cls) if f.name not in ("notify", "notify_exclude_platforms")
        }
        return cls(
            notify=_coerce_bool(data.get("notify"), True),
            notify_exclude_platforms=tuple(exclude) if exclude is not None else ("api_server", "webhook"),
            **plain,
        )


@dataclass
class ChannelOverride:
    """Per-channel model/provider/system_prompt override (``platforms.<name>.channel_overrides[channel_id]``)."""
    model: Optional[str] = None
    provider: Optional[str] = None
    system_prompt: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChannelOverride":
        return cls(**{f.name: data.get(f.name) for f in fields(cls)}) if data else cls()


# Platforms whose primary credential is ``PlatformConfig.token`` → its env var (empty-token
# warnings; multiplex primary-startup gate in ``gateway.run``). Platforms absent here
# authenticate another way and must never be skipped for a missing token.
# Platforms absent from this map authenticate some other way (session files, port-bound webhooks,
# api_key-only) and must never be skipped for a missing token. See #64674.
PLATFORM_TOKEN_ENV_NAMES: dict["Platform", str] = {
    Platform.TELEGRAM: "TELEGRAM_BOT_TOKEN",
    Platform.DISCORD: "DISCORD_BOT_TOKEN",
    Platform.SLACK: "SLACK_BOT_TOKEN",
    Platform.MATTERMOST: "MATTERMOST_TOKEN",
    Platform.MATRIX: "MATRIX_ACCESS_TOKEN",
    Platform.WEIXIN: "WEIXIN_TOKEN",
}


@dataclass
class PlatformConfig:
    """Configuration for a single messaging platform."""
    enabled: bool = False
    token: Optional[str] = None
    api_key: Optional[str] = None  # API key if different from token
    home_channel: Optional[HomeChannel] = None
    reply_to_mode: str = "first"  # "off" never threads, "first" only the first chunk, "all" every chunk
    gateway_restart_notification: bool = True  # "♻️ Gateway online/restarted" pings; noise on end-user platforms
    typing_indicator: bool = True  # drives _keep_typing; False where unwanted (Slack setStatus blocks compose)
    # Working-state text for text-rendering indicators (Slack status, Google Chat marker); None = platform default.
    typing_status_text: Optional[str] = None
    channel_overrides: Dict[str, ChannelOverride] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)  # Platform-specific settings

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "enabled": self.enabled, "extra": self.extra, "reply_to_mode": self.reply_to_mode,
            "gateway_restart_notification": self.gateway_restart_notification,
            "typing_indicator": self.typing_indicator,
            **({"typing_status_text": self.typing_status_text} if self.typing_status_text is not None else {}),
            **{k: v for k in ("token", "api_key") if (v := getattr(self, k))},
        }
        if self.home_channel:
            result["home_channel"] = self.home_channel.to_dict()
        if self.channel_overrides:
            result["channel_overrides"] = {cid: ov.to_dict() for cid, ov in self.channel_overrides.items()}
        return result

    # Keys consumed by typed fields; everything else at the top of a platform block is adapter
    # config and belongs in ``extra`` (see from_dict).
    _TYPED_KEYS = frozenset({
        "enabled", "token", "api_key", "home_channel", "reply_to_mode", "channel_overrides", "extra",
        "gateway_restart_notification", "typing_indicator", "typing_status_text",
    })

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlatformConfig":
        data = _coerce_dict(data)
        home = data.get("home_channel")
        # Adapters read their settings from ``extra`` (``config.extra.get("port")``), but users
        # write them where the docs and ``hermes config set platforms.webhook.port`` put them:
        # directly under the platform block. Promote every non-typed top-level key so neither
        # spelling is silently dropped (#10206); an explicit ``extra:`` value wins on a clash.
        extra = {**{k: v for k, v in data.items() if k not in cls._TYPED_KEYS}, **_coerce_dict(data.get("extra", {}))}

        def toplevel_or_extra(key: str) -> Any:
            value = data.get(key)
            return extra.get(key) if value is None else value

        raw_overrides = data.get("channel_overrides") or {}
        channel_overrides = {
            str(cid): ChannelOverride.from_dict(ov_data)
            for cid, ov_data in raw_overrides.items()
            if isinstance(ov_data, dict)
        } if isinstance(raw_overrides, dict) else {}

        return cls(
            enabled=_coerce_bool(data.get("enabled"), False),
            token=data.get("token"),
            api_key=data.get("api_key"),
            home_channel=HomeChannel.from_dict(home) if isinstance(home, dict) else None,
            reply_to_mode=data.get("reply_to_mode", "first"),
            gateway_restart_notification=_coerce_bool(toplevel_or_extra("gateway_restart_notification"), True),
            typing_indicator=_coerce_bool(toplevel_or_extra("typing_indicator"), True),
            typing_status_text=toplevel_or_extra("typing_status_text"),  # string passthrough, no coercion
            channel_overrides=channel_overrides,
            extra=extra,
        )


# Shared by StreamingConfig and StreamConsumerConfig. Tuned for Telegram's ~1 edit/s
# flood envelope; the small buffer threshold makes short DM replies feel instant.
DEFAULT_STREAMING_EDIT_INTERVAL: float = 0.8
DEFAULT_STREAMING_BUFFER_THRESHOLD: int = 24
DEFAULT_STREAMING_CURSOR: str = " ▉"


@dataclass
class StreamingConfig:
    """Real-time token streaming to messaging platforms."""
    enabled: bool = False
    # "auto" prefers native drafts (Telegram sendMessageDraft) with edit fallback (adapters without
    # draft support use the edit path unchanged); "draft" / "edit" force one; "off" disables.
    transport: str = "auto"
    edit_interval: float = DEFAULT_STREAMING_EDIT_INTERVAL
    buffer_threshold: int = DEFAULT_STREAMING_BUFFER_THRESHOLD
    cursor: str = DEFAULT_STREAMING_CURSOR
    # >0: final edit becomes a fresh message once the preview was visible this long (Telegram only; 0 = off).
    # Ported from openclaw/openclaw#72038. When >0, the final edit for a long-running streamed response is
    # delivered as a fresh message if the original preview has been visible for at least this many seconds,
    # so the platform's visible timestamp reflects completion time instead of the preview creation time.
    # Currently applied to Telegram only (other platforms ignore the setting). Default 0 disables the
    # fresh-message replacement path; set >0 to opt in.
    fresh_final_after_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StreamingConfig":
        if not isinstance(data, dict) or not data:
            return cls()

        # ``mode`` is a transport alias that ALSO implies ``enabled`` (``mode: off`` disables;
        # explicit ``enabled`` wins). A bare ``transport`` does NOT imply enabled:
        # ``streaming.enabled`` is the documented master switch.
        raw_transport = data.get("transport")
        raw_mode = data.get("mode")
        if "enabled" in data:
            enabled = _coerce_bool(data.get("enabled"), False)
        else:
            enabled = raw_mode is not None and _normalize_transport_token(raw_mode) != "off"
        return cls(
            enabled=enabled,
            transport=_normalize_transport_token(raw_transport if raw_transport is not None else raw_mode),
            edit_interval=_coerce_float(data.get("edit_interval"), DEFAULT_STREAMING_EDIT_INTERVAL),
            buffer_threshold=_coerce_int(data.get("buffer_threshold"), DEFAULT_STREAMING_BUFFER_THRESHOLD),
            cursor=data.get("cursor", DEFAULT_STREAMING_CURSOR),
            fresh_final_after_seconds=_coerce_float(data.get("fresh_final_after_seconds"), 0.0),
        )


def _has_usable_api_server_key(key: object) -> bool:
    """True when API_SERVER_KEY is strong enough for the adapter to start (mirrors the
    ``has_usable_secret(min_length=16)`` guard in ``gateway/platforms/api_server.py``)."""
    if not key:
        return False
    try:
        from openagents_cli.auth import has_usable_secret
        return has_usable_secret(key, min_length=16)
    except ImportError:
        return len(str(key).strip()) >= 16


def _needs_extra(*keys: str) -> Callable[[PlatformConfig], bool]:
    return lambda cfg: all(cfg.extra.get(k) for k in keys)


# Built-in "sufficiently configured?" checks; platforms covered by the generic
# ``token or api_key`` check (Telegram, Discord, Slack, Matrix, ...) need no entry.
_PLATFORM_CONNECTED_CHECKERS: dict[Platform, Callable[[PlatformConfig], bool]] = {
    Platform.WEIXIN: lambda cfg: bool(cfg.extra.get("account_id") and (cfg.token or cfg.extra.get("token"))),
    Platform.WHATSAPP_CLOUD: _needs_extra("phone_number_id", "access_token"),
    Platform.SIGNAL: _needs_extra("http_url"),
    Platform.API_SERVER: lambda cfg: _has_usable_api_server_key(cfg.extra.get("key") if cfg else None),
    Platform.WEBHOOK: lambda cfg: True,
    Platform.MSGRAPH_WEBHOOK: lambda cfg: bool(str(cfg.extra.get("client_state") or "").strip()),
    Platform.BLUEBUBBLES: _needs_extra("server_url", "password"),
    Platform.QQBOT: _needs_extra("app_id", "client_secret"),
    Platform.YUANBAO: _needs_extra("app_id", "app_secret"),
    # Relay dials OUT: "connected" once an endpoint URL is configured. EXPERIMENTAL.
    Platform.RELAY: lambda cfg: bool(cfg.extra.get("relay_url") or cfg.extra.get("url")),
}


# Top-level bool-ish keys read verbatim (no nested ``gateway.`` fallback) with their defaults.
_TOPLEVEL_BOOL_DEFAULTS = {
    "write_sessions_json": True, "always_log_local": True, "filter_silence_narration": True,
    "group_sessions_per_user": True, "thread_sessions_per_user": False,
}


@dataclass
class GatewayConfig:
    """Main gateway configuration: platform connections, session policies, delivery settings."""
    platforms: Dict[Platform, PlatformConfig] = field(default_factory=dict)
    reset_triggers: List[str] = field(default_factory=lambda: ["/new", "/reset"])
    quick_commands: Dict[str, Any] = field(default_factory=dict)  # slash commands that bypass the agent loop
    sessions_dir: Path = field(default_factory=lambda: get_openagents_home() / "sessions")
    # Legacy sessions.json mirror of the routing index (primary: state.db) for external tooling / downgrades.
    # The primary copy lives in state.db (gateway_routing table, #9006). Default True for backward
    # compatibility with external tooling and downgrade safety; set gateway.write_sessions_json: false in
    # config.yaml to stop producing the file.
    write_sessions_json: bool = True
    always_log_local: bool = True  # Always save cron outputs to local files
    # Drop outbound "silence narration" (*(silent)*, 🔇, a bare ".") that ping-pongs in bot-to-bot
    # channels; a substrate guard that survives prompt drift.
    filter_silence_narration: bool = True
    stt_enabled: bool = True  # Auto-transcribe inbound voice messages
    stt_echo_transcripts: bool = True  # Echo raw STT transcripts back to the user
    group_sessions_per_user: bool = True  # Isolate group sessions per participant when user IDs exist
    thread_sessions_per_user: bool = False  # False = threads shared across participants
    max_concurrent_sessions: Optional[int] = None  # Positive int caps simultaneous active sessions
    # The default profile's gateway serves every profile on the host (profiles stamped into session
    # keys, per-profile adapters/credentials). On by default (DEFAULT_CONFIG), but UNSET here is
    # ``None``: a request the gateway settles at boot, not a verdict. ``openagents_cli.gateway_multiplex_mode
    # .resolve_multiplex_mode`` runs the migration preflight (default profile, >= 2 profiles, no
    # secondary running its own gateway, no blocker, migratable host) and only then writes True/False.
    # An explicit value (config.yaml, GATEWAY_MULTIPLEX_PROFILES, a constructor argument) is honoured
    # verbatim. Every reader tests truthiness, so an unresolved ``None`` never multiplexes by accident.
    multiplex_profiles: Optional[bool] = None
    # Public HTTPS endpoint for scoped RoomLink calls (an API key alone must never advertise a
    # route); HERMES_ROOM_LINK_URL overrides.
    room_link_url: Optional[str] = None
    systemd_watchdog_seconds: int = 0  # opt-in; zero keeps Type=simple and disables sd_notify
    # In-process loop liveness watchdog: after consecutive missed probes it dumps all-thread stacks
    # and hard-exits with the service-restart code. The knobs tolerate transient self-recovering
    # stalls (adapter reconnect doing sync socket I/O) so a short block does not cause restart churn.
    # max_strikes ~= 90-120s sustained block; the heartbeat-fsync false positive is fixed at the root
    # (off-loop write + two-witness probe), so raising it would only delay recovery.
    # On by default; set gateway.loop_watchdog: false in config.yaml to disable. Telegram/Discord reconnect
    # doing synchronous socket I/O during a network blip — so a short block does not force exit code 75 and
    # trigger a restart churn that stalls cron dispatch (recurring fleet incidents on 2026-08-17, kanban
    # t_0f76430f/t_70483f23). A genuine wedge (event loop frozen for the full tolerance window) still
    # escalates to a supervised restart. See #69089.
    loop_watchdog: bool = True
    loop_watchdog_probe_interval_s: float = DEFAULT_LOOP_WATCHDOG_INTERVAL_S
    loop_watchdog_probe_timeout_s: float = DEFAULT_LOOP_WATCHDOG_TIMEOUT_S
    loop_watchdog_max_strikes: int = DEFAULT_LOOP_WATCHDOG_MAX_STRIKES
    unauthorized_dm_behavior: str = "pair"  # UNAUTHORIZED_DM_BEHAVIORS
    unauthorized_dm_decline_message: str = ""  # "decline" reply text; empty → DEFAULT_UNAUTHORIZED_DM_DECLINE_MESSAGE
    streaming: StreamingConfig = field(default_factory=StreamingConfig)
    # Prune SessionEntry records older than this (a resumed chat gets a fresh session). 0 = off.
    session_store_max_age_days: int = 90
    profile_routes: list = field(default_factory=list)  # gateway/profile_routing.py

    # Scalar fields serialized verbatim by ``to_dict`` (in output order).
    _SCALAR_DICT_FIELDS = (
        "write_sessions_json", "always_log_local", "filter_silence_narration", "stt_enabled",
        "stt_echo_transcripts", "group_sessions_per_user", "thread_sessions_per_user",
        "max_concurrent_sessions", "multiplex_profiles",
        "room_link_url", "systemd_watchdog_seconds", "loop_watchdog",
        "loop_watchdog_probe_interval_s", "loop_watchdog_probe_timeout_s",
        "loop_watchdog_max_strikes", "unauthorized_dm_behavior", "unauthorized_dm_decline_message",
    )

    def __post_init__(self) -> None:
        self.systemd_watchdog_seconds = coerce_systemd_watchdog_seconds(self.systemd_watchdog_seconds)

    def get_connected_platforms(self) -> List[Platform]:
        """Enabled + configured platforms, sorted by value so the rendered "Connected
        Platforms" prompt block is byte-stable (a reorder busts the prompt cache)."""
        connected = [p for p, c in self.platforms.items() if c.enabled and self._is_platform_connected(p, c)]
        return sorted(connected, key=lambda p: str(p.value))

    def _is_platform_connected(self, platform: Platform, config: PlatformConfig) -> bool:
        checker = _PLATFORM_CONNECTED_CHECKERS.get(platform)
        # Weixin needs token AND account_id, so it must bypass the generic token branch.
        if platform == Platform.WEIXIN:
            return checker(config)
        if config.token or config.api_key:
            return True
        if checker is not None:
            return checker(config)

        # Plugin platforms; force (idempotent) discovery for directly-constructed configs.
        try:
            from gateway.platform_registry import platform_registry
            with contextlib.suppress(Exception):
                # Iterate built-in platforms plus any registered plugin platforms so plugin authors get the
                # same shared-key bridging (#24836).
                # Registry-driven enable for plugin platforms. Built-ins have explicit blocks above. A
                # plugin platform is enabled when its credentials are configured (``is_connected``) and its
                # dependencies are either present (passive ``check_fn``) or installable on demand
                # (``ensure_deps_fn``, run later by ``create_adapter()`` — never here). Plugins that need to
                # seed ``PlatformConfig.extra`` from env vars (e.g. Google Chat's project_id /
                # subscription_name) can supply ``env_enablement_fn`` on their PlatformEntry — called here
                # BEFORE adapter construction. Enablement gate (#31116): when a plugin registers
                # ``is_connected`` (the "has the user actually configured credentials for this?" check), we
                # MUST consult it before flipping ``enabled = True``. Otherwise ``check_fn`` alone — a
                # passive "is the SDK importable?" probe — silently enables platforms the user never opted
                # into, and the gateway then tries to connect to Discord / Teams / Google Chat with no token
                # and emits noisy retry-forever errors. ``_platform_status`` was already fixed for the same
                # bug class in commit 7849a3d73; this is the runtime counterpart.
                from openagents_cli.plugins import discover_plugins
                discover_plugins()
            entry = platform_registry.get(platform.value)
            if entry:
                check = entry.is_connected if entry.is_connected is not None else entry.validate_config
                return True if check is None else check(config)
        except Exception:
            pass  # Registry not yet initialised during early import
        return False

    def get_home_channel(self, platform: Platform) -> Optional[HomeChannel]:
        return self.platforms[platform].home_channel if self.platforms.get(platform) else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platforms": {p.value: c.to_dict() for p, c in self.platforms.items()},
            "reset_triggers": self.reset_triggers,
            "quick_commands": self.quick_commands,
            "sessions_dir": str(self.sessions_dir),
            **{name: getattr(self, name) for name in self._SCALAR_DICT_FIELDS},
            "streaming": self.streaming.to_dict(),
            "session_store_max_age_days": self.session_store_max_age_days,
            "profile_routes": [
                {k: v for k, v in asdict(r).items() if k != "user_id" or v is not None}
                if is_dataclass(r) and not isinstance(r, type) else r
                for r in self.profile_routes
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GatewayConfig":
        data = _coerce_dict(data)
        nested_gateway = _coerce_dict(data.get("gateway"))

        def pick(key: str) -> Any:
            """Top-level key wins by presence; else the nested ``gateway.<key>`` form."""
            return data[key] if key in data else nested_gateway.get(key)

        def key_label(key: str) -> str:
            """Warning key prefix: "gateway." when the nested form was the one consulted."""
            return key if key in data else f"gateway.{key}"

        def by_platform(key: str, parse, *, dicts_only: bool = False) -> dict:
            """``{Platform(name): parse(block)}`` for a platform-keyed mapping; unknown platforms skipped."""
            out = {}
            for platform_name, block in _coerce_dict(data.get(key, {})).items():
                if dicts_only and not isinstance(block, dict):
                    continue
                try:
                    out[Platform(platform_name)] = parse(block)
                except ValueError:
                    pass
            return out

        def stt_setting(flat_key: str, nested_key: str) -> Any:
            value = data.get(flat_key)
            return _coerce_dict(data.get("stt")).get(nested_key) if value is None else value

        def bounded_float(key: str, default: float, lo: float, hi: float) -> float:
            # Out-of-range / non-finite watchdog knobs fall back to the shipped defaults.
            value = _coerce_float(pick(key), default)
            return value if math.isfinite(value) and lo <= value <= hi else default

        room_link_url = data.get("room_link_url")
        max_strikes = _coerce_int(pick("loop_watchdog_max_strikes"), DEFAULT_LOOP_WATCHDOG_MAX_STRIKES)
        if not 1 <= max_strikes <= 1000:
            max_strikes = DEFAULT_LOOP_WATCHDOG_MAX_STRIKES

        systemd_watchdog_seconds = coerce_systemd_watchdog_seconds(
            pick("systemd_watchdog_seconds"), key_label("systemd_watchdog_seconds")
        )
        # env > config.yaml > unset: a recognized GATEWAY_MULTIPLEX_PROFILES wins (hosted deployments
        # stamp it on the container); blank/unrecognized falls through to the top-level VALUE when not
        # None, else ``gateway.multiplex_profiles``. Nothing set stays ``None`` so the boot-time guard
        # (``resolve_multiplex_mode``) can tell "the operator chose" from "the default applies".
        multiplex_profiles = data.get("multiplex_profiles")
        if multiplex_profiles is None:
            multiplex_profiles = nested_gateway.get("multiplex_profiles")
        env_multiplex = _env_multiplex_profiles_override()
        if env_multiplex is not None:
            multiplex_profiles = env_multiplex
        max_concurrent_sessions = _coerce_optional_positive_int(
            pick("max_concurrent_sessions"), key_label("max_concurrent_sessions")
        )

        try:
            session_store_max_age_days = max(int(data.get("session_store_max_age_days", 90)), 0)
        except (TypeError, ValueError):
            session_store_max_age_days = 90

        from gateway.profile_routing import parse_profile_routes

        return cls(
            platforms=by_platform("platforms", PlatformConfig.from_dict, dicts_only=True),
            reset_triggers=data.get("reset_triggers", ["/new", "/reset"]),
            quick_commands=_coerce_dict(data.get("quick_commands", {})),
            sessions_dir=Path(data["sessions_dir"]) if "sessions_dir" in data else get_openagents_home() / "sessions",
            **{name: _coerce_bool(data.get(name), default) for name, default in _TOPLEVEL_BOOL_DEFAULTS.items()},
            stt_enabled=_coerce_bool(stt_setting("stt_enabled", "enabled"), True),
            stt_echo_transcripts=_coerce_bool(stt_setting("stt_echo_transcripts", "echo_transcripts"), True),
            multiplex_profiles=None if multiplex_profiles is None else _coerce_bool(multiplex_profiles, True),
            room_link_url=room_link_url if isinstance(room_link_url, str) else None,
            systemd_watchdog_seconds=systemd_watchdog_seconds,
            loop_watchdog=_coerce_bool(pick("loop_watchdog"), True),
            loop_watchdog_probe_interval_s=bounded_float("loop_watchdog_probe_interval_s", DEFAULT_LOOP_WATCHDOG_INTERVAL_S, 1.0, 3600.0),
            loop_watchdog_probe_timeout_s=bounded_float("loop_watchdog_probe_timeout_s", DEFAULT_LOOP_WATCHDOG_TIMEOUT_S, 1.0, 600.0),
            loop_watchdog_max_strikes=max_strikes,
            max_concurrent_sessions=max_concurrent_sessions,
            unauthorized_dm_behavior=_normalize_choice(data.get("unauthorized_dm_behavior"), UNAUTHORIZED_DM_BEHAVIORS, "pair"),
            unauthorized_dm_decline_message=str(data.get("unauthorized_dm_decline_message") or "").strip(),
            streaming=StreamingConfig.from_dict(data.get("streaming", {})),
            session_store_max_age_days=session_store_max_age_days,
            profile_routes=parse_profile_routes(data.get("profile_routes") or []),
        )

    def _extra_choice(self, platform: Optional[Platform], key: str, choices: set, default: str) -> Optional[str]:
        """Normalized ``platforms[platform].extra[key]`` when the key is present, else None."""
        platform_cfg = self.platforms.get(platform) if platform else None
        if platform_cfg and key in platform_cfg.extra:
            return _normalize_choice(platform_cfg.extra.get(key), choices, default)
        return None

    def get_unauthorized_dm_behavior(self, platform: Optional[Platform] = None) -> str:
        """Effective unauthorized-DM behavior. Email is inbox-shaped so it defaults to ``"ignore"``
        unless its own ``unauthorized_dm_behavior`` opts in (a global default does not)."""
        choice = self._extra_choice(platform, "unauthorized_dm_behavior", UNAUTHORIZED_DM_BEHAVIORS, self.unauthorized_dm_behavior)
        if choice is not None:
            return choice
        return "ignore" if platform == Platform.EMAIL else self.unauthorized_dm_behavior

    def get_notice_delivery(self, platform: Optional[Platform] = None) -> str:
        """Effective notice-delivery mode ("public"/"private") for a platform."""
        choice = self._extra_choice(platform, "notice_delivery", {"public", "private"}, "public")
        return "public" if choice is None else choice


def load_gateway_config() -> GatewayConfig:
    """Load gateway configuration. Priority: env > ~/.openagents/config.yaml > legacy gateway.json > defaults."""
    from gateway import config_loader

    _home = get_openagents_home()
    gw_data = config_loader.load_legacy_gateway_json(_home)
    try:
<<<<<<< HEAD
        import yaml
        config_yaml_path = _home / "config.yaml"
        if config_yaml_path.exists():
            with open(config_yaml_path, encoding="utf-8") as f:
                yaml_cfg = yaml.safe_load(f) or {}

            # Managed scope: overlay administrator-pinned values so the gateway
            # honors them too. This loader builds its own dict instead of going
            # through openagents_cli.config.load_config, so without this a managed
            # session_reset / quick_commands / stt / model would be ignored by
            # the messaging gateway. Fail-open via the shared helper.
            from openagents_cli import managed_scope
            yaml_cfg = managed_scope.apply_managed_overlay(yaml_cfg)

            # Map config.yaml keys → GatewayConfig.from_dict() schema.
            # Each key overwrites whatever gateway.json may have set.
            sr = yaml_cfg.get("session_reset")
            if sr and isinstance(sr, dict):
                gw_data["default_reset_policy"] = sr

            qc = yaml_cfg.get("quick_commands")
            if qc is not None:
                if isinstance(qc, dict):
                    gw_data["quick_commands"] = qc
                else:
                    logger.warning(
                        "Ignoring invalid quick_commands in config.yaml "
                        "(expected mapping, got %s)",
                        type(qc).__name__,
                    )

            stt_cfg = yaml_cfg.get("stt")
            if isinstance(stt_cfg, dict):
                gw_data["stt"] = stt_cfg

            if "group_sessions_per_user" in yaml_cfg:
                gw_data["group_sessions_per_user"] = yaml_cfg["group_sessions_per_user"]

            if "thread_sessions_per_user" in yaml_cfg:
                gw_data["thread_sessions_per_user"] = yaml_cfg["thread_sessions_per_user"]

            # Multiplexing flag: accept both the top-level key and the nested
            # gateway.multiplex_profiles form (from_dict resolves the nested
            # fallback, but surface the top-level key here for parity with the
            # other session-scope flags above).
            gateway_section = (
                yaml_cfg.get("gateway")
                if isinstance(yaml_cfg.get("gateway"), dict)
                else {}
            )
            if "multiplex_profiles" in yaml_cfg:
                gw_data["multiplex_profiles"] = yaml_cfg["multiplex_profiles"]
            elif "multiplex_profiles" in gateway_section:
                gw_data["multiplex_profiles"] = gateway_section["multiplex_profiles"]

            if "max_concurrent_sessions" in gateway_section:
                gw_data["max_concurrent_sessions"] = gateway_section["max_concurrent_sessions"]

            if "max_concurrent_sessions" in yaml_cfg:
                gw_data["max_concurrent_sessions"] = yaml_cfg["max_concurrent_sessions"]

            streaming_cfg = yaml_cfg.get("streaming")
            if not isinstance(streaming_cfg, dict):
                # Fall back to nested gateway.streaming written by
                # ``hermes config set gateway.streaming.*``
                streaming_cfg = yaml_cfg.get("gateway", {}).get("streaming")
            if isinstance(streaming_cfg, dict):
                gw_data["streaming"] = streaming_cfg

            if "reset_triggers" in yaml_cfg:
                gw_data["reset_triggers"] = yaml_cfg["reset_triggers"]

            if "always_log_local" in yaml_cfg:
                gw_data["always_log_local"] = yaml_cfg["always_log_local"]

            if "filter_silence_narration" in yaml_cfg:
                gw_data["filter_silence_narration"] = yaml_cfg[
                    "filter_silence_narration"
                ]

            if "unauthorized_dm_behavior" in yaml_cfg:
                gw_data["unauthorized_dm_behavior"] = _normalize_unauthorized_dm_behavior(
                    yaml_cfg.get("unauthorized_dm_behavior"),
                    "pair",
                )

            # Merge platform config into gw_data so runtime-only settings under
            # ``gateway.platforms`` are loaded the same way as top-level
            # ``platforms``. Merge nested first so top-level config keeps
            # precedence, matching the existing gateway.streaming fallback.
            gateway_cfg = yaml_cfg.get("gateway")
            gateway_platforms = gateway_cfg.get("platforms") if isinstance(gateway_cfg, dict) else None
            platforms_data = gw_data.setdefault("platforms", {})
            if not isinstance(platforms_data, dict):
                platforms_data = {}
                gw_data["platforms"] = platforms_data

            def _merge_platform_map(source_platforms: Any) -> None:
                if not isinstance(source_platforms, dict):
                    return
                for plat_name, plat_block in source_platforms.items():
                    if not isinstance(plat_block, dict):
                        continue
                    existing = platforms_data.get(plat_name, {})
                    if not isinstance(existing, dict):
                        existing = {}
                    # Deep-merge extra dicts so gateway.json defaults survive
                    merged_extra = {**existing.get("extra", {}), **plat_block.get("extra", {})}
                    if "enabled" in plat_block:
                        merged_extra["_enabled_explicit"] = True
                    merged = {**existing, **plat_block}
                    if merged_extra:
                        merged["extra"] = merged_extra
                    platforms_data[plat_name] = merged

            _merge_platform_map(gateway_platforms)
            _merge_platform_map(yaml_cfg.get("platforms"))
            if platforms_data:
                gw_data["platforms"] = platforms_data
            # Iterate built-in platforms plus any registered plugin platforms
            # so plugin authors get the same shared-key bridging (#24836).
            try:
                from openagents_cli.plugins import discover_plugins
                discover_plugins()  # idempotent
                from gateway.platform_registry import platform_registry as _pr
            except Exception as e:
                logger.debug("plugin discovery skipped: %s", e)
                _pr = None

            _shared_loop_targets: list = list(Platform)
            if _pr is not None:
                for _entry in _pr.plugin_entries():
                    try:
                        _plat = Platform(_entry.name)
                    except (ValueError, KeyError):
                        continue
                    if _plat not in _shared_loop_targets:
                        _shared_loop_targets.append(_plat)

            for plat in _shared_loop_targets:
                if plat == Platform.LOCAL:
                    continue
                platform_cfg = yaml_cfg.get(plat.value)
                _cfg_toplevel = isinstance(platform_cfg, dict)
                # Fall back to the platform's block under ``platforms`` /
                # ``gateway.platforms`` so shared-key bridging (allow_from,
                # require_mention, free_response_channels, …) still runs when
                # the user configured the platform only under those nested paths
                # and not via a top-level block.  Mirrors the identical fallback
                # already applied to the apply_yaml_config_fn dispatch below
                # (#44f3e51).
                # Note: ``enabled`` is only written to plat_data from a
                # top-level block (``_cfg_toplevel``); for nested-only configs
                # ``_merge_platform_map`` already merged it with the correct
                # precedence, so re-applying it here would overwrite that.
                if not _cfg_toplevel:
                    for _src in (gateway_platforms, yaml_cfg.get("platforms")):
                        if isinstance(_src, dict):
                            _candidate = _src.get(plat.value)
                            if isinstance(_candidate, dict):
                                platform_cfg = _candidate
                                break
                if not isinstance(platform_cfg, dict):
                    continue
                # Collect bridgeable keys from this platform section
                bridged = {}
                if "unauthorized_dm_behavior" in platform_cfg:
                    bridged["unauthorized_dm_behavior"] = _normalize_unauthorized_dm_behavior(
                        platform_cfg.get("unauthorized_dm_behavior"),
                        gw_data.get("unauthorized_dm_behavior", "pair"),
                    )
                if "notice_delivery" in platform_cfg:
                    bridged["notice_delivery"] = _normalize_notice_delivery(
                        platform_cfg.get("notice_delivery"),
                        "public",
                    )
                if "reply_prefix" in platform_cfg:
                    bridged["reply_prefix"] = platform_cfg["reply_prefix"]
                if "reply_in_thread" in platform_cfg:
                    bridged["reply_in_thread"] = platform_cfg["reply_in_thread"]
                if "require_mention" in platform_cfg:
                    bridged["require_mention"] = platform_cfg["require_mention"]
                if plat == Platform.TELEGRAM and "allowed_chats" in platform_cfg:
                    bridged["allowed_chats"] = platform_cfg["allowed_chats"]
                if plat == Platform.TELEGRAM and "group_allowed_chats" in platform_cfg:
                    bridged["group_allowed_chats"] = platform_cfg["group_allowed_chats"]
                if plat == Platform.TELEGRAM and "allowed_topics" in platform_cfg:
                    bridged["allowed_topics"] = platform_cfg["allowed_topics"]
                if "free_response_channels" in platform_cfg:
                    bridged["free_response_channels"] = platform_cfg["free_response_channels"]
                if "mention_patterns" in platform_cfg:
                    bridged["mention_patterns"] = platform_cfg["mention_patterns"]
                if "exclusive_bot_mentions" in platform_cfg:
                    bridged["exclusive_bot_mentions"] = platform_cfg["exclusive_bot_mentions"]
                if plat == Platform.TELEGRAM and "observe_unmentioned_group_messages" in platform_cfg:
                    bridged["observe_unmentioned_group_messages"] = platform_cfg["observe_unmentioned_group_messages"]
                if "dm_policy" in platform_cfg:
                    bridged["dm_policy"] = platform_cfg["dm_policy"]
                if "allow_from" in platform_cfg:
                    bridged["allow_from"] = platform_cfg["allow_from"]
                if "allow_admin_from" in platform_cfg:
                    bridged["allow_admin_from"] = platform_cfg["allow_admin_from"]
                if "user_allowed_commands" in platform_cfg:
                    bridged["user_allowed_commands"] = platform_cfg["user_allowed_commands"]
                if "group_policy" in platform_cfg:
                    bridged["group_policy"] = platform_cfg["group_policy"]
                if "group_allow_from" in platform_cfg:
                    bridged["group_allow_from"] = platform_cfg["group_allow_from"]
                if "group_allow_admin_from" in platform_cfg:
                    bridged["group_allow_admin_from"] = platform_cfg["group_allow_admin_from"]
                if "group_user_allowed_commands" in platform_cfg:
                    bridged["group_user_allowed_commands"] = platform_cfg["group_user_allowed_commands"]
                if plat in {Platform.DISCORD, Platform.SLACK} and "channel_skill_bindings" in platform_cfg:
                    bridged["channel_skill_bindings"] = platform_cfg["channel_skill_bindings"]
                if "channel_prompts" in platform_cfg:
                    channel_prompts = platform_cfg["channel_prompts"]
                    if isinstance(channel_prompts, dict):
                        bridged["channel_prompts"] = {str(k): v for k, v in channel_prompts.items()}
                    else:
                        bridged["channel_prompts"] = channel_prompts
                if "gateway_restart_notification" in platform_cfg:
                    bridged["gateway_restart_notification"] = platform_cfg["gateway_restart_notification"]
                enabled_was_explicit = _cfg_toplevel and "enabled" in platform_cfg
                if not bridged and not enabled_was_explicit:
                    continue
                plat_data, extra = _ensure_platform_extra_dict(platforms_data, plat.value)
                if enabled_was_explicit:
                    plat_data["enabled"] = platform_cfg["enabled"]
                    # Mark the explicit enable/disable so the registry-driven
                    # plugin-enable pass in _apply_env_overrides honors an
                    # explicit ``enabled: false`` for migrated plugin platforms
                    # (slack, telegram, matrix, dingtalk, whatsapp, feishu …)
                    # instead of re-enabling them on token/SDK presence. #41112.
                    extra["_enabled_explicit"] = True
                extra.update(bridged)

            # Plugin-owned YAML→env config bridges (#24836).  See
            # ``PlatformEntry.apply_yaml_config_fn`` for the hook contract.
            # Order: shared-key loop (above) → this dispatch → legacy hardcoded
            # blocks (below; no-op when a hook already set their env var) →
            # ``_apply_env_overrides()`` after ``GatewayConfig.from_dict``.
            if _pr is not None:
                for entry in _pr.all_entries():
                    if entry.apply_yaml_config_fn is None:
                        continue
                    platform_cfg = yaml_cfg.get(entry.name)
                    # Fall back to the platform's block under ``platforms`` /
                    # ``gateway.platforms`` so adapter hooks still run when the
                    # user configured the platform only under those nested paths
                    # (e.g. ``platforms.discord.extra.allow_from``) and not via a
                    # top-level ``discord:`` block.
                    if not isinstance(platform_cfg, dict):
                        for _src in (gateway_platforms, yaml_cfg.get("platforms")):
                            if isinstance(_src, dict):
                                _candidate = _src.get(entry.name)
                                if isinstance(_candidate, dict):
                                    platform_cfg = _candidate
                                    break
                    if not isinstance(platform_cfg, dict):
                        continue
                    try:
                        seeded = entry.apply_yaml_config_fn(yaml_cfg, platform_cfg)
                    except Exception as e:
                        logger.debug(
                            "apply_yaml_config_fn for %s raised: %s",
                            entry.name, e,
                        )
                        continue
                    if not isinstance(seeded, dict) or not seeded:
                        continue
                    _, extra = _ensure_platform_extra_dict(platforms_data, entry.name)
                    extra.update(seeded)

            # Slack settings → env vars: migrated to the slack plugin's
            # ``apply_yaml_config_fn`` hook (see plugins/platforms/slack/
            # adapter.py::_apply_yaml_config), dispatched in the
            # ``apply_yaml_config_fn`` loop above. #41112 / #3823.

            # Bridge top-level require_mention to Telegram when the telegram: section
            # does not already provide one.  Users often write "require_mention: true"
            # at the top level alongside group_sessions_per_user, expecting it to work
            # the same way (#3979).
            _tl_require_mention = yaml_cfg.get("require_mention")
            if _tl_require_mention is not None:
                _tg_section = yaml_cfg.get("telegram") or {}
                if "require_mention" not in _tg_section:
                    _tg_plat = platforms_data.setdefault(Platform.TELEGRAM.value, {})
                    _tg_extra = _tg_plat.setdefault("extra", {})
                    _tg_extra.setdefault("require_mention", _tl_require_mention)
                    # Also bridge to the TELEGRAM_REQUIRE_MENTION env var that the
                    # adapter reads at runtime.  This used to live in the telegram_cfg
                    # block in core; it stays in core because it keys off the TOP-LEVEL
                    # require_mention (not a telegram: block), so the telegram plugin's
                    # apply_yaml_config_fn hook — which only runs when a telegram config
                    # block exists — can't cover the no-telegram-block case (#3979).
                    if not os.getenv("TELEGRAM_REQUIRE_MENTION"):
                        os.environ["TELEGRAM_REQUIRE_MENTION"] = str(_tl_require_mention).lower()

            # Telegram settings → env vars / extra: migrated to the telegram
            # plugin's apply_yaml_config_fn hook
            # (plugins/platforms/telegram/adapter.py). #41112 / #3823.

            # WhatsApp settings → env vars: migrated to the whatsapp plugin's
            # apply_yaml_config_fn hook (plugins/platforms/whatsapp/adapter.py).
            # #41112 / #3823.

            # Signal settings → env vars (env vars take precedence)
            signal_cfg = yaml_cfg.get("signal", {})
            if isinstance(signal_cfg, dict):
                if "require_mention" in signal_cfg and not os.getenv("SIGNAL_REQUIRE_MENTION"):
                    os.environ["SIGNAL_REQUIRE_MENTION"] = str(signal_cfg["require_mention"]).lower()

            # DingTalk settings → env vars: migrated to the dingtalk plugin's
            # apply_yaml_config_fn hook (plugins/platforms/dingtalk/adapter.py).
            # #41112 / #3823.

            # Mattermost config bridge moved into plugins/platforms/mattermost/
            # adapter.py::_apply_yaml_config — see #25443 (apply_yaml_config_fn).

            # Matrix settings → env vars: migrated to the matrix plugin's
            # apply_yaml_config_fn hook (plugins/platforms/matrix/adapter.py).
            # #41112 / #3823.

            # Feishu settings → env vars: migrated to the feishu plugin's
            # apply_yaml_config_fn hook (plugins/platforms/feishu/adapter.py).
            # #41112 / #3823.

||||||| cf299e9a01
        import yaml
        config_yaml_path = _home / "config.yaml"
        if config_yaml_path.exists():
            with open(config_yaml_path, encoding="utf-8") as f:
                yaml_cfg = yaml.safe_load(f) or {}

            # Managed scope: overlay administrator-pinned values so the gateway
            # honors them too. This loader builds its own dict instead of going
            # through openagents_cli.config.load_config, so without this a managed
            # session_reset / quick_commands / stt / model would be ignored by
            # the messaging gateway. Fail-open via the shared helper.
            from openagents_cli import managed_scope
            yaml_cfg = managed_scope.apply_managed_overlay(yaml_cfg)

            # Map config.yaml keys → GatewayConfig.from_dict() schema.
            # Each key overwrites whatever gateway.json may have set.
            sr = yaml_cfg.get("session_reset")
            if sr and isinstance(sr, dict):
                gw_data["default_reset_policy"] = sr

            qc = yaml_cfg.get("quick_commands")
            if qc is not None:
                if isinstance(qc, dict):
                    gw_data["quick_commands"] = qc
                else:
                    logger.warning(
                        "Ignoring invalid quick_commands in config.yaml "
                        "(expected mapping, got %s)",
                        type(qc).__name__,
                    )

            stt_cfg = yaml_cfg.get("stt")
            if isinstance(stt_cfg, dict):
                gw_data["stt"] = stt_cfg

            if "group_sessions_per_user" in yaml_cfg:
                gw_data["group_sessions_per_user"] = yaml_cfg["group_sessions_per_user"]

            if "thread_sessions_per_user" in yaml_cfg:
                gw_data["thread_sessions_per_user"] = yaml_cfg["thread_sessions_per_user"]

            # Multiplexing flag: accept both the top-level key and the nested
            # gateway.multiplex_profiles form (from_dict resolves the nested
            # fallback, but surface the top-level key here for parity with the
            # other session-scope flags above).
            if "multiplex_profiles" in yaml_cfg:
                gw_data["multiplex_profiles"] = yaml_cfg["multiplex_profiles"]

            gateway_section = yaml_cfg.get("gateway")
            if isinstance(gateway_section, dict) and "max_concurrent_sessions" in gateway_section:
                gw_data["max_concurrent_sessions"] = gateway_section["max_concurrent_sessions"]

            if "max_concurrent_sessions" in yaml_cfg:
                gw_data["max_concurrent_sessions"] = yaml_cfg["max_concurrent_sessions"]

            streaming_cfg = yaml_cfg.get("streaming")
            if not isinstance(streaming_cfg, dict):
                # Fall back to nested gateway.streaming written by
                # ``hermes config set gateway.streaming.*``
                streaming_cfg = yaml_cfg.get("gateway", {}).get("streaming")
            if isinstance(streaming_cfg, dict):
                gw_data["streaming"] = streaming_cfg

            if "reset_triggers" in yaml_cfg:
                gw_data["reset_triggers"] = yaml_cfg["reset_triggers"]

            if "always_log_local" in yaml_cfg:
                gw_data["always_log_local"] = yaml_cfg["always_log_local"]

            if "filter_silence_narration" in yaml_cfg:
                gw_data["filter_silence_narration"] = yaml_cfg[
                    "filter_silence_narration"
                ]

            if "unauthorized_dm_behavior" in yaml_cfg:
                gw_data["unauthorized_dm_behavior"] = _normalize_unauthorized_dm_behavior(
                    yaml_cfg.get("unauthorized_dm_behavior"),
                    "pair",
                )

            # Merge platform config into gw_data so runtime-only settings under
            # ``gateway.platforms`` are loaded the same way as top-level
            # ``platforms``. Merge nested first so top-level config keeps
            # precedence, matching the existing gateway.streaming fallback.
            gateway_cfg = yaml_cfg.get("gateway")
            gateway_platforms = gateway_cfg.get("platforms") if isinstance(gateway_cfg, dict) else None
            platforms_data = gw_data.setdefault("platforms", {})
            if not isinstance(platforms_data, dict):
                platforms_data = {}
                gw_data["platforms"] = platforms_data

            def _merge_platform_map(source_platforms: Any) -> None:
                if not isinstance(source_platforms, dict):
                    return
                for plat_name, plat_block in source_platforms.items():
                    if not isinstance(plat_block, dict):
                        continue
                    existing = platforms_data.get(plat_name, {})
                    if not isinstance(existing, dict):
                        existing = {}
                    # Deep-merge extra dicts so gateway.json defaults survive
                    merged_extra = {**existing.get("extra", {}), **plat_block.get("extra", {})}
                    if "enabled" in plat_block:
                        merged_extra["_enabled_explicit"] = True
                    merged = {**existing, **plat_block}
                    if merged_extra:
                        merged["extra"] = merged_extra
                    platforms_data[plat_name] = merged

            _merge_platform_map(gateway_platforms)
            _merge_platform_map(yaml_cfg.get("platforms"))
            if platforms_data:
                gw_data["platforms"] = platforms_data
            # Iterate built-in platforms plus any registered plugin platforms
            # so plugin authors get the same shared-key bridging (#24836).
            try:
                from openagents_cli.plugins import discover_plugins
                discover_plugins()  # idempotent
                from gateway.platform_registry import platform_registry as _pr
            except Exception as e:
                logger.debug("plugin discovery skipped: %s", e)
                _pr = None

            _shared_loop_targets: list = list(Platform)
            if _pr is not None:
                for _entry in _pr.plugin_entries():
                    try:
                        _plat = Platform(_entry.name)
                    except (ValueError, KeyError):
                        continue
                    if _plat not in _shared_loop_targets:
                        _shared_loop_targets.append(_plat)

            for plat in _shared_loop_targets:
                if plat == Platform.LOCAL:
                    continue
                platform_cfg = yaml_cfg.get(plat.value)
                _cfg_toplevel = isinstance(platform_cfg, dict)
                # Fall back to the platform's block under ``platforms`` /
                # ``gateway.platforms`` so shared-key bridging (allow_from,
                # require_mention, free_response_channels, …) still runs when
                # the user configured the platform only under those nested paths
                # and not via a top-level block.  Mirrors the identical fallback
                # already applied to the apply_yaml_config_fn dispatch below
                # (#44f3e51).
                # Note: ``enabled`` is only written to plat_data from a
                # top-level block (``_cfg_toplevel``); for nested-only configs
                # ``_merge_platform_map`` already merged it with the correct
                # precedence, so re-applying it here would overwrite that.
                if not _cfg_toplevel:
                    for _src in (gateway_platforms, yaml_cfg.get("platforms")):
                        if isinstance(_src, dict):
                            _candidate = _src.get(plat.value)
                            if isinstance(_candidate, dict):
                                platform_cfg = _candidate
                                break
                if not isinstance(platform_cfg, dict):
                    continue
                # Collect bridgeable keys from this platform section
                bridged = {}
                if "unauthorized_dm_behavior" in platform_cfg:
                    bridged["unauthorized_dm_behavior"] = _normalize_unauthorized_dm_behavior(
                        platform_cfg.get("unauthorized_dm_behavior"),
                        gw_data.get("unauthorized_dm_behavior", "pair"),
                    )
                if "notice_delivery" in platform_cfg:
                    bridged["notice_delivery"] = _normalize_notice_delivery(
                        platform_cfg.get("notice_delivery"),
                        "public",
                    )
                if "reply_prefix" in platform_cfg:
                    bridged["reply_prefix"] = platform_cfg["reply_prefix"]
                if "reply_in_thread" in platform_cfg:
                    bridged["reply_in_thread"] = platform_cfg["reply_in_thread"]
                if "require_mention" in platform_cfg:
                    bridged["require_mention"] = platform_cfg["require_mention"]
                if plat == Platform.TELEGRAM and "allowed_chats" in platform_cfg:
                    bridged["allowed_chats"] = platform_cfg["allowed_chats"]
                if plat == Platform.TELEGRAM and "group_allowed_chats" in platform_cfg:
                    bridged["group_allowed_chats"] = platform_cfg["group_allowed_chats"]
                if plat == Platform.TELEGRAM and "allowed_topics" in platform_cfg:
                    bridged["allowed_topics"] = platform_cfg["allowed_topics"]
                if "free_response_channels" in platform_cfg:
                    bridged["free_response_channels"] = platform_cfg["free_response_channels"]
                if "mention_patterns" in platform_cfg:
                    bridged["mention_patterns"] = platform_cfg["mention_patterns"]
                if "exclusive_bot_mentions" in platform_cfg:
                    bridged["exclusive_bot_mentions"] = platform_cfg["exclusive_bot_mentions"]
                if plat == Platform.TELEGRAM and "observe_unmentioned_group_messages" in platform_cfg:
                    bridged["observe_unmentioned_group_messages"] = platform_cfg["observe_unmentioned_group_messages"]
                if "dm_policy" in platform_cfg:
                    bridged["dm_policy"] = platform_cfg["dm_policy"]
                if "allow_from" in platform_cfg:
                    bridged["allow_from"] = platform_cfg["allow_from"]
                if "allow_admin_from" in platform_cfg:
                    bridged["allow_admin_from"] = platform_cfg["allow_admin_from"]
                if "user_allowed_commands" in platform_cfg:
                    bridged["user_allowed_commands"] = platform_cfg["user_allowed_commands"]
                if "group_policy" in platform_cfg:
                    bridged["group_policy"] = platform_cfg["group_policy"]
                if "group_allow_from" in platform_cfg:
                    bridged["group_allow_from"] = platform_cfg["group_allow_from"]
                if "group_allow_admin_from" in platform_cfg:
                    bridged["group_allow_admin_from"] = platform_cfg["group_allow_admin_from"]
                if "group_user_allowed_commands" in platform_cfg:
                    bridged["group_user_allowed_commands"] = platform_cfg["group_user_allowed_commands"]
                if plat in {Platform.DISCORD, Platform.SLACK} and "channel_skill_bindings" in platform_cfg:
                    bridged["channel_skill_bindings"] = platform_cfg["channel_skill_bindings"]
                if "channel_prompts" in platform_cfg:
                    channel_prompts = platform_cfg["channel_prompts"]
                    if isinstance(channel_prompts, dict):
                        bridged["channel_prompts"] = {str(k): v for k, v in channel_prompts.items()}
                    else:
                        bridged["channel_prompts"] = channel_prompts
                if "gateway_restart_notification" in platform_cfg:
                    bridged["gateway_restart_notification"] = platform_cfg["gateway_restart_notification"]
                enabled_was_explicit = _cfg_toplevel and "enabled" in platform_cfg
                if not bridged and not enabled_was_explicit:
                    continue
                plat_data, extra = _ensure_platform_extra_dict(platforms_data, plat.value)
                if enabled_was_explicit:
                    plat_data["enabled"] = platform_cfg["enabled"]
                    # Mark the explicit enable/disable so the registry-driven
                    # plugin-enable pass in _apply_env_overrides honors an
                    # explicit ``enabled: false`` for migrated plugin platforms
                    # (slack, telegram, matrix, dingtalk, whatsapp, feishu …)
                    # instead of re-enabling them on token/SDK presence. #41112.
                    extra["_enabled_explicit"] = True
                extra.update(bridged)

            # Plugin-owned YAML→env config bridges (#24836).  See
            # ``PlatformEntry.apply_yaml_config_fn`` for the hook contract.
            # Order: shared-key loop (above) → this dispatch → legacy hardcoded
            # blocks (below; no-op when a hook already set their env var) →
            # ``_apply_env_overrides()`` after ``GatewayConfig.from_dict``.
            if _pr is not None:
                for entry in _pr.all_entries():
                    if entry.apply_yaml_config_fn is None:
                        continue
                    platform_cfg = yaml_cfg.get(entry.name)
                    # Fall back to the platform's block under ``platforms`` /
                    # ``gateway.platforms`` so adapter hooks still run when the
                    # user configured the platform only under those nested paths
                    # (e.g. ``platforms.discord.extra.allow_from``) and not via a
                    # top-level ``discord:`` block.
                    if not isinstance(platform_cfg, dict):
                        for _src in (gateway_platforms, yaml_cfg.get("platforms")):
                            if isinstance(_src, dict):
                                _candidate = _src.get(entry.name)
                                if isinstance(_candidate, dict):
                                    platform_cfg = _candidate
                                    break
                    if not isinstance(platform_cfg, dict):
                        continue
                    try:
                        seeded = entry.apply_yaml_config_fn(yaml_cfg, platform_cfg)
                    except Exception as e:
                        logger.debug(
                            "apply_yaml_config_fn for %s raised: %s",
                            entry.name, e,
                        )
                        continue
                    if not isinstance(seeded, dict) or not seeded:
                        continue
                    _, extra = _ensure_platform_extra_dict(platforms_data, entry.name)
                    extra.update(seeded)

            # Slack settings → env vars: migrated to the slack plugin's
            # ``apply_yaml_config_fn`` hook (see plugins/platforms/slack/
            # adapter.py::_apply_yaml_config), dispatched in the
            # ``apply_yaml_config_fn`` loop above. #41112 / #3823.

            # Bridge top-level require_mention to Telegram when the telegram: section
            # does not already provide one.  Users often write "require_mention: true"
            # at the top level alongside group_sessions_per_user, expecting it to work
            # the same way (#3979).
            _tl_require_mention = yaml_cfg.get("require_mention")
            if _tl_require_mention is not None:
                _tg_section = yaml_cfg.get("telegram") or {}
                if "require_mention" not in _tg_section:
                    _tg_plat = platforms_data.setdefault(Platform.TELEGRAM.value, {})
                    _tg_extra = _tg_plat.setdefault("extra", {})
                    _tg_extra.setdefault("require_mention", _tl_require_mention)
                    # Also bridge to the TELEGRAM_REQUIRE_MENTION env var that the
                    # adapter reads at runtime.  This used to live in the telegram_cfg
                    # block in core; it stays in core because it keys off the TOP-LEVEL
                    # require_mention (not a telegram: block), so the telegram plugin's
                    # apply_yaml_config_fn hook — which only runs when a telegram config
                    # block exists — can't cover the no-telegram-block case (#3979).
                    if not os.getenv("TELEGRAM_REQUIRE_MENTION"):
                        os.environ["TELEGRAM_REQUIRE_MENTION"] = str(_tl_require_mention).lower()

            # Telegram settings → env vars / extra: migrated to the telegram
            # plugin's apply_yaml_config_fn hook
            # (plugins/platforms/telegram/adapter.py). #41112 / #3823.

            # WhatsApp settings → env vars: migrated to the whatsapp plugin's
            # apply_yaml_config_fn hook (plugins/platforms/whatsapp/adapter.py).
            # #41112 / #3823.

            # Signal settings → env vars (env vars take precedence)
            signal_cfg = yaml_cfg.get("signal", {})
            if isinstance(signal_cfg, dict):
                if "require_mention" in signal_cfg and not os.getenv("SIGNAL_REQUIRE_MENTION"):
                    os.environ["SIGNAL_REQUIRE_MENTION"] = str(signal_cfg["require_mention"]).lower()

            # DingTalk settings → env vars: migrated to the dingtalk plugin's
            # apply_yaml_config_fn hook (plugins/platforms/dingtalk/adapter.py).
            # #41112 / #3823.

            # Mattermost config bridge moved into plugins/platforms/mattermost/
            # adapter.py::_apply_yaml_config — see #25443 (apply_yaml_config_fn).

            # Matrix settings → env vars: migrated to the matrix plugin's
            # apply_yaml_config_fn hook (plugins/platforms/matrix/adapter.py).
            # #41112 / #3823.

            # Feishu settings → env vars: migrated to the feishu plugin's
            # apply_yaml_config_fn hook (plugins/platforms/feishu/adapter.py).
            # #41112 / #3823.

=======
        config_loader.load_yaml_layer(_home, gw_data)
>>>>>>> rb/tag
    except Exception as e:
        logger.warning(
            # DingTalk settings → env vars: migrated to the dingtalk plugin's apply_yaml_config_fn hook
            # (plugins/platforms/dingtalk/adapter.py). #41112 / #3823.
            # Mattermost config bridge moved into plugins/platforms/mattermost/
            # adapter.py::_apply_yaml_config — see #25443 (apply_yaml_config_fn).
            # Matrix settings → env vars: migrated to the matrix plugin's apply_yaml_config_fn hook
            # (plugins/platforms/matrix/adapter.py). #41112 / #3823.
            # Feishu settings → env vars: migrated to the feishu plugin's apply_yaml_config_fn hook
            # (plugins/platforms/feishu/adapter.py). #41112 / #3823.
            "Failed to process config.yaml — falling back to .env / gateway.json values. "
            "Check %s for syntax errors. Error: %s",
            _home / "config.yaml", e,
        )

    config = GatewayConfig.from_dict(gw_data)
    _apply_env_overrides(config)
    _validate_gateway_config(config)
    return config


def _validate_gateway_config(config: "GatewayConfig") -> None:
    """Validate and sanitize a loaded GatewayConfig in place (after all sources are merged)."""
    try:
        # Reject known-weak placeholder tokens. Ported from openclaw/openclaw#64586: users who copy
        # .env.example without changing placeholder values get a clear startup error instead of a confusing
        # "auth failed" from the platform API.
        from openagents_cli.auth import has_usable_secret
    except ImportError:
        has_usable_secret = None

    token_platforms = [
        (p, c, PLATFORM_TOKEN_ENV_NAMES[p]) for p, c in config.platforms.items()
        if c.enabled and p in PLATFORM_TOKEN_ENV_NAMES and c.token is not None
    ]
    for platform, pconfig, env_name in token_platforms:  # an empty token won't connect; say so
        if not pconfig.token.strip():
            logger.warning("%s is enabled but %s is empty. The adapter will likely fail to connect.", platform.value, env_name)
    if has_usable_secret is None:
        return
    for platform, pconfig, env_name in token_platforms:  # reject placeholder tokens (copied .env.example)
        token = pconfig.token
        if token.strip() and not has_usable_secret(token, min_length=4):
            logger.error(
                "%s is enabled but %s is set to a placeholder value ('%s'). "
                "Set a real bot token before starting the gateway. "
                "The adapter will NOT be started.",
                platform.value, env_name, token.strip()[:6] + "...",
            )
            pconfig.enabled = False


def _apply_env_overrides(config: GatewayConfig) -> None:
    """Apply environment variable overrides to config (see ``gateway.config_env``)."""
    from gateway.config_env import _apply_env_overrides as _impl
    _impl(config)


<<<<<<< HEAD
        platform_config = config.platforms[platform]
        # Read (don't pop) the explicit-enable marker: the registry-driven
        # plugin-enable pass later in this function also needs it to avoid
        # re-enabling a platform the user explicitly disabled (migrated plugin
        # platforms — telegram, matrix — flow through here too, #41112). The
        # flag is cleared once for all platforms in the final cleanup at the
        # end of _apply_env_overrides.
        enabled_was_explicit = bool(platform_config.extra.get("_enabled_explicit", False))
        if not platform_config.enabled and not enabled_was_explicit:
            platform_config.enabled = True
        return platform_config
    
    # Telegram
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        telegram_config = _enable_from_env(Platform.TELEGRAM)
        telegram_config.token = telegram_token
    
    # Reply threading mode for Telegram (off/first/all)
    telegram_reply_mode = os.getenv("TELEGRAM_REPLY_TO_MODE", "").lower()
    if telegram_reply_mode in {"off", "first", "all"}:
        if Platform.TELEGRAM not in config.platforms:
            config.platforms[Platform.TELEGRAM] = PlatformConfig()
        config.platforms[Platform.TELEGRAM].reply_to_mode = telegram_reply_mode
    
    telegram_fallback_ips = os.getenv("TELEGRAM_FALLBACK_IPS", "")
    if telegram_fallback_ips:
        if Platform.TELEGRAM not in config.platforms:
            config.platforms[Platform.TELEGRAM] = PlatformConfig()
        config.platforms[Platform.TELEGRAM].extra["fallback_ips"] = [
            ip.strip() for ip in telegram_fallback_ips.split(",") if ip.strip()
        ]

    telegram_home = os.getenv("TELEGRAM_HOME_CHANNEL")
    if telegram_home and Platform.TELEGRAM in config.platforms:
        config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
            platform=Platform.TELEGRAM,
            chat_id=telegram_home,
            name=os.getenv("TELEGRAM_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("TELEGRAM_HOME_CHANNEL_THREAD_ID") or None,
        )
    
    # Discord
    discord_token = os.getenv("DISCORD_BOT_TOKEN")
    if discord_token:
        discord_config = _enable_from_env(Platform.DISCORD)
        discord_config.token = discord_token
    
    discord_home = os.getenv("DISCORD_HOME_CHANNEL")
    if discord_home and Platform.DISCORD in config.platforms:
        config.platforms[Platform.DISCORD].home_channel = HomeChannel(
            platform=Platform.DISCORD,
            chat_id=discord_home,
            name=os.getenv("DISCORD_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("DISCORD_HOME_CHANNEL_THREAD_ID") or None,
        )
    
    # Reply threading mode for Discord (off/first/all)
    discord_reply_mode = os.getenv("DISCORD_REPLY_TO_MODE", "").lower()
    if discord_reply_mode in {"off", "first", "all"}:
        if Platform.DISCORD not in config.platforms:
            config.platforms[Platform.DISCORD] = PlatformConfig()
        config.platforms[Platform.DISCORD].reply_to_mode = discord_reply_mode
    
    # WhatsApp (typically uses different auth mechanism)
    whatsapp_enabled = os.getenv("WHATSAPP_ENABLED", "").lower() in {"true", "1", "yes"}
    whatsapp_disabled_explicitly = os.getenv("WHATSAPP_ENABLED", "").lower() in {"false", "0", "no"}
    if Platform.WHATSAPP in config.platforms:
        # YAML config exists — respect explicit disable
        wa_cfg = config.platforms[Platform.WHATSAPP]
        if whatsapp_disabled_explicitly:
            wa_cfg.enabled = False
        elif whatsapp_enabled:
            wa_cfg.enabled = True
        # else: keep whatever the YAML set
    elif whatsapp_enabled:
        config.platforms[Platform.WHATSAPP] = PlatformConfig(enabled=True)
    whatsapp_home = os.getenv("WHATSAPP_HOME_CHANNEL")
    if whatsapp_home and Platform.WHATSAPP in config.platforms:
        config.platforms[Platform.WHATSAPP].home_channel = HomeChannel(
            platform=Platform.WHATSAPP,
            chat_id=whatsapp_home,
            name=os.getenv("WHATSAPP_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("WHATSAPP_HOME_CHANNEL_THREAD_ID") or None,
        )

    # WhatsApp Cloud API (official Business Platform via Meta).
    # Distinct from the Baileys bridge: pure HTTP graph.facebook.com calls
    # outbound, public webhook inbound. Both adapters can run in parallel
    # against different phone numbers.
    whatsapp_cloud_phone_id = os.getenv("WHATSAPP_CLOUD_PHONE_NUMBER_ID")
    whatsapp_cloud_token = os.getenv("WHATSAPP_CLOUD_ACCESS_TOKEN")
    if whatsapp_cloud_phone_id and whatsapp_cloud_token:
        if Platform.WHATSAPP_CLOUD not in config.platforms:
            config.platforms[Platform.WHATSAPP_CLOUD] = PlatformConfig()
        config.platforms[Platform.WHATSAPP_CLOUD].enabled = True
        config.platforms[Platform.WHATSAPP_CLOUD].extra.update({
            "phone_number_id": whatsapp_cloud_phone_id,
            "access_token": whatsapp_cloud_token,
        })
        # Optional: app_id / app_secret (signature verification)
        wa_cloud_app_id = os.getenv("WHATSAPP_CLOUD_APP_ID")
        if wa_cloud_app_id:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["app_id"] = wa_cloud_app_id
        wa_cloud_app_secret = os.getenv("WHATSAPP_CLOUD_APP_SECRET")
        if wa_cloud_app_secret:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["app_secret"] = wa_cloud_app_secret
        # Optional: WABA id (analytics, future use)
        wa_cloud_waba_id = os.getenv("WHATSAPP_CLOUD_WABA_ID")
        if wa_cloud_waba_id:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["waba_id"] = wa_cloud_waba_id
        # Webhook verify token — Meta hub.verify_token shared secret
        wa_cloud_verify_token = os.getenv("WHATSAPP_CLOUD_VERIFY_TOKEN")
        if wa_cloud_verify_token:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["verify_token"] = wa_cloud_verify_token
        # Webhook server bind config (defaults baked into the adapter)
        wa_cloud_host = os.getenv("WHATSAPP_CLOUD_WEBHOOK_HOST")
        if wa_cloud_host:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["webhook_host"] = wa_cloud_host
        wa_cloud_port = os.getenv("WHATSAPP_CLOUD_WEBHOOK_PORT")
        if wa_cloud_port:
            try:
                config.platforms[Platform.WHATSAPP_CLOUD].extra["webhook_port"] = int(wa_cloud_port)
            except ValueError:
                pass
        wa_cloud_path = os.getenv("WHATSAPP_CLOUD_WEBHOOK_PATH")
        if wa_cloud_path:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["webhook_path"] = wa_cloud_path
        # Graph API version override (rarely needed)
        wa_cloud_api_version = os.getenv("WHATSAPP_CLOUD_API_VERSION")
        if wa_cloud_api_version:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["api_version"] = wa_cloud_api_version
    whatsapp_cloud_home = os.getenv("WHATSAPP_CLOUD_HOME_CHANNEL")
    if whatsapp_cloud_home and Platform.WHATSAPP_CLOUD in config.platforms:
        config.platforms[Platform.WHATSAPP_CLOUD].home_channel = HomeChannel(
            platform=Platform.WHATSAPP_CLOUD,
            chat_id=whatsapp_cloud_home,
            name=os.getenv("WHATSAPP_CLOUD_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("WHATSAPP_CLOUD_HOME_CHANNEL_THREAD_ID") or None,
        )

    # Slack
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if slack_token:
        if Platform.SLACK not in config.platforms:
            # No yaml config for Slack — env-only setup, enable it
            config.platforms[Platform.SLACK] = PlatformConfig()
            config.platforms[Platform.SLACK].enabled = True
        else:
            slack_config = config.platforms[Platform.SLACK]
            # Read (don't pop) the explicit-enable marker: the registry-driven
            # plugin-enable pass below also needs it to avoid re-enabling a
            # platform the user explicitly disabled (Slack is now a plugin
            # entry — #41112). The flag is cleared once for all platforms in
            # the final cleanup at the end of _apply_env_overrides.
            enabled_was_explicit = bool(slack_config.extra.get("_enabled_explicit", False))
            if not slack_config.enabled and not enabled_was_explicit:
                # Top-level Slack settings such as channel prompts should not
                # turn an env-token setup into a disabled platform. Only an
                # explicit slack.enabled/platforms.slack.enabled false should.
                slack_config.enabled = True
        # If yaml config exists, respect its enabled flag (don't override
        # explicit enabled: false). Token is still stored so skills that
        # send Slack messages can use it without activating the gateway adapter.
        config.platforms[Platform.SLACK].token = slack_token
    slack_home = os.getenv("SLACK_HOME_CHANNEL")
    if slack_home and Platform.SLACK in config.platforms:
        config.platforms[Platform.SLACK].home_channel = HomeChannel(
            platform=Platform.SLACK,
            chat_id=slack_home,
            name=os.getenv("SLACK_HOME_CHANNEL_NAME", ""),
            thread_id=os.getenv("SLACK_HOME_CHANNEL_THREAD_ID") or None,
        )
    
    # Signal
    signal_url = os.getenv("SIGNAL_HTTP_URL")
    signal_account = os.getenv("SIGNAL_ACCOUNT")
    if signal_url and signal_account:
        signal_config = _enable_from_env(Platform.SIGNAL)
        signal_config.extra.update({
            "http_url": signal_url,
            "account": signal_account,
            "ignore_stories": os.getenv("SIGNAL_IGNORE_STORIES", "true").lower() in {"true", "1", "yes"},
        })
    signal_home = os.getenv("SIGNAL_HOME_CHANNEL")
    if signal_home and Platform.SIGNAL in config.platforms:
        config.platforms[Platform.SIGNAL].home_channel = HomeChannel(
            platform=Platform.SIGNAL,
            chat_id=signal_home,
            name=os.getenv("SIGNAL_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("SIGNAL_HOME_CHANNEL_THREAD_ID") or None,
        )

    # Mattermost
    mattermost_token = os.getenv("MATTERMOST_TOKEN")
    if mattermost_token:
        mattermost_url = os.getenv("MATTERMOST_URL", "")
        if not mattermost_url:
            logger.warning("MATTERMOST_TOKEN set but MATTERMOST_URL is missing")
        mattermost_config = _enable_from_env(Platform.MATTERMOST)
        mattermost_config.token = mattermost_token
        mattermost_config.extra["url"] = mattermost_url
    mattermost_home = os.getenv("MATTERMOST_HOME_CHANNEL")
    if mattermost_home and Platform.MATTERMOST in config.platforms:
        config.platforms[Platform.MATTERMOST].home_channel = HomeChannel(
            platform=Platform.MATTERMOST,
            chat_id=mattermost_home,
            name=os.getenv("MATTERMOST_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("MATTERMOST_HOME_CHANNEL_THREAD_ID") or None,
        )

    # Matrix
    matrix_token = os.getenv("MATRIX_ACCESS_TOKEN")
    matrix_homeserver = os.getenv("MATRIX_HOMESERVER", "")
    if matrix_token or os.getenv("MATRIX_PASSWORD"):
        if not matrix_homeserver:
            logger.warning("MATRIX_ACCESS_TOKEN/MATRIX_PASSWORD set but MATRIX_HOMESERVER is missing")
        matrix_config = _enable_from_env(Platform.MATRIX)
        if matrix_token:
            matrix_config.token = matrix_token
        matrix_config.extra["homeserver"] = matrix_homeserver
        matrix_user = os.getenv("MATRIX_USER_ID", "")
        if matrix_user:
            matrix_config.extra["user_id"] = matrix_user
        matrix_password = os.getenv("MATRIX_PASSWORD", "")
        if matrix_password:
            matrix_config.extra["password"] = matrix_password
        matrix_e2ee_mode = os.getenv("MATRIX_E2EE_MODE", "").strip().lower()
        matrix_e2ee = (
            matrix_e2ee_mode in ("required", "require", "optional", "prefer", "preferred")
            or os.getenv("MATRIX_ENCRYPTION", "").lower() in ("true", "1", "yes")
        )
        matrix_config.extra["encryption"] = matrix_e2ee
        if matrix_e2ee_mode:
            matrix_config.extra["e2ee_mode"] = matrix_e2ee_mode
        matrix_device_id = os.getenv("MATRIX_DEVICE_ID", "")
        if matrix_device_id:
            matrix_config.extra["device_id"] = matrix_device_id
    matrix_home = os.getenv("MATRIX_HOME_ROOM")
    if matrix_home and Platform.MATRIX in config.platforms:
        config.platforms[Platform.MATRIX].home_channel = HomeChannel(
            platform=Platform.MATRIX,
            chat_id=matrix_home,
            name=os.getenv("MATRIX_HOME_ROOM_NAME", "Home"),
            thread_id=os.getenv("MATRIX_HOME_ROOM_THREAD_ID") or None,
        )

    # Home Assistant
    hass_token = os.getenv("HASS_TOKEN")
    if hass_token:
        if Platform.HOMEASSISTANT not in config.platforms:
            config.platforms[Platform.HOMEASSISTANT] = PlatformConfig()
        config.platforms[Platform.HOMEASSISTANT].enabled = True
        config.platforms[Platform.HOMEASSISTANT].token = hass_token
        hass_url = os.getenv("HASS_URL")
        if hass_url:
            config.platforms[Platform.HOMEASSISTANT].extra["url"] = hass_url

    # Email
    email_addr = os.getenv("EMAIL_ADDRESS")
    email_pwd = os.getenv("EMAIL_PASSWORD")
    email_imap = os.getenv("EMAIL_IMAP_HOST")
    email_smtp = os.getenv("EMAIL_SMTP_HOST")
    if all([email_addr, email_pwd, email_imap, email_smtp]):
        if Platform.EMAIL not in config.platforms:
            config.platforms[Platform.EMAIL] = PlatformConfig()
        config.platforms[Platform.EMAIL].enabled = True
        config.platforms[Platform.EMAIL].extra.update({
            "address": email_addr,
            "imap_host": email_imap,
            "smtp_host": email_smtp,
        })
    email_home = os.getenv("EMAIL_HOME_ADDRESS")
    if email_home and Platform.EMAIL in config.platforms:
        config.platforms[Platform.EMAIL].home_channel = HomeChannel(
            platform=Platform.EMAIL,
            chat_id=email_home,
            name=os.getenv("EMAIL_HOME_ADDRESS_NAME", "Home"),
            thread_id=os.getenv("EMAIL_HOME_ADDRESS_THREAD_ID") or None,
        )

    # SMS (Twilio)
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    if twilio_sid:
        if Platform.SMS not in config.platforms:
            config.platforms[Platform.SMS] = PlatformConfig()
        config.platforms[Platform.SMS].enabled = True
        config.platforms[Platform.SMS].api_key = os.getenv("TWILIO_AUTH_TOKEN", "")
    sms_home = os.getenv("SMS_HOME_CHANNEL")
    if sms_home and Platform.SMS in config.platforms:
        config.platforms[Platform.SMS].home_channel = HomeChannel(
            platform=Platform.SMS,
            chat_id=sms_home,
            name=os.getenv("SMS_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("SMS_HOME_CHANNEL_THREAD_ID") or None,
        )

    # API Server
    api_server_enabled = os.getenv("API_SERVER_ENABLED", "").lower() in {"true", "1", "yes"}
    from agent.secret_scope import UnscopedSecretError, get_secret

    try:
        api_server_key = get_secret("API_SERVER_KEY", "") or ""
    except UnscopedSecretError:
        api_server_key = ""
    api_server_cors_origins = os.getenv("API_SERVER_CORS_ORIGINS", "")
    api_server_port = os.getenv("API_SERVER_PORT")
    api_server_host = os.getenv("API_SERVER_HOST")
    if api_server_enabled or api_server_key:
        api_server_config = _enable_from_env(Platform.API_SERVER)
        if api_server_key:
            api_server_config.extra["key"] = api_server_key
        if api_server_cors_origins:
            origins = [origin.strip() for origin in api_server_cors_origins.split(",") if origin.strip()]
            if origins:
                api_server_config.extra["cors_origins"] = origins
        if api_server_port:
            try:
                api_server_config.extra["port"] = int(api_server_port)
            except ValueError:
                pass
        if api_server_host:
            api_server_config.extra["host"] = api_server_host
        api_server_model_name = os.getenv("API_SERVER_MODEL_NAME", "")
        if api_server_model_name:
            api_server_config.extra["model_name"] = api_server_model_name

    # Webhook platform
    webhook_enabled = os.getenv("WEBHOOK_ENABLED", "").lower() in {"true", "1", "yes"}
    webhook_port = os.getenv("WEBHOOK_PORT")
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    if webhook_enabled:
        if Platform.WEBHOOK not in config.platforms:
            config.platforms[Platform.WEBHOOK] = PlatformConfig()
        config.platforms[Platform.WEBHOOK].enabled = True
        if webhook_port:
            try:
                config.platforms[Platform.WEBHOOK].extra["port"] = int(webhook_port)
            except ValueError:
                pass
        if webhook_secret:
            config.platforms[Platform.WEBHOOK].extra["secret"] = webhook_secret

    # Microsoft Graph webhook platform
    msgraph_webhook_enabled = os.getenv("MSGRAPH_WEBHOOK_ENABLED", "").lower() in {
        "true",
        "1",
        "yes",
    }
    msgraph_webhook_port = os.getenv("MSGRAPH_WEBHOOK_PORT")
    msgraph_webhook_client_state = os.getenv("MSGRAPH_WEBHOOK_CLIENT_STATE", "")
    msgraph_webhook_resources = os.getenv("MSGRAPH_WEBHOOK_ACCEPTED_RESOURCES", "")
    msgraph_webhook_allowed_cidrs = os.getenv(
        "MSGRAPH_WEBHOOK_ALLOWED_SOURCE_CIDRS", ""
    )
    if (
        msgraph_webhook_enabled
        or Platform.MSGRAPH_WEBHOOK in config.platforms
        or msgraph_webhook_port
        or msgraph_webhook_client_state
        or msgraph_webhook_resources
        or msgraph_webhook_allowed_cidrs
    ):
        if Platform.MSGRAPH_WEBHOOK not in config.platforms:
            config.platforms[Platform.MSGRAPH_WEBHOOK] = PlatformConfig()
        if msgraph_webhook_enabled:
            config.platforms[Platform.MSGRAPH_WEBHOOK].enabled = True
        if msgraph_webhook_port:
            try:
                config.platforms[Platform.MSGRAPH_WEBHOOK].extra["port"] = int(
                    msgraph_webhook_port
                )
            except ValueError:
                pass
        if msgraph_webhook_client_state:
            config.platforms[Platform.MSGRAPH_WEBHOOK].extra["client_state"] = (
                msgraph_webhook_client_state
            )
        if msgraph_webhook_resources:
            resources = [
                resource.strip()
                for resource in msgraph_webhook_resources.split(",")
                if resource.strip()
            ]
            if resources:
                config.platforms[Platform.MSGRAPH_WEBHOOK].extra[
                    "accepted_resources"
                ] = resources
        if msgraph_webhook_allowed_cidrs:
            cidrs = [
                cidr.strip()
                for cidr in msgraph_webhook_allowed_cidrs.split(",")
                if cidr.strip()
            ]
            if cidrs:
                config.platforms[Platform.MSGRAPH_WEBHOOK].extra[
                    "allowed_source_cidrs"
                ] = cidrs

    # DingTalk
    dingtalk_client_id = os.getenv("DINGTALK_CLIENT_ID")
    dingtalk_client_secret = os.getenv("DINGTALK_CLIENT_SECRET")
    if dingtalk_client_id and dingtalk_client_secret:
        if Platform.DINGTALK not in config.platforms:
            config.platforms[Platform.DINGTALK] = PlatformConfig()
        config.platforms[Platform.DINGTALK].enabled = True
        config.platforms[Platform.DINGTALK].extra.update({
            "client_id": dingtalk_client_id,
            "client_secret": dingtalk_client_secret,
        })
        dingtalk_home = os.getenv("DINGTALK_HOME_CHANNEL")
        if dingtalk_home:
            config.platforms[Platform.DINGTALK].home_channel = HomeChannel(
                platform=Platform.DINGTALK,
                chat_id=dingtalk_home,
                name=os.getenv("DINGTALK_HOME_CHANNEL_NAME", "Home"),
                thread_id=os.getenv("DINGTALK_HOME_CHANNEL_THREAD_ID") or None,
            )

    # Feishu / Lark
    feishu_app_id = os.getenv("FEISHU_APP_ID")
    feishu_app_secret = os.getenv("FEISHU_APP_SECRET")
    if feishu_app_id and feishu_app_secret:
        if Platform.FEISHU not in config.platforms:
            config.platforms[Platform.FEISHU] = PlatformConfig()
        config.platforms[Platform.FEISHU].enabled = True
        config.platforms[Platform.FEISHU].extra.update({
            "app_id": feishu_app_id,
            "app_secret": feishu_app_secret,
            "domain": os.getenv("FEISHU_DOMAIN", "feishu"),
            "connection_mode": os.getenv("FEISHU_CONNECTION_MODE", "websocket"),
        })
        feishu_encrypt_key = os.getenv("FEISHU_ENCRYPT_KEY", "")
        if feishu_encrypt_key:
            config.platforms[Platform.FEISHU].extra["encrypt_key"] = feishu_encrypt_key
        feishu_verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
        if feishu_verification_token:
            config.platforms[Platform.FEISHU].extra["verification_token"] = feishu_verification_token
        feishu_home = os.getenv("FEISHU_HOME_CHANNEL")
        if feishu_home:
            config.platforms[Platform.FEISHU].home_channel = HomeChannel(
                platform=Platform.FEISHU,
                chat_id=feishu_home,
                name=os.getenv("FEISHU_HOME_CHANNEL_NAME", "Home"),
                thread_id=os.getenv("FEISHU_HOME_CHANNEL_THREAD_ID") or None,
            )

    # WeCom (Enterprise WeChat)
    wecom_bot_id = os.getenv("WECOM_BOT_ID")
    wecom_secret = os.getenv("WECOM_SECRET")
    if wecom_bot_id and wecom_secret:
        if Platform.WECOM not in config.platforms:
            config.platforms[Platform.WECOM] = PlatformConfig()
        config.platforms[Platform.WECOM].enabled = True
        config.platforms[Platform.WECOM].extra.update({
            "bot_id": wecom_bot_id,
            "secret": wecom_secret,
        })
        wecom_ws_url = os.getenv("WECOM_WEBSOCKET_URL", "")
        if wecom_ws_url:
            config.platforms[Platform.WECOM].extra["websocket_url"] = wecom_ws_url
        wecom_home = os.getenv("WECOM_HOME_CHANNEL")
        if wecom_home:
            config.platforms[Platform.WECOM].home_channel = HomeChannel(
                platform=Platform.WECOM,
                chat_id=wecom_home,
                name=os.getenv("WECOM_HOME_CHANNEL_NAME", "Home"),
                thread_id=os.getenv("WECOM_HOME_CHANNEL_THREAD_ID") or None,
            )

    # WeCom callback mode (self-built apps)
    wecom_callback_corp_id = os.getenv("WECOM_CALLBACK_CORP_ID")
    wecom_callback_corp_secret = os.getenv("WECOM_CALLBACK_CORP_SECRET")
    if wecom_callback_corp_id and wecom_callback_corp_secret:
        if Platform.WECOM_CALLBACK not in config.platforms:
            config.platforms[Platform.WECOM_CALLBACK] = PlatformConfig()
        config.platforms[Platform.WECOM_CALLBACK].enabled = True
        config.platforms[Platform.WECOM_CALLBACK].extra.update({
            "corp_id": wecom_callback_corp_id,
            "corp_secret": wecom_callback_corp_secret,
            "agent_id": os.getenv("WECOM_CALLBACK_AGENT_ID", ""),
            "token": os.getenv("WECOM_CALLBACK_TOKEN", ""),
            "encoding_aes_key": os.getenv("WECOM_CALLBACK_ENCODING_AES_KEY", ""),
            "host": os.getenv("WECOM_CALLBACK_HOST", "0.0.0.0"),
            "port": env_int("WECOM_CALLBACK_PORT", 8645),
        })

    # Weixin (personal WeChat via iLink Bot API)
    weixin_token = os.getenv("WEIXIN_TOKEN")
    weixin_account_id = os.getenv("WEIXIN_ACCOUNT_ID")
    if weixin_token or weixin_account_id:
        if Platform.WEIXIN not in config.platforms:
            config.platforms[Platform.WEIXIN] = PlatformConfig()
        config.platforms[Platform.WEIXIN].enabled = True
        if weixin_token:
            config.platforms[Platform.WEIXIN].token = weixin_token
        extra = config.platforms[Platform.WEIXIN].extra
        if weixin_account_id:
            extra["account_id"] = weixin_account_id
        weixin_base_url = os.getenv("WEIXIN_BASE_URL", "").strip()
        if weixin_base_url:
            extra["base_url"] = weixin_base_url.rstrip("/")
        weixin_cdn_base_url = os.getenv("WEIXIN_CDN_BASE_URL", "").strip()
        if weixin_cdn_base_url:
            extra["cdn_base_url"] = weixin_cdn_base_url.rstrip("/")
        weixin_dm_policy = os.getenv("WEIXIN_DM_POLICY", "").strip().lower()
        if weixin_dm_policy:
            extra["dm_policy"] = weixin_dm_policy
        weixin_group_policy = os.getenv("WEIXIN_GROUP_POLICY", "").strip().lower()
        if weixin_group_policy:
            extra["group_policy"] = weixin_group_policy
        weixin_allowed_users = os.getenv("WEIXIN_ALLOWED_USERS", "").strip()
        if weixin_allowed_users:
            extra["allow_from"] = weixin_allowed_users
        weixin_group_allowed_users = os.getenv("WEIXIN_GROUP_ALLOWED_USERS", "").strip()
        if weixin_group_allowed_users:
            extra["group_allow_from"] = weixin_group_allowed_users
        weixin_split_multiline = os.getenv("WEIXIN_SPLIT_MULTILINE_MESSAGES", "").strip()
        if weixin_split_multiline:
            extra["split_multiline_messages"] = weixin_split_multiline
        weixin_home = os.getenv("WEIXIN_HOME_CHANNEL", "").strip()
        if weixin_home:
            config.platforms[Platform.WEIXIN].home_channel = HomeChannel(
                platform=Platform.WEIXIN,
                chat_id=weixin_home,
                name=os.getenv("WEIXIN_HOME_CHANNEL_NAME", "Home"),
                thread_id=os.getenv("WEIXIN_HOME_CHANNEL_THREAD_ID") or None,
            )

    # BlueBubbles (iMessage)
    bluebubbles_server_url = os.getenv("BLUEBUBBLES_SERVER_URL")
    bluebubbles_password = os.getenv("BLUEBUBBLES_PASSWORD")
    if bluebubbles_server_url and bluebubbles_password:
        if Platform.BLUEBUBBLES not in config.platforms:
            config.platforms[Platform.BLUEBUBBLES] = PlatformConfig()
        config.platforms[Platform.BLUEBUBBLES].enabled = True
        config.platforms[Platform.BLUEBUBBLES].extra.update({
            "server_url": bluebubbles_server_url.rstrip("/"),
            "password": bluebubbles_password,
            "webhook_host": os.getenv("BLUEBUBBLES_WEBHOOK_HOST", "127.0.0.1"),
            "webhook_port": env_int("BLUEBUBBLES_WEBHOOK_PORT", 8645),
            "webhook_path": os.getenv("BLUEBUBBLES_WEBHOOK_PATH", "/bluebubbles-webhook"),
            "send_read_receipts": os.getenv("BLUEBUBBLES_SEND_READ_RECEIPTS", "true").lower() in {"true", "1", "yes"},
        })
        bluebubbles_require_mention = os.getenv("BLUEBUBBLES_REQUIRE_MENTION")
        if bluebubbles_require_mention is not None:
            config.platforms[Platform.BLUEBUBBLES].extra["require_mention"] = (
                bluebubbles_require_mention.lower() in {"true", "1", "yes", "on"}
            )
        bluebubbles_mention_patterns = os.getenv("BLUEBUBBLES_MENTION_PATTERNS")
        if bluebubbles_mention_patterns:
            try:
                parsed_patterns = json.loads(bluebubbles_mention_patterns)
            except Exception:
                parsed_patterns = [
                    part.strip()
                    for part in bluebubbles_mention_patterns.replace("\n", ",").split(",")
                    if part.strip()
                ]
            config.platforms[Platform.BLUEBUBBLES].extra["mention_patterns"] = parsed_patterns
    bluebubbles_home = os.getenv("BLUEBUBBLES_HOME_CHANNEL")
    if bluebubbles_home and Platform.BLUEBUBBLES in config.platforms:
        config.platforms[Platform.BLUEBUBBLES].home_channel = HomeChannel(
            platform=Platform.BLUEBUBBLES,
            chat_id=bluebubbles_home,
            name=os.getenv("BLUEBUBBLES_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("BLUEBUBBLES_HOME_CHANNEL_THREAD_ID") or None,
        )

    # QQ (Official Bot API v2)
    qq_app_id = os.getenv("QQ_APP_ID")
    qq_client_secret = os.getenv("QQ_CLIENT_SECRET")
    if qq_app_id or qq_client_secret:
        if Platform.QQBOT not in config.platforms:
            config.platforms[Platform.QQBOT] = PlatformConfig()
        config.platforms[Platform.QQBOT].enabled = True
        extra = config.platforms[Platform.QQBOT].extra
        if qq_app_id:
            extra["app_id"] = qq_app_id
        if qq_client_secret:
            extra["client_secret"] = qq_client_secret
        qq_allowed_users = os.getenv("QQ_ALLOWED_USERS", "").strip()
        if qq_allowed_users:
            extra["allow_from"] = qq_allowed_users
        qq_group_allowed = os.getenv("QQ_GROUP_ALLOWED_USERS", "").strip()
        if qq_group_allowed:
            extra["group_allow_from"] = qq_group_allowed
        qq_home = os.getenv("QQBOT_HOME_CHANNEL", "").strip()
        qq_home_name_env = "QQBOT_HOME_CHANNEL_NAME"
        if not qq_home:
            # Back-compat: accept the pre-rename name and log a one-time warning.
            legacy_home = os.getenv("QQ_HOME_CHANNEL", "").strip()
            if legacy_home:
                qq_home = legacy_home
                qq_home_name_env = "QQ_HOME_CHANNEL_NAME"
                logging.getLogger(__name__).warning(
                    "QQ_HOME_CHANNEL is deprecated; rename to QQBOT_HOME_CHANNEL "
                    "in your .env for consistency with the platform key."
                )
        if qq_home:
            config.platforms[Platform.QQBOT].home_channel = HomeChannel(
                platform=Platform.QQBOT,
                chat_id=qq_home,
                name=os.getenv("QQBOT_HOME_CHANNEL_NAME") or os.getenv(qq_home_name_env, "Home"),
                thread_id=(
                    os.getenv("QQBOT_HOME_CHANNEL_THREAD_ID")
                    or os.getenv("QQ_HOME_CHANNEL_THREAD_ID")
                    or None
                ),
            )

    # Yuanbao — YUANBAO_APP_ID preferred
    yuanbao_app_id = os.getenv("YUANBAO_APP_ID") or os.getenv("YUANBAO_APP_KEY")
    yuanbao_app_secret = os.getenv("YUANBAO_APP_SECRET")
    if yuanbao_app_id and yuanbao_app_secret:
        if Platform.YUANBAO not in config.platforms:
            config.platforms[Platform.YUANBAO] = PlatformConfig()
        config.platforms[Platform.YUANBAO].enabled = True
        extra = config.platforms[Platform.YUANBAO].extra
        extra["app_id"] = yuanbao_app_id
        extra["app_secret"] = yuanbao_app_secret
        yuanbao_bot_id = os.getenv("YUANBAO_BOT_ID")
        if yuanbao_bot_id:
            extra["bot_id"] = yuanbao_bot_id
        yuanbao_ws_url = os.getenv("YUANBAO_WS_URL")
        if yuanbao_ws_url:
            extra["ws_url"] = yuanbao_ws_url
        yuanbao_api_domain = os.getenv("YUANBAO_API_DOMAIN")
        if yuanbao_api_domain:
            extra["api_domain"] = yuanbao_api_domain
        yuanbao_route_env = os.getenv("YUANBAO_ROUTE_ENV")
        if yuanbao_route_env:
            extra["route_env"] = yuanbao_route_env
        yuanbao_home = os.getenv("YUANBAO_HOME_CHANNEL")
        if yuanbao_home:
            config.platforms[Platform.YUANBAO].home_channel = HomeChannel(
                platform=Platform.YUANBAO,
                chat_id=yuanbao_home,
                name=os.getenv("YUANBAO_HOME_CHANNEL_NAME", "Home"),
                thread_id=os.getenv("YUANBAO_HOME_CHANNEL_THREAD_ID") or None,
            )
        yuanbao_dm_policy = os.getenv("YUANBAO_DM_POLICY")
        if yuanbao_dm_policy:
            extra["dm_policy"] = yuanbao_dm_policy.strip().lower()
        yuanbao_dm_allow_from = os.getenv("YUANBAO_DM_ALLOW_FROM")
        if yuanbao_dm_allow_from:
            extra["dm_allow_from"] = yuanbao_dm_allow_from
        yuanbao_group_policy = os.getenv("YUANBAO_GROUP_POLICY")
        if yuanbao_group_policy:
            extra["group_policy"] = yuanbao_group_policy.strip().lower()
        yuanbao_group_allow_from = os.getenv("YUANBAO_GROUP_ALLOW_FROM")
        if yuanbao_group_allow_from:
            extra["group_allow_from"] = yuanbao_group_allow_from

    # Session settings
    idle_minutes = os.getenv("SESSION_IDLE_MINUTES")
    if idle_minutes:
        try:
            config.default_reset_policy.idle_minutes = int(idle_minutes)
        except ValueError:
            pass
    
    reset_hour = os.getenv("SESSION_RESET_HOUR")
    if reset_hour:
        try:
            config.default_reset_policy.at_hour = int(reset_hour)
        except ValueError:
            pass

    # Registry-driven enable for plugin platforms.  Built-ins have explicit
    # blocks above; plugins expose check_fn() which is the single source of
    # truth for "are my env vars set?".  When it returns True, ensure the
    # platform is enabled so start() will create its adapter.  Plugins that
    # need to seed ``PlatformConfig.extra`` from env vars (e.g. Google Chat's
    # project_id / subscription_name) can supply ``env_enablement_fn`` on
    # their PlatformEntry — called here BEFORE adapter construction.
    #
    # Enablement gate (#31116): when a plugin registers ``is_connected``
    # (the "has the user actually configured credentials for this?" check),
    # we MUST consult it before flipping ``enabled = True``.  Otherwise
    # ``check_fn`` alone — which for adapter plugins typically just
    # verifies the SDK is importable / lazy-installs it — silently enables
    # platforms the user never opted into, and the gateway then tries to
    # connect to Discord / Teams / Google Chat with no token and emits
    # noisy retry-forever errors.  ``_platform_status`` was already fixed
    # for the same bug class in commit 7849a3d73; this is the runtime
    # counterpart.
    try:
        from openagents_cli.plugins import discover_plugins
        discover_plugins()  # idempotent
        from gateway.platform_registry import platform_registry
        for entry in platform_registry.plugin_entries():
            try:
                platform = Platform(entry.name)
            except Exception as e:
                logger.debug("unknown platform name %r: %s", entry.name, e)
                continue
            existing_cfg = config.platforms.get(platform)
            # Respect an explicit ``enabled: false`` (YAML / gateway.json /
            # dashboard PUT).  ``_enabled_explicit`` is set in
            # load_gateway_config() (via _merge_platform_map / the shared-key
            # loop) when the user wrote ``enabled`` for this platform; if they
            # explicitly disabled it, never re-enable here just because
            # check_fn() / is_connected() pass (e.g. a token is present but the
            # user set telegram.enabled: false). #41112.
            if (
                existing_cfg is not None
                and not existing_cfg.enabled
                and bool((existing_cfg.extra or {}).get("_enabled_explicit", False))
            ):
                continue
            # Seed candidate extras from ``env_enablement_fn`` so plugins
            # whose ``is_connected`` reads ``config.extra`` (e.g. Google
            # Chat's ``_is_connected`` checks ``config.extra["project_id"]``)
            # see the same state they will after enablement. Without this,
            # Google-Chat-on-env-vars-only setups silently fail the gate
            # below even though the user is configured.  Plugins whose
            # ``is_connected`` reads env vars directly (Discord, IRC,
            # Teams, LINE, ntfy, Simplex) are unaffected; this only
            # restores Google Chat.
            seed_for_probe = None
            if entry.env_enablement_fn is not None:
                try:
                    seed_for_probe = entry.env_enablement_fn()
                except Exception as e:
                    logger.debug(
                        "env_enablement_fn for %s raised: %s", entry.name, e
                    )
                    seed_for_probe = None

            # Only consult is_connected for platforms that are NOT already
            # explicitly configured in YAML / env (existing_cfg with
            # enabled=True means the user wrote it themselves or another
            # env-var bridge enabled it — keep that decision).
            if existing_cfg is None or not existing_cfg.enabled:
                if entry.is_connected is not None:
                    try:
                        # Probe with ``enabled=True`` since we're asking
                        # "would this plugin BE configured if we enabled
                        # it?" not "is it currently enabled?". Google
                        # Chat's ``_is_connected`` short-circuits on
                        # ``config.enabled`` being False, which on the
                        # default ``PlatformConfig()`` would fail the
                        # gate even with proper env vars set.
                        if existing_cfg is not None:
                            probe_cfg = existing_cfg
                            if not probe_cfg.enabled:
                                probe_cfg = PlatformConfig(
                                    enabled=True,
                                    extra=dict(probe_cfg.extra or {}),
                                )
                        else:
                            probe_cfg = PlatformConfig(enabled=True)
                        if isinstance(seed_for_probe, dict) and seed_for_probe:
                            # Don't mutate ``existing_cfg``; the probe gets
                            # a transient view with env-seeded extras layered
                            # on top of whatever's already there.
                            probe_extra = dict(getattr(probe_cfg, "extra", {}) or {})
                            for k, v in seed_for_probe.items():
                                if k == "home_channel":
                                    continue
                                probe_extra.setdefault(k, v)
                            probe_cfg = PlatformConfig(
                                enabled=True,
                                extra=probe_extra,
                            )
                        configured = bool(entry.is_connected(probe_cfg))
                    except Exception as exc:
                        logger.debug(
                            "is_connected for %s raised: %s — skipping enablement",
                            entry.name, exc,
                        )
                        configured = False
                    if not configured:
                        logger.debug(
                            "Plugin platform '%s' available but not configured "
                            "(is_connected returned False) — skipping enable",
                            entry.name,
                        )
                        continue
            # Verify dependencies LAST — only for platforms that are already
            # enabled or passed the credential gate above.  For adapter plugins
            # ``check_fn`` lazy-INSTALLS the platform SDK (pip) as a side
            # effect, so running it as an unconditional sweep over every
            # registered platform made ``load_gateway_config()`` pip-install
            # Discord/Telegram/Slack/Feishu/Dingtalk on every call — including
            # the desktop/dashboard readiness probe (``GET /api/status``, which
            # awaits this synchronously) — even when the user configured none
            # of them.  That blocked startup until every install finished and
            # caused the desktop app to time out and boot-loop (stuck at 94%).
            try:
                if not entry.check_fn():
                    continue
            except Exception as e:
                logger.debug("check_fn for %s raised: %s", entry.name, e)
                continue
            if platform not in config.platforms:
                config.platforms[platform] = PlatformConfig()
            config.platforms[platform].enabled = True
            # Commit env-seeded extras onto the now-enabled platform.
            # We've already called ``env_enablement_fn`` above (for the
            # probe); reuse that result instead of calling it twice.
            if isinstance(seed_for_probe, dict) and seed_for_probe:
                seed = dict(seed_for_probe)
                # Extract the home_channel dict (if provided) so we wire it
                # up as a proper HomeChannel dataclass.  Everything else is
                # merged into ``extra``.
                home = seed.pop("home_channel", None)
                config.platforms[platform].extra.update(seed)
                if isinstance(home, dict) and home.get("chat_id"):
                    config.platforms[platform].home_channel = HomeChannel(
                        platform=platform,
                        chat_id=str(home["chat_id"]),
                        name=str(home.get("name") or "Home"),
                        thread_id=(
                            str(home["thread_id"])
                            if home.get("thread_id")
                            else None
                        ),
                    )
    except Exception as e:
        logger.debug("Plugin platform enable pass failed: %s", e)

    # Relay (generic connector-fronted platform, EXPERIMENTAL). Enabled when a
    # connector relay URL is configured via GATEWAY_RELAY_URL (env) or
    # gateway.relay_url (config.yaml). The adapter is registered into the
    # platform_registry at gateway startup (gateway.relay.register_relay_adapter)
    # and dials OUT to the connector — so, like Telegram/Matrix, it has no public
    # inbound port and just needs Platform.RELAY present+enabled in
    # config.platforms for start_gateway()'s connect loop to bring it up. The
    # connected-checker (Platform.RELAY in _PLATFORM_CONNECTED_CHECKERS) keys on
    # extra["relay_url"], so mirror the URL into extra here.
    relay_url_env = os.getenv("GATEWAY_RELAY_URL", "").strip()
    relay_url_yaml = ""
    existing_relay = config.platforms.get(Platform.RELAY)
    if existing_relay is not None:
        relay_url_yaml = str(existing_relay.extra.get("relay_url") or "").strip()
    relay_url_val = relay_url_env or relay_url_yaml
    if relay_url_val:
        relay_config = _enable_from_env(Platform.RELAY)
        relay_config.extra["relay_url"] = relay_url_val.rstrip("/")

    for platform_config in config.platforms.values():
        platform_config.extra.pop("_enabled_explicit", None)
||||||| cf299e9a01
        platform_config = config.platforms[platform]
        # Read (don't pop) the explicit-enable marker: the registry-driven
        # plugin-enable pass later in this function also needs it to avoid
        # re-enabling a platform the user explicitly disabled (migrated plugin
        # platforms — telegram, matrix — flow through here too, #41112). The
        # flag is cleared once for all platforms in the final cleanup at the
        # end of _apply_env_overrides.
        enabled_was_explicit = bool(platform_config.extra.get("_enabled_explicit", False))
        if not platform_config.enabled and not enabled_was_explicit:
            platform_config.enabled = True
        return platform_config
    
    # Telegram
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        telegram_config = _enable_from_env(Platform.TELEGRAM)
        telegram_config.token = telegram_token
    
    # Reply threading mode for Telegram (off/first/all)
    telegram_reply_mode = os.getenv("TELEGRAM_REPLY_TO_MODE", "").lower()
    if telegram_reply_mode in {"off", "first", "all"}:
        if Platform.TELEGRAM not in config.platforms:
            config.platforms[Platform.TELEGRAM] = PlatformConfig()
        config.platforms[Platform.TELEGRAM].reply_to_mode = telegram_reply_mode
    
    telegram_fallback_ips = os.getenv("TELEGRAM_FALLBACK_IPS", "")
    if telegram_fallback_ips:
        if Platform.TELEGRAM not in config.platforms:
            config.platforms[Platform.TELEGRAM] = PlatformConfig()
        config.platforms[Platform.TELEGRAM].extra["fallback_ips"] = [
            ip.strip() for ip in telegram_fallback_ips.split(",") if ip.strip()
        ]

    telegram_home = os.getenv("TELEGRAM_HOME_CHANNEL")
    if telegram_home and Platform.TELEGRAM in config.platforms:
        config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
            platform=Platform.TELEGRAM,
            chat_id=telegram_home,
            name=os.getenv("TELEGRAM_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("TELEGRAM_HOME_CHANNEL_THREAD_ID") or None,
        )
    
    # Discord
    discord_token = os.getenv("DISCORD_BOT_TOKEN")
    if discord_token:
        discord_config = _enable_from_env(Platform.DISCORD)
        discord_config.token = discord_token
    
    discord_home = os.getenv("DISCORD_HOME_CHANNEL")
    if discord_home and Platform.DISCORD in config.platforms:
        config.platforms[Platform.DISCORD].home_channel = HomeChannel(
            platform=Platform.DISCORD,
            chat_id=discord_home,
            name=os.getenv("DISCORD_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("DISCORD_HOME_CHANNEL_THREAD_ID") or None,
        )
    
    # Reply threading mode for Discord (off/first/all)
    discord_reply_mode = os.getenv("DISCORD_REPLY_TO_MODE", "").lower()
    if discord_reply_mode in {"off", "first", "all"}:
        if Platform.DISCORD not in config.platforms:
            config.platforms[Platform.DISCORD] = PlatformConfig()
        config.platforms[Platform.DISCORD].reply_to_mode = discord_reply_mode
    
    # WhatsApp (typically uses different auth mechanism)
    whatsapp_enabled = os.getenv("WHATSAPP_ENABLED", "").lower() in {"true", "1", "yes"}
    whatsapp_disabled_explicitly = os.getenv("WHATSAPP_ENABLED", "").lower() in {"false", "0", "no"}
    if Platform.WHATSAPP in config.platforms:
        # YAML config exists — respect explicit disable
        wa_cfg = config.platforms[Platform.WHATSAPP]
        if whatsapp_disabled_explicitly:
            wa_cfg.enabled = False
        elif whatsapp_enabled:
            wa_cfg.enabled = True
        # else: keep whatever the YAML set
    elif whatsapp_enabled:
        config.platforms[Platform.WHATSAPP] = PlatformConfig(enabled=True)
    whatsapp_home = os.getenv("WHATSAPP_HOME_CHANNEL")
    if whatsapp_home and Platform.WHATSAPP in config.platforms:
        config.platforms[Platform.WHATSAPP].home_channel = HomeChannel(
            platform=Platform.WHATSAPP,
            chat_id=whatsapp_home,
            name=os.getenv("WHATSAPP_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("WHATSAPP_HOME_CHANNEL_THREAD_ID") or None,
        )

    # WhatsApp Cloud API (official Business Platform via Meta).
    # Distinct from the Baileys bridge: pure HTTP graph.facebook.com calls
    # outbound, public webhook inbound. Both adapters can run in parallel
    # against different phone numbers.
    whatsapp_cloud_phone_id = os.getenv("WHATSAPP_CLOUD_PHONE_NUMBER_ID")
    whatsapp_cloud_token = os.getenv("WHATSAPP_CLOUD_ACCESS_TOKEN")
    if whatsapp_cloud_phone_id and whatsapp_cloud_token:
        if Platform.WHATSAPP_CLOUD not in config.platforms:
            config.platforms[Platform.WHATSAPP_CLOUD] = PlatformConfig()
        config.platforms[Platform.WHATSAPP_CLOUD].enabled = True
        config.platforms[Platform.WHATSAPP_CLOUD].extra.update({
            "phone_number_id": whatsapp_cloud_phone_id,
            "access_token": whatsapp_cloud_token,
        })
        # Optional: app_id / app_secret (signature verification)
        wa_cloud_app_id = os.getenv("WHATSAPP_CLOUD_APP_ID")
        if wa_cloud_app_id:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["app_id"] = wa_cloud_app_id
        wa_cloud_app_secret = os.getenv("WHATSAPP_CLOUD_APP_SECRET")
        if wa_cloud_app_secret:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["app_secret"] = wa_cloud_app_secret
        # Optional: WABA id (analytics, future use)
        wa_cloud_waba_id = os.getenv("WHATSAPP_CLOUD_WABA_ID")
        if wa_cloud_waba_id:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["waba_id"] = wa_cloud_waba_id
        # Webhook verify token — Meta hub.verify_token shared secret
        wa_cloud_verify_token = os.getenv("WHATSAPP_CLOUD_VERIFY_TOKEN")
        if wa_cloud_verify_token:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["verify_token"] = wa_cloud_verify_token
        # Webhook server bind config (defaults baked into the adapter)
        wa_cloud_host = os.getenv("WHATSAPP_CLOUD_WEBHOOK_HOST")
        if wa_cloud_host:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["webhook_host"] = wa_cloud_host
        wa_cloud_port = os.getenv("WHATSAPP_CLOUD_WEBHOOK_PORT")
        if wa_cloud_port:
            try:
                config.platforms[Platform.WHATSAPP_CLOUD].extra["webhook_port"] = int(wa_cloud_port)
            except ValueError:
                pass
        wa_cloud_path = os.getenv("WHATSAPP_CLOUD_WEBHOOK_PATH")
        if wa_cloud_path:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["webhook_path"] = wa_cloud_path
        # Graph API version override (rarely needed)
        wa_cloud_api_version = os.getenv("WHATSAPP_CLOUD_API_VERSION")
        if wa_cloud_api_version:
            config.platforms[Platform.WHATSAPP_CLOUD].extra["api_version"] = wa_cloud_api_version
    whatsapp_cloud_home = os.getenv("WHATSAPP_CLOUD_HOME_CHANNEL")
    if whatsapp_cloud_home and Platform.WHATSAPP_CLOUD in config.platforms:
        config.platforms[Platform.WHATSAPP_CLOUD].home_channel = HomeChannel(
            platform=Platform.WHATSAPP_CLOUD,
            chat_id=whatsapp_cloud_home,
            name=os.getenv("WHATSAPP_CLOUD_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("WHATSAPP_CLOUD_HOME_CHANNEL_THREAD_ID") or None,
        )

    # Slack
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if slack_token:
        if Platform.SLACK not in config.platforms:
            # No yaml config for Slack — env-only setup, enable it
            config.platforms[Platform.SLACK] = PlatformConfig()
            config.platforms[Platform.SLACK].enabled = True
        else:
            slack_config = config.platforms[Platform.SLACK]
            # Read (don't pop) the explicit-enable marker: the registry-driven
            # plugin-enable pass below also needs it to avoid re-enabling a
            # platform the user explicitly disabled (Slack is now a plugin
            # entry — #41112). The flag is cleared once for all platforms in
            # the final cleanup at the end of _apply_env_overrides.
            enabled_was_explicit = bool(slack_config.extra.get("_enabled_explicit", False))
            if not slack_config.enabled and not enabled_was_explicit:
                # Top-level Slack settings such as channel prompts should not
                # turn an env-token setup into a disabled platform. Only an
                # explicit slack.enabled/platforms.slack.enabled false should.
                slack_config.enabled = True
        # If yaml config exists, respect its enabled flag (don't override
        # explicit enabled: false). Token is still stored so skills that
        # send Slack messages can use it without activating the gateway adapter.
        config.platforms[Platform.SLACK].token = slack_token
    slack_home = os.getenv("SLACK_HOME_CHANNEL")
    if slack_home and Platform.SLACK in config.platforms:
        config.platforms[Platform.SLACK].home_channel = HomeChannel(
            platform=Platform.SLACK,
            chat_id=slack_home,
            name=os.getenv("SLACK_HOME_CHANNEL_NAME", ""),
            thread_id=os.getenv("SLACK_HOME_CHANNEL_THREAD_ID") or None,
        )
    
    # Signal
    signal_url = os.getenv("SIGNAL_HTTP_URL")
    signal_account = os.getenv("SIGNAL_ACCOUNT")
    if signal_url and signal_account:
        signal_config = _enable_from_env(Platform.SIGNAL)
        signal_config.extra.update({
            "http_url": signal_url,
            "account": signal_account,
            "ignore_stories": os.getenv("SIGNAL_IGNORE_STORIES", "true").lower() in {"true", "1", "yes"},
        })
    signal_home = os.getenv("SIGNAL_HOME_CHANNEL")
    if signal_home and Platform.SIGNAL in config.platforms:
        config.platforms[Platform.SIGNAL].home_channel = HomeChannel(
            platform=Platform.SIGNAL,
            chat_id=signal_home,
            name=os.getenv("SIGNAL_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("SIGNAL_HOME_CHANNEL_THREAD_ID") or None,
        )

    # Mattermost
    mattermost_token = os.getenv("MATTERMOST_TOKEN")
    if mattermost_token:
        mattermost_url = os.getenv("MATTERMOST_URL", "")
        if not mattermost_url:
            logger.warning("MATTERMOST_TOKEN set but MATTERMOST_URL is missing")
        mattermost_config = _enable_from_env(Platform.MATTERMOST)
        mattermost_config.token = mattermost_token
        mattermost_config.extra["url"] = mattermost_url
    mattermost_home = os.getenv("MATTERMOST_HOME_CHANNEL")
    if mattermost_home and Platform.MATTERMOST in config.platforms:
        config.platforms[Platform.MATTERMOST].home_channel = HomeChannel(
            platform=Platform.MATTERMOST,
            chat_id=mattermost_home,
            name=os.getenv("MATTERMOST_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("MATTERMOST_HOME_CHANNEL_THREAD_ID") or None,
        )

    # Matrix
    matrix_token = os.getenv("MATRIX_ACCESS_TOKEN")
    matrix_homeserver = os.getenv("MATRIX_HOMESERVER", "")
    if matrix_token or os.getenv("MATRIX_PASSWORD"):
        if not matrix_homeserver:
            logger.warning("MATRIX_ACCESS_TOKEN/MATRIX_PASSWORD set but MATRIX_HOMESERVER is missing")
        matrix_config = _enable_from_env(Platform.MATRIX)
        if matrix_token:
            matrix_config.token = matrix_token
        matrix_config.extra["homeserver"] = matrix_homeserver
        matrix_user = os.getenv("MATRIX_USER_ID", "")
        if matrix_user:
            matrix_config.extra["user_id"] = matrix_user
        matrix_password = os.getenv("MATRIX_PASSWORD", "")
        if matrix_password:
            matrix_config.extra["password"] = matrix_password
        matrix_e2ee_mode = os.getenv("MATRIX_E2EE_MODE", "").strip().lower()
        matrix_e2ee = (
            matrix_e2ee_mode in ("required", "require", "optional", "prefer", "preferred")
            or os.getenv("MATRIX_ENCRYPTION", "").lower() in ("true", "1", "yes")
        )
        matrix_config.extra["encryption"] = matrix_e2ee
        if matrix_e2ee_mode:
            matrix_config.extra["e2ee_mode"] = matrix_e2ee_mode
        matrix_device_id = os.getenv("MATRIX_DEVICE_ID", "")
        if matrix_device_id:
            matrix_config.extra["device_id"] = matrix_device_id
    matrix_home = os.getenv("MATRIX_HOME_ROOM")
    if matrix_home and Platform.MATRIX in config.platforms:
        config.platforms[Platform.MATRIX].home_channel = HomeChannel(
            platform=Platform.MATRIX,
            chat_id=matrix_home,
            name=os.getenv("MATRIX_HOME_ROOM_NAME", "Home"),
            thread_id=os.getenv("MATRIX_HOME_ROOM_THREAD_ID") or None,
        )

    # Home Assistant
    hass_token = os.getenv("HASS_TOKEN")
    if hass_token:
        if Platform.HOMEASSISTANT not in config.platforms:
            config.platforms[Platform.HOMEASSISTANT] = PlatformConfig()
        config.platforms[Platform.HOMEASSISTANT].enabled = True
        config.platforms[Platform.HOMEASSISTANT].token = hass_token
        hass_url = os.getenv("HASS_URL")
        if hass_url:
            config.platforms[Platform.HOMEASSISTANT].extra["url"] = hass_url

    # Email
    email_addr = os.getenv("EMAIL_ADDRESS")
    email_pwd = os.getenv("EMAIL_PASSWORD")
    email_imap = os.getenv("EMAIL_IMAP_HOST")
    email_smtp = os.getenv("EMAIL_SMTP_HOST")
    if all([email_addr, email_pwd, email_imap, email_smtp]):
        if Platform.EMAIL not in config.platforms:
            config.platforms[Platform.EMAIL] = PlatformConfig()
        config.platforms[Platform.EMAIL].enabled = True
        config.platforms[Platform.EMAIL].extra.update({
            "address": email_addr,
            "imap_host": email_imap,
            "smtp_host": email_smtp,
        })
    email_home = os.getenv("EMAIL_HOME_ADDRESS")
    if email_home and Platform.EMAIL in config.platforms:
        config.platforms[Platform.EMAIL].home_channel = HomeChannel(
            platform=Platform.EMAIL,
            chat_id=email_home,
            name=os.getenv("EMAIL_HOME_ADDRESS_NAME", "Home"),
            thread_id=os.getenv("EMAIL_HOME_ADDRESS_THREAD_ID") or None,
        )

    # SMS (Twilio)
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    if twilio_sid:
        if Platform.SMS not in config.platforms:
            config.platforms[Platform.SMS] = PlatformConfig()
        config.platforms[Platform.SMS].enabled = True
        config.platforms[Platform.SMS].api_key = os.getenv("TWILIO_AUTH_TOKEN", "")
    sms_home = os.getenv("SMS_HOME_CHANNEL")
    if sms_home and Platform.SMS in config.platforms:
        config.platforms[Platform.SMS].home_channel = HomeChannel(
            platform=Platform.SMS,
            chat_id=sms_home,
            name=os.getenv("SMS_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("SMS_HOME_CHANNEL_THREAD_ID") or None,
        )

    # API Server
    api_server_enabled = os.getenv("API_SERVER_ENABLED", "").lower() in {"true", "1", "yes"}
    api_server_key = os.getenv("API_SERVER_KEY", "")
    api_server_cors_origins = os.getenv("API_SERVER_CORS_ORIGINS", "")
    api_server_port = os.getenv("API_SERVER_PORT")
    api_server_host = os.getenv("API_SERVER_HOST")
    if api_server_enabled or api_server_key:
        if Platform.API_SERVER not in config.platforms:
            config.platforms[Platform.API_SERVER] = PlatformConfig()
        config.platforms[Platform.API_SERVER].enabled = True
        if api_server_key:
            config.platforms[Platform.API_SERVER].extra["key"] = api_server_key
        if api_server_cors_origins:
            origins = [origin.strip() for origin in api_server_cors_origins.split(",") if origin.strip()]
            if origins:
                config.platforms[Platform.API_SERVER].extra["cors_origins"] = origins
        if api_server_port:
            try:
                config.platforms[Platform.API_SERVER].extra["port"] = int(api_server_port)
            except ValueError:
                pass
        if api_server_host:
            config.platforms[Platform.API_SERVER].extra["host"] = api_server_host
        api_server_model_name = os.getenv("API_SERVER_MODEL_NAME", "")
        if api_server_model_name:
            config.platforms[Platform.API_SERVER].extra["model_name"] = api_server_model_name

    # Webhook platform
    webhook_enabled = os.getenv("WEBHOOK_ENABLED", "").lower() in {"true", "1", "yes"}
    webhook_port = os.getenv("WEBHOOK_PORT")
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    if webhook_enabled:
        if Platform.WEBHOOK not in config.platforms:
            config.platforms[Platform.WEBHOOK] = PlatformConfig()
        config.platforms[Platform.WEBHOOK].enabled = True
        if webhook_port:
            try:
                config.platforms[Platform.WEBHOOK].extra["port"] = int(webhook_port)
            except ValueError:
                pass
        if webhook_secret:
            config.platforms[Platform.WEBHOOK].extra["secret"] = webhook_secret

    # Microsoft Graph webhook platform
    msgraph_webhook_enabled = os.getenv("MSGRAPH_WEBHOOK_ENABLED", "").lower() in {
        "true",
        "1",
        "yes",
    }
    msgraph_webhook_port = os.getenv("MSGRAPH_WEBHOOK_PORT")
    msgraph_webhook_client_state = os.getenv("MSGRAPH_WEBHOOK_CLIENT_STATE", "")
    msgraph_webhook_resources = os.getenv("MSGRAPH_WEBHOOK_ACCEPTED_RESOURCES", "")
    msgraph_webhook_allowed_cidrs = os.getenv(
        "MSGRAPH_WEBHOOK_ALLOWED_SOURCE_CIDRS", ""
    )
    if (
        msgraph_webhook_enabled
        or Platform.MSGRAPH_WEBHOOK in config.platforms
        or msgraph_webhook_port
        or msgraph_webhook_client_state
        or msgraph_webhook_resources
        or msgraph_webhook_allowed_cidrs
    ):
        if Platform.MSGRAPH_WEBHOOK not in config.platforms:
            config.platforms[Platform.MSGRAPH_WEBHOOK] = PlatformConfig()
        if msgraph_webhook_enabled:
            config.platforms[Platform.MSGRAPH_WEBHOOK].enabled = True
        if msgraph_webhook_port:
            try:
                config.platforms[Platform.MSGRAPH_WEBHOOK].extra["port"] = int(
                    msgraph_webhook_port
                )
            except ValueError:
                pass
        if msgraph_webhook_client_state:
            config.platforms[Platform.MSGRAPH_WEBHOOK].extra["client_state"] = (
                msgraph_webhook_client_state
            )
        if msgraph_webhook_resources:
            resources = [
                resource.strip()
                for resource in msgraph_webhook_resources.split(",")
                if resource.strip()
            ]
            if resources:
                config.platforms[Platform.MSGRAPH_WEBHOOK].extra[
                    "accepted_resources"
                ] = resources
        if msgraph_webhook_allowed_cidrs:
            cidrs = [
                cidr.strip()
                for cidr in msgraph_webhook_allowed_cidrs.split(",")
                if cidr.strip()
            ]
            if cidrs:
                config.platforms[Platform.MSGRAPH_WEBHOOK].extra[
                    "allowed_source_cidrs"
                ] = cidrs

    # DingTalk
    dingtalk_client_id = os.getenv("DINGTALK_CLIENT_ID")
    dingtalk_client_secret = os.getenv("DINGTALK_CLIENT_SECRET")
    if dingtalk_client_id and dingtalk_client_secret:
        if Platform.DINGTALK not in config.platforms:
            config.platforms[Platform.DINGTALK] = PlatformConfig()
        config.platforms[Platform.DINGTALK].enabled = True
        config.platforms[Platform.DINGTALK].extra.update({
            "client_id": dingtalk_client_id,
            "client_secret": dingtalk_client_secret,
        })
        dingtalk_home = os.getenv("DINGTALK_HOME_CHANNEL")
        if dingtalk_home:
            config.platforms[Platform.DINGTALK].home_channel = HomeChannel(
                platform=Platform.DINGTALK,
                chat_id=dingtalk_home,
                name=os.getenv("DINGTALK_HOME_CHANNEL_NAME", "Home"),
                thread_id=os.getenv("DINGTALK_HOME_CHANNEL_THREAD_ID") or None,
            )

    # Feishu / Lark
    feishu_app_id = os.getenv("FEISHU_APP_ID")
    feishu_app_secret = os.getenv("FEISHU_APP_SECRET")
    if feishu_app_id and feishu_app_secret:
        if Platform.FEISHU not in config.platforms:
            config.platforms[Platform.FEISHU] = PlatformConfig()
        config.platforms[Platform.FEISHU].enabled = True
        config.platforms[Platform.FEISHU].extra.update({
            "app_id": feishu_app_id,
            "app_secret": feishu_app_secret,
            "domain": os.getenv("FEISHU_DOMAIN", "feishu"),
            "connection_mode": os.getenv("FEISHU_CONNECTION_MODE", "websocket"),
        })
        feishu_encrypt_key = os.getenv("FEISHU_ENCRYPT_KEY", "")
        if feishu_encrypt_key:
            config.platforms[Platform.FEISHU].extra["encrypt_key"] = feishu_encrypt_key
        feishu_verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
        if feishu_verification_token:
            config.platforms[Platform.FEISHU].extra["verification_token"] = feishu_verification_token
        feishu_home = os.getenv("FEISHU_HOME_CHANNEL")
        if feishu_home:
            config.platforms[Platform.FEISHU].home_channel = HomeChannel(
                platform=Platform.FEISHU,
                chat_id=feishu_home,
                name=os.getenv("FEISHU_HOME_CHANNEL_NAME", "Home"),
                thread_id=os.getenv("FEISHU_HOME_CHANNEL_THREAD_ID") or None,
            )

    # WeCom (Enterprise WeChat)
    wecom_bot_id = os.getenv("WECOM_BOT_ID")
    wecom_secret = os.getenv("WECOM_SECRET")
    if wecom_bot_id and wecom_secret:
        if Platform.WECOM not in config.platforms:
            config.platforms[Platform.WECOM] = PlatformConfig()
        config.platforms[Platform.WECOM].enabled = True
        config.platforms[Platform.WECOM].extra.update({
            "bot_id": wecom_bot_id,
            "secret": wecom_secret,
        })
        wecom_ws_url = os.getenv("WECOM_WEBSOCKET_URL", "")
        if wecom_ws_url:
            config.platforms[Platform.WECOM].extra["websocket_url"] = wecom_ws_url
        wecom_home = os.getenv("WECOM_HOME_CHANNEL")
        if wecom_home:
            config.platforms[Platform.WECOM].home_channel = HomeChannel(
                platform=Platform.WECOM,
                chat_id=wecom_home,
                name=os.getenv("WECOM_HOME_CHANNEL_NAME", "Home"),
                thread_id=os.getenv("WECOM_HOME_CHANNEL_THREAD_ID") or None,
            )

    # WeCom callback mode (self-built apps)
    wecom_callback_corp_id = os.getenv("WECOM_CALLBACK_CORP_ID")
    wecom_callback_corp_secret = os.getenv("WECOM_CALLBACK_CORP_SECRET")
    if wecom_callback_corp_id and wecom_callback_corp_secret:
        if Platform.WECOM_CALLBACK not in config.platforms:
            config.platforms[Platform.WECOM_CALLBACK] = PlatformConfig()
        config.platforms[Platform.WECOM_CALLBACK].enabled = True
        config.platforms[Platform.WECOM_CALLBACK].extra.update({
            "corp_id": wecom_callback_corp_id,
            "corp_secret": wecom_callback_corp_secret,
            "agent_id": os.getenv("WECOM_CALLBACK_AGENT_ID", ""),
            "token": os.getenv("WECOM_CALLBACK_TOKEN", ""),
            "encoding_aes_key": os.getenv("WECOM_CALLBACK_ENCODING_AES_KEY", ""),
            "host": os.getenv("WECOM_CALLBACK_HOST", "0.0.0.0"),
            "port": env_int("WECOM_CALLBACK_PORT", 8645),
        })

    # Weixin (personal WeChat via iLink Bot API)
    weixin_token = os.getenv("WEIXIN_TOKEN")
    weixin_account_id = os.getenv("WEIXIN_ACCOUNT_ID")
    if weixin_token or weixin_account_id:
        if Platform.WEIXIN not in config.platforms:
            config.platforms[Platform.WEIXIN] = PlatformConfig()
        config.platforms[Platform.WEIXIN].enabled = True
        if weixin_token:
            config.platforms[Platform.WEIXIN].token = weixin_token
        extra = config.platforms[Platform.WEIXIN].extra
        if weixin_account_id:
            extra["account_id"] = weixin_account_id
        weixin_base_url = os.getenv("WEIXIN_BASE_URL", "").strip()
        if weixin_base_url:
            extra["base_url"] = weixin_base_url.rstrip("/")
        weixin_cdn_base_url = os.getenv("WEIXIN_CDN_BASE_URL", "").strip()
        if weixin_cdn_base_url:
            extra["cdn_base_url"] = weixin_cdn_base_url.rstrip("/")
        weixin_dm_policy = os.getenv("WEIXIN_DM_POLICY", "").strip().lower()
        if weixin_dm_policy:
            extra["dm_policy"] = weixin_dm_policy
        weixin_group_policy = os.getenv("WEIXIN_GROUP_POLICY", "").strip().lower()
        if weixin_group_policy:
            extra["group_policy"] = weixin_group_policy
        weixin_allowed_users = os.getenv("WEIXIN_ALLOWED_USERS", "").strip()
        if weixin_allowed_users:
            extra["allow_from"] = weixin_allowed_users
        weixin_group_allowed_users = os.getenv("WEIXIN_GROUP_ALLOWED_USERS", "").strip()
        if weixin_group_allowed_users:
            extra["group_allow_from"] = weixin_group_allowed_users
        weixin_split_multiline = os.getenv("WEIXIN_SPLIT_MULTILINE_MESSAGES", "").strip()
        if weixin_split_multiline:
            extra["split_multiline_messages"] = weixin_split_multiline
        weixin_home = os.getenv("WEIXIN_HOME_CHANNEL", "").strip()
        if weixin_home:
            config.platforms[Platform.WEIXIN].home_channel = HomeChannel(
                platform=Platform.WEIXIN,
                chat_id=weixin_home,
                name=os.getenv("WEIXIN_HOME_CHANNEL_NAME", "Home"),
                thread_id=os.getenv("WEIXIN_HOME_CHANNEL_THREAD_ID") or None,
            )

    # BlueBubbles (iMessage)
    bluebubbles_server_url = os.getenv("BLUEBUBBLES_SERVER_URL")
    bluebubbles_password = os.getenv("BLUEBUBBLES_PASSWORD")
    if bluebubbles_server_url and bluebubbles_password:
        if Platform.BLUEBUBBLES not in config.platforms:
            config.platforms[Platform.BLUEBUBBLES] = PlatformConfig()
        config.platforms[Platform.BLUEBUBBLES].enabled = True
        config.platforms[Platform.BLUEBUBBLES].extra.update({
            "server_url": bluebubbles_server_url.rstrip("/"),
            "password": bluebubbles_password,
            "webhook_host": os.getenv("BLUEBUBBLES_WEBHOOK_HOST", "127.0.0.1"),
            "webhook_port": env_int("BLUEBUBBLES_WEBHOOK_PORT", 8645),
            "webhook_path": os.getenv("BLUEBUBBLES_WEBHOOK_PATH", "/bluebubbles-webhook"),
            "send_read_receipts": os.getenv("BLUEBUBBLES_SEND_READ_RECEIPTS", "true").lower() in {"true", "1", "yes"},
        })
        bluebubbles_require_mention = os.getenv("BLUEBUBBLES_REQUIRE_MENTION")
        if bluebubbles_require_mention is not None:
            config.platforms[Platform.BLUEBUBBLES].extra["require_mention"] = (
                bluebubbles_require_mention.lower() in {"true", "1", "yes", "on"}
            )
        bluebubbles_mention_patterns = os.getenv("BLUEBUBBLES_MENTION_PATTERNS")
        if bluebubbles_mention_patterns:
            try:
                parsed_patterns = json.loads(bluebubbles_mention_patterns)
            except Exception:
                parsed_patterns = [
                    part.strip()
                    for part in bluebubbles_mention_patterns.replace("\n", ",").split(",")
                    if part.strip()
                ]
            config.platforms[Platform.BLUEBUBBLES].extra["mention_patterns"] = parsed_patterns
    bluebubbles_home = os.getenv("BLUEBUBBLES_HOME_CHANNEL")
    if bluebubbles_home and Platform.BLUEBUBBLES in config.platforms:
        config.platforms[Platform.BLUEBUBBLES].home_channel = HomeChannel(
            platform=Platform.BLUEBUBBLES,
            chat_id=bluebubbles_home,
            name=os.getenv("BLUEBUBBLES_HOME_CHANNEL_NAME", "Home"),
            thread_id=os.getenv("BLUEBUBBLES_HOME_CHANNEL_THREAD_ID") or None,
        )

    # QQ (Official Bot API v2)
    qq_app_id = os.getenv("QQ_APP_ID")
    qq_client_secret = os.getenv("QQ_CLIENT_SECRET")
    if qq_app_id or qq_client_secret:
        if Platform.QQBOT not in config.platforms:
            config.platforms[Platform.QQBOT] = PlatformConfig()
        config.platforms[Platform.QQBOT].enabled = True
        extra = config.platforms[Platform.QQBOT].extra
        if qq_app_id:
            extra["app_id"] = qq_app_id
        if qq_client_secret:
            extra["client_secret"] = qq_client_secret
        qq_allowed_users = os.getenv("QQ_ALLOWED_USERS", "").strip()
        if qq_allowed_users:
            extra["allow_from"] = qq_allowed_users
        qq_group_allowed = os.getenv("QQ_GROUP_ALLOWED_USERS", "").strip()
        if qq_group_allowed:
            extra["group_allow_from"] = qq_group_allowed
        qq_home = os.getenv("QQBOT_HOME_CHANNEL", "").strip()
        qq_home_name_env = "QQBOT_HOME_CHANNEL_NAME"
        if not qq_home:
            # Back-compat: accept the pre-rename name and log a one-time warning.
            legacy_home = os.getenv("QQ_HOME_CHANNEL", "").strip()
            if legacy_home:
                qq_home = legacy_home
                qq_home_name_env = "QQ_HOME_CHANNEL_NAME"
                logging.getLogger(__name__).warning(
                    "QQ_HOME_CHANNEL is deprecated; rename to QQBOT_HOME_CHANNEL "
                    "in your .env for consistency with the platform key."
                )
        if qq_home:
            config.platforms[Platform.QQBOT].home_channel = HomeChannel(
                platform=Platform.QQBOT,
                chat_id=qq_home,
                name=os.getenv("QQBOT_HOME_CHANNEL_NAME") or os.getenv(qq_home_name_env, "Home"),
                thread_id=(
                    os.getenv("QQBOT_HOME_CHANNEL_THREAD_ID")
                    or os.getenv("QQ_HOME_CHANNEL_THREAD_ID")
                    or None
                ),
            )

    # Yuanbao — YUANBAO_APP_ID preferred
    yuanbao_app_id = os.getenv("YUANBAO_APP_ID") or os.getenv("YUANBAO_APP_KEY")
    yuanbao_app_secret = os.getenv("YUANBAO_APP_SECRET")
    if yuanbao_app_id and yuanbao_app_secret:
        if Platform.YUANBAO not in config.platforms:
            config.platforms[Platform.YUANBAO] = PlatformConfig()
        config.platforms[Platform.YUANBAO].enabled = True
        extra = config.platforms[Platform.YUANBAO].extra
        extra["app_id"] = yuanbao_app_id
        extra["app_secret"] = yuanbao_app_secret
        yuanbao_bot_id = os.getenv("YUANBAO_BOT_ID")
        if yuanbao_bot_id:
            extra["bot_id"] = yuanbao_bot_id
        yuanbao_ws_url = os.getenv("YUANBAO_WS_URL")
        if yuanbao_ws_url:
            extra["ws_url"] = yuanbao_ws_url
        yuanbao_api_domain = os.getenv("YUANBAO_API_DOMAIN")
        if yuanbao_api_domain:
            extra["api_domain"] = yuanbao_api_domain
        yuanbao_route_env = os.getenv("YUANBAO_ROUTE_ENV")
        if yuanbao_route_env:
            extra["route_env"] = yuanbao_route_env
        yuanbao_home = os.getenv("YUANBAO_HOME_CHANNEL")
        if yuanbao_home:
            config.platforms[Platform.YUANBAO].home_channel = HomeChannel(
                platform=Platform.YUANBAO,
                chat_id=yuanbao_home,
                name=os.getenv("YUANBAO_HOME_CHANNEL_NAME", "Home"),
                thread_id=os.getenv("YUANBAO_HOME_CHANNEL_THREAD_ID") or None,
            )
        yuanbao_dm_policy = os.getenv("YUANBAO_DM_POLICY")
        if yuanbao_dm_policy:
            extra["dm_policy"] = yuanbao_dm_policy.strip().lower()
        yuanbao_dm_allow_from = os.getenv("YUANBAO_DM_ALLOW_FROM")
        if yuanbao_dm_allow_from:
            extra["dm_allow_from"] = yuanbao_dm_allow_from
        yuanbao_group_policy = os.getenv("YUANBAO_GROUP_POLICY")
        if yuanbao_group_policy:
            extra["group_policy"] = yuanbao_group_policy.strip().lower()
        yuanbao_group_allow_from = os.getenv("YUANBAO_GROUP_ALLOW_FROM")
        if yuanbao_group_allow_from:
            extra["group_allow_from"] = yuanbao_group_allow_from

    # Session settings
    idle_minutes = os.getenv("SESSION_IDLE_MINUTES")
    if idle_minutes:
        try:
            config.default_reset_policy.idle_minutes = int(idle_minutes)
        except ValueError:
            pass
    
    reset_hour = os.getenv("SESSION_RESET_HOUR")
    if reset_hour:
        try:
            config.default_reset_policy.at_hour = int(reset_hour)
        except ValueError:
            pass

    # Registry-driven enable for plugin platforms.  Built-ins have explicit
    # blocks above; plugins expose check_fn() which is the single source of
    # truth for "are my env vars set?".  When it returns True, ensure the
    # platform is enabled so start() will create its adapter.  Plugins that
    # need to seed ``PlatformConfig.extra`` from env vars (e.g. Google Chat's
    # project_id / subscription_name) can supply ``env_enablement_fn`` on
    # their PlatformEntry — called here BEFORE adapter construction.
    #
    # Enablement gate (#31116): when a plugin registers ``is_connected``
    # (the "has the user actually configured credentials for this?" check),
    # we MUST consult it before flipping ``enabled = True``.  Otherwise
    # ``check_fn`` alone — which for adapter plugins typically just
    # verifies the SDK is importable / lazy-installs it — silently enables
    # platforms the user never opted into, and the gateway then tries to
    # connect to Discord / Teams / Google Chat with no token and emits
    # noisy retry-forever errors.  ``_platform_status`` was already fixed
    # for the same bug class in commit 7849a3d73; this is the runtime
    # counterpart.
    try:
        from openagents_cli.plugins import discover_plugins
        discover_plugins()  # idempotent
        from gateway.platform_registry import platform_registry
        for entry in platform_registry.plugin_entries():
            try:
                platform = Platform(entry.name)
            except Exception as e:
                logger.debug("unknown platform name %r: %s", entry.name, e)
                continue
            existing_cfg = config.platforms.get(platform)
            # Respect an explicit ``enabled: false`` (YAML / gateway.json /
            # dashboard PUT).  ``_enabled_explicit`` is set in
            # load_gateway_config() (via _merge_platform_map / the shared-key
            # loop) when the user wrote ``enabled`` for this platform; if they
            # explicitly disabled it, never re-enable here just because
            # check_fn() / is_connected() pass (e.g. a token is present but the
            # user set telegram.enabled: false). #41112.
            if (
                existing_cfg is not None
                and not existing_cfg.enabled
                and bool((existing_cfg.extra or {}).get("_enabled_explicit", False))
            ):
                continue
            # Seed candidate extras from ``env_enablement_fn`` so plugins
            # whose ``is_connected`` reads ``config.extra`` (e.g. Google
            # Chat's ``_is_connected`` checks ``config.extra["project_id"]``)
            # see the same state they will after enablement. Without this,
            # Google-Chat-on-env-vars-only setups silently fail the gate
            # below even though the user is configured.  Plugins whose
            # ``is_connected`` reads env vars directly (Discord, IRC,
            # Teams, LINE, ntfy, Simplex) are unaffected; this only
            # restores Google Chat.
            seed_for_probe = None
            if entry.env_enablement_fn is not None:
                try:
                    seed_for_probe = entry.env_enablement_fn()
                except Exception as e:
                    logger.debug(
                        "env_enablement_fn for %s raised: %s", entry.name, e
                    )
                    seed_for_probe = None

            # Only consult is_connected for platforms that are NOT already
            # explicitly configured in YAML / env (existing_cfg with
            # enabled=True means the user wrote it themselves or another
            # env-var bridge enabled it — keep that decision).
            if existing_cfg is None or not existing_cfg.enabled:
                if entry.is_connected is not None:
                    try:
                        # Probe with ``enabled=True`` since we're asking
                        # "would this plugin BE configured if we enabled
                        # it?" not "is it currently enabled?". Google
                        # Chat's ``_is_connected`` short-circuits on
                        # ``config.enabled`` being False, which on the
                        # default ``PlatformConfig()`` would fail the
                        # gate even with proper env vars set.
                        if existing_cfg is not None:
                            probe_cfg = existing_cfg
                            if not probe_cfg.enabled:
                                probe_cfg = PlatformConfig(
                                    enabled=True,
                                    extra=dict(probe_cfg.extra or {}),
                                )
                        else:
                            probe_cfg = PlatformConfig(enabled=True)
                        if isinstance(seed_for_probe, dict) and seed_for_probe:
                            # Don't mutate ``existing_cfg``; the probe gets
                            # a transient view with env-seeded extras layered
                            # on top of whatever's already there.
                            probe_extra = dict(getattr(probe_cfg, "extra", {}) or {})
                            for k, v in seed_for_probe.items():
                                if k == "home_channel":
                                    continue
                                probe_extra.setdefault(k, v)
                            probe_cfg = PlatformConfig(
                                enabled=True,
                                extra=probe_extra,
                            )
                        configured = bool(entry.is_connected(probe_cfg))
                    except Exception as exc:
                        logger.debug(
                            "is_connected for %s raised: %s — skipping enablement",
                            entry.name, exc,
                        )
                        configured = False
                    if not configured:
                        logger.debug(
                            "Plugin platform '%s' available but not configured "
                            "(is_connected returned False) — skipping enable",
                            entry.name,
                        )
                        continue
            # Verify dependencies LAST — only for platforms that are already
            # enabled or passed the credential gate above.  For adapter plugins
            # ``check_fn`` lazy-INSTALLS the platform SDK (pip) as a side
            # effect, so running it as an unconditional sweep over every
            # registered platform made ``load_gateway_config()`` pip-install
            # Discord/Telegram/Slack/Feishu/Dingtalk on every call — including
            # the desktop/dashboard readiness probe (``GET /api/status``, which
            # awaits this synchronously) — even when the user configured none
            # of them.  That blocked startup until every install finished and
            # caused the desktop app to time out and boot-loop (stuck at 94%).
            try:
                if not entry.check_fn():
                    continue
            except Exception as e:
                logger.debug("check_fn for %s raised: %s", entry.name, e)
                continue
            if platform not in config.platforms:
                config.platforms[platform] = PlatformConfig()
            config.platforms[platform].enabled = True
            # Commit env-seeded extras onto the now-enabled platform.
            # We've already called ``env_enablement_fn`` above (for the
            # probe); reuse that result instead of calling it twice.
            if isinstance(seed_for_probe, dict) and seed_for_probe:
                seed = dict(seed_for_probe)
                # Extract the home_channel dict (if provided) so we wire it
                # up as a proper HomeChannel dataclass.  Everything else is
                # merged into ``extra``.
                home = seed.pop("home_channel", None)
                config.platforms[platform].extra.update(seed)
                if isinstance(home, dict) and home.get("chat_id"):
                    config.platforms[platform].home_channel = HomeChannel(
                        platform=platform,
                        chat_id=str(home["chat_id"]),
                        name=str(home.get("name") or "Home"),
                        thread_id=(
                            str(home["thread_id"])
                            if home.get("thread_id")
                            else None
                        ),
                    )
    except Exception as e:
        logger.debug("Plugin platform enable pass failed: %s", e)

    # Relay (generic connector-fronted platform, EXPERIMENTAL). Enabled when a
    # connector relay URL is configured via GATEWAY_RELAY_URL (env) or
    # gateway.relay_url (config.yaml). The adapter is registered into the
    # platform_registry at gateway startup (gateway.relay.register_relay_adapter)
    # and dials OUT to the connector — so, like Telegram/Matrix, it has no public
    # inbound port and just needs Platform.RELAY present+enabled in
    # config.platforms for start_gateway()'s connect loop to bring it up. The
    # connected-checker (Platform.RELAY in _PLATFORM_CONNECTED_CHECKERS) keys on
    # extra["relay_url"], so mirror the URL into extra here.
    relay_url_env = os.getenv("GATEWAY_RELAY_URL", "").strip()
    relay_url_yaml = ""
    existing_relay = config.platforms.get(Platform.RELAY)
    if existing_relay is not None:
        relay_url_yaml = str(existing_relay.extra.get("relay_url") or "").strip()
    relay_url_val = relay_url_env or relay_url_yaml
    if relay_url_val:
        relay_config = _enable_from_env(Platform.RELAY)
        relay_config.extra["relay_url"] = relay_url_val.rstrip("/")

    for platform_config in config.platforms.values():
        platform_config.extra.pop("_enabled_explicit", None)
=======
# ---- BEGIN PLUGIN-COMPAT (revert-scheduled; see COMPAT_MANIFEST.md) ----
# Names external plugins imported from this module before the Sep 2026 decomposition.
# Internal code MUST NOT use these (scripts/check_compat_pointers.py fails CI if it does).
# The whole block is removed by reverting the commit that added it.
import json  # noqa: F401,E402
# ---- END PLUGIN-COMPAT ----
>>>>>>> rb/tag
