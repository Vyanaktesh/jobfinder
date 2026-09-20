"""PDF resume generator using fpdf2.

Renders a TailoredResume into a clean, one-page PDF that matches the
candidate's existing resume format: serif font, section headers with
underlines, bullet points, and consistent spacing.

Includes automatic page overflow detection — if content exceeds one page,
it progressively condenses (drops optional sections, trims bullets) and
retries until the resume fits.
"""
from __future__ import annotations

import copy
import logging
import re
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from src.resume.tailor import TailoredResume, load_master_resume


logger = logging.getLogger(__name__)


def resume_filename_prefix(master: dict) -> str:
    """'Last_First_Resume' built from contact.full_name in master_resume.yml.

    "Devanshi Patel" -> "Patel_Devanshi_Resume"; "Mary Ann Lee" -> "Lee_Mary_Ann_Resume".
    """
    parts = re.sub(r"[^\w\s-]", "", master.get("contact", {}).get("full_name", "")).split()
    if not parts:
        return "Resume"
    if len(parts) == 1:
        return f"{parts[0]}_Resume"
    return "_".join([parts[-1], *parts[:-1]]) + "_Resume"


# Unicode → ASCII replacements for built-in PDF fonts
_UNICODE_REPLACEMENTS = {
    "—": "-",   # em dash → hyphen
    "–": "-",   # en dash → hyphen
    "'": "'",   # left single quote
    "'": "'",   # right single quote (apostrophe)
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    "…": "...", # ellipsis
    "•": "-",   # bullet
    " ": " ",   # non-breaking space
    "​": "",    # zero-width space
    "·": "-",   # middle dot
}


def _sanitize_text(text: str) -> str:
    """Replace Unicode characters that built-in PDF fonts can't render."""
    for char, replacement in _UNICODE_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    # Fallback: strip any remaining non-latin-1 characters
    return text.encode("latin-1", errors="replace").decode("latin-1")

# Page layout constants (in mm)
PAGE_W = 215.9  # Letter width
PAGE_H = 279.4  # Letter height
MARGIN_LEFT = 15
MARGIN_RIGHT = 15
MARGIN_TOP = 12
MARGIN_BOTTOM = 10
CONTENT_W = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT
MAX_Y = PAGE_H - MARGIN_BOTTOM  # 269.4mm — content must stay above this

# Font sizes
NAME_SIZE = 22
CONTACT_SIZE = 9.5
SECTION_HEADER_SIZE = 10.5
COMPANY_SIZE = 10
ROLE_SIZE = 10
BULLET_SIZE = 9.5
SKILLS_SIZE = 9.5
EDUCATION_SIZE = 10

# Spacing
LINE_HEIGHT = 4.2
BULLET_HEIGHT = 4.0
SECTION_GAP = 2.5
BULLET_INDENT = 5
BULLET_TEXT_OFFSET = 3.5


class ResumePDF(FPDF):
    """Custom FPDF subclass for resume generation."""

    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.set_auto_page_break(auto=False)
        self.set_margins(MARGIN_LEFT, MARGIN_TOP, MARGIN_RIGHT)
        self.alias_nb_pages()

    def cell(self, *args, **kwargs):
        if args and len(args) >= 3 and isinstance(args[2], str):
            args = list(args)
            args[2] = _sanitize_text(args[2])
        if "text" in kwargs and isinstance(kwargs["text"], str):
            kwargs["text"] = _sanitize_text(kwargs["text"])
        return super().cell(*args, **kwargs)

    def multi_cell(self, *args, **kwargs):
        if args and len(args) >= 3 and isinstance(args[2], str):
            args = list(args)
            args[2] = _sanitize_text(args[2])
        if "text" in kwargs and isinstance(kwargs["text"], str):
            kwargs["text"] = _sanitize_text(kwargs["text"])
        return super().multi_cell(*args, **kwargs)

    def _section_header(self, title: str):
        """Render a section header with underline."""
        self.set_font("Times", "B", SECTION_HEADER_SIZE)
        self.cell(0, LINE_HEIGHT + 1, title.upper(), new_x="LMARGIN", new_y="NEXT")
        # Draw underline
        y = self.get_y() - 0.5
        self.line(MARGIN_LEFT, y, PAGE_W - MARGIN_RIGHT, y)
        self.set_y(y + 1.5)

    def _company_line(self, left_text: str, right_text: str, bold: bool = True):
        """Render a company/school line with right-aligned location."""
        style = "B" if bold else ""
        self.set_font("Times", style, COMPANY_SIZE)
        y = self.get_y()
        self.set_xy(MARGIN_LEFT, y)
        self.cell(CONTENT_W, LINE_HEIGHT, left_text)
        self.set_xy(MARGIN_LEFT, y)
        self.cell(CONTENT_W, LINE_HEIGHT, right_text, align="R")
        self.set_xy(MARGIN_LEFT, y + LINE_HEIGHT)
        self.ln(0)

    def _role_line(self, title: str, dates: str):
        """Render a role title (italic) with right-aligned dates."""
        self.set_font("Times", "I", ROLE_SIZE)
        y = self.get_y()
        self.set_xy(MARGIN_LEFT, y)
        self.cell(CONTENT_W, LINE_HEIGHT, title)
        self.set_xy(MARGIN_LEFT, y)
        self.cell(CONTENT_W, LINE_HEIGHT, dates, align="R")
        self.set_xy(MARGIN_LEFT, y + LINE_HEIGHT)
        self.ln(0)

    def _draw_bullet_dot(self, x: float, y: float, h: float):
        """Draw a small filled circle bullet at (x, y) aligned to line height h."""
        r = 0.6
        cx = x + 1.2
        cy = y + h / 2
        self.set_fill_color(0, 0, 0)
        self.ellipse(cx - r, cy - r, r * 2, r * 2, style="F")

    def _bullet_point(self, text: str):
        """Render a bullet point with hanging indent."""
        self.set_font("Times", "", BULLET_SIZE)
        x = MARGIN_LEFT + BULLET_INDENT
        y = self.get_y()

        self._draw_bullet_dot(x, y, BULLET_HEIGHT)

        # Text with wrapping
        text_x = x + BULLET_TEXT_OFFSET
        text_w = CONTENT_W - BULLET_INDENT - BULLET_TEXT_OFFSET
        self.set_xy(text_x, y)
        self.multi_cell(text_w, BULLET_HEIGHT, text)

    def _skills_line(self, label: str, skills: list[str]):
        """Render a skills line: 'Label: skill1 | skill2 | skill3'."""
        self.set_font("Times", "B", SKILLS_SIZE)
        label_text = f"{label}: "
        label_w = self.get_string_width(label_text) + 1
        self.cell(label_w, LINE_HEIGHT, label_text)

        self.set_font("Times", "", SKILLS_SIZE)
        skills_text = " | ".join(skills)
        remaining_w = CONTENT_W - label_w
        if self.get_string_width(skills_text) <= remaining_w:
            self.cell(remaining_w, LINE_HEIGHT, skills_text,
                      new_x="LMARGIN", new_y="NEXT")
        else:
            self.multi_cell(remaining_w, LINE_HEIGHT, skills_text)


def _render_resume_pdf(tailored: TailoredResume, master: dict) -> tuple[ResumePDF, float]:
    """Render the resume into a PDF and return (pdf, final_y_position).

    Does NOT save to disk — caller decides whether to save or retry.
    """
    pdf = ResumePDF()
    pdf.add_page()
    contact = master["contact"]

    # === NAME ===
    pdf.set_font("Times", "B", NAME_SIZE)
    pdf.cell(0, 9, contact["full_name"], align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    # === CONTACT LINE (with clickable LinkedIn) ===
    pdf.set_font("Times", "", CONTACT_SIZE)
    parts_before = f"{contact['location']} | "
    linkedin_text = contact.get("linkedin_text", "LinkedIn")
    linkedin_url = contact.get("linkedin_url", "")
    parts_after = f" | {contact['phone']} | {contact['email']}"
    full_line = parts_before + linkedin_text + parts_after
    full_w = pdf.get_string_width(full_line)
    x_start = (PAGE_W - full_w) / 2
    y = pdf.get_y()

    pdf.set_xy(x_start, y)
    pdf.cell(pdf.get_string_width(parts_before), LINE_HEIGHT, parts_before)
    pdf.set_text_color(0, 0, 180)
    pdf.set_font("Times", "U", CONTACT_SIZE)
    pdf.cell(pdf.get_string_width(linkedin_text), LINE_HEIGHT, linkedin_text, link=linkedin_url)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Times", "", CONTACT_SIZE)
    pdf.cell(pdf.get_string_width(parts_after), LINE_HEIGHT, parts_after, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    # === PROFESSIONAL SUMMARY ===
    pdf._section_header("Professional Summary")
    pdf.set_font("Times", "", BULLET_SIZE)
    pdf.multi_cell(CONTENT_W, BULLET_HEIGHT, tailored.summary)
    pdf.ln(SECTION_GAP)

    # === WORK EXPERIENCE ===
    pdf._section_header("Work Experience")

    for exp in tailored.experience:
        company = exp["company"]
        location = exp.get("location", "")
        pdf._company_line(company, location)

        for role in exp.get("roles", []):
            pdf._role_line(role["title"], role["dates"])
            for bullet_text in role.get("resolved_bullets", []):
                pdf._bullet_point(bullet_text)
            pdf.ln(0.5)

    pdf.ln(SECTION_GAP - 1)

    # === PROJECT ===
    if tailored.project and tailored.project.get("resolved_bullets"):
        proj_label = tailored.project.get("name", "Selected Project")
        pdf._section_header("Projects" if "project" in proj_label.lower() else "Selected Project")
        pdf.set_font("Times", "B", COMPANY_SIZE)
        pdf.cell(0, LINE_HEIGHT, proj_label, new_x="LMARGIN", new_y="NEXT")
        for bullet_text in tailored.project.get("resolved_bullets", []):
            pdf._bullet_point(bullet_text)
        pdf.ln(SECTION_GAP)

    # === LEADERSHIP (optional) ===
    if tailored.include_leadership and master.get("leadership"):
        pdf._section_header("Leadership")
        for item in master["leadership"]:
            pdf._company_line(item["org"], item["location"])
            pdf._role_line(item["title"], item["dates"])
            for bullet in item.get("bullets", []):
                pdf._bullet_point(bullet["text"])
        pdf.ln(SECTION_GAP)

    # === EDUCATION ===
    pdf._section_header("Education")
    for edu in master["education"]:
        pdf._company_line(edu["school"], edu["location"])

        degree_text = edu["degree"]
        if edu.get("concentration"):
            degree_text += f"; Concentration: {edu['concentration']}"

        if tailored.education_date_variant == "alt" and edu.get("alt_date"):
            date_text = edu["alt_date"]
        else:
            date_text = edu["date"]

        pdf._role_line(degree_text, date_text)

        # Coursework
        cw_key = tailored.education_coursework
        coursework = edu.get("coursework", {}).get(cw_key, "")
        if coursework:
            x = MARGIN_LEFT + BULLET_INDENT
            y = pdf.get_y()
            pdf._draw_bullet_dot(x, y, BULLET_HEIGHT)
            text_x = x + BULLET_TEXT_OFFSET
            pdf.set_xy(text_x, y)
            pdf.set_font("Times", "I", BULLET_SIZE)
            cw_label = "Relevant Coursework: "
            pdf.cell(pdf.get_string_width(cw_label), BULLET_HEIGHT, cw_label)
            pdf.set_font("Times", "", BULLET_SIZE)
            cw_w = CONTENT_W - BULLET_INDENT - BULLET_TEXT_OFFSET - pdf.get_string_width(cw_label)
            pdf.multi_cell(cw_w, BULLET_HEIGHT, coursework)

        # Honors
        if tailored.include_honors and edu.get("honors"):
            x = MARGIN_LEFT + BULLET_INDENT
            y = pdf.get_y()
            pdf._draw_bullet_dot(x, y, BULLET_HEIGHT)
            text_x = x + BULLET_TEXT_OFFSET
            pdf.set_xy(text_x, y)
            pdf.set_font("Times", "I", BULLET_SIZE)
            honors_label = "Honors: "
            pdf.cell(pdf.get_string_width(honors_label), BULLET_HEIGHT, honors_label)
            pdf.set_font("Times", "", BULLET_SIZE)
            honors_text = "; ".join(edu["honors"])
            honors_w = CONTENT_W - BULLET_INDENT - BULLET_TEXT_OFFSET - pdf.get_string_width(honors_label)
            pdf.multi_cell(honors_w, BULLET_HEIGHT, honors_text)

    pdf.ln(SECTION_GAP)

    # === SKILLS & INTERESTS ===
    pdf._section_header("Technical Skills" if tailored.include_certifications else "Skills & Interests")

    certs_rendered = False
    rendered_categories = set()
    for cat_key in tailored.skills_order:
        if cat_key in rendered_categories:
            continue
        if cat_key == "certifications":
            if tailored.include_certifications and tailored.certifications_selected:
                label = tailored.skill_line_labels.get("certifications", "Certifications")
                pdf._skills_line(label, tailored.certifications_selected)
                certs_rendered = True
            rendered_categories.add(cat_key)
            continue
        skills = tailored.skills_selected.get(cat_key, [])
        if not skills:
            continue
        label = tailored.skill_line_labels.get(cat_key, cat_key.replace("_", " ").title())
        pdf._skills_line(label, skills)
        rendered_categories.add(cat_key)

    if not certs_rendered and tailored.include_certifications and tailored.certifications_selected:
        label = tailored.skill_line_labels.get("certifications", "Certifications")
        pdf._skills_line(label, tailored.certifications_selected)

    final_y = pdf.get_y()
    return pdf, final_y


def _condense_tailored(tailored: TailoredResume, level: int) -> TailoredResume:
    """Return a progressively condensed copy of the tailored resume.

    Condensing levels (cumulative):
        1: Drop leadership, switch coursework to "short"
        2: Drop project section, drop honors
        3: Trim last bullet from the role with the most bullets
        4: Trim another bullet from the next longest role
        5: Drop certifications
    """
    t = copy.deepcopy(tailored)

    if level >= 1:
        t.include_leadership = False
        t.education_coursework = "short"

    if level >= 2:
        t.project = {}
        t.include_honors = False

    if level >= 3:
        # Find the role with the most resolved_bullets and trim one
        _trim_longest_role(t)

    if level >= 4:
        _trim_longest_role(t)

    if level >= 5:
        t.include_certifications = False
        t.certifications_selected = []

    return t


def _trim_longest_role(tailored: TailoredResume):
    """Remove the last bullet from the role with the most bullets."""
    longest_role = None
    max_bullets = 0
    for exp in tailored.experience:
        for role in exp.get("roles", []):
            bullets = role.get("resolved_bullets", [])
            if len(bullets) > max_bullets:
                max_bullets = len(bullets)
                longest_role = role
    if longest_role and max_bullets > 1:
        longest_role["resolved_bullets"].pop()


def generate_resume_pdf(
    tailored: TailoredResume,
    master: dict,
    output_dir: Path | str,
) -> Path:
    """Generate a one-page PDF resume with automatic overflow handling.

    If the initial render exceeds one page, progressively condenses content
    (drops optional sections, trims bullets) and retries up to 5 times.

    Args:
        tailored: AI-tailored resume structure.
        master: Full master resume dict (for contact info, education, etc.).
        output_dir: Directory to write the PDF.

    Returns:
        Path to the generated PDF file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    MAX_CONDENSE_LEVELS = 5

    # Try rendering, condense if overflow
    pdf, final_y = _render_resume_pdf(tailored, master)

    if final_y > MAX_Y:
        overflow_mm = final_y - MAX_Y
        logger.warning(
            f"Resume for {tailored.company_name}/{tailored.job_title} "
            f"overflows by {overflow_mm:.1f}mm — condensing..."
        )

        for level in range(1, MAX_CONDENSE_LEVELS + 1):
            condensed = _condense_tailored(tailored, level)
            pdf, final_y = _render_resume_pdf(condensed, master)

            if final_y <= MAX_Y:
                logger.info(f"  Fit after condense level {level} (y={final_y:.1f}mm)")
                break
            else:
                logger.debug(f"  Level {level}: still overflows by {final_y - MAX_Y:.1f}mm")
        else:
            logger.warning(
                f"  Resume still overflows after max condensing "
                f"(y={final_y:.1f}mm, limit={MAX_Y:.1f}mm) — saving anyway"
            )
    else:
        remaining = MAX_Y - final_y
        logger.debug(f"Resume fits with {remaining:.1f}mm to spare (y={final_y:.1f}mm)")

    # === Generate filename ===
    company_clean = re.sub(r'[^\w\s-]', '', tailored.company_name).strip().replace(' ', '_')
    title_clean = re.sub(r'[^\w\s-]', '', tailored.job_title).strip().replace(' ', '_')[:30]
    timestamp = datetime.now().strftime("%Y%m%d")
    filename = f"{resume_filename_prefix(master)}_{company_clean}_{title_clean}_{timestamp}.pdf"

    filepath = output_dir / filename
    pdf.output(str(filepath))

    logger.info(f"Resume PDF generated: {filepath}")
    return filepath
