import uuid
import requests
import logging
import json
import requests
from flask import Blueprint, render_template, jsonify, redirect, url_for, request
from db import requests_collection, deposit_requests_collection, transactions_collection
from time_utils import now_bangkok, now_bangkok_and_utc
from http_utils import build_correlation_headers, get_rest_api_ci_base_for_branch

# ✅ ตั้งค่า Logging ให้ใช้งานได้
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)  # ✅ แก้ไขให้ประกาศ logger ที่นี่

# สร้าง Blueprint สำหรับ Web UI / LIFF เงิน
approved_requests_bp = Blueprint("approved_requests", __name__, template_folder="templates")


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

    # ข้อมูลฝากเงิน (deposit) จาก collection transactions
    deposit_query = {
        "direction": "deposit",
        "transaction_date_bkk": selected_date,
    }
    if selected_branch in ("คลังห้องเย็น", "โนนิโกะ"):
        deposit_query["selectedStorage"] = selected_branch

    deposit_cursor = transactions_collection.find(deposit_query, {"_id": 0}).sort(
        "transaction_at_bkk", -1
    )
    deposit_transactions = list(deposit_cursor)

    return render_template(
        "request_status.html",
        approved_requests=approved_requests,
        rejected_requests=rejected_requests,
        deposit_transactions=deposit_transactions,
        selected_date=selected_date,
        selected_branch=selected_branch,
    )


@approved_requests_bp.route("/money/approve/<request_id>", methods=["POST"])
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
                        "by": "approver_ui",
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
        api_url = f"{base}/bot/withdraw"
        payload = {
            "amount": int(amount),  # ✅ แปลงเป็น int
            "machine_id": "line_bot_audit_kf",
            "branch_id": "NONIKO"
        }
        headers, meta = build_correlation_headers(sale_id=request_id)
        trace_id = meta["trace_id"]
        request_header_id = meta["request_id"]

        logger.info(f"📤 กำลังส่ง API ไปยัง {api_url} ด้วย Payload: {payload}")

        try:
            # Withdraw can take longer; allow up to 60s
            response = requests.post(api_url, json=payload, headers=headers, timeout=60)

            # ✅ Log response status และ body
            logger.info(f"📤 API Response Status: {response.status_code}")
            logger.info(f"📤 API Response Body: {response.text}")

            response.raise_for_status()  # ถ้า HTTP Status ไม่ใช่ 200 จะเกิด Exception

            response_data = response.json()
            if response_data.get("transaction_status") != "success":
                logger.error(f"❌ API ตอบกลับผิดพลาด: {response_data}")
                return jsonify({"status": "error", "message": f"API ตอบกลับผิดพลาด: {response_data}"}), 500
            else:
                # ✅ ใช้เวลาแบบ Bangkok +7 และ UTC สำหรับบันทึกสถานะ
                now_bkk, now_utc = now_bangkok_and_utc()
                date_bkk = now_bkk.date().isoformat()

                # ✅ อัปเดตสถานะเป็น "approved" ในฐานข้อมูล พร้อมเก็บเวลาและประวัติสถานะ
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "status": "approved",
                            "updated_at_bkk": now_bkk.isoformat(),
                            "updated_at_utc": now_utc.isoformat(),
                            "machine_response": {
                                "status_code": response.status_code,
                                "body": response_data,
                                "trace_id": trace_id,
                                "request_id": request_header_id,
                            },
                        },
                        "$push": {
                            "status_history": {
                                "status": "approved",
                                "at_bkk": now_bkk.isoformat(),
                                "at_utc": now_utc.isoformat(),
                                "date_bkk": date_bkk,
                                "by": "approver_ui",
                            }
                        },
                    },
                )

                # ✅ บันทึกข้อมูลธุรกรรมใน transactions collection
                transaction_data = {
                    "name": reason,
                    "amount": int(amount),
                    "receiptAttached": False,
                    "tags": [],
                    "type": "expense",
                    "selectedStorage": location,
                    "selectedDate": date_bkk,
                    "transaction_at_bkk": now_bkk.isoformat(),
                    "transaction_at_utc": now_utc.isoformat(),
                    "transaction_date_bkk": date_bkk,
                    "request_id": request_id,
                    "machine_trace_id": trace_id,
                    "machine_request_id": request_header_id,
                }

                # บันทึกข้อมูลลงฐานข้อมูล
                transaction_result = transactions_collection.insert_one(transaction_data)
                logger.info(f"✅ บันทึกข้อมูลธุรกรรม ID: {transaction_result.inserted_id} สำเร็จ")

                logger.info(f"✅ อนุมัติคำขอ {request_id} สำเร็จ")
                return redirect("/money/approved-requests")

        except requests.exceptions.RequestException as e:
            # เก็บ error ลงคำขอ แต่คงสถานะ awaiting_machine เพื่อให้ตามงานต่อได้
            logger.error(f"❌ API Error: {str(e)}")
            try:
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "machine_error": str(e),
                            "machine_last_attempt_at_bkk": now_bkk.isoformat(),
                            "machine_last_attempt_at_utc": now_utc.isoformat(),
                            "machine_request": {
                                "api_url": api_url,
                                "payload": payload,
                                "headers": {"X-Trace-Id": trace_id, "X-Request-Id": request_header_id, "X-Sale-Id": str(request_id)},
                            },
                        }
                    },
                )
            except Exception as e2:
                logger.error(f"❌ บันทึก machine_error ไม่สำเร็จ: {str(e2)}")
            return jsonify({"status": "error", "message": "ตู้ถอนเงินยังไม่ตอบรับ กรุณาตรวจสอบสถานะอีกครั้ง"}), 502
    elif location == "คลังห้องเย็น":
        base = get_rest_api_ci_base_for_branch("Klangfrozen")
        api_url = f"{base}/bot/withdraw"
        payload = {
            "amount": int(amount),  # ✅ แปลงเป็น int
            "machine_id": "line_bot_audit_kf",
            "branch_id": "Klangfrozen"
        }
        headers, meta = build_correlation_headers(sale_id=request_id)
        trace_id = meta["trace_id"]
        request_header_id = meta["request_id"]

        logger.info(f"📤 กำลังส่ง API ไปยัง {api_url} ด้วย Payload: {payload}")

        try:
            # Withdraw can take longer; allow up to 60s
            response = requests.post(api_url, json=payload, headers=headers, timeout=60)

            # ✅ Log response status และ body
            logger.info(f"📤 API Response Status: {response.status_code}")
            logger.info(f"📤 API Response Body: {response.text}")

            response.raise_for_status()  # ถ้า HTTP Status ไม่ใช่ 200 จะเกิด Exception

            response_data = response.json()
            if response_data.get("transaction_status") != "success":
                logger.error(f"❌ API ตอบกลับผิดพลาด: {response_data}")
                return jsonify({"status": "error", "message": f"API ตอบกลับผิดพลาด: {response_data}"}), 500
            else:
                # ✅ ใช้เวลาแบบ Bangkok +7 และ UTC สำหรับบันทึกสถานะ
                now_bkk, now_utc = now_bangkok_and_utc()
                date_bkk = now_bkk.date().isoformat()

                # ✅ อัปเดตสถานะเป็น "approved" ในฐานข้อมูล พร้อมเก็บเวลาและประวัติสถานะ
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "status": "approved",
                            "updated_at_bkk": now_bkk.isoformat(),
                            "updated_at_utc": now_utc.isoformat(),
                            "machine_response": {
                                "status_code": response.status_code,
                                "body": response_data,
                                "trace_id": trace_id,
                                "request_id": request_header_id,
                            },
                        },
                        "$push": {
                            "status_history": {
                                "status": "approved",
                                "at_bkk": now_bkk.isoformat(),
                                "at_utc": now_utc.isoformat(),
                                "date_bkk": date_bkk,
                                "by": "approver_ui",
                            }
                        },
                    },
                )

                # ✅ บันทึกข้อมูลธุรกรรมใน transactions collection
                transaction_data = {
                    "name": reason,
                    "amount": int(amount),
                    "receiptAttached": False,
                    "tags": [],
                    "type": "expense",
                    "selectedStorage": location,
                    "selectedDate": date_bkk,
                    "transaction_at_bkk": now_bkk.isoformat(),
                    "transaction_at_utc": now_utc.isoformat(),
                    "transaction_date_bkk": date_bkk,
                    "request_id": request_id,
                    "machine_trace_id": trace_id,
                    "machine_request_id": request_header_id,
                }

                # บันทึกข้อมูลลงฐานข้อมูล
                transaction_result = transactions_collection.insert_one(transaction_data)
                logger.info(f"✅ บันทึกข้อมูลธุรกรรม ID: {transaction_result.inserted_id} สำเร็จ")

                logger.info(f"✅ อนุมัติคำขอ {request_id} สำเร็จ")
                return redirect("/money/approved-requests")

        except requests.exceptions.RequestException as e:
            # เก็บ error ลงคำขอ แต่คงสถานะ awaiting_machine เพื่อให้ตามงานต่อได้
            logger.error(f"❌ API Error: {str(e)}")
            try:
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "machine_error": str(e),
                            "machine_last_attempt_at_bkk": now_bkk.isoformat(),
                            "machine_last_attempt_at_utc": now_utc.isoformat(),
                            "machine_request": {
                                "api_url": api_url,
                                "payload": payload,
                                "headers": {"X-Trace-Id": trace_id, "X-Request-Id": request_header_id, "X-Sale-Id": str(request_id)},
                            },
                        }
                    },
                )
            except Exception as e2:
                logger.error(f"❌ บันทึก machine_error ไม่สำเร็จ: {str(e2)}")
            return jsonify({"status": "error", "message": "ตู้ถอนเงินยังไม่ตอบรับ กรุณาตรวจสอบสถานะอีกครั้ง"}), 502

@approved_requests_bp.route("/money/reject/<request_id>", methods=["POST"])
def reject_request(request_id):
    """ ปฏิเสธคำขอและอัปเดตสถานะใน MongoDB """
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
                    "by": "approver_ui",
                }
            },
        },
    )
    return redirect("/money/approved-requests")


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
    from handlers import generate_request_id  # นำมาใช้ซ้ำเพื่อไม่ต้องสร้างซ้ำ

    request_id = generate_request_id()

    now_bkk, now_utc = now_bangkok_and_utc()
    date_bkk = now_bkk.date().isoformat()

    request_data = {
        "request_id": request_id,
        "user_id": user_id,
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
                "by": user_id,
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

    ทำงานคล้าย flow เดิมใน handlers: ยิง API ไปที่ตู้ฝากเงิน แล้วตอบกลับผลลัพธ์
    """
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "error", "message": "รูปแบบข้อมูลไม่ถูกต้อง (ต้องเป็น JSON)"}), 400

    user_id = data.get("userId")
    amount_raw = data.get("amount")
    reason_code = data.get("reason")
    reason_other = (data.get("reasonOther") or "").strip()
    location_text = (data.get("location") or "").strip()

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

    # กำหนด endpoint และ branch_id ตามสาขา (ตามโค้ดเดิมใน handlers)
    if location_text == "โนนิโกะ":
        base = get_rest_api_ci_base_for_branch("NONIKO")
        api_url = f"{base}/bot/deposit"
        branch_id = "NONIKO"
    else:  # คลังห้องเย็น
        base = get_rest_api_ci_base_for_branch("Klangfrozen")
        api_url = f"{base}/bot/deposit"
        branch_id = "Klangfrozen"

    payload = {
        "amount": amount_int,
        "machine_id": "line_bot_audit_kf",
        "branch_id": branch_id,
    }
    # Use deposit_request_id as sale_id for downstream correlation
    headers, meta = build_correlation_headers(sale_id=deposit_request_id)
    trace_id = meta["trace_id"]
    request_header_id = meta["request_id"]

    # สร้าง log คำขอฝากเงินก่อน (pending)
    now_bkk, now_utc = now_bangkok_and_utc()
    date_bkk = now_bkk.date().isoformat()

    deposit_request_id = f"d-{uuid.uuid4().hex[:8]}"

    deposit_doc = {
        "deposit_request_id": deposit_request_id,
        "user_id": user_id,
        "amount": amount_int,
        "reason_code": reason_code,
        "reason": reason,
        "location": location_text,
        "branch_id": branch_id,
        "api_url": api_url,
        "payload": payload,
        "trace_id": trace_id,
        "request_header_id": request_header_id,
        "status": "pending",
        "created_at_bkk": now_bkk.isoformat(),
        "created_at_utc": now_utc.isoformat(),
        "created_date_bkk": date_bkk,
        "sale_id_for_machine": deposit_request_id,
        "status_history": [
            {
                "status": "pending",
                "at_bkk": now_bkk.isoformat(),
                "at_utc": now_utc.isoformat(),
                "date_bkk": date_bkk,
                "by": user_id,
            }
        ],
    }

    try:
        deposit_requests_collection.insert_one(deposit_doc)
        logger.info(f"✅ [DEPOSIT] สร้างคำขอฝากเงิน log: {deposit_request_id}")
    except Exception as e:
        logger.error(f"❌ [DEPOSIT] ไม่สามารถบันทึกคำขอฝากเงินได้: {str(e)}")
        return jsonify({"status": "error", "message": "ไม่สามารถบันทึกคำขอฝากเงินได้"}), 500

    logger.info(f"📤 [DEPOSIT] ส่งคำขอฝากเงินไปยัง {api_url} payload={payload} headers={headers}")

    try:
        response = requests.post(api_url, json=payload, headers=headers, timeout=3600)
        logger.info(f"📤 [DEPOSIT] Response Status: {response.status_code}")
        logger.info(f"📤 [DEPOSIT] Response Body: {response.text}")
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ [DEPOSIT] API Error: {str(e)}")

        now_bkk_err, now_utc_err = now_bangkok_and_utc()
        date_bkk_err = now_bkk_err.date().isoformat()

        # อัปเดต log คำขอให้เป็น error
        deposit_requests_collection.update_one(
            {"deposit_request_id": deposit_request_id},
            {
                "$set": {
                    "status": "error",
                    "error_message": str(e),
                    "updated_at_bkk": now_bkk_err.isoformat(),
                    "updated_at_utc": now_utc_err.isoformat(),
                },
                "$push": {
                    "status_history": {
                        "status": "error",
                        "at_bkk": now_bkk_err.isoformat(),
                        "at_utc": now_utc_err.isoformat(),
                        "date_bkk": date_bkk_err,
                        "by": "deposit_api",
                    }
                },
            },
        )

        return jsonify({"status": "error", "message": f"API ฝากเงินผิดพลาด: {str(e)}"}), 500

    # สำเร็จ: อัปเดต log คำขอฝากเงิน และบันทึกธุรกรรม
    now_bkk_ok, now_utc_ok = now_bangkok_and_utc()
    date_bkk_ok = now_bkk_ok.date().isoformat()

    try:
        # บันทึก response ดิบไว้ใน log (อาจยาว แต่มีประโยชน์ตอน debug)
        response_text = response.text
    except Exception:
        response_text = ""

    deposit_requests_collection.update_one(
        {"deposit_request_id": deposit_request_id},
        {
            "$set": {
                "status": "success",
                "updated_at_bkk": now_bkk_ok.isoformat(),
                "updated_at_utc": now_utc_ok.isoformat(),
                "external_response_text": response_text,
            },
            "$push": {
                "status_history": {
                    "status": "success",
                    "at_bkk": now_bkk_ok.isoformat(),
                    "at_utc": now_utc_ok.isoformat(),
                    "date_bkk": date_bkk_ok,
                    "by": "deposit_api",
                }
            },
        },
    )

    transaction_data = {
        "name": reason,
        "amount": amount_int,
        "receiptAttached": False,
        "tags": [],
        "type": "income",  # แยกจากเบิกเงินที่เป็น expense
        "selectedStorage": location_text,
        "selectedDate": date_bkk_ok,
        "transaction_at_bkk": now_bkk_ok.isoformat(),
        "transaction_at_utc": now_utc_ok.isoformat(),
        "transaction_date_bkk": date_bkk_ok,
        "direction": "deposit",
        "channel": "liff",
        "user_id": user_id,
    }

    try:
        result = transactions_collection.insert_one(transaction_data)
        logger.info(f"✅ [DEPOSIT] บันทึกธุรกรรมฝากเงิน ID: {result.inserted_id} สำเร็จ")
    except Exception as e:
        logger.error(f"❌ [DEPOSIT] บันทึกธุรกรรมฝากเงินไม่สำเร็จ: {str(e)}")
        # ไม่ต้องถือว่าเป็น error ต่อหน้าผู้ใช้ เพราะตัวฝากเงินจริงสำเร็จแล้ว

    return jsonify({"status": "ok"})
