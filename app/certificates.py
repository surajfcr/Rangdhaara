"""Course completion certificates, issued once and stored so they can be re-sent unchanged."""
import secrets
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from .db import iso, one
from .shipping import IST
from .storage import resolve_key

_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
# Fonts that can draw Indic scripts, tried in order when a name isn't plain Latin: (path, index in a .ttc collection).
_UNICODE_FONTS = [
    ("C:/Windows/Fonts/Nirmala.ttc", 0),
    ("C:/Windows/Fonts/Nirmala.ttf", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
]


def get_or_issue(conn, enrollment: dict, student_name: str, course_title: str) -> dict:
    existing = one(conn, "SELECT * FROM certificates WHERE enrollment_id = ?", (enrollment["id"],))
    if existing and resolve_key(existing["storage_key"]).exists():
        return existing
    cert_id = existing["id"] if existing else "RGC-" + "".join(secrets.choice(_ALPHABET) for _ in range(8))
    key = f"certificates/{cert_id}.pdf"
    issued = existing["issued_at"] if existing else iso()
    _render(resolve_key(key), cert_id, student_name.strip() or "Rangdhaara Student", course_title, issued)
    if not existing:
        conn.execute(
            "INSERT INTO certificates (id, enrollment_id, student_name, course_title, storage_key, issued_at) VALUES (?, ?, ?, ?, ?, ?)",
            (cert_id, enrollment["id"], student_name, course_title, key, issued),
        )
    return one(conn, "SELECT * FROM certificates WHERE id = ?", (cert_id,))


def _latin(text: str) -> bool:
    try:
        text.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


def _render(path: Path, cert_id: str, student: str, course: str, issued_iso: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_title(f"Certificate {cert_id}")
    pdf.add_page()
    width, height = 297, 210

    name_font, course_font = ("Times", "I"), ("Times", "B")
    if not (_latin(student) and _latin(course)):
        for candidate, index in _UNICODE_FONTS:
            if Path(candidate).exists():
                pdf.add_font("Unicode", "", candidate, collection_font_number=index)
                try:
                    pdf.set_text_shaping(True)  # correct Devanagari conjuncts when uharfbuzz is installed
                except Exception:
                    pass
                name_font = course_font = ("Unicode", "")
                break
        else:
            student = student.encode("latin-1", "replace").decode("latin-1")
            course = course.encode("latin-1", "replace").decode("latin-1")

    pdf.set_fill_color(250, 246, 240)
    pdf.rect(0, 0, width, height, style="F")
    pdf.set_draw_color(184, 92, 56)
    pdf.set_line_width(1.4)
    pdf.rect(12, 12, width - 24, height - 24)
    pdf.set_line_width(0.3)
    pdf.rect(17, 17, width - 34, height - 34)

    pdf.set_text_color(184, 92, 56)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_xy(0, 34)
    pdf.cell(width, 8, text="R A N G D H A A R A   A R T   A C A D E M Y", align="C")

    pdf.set_text_color(35, 32, 29)
    pdf.set_font("Times", "B", 36)
    pdf.set_xy(0, 50)
    pdf.cell(width, 16, text="Certificate of Completion", align="C")

    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(90, 82, 75)
    pdf.set_xy(0, 80)
    pdf.cell(width, 8, text="This certifies that", align="C")

    pdf.set_text_color(35, 32, 29)
    pdf.set_font(name_font[0], name_font[1], 30)
    pdf.set_xy(0, 93)
    pdf.cell(width, 16, text=student, align="C")
    pdf.set_draw_color(207, 193, 178)
    pdf.set_line_width(0.4)
    pdf.line(width / 2 - 75, 112, width / 2 + 75, 112)

    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(90, 82, 75)
    pdf.set_xy(0, 119)
    pdf.cell(width, 8, text="has completed the masterclass", align="C")

    pdf.set_text_color(35, 32, 29)
    pdf.set_font(course_font[0], course_font[1], 22)
    pdf.set_xy(35, 131)
    pdf.multi_cell(width - 70, 10, text=course, align="C")

    issued = datetime.strptime(issued_iso, "%Y-%m-%dT%H:%M:%SZ").astimezone(IST).strftime("%d %B %Y")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(90, 82, 75)
    pdf.set_xy(30, 172)
    pdf.cell(80, 6, text=f"Issued {issued}", align="L")
    pdf.set_xy(width - 110, 172)
    pdf.cell(80, 6, text=f"Certificate ID {cert_id}", align="R")
    pdf.set_draw_color(184, 92, 56)
    pdf.line(width / 2 - 35, 170, width / 2 + 35, 170)
    pdf.set_font("Times", "I", 12)
    pdf.set_text_color(35, 32, 29)
    pdf.set_xy(0, 172)
    pdf.cell(width, 6, text="Rangdhaara Art Studio", align="C")
    pdf.output(str(path))
