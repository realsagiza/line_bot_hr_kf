from flask import Blueprint, render_template
from db import requests_collection  # ✅ ใช้ connection pool

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
    """ อนุมัติคำขอและอัปเดตสถานะใน MongoDB """
    requests_collection.update_one({"request_id": request_id}, {"$set": {"status": "approved"}})
    return redirect("/approved-requests")

@approved_requests_bp.route("/reject/<request_id>", methods=["POST"])
def reject_request(request_id):
    """ ปฏิเสธคำขอและอัปเดตสถานะใน MongoDB """
    requests_collection.update_one({"request_id": request_id}, {"$set": {"status": "rejected"}})
    return redirect("/approved-requests")