"""Deterministic normalisation of companies, titles, locations and URLs."""

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

from scout_core.text import fold

# Offline extractor: uses the bundled public-suffix snapshot, no network call at runtime.
_tld = tldextract.TLDExtract(suffix_list_urls=())

# ---------------------------------------------------------------- company

_LEGAL_SUFFIXES = re.compile(
    r"\b(private limited|pvt\.? ltd\.?|pvt|ltd\.?|limited|llc|l\.l\.c\.|inc\.?|incorporated|"
    r"corp\.?|corporation|co\.|gmbh|s\.a\.|plc|llp|technologies private limited)\s*$"
)


def normalize_company(name: str | None) -> str:
    n = fold(name)
    n = re.sub(r"[,.]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    previous = None
    while previous != n:
        previous = n
        n = _LEGAL_SUFFIXES.sub("", n).strip()
    n = re.sub(r"[^a-z0-9 ]", "", n)
    return re.sub(r"\s+", " ", n).strip()


# ---------------------------------------------------------------- title

_TITLE_ABBREVIATIONS = [
    (r"\bsr\b\.?", "senior"),
    (r"\bjr\b\.?", "junior"),
    (r"\bswe\b", "software engineer"),
    (r"\bsde\b", "software development engineer"),
    (r"\bengg\b\.?", "engineer"),
    (r"\bdev\b", "developer"),
    (r"\bml\b", "machine learning"),
    (r"\bai/ml\b", "ai machine learning"),
    (r"\bfull[- ]?stack\b", "fullstack"),
    (r"\bback[- ]?end\b", "backend"),
    (r"\bfront[- ]?end\b", "frontend"),
]


def normalize_title(title: str | None) -> str:
    t = fold(title)
    # Drop bracketed noise like "(Remote)", "[Bengaluru]", "- 2026 batch" is kept (it is signal).
    t = re.sub(r"[\(\[][^\)\]]*(remote|hybrid|onsite|on-site|wfh|india|bengaluru|bangalore)[^\)\]]*[\)\]]", " ", t)
    for pattern, repl in _TITLE_ABBREVIATIONS:
        t = re.sub(pattern, repl, t)
    t = re.sub(r"[^a-z0-9+#/ ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- location

INDIA_PLACES = {
    "india": "india",
    "bengaluru": "bengaluru", "bangalore": "bengaluru", "blr": "bengaluru",
    "mumbai": "mumbai", "bombay": "mumbai", "navi mumbai": "mumbai", "thane": "mumbai",
    "delhi": "delhi", "new delhi": "delhi", "ncr": "delhi", "delhi ncr": "delhi",
    "gurugram": "gurugram", "gurgaon": "gurugram", "noida": "noida", "greater noida": "noida",
    "hyderabad": "hyderabad", "secunderabad": "hyderabad",
    "chennai": "chennai", "madras": "chennai",
    "pune": "pune", "kolkata": "kolkata", "calcutta": "kolkata",
    "ahmedabad": "ahmedabad", "gandhinagar": "ahmedabad", "jaipur": "jaipur",
    "kochi": "kochi", "cochin": "kochi", "coimbatore": "coimbatore", "indore": "indore",
    "chandigarh": "chandigarh", "mohali": "chandigarh",
    "thiruvananthapuram": "thiruvananthapuram", "trivandrum": "thiruvananthapuram",
    "mysuru": "mysuru", "mysore": "mysuru", "nagpur": "nagpur", "bhubaneswar": "bhubaneswar",
    "vadodara": "vadodara", "visakhapatnam": "visakhapatnam", "vizag": "visakhapatnam",
    "lucknow": "lucknow", "surat": "surat", "mangaluru": "mangaluru", "mangalore": "mangaluru",
}

_REMOTE_WORDS = re.compile(r"\b(remote|anywhere|work from home|wfh|distributed|fully remote)\b")
_REMOTE_OPEN_REGIONS = re.compile(r"\b(worldwide|global|anywhere|apac|asia|asia pacific|india)\b")
_FOREIGN_REGIONS = re.compile(
    r"\b(us|u\.s\.|usa|united states|canada|uk|united kingdom|england|europe|eu|emea|germany|"
    r"france|netherlands|spain|portugal|poland|ireland|australia|new zealand|singapore|brazil|"
    r"latam|latin america|mexico|americas|north america|japan|israel|uae|dubai|switzerland|"
    r"sweden|denmark|norway|finland|austria|italy|belgium|philippines|indonesia|vietnam|"
    r"est|pst|cet|gmt|utc[+-]?[0-9]*|[a-z ]+, (ca|ny|tx|wa|ma|il|co|ga|fl|nc|va|or|pa|nj|az|ut))\b"
)


class LocationBucket(StrEnum):
    INDIA = "india"
    REMOTE_GLOBAL = "remote_global"
    REMOTE_RESTRICTED = "remote_restricted"  # remote, but only for another country/region
    ABROAD = "abroad"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LocationInfo:
    bucket: LocationBucket
    india_cities: tuple[str, ...]
    remote: bool | None


def _india_cities(text: str) -> list[str]:
    found = []
    for alias, city in INDIA_PLACES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text) and city not in found:
            found.append(city)
    return found


def classify_location(
    locations: list[str],
    remote_flag: bool | None = None,
    country_codes: list[str] | None = None,
) -> LocationInfo:
    """Decide whether someone based in India can take this job, from location strings only."""
    folded = [fold(loc) for loc in locations if loc and loc.strip()]
    joined = " | ".join(folded)
    cities = _india_cities(joined)
    codes = {c.upper() for c in (country_codes or []) if c}
    remote = remote_flag if remote_flag is not None else (True if _REMOTE_WORDS.search(joined) else None)

    if cities or "IN" in codes:
        return LocationInfo(LocationBucket.INDIA, tuple(cities or ["india"]), remote)
    if remote:
        if _REMOTE_OPEN_REGIONS.search(joined):
            return LocationInfo(LocationBucket.REMOTE_GLOBAL, (), True)
        if _FOREIGN_REGIONS.search(joined) or (codes and "IN" not in codes):
            return LocationInfo(LocationBucket.REMOTE_RESTRICTED, (), True)
        # Anything named besides the remote wording itself is a place, and it isn't India:
        # "Remote" -> global, but "Remote · Shenzhen, China" -> restricted to that place.
        leftover = _REMOTE_WORDS.sub(" ", joined)
        leftover = re.sub(r"[^a-z]+", " ", leftover)
        leftover = re.sub(r"\b(first|friendly|only|ok|optional|hybrid|global|team|position|role)\b", " ", leftover).strip()
        if leftover:
            return LocationInfo(LocationBucket.REMOTE_RESTRICTED, (), True)
        return LocationInfo(LocationBucket.REMOTE_GLOBAL, (), True)
    if not folded and not codes:
        return LocationInfo(LocationBucket.UNKNOWN, (), remote)
    return LocationInfo(LocationBucket.ABROAD, (), remote)


_REMOTE_ONLY_ABROAD = re.compile(
    r"(must|should|need to|required to) (be )?(currently )?(based|located|reside|residing|live|living) in "
    r"(the )?(us|u\.s\.|usa|united states|canada|uk|united kingdom|europe|eu|germany|australia|"
    r"north america|latam|latin america|philippines)\b"
    r"|(authori[sz]ed|eligible|legally able) to work in (the )?(us|u\.s\.|usa|united states|canada|uk|eu)\b"
    r"|\b(us|u\.s\.) work authori[sz]ation\b|\bus[- ]based (only|candidates|applicants)\b"
    r"|\bremote \((us|usa|united states|canada)( only)?\)|\bwithin the (us|united states|continental us)\b"
    r"|open to (us|united states|canada|uk|eu)[- ]based candidates"
)
_INDIA_WELCOME = re.compile(r"\b(india|indian|ist|apac|asia)\b")


def remote_restriction(description: str) -> str | None:
    """If a 'Remote' job's text limits it to another country, return the matching phrase."""
    text = fold(description)
    m = _REMOTE_ONLY_ABROAD.search(text)
    if not m:
        return None
    window = text[max(0, m.start() - 120): m.end() + 120]
    if _INDIA_WELCOME.search(window):
        return None
    return m.group(0)


_TITLE_REGION = re.compile(
    r"\b(us|usa|u\.s\.|united states|canada|uk|united kingdom|emea|europe|eu|latam|americas|"
    r"spain|germany|france|netherlands|poland|portugal|ireland|australia|japan|singapore|brazil|mexico|"
    r"philippines|israel|uae)\b"
)


def title_names_foreign_region(title: str) -> bool:
    """'Mobile Developer I - US Remote', 'Partner Engineer, Spain' -> True. 'US Shift' roles in India
    are not affected because this is only checked for remote jobs without an India location."""
    t = fold(title)
    return bool(_TITLE_REGION.search(t)) and not _INDIA_WELCOME.search(t)


def primary_location_key(info: LocationInfo, locations: list[str]) -> str:
    """Stable location token for the fuzzy dedupe key."""
    if info.india_cities:
        return info.india_cities[0]
    if info.bucket in (LocationBucket.REMOTE_GLOBAL, LocationBucket.REMOTE_RESTRICTED):
        return "remote"
    return fold(locations[0]) if locations else ""


# ---------------------------------------------------------------- URLs

_TRACKING_PARAMS = re.compile(
    r"^(utm_.*|gh_src|source|src|ref|referrer|lever-source.*|lever-origin|trk.*|refid|"
    r"trackingid|fbclid|gclid|mc_.*|_hs.*|campaign|medium|ashby_jid_src)$",
    re.IGNORECASE,
)
_ATS_SUFFIXES = re.compile(r"/(apply|application|apply/?thanks)/?$", re.IGNORECASE)


def canonical_url(url: str | None) -> str | None:
    """Canonical form used for dedupe: lowercased host, no tracking params, no apply suffix."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    host = parts.netloc.lower()
    host = host.removeprefix("www.")
    # Both Greenhouse board hosts serve the same job.
    if host == "boards.greenhouse.io":
        host = "job-boards.greenhouse.io"
    path = _ATS_SUFFIXES.sub("", parts.path).rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if not _TRACKING_PARAMS.match(k)]
    query.sort()
    return urlunsplit(("https", host, path, urlencode(query), ""))


def registered_domain(url_or_host: str | None) -> str | None:
    """'https://jobs.acme.co.in/x' -> 'acme.co.in'."""
    if not url_or_host:
        return None
    host = url_or_host
    if "//" in url_or_host:
        host = urlsplit(url_or_host).netloc
    ext = _tld(host.lower())
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"
