"""Built-in Python command security scanner (OpenAgents default).

Provides tirith-like content checks without an external binary: pipe-to-
interpreter chains, homograph URLs, terminal injection, and encoded payloads.
Returns the same shape as ``tools.tirith_security.check_command_security``.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Tuple

# action ∈ {"warn", "block"} — both flow through the approval UI.
_PATTERNS: List[Tuple[str, str, str, str, str]] = [
    (
        r"\b(curl|wget|fetch)\s[^\n|&;]*\|\s*(?:[/\w.-]*/)?(?:ba)?sh\b",
        "warn",
        "pipe_to_shell",
        "Pipe remote content to shell",
        "Downloading and piping directly into a shell can execute untrusted code.",
    ),
    (
        r"\b(curl|wget|fetch)\s[^\n|&;]*\|\s*(?:[/\w.-]*/)?(?:python[23]?|node|ruby|perl)\b",
        "warn",
        "pipe_to_interpreter",
        "Pipe remote content to interpreter",
        "Remote content piped into an interpreter may run arbitrary code.",
    ),
    (
        r"\beval\s*\$\(\s*(curl|wget|fetch)\b",
        "warn",
        "eval_curl_substitution",
        "Eval with remote fetch",
        "Evaluating command substitution from curl/wget can execute remote payloads.",
    ),
    (
        r"\bsource\s+<\(\s*(curl|wget|fetch)\b",
        "warn",
        "source_process_substitution",
        "Source from remote process substitution",
        "Sourcing output from a remote download can execute untrusted code.",
    ),
    (
        r"(?:base64|openssl)\s+[^\n|&;]*\|\s*(?:[/\w.-]*/)?(?:ba)?sh\b",
        "warn",
        "encoded_pipe_to_shell",
        "Encoded payload piped to shell",
        "Decoding or decrypting into a shell often hides malicious commands.",
    ),
    (
        r"https?://[^\s\"'\\]+[^\x00-\x7F]",
        "warn",
        "homograph_url",
        "Internationalized URL (homograph risk)",
        "Non-ASCII characters in URLs can disguise lookalike domains.",
    ),
    (
        r"(?:echo|printf)\s+[^\n]*(?:\\033|\\x1[bB]|\\e\[|\x1b\[)",
        "warn",
        "terminal_injection",
        "Terminal escape injection",
        "Escape sequences in terminal output can trick users or hide malicious text.",
    ),
    (
        r"/dev/tcp/\d{1,3}(?:\.\d{1,3}){3}/\d+",
        "block",
        "bash_dev_tcp",
        "Bash /dev/tcp connection",
        "Direct /dev/tcp connections are commonly used for reverse shells.",
    ),
    (
        r"\bnc\s+(-[^\s]+\s+)*-e\s+/(?:ba)?sh\b",
        "block",
        "netcat_reverse_shell",
        "Netcat reverse shell",
        "Netcat executing a shell is a common reverse-shell pattern.",
    ),
    (
        r"\b(powershell|pwsh)\b[^\n]*\b-(?:enc|encodedcommand)\b",
        "warn",
        "powershell_encoded",
        "Encoded PowerShell command",
        "Encoded PowerShell hides the actual command being executed.",
    ),
]

_COMPILED = [
    (re.compile(pattern, re.IGNORECASE), action, rule_id, title, description)
    for pattern, action, rule_id, title, description in _PATTERNS
]

_ACTION_RANK = {"allow": 0, "warn": 1, "block": 2}


def is_enabled() -> bool:
    """Return whether the built-in scanner is active."""
    env = os.getenv("OPENAGENTS_BUILTIN_SCANNER")
    if env is not None and env.lower() in {"0", "false", "no", "off"}:
        return False
    try:
        from openagents_cli.config import load_config

        sec = (load_config() or {}).get("security", {}) or {}
        return bool(sec.get("builtin_command_scanner", True))
    except Exception:
        return True


def check_command_security(command: str) -> Dict[str, Any]:
    """Scan *command* and return tirith-compatible result dict."""
    if not is_enabled() or not command:
        return {"action": "allow", "findings": [], "summary": ""}

    findings: List[Dict[str, str]] = []
    worst = "allow"

    for pattern, action, rule_id, title, description in _COMPILED:
        if pattern.search(command):
            findings.append(
                {
                    "rule_id": rule_id,
                    "severity": "HIGH" if action == "block" else "MEDIUM",
                    "title": title,
                    "description": description,
                }
            )
            if _ACTION_RANK.get(action, 0) > _ACTION_RANK.get(worst, 0):
                worst = action

    if not findings:
        return {"action": "allow", "findings": [], "summary": ""}

    summary = findings[0]["title"]
    if len(findings) > 1:
        summary = f"{summary} (+{len(findings) - 1} more)"
    return {"action": worst, "findings": findings, "summary": summary}


def merge_scan_results(*results: Dict[str, Any]) -> Dict[str, Any]:
    """Merge multiple scan results, keeping the strictest action and all findings."""
    merged_findings: List[Dict[str, str]] = []
    worst = "allow"
    summaries: List[str] = []

    for result in results:
        if not result:
            continue
        action = str(result.get("action") or "allow")
        if _ACTION_RANK.get(action, 0) > _ACTION_RANK.get(worst, 0):
            worst = action
        merged_findings.extend(result.get("findings") or [])
        summary = (result.get("summary") or "").strip()
        if summary:
            summaries.append(summary)

    if not merged_findings:
        return {"action": "allow", "findings": [], "summary": ""}

    summary = summaries[0] if summaries else merged_findings[0].get("title", "security issue")
    if len(merged_findings) > 1 and not summary.endswith("more)"):
        summary = f"{summary} (+{len(merged_findings) - 1} findings)"
    return {"action": worst, "findings": merged_findings, "summary": summary}
