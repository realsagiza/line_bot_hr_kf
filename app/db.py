from pymongo import MongoClient

class Database:
    """ จัดการ Connection Pool ของ MongoDB """
    _client = None

    @classmethod
    def get_connection(cls):
        """ คืนค่า MongoClient ที่เชื่อมต่ออยู่ """
        if cls._client is None:
            cls._client = MongoClient("mongodb://mongo:27017/", maxPoolSize=50, minPoolSize=5)
        return cls._client

# สร้าง Database Instance
db_client = Database.get_connection()
db = db_client["line_bot"]
requests_collection = db["withdraw_requests"]