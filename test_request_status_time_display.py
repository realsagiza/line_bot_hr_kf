import os
import sys
import unittest

# Add app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from time_utils import format_bkk_datetime_display
from services.request_status_service import enrich_request_status_records


class TestTimeDisplayUtils(unittest.TestCase):
    def test_format_bkk_datetime_display_with_offset(self):
        self.assertEqual(
            format_bkk_datetime_display("2024-01-15T10:30:00+07:00"),
            "2024-01-15 10:30",
        )

    def test_format_bkk_datetime_display_with_utc_z(self):
        # 03:30 UTC == 10:30 Bangkok
        self.assertEqual(
            format_bkk_datetime_display("2024-01-15T03:30:00Z"),
            "2024-01-15 10:30",
        )

    def test_format_bkk_datetime_display_date_only(self):
        self.assertEqual(format_bkk_datetime_display("2024-01-15"), "2024-01-15 00:00")

    def test_format_bkk_datetime_display_empty(self):
        self.assertEqual(format_bkk_datetime_display(None), "")
        self.assertEqual(format_bkk_datetime_display(""), "")


class TestRequestStatusService(unittest.TestCase):
    def test_enrich_request_status_records_adds_display_fields(self):
        approved = [{"request_id": "r1", "created_at_bkk": "2024-01-15T10:30:00+07:00"}]
        rejected = [{"request_id": "r2", "created_date_bkk": "2024-01-15"}]
        deposit_requests = [{"deposit_request_id": "d1", "created_at_bkk": "2024-01-15T09:05:00+07:00"}]
        deposit_transactions = [{"name": "x", "transaction_at_bkk": "2024-01-15T08:01:00+07:00"}]

        a, r, dr, tx = enrich_request_status_records(
            approved_requests=approved,
            rejected_requests=rejected,
            deposit_requests=deposit_requests,
            deposit_transactions=deposit_transactions,
        )

        self.assertEqual(a[0]["created_at_bkk_display"], "2024-01-15 10:30")
        self.assertEqual(r[0]["created_at_bkk_display"], "2024-01-15 00:00")
        self.assertEqual(dr[0]["created_at_bkk_display"], "2024-01-15 09:05")
        self.assertEqual(tx[0]["transaction_at_bkk_display"], "2024-01-15 08:01")

        # Ensure inputs not mutated (pure)
        self.assertNotIn("created_at_bkk_display", approved[0])
        self.assertNotIn("transaction_at_bkk_display", deposit_transactions[0])


if __name__ == "__main__":
    unittest.main()


