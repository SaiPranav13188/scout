"""Render a resume document to an ATS-safe, single-column, one-page PDF (and DOCX). Plain code.

Standard fonts only (no icon fonts), contact links as plain text, real text (no images), so job
portals can read it. If the content runs over one page, the least important lines are dropped
(last bullets first, then achievements) until it fits; what was dropped is reported.
"""

import copy
import io
import unicodedata
from typing import Any
from xml.sax.saxutils import escape

_REPLACE = {"→": "->", "←": "<-", "✓": "", "★": "*", "≥": ">=", "≤": "<=", "×": "x", "…": "...",
            " ": " ", "​": ""}


def _safe(text: str | None) -> str:
    """Keep characters the standard PDF fonts can draw (WinAnsi); map or drop the rest."""
    if not text:
        return ""
    out = []
    for ch in text:
        ch = _REPLACE.get(ch, ch)
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            out.append(unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode())
    return "".join(out)


def header_links(links: list[str], limit: int = 3) -> list[str]:
    """LinkedIn, GitHub profile, portfolio first; per-project repo links stay out of the header."""
    import re

    def rank(u: str) -> int:
        low = u.lower()
        if "linkedin.com" in low:
            return 0
        if re.search(r"github\.com/[^/]+/?$", low):
            return 1
        if "github.com" in low:
            return 9  # a project repo
        return 2
    picked = [u for u in sorted(links, key=rank) if rank(u) < 9][:limit]
    return [re.sub(r"^https?://(www\.)?", "", u).rstrip("/") for u in picked]


def _dates(a: str | None, b: str | None) -> str:
    return " - ".join(x for x in (a, b) if x)


def _pdf_once(doc: dict[str, Any]) -> bytes:
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9.6, leading=12)
    name = ParagraphStyle("name", parent=base, fontName="Helvetica-Bold", fontSize=16, leading=19, alignment=TA_CENTER)
    centre = ParagraphStyle("centre", parent=base, alignment=TA_CENTER, fontSize=9)
    head = ParagraphStyle("head", parent=base, fontName="Helvetica-Bold", fontSize=10.5, leading=13, spaceBefore=5)
    item = ParagraphStyle("item", parent=base, fontName="Helvetica-Bold")
    bullet = ParagraphStyle("bullet", parent=base, leftIndent=10, bulletIndent=2)

    def p(text: str, style=base):
        return Paragraph(text, style)

    def t(x: str | None) -> str:
        return escape(_safe(x))

    story: list = []
    c = doc.get("contact") or {}
    story.append(p(t(c.get("name") or "Resume"), name))
    line = " | ".join(t(x) for x in [*c.get("emails", [])[:1], *c.get("phones", [])[:1], *header_links(c.get("links", []))])
    if line:
        story.append(p(line, centre))
    if doc.get("headline"):
        story.append(p(t(doc["headline"]), centre))

    def section(title: str):
        story.append(p(title.upper(), head))
        story.append(HRFlowable(width="100%", thickness=0.6, spaceBefore=1, spaceAfter=3))

    if doc.get("summary"):
        section("Summary")
        story.append(p(t(doc["summary"])))
    if doc.get("education"):
        section("Education")
        for e in doc["education"]:
            degree = ", ".join(x for x in (e.get("degree"), e.get("field")) if x)
            right = _dates(e.get("start"), e.get("end"))
            story.append(p(f"<b>{t(e.get('institution'))}</b>" + (f" - {t(degree)}" if degree else "")
                           + (f" ({t(right)})" if right else "") + (f" | {t(e.get('grade'))}" if e.get("grade") else "")))
    if doc.get("skills"):
        section("Skills")
        for g in doc["skills"]:
            story.append(p(f"<b>{t(g['category'])}:</b> {t(', '.join(g['items']))}"))
    if doc.get("projects"):
        section("Projects")
        for pr in doc["projects"]:
            meta = " | ".join(x for x in (", ".join(pr.get("tech") or []), pr.get("dates"), pr.get("link_label")) if x)
            story.append(p(f"{t(pr['name'])}" + (f" <font name='Helvetica'>| {t(meta)}</font>" if meta else ""), item))
            story += [Paragraph(t(b), bullet, bulletText="•") for b in pr.get("bullets", [])]
            story.append(Spacer(1, 2))
    if doc.get("experience"):
        section("Experience")
        for ex in doc["experience"]:
            right = " | ".join(x for x in (_dates(ex.get("start"), ex.get("end")), ex.get("location")) if x)
            story.append(p(f"{t(ex['role'])}, {t(ex['organization'])}" + (f" <font name='Helvetica'>| {t(right)}</font>" if right else ""), item))
            story += [Paragraph(t(b), bullet, bulletText="•") for b in ex.get("bullets", [])]
            story.append(Spacer(1, 2))
    if doc.get("achievements"):
        section("Achievements")
        story += [Paragraph(t(a), bullet, bulletText="•") for a in doc["achievements"]]
    if doc.get("certifications"):
        section("Certifications")
        story += [Paragraph(t(a), bullet, bulletText="•") for a in doc["certifications"]]

    buf = io.BytesIO()
    title = f"{_safe(c.get('name') or 'Resume')} - Resume"
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=11 * mm,
                      bottomMargin=11 * mm, title=title, author=_safe(c.get("name") or "")).build(story)
    return buf.getvalue()


def page_count(pdf: bytes) -> int:
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(pdf)).pages)


def _trim_once(doc: dict[str, Any]) -> str | None:
    """Drop the least important line. Returns what was dropped, or None if nothing is left to drop."""
    groups = [("experience", e) for e in doc.get("experience", [])] + [("projects", p) for p in doc.get("projects", [])]
    longest = max((g for g in groups if len(g[1].get("bullets", [])) > 2), key=lambda g: len(g[1]["bullets"]), default=None)
    if longest:
        return f"bullet: {longest[1]['bullets'].pop()[:60]}"
    if doc.get("achievements"):
        return f"achievement: {doc['achievements'].pop()[:60]}"
    if doc.get("certifications"):
        return f"certification: {doc['certifications'].pop()[:60]}"
    longest = max((g for g in groups if len(g[1].get("bullets", [])) > 1), key=lambda g: len(g[1]["bullets"]), default=None)
    if longest:
        return f"bullet: {longest[1]['bullets'].pop()[:60]}"
    return None


def fit_one_page(doc: dict[str, Any]) -> tuple[dict[str, Any], bytes, list[str]]:
    """Render, dropping the least important lines until it fits on one page."""
    doc = copy.deepcopy(doc)
    dropped: list[str] = []
    pdf = _pdf_once(doc)
    while page_count(pdf) > 1:
        what = _trim_once(doc)
        if what is None:
            break
        dropped.append(what)
        pdf = _pdf_once(doc)
    return doc, pdf, dropped


def render_pdf(doc: dict[str, Any]) -> bytes:
    return _pdf_once(doc)


def render_docx(doc: dict[str, Any]) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    d = Document()
    for s in d.sections:
        s.left_margin = s.right_margin = Mm(14)
        s.top_margin = s.bottom_margin = Mm(11)
    style = d.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(10)
    style.paragraph_format.space_after = Pt(1)

    c = doc.get("contact") or {}
    h = d.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = h.add_run(c.get("name") or "Resume")
    r.bold, r.font.size = True, Pt(16)
    line = " | ".join([*c.get("emails", [])[:1], *c.get("phones", [])[:1], *header_links(c.get("links", []))])
    if line:
        d.add_paragraph(line).alignment = WD_ALIGN_PARAGRAPH.CENTER
    if doc.get("headline"):
        d.add_paragraph(doc["headline"]).alignment = WD_ALIGN_PARAGRAPH.CENTER

    def section(title: str):
        para = d.add_paragraph()
        run = para.add_run(title.upper())
        run.bold, run.font.size = True, Pt(11)
        para.paragraph_format.space_before = Pt(6)

    def bullets(items: list[str]):
        for b in items:
            d.add_paragraph(b, style="List Bullet")

    if doc.get("summary"):
        section("Summary")
        d.add_paragraph(doc["summary"])
    if doc.get("education"):
        section("Education")
        for e in doc["education"]:
            degree = ", ".join(x for x in (e.get("degree"), e.get("field")) if x)
            para = d.add_paragraph()
            para.add_run(e.get("institution") or "").bold = True
            rest = (f" - {degree}" if degree else "") + (f" ({_dates(e.get('start'), e.get('end'))})" if e.get("start") or e.get("end") else "")
            para.add_run(rest + (f" | {e['grade']}" if e.get("grade") else ""))
    if doc.get("skills"):
        section("Skills")
        for g in doc["skills"]:
            para = d.add_paragraph()
            para.add_run(f"{g['category']}: ").bold = True
            para.add_run(", ".join(g["items"]))
    for key, title in (("projects", "Projects"), ("experience", "Experience")):
        if not doc.get(key):
            continue
        section(title)
        for it in doc[key]:
            para = d.add_paragraph()
            if key == "projects":
                para.add_run(it["name"]).bold = True
                meta = " | ".join(x for x in (", ".join(it.get("tech") or []), it.get("dates"), it.get("link_label")) if x)
            else:
                para.add_run(f"{it['role']}, {it['organization']}").bold = True
                meta = " | ".join(x for x in (_dates(it.get("start"), it.get("end")), it.get("location")) if x)
            if meta:
                para.add_run(f" | {meta}")
            bullets(it.get("bullets", []))
    if doc.get("achievements"):
        section("Achievements")
        bullets(doc["achievements"])
    if doc.get("certifications"):
        section("Certifications")
        bullets(doc["certifications"])
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def ats_text_check(pdf: bytes, doc: dict[str, Any]) -> dict[str, Any]:
    """Can a job portal read it? Extract the text back and look for the key parts."""
    from pypdf import PdfReader

    text = " ".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(pdf)).pages)
    flat = " ".join(text.split()).lower()
    c = doc.get("contact") or {}
    expected = [x for x in [c.get("name"), *(c.get("emails") or [])[:1]] if x]
    expected += [h for h, k in (("skills", "skills"), ("projects", "projects"), ("education", "education")) if doc.get(k)]
    expected += [p["name"] for p in doc.get("projects", [])[:3]]
    missing = [e for e in expected if " ".join(_safe(e).split()).lower() not in flat]
    return {"ok": not missing, "missing": missing, "characters": len(text)}
