import uuid
from typing import Dict, Tuple, Optional
from config import Config


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


def get_rest_api_ci_base_for_branch(branch_id: Optional[str]) -> str:
    """
    Resolve REST_API_CI base URL per branch when overrides are configured.
    Falls back to Config.REST_API_CI_BASE.
    """
    b = (branch_id or "").strip().lower()
    if b in ("noniko", "branch_noniko"):
        return Config.REST_API_CI_BASE_NONIKO or Config.REST_API_CI_BASE
    if b in ("klangfrozen", "klanfrozen", "cold_storage", "coldstorage"):
        return Config.REST_API_CI_BASE_KLANGFROZEN or Config.REST_API_CI_BASE
    return Config.REST_API_CI_BASE


