import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class LabPageContractTest(unittest.TestCase):
    def test_lab_page_exposes_visual_start_boat_and_speed_contract(self):
        page_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Lab.tsx").read_text(
            encoding="utf-8"
        )
        hook_text = (REPO_ROOT / "frontend" / "src" / "hooks" / "use-lab-map.ts").read_text(
            encoding="utf-8"
        )
        css_text = (REPO_ROOT / "frontend" / "src" / "index.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("'start'", page_text)
        self.assertIn("放起点", page_text)
        self.assertIn("useLabMap", page_text)
        self.assertIn("setStartPosition", hook_text)
        self.assertIn("max={20}", page_text)
        self.assertIn("速度上限", page_text)
        self.assertIn("usv-start-icon", css_text)
        self.assertIn("usv-boat-glyph", css_text)
        self.assertIn("transition: transform", css_text)
        self.assertNotRegex(
            css_text,
            r"\.usv-boat-icon\s*\{[^}]*transition\s*:\s*transform",
        )


if __name__ == "__main__":
    unittest.main()
