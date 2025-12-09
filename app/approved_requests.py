import uuid
import requests
import logging
import json
import requests
import threading
from flask import Blueprint, render_template, jsonify, redirect, url_for, request
from db import requests_collection, deposit_requests_collection, transactions_collection
from time_utils import now_bangkok, now_bangkok_and_utc
from http_utils import build_correlation_headers, get_rest_api_ci_base_for_branch

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
            # Fire-and-forget: send request without waiting for response
            # Status will be checked via polling
            try:
                requests.post(api_url, json=payload, headers=headers, timeout=10)
                logger.info(f"📤 [WITHDRAW] Request sent successfully (fire-and-forget)")
            except Exception as e_send:
                logger.error(f"📤 [WITHDRAW] Failed to send request: {str(e_send)}")
                # อัปเดตสถานะเป็น error
                now_bkk, now_utc = now_bangkok_and_utc()
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "status": "error",
                            "machine_error": str(e_send),
                            "updated_at_bkk": now_bkk.isoformat(),
                            "updated_at_utc": now_utc.isoformat(),
                        },
                        "$push": {
                            "status_history": {
                                "status": "error",
                                "at_bkk": now_bkk.isoformat(),
                                "at_utc": now_utc.isoformat(),
                                "date_bkk": now_bkk.date().isoformat(),
                                "by": "approver_ui",
                            }
                        },
                    },
                )
                return jsonify({"status": "error", "message": f"Failed to send request: {str(e_send)}"}), 500
            
            # In fire-and-forget mode, we don't wait for response
            # Update status to pending and return immediately
            now_bkk, now_utc = now_bangkok_and_utc()
            date_bkk = now_bkk.date().isoformat()

            # ✅ อัปเดตสถานะเป็น "pending" ในฐานข้อมูล พร้อมเก็บเวลาและประวัติสถานะ
            requests_collection.update_one(
                {"request_id": request_id},
                {
                    "$set": {
                        "status": "pending",
                        "updated_at_bkk": now_bkk.isoformat(),
                        "updated_at_utc": now_utc.isoformat(),
                    },
                    "$push": {
                        "status_history": {
                            "status": "pending",
                            "at_bkk": now_bkk.isoformat(),
                            "at_utc": now_utc.isoformat(),
                            "date_bkk": date_bkk,
                            "by": "approver_ui",
                        }
                    },
                },
            )
            
            logger.info(f"✅ อนุมัติคำขอ {request_id} - Request sent (fire-and-forget)")
            return redirect("/money/approved-requests")

        except Exception as e:
            logger.error(f"❌ [WITHDRAW] Error: {str(e)}")
            return jsonify({"status": "error", "message": str(e)}), 500
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
            # Fire-and-forget: send request without waiting for response
            # Status will be checked via polling
            try:
                requests.post(api_url, json=payload, headers=headers, timeout=10)
                logger.info(f"📤 [WITHDRAW] Request sent successfully (fire-and-forget)")
            except Exception as e_send:
                logger.error(f"📤 [WITHDRAW] Failed to send request: {str(e_send)}")
                # อัปเดตสถานะเป็น error
                now_bkk, now_utc = now_bangkok_and_utc()
                requests_collection.update_one(
                    {"request_id": request_id},
                    {
                        "$set": {
                            "status": "error",
                            "machine_error": str(e_send),
                            "updated_at_bkk": now_bkk.isoformat(),
                            "updated_at_utc": now_utc.isoformat(),
                        },
                        "$push": {
                            "status_history": {
                                "status": "error",
                                "at_bkk": now_bkk.isoformat(),
                                "at_utc": now_utc.isoformat(),
                                "date_bkk": now_bkk.date().isoformat(),
                                "by": "approver_ui",
                            }
                        },
                    },
                )
                return jsonify({"status": "error", "message": f"Failed to send request: {str(e_send)}"}), 500
            
            # In fire-and-forget mode, we don't wait for response
            # Update status to pending and return immediately
            now_bkk, now_utc = now_bangkok_and_utc()
            date_bkk = now_bkk.date().isoformat()

            # ✅ อัปเดตสถานะเป็น "pending" ในฐานข้อมูล พร้อมเก็บเวลาและประวัติสถานะ
            requests_collection.update_one(
                {"request_id": request_id},
                {
                    "$set": {
                        "status": "pending",
                        "updated_at_bkk": now_bkk.isoformat(),
                        "updated_at_utc": now_utc.isoformat(),
                    },
                    "$push": {
                        "status_history": {
                            "status": "pending",
                            "at_bkk": now_bkk.isoformat(),
                            "at_utc": now_utc.isoformat(),
                            "date_bkk": date_bkk,
                            "by": "approver_ui",
                        }
                    },
                },
            )
            
            logger.info(f"✅ อนุมัติคำขอ {request_id} - Request sent (fire-and-forget)")
            return redirect("/money/approved-requests")

        except Exception as e:
            logger.error(f"❌ [WITHDRAW] Error: {str(e)}")
            return jsonify({"status": "error", "message": str(e)}), 500

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
    deposit_request_id = f"d-{uuid.uuid4().hex[:8]}"
    headers, meta = build_correlation_headers(sale_id=deposit_request_id)
    trace_id = meta["trace_id"]
    request_header_id = meta["request_id"]

    # สร้าง log คำขอฝากเงินก่อน (pending)
    now_bkk, now_utc = now_bangkok_and_utc()
    date_bkk = now_bkk.date().isoformat()

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

    # ประมวลผล async ใน background thread (fire-and-forget mode)
    def _process_deposit_async():
        logger.info(f"📤 [DEPOSIT] (async) ส่งคำขอฝากเงินไปยัง {api_url} payload={payload} headers={headers}")
        try:
            # Fire-and-forget: send request without waiting for response
            # Status will be checked via polling
            try:
                requests.post(api_url, json=payload, headers=headers, timeout=10)
                logger.info(f"📤 [DEPOSIT] (async) Request sent successfully")
            except Exception as e_send:
                logger.error(f"📤 [DEPOSIT] (async) Failed to send request: {str(e_send)}")
                # Update status to error
                try:
                    now_bkk_err, now_utc_err = now_bangkok_and_utc()
                    date_bkk_err = now_bkk_err.date().isoformat()
                    deposit_requests_collection.update_one(
                        {"deposit_request_id": deposit_request_id},
                        {
                            "$set": {
                                "status": "error",
                                "error_message": f"Failed to send request: {str(e_send)}",
                                "updated_at_bkk": now_bkk_err.isoformat(),
                                "updated_at_utc": now_utc_err.isoformat(),
                            },
                            "$push": {
                                "status_history": {
                                    "status": "error",
                                    "at_bkk": now_bkk_err.isoformat(),
                                    "at_utc": now_utc_err.isoformat(),
                                    "date_bkk": date_bkk_err,
                                    "by": "deposit_api_async",
                                }
                            },
                        },
                    )
                except Exception:
                    pass
                return
            
            # In fire-and-forget mode, we don't wait for response
            # Status will be checked via polling
            return
        except Exception as e_http:
            # Handle any unexpected errors
            logger.error(f"📤 [DEPOSIT] (async) Unexpected error: {str(e_http)}")
            try:
                now_bkk_err, now_utc_err = now_bangkok_and_utc()
                date_bkk_err = now_bkk_err.date().isoformat()
                deposit_requests_collection.update_one(
                    {"deposit_request_id": deposit_request_id},
                    {
                        "$set": {
                            "status": "error",
                            "error_message": f"Unexpected error: {str(e_http)}",
                            "updated_at_bkk": now_bkk_err.isoformat(),
                            "updated_at_utc": now_utc_err.isoformat(),
                        },
                        "$push": {
                            "status_history": {
                                "status": "error",
                                "at_bkk": now_bkk_err.isoformat(),
                                "at_utc": now_utc_err.isoformat(),
                                "date_bkk": date_bkk_err,
                                "by": "deposit_api_async",
                            }
                        },
                    },
                )
            except Exception:
                pass
            return
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ [DEPOSIT] (async) API Error: {str(e)}")
            now_bkk_err, now_utc_err = now_bangkok_and_utc()
            date_bkk_err = now_bkk_err.date().isoformat()
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
                            "by": "deposit_api_async",
                        }
                    },
                },
            )

    threading.Thread(target=_process_deposit_async, name=f"deposit-{deposit_request_id}", daemon=True).start()
    return jsonify({"status": "ok", "deposit_request_id": deposit_request_id})

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
