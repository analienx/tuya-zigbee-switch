"""Regression tests for the documentation integrity contract (GitHub-hosted only)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
import check_docs_quality as dq


class DocsQualityTests(unittest.TestCase):
    def test_github_slug(self):
        self.assertEqual(dq.github_slug("Current sealed image (2026)"), "current-sealed-image-2026")
        self.assertEqual(dq.github_slug("What the states mean"), "what-the-states-mean")

    def test_relative_link_and_heading(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "index.md").write_text("[Read](topic.md#topic-name)\n", encoding="utf-8")
            (root / "topic.md").write_text("# Topic Name\n", encoding="utf-8")
            self.assertEqual(dq.check_links(root, ("index.md",)), [])

    def test_missing_relative_link_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "index.md").write_text("[Oops](missing.md)\n", encoding="utf-8")
            self.assertIn("broken relative link", "\n".join(dq.check_links(root, ("index.md",))))

    def test_missing_heading_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "index.md").write_text("[Read](topic.md#wrong)\n", encoding="utf-8")
            (root / "topic.md").write_text("# Topic\n", encoding="utf-8")
            self.assertIn("unresolved heading", "\n".join(dq.check_links(root, ("index.md",))))

    def test_fenced_external_links_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "index.md").write_text(
                "```md\n[Bad](missing.md)\n```\n[Web](https://example.com/a)\n",
                encoding="utf-8",
            )
            self.assertEqual(dq.check_links(root, ("index.md",)), [])

    def test_source_literal_and_keyword_dict(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "roles.py").write_text(
                "ROUTER = {'build': 'x', 'version': 12, 'type': 345}\n"
                "CLIENT = dict(build='y', version=12, type=678)\n", encoding="utf-8"
            )
            self.assertEqual(dq.source_candidate(root, "roles.py", "ROUTER")["build"], "x")
            self.assertEqual(dq.source_candidate(root, "roles.py", "CLIENT")["type"], 678)

    def test_dynamic_candidate_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "roles.py").write_text("CLIENT = get_remote_value()\n", encoding="utf-8")
            with self.assertRaises(dq.DocsError):
                dq.source_candidate(root, "roles.py", "CLIENT")

    def test_public_index_rejects_client(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            index = root / "zigbee2mqtt/ota/index_bseed.json"
            index.parent.mkdir(parents=True)
            entries = [
                {"imageType": key, "fileVersion": version, "manufacturerName": [manufacturer]}
                for key, (version, manufacturer) in dq.PUBLISHED.items()
            ]
            entries += [
                {"imageType": 54179, "fileVersion": 0xFFFFFFFF, "manufacturerName": [name]}
                for name in ("_TZ3000_b28wrpvx", "_TZ3000_o1jzcxou", "_TZ3002_iedhxgyi")
            ]
            index.write_text(json.dumps(entries), encoding="utf-8")
            self.assertEqual(dq.check_public_ota_index(root), [])
            entries[0]["imageType"] = 65024
            index.write_text(json.dumps(entries), encoding="utf-8")
            self.assertNotEqual(dq.check_public_ota_index(root), [])

    def test_repository_current_state(self):
        self.assertEqual(dq.check(), [], "Current main-facing docs must match candidate definitions and OTA index")


if __name__ == "__main__":
    unittest.main()
