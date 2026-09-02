"""Tests for startup animation and session-on-launch settings."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest


class TestStartupAnimation:
    def test_skips_when_disabled(self):
        from openagents_cli.startup_animation import play_startup_animation

        with patch("openagents_cli.startup_animation.time.sleep") as sleep:
            play_startup_animation(enabled=False)
        sleep.assert_not_called()

    def test_skips_when_env_flag_set(self, monkeypatch):
        from openagents_cli.startup_animation import play_startup_animation

        monkeypatch.setenv("HERMES_NO_STARTUP_ANIMATION", "1")
        with patch("openagents_cli.startup_animation.time.sleep") as sleep:
            play_startup_animation(enabled=True)
        sleep.assert_not_called()

    def test_runs_on_tty_when_enabled(self, monkeypatch):
        from openagents_cli.startup_animation import play_startup_animation

        monkeypatch.delenv("HERMES_NO_STARTUP_ANIMATION", raising=False)
        monkeypatch.delenv("HERMES_FAST_STARTUP_BANNER", raising=False)
        with (
            patch(
                "openagents_cli.startup_animation._is_interactive_tty",
                return_value=True,
            ),
            patch("openagents_cli.startup_animation.time.sleep") as sleep,
            patch("openagents_cli.startup_animation.Live", create=True),
        ):
            try:
                from rich.live import Live  # noqa: F401

                with patch("openagents_cli.startup_animation.Live") as live_cls:
                    live_cls.return_value.__enter__.return_value = SimpleNamespace(
                        update=lambda *_: None
                    )
                    play_startup_animation(enabled=True)
            except Exception:
                play_startup_animation(enabled=True)
        assert sleep.call_count >= 1


class TestSessionOnLaunch:
    def test_normalize_invalid_mode(self):
        from openagents_cli.session_launch import normalize_session_on_launch

        assert normalize_session_on_launch("bogus") == "new"
        assert normalize_session_on_launch("last") == "last"
        assert normalize_session_on_launch("prompt") == "prompt"

    def test_new_mode_is_noop(self):
        from openagents_cli.session_launch import apply_session_on_launch

        args = SimpleNamespace(resume=None, continue_last=None, query=None, quiet=False)
        with patch("openagents_cli.session_launch.load_config", create=True):
            with patch(
                "openagents_cli.config.load_config",
                return_value={"display": {"session_on_launch": "new"}},
            ):
                apply_session_on_launch(args, use_tui=False)
        assert args.resume is None

    def test_last_mode_sets_resume(self, monkeypatch):
        from openagents_cli.session_launch import apply_session_on_launch

        args = SimpleNamespace(resume=None, continue_last=None, query=None, quiet=False)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr(
            "openagents_cli.main._resolve_last_session",
            lambda source="cli": "sess-123",
        )
        with patch(
            "openagents_cli.config.load_config",
            return_value={"display": {"session_on_launch": "last"}},
        ):
            apply_session_on_launch(args, use_tui=False)
        assert args.resume == "sess-123"

    def test_explicit_resume_not_overridden(self, monkeypatch):
        from openagents_cli.session_launch import apply_session_on_launch

        args = SimpleNamespace(
            resume="keep-me", continue_last=None, query=None, quiet=False
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        with patch(
            "openagents_cli.config.load_config",
            return_value={"display": {"session_on_launch": "last"}},
        ):
            apply_session_on_launch(args, use_tui=False)
        assert args.resume == "keep-me"

    def test_prompt_mode_sets_selected_session(self, monkeypatch):
        from openagents_cli.session_launch import apply_session_on_launch

        args = SimpleNamespace(resume=None, continue_last=None, query=None, quiet=False)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        sessions = [{"id": "abc", "title": "demo"}]
        monkeypatch.setattr(
            "openagents_cli.session_launch._list_launch_sessions",
            lambda source, limit: sessions,
        )
        monkeypatch.setattr(
            "openagents_cli.main._session_browse_picker",
            lambda items: "abc",
        )
        with patch(
            "openagents_cli.config.load_config",
            return_value={"display": {"session_on_launch": "prompt"}},
        ):
            apply_session_on_launch(args, use_tui=False)
        assert args.resume == "abc"
