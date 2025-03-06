import requests
import logging
import json
import requests
from flask import Blueprint, render_template, jsonify, redirect, url_for
from db import requests_collection  # ✅ ใช้ connection pool

# ✅ ตั้งค่า Logging ให้ใช้งานได้
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)  # ✅ แก้ไขให้ประกาศ logger ที่นี่

# สร้าง Blueprint สำหรับ Web UI อนุมัติ
approved_requests_bp = Blueprint("approved_requests", __name__, template_folder="templates")

@approved_requests_bp.route("/approved-requests", methods=["GET"])
def get_approved_requests():
    """ แสดงรายการที่รออนุมัติ """
    pending_requests = list(requests_collection.find({"status": "pending"}, {"_id": 0}))
    return render_template("approved_requests.html", requests=pending_requests)

@approved_requests_bp.route("/request-status", methods=["GET"])
def request_status():
    """ แสดงรายการคำขอที่อนุมัติและปฏิเสธแล้ว """
    approved_requests = list(requests_collection.find({"status": "approved"}, {"_id": 0}))
    rejected_requests = list(requests_collection.find({"status": "rejected"}, {"_id": 0}))

    return render_template(
        "request_status.html",
        approved_requests=approved_requests,
        rejected_requests=rejected_requests
    )


@approved_requests_bp.route("/approve/<request_id>", methods=["POST"])
def approve_request(request_id):
    """ อนุมัติคำขอ และส่ง API ถ้าจำเป็น """

    logger.info(f"📢 กำลังอนุมัติคำขอ: {request_id}")

    # ✅ ค้นหาคำขอจากฐานข้อมูล
    request_data = requests_collection.find_one({"request_id": request_id})

    if not request_data:
        logger.error(f"❌ ไม่พบคำขอ {request_id} ในระบบ")
        return jsonify({"status": "error", "message": f"ไม่พบคำขอ {request_id} ในระบบ"}), 404

    # ✅ ดึงค่าจำนวนเงิน และสถานที่
    amount = request_data.get("amount")
    location = request_data.get("location")

    if not amount or not location:
        logger.error("❌ ข้อมูลคำขอไม่สมบูรณ์")
        return jsonify({"status": "error", "message": "ข้อมูลคำขอไม่สมบูรณ์"}), 400

    # ✅ กรณีสถานที่รับเงินเป็น "โนนิโกะ"
    if location == "โนนิโกะ":
        api_url = "http://10.0.0.14:5050/api/withdraw"
        payload = {
            "amount": int(amount),  # ✅ แปลงเป็น int
            "machine_id": "line_bot_audit_kf",
            "branch_id": "NONIKO"
        }
        headers = {
            "Content-Type": "application/json"
        }

        logger.info(f"📤 กำลังส่ง API ไปยัง {api_url} ด้วย Payload: {payload}")

        try:
            response = requests.post(api_url, json=payload, headers=headers, timeout=3600)

            # ✅ Log response status และ body
            logger.info(f"📤 API Response Status: {response.status_code}")
            logger.info(f"📤 API Response Body: {response.text}")

            response.raise_for_status()  # ถ้า HTTP Status ไม่ใช่ 200 จะเกิด Exception

            response_data = response.json()
            if response_data.get("transaction_status") != "success":
                logger.error(f"❌ API ตอบกลับผิดพลาด: {response_data}")
                return jsonify({"status": "error", "message": f"API ตอบกลับผิดพลาด: {response_data}"}), 500
            else:
                # ✅ อัปเดตสถานะเป็น "approved" ในฐานข้อมูล
                requests_collection.update_one({"request_id": request_id}, {"$set": {"status": "approved"}})
                logger.info(f"✅ อนุมัติคำขอ {request_id} สำเร็จ")
                return redirect("/approved-requests")

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ API Error: {str(e)}")
            return jsonify({"status": "error", "message": f"API Error: {str(e)}"}), 500

@approved_requests_bp.route("/reject/<request_id>", methods=["POST"])
def reject_request(request_id):
    """ ปฏิเสธคำขอและอัปเดตสถานะใน MongoDB """
    requests_collection.update_one({"request_id": request_id}, {"$set": {"status": "rejected"}})
    return redirect("/approved-requests")