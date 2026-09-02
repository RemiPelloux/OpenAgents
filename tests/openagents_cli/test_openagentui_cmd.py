"""Tests for the /OpenAgentUI shared command (start/stop/status)."""

from __future__ import annotations

from openagents_cli import openagentui_cmd as cmd


class _FakeProc:
    def __init__(self, pid=4242, exit_code=None):
        self.pid = pid
        self._exit_code = exit_code
        self.returncode = exit_code

    def poll(self):
        return self._exit_code


def _make_app_dir(tmp_path, with_node_modules=True):
    app_dir = tmp_path / "apps" / "openagentui"
    app_dir.mkdir(parents=True)
    if with_node_modules:
        (app_dir / "node_modules").mkdir()
    return app_dir


def test_status_when_never_started():
    result = cmd.status_openagentui()
    assert result.text == "OpenAgentUI: stopped"


def test_start_fails_when_frontend_not_vendored(monkeypatch, tmp_path):
    monkeypatch.setattr(cmd, "_app_dir", lambda: tmp_path / "missing_app")
    result = cmd.start_openagentui(open_browser=False)
    assert "not found" in result.text


def test_start_fails_without_npm(monkeypatch, tmp_path):
    monkeypatch.setattr(cmd, "_app_dir", lambda: _make_app_dir(tmp_path))
    monkeypatch.setattr(cmd, "_npm_command", lambda: None)
    result = cmd.start_openagentui(open_browser=False)
    assert "npm not found" in result.text


def test_start_fails_without_node_modules(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cmd, "_app_dir", lambda: _make_app_dir(tmp_path, with_node_modules=False)
    )
    monkeypatch.setattr(cmd, "_npm_command", lambda: "/usr/bin/npm")
    result = cmd.start_openagentui(open_browser=False)
    assert "npm install" in result.text


def test_start_launches_subprocess_and_records_state(monkeypatch, tmp_path):
    app_dir = _make_app_dir(tmp_path)
    monkeypatch.setattr(cmd, "_app_dir", lambda: app_dir)
    monkeypatch.setattr(cmd, "_npm_command", lambda: "/usr/bin/npm")
    monkeypatch.setattr(cmd, "_ensure_dashboard_online", lambda: "Dashboard API online")
    monkeypatch.setattr(cmd.time, "sleep", lambda *_a: None)

    fake_proc = _FakeProc(pid=1234)
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return fake_proc

    monkeypatch.setattr(cmd.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        cmd.webbrowser, "open", lambda url: captured.setdefault("opened_url", url)
    )

    result = cmd.start_openagentui(port=5555, open_browser=True)

    assert "online" in result.text
    assert "1234" in result.text
    assert captured["opened_url"] == "http://127.0.0.1:5555"
    assert "-p" in captured["args"] and "5555" in captured["args"]

    state = cmd._read_state()
    assert state["pid"] == 1234
    assert state["port"] == 5555


def test_start_does_not_open_browser_by_default(monkeypatch, tmp_path):
    app_dir = _make_app_dir(tmp_path)
    monkeypatch.setattr(cmd, "_app_dir", lambda: app_dir)
    monkeypatch.setattr(cmd, "_npm_command", lambda: "/usr/bin/npm")
    monkeypatch.setattr(cmd, "_ensure_dashboard_online", lambda: "")
    monkeypatch.setattr(cmd.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(cmd.subprocess, "Popen", lambda *a, **k: _FakeProc(pid=1234))
    opened = {}
    monkeypatch.setattr(
        cmd.webbrowser, "open", lambda url: opened.setdefault("url", url)
    )

    cmd.handle_openagentui_command("true")
    assert "url" not in opened


def test_start_open_flag_opens_browser(monkeypatch, tmp_path):
    app_dir = _make_app_dir(tmp_path)
    monkeypatch.setattr(cmd, "_app_dir", lambda: app_dir)
    monkeypatch.setattr(cmd, "_npm_command", lambda: "/usr/bin/npm")
    monkeypatch.setattr(cmd, "_ensure_dashboard_online", lambda: "")
    monkeypatch.setattr(cmd.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(cmd.subprocess, "Popen", lambda *a, **k: _FakeProc(pid=1234))
    opened = {}
    monkeypatch.setattr(
        cmd.webbrowser, "open", lambda url: opened.setdefault("url", url)
    )

    cmd.handle_openagentui_command("true open")
    assert opened["url"] == "http://127.0.0.1:4173"


def test_start_reports_already_running(monkeypatch, tmp_path):
    app_dir = _make_app_dir(tmp_path)
    monkeypatch.setattr(cmd, "_app_dir", lambda: app_dir)
    monkeypatch.setattr(cmd, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(cmd, "_read_state", lambda: {"pid": 999, "port": 4173})
    monkeypatch.setattr(cmd, "_ensure_dashboard_online", lambda: "")
    opened = {}
    monkeypatch.setattr(
        cmd.webbrowser, "open", lambda url: opened.setdefault("url", url)
    )

    result = cmd.start_openagentui()
    assert "already running" in result.text
    assert "url" not in opened


def test_start_reports_immediate_exit(monkeypatch, tmp_path):
    app_dir = _make_app_dir(tmp_path)
    monkeypatch.setattr(cmd, "_app_dir", lambda: app_dir)
    monkeypatch.setattr(cmd, "_npm_command", lambda: "/usr/bin/npm")
    monkeypatch.setattr(cmd.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(
        cmd.subprocess, "Popen", lambda *a, **k: _FakeProc(pid=1, exit_code=1)
    )

    result = cmd.start_openagentui(open_browser=False)
    assert "exited immediately" in result.text


def test_stop_when_not_running():
    result = cmd.stop_openagentui()
    assert result.text == "OpenAgentUI is not running."


def test_stop_kills_live_process(monkeypatch):
    cmd._write_state({"pid": 777, "port": 4173})
    monkeypatch.setattr(cmd, "_pid_alive", lambda pid: True)
    killed = {}
    monkeypatch.setattr(cmd.os, "kill", lambda pid, sig: killed.setdefault("pid", pid))

    result = cmd.stop_openagentui()
    assert "stopped" in result.text
    assert killed["pid"] == 777
    assert cmd._read_state() is None


def test_stop_cleans_up_stale_state(monkeypatch):
    cmd._write_state({"pid": 888, "port": 4173})
    monkeypatch.setattr(cmd, "_pid_alive", lambda pid: False)

    result = cmd.stop_openagentui()
    assert result.text == "OpenAgentUI is not running."
    assert cmd._read_state() is None


def test_status_reports_running(monkeypatch):
    cmd._write_state({"pid": 555, "port": 4200, "mode": "start"})
    monkeypatch.setattr(cmd, "_pid_alive", lambda pid: True)

    result = cmd.status_openagentui()
    assert "running" in result.text
    assert "4200" in result.text
    assert "555" in result.text


def test_handle_command_dispatches_verbs(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cmd,
        "start_openagentui",
        lambda **kwargs: calls.append(("start", kwargs))
        or cmd.OpenAgentUiCommandResult("started"),
    )
    monkeypatch.setattr(
        cmd,
        "stop_openagentui",
        lambda: calls.append("stop") or cmd.OpenAgentUiCommandResult("stopped"),
    )
    monkeypatch.setattr(
        cmd,
        "status_openagentui",
        lambda: calls.append("status") or cmd.OpenAgentUiCommandResult("status"),
    )

    assert cmd.handle_openagentui_command("true").text == "started"
    assert cmd.handle_openagentui_command("true open").text == "started"
    assert cmd.handle_openagentui_command("false").text == "stopped"
    assert cmd.handle_openagentui_command("").text == "status"
    assert calls[0] == ("start", {"open_browser": False})
    assert calls[1] == ("start", {"open_browser": True})
    assert calls[2:] == ["stop", "status"]


def test_handle_command_unknown_argument():
    result = cmd.handle_openagentui_command("bogus")
    assert "Unknown argument" in result.text
