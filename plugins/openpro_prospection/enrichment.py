"""Email extraction for TikTok leads (OpenAgents side)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def extract_emails(text: str) -> List[str]:
    if not text:
        return []
    return list(dict.fromkeys(EMAIL_PATTERN.findall(text)))


def extract_email_from_lead(lead: Dict[str, Any]) -> Optional[str]:
    raw = lead.get("raw") if isinstance(lead.get("raw"), dict) else {}
    author_meta = raw.get("authorMeta") if isinstance(raw.get("authorMeta"), dict) else {}
    for chunk in (
        str(lead.get("description") or ""),
        str(author_meta.get("signature") or ""),
        str(author_meta.get("bioLink") or ""),
    ):
        found = extract_emails(chunk)
        if found:
            return found[0]
    return None


def build_company_brief(lead: Dict[str, Any], email: Optional[str] = None) -> str:
    account = str(lead.get("account") or "").lstrip("@")
    description = str(lead.get("description") or "")
    parts = [
        f"TikTok recruiter @{account} posted a hiring video.",
        description,
        f"Video: {lead.get('video_url', '')}",
    ]
    if email:
        parts.append(f"Contact email found: {email}")
    return "\n".join(p for p in parts if p)
