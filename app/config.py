import os
from dotenv import load_dotenv

# โหลดค่าจากไฟล์ .env
load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "your_secret_key")
    LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    REST_API_CI_BASE = os.getenv("REST_API_CI_BASE", "http://localhost:5000")
    # Optional per-branch overrides
    REST_API_CI_BASE_NONIKO = os.getenv("REST_API_CI_BASE_NONIKO")
    REST_API_CI_BASE_KLANGFROZEN = os.getenv("REST_API_CI_BASE_KLANGFROZEN")