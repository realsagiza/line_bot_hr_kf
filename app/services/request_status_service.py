from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple

from time_utils import format_bkk_datetime_display


def enrich_request_status_records(
    *,
    approved_requests: List[Dict[str, Any]],
    rejected_requests: List[Dict[str, Any]],
    deposit_requests: List[Dict[str, Any]],
    deposit_transactions: List[Dict[str, Any]],
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    """
    Pure transformation: copy records and add display fields for Bangkok day+time.

    Adds:
    - withdraw items (approved/rejected): created_at_bkk_display
    - new deposit requests: created_at_bkk_display
    - legacy deposit transactions: transaction_at_bkk_display
    """

    approved_out = deepcopy(approved_requests)
    rejected_out = deepcopy(rejected_requests)
    deposit_req_out = deepcopy(deposit_requests)
    deposit_tx_out = deepcopy(deposit_transactions)

    for r in approved_out:
        r["created_at_bkk_display"] = format_bkk_datetime_display(
            r.get("created_at_bkk") or r.get("created_date_bkk")
        )

    for r in rejected_out:
        r["created_at_bkk_display"] = format_bkk_datetime_display(
            r.get("created_at_bkk") or r.get("created_date_bkk")
        )

    for dr in deposit_req_out:
        dr["created_at_bkk_display"] = format_bkk_datetime_display(
            dr.get("created_at_bkk") or dr.get("created_date_bkk")
        )

    for tx in deposit_tx_out:
        tx["transaction_at_bkk_display"] = format_bkk_datetime_display(
            tx.get("transaction_at_bkk")
            or tx.get("transaction_date_bkk")
            or tx.get("selectedDate")
        )

    return approved_out, rejected_out, deposit_req_out, deposit_tx_out


