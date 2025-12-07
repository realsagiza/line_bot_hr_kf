from pymongo import MongoClient
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Database:
    """ จัดการ Connection Pool ของ MongoDB """
    _client = None

    @classmethod
    def get_connection(cls):
        """ คืนค่า MongoClient ที่เชื่อมต่ออยู่ """
        if cls._client is None:
            mongodb_uri = os.getenv("MONGODB_URI")
            cls._client = MongoClient(mongodb_uri, maxPoolSize=50, minPoolSize=5)
        return cls._client

# สร้าง Database Instance
db_client = Database.get_connection()
db = db_client["kf_hr"]  # ใช้ database ตามที่กำหนดใน .env

# คำขอเบิกเงินสด
requests_collection = db["withdraw_requests"]

# คำขอฝากเงินสด (log คำขอ + สถานะจากเครื่องฝากเงิน)
deposit_requests_collection = db["deposit_requests"]

# ธุรกรรมการเงินที่สำเร็จแล้ว (ทั้งถอน/ฝาก)
transactions_collection = db["transactions"]  # เพิ่ม collection สำหรับเก็บข้อมูลธุรกรรม