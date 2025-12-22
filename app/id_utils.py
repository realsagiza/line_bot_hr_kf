import uuid


def generate_request_id() -> str:
    """สร้างหมายเลขคำขอที่เป็น Unique (ใช้ 8 ตัวอักษรแรกของ UUID)"""
    return str(uuid.uuid4())[:8]


