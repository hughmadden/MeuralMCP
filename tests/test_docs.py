import unittest
from pathlib import Path


class DocumentationConsistencyTests(unittest.TestCase):
    def test_packaged_agent_docs_match_source_docs(self):
        root = Path(__file__).parents[1]
        self.assertEqual(
            (root / "docs/agents.md").read_text(),
            (root / "src/meural_mcp/data/agents.md").read_text(),
        )
        self.assertEqual(
            (root / "llms.txt").read_text(),
            (root / "src/meural_mcp/data/llms.txt").read_text(),
        )

    def test_agent_docs_cover_current_features_and_status(self):
        root = Path(__file__).parents[1]
        text = (root / "docs/agents.md").read_text()
        for name in (
            "get_device_time", "list_device_galleries", "create_device_gallery",
            "upload_device_gallery_item", "show_device_gallery", "show_device_item",
            "delete_device_gallery", "delete_device_item", "set_device_picture",
            "get_device_settings", "get_device_settings_schema", "update_device_settings",
            "run_device_action",
        ):
            self.assertIn(name, text)
        self.assertIn("GET    /status                        -> device counts", text)
        self.assertNotIn("service and device summary", text)
        self.assertNotIn("/preview", text)
        self.assertNotIn("service-install", text)


if __name__ == "__main__":
    unittest.main()
