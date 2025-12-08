import uuid
from typing import Dict, Tuple, Optional


def build_correlation_headers(
    sale_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Create standardized correlation headers for outbound API calls so downstream
    services (e.g., REST_API_CI) can log and audit uniformly.

    Returns a tuple of:
      - headers: dict[str, str]
      - meta: dict[str, str] containing trace_id, request_id, sale_id for persistence
    """
    if trace_id is None:
        trace_id = f"t-{uuid.uuid4().hex[:8]}"
    if request_id is None:
        request_id = f"r-{uuid.uuid4().hex[:8]}"
    if sale_id is None:
        sale_id = f"s-{uuid.uuid4().hex[:8]}"

    headers = {
        "Content-Type": "application/json",
        "X-Trace-Id": trace_id,
        "X-Request-Id": request_id,
        "X-Sale-Id": str(sale_id),
        "X-Caller-Service": "line_bot_hr_kf",
    }
    return headers, {
        "trace_id": trace_id,
        "request_id": request_id,
        "sale_id": str(sale_id),
    }


