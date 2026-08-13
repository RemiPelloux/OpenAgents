"""Source-backed normalization and qualification evidence for TikTok leads."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
VIDEO_PATH_PATTERN = re.compile(r"/(?:@[^/]+/)?video/\d+", re.IGNORECASE)
INSTRUCTION_PATTERN = re.compile(
    r"\b(?:ignore (?:all |the )?(?:previous|prior) instructions|system prompt|developer message|"
    r"reveal (?:the )?(?:prompt|secret|api key)|call (?:the )?tool)\b",
    re.IGNORECASE,
)
GENERIC_IDENTITIES = frozenset(
    {
        "admin",
        "creator",
        "emploi",
        "jobs",
        "job",
        "officiel",
        "official",
        "recrutement",
        "recruiter",
        "rh",
        "tiktok",
        "user",
    }
)
PERSONAL_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "outlook.com",
        "proton.me",
        "protonmail.com",
        "yahoo.com",
        "yahoo.fr",
    }
)

_STRONG_HIRING_PATTERNS = {
    "hiring": re.compile(r"\b(?:hiring|now hiring|we are hiring|we're hiring)\b"),
    "recruiting": re.compile(r"\b(?:recrute|recrutons|recrutement|embauche|recruiting)\b"),
    "open_position": re.compile(
        r"\b(?:poste a pourvoir|offre d emploi|job opening|open position|join (?:our|the) team|"
        r"rejoignez (?:notre|l )equipe)\b"
    ),
}
_SUPPORTING_HIRING_PATTERNS = {
    "employment_type": re.compile(r"\b(?:cdi|cdd|alternance|apprentissage|internship|stage)\b"),
    "application": re.compile(
        r"\b(?:candidature|candidatez|postule|postulez|apply now|send (?:us )?your cv|envoyez (?:nous )?votre cv)\b"
    ),
    "role": re.compile(
        r"\b(?:commercial|vendeur|vendeuse|serveur|serveuse|barista|developpeur|developer|"
        r"designer|manager|assistant|technicien|engineer)\b"
    ),
}
_NEGATIVE_HIRING_PATTERN = re.compile(
    r"\b(?:ne recrute pas|plus de recrutement|not hiring|no vacancies|position closed|poste pourvu)\b"
)


def _text(value: Any, limit: int = 2_000) -> str:
    if not isinstance(value, (str, int, float)):
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value)).strip()[:limit]


def _normalized_text(value: Any) -> str:
    value = unicodedata.normalize("NFKD", _text(value).lower())
    return "".join(char for char in value if not unicodedata.combining(char))


def _nested(mapping: Dict[str, Any], *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first(values: Iterable[Any]) -> str:
    for value in values:
        result = _text(value)
        if result:
            return result
    return ""


def _safe_http_url(value: Any) -> str | None:
    raw = _text(value, 1_024)
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))


def normalize_tiktok_url(value: Any) -> str | None:
    url = _safe_http_url(value)
    if not url:
        return None
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname != "tiktok.com" and not hostname.endswith(".tiktok.com"):
        return None
    path = parsed.path.rstrip("/")
    if not VIDEO_PATH_PATTERN.search(path):
        return None
    return urlunsplit(("https", "www.tiktok.com", path, "", ""))


def normalize_tiktok_profile_url(value: Any) -> str | None:
    url = _safe_http_url(value)
    if not url:
        return None
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/")
    if (hostname != "tiktok.com" and not hostname.endswith(".tiktok.com")) or not path.startswith("/@"):
        return None
    return urlunsplit(("https", "www.tiktok.com", path, "", ""))


def _profile_url_from_account(value: Any) -> str | None:
    account = _text(value, 64).lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9._-]{2,32}", account):
        return None
    return f"https://www.tiktok.com/@{account}"


def extract_emails(text: str) -> List[str]:
    if not text:
        return []
    return list(dict.fromkeys(match.lower() for match in EMAIL_PATTERN.findall(text)))


def extract_email_from_lead(lead: Dict[str, Any]) -> Optional[str]:
    raw = lead.get("raw") if isinstance(lead.get("raw"), dict) else {}
    author_meta = raw.get("authorMeta") if isinstance(raw.get("authorMeta"), dict) else {}
    for chunk in (
        lead.get("email"),
        lead.get("description"),
        author_meta.get("signature"),
        author_meta.get("bioLink"),
    ):
        found = extract_emails(_text(chunk))
        if found:
            return found[0]
    return None


def _identity_candidate(value: Any, source: str, confidence: float) -> dict[str, Any] | None:
    name = re.sub(r"\s+", " ", _text(value, 255)).strip(" @|,;:-")
    if not name or name.lower() in GENERIC_IDENTITIES or name.isdigit():
        return None
    return {"name": name, "source": source, "confidence": confidence}


def _company_candidates(lead: Dict[str, Any], raw: Dict[str, Any], author: Dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    values = (
        (lead.get("company_name"), "lead.company_name", 1.0),
        (raw.get("companyName"), "raw.companyName", 0.95),
        (raw.get("businessName"), "raw.businessName", 0.95),
        (author.get("businessName"), "author.businessName", 0.95),
        (author.get("nickName"), "author.nickName", 0.85),
        (author.get("nickname"), "author.nickname", 0.85),
    )
    seen: set[str] = set()
    for value, source, confidence in values:
        candidate = _identity_candidate(value, source, confidence)
        if candidate and candidate["name"].casefold() not in seen:
            seen.add(candidate["name"].casefold())
            candidates.append(candidate)

    handle = _first((lead.get("account"), raw.get("author"), author.get("name"))).lstrip("@")
    handle_name = re.sub(r"[._-]+", " ", handle)
    handle_candidate = _identity_candidate(handle_name, "author.handle", 0.65)
    if handle_candidate and handle_candidate["name"].casefold() not in seen:
        candidates.append(handle_candidate)
    return candidates


def _hiring_evidence(lead: Dict[str, Any], raw: Dict[str, Any], author: Dict[str, Any]) -> dict[str, Any]:
    source_text = "\n".join(
        filter(
            None,
            (
                _text(lead.get("description")),
                _text(raw.get("text")),
                _text(author.get("signature")),
            ),
        )
    )
    normalized = _normalized_text(source_text)
    strong = [name for name, pattern in _STRONG_HIRING_PATTERNS.items() if pattern.search(normalized)]
    supporting = [name for name, pattern in _SUPPORTING_HIRING_PATTERNS.items() if pattern.search(normalized)]
    negative = bool(_NEGATIVE_HIRING_PATTERN.search(normalized))
    credible = not negative and (bool(strong) or len(supporting) >= 2)
    return {
        "credible": credible,
        "strong_signals": strong,
        "supporting_signals": supporting,
        "negative_signal": negative,
        "evidence_excerpt": source_text[:500],
    }


def analyze_tiktok_lead(lead: Dict[str, Any]) -> dict[str, Any]:
    raw = lead.get("raw") if isinstance(lead.get("raw"), dict) else {}
    author = raw.get("authorMeta") if isinstance(raw.get("authorMeta"), dict) else {}
    source_video_url = _text(lead.get("video_url") or raw.get("webVideoUrl"), 1_024)
    account = _first((lead.get("account"), raw.get("author"), author.get("name")))
    video_url = normalize_tiktok_url(source_video_url)
    profile_url = normalize_tiktok_profile_url(
        lead.get("profile_url") or author.get("profileUrl")
    ) or _profile_url_from_account(account)
    candidates = _company_candidates(lead, raw, author)
    company = candidates[0] if candidates else None
    email = extract_email_from_lead(lead)
    email_domain = email.rsplit("@", 1)[1] if email else None
    website = _safe_http_url(author.get("bioLink"))
    website_domain = (urlsplit(website).hostname or "").lower() if website else None
    location = _first(
        (
            lead.get("city"),
            _nested(raw, "locationMeta", "city"),
        )
    )
    location_source = next(
        (
            source
            for value, source in (
                (lead.get("city"), "lead.city"),
                (_nested(raw, "locationMeta", "city"), "raw.locationMeta.city"),
            )
            if _text(value)
        ),
        None,
    )
    country = _first(
        (
            _nested(raw, "locationMeta", "countryCode"),
            raw.get("locationCreated"),
            author.get("region"),
        )
    )
    hiring = _hiring_evidence(lead, raw, author)
    content = "\n".join(
        (_text(lead.get("description")), _text(raw.get("text")), _text(author.get("signature")))
    )
    injection_detected = bool(INSTRUCTION_PATTERN.search(content))
    identity_confidence = float(company["confidence"]) if company else 0.0
    strong_video_identity = bool(video_url)
    blockers: list[str] = []
    if not video_url:
        blockers.append("invalid_or_missing_tiktok_video_url")
    if not hiring["credible"]:
        blockers.append("hiring_need_unconfirmed")
    if identity_confidence < 0.65:
        blockers.append("company_identity_unresolved")

    score = 0
    score += 20 if video_url else 0
    score += 5 if strong_video_identity else 0
    score += 25 if identity_confidence >= 0.8 else 15 if identity_confidence >= 0.65 else 0
    score += 30 if hiring["credible"] else 0
    score += 10 if email else 0
    score += 5 if website_domain else 0
    score += 5 if location else 0
    preflight_pass = not blockers and score >= 60
    commerce = author.get("commerceUserInfo") if isinstance(author.get("commerceUserInfo"), dict) else {}

    result = {
        "preflight_pass": preflight_pass,
        "requires_model_review": True,
        "quality_score": score,
        "rejection_reasons": blockers,
        "normalized": {
            "video_url": video_url,
            "profile_url": profile_url,
            "account": account,
            "company_name": company["name"] if company else None,
            "city": location or None,
            "email": email,
            "email_domain": email_domain,
            "website": website,
            "website_domain": website_domain,
        },
        "company_evidence": {
            "selected": company,
            "candidates": candidates,
            "business_profile": bool(commerce.get("commerceUser")),
            "business_category": _text(commerce.get("category"), 120) or None,
            "verified_profile": bool(author.get("verified")),
        },
        "contact_evidence": {
            "email_source_provided": bool(email),
            "email_domain_type": (
                "personal_provider" if email_domain in PERSONAL_EMAIL_DOMAINS else "organization_domain"
            )
            if email_domain
            else None,
            "website_source_provided": bool(website),
        },
        "location_evidence": {
            "city": location or None,
            "city_source": location_source,
            "country": country or None,
        },
        "hiring_evidence": hiring,
        "safety": {
            "embedded_instruction_detected": injection_detected,
            "embedded_instructions_ignored": True,
        },
        "source_metrics": {
            "plays": lead.get("plays") or raw.get("playCount"),
            "followers": author.get("fans"),
            "published_at": lead.get("published_at") or raw.get("createTimeISO"),
        },
        "source": {"video_url": source_video_url or None},
    }
    result["brief"] = build_company_brief(lead, email, analysis=result)
    result.update(
        {
            "email": email,
            "account": result["normalized"]["account"],
            "video_url": result["normalized"]["video_url"],
            "source_video_url": source_video_url or None,
            "profile_url": result["normalized"]["profile_url"],
            "company_name": result["normalized"]["company_name"],
            "city": result["normalized"]["city"],
            "description": _text(lead.get("description"), 1_000),
        }
    )
    return result


def filter_tiktok_leads(leads: list[Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for index, lead in enumerate(leads[:100]):
        if not isinstance(lead, dict):
            rejected.append({"index": index, "rejection_reasons": ["invalid_lead_object"]})
            continue
        analysis = analyze_tiktok_lead(lead)
        video_url = analysis["normalized"]["video_url"]
        if video_url and video_url in seen_urls:
            duplicates.append({"index": index, "video_url": video_url, "reason": "duplicate_video_url"})
            continue
        if video_url:
            seen_urls.add(video_url)
        item = {"index": index, **analysis}
        (candidates if analysis["preflight_pass"] else rejected).append(item)

    return {
        "input_count": len(leads),
        "evaluated_count": min(len(leads), 100),
        "truncated": len(leads) > 100,
        "unique_count": len(candidates) + len(rejected),
        "candidate_count": len(candidates),
        "rejected_count": len(rejected),
        "duplicate_count": len(duplicates),
        "candidates": candidates,
        "rejected": rejected,
        "duplicates": duplicates,
        "decision_policy": "deterministic_preflight_then_model_review",
    }


def build_company_brief(
    lead: Dict[str, Any],
    email: Optional[str] = None,
    *,
    analysis: Optional[Dict[str, Any]] = None,
) -> str:
    normalized = analysis.get("normalized", {}) if analysis else {}
    account = _text(normalized.get("account") or lead.get("account")).lstrip("@")
    description = _text(lead.get("description"), 1_000)
    parts = [
        f"TikTok source account: @{account}" if account else "TikTok source account: unknown",
        f"Company candidate: {normalized.get('company_name')}" if normalized.get("company_name") else "",
        description,
        f"Video: {normalized.get('video_url') or lead.get('video_url', '')}",
    ]
    if email:
        parts.append(f"Source-provided contact email: {email}")
    if normalized.get("website"):
        parts.append(f"Source-provided profile website: {normalized['website']}")
    return "\n".join(part for part in parts if part)
