import uuid
import requests
import logging
import json
import requests
import threading
from flask import Blueprint, render_template, jsonify, redirect, url_for, request
from bson import json_util
from db import requests_collection, deposit_requests_collection, transactions_collection
from time_utils import now_bangkok, now_bangkok_and_utc
from http_utils import build_correlation_headers, get_rest_api_ci_base_for_branch
from services.request_status_service import enrich_request_status_records
from id_utils import generate_request_id

# ✅ ตั้งค่า Logging ให้ใช้งานได้
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)  # ✅ แก้ไขให้ประกาศ logger ที่นี่

# สร้าง Blueprint สำหรับ Web UI / LIFF เงิน
approved_requests_bp = Blueprint("approved_requests", __name__, template_folder="templates")

def _is_withdraw_success(response_json: dict) -> bool:
    """
    Accept both legacy shape {\"transaction_status\":\"success\"}
    and new REST_API_CI SOAP shape {\"response\": { Body:[{ CashoutResponse:[{ result:'0', ... }] }] } }.
    """
    try:
        if response_json.get("transaction_status") == "success":
            return True
    except Exception:
        pass
    try:
        resp = response_json.get("response") or {}
        body = (resp.get("Body") or [None])[0] or {}
        cashout = (body.get("CashoutResponse") or [None])[0] or {}
        result = cashout.get("result")
        return str(result) == "0"
    except Exception:
        return False

def save_expense_to_transactions(request_data, location, amount, reason, date_bkk, now_bkk, now_utc):
    """
    บันทึกค่าใช้จ่ายเงินสดลง transactions_collection สำหรับใช้ในระบบบัญชี
    ตามรูปแบบที่ใช้ใน klangfrozen (หน้า สรุปยอดเงินสิ้นวัน)
    
    Args:
        request_data: ข้อมูลคำขอเบิกเงิน
        location: สถานที่ (คลังห้องเย็น หรือ โนนิโกะ)
        amount: จำนวนเงิน
        reason: เหตุผลในการเบิกเงิน
        date_bkk: วันที่อนุมัติ (YYYY-MM-DD)
        now_bkk: datetime object เวลาไทย
        now_utc: datetime object UTC
    """
    try:
        # แปลง selectedStorage ตาม location
        selected_storage = location  # ใช้ location ตรงๆ (คลังห้องเย็น หรือ โนนิโกะ)
        
        # สร้าง tag "เบิกเงินผ่านไลน์"
        expense_tag = {
            "label": "เบิกเงินผ่านไลน์",
            "color": "primary",
            "bgColor": "#1976d2"
        }
        
        # สร้างข้อมูลค่าใช้จ่ายเงินสด
        expense_doc = {
            "type": "expense",
            "selectedStorage": selected_storage,
            "selectedDate": date_bkk,
            "name": reason,
            "amount": float(amount),
            "tags": [expense_tag],
            "receiptAttached": False,
            "request_id": request_data.get("request_id"),  # เชื่อมโยงกับคำขอเบิกเงิน
            "user_id": request_data.get("user_id"),
            "created_at_bkk": now_bkk.isoformat(),
            "created_at_utc": now_utc.isoformat(),
        }
        
        # บันทึกลง transactions_collection
        transactions_collection.insert_one(expense_doc)
        logger.info(f"✅ บันทึกค่าใช้จ่ายเงินสดลง transactions_collection สำเร็จ: request_id={request_data.get('request_id')}, location={selected_storage}, amount={amount}")
        
    except Exception as e:
        logger.error(f"❌ ไม่สามารถบันทึกค่าใช้จ่ายเงินสดลง transactions_collection ได้: {str(e)}")
        # ไม่ throw error เพราะการบันทึกค่าใช้จ่ายไม่ควรทำให้การอนุมัติล้มเหลว


@approved_requests_bp.route("/money/liff", methods=["GET"])
def money_liff_home():
    """
    หน้า LIFF หลักสำหรับจัดการเงิน:
    - ขอเบิกเงินสด
    - ดูสถานะคำขอ
    - ลิงก์ไปหน้ารออนุมัติ (สำหรับผู้อนุมัติ)
    """
    return render_template("money_liff.html")

@approved_requests_bp.route("/money/approved-requests", methods=["GET"])
def get_approved_requests():
    """ แสดงรายการที่รออนุมัติ (เรียงตามวันที่ล่าสุดก่อน) """
    pending_requests = list(
        requests_collection.find({"status": "pending"}, {"_id": 0}).sort("created_at_bkk", -1)
    )
    return render_template("approved_requests.html", requests=pending_requests)

@approved_requests_bp.route("/money/request-status", methods=["GET"])
def request_status():
    """
    แสดงสถานะคำขอ (สำเร็จ / ปฏิเสธ) แบบมีตัวกรอง:
    - วันที่ (default = วันนี้ ตามเวลาไทย)
    - สาขา (สถานที่รับเงิน) : ทั้งหมด / คลังห้องเย็น / โนนิโกะ
    """
    selected_date = request.args.get("date")
    selected_branch = request.args.get("branch", "all")

    # ถ้าไม่ส่งวันที่มา ให้ใช้วันที่ปัจจุบันตามเวลาไทย
    if not selected_date:
        selected_date = now_bangkok().date().isoformat()

    # คำขอเบิกเงิน (withdraw) จาก collection withdraw_requests
    query = {
        "status": {"$in": ["approved", "rejected", "awaiting_machine"]},
        "created_date_bkk": selected_date,
    }

    if selected_branch in ("คลังห้องเย็น", "โนนิโกะ"):
        query["location"] = selected_branch

    cursor = requests_collection.find(query, {"_id": 0}).sort("created_at_bkk", -1)
    all_requests = list(cursor)

    approved_requests = [r for r in all_requests if r.get("status") == "approved"]
    rejected_requests = [r for r in all_requests if r.get("status") == "rejected"]

    # ข้อมูลฝากเงิน (deposit) จาก collection deposit_requests (ระบบใหม่ - replenishment)
    # แสดงเฉพาะรายการที่เสร็จสิ้นแล้ว (status = "completed")
    deposit_requests_query = {
        "created_date_bkk": selected_date,
        "status": "completed",
    }
    if selected_branch in ("คลังห้องเย็น", "โนนิโกะ"):
        deposit_requests_query["location"] = selected_branch

    deposit_requests_cursor = deposit_requests_collection.find(
        deposit_requests_query, {"_id": 0}
    ).sort("created_at_bkk", -1)
    deposit_requests = list(deposit_requests_cursor)

    approved_requests, rejected_requests, deposit_requests = enrich_request_status_records(
        approved_requests=approved_requests,
        rejected_requests=rejected_requests,
        deposit_requests=deposit_requests,
    )

    return render_template(
        "request_status.html",
        approved_requests=approved_requests,
        rejected_requests=rejected_requests,
        deposit_requests=deposit_requests,
        selected_date=selected_date,
        selected_branch=selected_branch,
    )


@approved_requests_bp.route("/money/approve/<request_id>", methods=["POST"])
def approve_request(request_id):
    """ อนุมัติคำขอ และส่ง API ถ้าจำเป็น """

    logger.info(f"📢 กำลังอนุมัติคำขอ: {request_id}")

    # ✅ รับชื่อผู้อนุมัติจากฟอร์ม
    approver_name = request.form.get("approver_name", "").strip() or "approver_ui"

    # ✅ ค้นหาคำขอจากฐานข้อมูล
    request_data = requests_collection.find_one({"request_id": request_id})

    if not request_data:
        logger.error(f"❌ ไม่พบคำขอ {request_id} ในระบบ")
        return jsonify({"status": "error", "message": f"ไม่พบคำขอ {request_id} ในระบบ"}), 404

    # ✅ ดึงค่าจำนวนเงิน และสถานที่
    amount = request_data.get("amount")
    location = request_data.get("location")
    reason = request_data.get("reason", "")  # ✅ ดึงข้อมูลเหตุผลจากคำขอ
    current_status = request_data.get("status")

    if not amount or not location:
        logger.error("❌ ข้อมูลคำขอไม่สมบูรณ์")
        return jsonify({"status": "error", "message": "ข้อมูลคำขอไม่สมบูรณ์"}), 400

    # ✅ อนุญาตให้อนุมัติได้เฉพาะสถานะ pending เท่านั้น ป้องกันการกดย้ำ
    if current_status != "pending":
        logger.warning(f"⚠️ คำขอ {request_id} มีสถานะ {current_status} อยู่แล้ว ข้ามการยิง API ซ้ำ")
        return redirect("/money/approved-requests")

    # ✅ ตั้งสถานะเป็น "รอการตอบรับจากเครื่องถอนเงิน" ทันทีที่กดอนุมัติ
    now_bkk, now_utc = now_bangkok_and_utc()
    date_bkk = now_bkk.date().isoformat()
    try:
        requests_collection.update_one(
            {"request_id": request_id},
            {
                "$set": {
                    "status": "awaiting_machine",
                    "updated_at_bkk": now_bkk.isoformat(),
                    "updated_at_utc": now_utc.isoformat(),
                },
                "$push": {
                    "status_history": {
                        "status": "awaiting_machine",
                        "at_bkk": now_bkk.isoformat(),
                        "at_utc": now_utc.isoformat(),
                        "date_bkk": date_bkk,
                        "by": approver_name,
                    }
                },
            },
        )
        logger.info(f"⏳ ตั้งสถานะคำขอ {request_id} เป็น awaiting_machine แล้ว")
    except Exception as e:
        logger.error(f"❌ อัปเดตสถานะ awaiting_machine ไม่สำเร็จ: {str(e)}")
        return jsonify({"status": "error", "message": "อัปเดตสถานะไม่สำเร็จ"}), 500

    # ✅ กรณีสถานที่รับเงินเป็น "โนนิโกะ"
    if location == "โนนิโกะ":
        base = get_rest_api_ci_base_for_branch("NONIKO")
        headers, meta = build_correlation_headers(sale_id=request_id)
        trace_id = meta["trace_id"]
        request_header_id = meta["request_id"]

        try:
            # Step 1: ยิง API /cashout/plan เพื่อคำนวณ denominations
            plan_url = f"{base}/cashout/plan"
            plan_payload = {
                "amount": float(amount)  # แปลงเป็น float ตามที่ API ต้องการ
            }
            
            logger.info(f"📤 [CASHOUT] กำลังส่ง API ไปยัง {plan_url} ด้วย Payload: {plan_payload}")
            
            plan_response = requests.post(plan_url, json=plan_payload, headers=headers, timeout=10)
            plan_response.raise_for_status()
            plan_data = plan_response.json()
            
            if not plan_data.get("success"):
                error_msg = plan_data.get("error", "Unknown error from /cashout/plan")
                logger.error(f"❌ [CASHOUT] /cashout/plan failed: {error_msg}")
                now_bkk, now_utc = now_bangkok_and_utc()
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "status": "error",
                            "machine_error": f"/cashout/plan failed: {error_msg}",
                            "updated_at_bkk": now_bkk.isoformat(),
                            "updated_at_utc": now_utc.isoformat(),
                        },
                        "$push": {
                            "status_history": {
                                "status": "error",
                                "at_bkk": now_bkk.isoformat(),
                                "at_utc": now_utc.isoformat(),
                                "date_bkk": now_bkk.date().isoformat(),
                                "by": approver_name,
                            }
                        },
                    },
                )
                return jsonify({"status": "error", "message": f"/cashout/plan failed: {error_msg}"}), 500
            
            # Step 2: รับ denominations จาก response
            denominations = plan_data.get("denominations")
            if not denominations:
                logger.error(f"❌ [CASHOUT] ไม่พบ denominations ใน response จาก /cashout/plan")
                now_bkk, now_utc = now_bangkok_and_utc()
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "status": "error",
                            "machine_error": "ไม่พบ denominations ใน response",
                            "updated_at_bkk": now_bkk.isoformat(),
                            "updated_at_utc": now_utc.isoformat(),
                        },
                        "$push": {
                            "status_history": {
                                "status": "error",
                                "at_bkk": now_bkk.isoformat(),
                                "at_utc": now_utc.isoformat(),
                                "date_bkk": now_bkk.date().isoformat(),
                                "by": approver_name,
                            }
                        },
                    },
                )
                return jsonify({"status": "error", "message": "ไม่พบ denominations ใน response"}), 500
            
            logger.info(f"✅ [CASHOUT] ได้รับ denominations จาก /cashout/plan: {denominations}")
            
            # Step 3: ส่ง denominations ไปที่ /cashout/request
            request_url = f"{base}/cashout/request"
            request_payload = {
                "denominations": denominations
            }
            
            logger.info(f"📤 [CASHOUT] กำลังส่ง API ไปยัง {request_url} ด้วย Payload: {request_payload}")
            
            request_response = requests.post(request_url, json=request_payload, headers=headers, timeout=10)
            request_response.raise_for_status()
            request_data = request_response.json()
            
            if not request_data.get("success"):
                error_msg = request_data.get("error", "Unknown error from /cashout/request")
                logger.error(f"❌ [CASHOUT] /cashout/request failed: {error_msg}")
                now_bkk, now_utc = now_bangkok_and_utc()
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "status": "error",
                            "machine_error": f"/cashout/request failed: {error_msg}",
                            "updated_at_bkk": now_bkk.isoformat(),
                            "updated_at_utc": now_utc.isoformat(),
                        },
                        "$push": {
                            "status_history": {
                                "status": "error",
                                "at_bkk": now_bkk.isoformat(),
                                "at_utc": now_utc.isoformat(),
                                "date_bkk": now_bkk.date().isoformat(),
                                "by": approver_name,
                            }
                        },
                    },
                )
                return jsonify({"status": "error", "message": f"/cashout/request failed: {error_msg}"}), 500
            
            logger.info(f"✅ [CASHOUT] ส่ง /cashout/request สำเร็จ: {request_data}")
            
            # Step 4: อัปเดตสถานะเป็น approved (สำเร็จ)
            now_bkk, now_utc = now_bangkok_and_utc()
            date_bkk = now_bkk.date().isoformat()
            
            requests_collection.update_one(
                {"request_id": request_id},
                {
                    "$set": {
                        "status": "approved",
                        "updated_at_bkk": now_bkk.isoformat(),
                        "updated_at_utc": now_utc.isoformat(),
                        "denominations": denominations,
                        "cashout_plan_response": plan_data,
                        "cashout_request_response": request_data,
                    },
                    "$push": {
                        "status_history": {
                            "status": "approved",
                            "at_bkk": now_bkk.isoformat(),
                            "at_utc": now_utc.isoformat(),
                            "date_bkk": date_bkk,
                            "by": approver_name,
                        }
                    },
                },
            )
            
            # บันทึกค่าใช้จ่ายเงินสดลง transactions_collection สำหรับใช้ในระบบบัญชี
            save_expense_to_transactions(request_data, location, amount, reason, date_bkk, now_bkk, now_utc)
            logger.info(f"✅ อนุมัติคำขอ {request_id} - Cashout สำเร็จ")
            return redirect("/money/approved-requests")

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ [CASHOUT] Request Exception: {str(e)}")
            now_bkk, now_utc = now_bangkok_and_utc()
            requests_collection.update_one(
                {"request_id": request_id},
                {
                    "$set": {
                        "status": "error",
                        "machine_error": f"Request exception: {str(e)}",
                        "updated_at_bkk": now_bkk.isoformat(),
                        "updated_at_utc": now_utc.isoformat(),
                    },
                    "$push": {
                        "status_history": {
                            "status": "error",
                            "at_bkk": now_bkk.isoformat(),
                            "at_utc": now_utc.isoformat(),
                            "date_bkk": now_bkk.date().isoformat(),
                            "by": approver_name,
                        }
                    },
                },
            )
            return jsonify({"status": "error", "message": f"Request exception: {str(e)}"}), 500
        except Exception as e:
            logger.error(f"❌ [CASHOUT] Error: {str(e)}")
            now_bkk, now_utc = now_bangkok_and_utc()
            requests_collection.update_one(
                {"request_id": request_id},
                {
                    "$set": {
                        "status": "error",
                        "machine_error": str(e),
                        "updated_at_bkk": now_bkk.isoformat(),
                        "updated_at_utc": now_utc.isoformat(),
                    },
                    "$push": {
                        "status_history": {
                            "status": "error",
                            "at_bkk": now_bkk.isoformat(),
                            "at_utc": now_utc.isoformat(),
                            "date_bkk": now_bkk.date().isoformat(),
                            "by": approver_name,
                        }
                    },
                },
            )
            return jsonify({"status": "error", "message": str(e)}), 500
    elif location == "คลังห้องเย็น":
        base = get_rest_api_ci_base_for_branch("Klangfrozen")
        headers, meta = build_correlation_headers(sale_id=request_id)
        trace_id = meta["trace_id"]
        request_header_id = meta["request_id"]

        try:
            # Step 1: ยิง API /cashout/plan เพื่อคำนวณ denominations
            plan_url = f"{base}/cashout/plan"
            plan_payload = {
                "amount": float(amount)  # แปลงเป็น float ตามที่ API ต้องการ
            }
            
            logger.info(f"📤 [CASHOUT] กำลังส่ง API ไปยัง {plan_url} ด้วย Payload: {plan_payload}")
            
            plan_response = requests.post(plan_url, json=plan_payload, headers=headers, timeout=10)
            plan_response.raise_for_status()
            plan_data = plan_response.json()
            
            if not plan_data.get("success"):
                error_msg = plan_data.get("error", "Unknown error from /cashout/plan")
                logger.error(f"❌ [CASHOUT] /cashout/plan failed: {error_msg}")
                now_bkk, now_utc = now_bangkok_and_utc()
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "status": "error",
                            "machine_error": f"/cashout/plan failed: {error_msg}",
                            "updated_at_bkk": now_bkk.isoformat(),
                            "updated_at_utc": now_utc.isoformat(),
                        },
                        "$push": {
                            "status_history": {
                                "status": "error",
                                "at_bkk": now_bkk.isoformat(),
                                "at_utc": now_utc.isoformat(),
                                "date_bkk": now_bkk.date().isoformat(),
                                "by": approver_name,
                            }
                        },
                    },
                )
                return jsonify({"status": "error", "message": f"/cashout/plan failed: {error_msg}"}), 500
            
            # Step 2: รับ denominations จาก response
            denominations = plan_data.get("denominations")
            if not denominations:
                logger.error(f"❌ [CASHOUT] ไม่พบ denominations ใน response จาก /cashout/plan")
                now_bkk, now_utc = now_bangkok_and_utc()
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "status": "error",
                            "machine_error": "ไม่พบ denominations ใน response",
                            "updated_at_bkk": now_bkk.isoformat(),
                            "updated_at_utc": now_utc.isoformat(),
                        },
                        "$push": {
                            "status_history": {
                                "status": "error",
                                "at_bkk": now_bkk.isoformat(),
                                "at_utc": now_utc.isoformat(),
                                "date_bkk": now_bkk.date().isoformat(),
                                "by": approver_name,
                            }
                        },
                    },
                )
                return jsonify({"status": "error", "message": "ไม่พบ denominations ใน response"}), 500
            
            logger.info(f"✅ [CASHOUT] ได้รับ denominations จาก /cashout/plan: {denominations}")
            
            # Step 3: ส่ง denominations ไปที่ /cashout/request
            request_url = f"{base}/cashout/request"
            request_payload = {
                "denominations": denominations
            }
            
            logger.info(f"📤 [CASHOUT] กำลังส่ง API ไปยัง {request_url} ด้วย Payload: {request_payload}")
            
            request_response = requests.post(request_url, json=request_payload, headers=headers, timeout=10)
            request_response.raise_for_status()
            request_data = request_response.json()
            
            if not request_data.get("success"):
                error_msg = request_data.get("error", "Unknown error from /cashout/request")
                logger.error(f"❌ [CASHOUT] /cashout/request failed: {error_msg}")
                now_bkk, now_utc = now_bangkok_and_utc()
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "status": "error",
                            "machine_error": f"/cashout/request failed: {error_msg}",
                            "updated_at_bkk": now_bkk.isoformat(),
                            "updated_at_utc": now_utc.isoformat(),
                        },
                        "$push": {
                            "status_history": {
                                "status": "error",
                                "at_bkk": now_bkk.isoformat(),
                                "at_utc": now_utc.isoformat(),
                                "date_bkk": now_bkk.date().isoformat(),
                                "by": approver_name,
                            }
                        },
                    },
                )
                return jsonify({"status": "error", "message": f"/cashout/request failed: {error_msg}"}), 500
            
            logger.info(f"✅ [CASHOUT] ส่ง /cashout/request สำเร็จ: {request_data}")
            
            # Step 4: อัปเดตสถานะเป็น approved (สำเร็จ)
            now_bkk, now_utc = now_bangkok_and_utc()
            date_bkk = now_bkk.date().isoformat()
            
            requests_collection.update_one(
                {"request_id": request_id},
                {
                    "$set": {
                        "status": "approved",
                        "updated_at_bkk": now_bkk.isoformat(),
                        "updated_at_utc": now_utc.isoformat(),
                        "denominations": denominations,
                        "cashout_plan_response": plan_data,
                        "cashout_request_response": request_data,
                    },
                    "$push": {
                        "status_history": {
                            "status": "approved",
                            "at_bkk": now_bkk.isoformat(),
                            "at_utc": now_utc.isoformat(),
                            "date_bkk": date_bkk,
                            "by": approver_name,
                        }
                    },
                },
            )
            
            # บันทึกค่าใช้จ่ายเงินสดลง transactions_collection สำหรับใช้ในระบบบัญชี
            save_expense_to_transactions(request_data, location, amount, reason, date_bkk, now_bkk, now_utc)
            logger.info(f"✅ อนุมัติคำขอ {request_id} - Cashout สำเร็จ")
            return redirect("/money/approved-requests")

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ [CASHOUT] Request Exception: {str(e)}")
            now_bkk, now_utc = now_bangkok_and_utc()
            requests_collection.update_one(
                {"request_id": request_id},
                {
                    "$set": {
                        "status": "error",
                        "machine_error": f"Request exception: {str(e)}",
                        "updated_at_bkk": now_bkk.isoformat(),
                        "updated_at_utc": now_utc.isoformat(),
                    },
                    "$push": {
                        "status_history": {
                            "status": "error",
                            "at_bkk": now_bkk.isoformat(),
                            "at_utc": now_utc.isoformat(),
                            "date_bkk": now_bkk.date().isoformat(),
                            "by": approver_name,
                        }
                    },
                },
            )
            return jsonify({"status": "error", "message": f"Request exception: {str(e)}"}), 500
        except Exception as e:
            logger.error(f"❌ [CASHOUT] Error: {str(e)}")
            now_bkk, now_utc = now_bangkok_and_utc()
            requests_collection.update_one(
                {"request_id": request_id},
                {
                    "$set": {
                        "status": "error",
                        "machine_error": str(e),
                        "updated_at_bkk": now_bkk.isoformat(),
                        "updated_at_utc": now_utc.isoformat(),
                    },
                    "$push": {
                        "status_history": {
                            "status": "error",
                            "at_bkk": now_bkk.isoformat(),
                            "at_utc": now_utc.isoformat(),
                            "date_bkk": now_bkk.date().isoformat(),
                            "by": approver_name,
                        }
                    },
                },
            )
            return jsonify({"status": "error", "message": str(e)}), 500

@approved_requests_bp.route("/money/reject/<request_id>", methods=["POST"])
def reject_request(request_id):
    """ ปฏิเสธคำขอและอัปเดตสถานะใน MongoDB """
    approver_name = request.form.get("approver_name", "").strip() or "approver_ui"
    now_bkk, now_utc = now_bangkok_and_utc()
    date_bkk = now_bkk.date().isoformat()

    requests_collection.update_one(
        {"request_id": request_id},
        {
            "$set": {
                "status": "rejected",
                "updated_at_bkk": now_bkk.isoformat(),
                "updated_at_utc": now_utc.isoformat(),
            },
            "$push": {
                "status_history": {
                    "status": "rejected",
                    "at_bkk": now_bkk.isoformat(),
                    "at_utc": now_utc.isoformat(),
                    "date_bkk": date_bkk,
                    "by": approver_name,
                }
            },
        },
    )
    return redirect("/money/approved-requests")


# ═══════════════════════════════════════════════════════════════
#  ฟีเจอร์ใหม่: เบิกเงินแบบระบุ Denominations เอง (Manual)
# ═══════════════════════════════════════════════════════════════

# Mapping ค่าเงิน (สตางค์ → บาท) สำหรับแสดงผล
FV_TO_BAHT = {
    10000: 100,
    2000: 20,
    1000: 10,
    500: 5,
    200: 2,
    100: 1,
}

FV_LABELS = {
    10000: "แบงค์ 100 บาท",
    2000: "แบงค์ 20 บาท",
    1000: "เหรียญ 10 บาท",
    500: "เหรียญ 5 บาท",
    200: "เหรียญ 2 บาท",
    100: "เหรียญ 1 บาท",
}


def _parse_inventory_response(inventory_data: dict) -> dict:
    """
    แปลง response จาก /inventory/check ให้อยู่ในรูป {fv: available_pieces}
    โดย fv เป็นหน่วยสตางค์ (10000 = 100 บาท)
    """
    available = {}
    try:
        body = inventory_data.get("Body") or inventory_data.get("response", {}).get("Body") or []
        if not body:
            body = inventory_data.get("result", {}).get("Body", [])
        inv_response = body[0].get("InventoryResponse", [{}])[0]
        cash = inv_response.get("Cash", [{}])[0]
        for denom in cash.get("Denomination", []):
            fv = int(denom.get("fv", 0))
            pieces = int(denom.get("Piece", [{}])[0].get("value", 0)) if isinstance(denom.get("Piece"), list) else int(denom.get("Piece", 0))
            available[fv] = pieces
    except Exception as e:
        logger.warning(f"⚠️ [INVENTORY_PARSE] Failed to parse inventory: {e}")
    return available


def _denominations_to_baht_total(denominations: dict) -> float:
    """คำนวณยอดรวมจาก denominations (รับ key เป็นสตางค์)"""
    total = 0
    for fv, pieces in denominations.items():
        total += int(fv) * int(pieces)
    return total / 100.0


@approved_requests_bp.route("/money/inventory", methods=["GET"])
def get_inventory():
    """
    ดึงข้อมูล inventory (ยอดเงินในเครื่อง) จาก REST_API_CI
    ใช้สำหรับให้พนักงานดูก่อนเลือก denominations
    
    Query params:
        branch: "noniko" หรือ "klangfrozen" (default: noniko)
    
    Returns:
        JSON { success, inventory: { fv: pieces, ... }, total_baht, denominations_info }
    """
    branch = request.args.get("branch", "noniko").strip().lower()
    
    # Map branch to REST_API_CI base
    if branch in ("noniko",):
        base = get_rest_api_ci_base_for_branch("NONIKO")
    elif branch in ("klangfrozen", "cold_storage", "คลังห้องเย็น"):
        base = get_rest_api_ci_base_for_branch("Klangfrozen")
    else:
        return jsonify({"success": False, "error": f"ไม่พบสาขา: {branch}"}), 400
    
    headers, meta = build_correlation_headers(sale_id=f"inv-{uuid.uuid4().hex[:6]}")
    
    try:
        inv_url = f"{base}/inventory/check"
        logger.info(f"📤 [INVENTORY] กำลังดึง inventory จาก {inv_url}")
        
        resp = requests.get(inv_url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if not data.get("success"):
            logger.error(f"❌ [INVENTORY] API error: {data.get('error', 'unknown')}")
            return jsonify({"success": False, "error": data.get("error", "ไม่สามารถดึงข้อมูล inventory ได้")}), 500
        
        # Parse inventory
        inventory_raw = data.get("result", data)
        available = _parse_inventory_response(inventory_raw)
        
        # สร้างข้อมูล denominations สำหรับแสดงผล
        denom_info = []
        for fv in sorted(available.keys(), reverse=True):
            baht = FV_TO_BAHT.get(fv, fv // 100)
            label = FV_LABELS.get(fv, f"{baht} บาท")
            denom_info.append({
                "fv": fv,
                "baht": baht,
                "label": label,
                "available": available.get(fv, 0),
            })
        
        total_baht = sum(fv * pieces for fv, pieces in available.items()) / 100.0
        
        return jsonify({
            "success": True,
            "inventory": available,
            "total_baht": total_baht,
            "denominations": denom_info,
            "branch": branch,
        }), 200
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ [INVENTORY] Connection error: {e}")
        return jsonify({"success": False, "error": f"ไม่สามารถเชื่อมต่อเครื่องได้: {str(e)}"}), 502
    except Exception as e:
        logger.error(f"❌ [INVENTORY] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@approved_requests_bp.route("/money/submit-manual-withdraw", methods=["POST"])
def submit_manual_withdraw():
    """
    สร้างคำขอเบิกเงินแบบระบุ denominations เอง
    
    Request body (JSON):
    {
        "user_id": "LINE user id",
        "location": "คลังห้องเย็น" หรือ "โนนิโกะ",
        "reason": "ice" | "fuel" | "other" | string,
        "reason_text": "ข้อความเหตุผล (ถ้ามี)",
        "license_plate": "ทะเบียนรถ (ถ้า fuel)",
        "denominations": { "10000": 1, "2000": 3, ... }
    }
    
    ตรวจสอบ:
    - denominations ต้องไม่เกินยอดในเครื่องจริง
    - ต้องมีอย่างน้อย 1 รายการ
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "กรุณาส่งข้อมูลในรูปแบบ JSON"}), 400
        
        user_id = data.get("user_id", "").strip()
        display_name = data.get("display_name", "").strip() or user_id
        location = data.get("location", "").strip()
        reason_code = data.get("reason", "other").strip()
        reason_text = data.get("reason_text", "").strip()
        license_plate = data.get("license_plate", "").strip()
        raw_denominations = data.get("denominations", {})
        
        # ── Validate ──
        if not user_id:
            return jsonify({"success": False, "error": "กรุณาระบุ user_id"}), 400
        if not location:
            return jsonify({"success": False, "error": "กรุณาเลือกสถานที่รับเงิน"}), 400
        if not raw_denominations or not isinstance(raw_denominations, dict):
            return jsonify({"success": False, "error": "กรุณาระบุ denominations"}), 400
        
        # แปลง keys เป็น int และกรองเอาเฉพาะค่าที่รู้จัก
        # เก็บทั้งแบบ int keys (สำหรับส่ง API) และ string keys (สำหรับ MongoDB)
        denominations_int = {}  # {int: int} สำหรับส่ง /cashout/request
        denominations_str = {}  # {str: int} สำหรับเก็บใน MongoDB
        for k, v in raw_denominations.items():
            try:
                fv = int(k)
                pieces = int(v)
                if pieces > 0 and fv in FV_TO_BAHT:
                    denominations_int[fv] = pieces
                    denominations_str[str(fv)] = pieces
            except (ValueError, TypeError):
                continue
        
        if not denominations_int:
            return jsonify({"success": False, "error": "กรุณาระบุจำนวนเงินอย่างน้อย 1 รายการ"}), 400
        
        denominations = denominations_int  # ใช้ int keys สำหรับ logic ภายใน
        
        # ── ตรวจสอบยอดในเครื่อง ──
        branch_id = "NONIKO" if "โนนิโกะ" in location else "Klangfrozen"
        base = get_rest_api_ci_base_for_branch(branch_id)
        headers_inv, meta_inv = build_correlation_headers(sale_id=f"inv-check-{uuid.uuid4().hex[:6]}")
        
        try:
            inv_resp = requests.get(f"{base}/inventory/check", headers=headers_inv, timeout=10)
            inv_resp.raise_for_status()
            inv_data = inv_resp.json()
            inventory_raw = inv_data.get("result", inv_data)
            available = _parse_inventory_response(inventory_raw)
        except Exception as e:
            logger.warning(f"⚠️ [MANUAL_WITHDRAW] ไม่สามารถตรวจสอบ inventory ได้: {e}")
            available = {}
        
        # ตรวจสอบว่าแต่ละ denomination ไม่เกินที่มีในเครื่อง
        over_limit = []
        for fv, pieces in denominations.items():
            max_avail = available.get(fv, 999999)  # ถ้าดึง inventory ไม่ได้ ให้ผ่าน
            if pieces > max_avail:
                over_limit.append(f"{FV_LABELS.get(fv, f'{fv//100} บาท')} (ขอ {pieces}, มี {max_avail})")
        
        if over_limit:
            return jsonify({
                "success": False,
                "error": f"จำนวนเงินที่ขอเกินยอดที่มีในเครื่อง: {'; '.join(over_limit)}",
                "over_limit": over_limit,
            }), 400
        
        # ── คำนวณยอดรวม ──
        total_amount = _denominations_to_baht_total(denominations)
        
        # ── Map reason ──
        reason_map = {
            "ice": "ซื้อน้ำแข็ง",
            "fuel": "เติมน้ำมัน",
            "other": reason_text or "อื่นๆ",
        }
        display_reason = reason_map.get(reason_code, reason_text or reason_code)
        
        # ── สร้าง request_id ──
        request_id = generate_request_id()
        
        # ── บันทึกลง MongoDB ──
        now_bkk, now_utc = now_bangkok_and_utc()
        date_bkk = now_bkk.date().isoformat()
        
        # แปลง denominations_baht keys เป็น string สำหรับ MongoDB
        denom_baht_str = {}
        for k, v in denominations.items():
            baht_key = str(FV_TO_BAHT.get(k, k // 100))
            denom_baht_str[baht_key] = v
        
        doc = {
            "request_id": request_id,
            "user_id": user_id,
            "amount": str(total_amount),
            "amount_baht": total_amount,
            "reason": display_reason,
            "reason_code": reason_code,
            "license_plate": license_plate or None,
            "location": location,
            "branch_id": branch_id,
            "status": "pending",
            "cashout_mode": "manual",  # ← ระบุว่าเป็นแบบ manual
            "requester_name": display_name,  # ✅ ชื่อผู้ขอเบิก
            "denominations": denominations_str,  # ← เก็บ denominations (string keys)
            "denominations_baht": denom_baht_str,  # ← แปลงเป็นบาท (string keys)
            "created_at_bkk": now_bkk.isoformat(),
            "created_at_utc": now_utc.isoformat(),
            "created_date_bkk": date_bkk,
            "updated_at_bkk": None,
            "updated_at_utc": None,
            "channel": "line_bot_liff",
            "status_history": [
                {
                    "status": "pending",
                    "at_bkk": now_bkk.isoformat(),
                    "at_utc": now_utc.isoformat(),
                    "date_bkk": date_bkk,
                    "by": display_name,
                    "by_user_id": user_id,
                }
            ],
        }
        
        requests_collection.insert_one(doc)
        logger.info(f"✅ [MANUAL_WITHDRAW] สร้างคำขอ {request_id} สำเร็จ: {denominations} = {total_amount} บาท")
        
        return jsonify({
            "success": True,
            "request_id": request_id,
            "amount_baht": total_amount,
            "denominations": denominations,
            "message": f"สร้างคำขอเบิกเงิน {total_amount:.0f} บาท สำเร็จ รอหัวหน้าอนุมัติ",
        }), 200
        
    except Exception as e:
        logger.error(f"❌ [MANUAL_WITHDRAW] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@approved_requests_bp.route("/money/approve-manual/<request_id>", methods=["POST"])
def approve_manual_request(request_id):
    """
    อนุมัติคำขอเบิกเงินแบบ manual (ระบุ denominations เอง)
    ข้าม /cashout/plan → ส่ง /cashout/request โดยตรงด้วย denominations ที่เลือก
    """
    logger.info(f"📢 กำลังอนุมัติคำขอแบบ manual: {request_id}")
    
    approver_name = request.form.get("approver_name", "").strip() or "approver_ui"
    
    request_data = requests_collection.find_one({"request_id": request_id})
    if not request_data:
        return jsonify({"status": "error", "message": f"ไม่พบคำขอ {request_id}"}), 404
    
    # ตรวจสอบว่าเป็น manual mode จริง
    if request_data.get("cashout_mode") != "manual":
        logger.warning(f"⚠️ คำขอ {request_id} ไม่ใช่ manual mode (mode={request_data.get('cashout_mode')}) ใช้ approve ปกติแทน")
        return approve_request(request_id)
    
    if request_data.get("status") != "pending":
        return redirect("/money/approved-requests")
    
    amount = request_data.get("amount_baht") or request_data.get("amount")
    location = request_data.get("location")
    reason = request_data.get("reason", "")
    denominations = request_data.get("denominations", {})
    
    # แปลง denominations keys จาก string → int ก่อนส่ง /cashout/request
    # เพราะ MongoDB เก็บเป็น string keys แต่ REST_API_CI ต้องการ int keys (fv ในหน่วยสตางค์)
    denominations_for_api = {}
    for k, v in denominations.items():
        try:
            denominations_for_api[int(k)] = int(v)
        except (ValueError, TypeError):
            continue
    
    if not denominations_for_api:
        return jsonify({"status": "error", "message": "ไม่พบข้อมูล denominations ในคำขอ"}), 400
    
    # ── ตั้งสถานะ awaiting_machine ──
    now_bkk, now_utc = now_bangkok_and_utc()
    date_bkk = now_bkk.date().isoformat()
    
    requests_collection.update_one(
        {"request_id": request_id},
        {
            "$set": {
                "status": "awaiting_machine",
                "updated_at_bkk": now_bkk.isoformat(),
                "updated_at_utc": now_utc.isoformat(),
            },
            "$push": {
                "status_history": {
                    "status": "awaiting_machine",
                    "at_bkk": now_bkk.isoformat(),
                    "at_utc": now_utc.isoformat(),
                    "date_bkk": date_bkk,
                    "by": approver_name,
                }
            },
        },
    )
    
    # ── เลือก base URL ตามสาขา ──
    branch_id = "NONIKO" if "โนนิโกะ" in (location or "") else "Klangfrozen"
    base = get_rest_api_ci_base_for_branch(branch_id)
    headers, meta = build_correlation_headers(sale_id=request_id)
    
    try:
        # ✅ ข้าม /cashout/plan → ส่ง /cashout/request โดยตรง
        request_url = f"{base}/cashout/request"
        request_payload = {
            "denominations": denominations_for_api  # ส่ง int keys ไป API
        }
        
        logger.info(f"📤 [MANUAL_CASHOUT] ส่ง /cashout/request (manual) ไปยัง {request_url}: {request_payload}")
        
        resp = requests.post(request_url, json=request_payload, headers=headers, timeout=10)
        resp.raise_for_status()
        resp_data = resp.json()
        
        if not resp_data.get("success"):
            error_msg = resp_data.get("error", "Unknown error")
            logger.error(f"❌ [MANUAL_CASHOUT] /cashout/request failed: {error_msg}")
            now_bkk, now_utc = now_bangkok_and_utc()
            requests_collection.update_one(
                {"request_id": request_id},
                {
                    "$set": {
                        "status": "error",
                        "machine_error": f"/cashout/request failed: {error_msg}",
                        "updated_at_bkk": now_bkk.isoformat(),
                        "updated_at_utc": now_utc.isoformat(),
                    },
                    "$push": {
                        "status_history": {
                            "status": "error",
                            "at_bkk": now_bkk.isoformat(),
                            "at_utc": now_utc.isoformat(),
                            "date_bkk": now_bkk.date().isoformat(),
                            "by": approver_name,
                        }
                    },
                },
            )
            return jsonify({"status": "error", "message": error_msg}), 500
        
        # ✅ สำเร็จ → อัปเดตสถานะ
        now_bkk, now_utc = now_bangkok_and_utc()
        date_bkk = now_bkk.date().isoformat()
        
        requests_collection.update_one(
            {"request_id": request_id},
            {
                "$set": {
                    "status": "approved",
                    "updated_at_bkk": now_bkk.isoformat(),
                    "updated_at_utc": now_utc.isoformat(),
                    "cashout_request_response": resp_data,
                },
                "$push": {
                    "status_history": {
                        "status": "approved",
                        "at_bkk": now_bkk.isoformat(),
                        "at_utc": now_utc.isoformat(),
                        "date_bkk": date_bkk,
                        "by": approver_name,
                    }
                },
            },
        )
        
        # บันทึก transactions
        save_expense_to_transactions(request_data, location, amount, reason, date_bkk, now_bkk, now_utc)
        logger.info(f"✅ [MANUAL_CASHOUT] อนุมัติคำขอ {request_id} สำเร็จ (manual denominations)")
        
        return redirect("/money/approved-requests")
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ [MANUAL_CASHOUT] Request error: {e}")
        now_bkk, now_utc = now_bangkok_and_utc()
        requests_collection.update_one(
            {"request_id": request_id},
            {
                "$set": {
                    "status": "error",
                    "machine_error": str(e),
                    "updated_at_bkk": now_bkk.isoformat(),
                    "updated_at_utc": now_utc.isoformat(),
                },
                "$push": {
                    "status_history": {
                        "status": "error",
                        "at_bkk": now_bkk.isoformat(),
                        "at_utc": now_utc.isoformat(),
                        "date_bkk": now_bkk.date().isoformat(),
                        "by": approver_name,
                    }
                },
            },
        )
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        logger.error(f"❌ [MANUAL_CASHOUT] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@approved_requests_bp.route("/money/api/withdraw-direct", methods=["POST"])
def api_withdraw_direct():
    """
    API สำหรับเบิกเงินทอนโดยตรง (ไม่ต้องรออนุมัติ)
    รับ JSON:
    {
      "user_id": "LINE user id",
      "location": "คลังห้องเย็น" หรือ "โนนิโกะ",
      "denominations": { "10000": 1, "2000": 3, ... }
    }
    
    Flow:
    1. เช็คเหตุผลต้องเป็น "change" (เงินทอน) เท่านั้น
    2. ยิง /cashout/request โดยตรง (ข้าม /cashout/plan, ขั้นตอนอนุมัติ)
    3. บันทึก request ใน MongoDB สถานะ approved ทันที
    4. บันทึก transaction ใน transactions_collection
    """
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "error", "message": "รูปแบบข้อมูลไม่ถูกต้อง (ต้องเป็น JSON)"}), 400

    user_id = data.get("user_id", "").strip()
    display_name = data.get("display_name", "").strip() or user_id
    location = data.get("location", "").strip()
    raw_denominations = data.get("denominations", {})

    if not user_id:
        return jsonify({"status": "error", "message": "ไม่พบข้อมูลผู้ใช้"}), 400
    if not location:
        return jsonify({"status": "error", "message": "กรุณาเลือกสถานที่รับเงิน"}), 400
    if not raw_denominations or not isinstance(raw_denominations, dict):
        return jsonify({"status": "error", "message": "กรุณาระบุ denominations"}), 400

    # แปลง denominations keys เป็น int
    denominations_int = {}
    for k, v in raw_denominations.items():
        try:
            fv = int(k)
            pieces = int(v)
            if pieces > 0 and fv in FV_TO_BAHT:
                denominations_int[fv] = pieces
        except (ValueError, TypeError):
            continue

    if not denominations_int:
        return jsonify({"status": "error", "message": "กรุณาระบุจำนวนเงินอย่างน้อย 1 รายการ"}), 400

    # คำนวณยอดรวม
    total_amount = _denominations_to_baht_total(denominations_int)
    if total_amount <= 0:
        return jsonify({"status": "error", "message": "ยอดรวมต้องมากกว่า 0 บาท"}), 400

    # ── ⛔ ตรวจสอบวงเงินสะสมวันนี้ ต้องไม่เกิน 1,000 บาท/วัน/คน ──
    today_bkk = now_bangkok().date().isoformat()
    pipeline = [
        {"$match": {
            "user_id": user_id,
            "is_direct_withdraw": True,
            "created_date_bkk": today_bkk,
            "status": "approved"
        }},
        {"$group": {"_id": None, "total": {"$sum": "$amount_baht"}}}
    ]
    aggregation = list(requests_collection.aggregate(pipeline))
    total_today = aggregation[0]["total"] if aggregation else 0
    remaining = 1000 - total_today
    if total_amount > remaining:
        logger.warning(f"⛔ [WITHDRAW_DIRECT] {display_name} ({user_id}) เบิกเกินวงเงินวันนี้: ขอ {total_amount:.0f} บาท, ใช้ไปแล้ว {total_today:.0f} บาท, คงเหลือ {remaining:.0f} บาท")
        return jsonify({
            "status": "error",
            "message": (
                f"⚠️ เบิกเงินทอนไม่สำเร็จ\n"
                f"คุณ {display_name} เบิกเงินทอนวันนี้ไปแล้ว {total_today:.0f} บาท\n"
                f"คงเหลือวงเงินวันนี้ {remaining:.0f} บาท\n"
                f"(จำกัดวงเงินไม่เกิน 1,000 บาท/วัน/คน เพื่อความปลอดภัย)"
            ),
            "total_today": total_today,
            "daily_limit": 1000,
            "remaining": remaining,
        }), 400

    # กำหนดสาขา
    if "โนนิโกะ" in location:
        branch_id = "NONIKO"
    else:
        branch_id = "Klangfrozen"
    base = get_rest_api_ci_base_for_branch(branch_id)

    # สร้าง request_id
    request_id = generate_request_id()
    headers, meta = build_correlation_headers(sale_id=request_id)

    # ── ยิง /cashout/request โดยตรง (ข้าม /cashout/plan, ข้ามอนุมัติ) ──
    try:
        request_url = f"{base}/cashout/request"
        request_payload = {"denominations": denominations_int}

        logger.info(f"📤 [WITHDRAW_DIRECT] เงินทอน - ส่ง /cashout/request ไปยัง {request_url}: {request_payload}")

        resp = requests.post(request_url, json=request_payload, headers=headers, timeout=10)
        resp.raise_for_status()
        resp_data = resp.json()

        if not resp_data.get("success"):
            error_msg = resp_data.get("error", "Unknown error")
            logger.error(f"❌ [WITHDRAW_DIRECT] /cashout/request failed: {error_msg}")
            return jsonify({"status": "error", "message": f"เครื่องไม่สามารถจ่ายเงินได้: {error_msg}"}), 500

        logger.info(f"✅ [WITHDRAW_DIRECT] /cashout/request สำเร็จ: {resp_data}")

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ [WITHDRAW_DIRECT] Request Exception: {str(e)}")
        return jsonify({"status": "error", "message": f"ไม่สามารถเชื่อมต่อเครื่องได้: {str(e)}"}), 502
    except Exception as e:
        logger.error(f"❌ [WITHDRAW_DIRECT] Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

    # ── บันทึกคำขอใน MongoDB สถานะ approved ทันที ──
    now_bkk, now_utc = now_bangkok_and_utc()
    date_bkk = now_bkk.date().isoformat()

    # แปลง denominations เป็น string keys สำหรับ MongoDB
    denominations_str = {str(k): v for k, v in denominations_int.items()}

    # แปลงเป็นบาท
    denom_baht_str = {}
    for k, v in denominations_int.items():
        baht_key = str(FV_TO_BAHT.get(k, k // 100))
        denom_baht_str[baht_key] = v

    doc = {
        "request_id": request_id,
        "user_id": user_id,
        "amount": str(total_amount),
        "amount_baht": total_amount,
        "reason": "เงินทอน",
        "reason_code": "change",
        "license_plate": None,
        "location": location,
        "branch_id": branch_id,
        "status": "approved",
        "cashout_mode": "manual",
        "is_direct_withdraw": True,  # ✅ flag ระบุว่าเป็นการเบิกตรงไม่ต้องอนุมัติ
        "requester_name": display_name,  # ✅ ชื่อผู้ขอเบิก
        "denominations": denominations_str,
        "denominations_baht": denom_baht_str,
        "cashout_request_response": resp_data,
        "created_at_bkk": now_bkk.isoformat(),
        "created_at_utc": now_utc.isoformat(),
        "created_date_bkk": date_bkk,
        "updated_at_bkk": now_bkk.isoformat(),
        "updated_at_utc": now_utc.isoformat(),
        "channel": "liff_direct_withdraw",
        "status_history": [
            {
                "status": "approved",
                "at_bkk": now_bkk.isoformat(),
                "at_utc": now_utc.isoformat(),
                "date_bkk": date_bkk,
                "by": display_name,
                "by_user_id": user_id,
            }
        ],
    }

    try:
        requests_collection.insert_one(doc)
        logger.info(f"✅ [WITHDRAW_DIRECT] บันทึกคำขอเบิกเงินทอน {request_id} สำเร็จ: {total_amount} บาท")
    except Exception as e:
        logger.error(f"❌ [WITHDRAW_DIRECT] ไม่สามารถบันทึกคำขอได้: {str(e)}")
        # API ไปที่เครื่องสำเร็จแล้ว แต่บันทึก DB ไม่ได้ — แจ้งเตือนแต่ให้ผ่าน
        # ไม่ return error เพราะเงินออกจากเครื่องไปแล้ว

    # ── บันทึก transaction ──
    try:
        save_expense_to_transactions(doc, location, total_amount, "เงินทอน", date_bkk, now_bkk, now_utc)
    except Exception as e:
        logger.error(f"❌ [WITHDRAW_DIRECT] ไม่สามารถบันทึก transaction: {str(e)}")

    return jsonify({
        "status": "ok",
        "request_id": request_id,
        "amount_baht": total_amount,
        "message": f"เบิกเงินทอน {total_amount:.0f} บาทสำเร็จ",
    }), 200


@approved_requests_bp.route("/money/api/withdraw-request", methods=["POST"])
def api_withdraw_request():
    """
    API สำหรับ LIFF ฟอร์มขอเบิกเงิน
    รับ JSON:
    {
      "userId": "...",
      "amount": "100",
      "reason": "ice" | "fuel" | "other",
      "reasonOther": "ข้อความเหตุผลเมื่อเลือก other",
      "licensePlate": "ทะเบียนรถ (ถ้ามี)",
      "location": "คลังห้องเย็น" | "โนนิโกะ"
    }
    """
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "error", "message": "รูปแบบข้อมูลไม่ถูกต้อง (ต้องเป็น JSON)"}), 400

    user_id = data.get("userId")
    display_name = data.get("displayName", "").strip() or user_id
    amount_raw = data.get("amount")
    reason_code = data.get("reason")
    reason_other = (data.get("reasonOther") or "").strip()
    license_plate = (data.get("licensePlate") or "").strip()
    location_text = (data.get("location") or "").strip()

    # ตรวจสอบข้อมูลเบื้องต้น
    if not user_id:
        return jsonify({"status": "error", "message": "ไม่พบข้อมูลผู้ใช้จาก LIFF"}), 400

    if not amount_raw:
        return jsonify({"status": "error", "message": "กรุณาระบุจำนวนเงิน"}), 400

    try:
        amount_int = int(str(amount_raw).strip())
        if amount_int <= 0:
            raise ValueError()
    except ValueError:
        return jsonify({"status": "error", "message": "จำนวนเงินไม่ถูกต้อง"}), 400

    if reason_code not in ("ice", "fuel", "other"):
        return jsonify({"status": "error", "message": "เหตุผลในการเบิกไม่ถูกต้อง"}), 400

    # แปลงเหตุผลจริงสำหรับเก็บลงฐานข้อมูล
    if reason_code == "other":
        if not reason_other:
            return jsonify({"status": "error", "message": "กรุณาระบุเหตุผลเพิ่มเติม"}), 400
        reason = reason_other
    elif reason_code == "ice":
        reason = "ซื้อน้ำแข็ง"
    elif reason_code == "fuel":
        reason = "เติมน้ำมัน"
    else:
        reason = reason_code

    if reason_code == "fuel" and not license_plate:
        return jsonify({"status": "error", "message": "กรุณากรอกหมายเลขทะเบียนรถ"}), 400

    if location_text not in ("คลังห้องเย็น", "โนนิโกะ"):
        return jsonify({"status": "error", "message": "กรุณาเลือกสถานที่รับเงินให้ถูกต้อง"}), 400

    # สร้างหมายเลขคำขอ
    request_id = generate_request_id()

    now_bkk, now_utc = now_bangkok_and_utc()
    date_bkk = now_bkk.date().isoformat()

    request_data = {
        "request_id": request_id,
        "user_id": user_id,
        "requester_name": display_name,
        "amount": str(amount_int),
        "reason": reason,
        "license_plate": license_plate if license_plate else None,
        "location": location_text,
        "status": "pending",
        "created_at_bkk": now_bkk.isoformat(),
        "created_at_utc": now_utc.isoformat(),
        "created_date_bkk": date_bkk,
        "status_history": [
            {
                "status": "pending",
                "at_bkk": now_bkk.isoformat(),
                "at_utc": now_utc.isoformat(),
                "date_bkk": date_bkk,
                "by": display_name,
                "by_user_id": user_id,
            }
        ],
        "channel": "liff",  # ระบุว่ามาจาก LIFF
    }

    try:
        requests_collection.insert_one(request_data)
        logger.info(f"✅ สร้างคำขอเบิกเงินผ่าน LIFF สำเร็จ: {request_id}")
        return jsonify(
            {
                "status": "ok",
                "request_id": request_id,
                "created_date_bkk": date_bkk,
            }
        )
    except Exception as e:
        logger.error(f"❌ บันทึกคำขอเบิกเงินผ่าน LIFF ไม่สำเร็จ: {str(e)}")
        return jsonify({"status": "error", "message": "ไม่สามารถบันทึกคำขอได้ กรุณาลองใหม่อีกครั้ง"}), 500


@approved_requests_bp.route("/money/api/deposit-request", methods=["POST"])
def api_deposit_request():
    """
    API สำหรับ LIFF ฟอร์มฝากเงินสด
    รับ JSON:
    {
      "userId": "...",
      "amount": "100",
      "reason": "change" | "daily_sales" | "other_deposit",
      "reasonOther": "ข้อความเหตุผลเมื่อเลือก other_deposit",
      "location": "คลังห้องเย็น" | "โนนิโกะ"
    }

    โหมดใหม่: async
    - บันทึกคำขอและสร้าง deposit_request_id
    - ตอบกลับทันที (status=ok, deposit_request_id)
    - ประมวลผลฝากเงินจริงใน background และอัปเดตสถานะใน DB
    """
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "error", "message": "รูปแบบข้อมูลไม่ถูกต้อง (ต้องเป็น JSON)"}), 400

    user_id = data.get("userId")
    display_name = data.get("displayName", "").strip() or user_id
    reason_code = data.get("reason")
    reason_other = (data.get("reasonOther") or "").strip()
    location_text = (data.get("location") or "").strip()

    if not user_id:
        return jsonify({"status": "error", "message": "ไม่พบข้อมูลผู้ใช้จาก LIFF"}), 400

    if reason_code not in ("change", "daily_sales", "other_deposit"):
        return jsonify({"status": "error", "message": "เหตุผลในการฝากเงินไม่ถูกต้อง"}), 400

    if reason_code == "other_deposit" and not reason_other:
        return jsonify({"status": "error", "message": "กรุณาระบุเหตุผลเพิ่มเติม"}), 400

    if location_text not in ("คลังห้องเย็น", "โนนิโกะ"):
        return jsonify({"status": "error", "message": "กรุณาเลือกสาขาที่ฝากเงินให้ถูกต้อง"}), 400

    # แม็ปเหตุผลให้เป็นข้อความอ่านง่าย
    if reason_code == "change":
        reason = "เงินทอน"
    elif reason_code == "daily_sales":
        reason = "ฝากยอดขาย"
    elif reason_code == "other_deposit":
        reason = reason_other
    else:
        reason = reason_code

    # กำหนด endpoint และ branch_id ตามสาขา
    if location_text == "โนนิโกะ":
        base = get_rest_api_ci_base_for_branch("NONIKO")
        branch_id = "NONIKO"
    else:  # คลังห้องเย็น
        base = get_rest_api_ci_base_for_branch("Klangfrozen")
        branch_id = "Klangfrozen"

    # Use deposit_request_id as sale_id for downstream correlation
    deposit_request_id = f"d-{uuid.uuid4().hex[:8]}"
    headers, meta = build_correlation_headers(sale_id=deposit_request_id)
    trace_id = meta["trace_id"]
    request_header_id = meta["request_id"]

    # สร้าง session_id และ seq_no สำหรับ replenishment
    session_id = deposit_request_id
    seq_no = "1"

    # ไม่บันทึกตอนเริ่มฝาก - จะบันทึกตอนจบฝากเมื่อได้ยอดเงินแล้ว

    # ยิง API /replenishment/start
    try:
        replenishment_start_url = f"{base}/replenishment/start"
        replenishment_payload = {
            "seq_no": seq_no,
            "session_id": session_id
        }
        
        logger.info(f"📤 [DEPOSIT] กำลังยิง /replenishment/start: {replenishment_start_url}")
        start_response = requests.post(replenishment_start_url, json=replenishment_payload, headers=headers, timeout=10)
        start_response.raise_for_status()
        start_data = start_response.json()
        
        if not start_data.get("success"):
            error_msg = start_data.get("error", "Unknown error from /replenishment/start")
            logger.error(f"❌ [DEPOSIT] /replenishment/start failed: {error_msg}")
            return jsonify({"status": "error", "message": f"/replenishment/start failed: {error_msg}"}), 500
        
        logger.info(f"✅ [DEPOSIT] /replenishment/start สำเร็จ: {start_data}")
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ [DEPOSIT] Request Exception: {str(e)}")
        return jsonify({"status": "error", "message": f"Request exception: {str(e)}"}), 500
    except Exception as e:
        logger.error(f"❌ [DEPOSIT] Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

    # Return deposit_request_id และข้อมูลที่จำเป็นสำหรับหน้า UI
    return jsonify({
        "status": "ok",
        "deposit_request_id": deposit_request_id,
        "session_id": session_id,
        "seq_no": seq_no,
        "branch_base_url": base,
        "location": location_text,
        "reason": reason,
        "trace_id": trace_id,
        "request_id": request_header_id,
        "sale_id": deposit_request_id
    })

@approved_requests_bp.route("/money/api/deposit-status", methods=["GET"])
def api_deposit_status():
    deposit_request_id = request.args.get("id") or request.args.get("deposit_request_id")
    if not deposit_request_id:
        return jsonify({"status": "error", "message": "missing deposit_request_id"}), 400
    doc = deposit_requests_collection.find_one({"deposit_request_id": deposit_request_id}, {"_id": 0})
    if not doc:
        return jsonify({"status": "error", "message": "not found"}), 404
    resp = {
        "deposit_request_id": doc.get("deposit_request_id"),
        "status": doc.get("status"),
        "error_message": doc.get("error_message"),
        "created_at_bkk": doc.get("created_at_bkk"),
        "updated_at_bkk": doc.get("updated_at_bkk"),
        "location": doc.get("location"),
        "amount": doc.get("amount"),
    }
    return jsonify({"status": "ok", "data": resp})


@approved_requests_bp.route("/money/api/deposit-info", methods=["GET"])
def api_deposit_info():
    """Get deposit request info for monitoring page"""
    deposit_request_id = request.args.get("id") or request.args.get("deposit_request_id")
    if not deposit_request_id:
        return jsonify({"status": "error", "message": "missing deposit_request_id"}), 400
    
    doc = deposit_requests_collection.find_one({"deposit_request_id": deposit_request_id}, {"_id": 0})
    if not doc:
        return jsonify({"status": "error", "message": "not found"}), 404
    
    # กำหนด branch_base_url จาก branch_id
    branch_id = doc.get("branch_id")
    branch_base_url = get_rest_api_ci_base_for_branch(branch_id) if branch_id else None
    
    resp = {
        "deposit_request_id": doc.get("deposit_request_id"),
        "location": doc.get("location"),
        "reason": doc.get("reason"),
        "session_id": doc.get("session_id"),
        "seq_no": doc.get("seq_no"),
        "branch_id": branch_id,
        "branch_base_url": branch_base_url,
        "status": doc.get("status"),
    }
    return jsonify({"status": "ok", "data": resp})


@approved_requests_bp.route("/money/api/replenishment-end", methods=["POST"])
def api_replenishment_end():
    """End replenishment operation and save deposit record"""
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "error", "message": "รูปแบบข้อมูลไม่ถูกต้อง (ต้องเป็น JSON)"}), 400
    
    deposit_id = data.get("deposit_id")
    session_id = data.get("session_id")
    seq_no = data.get("seq_no", "1")
    user_id = data.get("user_id")
    display_name = data.get("display_name", "").strip() or user_id
    reason_code = data.get("reason_code")
    reason_other = data.get("reason_other", "")
    location_text = data.get("location")
    amount = data.get("amount", 0)  # ยอดเงินที่ส่งมาจาก frontend
    
    if not deposit_id:
        return jsonify({"status": "error", "message": "missing deposit_id"}), 400
    
    if not user_id or not reason_code or not location_text:
        return jsonify({"status": "error", "message": "missing required fields (user_id, reason_code, location)"}), 400
    
    # กำหนด branch_id และ branch_base_url ตาม location
    if location_text == "โนนิโกะ":
        branch_id = "NONIKO"
        branch_base_url = get_rest_api_ci_base_for_branch("NONIKO")
    else:  # คลังห้องเย็น
        branch_id = "Klangfrozen"
        branch_base_url = get_rest_api_ci_base_for_branch("Klangfrozen")
    
    if not branch_base_url:
        return jsonify({"status": "error", "message": "branch_base_url not found"}), 400
    
    # แม็ปเหตุผลให้เป็นข้อความอ่านง่าย
    if reason_code == "change":
        reason = "เงินทอน"
    elif reason_code == "daily_sales":
        reason = "ฝากยอดขาย"
    elif reason_code == "other_deposit":
        reason = reason_other
    else:
        reason = reason_code
    
    # ยิง API /replenishment/end
    try:
        headers, meta = build_correlation_headers(sale_id=deposit_id)
        end_url = f"{branch_base_url}/replenishment/end"
        end_payload = {
            "seq_no": seq_no,
            "session_id": session_id
        }
        
        logger.info(f"📤 [REPLENISHMENT] กำลังยิง /replenishment/end: {end_url}")
        end_response = requests.post(end_url, json=end_payload, headers=headers, timeout=10)
        end_response.raise_for_status()
        end_data = end_response.json()
        
        if not end_data.get("success"):
            error_msg = end_data.get("error", "Unknown error from /replenishment/end")
            logger.error(f"❌ [REPLENISHMENT] /replenishment/end failed: {error_msg}")
            return jsonify({"status": "error", "message": f"/replenishment/end failed: {error_msg}"}), 500
        
        logger.info(f"✅ [REPLENISHMENT] /replenishment/end สำเร็จ: {end_data}")
        
        # ดึงยอดเงินจาก socket/latest (ถ้ายังไม่มี amount)
        if not amount or amount == 0:
            try:
                socket_url = f"{branch_base_url}/socket/latest"
                socket_response = requests.get(socket_url, headers=headers, timeout=5)
                if socket_response.status_code == 200:
                    socket_data = socket_response.json()
                    if socket_data.get("success") and socket_data.get("amount_baht"):
                        amount = socket_data.get("amount_baht")
                        logger.info(f"💰 [REPLENISHMENT] ดึงยอดเงินจาก socket/latest: {amount} บาท")
            except Exception as e:
                logger.warning(f"⚠️ [REPLENISHMENT] ไม่สามารถดึงยอดเงินจาก socket/latest: {str(e)}")
        
        # บันทึกข้อมูลการฝากเงินใหม่ (บันทึกตอนจบฝากเมื่อได้ยอดเงินแล้ว)
        now_bkk, now_utc = now_bangkok_and_utc()
        date_bkk = now_bkk.date().isoformat()
        
        deposit_doc = {
            "deposit_request_id": deposit_id,
            "user_id": user_id,
            "requester_name": display_name,
            "amount": float(amount) if amount else None,
            "reason_code": reason_code,
            "reason": reason,
            "location": location_text,
            "branch_id": branch_id,
            "session_id": session_id,
            "seq_no": seq_no,
            "trace_id": meta.get("trace_id"),
            "request_header_id": meta.get("request_id"),
            "status": "completed",
            "created_at_bkk": now_bkk.isoformat(),
            "created_at_utc": now_utc.isoformat(),
            "created_date_bkk": date_bkk,
            "updated_at_bkk": now_bkk.isoformat(),
            "updated_at_utc": now_utc.isoformat(),
            "status_history": [
                {
                    "status": "completed",
                    "at_bkk": now_bkk.isoformat(),
                    "at_utc": now_utc.isoformat(),
                    "date_bkk": date_bkk,
                    "by": display_name,
                    "by_user_id": user_id,
                }
            ],
        }
        
        try:
            deposit_requests_collection.insert_one(deposit_doc)
            logger.info(f"✅ [DEPOSIT] บันทึกข้อมูลการฝากเงินสำเร็จ: {deposit_id}, จำนวน: {amount} บาท")
        except Exception as e:
            logger.error(f"❌ [DEPOSIT] ไม่สามารถบันทึกข้อมูลการฝากเงินได้: {str(e)}")
            # ไม่ return error เพราะ replenishment/end สำเร็จแล้ว
        
        return jsonify({"status": "ok", "message": "จบการฝากเงินสำเร็จ"})
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ [REPLENISHMENT] Request Exception: {str(e)}")
        return jsonify({"status": "error", "message": f"Request exception: {str(e)}"}), 500
    except Exception as e:
        logger.error(f"❌ [REPLENISHMENT] Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@approved_requests_bp.route("/money/api/replenishment-cancel", methods=["POST"])
def api_replenishment_cancel():
    """Cancel replenishment operation"""
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "error", "message": "รูปแบบข้อมูลไม่ถูกต้อง (ต้องเป็น JSON)"}), 400
    
    deposit_id = data.get("deposit_id")
    session_id = data.get("session_id")
    seq_no = data.get("seq_no", "1")
    location_text = data.get("location")
    
    if not deposit_id:
        return jsonify({"status": "error", "message": "missing deposit_id"}), 400
    
    # กำหนด branch_id และ branch_base_url ตาม location
    if location_text == "โนนิโกะ":
        branch_id = "NONIKO"
        branch_base_url = get_rest_api_ci_base_for_branch("NONIKO")
    elif location_text == "คลังห้องเย็น":
        branch_id = "Klangfrozen"
        branch_base_url = get_rest_api_ci_base_for_branch("Klangfrozen")
    else:
        # ถ้าไม่มี location ให้ลองดึงจาก doc (กรณีเก่า)
        doc = deposit_requests_collection.find_one({"deposit_request_id": deposit_id})
        if doc:
            branch_id = doc.get("branch_id")
            branch_base_url = get_rest_api_ci_base_for_branch(branch_id) if branch_id else None
            session_id = session_id or doc.get("session_id")
            seq_no = seq_no or doc.get("seq_no", "1")
        else:
            branch_base_url = None
    
    if not branch_base_url:
        return jsonify({"status": "error", "message": "branch_base_url not found"}), 400
    
    # ยิง API /replenishment/cancel
    try:
        headers, meta = build_correlation_headers(sale_id=deposit_id)
        cancel_url = f"{branch_base_url}/replenishment/cancel"
        cancel_payload = {
            "seq_no": seq_no,
            "session_id": session_id
        }
        
        logger.info(f"📤 [REPLENISHMENT] กำลังยิง /replenishment/cancel: {cancel_url}")
        cancel_response = requests.post(cancel_url, json=cancel_payload, headers=headers, timeout=10)
        cancel_response.raise_for_status()
        cancel_data = cancel_response.json()
        
        if not cancel_data.get("success"):
            error_msg = cancel_data.get("error", "Unknown error from /replenishment/cancel")
            logger.error(f"❌ [REPLENISHMENT] /replenishment/cancel failed: {error_msg}")
            return jsonify({"status": "error", "message": f"/replenishment/cancel failed: {error_msg}"}), 500
        
        logger.info(f"✅ [REPLENISHMENT] /replenishment/cancel สำเร็จ: {cancel_data}")
        
        # ไม่ต้องบันทึกอะไรเมื่อยกเลิก เพราะยังไม่มีการฝากเงินจริง
        
        return jsonify({"status": "ok", "message": "ยกเลิกการฝากเงินสำเร็จ"})
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ [REPLENISHMENT] Request Exception: {str(e)}")
        return jsonify({"status": "error", "message": f"Request exception: {str(e)}"}), 500
    except Exception as e:
        logger.error(f"❌ [REPLENISHMENT] Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@approved_requests_bp.route("/money/api/socket-latest", methods=["GET"])
def api_socket_latest():
    """Get latest socket amount from branch API"""
    try:
        deposit_id = request.args.get("deposit_id")
        
        if not deposit_id:
            return jsonify({"status": "error", "message": "missing deposit_id"}), 400
        
        # ดึงข้อมูล deposit request
        doc = deposit_requests_collection.find_one({"deposit_request_id": deposit_id})
        if not doc:
            return jsonify({"status": "error", "message": "deposit request not found"}), 404
        
        branch_id = doc.get("branch_id")
        branch_base_url = get_rest_api_ci_base_for_branch(branch_id) if branch_id else None
        
        if not branch_base_url:
            return jsonify({"status": "error", "message": "branch_base_url not found"}), 400
        
        # ยิง GET request ไปที่ /socket/latest
        try:
            headers, meta = build_correlation_headers(sale_id=deposit_id)
            socket_url = f"{branch_base_url}/socket/latest"
            
            logger.debug(f"📤 [SOCKET] กำลังยิง /socket/latest: {socket_url}")
            socket_response = requests.get(socket_url, headers=headers, timeout=5)
            socket_response.raise_for_status()
            socket_data = socket_response.json()
            
            logger.debug(f"✅ [SOCKET] /socket/latest สำเร็จ: {socket_data}")
            
            # Return response ตาม format เดิม
            return jsonify({
                "status": "ok",
                "amount_baht": socket_data.get("amount_baht", 0),
                "success": socket_data.get("success", True),
                "ts": socket_data.get("ts", 0)
            })
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ [SOCKET] Request Exception: {str(e)}")
            return jsonify({
                "status": "error",
                "message": f"Request exception: {str(e)}",
                "amount_baht": 0,
                "success": False,
                "ts": 0
            }), 500
        except Exception as e:
            logger.error(f"❌ [SOCKET] Error: {str(e)}")
            return jsonify({
                "status": "error",
                "message": str(e),
                "amount_baht": 0,
                "success": False,
                "ts": 0
            }), 500
            
    except Exception as e:
        logger.error(f"❌ [SOCKET] Error: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e),
            "amount_baht": 0,
            "success": False,
            "ts": 0
        }), 500


@approved_requests_bp.route("/money/api/socket-latest-proxy", methods=["GET"])
def api_socket_latest_proxy():
    """
    Proxy endpoint สำหรับ frontend เพื่อยิง API ไปที่ new_ci_api /socket/latest
    รับ parameters: branch_base_url, trace_id, request_id, sale_id, seq_no, session_id
    """
    try:
        branch_base_url = request.args.get("branch_base_url")
        trace_id = request.args.get("trace_id")
        request_id = request.args.get("request_id")
        sale_id = request.args.get("sale_id")
        seq_no = request.args.get("seq_no")
        session_id = request.args.get("session_id")
        
        if not branch_base_url:
            return jsonify({
                "status": "error",
                "message": "missing branch_base_url",
                "amount_baht": 0,
                "success": False,
                "ts": 0
            }), 400
        
        # สร้าง headers ตามที่ API ต้องการ
        headers = {
            "Content-Type": "application/json"
        }
        
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        if request_id:
            headers["X-Request-Id"] = request_id
        if sale_id:
            headers["X-Sale-Id"] = sale_id
        if seq_no:
            headers["X-Seq-No"] = seq_no
        if session_id:
            headers["X-Session-Id"] = session_id
        
        # ยิง GET request ไปที่ /socket/latest
        try:
            socket_url = f"{branch_base_url}/socket/latest"
            
            logger.debug(f"📤 [SOCKET-PROXY] กำลังยิง /socket/latest: {socket_url}")
            socket_response = requests.get(socket_url, headers=headers, timeout=5)
            socket_response.raise_for_status()
            socket_data = socket_response.json()
            
            logger.debug(f"✅ [SOCKET-PROXY] /socket/latest สำเร็จ: {socket_data}")
            
            # Return response ตาม format เดิม
            return jsonify({
                "status": "ok",
                "amount_baht": socket_data.get("amount_baht", 0),
                "success": socket_data.get("success", True),
                "ts": socket_data.get("ts", 0)
            })
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ [SOCKET-PROXY] Request Exception: {str(e)}")
            return jsonify({
                "status": "error",
                "message": f"Request exception: {str(e)}",
                "amount_baht": 0,
                "success": False,
                "ts": 0
            }), 500
        except Exception as e:
            logger.error(f"❌ [SOCKET-PROXY] Error: {str(e)}")
            return jsonify({
                "status": "error",
                "message": str(e),
                "amount_baht": 0,
                "success": False,
                "ts": 0
            }), 500
            
    except Exception as e:
        logger.error(f"❌ [SOCKET-PROXY] Error: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e),
            "amount_baht": 0,
            "success": False,
            "ts": 0
        }), 500
@approved_requests_bp.route("/money/deposit-monitor", methods=["GET"])
def deposit_monitor():
    """หน้า UI สำหรับติดตามการฝากเงิน"""
    from flask import make_response
    # เพิ่ม headers เพื่อป้องกัน cache
    response = make_response(render_template("deposit_monitor.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
