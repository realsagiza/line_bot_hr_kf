import os
import unittest


class TestMoneyLiffTemplate(unittest.TestCase):
    def test_money_liff_has_no_merge_markers(self):
        repo_root = os.path.dirname(__file__)
        path = os.path.join(repo_root, "app", "templates", "money_liff.html")
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()

        self.assertNotIn("<<<<<<<", html)
        self.assertNotIn("=======", html)
        self.assertNotIn(">>>>>>>", html)
        # ensure we use proxy endpoint to avoid CORS
        self.assertIn("/money/api/socket-latest-proxy", html)


if __name__ == "__main__":
    unittest.main()


