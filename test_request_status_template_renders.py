import os
import unittest

from jinja2 import Environment, FileSystemLoader, StrictUndefined


class TestRequestStatusTemplateRenders(unittest.TestCase):
    def test_request_status_renders_with_empty_lists(self):
        repo_root = os.path.dirname(__file__)
        templates_dir = os.path.join(repo_root, "app", "templates")
        env = Environment(
            loader=FileSystemLoader(templates_dir),
            undefined=StrictUndefined,  # fail fast if template references missing vars
            autoescape=True,
        )
        tpl = env.get_template("request_status.html")

        html = tpl.render(
            approved_requests=[],
            rejected_requests=[],
            deposit_transactions=[],
            deposit_requests=[],
            selected_date="2025-12-22",
            selected_branch="all",
        )

        self.assertIn("สถานะการถอน/ฝากเงิน", html)


if __name__ == "__main__":
    unittest.main()


