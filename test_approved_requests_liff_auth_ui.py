import os
import unittest


class TestApprovedRequestsLiffAuthUi(unittest.TestCase):
    def test_template_has_auth_loading_and_fallback(self):
        repo_root = os.path.dirname(__file__)
        path = os.path.join(repo_root, "app", "templates", "approved_requests.html")
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()

        # Loading container shown by default to avoid "stuck on checking permission"
        self.assertIn('id="auth-loading"', html)
        self.assertIn("กำลังตรวจสอบสิทธิ์ผู้ใช้งาน", html)

        # Must explicitly handle init failures
        self.assertIn("LIFF initialization failed", html)
        self.assertIn("ไม่สามารถตรวจสอบสิทธิ์ผู้ใช้งานได้", html)


if __name__ == "__main__":
    unittest.main()


