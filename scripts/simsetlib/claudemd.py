"""Marker-delimited simset section inside a project's CLAUDE.md."""
import re
from pathlib import Path

START = "<!-- simset:start -->"
END = "<!-- simset:end -->"
TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "references" / "claude-md-section.md"
BLOCK_RE = re.compile(re.escape(START) + r".*?" + re.escape(END) + r"\n?", re.DOTALL)
SEAM_RE = re.compile(r"\n*" + re.escape(START) + r".*?" + re.escape(END) + r"\n*", re.DOTALL)


class SectionError(Exception):
    pass


def render_section(set_id, template=None):
    text = TEMPLATE_PATH.read_text() if template is None else template
    return text.replace("{{SET_ID}}", set_id).rstrip("\n")


def inject_section(text, section):
    section = section.rstrip("\n")
    has_start, has_end = START in text, END in text
    if has_start != has_end:
        raise SectionError("CLAUDE.md has a simset start or end marker but not the other; fix it by hand before running simset again")
    if has_start and has_end:
        return BLOCK_RE.sub(lambda _: section + "\n", text, count=1)
    if not text.strip():
        return section + "\n"
    return text.rstrip("\n") + "\n\n" + section + "\n"


def remove_section(text):
    if START not in text:
        return text
    match = SEAM_RE.search(text)
    if match is None:
        return text
    before, after = text[:match.start()], text[match.end():]
    if not before:
        return after
    if not after:
        return before if before.endswith("\n") else before + "\n"
    return before.rstrip("\n") + "\n\n" + after


def update_claude_md(project_root, set_id):
    path = Path(project_root) / "CLAUDE.md"
    existing = path.read_text() if path.exists() else ""
    path.write_text(inject_section(existing, render_section(set_id)))
    return path


def remove_from_claude_md(project_root):
    path = Path(project_root) / "CLAUDE.md"
    if not path.exists() or START not in path.read_text():
        return False
    path.write_text(remove_section(path.read_text()))
    return True
