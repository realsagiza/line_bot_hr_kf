import os
from datetime import timezone

from bson.objectid import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient

from time_utils import BANGKOK_TZ


def get_db():
    """
    ใช้ค่า MONGODB_URI ตัวเดียวกับแอปหลัก แล้วคืนค่า db kf_hr
    """
    load_dotenv()
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        raise RuntimeError("MONGODB_URI not found in environment")
    client = MongoClient(mongodb_uri, maxPoolSize=10)
    return client["kf_hr"]


def backfill_created_dates():
    """
    เติม created_at_* / created_date_bkk และ status_history ให้ document เก่าใน
    collection withdraw_requests ที่ยังไม่มีฟิลด์ created_date_bkk
    """
    db = get_db()
    col = db["withdraw_requests"]

    cursor = col.find({"created_date_bkk": {"$exists": False}})
    total = 0
    updated = 0

    for doc in cursor:
        total += 1
        _id = doc["_id"]
        oid: ObjectId = _id if isinstance(_id, ObjectId) else ObjectId(str(_id))

        # เวลาเดิมจาก ObjectId เป็น UTC
        created_utc = oid.generation_time.replace(tzinfo=timezone.utc)
        created_bkk = created_utc.astimezone(BANGKOK_TZ)
        date_bkk = created_bkk.date().isoformat()

        status = doc.get("status", "unknown")

        set_fields = {
            "created_at_utc": created_utc.isoformat(),
            "created_at_bkk": created_bkk.isoformat(),
            "created_date_bkk": date_bkk,
        }

        update = {"$set": set_fields}

        # ถ้ายังไม่มี status_history ให้สร้างเริ่มต้นให้ด้วย
        if "status_history" not in doc:
            update["$set"]["status_history"] = [
                {
                    "status": status,
                    "at_bkk": created_bkk.isoformat(),
                    "at_utc": created_utc.isoformat(),
                    "date_bkk": date_bkk,
                    "by": "backfill_script",
                }
            ]

        result = col.update_one({"_id": _id}, update)
        if result.modified_count:
            updated += 1

    print(f"Found legacy documents without created_date_bkk: {total}")
    print(f"Updated documents: {updated}")


if __name__ == "__main__":
    backfill_created_dates()


