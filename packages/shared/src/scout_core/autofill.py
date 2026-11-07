"""Profile for the Scout Autofill Chrome extension, built from my active resume version.

Only plain facts a job form asks for. Questions that are my call (work authorisation, salary, notice
period, demographics, consent) are never part of it.
"""

import re
from typing import Any

from scout_core.db.models import MasterResume
from scout_core.render_resume import header_links
from scout_core.resume import Contact
from scout_core.resume_store import resume_data


def build_profile(row: MasterResume, *, last_name: str | None = None, city: str | None = None) -> dict[str, Any]:
    p = row.parsed or {}
    data, contact = resume_data(p), Contact.model_validate(p.get("contact") or {})
    name = (contact.name or "").replace(".", ". ").split()
    # "B. Sai Pranav": a leading initial is usually the family name in India; put it last (editable).
    initials = [w for w in name if len(w.rstrip(".")) == 1]
    given = [w for w in name if len(w.rstrip(".")) > 1]
    first = (" ".join(given[:-1]) if not initials and len(given) > 1 else " ".join(given)) or None
    last = " ".join(w.rstrip(".") for w in initials) if initials else (given[-1] if len(given) > 1 else None)
    if last_name:
        last = last_name
    links = contact.links
    edu = data.education[0] if data.education else None
    year = None
    if edu and edu.end:
        m = re.search(r"(19|20)\d{2}", edu.end)
        year = m.group(0) if m else None
    return {
        "version": 1, "resume_version": row.version, "reviewed": bool(p.get("reviewed")),
        "first_name": first, "last_name": last,
        "full_name": " ".join(x for x in (first, last) if x) if last_name else (" ".join(name) or None),
        "email": contact.emails[0] if contact.emails else None,
        "phone": contact.phones[0] if contact.phones else None,
        "linkedin": next((u for u in links if "linkedin.com" in u.lower()), None),
        "github": next((("https://" + u) for u in header_links(links, 5) if u.startswith("github.com")), None),
        "portfolio": next((u for u in links if "linkedin.com" not in u.lower() and "github.com" not in u.lower()), None),
        "location": city,
        "school": edu.institution if edu else None, "degree": edu.degree if edu else None,
        "discipline": edu.field if edu else None, "graduation_year": year, "gpa": edu.grade if edu else None,
        "years_experience": "0",
    }
