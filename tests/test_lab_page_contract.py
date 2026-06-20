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
        self.assertIn("configWriteWithGcj02Start", hook_text)
        self.assertIn("setBoatToStart", hook_text)
        self.assertIn("boatRef", hook_text)
        self.assertIn("max={20}", page_text)
        self.assertIn("速度上限", page_text)
        self.assertIn("droplet_count", page_text)
        self.assertIn("droplet_count_range", page_text)
        self.assertIn("采样模式", page_text)
        self.assertIn("走航 Survey", page_text)
        self.assertIn("液滴信号噪声", page_text)
        self.assertIn("多分析物 / 污染源基础配置", page_text)
        self.assertIn("/api/lab/route/auto-scan", page_text)
        self.assertIn("预览扫描", page_text)
        self.assertIn("应用扫描", page_text)
        self.assertIn("setPreviewRoute", hook_text)
        self.assertIn("previewLayerRef", hook_text)
        self.assertIn("usv-start-icon", css_text)
        self.assertIn("usv-boat-glyph", css_text)
        self.assertIn("usv-draft-waypoint-icon", css_text)
        self.assertIn("transition: transform", css_text)
        self.assertNotRegex(
            css_text,
            r"\.usv-boat-icon\s*\{[^}]*transition\s*:\s*transform",
        )

    def test_lab_page_exposes_sampling_progress_duration_and_virtual_signal(self):
        page_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Lab.tsx").read_text(
            encoding="utf-8"
        )
        types_text = (REPO_ROOT / "frontend" / "src" / "lib" / "lab-types.ts").read_text(
            encoding="utf-8"
        )

        self.assertIn("sampling.progress_percent", page_text)
        self.assertIn("sampling.duration_s", page_text)
        self.assertIn("signal.pollution_value", page_text)
        self.assertIn("虚拟信号", page_text)
        self.assertIn("采样进度", page_text)
        self.assertIn("duration_s", types_text)
        self.assertIn("pollution_value", types_text)


if __name__ == "__main__":
    unittest.main()
