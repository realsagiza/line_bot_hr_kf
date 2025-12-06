from datetime import datetime, timezone, timedelta

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


