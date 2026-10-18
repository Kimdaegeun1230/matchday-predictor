import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from football_predictor.web.build import build_site


class BuildTest(unittest.TestCase):
    def test_static_site_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "site"
            summary = build_site(out, source="sample", domain="example.com", today=date(2026, 10, 18))

            self.assertEqual((out / "CNAME").read_text().strip(), "example.com")
            self.assertTrue((out / ".nojekyll").exists())
            html = (out / "index.html").read_text(encoding="utf-8")
            self.assertIn("window.MP_STATIC = true", html)
            self.assertNotIn("<!--MP_STATIC-->", html)

            calendar = json.loads((out / "data" / "EPL" / "calendar.json").read_text(encoding="utf-8"))
            self.assertEqual(len(calendar), summary["leagues"]["EPL"]["dates"])
            for day in calendar:
                report = json.loads((out / "data" / "EPL" / "predictions" / f"{day['date']}.json")
                                    .read_text(encoding="utf-8"))
                self.assertEqual(len(report["predictions"]), day["matches"])

    def test_no_domain_no_cname(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "site"
            build_site(out, source="sample", today=date(2026, 10, 18))
            self.assertFalse((out / "CNAME").exists())


if __name__ == "__main__":
    unittest.main()
