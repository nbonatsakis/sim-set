import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import tempfile
import unittest

from simsetlib.claudemd import (END, START, inject_section, remove_from_claude_md, remove_section,
                                render_section, update_claude_md)


class SectionTests(unittest.TestCase):
    def test_render_substitutes_set_id_and_keeps_markers(self):
        section = render_section("triton")
        self.assertTrue(section.startswith(START))
        self.assertTrue(section.rstrip().endswith(END))
        self.assertIn("`[triton]`", section)
        self.assertNotIn("{{SET_ID}}", section)

    def test_inject_appends_to_existing_text_once(self):
        section = f"{START}\nbody\n{END}"
        once = inject_section("# Project\n\nIntro.\n", section)
        self.assertEqual(once, f"# Project\n\nIntro.\n\n{START}\nbody\n{END}\n")
        self.assertEqual(inject_section(once, section), once)

    def test_inject_replaces_existing_block_in_place(self):
        old = f"# P\n\n{START}\nold\n{END}\n\n## After\n"
        new = inject_section(old, f"{START}\nnew\n{END}")
        self.assertEqual(new, f"# P\n\n{START}\nnew\n{END}\n\n## After\n")

    def test_inject_into_empty_text(self):
        self.assertEqual(inject_section("", f"{START}\nx\n{END}"), f"{START}\nx\n{END}\n")

    def test_remove_section_leaves_rest_untouched(self):
        text = f"# P\n\n{START}\nx\n{END}\n\n## After\n"
        self.assertEqual(remove_section(text), "# P\n\n## After\n")
        self.assertEqual(remove_section("# P\n"), "# P\n")


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_creates_claude_md_when_missing(self):
        path = update_claude_md(self.root, "triton")
        text = path.read_text()
        self.assertIn("[triton]", text)
        update_claude_md(self.root, "triton")
        self.assertEqual(text, path.read_text())

    def test_remove_from_claude_md(self):
        (self.root / "CLAUDE.md").write_text("# P\n")
        update_claude_md(self.root, "triton")
        self.assertTrue(remove_from_claude_md(self.root))
        self.assertEqual((self.root / "CLAUDE.md").read_text(), "# P\n")
        self.assertFalse(remove_from_claude_md(Path(tempfile.mkdtemp())))


if __name__ == "__main__":
    unittest.main()
