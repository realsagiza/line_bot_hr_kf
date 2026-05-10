from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple

from time_utils import format_bkk_datetime_display


def enrich_request_status_records(
    *,
    approved_requests: List[Dict[str, Any]],
    rejected_requests: List[Dict[str, Any]],
    deposit_requests: List[Dict[str, Any]],
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    """
    Pure transformation: copy records and add display fields for Bangkok day+time.

    Adds:
    - withdraw items (approved/rejected): created_at_bkk_display
    - new deposit requests: created_at_bkk_display
    (legacy deposit transactions removed)
    """

    approved_out = deepcopy(approved_requests)
    rejected_out = deepcopy(rejected_requests)
    deposit_req_out = deepcopy(deposit_requests)

    for r in approved_out:
        r["created_at_bkk_display"] = format_bkk_datetime_display(
            r.get("created_at_bkk") or r.get("created_date_bkk")
        )
        # ดึงชื่อผู้อนุมัติจาก status_history ล่าสุดที่ status=approved
        if r.get("status_history"):
            for sh in reversed(r["status_history"]):
                if sh.get("status") == "approved":
                    r["approved_by"] = sh.get("by", "")
                    break
        if not r.get("approved_by"):
            r["approved_by"] = ""

    for r in rejected_out:
        r["created_at_bkk_display"] = format_bkk_datetime_display(
            r.get("created_at_bkk") or r.get("created_date_bkk")
        )
        # ดึงชื่อผู้อนุมัติจาก status_history ล่าสุดที่ status=rejected
        if r.get("status_history"):
            for sh in reversed(r["status_history"]):
                if sh.get("status") == "rejected":
                    r["rejected_by"] = sh.get("by", "")
                    break
        if not r.get("rejected_by"):
            r["rejected_by"] = ""

    for dr in deposit_req_out:
        dr["created_at_bkk_display"] = format_bkk_datetime_display(
            dr.get("created_at_bkk") or dr.get("created_date_bkk")
        )
    return approved_out, rejected_out, deposit_req_out


