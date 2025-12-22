from datetime import datetime, timezone, timedelta
from typing import Any, Optional

# Timezone สำหรับกรุงเทพ (+7) ตาม requirement
BANGKOK_TZ = timezone(timedelta(hours=7))


def now_bangkok():
    """
    คืนค่า datetime timezone-aware ของกรุงเทพ
    """
    return datetime.now(BANGKOK_TZ)


def now_bangkok_and_utc():
    """
    คืนค่า (now_bkk, now_utc) เป็น timezone-aware ทั้งคู่
    """
    bkk = now_bangkok()
    utc = bkk.astimezone(timezone.utc)
    return bkk, utc


def _parse_datetime(value: Any, *, assume_tz: timezone) -> Optional[datetime]:
    """
    Parse datetime from supported inputs:
    - datetime (aware/naive)
    - ISO string (datetime or date)

    If parsed datetime is naive, attach assume_tz.
    Returns None if cannot parse.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
        return dt if dt.tzinfo else dt.replace(tzinfo=assume_tz)

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            # Handle Zulu suffix
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=assume_tz)
        except ValueError:
            # Might be date-only "YYYY-MM-DD"
            try:
                d = datetime.fromisoformat(s + "T00:00:00")
                return d.replace(tzinfo=assume_tz)
            except Exception:
                return None

    return None


def format_bkk_datetime_display(value: Any) -> str:
    """
    Format given datetime-ish value into Bangkok time as 'YYYY-MM-DD HH:MM'.
    Returns empty string if value is missing/unparseable.
    """
    dt = _parse_datetime(value, assume_tz=BANGKOK_TZ)
    if not dt:
        return ""
    bkk = dt.astimezone(BANGKOK_TZ)
    return bkk.strftime("%Y-%m-%d %H:%M")


