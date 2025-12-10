import uuid
import logging
import requests
from pymongo import MongoClient
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ButtonsTemplate, TemplateSendMessage, PostbackAction, URITemplateAction
)
from config import Config
from http_utils import build_correlation_headers, get_rest_api_ci_base_for_branch
from db import requests_collection, deposit_requests_collection, transactions_collection  # ✅ ใช้ connection pool
from time_utils import now_bangkok_and_utc

# ✅ ตั้งค่า Logging ให้ใช้งานได้
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)  # ✅ แก้ไขให้ประกาศ logger ที่นี่

# เก็บ state ของผู้ใช้
user_session = {}

def reset_state(user_id):
    """ รีเซ็ต state เมื่อเริ่มใหม่ """
    user_session[user_id] = {
        "state": "choosing_action",
        "amount": None,
        "reason": None,
        "license_plate": None,  # เพิ่มช่องหมายเลขทะเบียน
        "location": None,
        "request_id": None
    }

def generate_request_id():
    """ สร้างหมายเลขคำขอที่เป็น Unique """
    return str(uuid.uuid4())[:8]  # ใช้แค่ 8 ตัวอักษรแรกของ UUID

def handle_user_request(event, line_bot_api):
    """ เริ่มต้นใหม่เมื่อพิมพ์ 'เมนู' """
    user_id = event.source.user_id

    if user_id not in user_session:
        reset_state(user_id)

    reply_message = TemplateSendMessage(
        alt_text="กรุณาเลือกเมนู",
        template=ButtonsTemplate(
            text="📌 กรุณาเลือกเมนูที่ต้องการ",
            actions=[
                PostbackAction(label="เบิกเงินสด", data=f"menu_withdraw_cash|{user_id}"),
                PostbackAction(label="ฝากเงินสด", data=f"deposit_cash|{user_id}")
                ]
        )
    )
    line_bot_api.reply_message(event.reply_token, reply_message)


def send_location_menu(user_id):
    """ ส่งเมนูให้เลือกสถานที่รับเงิน """
    return TemplateSendMessage(
        alt_text="เลือกสถานที่รับเงิน",
        template=ButtonsTemplate(
            text="📌 กรุณาเลือกสถานที่รับเงิน",
            actions=[
                PostbackAction(label="คลังห้องเย็น", data=f"select_location|cold_storage|{user_id}"),
                PostbackAction(label="โนนิโกะ", data=f"select_location|noniko|{user_id}")
            ]
        )
    )

def send_reason_menu(user_id):
    """ ส่งเมนูให้เลือกเหตุผลในการเบิกเงิน """
    return TemplateSendMessage(
        alt_text="เลือกเหตุผลในการเบิกเงิน",
        template=ButtonsTemplate(
            text="📌 กรุณาเลือกเหตุผลในการเบิกเงิน",
            actions=[
                PostbackAction(label="ซื้อน้ำแข็ง", data=f"select_reason|ice|{user_id}"),
                PostbackAction(label="เติมน้ำมัน", data=f"select_reason|fuel|{user_id}"),
                PostbackAction(label="อื่นๆ", data=f"select_reason|other|{user_id}")
            ]
        )
    )

def send_reason_deposit_menu(user_id):
    """ ส่งเมนูให้เลือกเหตุผลในการเบิกเงิน """
    return TemplateSendMessage(
        alt_text="เลือกเหตุผลในการฝากเงิน",
        template=ButtonsTemplate(
            text="📌 กรุณาเลือกเหตุผลในการฝากเงิน",
            actions=[
                PostbackAction(label="เงินทอน", data=f"select_reason_deposit|change|{user_id}"),
                PostbackAction(label="ฝากยอดขาย", data=f"select_reason_deposit|daily_sales|{user_id}"),
                PostbackAction(label="อื่นๆ", data=f"select_reason_deposit|other_deposit|{user_id}")
            ]
        )
    )

def handle_postback(event, line_bot_api):
    """ จัดการปุ่มกด """
    data = event.postback.data.split("|")
    action = data[0]
    user_id = data[-1]

    if user_id not in user_session:
        reset_state(user_id)

    reply_message = None

    if action == "menu_withdraw_cash":
        user_session[user_id]["state"] = "choosing_amount"
        reply_message = TemplateSendMessage(
            alt_text="เลือกจำนวนเงินที่ต้องการเบิก",
            template=ButtonsTemplate(
                text="📌 กรุณาเลือกจำนวนเงินที่ต้องการเบิก",
                actions=[
                    PostbackAction(label="40 บาท", data=f"select_amount|40|{user_id}"),
                    PostbackAction(label="80 บาท", data=f"select_amount|80|{user_id}"),
                    PostbackAction(label="100 บาท", data=f"select_amount|100|{user_id}"),
                    PostbackAction(label="กรอกเอง", data=f"select_amount|custom|{user_id}")
                ]
            )
        )

    elif action == "deposit_cash":
        user_session[user_id]["state"] = "waiting_for_deposit_amount"
        reply_message = TextSendMessage(text="📌 กรุณาพิมพ์จำนวนเงินที่ต้องการฝาก (ตัวเลขเท่านั้น)")

    elif action == "select_reason_deposit":
        reason = data[1]
        user_session[user_id]["reason"] = reason
        if reason == "other_deposit":
            user_session[user_id]["state"] = "waiting_for_location_deposit"
            reply_message = TextSendMessage(text="📌 กรุณาระบุเหตุผลที่ฝากเงิน")
        else:
            user_session[user_id]["state"] = "waiting_for_location_deposit"
            reply_message = send_location_menu(user_id)


    elif action == "select_amount":
        amount = data[1]
        if amount == "custom":
            user_session[user_id]["state"] = "waiting_for_amount"
            reply_message = TextSendMessage(text="📌 กรุณาพิมพ์จำนวนเงินที่ต้องการเบิก (ตัวเลขเท่านั้น)")
        else:
            if not amount.isdigit():
                reply_message = TextSendMessage(text="⚠️ กรุณาเลือกจำนวนเงินให้ถูกต้อง")
            else:
                user_session[user_id]["amount"] = amount
                user_session[user_id]["state"] = "choosing_reason"
                reply_message = send_reason_menu(user_id)

    elif action == "select_reason":
        reason = data[1]
        user_session[user_id]["reason"] = reason

        if reason == "fuel":
            user_session[user_id]["state"] = "waiting_for_license_plate"
            reply_message = TextSendMessage(text="📌 กรุณากรอกหมายเลขทะเบียนรถ")
        elif reason == "other":
            user_session[user_id]["state"] = "waiting_for_other_reason"
            reply_message = TextSendMessage(text="📌 กรุณาพิมพ์เหตุผลในการเบิกเงิน")
        else:
            user_session[user_id]["state"] = "waiting_for_location"
            reply_message = send_location_menu(user_id)

    elif action == "select_location":
        location = user_session[user_id]["location"] = data[1]
        amount = user_session[user_id]["amount"]
        reson = user_session[user_id]["reason"]
        state = user_session[user_id]["state"]
        logger.info(f"❌ สถานะ {state} ตอนนี้")
        if  state == "waiting_for_location_deposit" and location == "noniko":
            # แม็ปเหตุผลให้เป็นข้อความอ่านง่าย
            if reson == "change":
                reason_text = "เงินทอน"
            elif reson == "daily_sales":
                reason_text = "ฝากยอดขาย"
            else:
                reason_text = reson if isinstance(reson, str) else str(reson)
            
            location_text = "โนนิโกะ"
            branch_id = "NONIKO"
            base_url = get_rest_api_ci_base_for_branch('NONIKO')
            
            # สร้าง deposit_request_id และ correlation headers
            deposit_request_id = f"d-{uuid.uuid4().hex[:8]}"
            headers, meta = build_correlation_headers(sale_id=deposit_request_id)
            trace_id = meta["trace_id"]
            request_header_id = meta["request_id"]
            
            # บันทึกคำขอฝากเงินลง MongoDB ก่อน
            now_bkk, now_utc = now_bangkok_and_utc()
            date_bkk = now_bkk.date().isoformat()
            
            # สร้าง session_id และ seq_no สำหรับ replenishment
            session_id = deposit_request_id
            seq_no = "1"
            
            deposit_doc = {
                "deposit_request_id": deposit_request_id,
                "user_id": user_id,
                "amount": int(amount) if amount else None,
                "reason_code": reson,
                "reason": reason_text,
                "location": location_text,
                "branch_id": branch_id,
                "trace_id": trace_id,
                "request_header_id": request_header_id,
                "session_id": session_id,
                "seq_no": seq_no,
                "status": "replenishment_started",
                "created_at_bkk": now_bkk.isoformat(),
                "created_at_utc": now_utc.isoformat(),
                "created_date_bkk": date_bkk,
                "channel": "line_bot",
                "status_history": [
                    {
                        "status": "replenishment_started",
                        "at_bkk": now_bkk.isoformat(),
                        "at_utc": now_utc.isoformat(),
                        "date_bkk": date_bkk,
                        "by": user_id,
                    }
                ],
            }
            
            try:
                deposit_requests_collection.insert_one(deposit_doc)
                logger.info(f"✅ [DEPOSIT] บันทึกคำขอฝากเงิน (โนนิโกะ): {deposit_request_id}")
            except Exception as e:
                logger.error(f"❌ [DEPOSIT] ไม่สามารถบันทึกคำขอฝากเงินได้: {str(e)}")
            
            # ยิง API /replenishment/start
            try:
                replenishment_start_url = f"{base_url}/replenishment/start"
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
                    now_bkk_err, now_utc_err = now_bangkok_and_utc()
                    deposit_requests_collection.update_one(
                        {"deposit_request_id": deposit_request_id},
                        {
                            "$set": {
                                "status": "error",
                                "error_message": f"/replenishment/start failed: {error_msg}",
                                "updated_at_bkk": now_bkk_err.isoformat(),
                                "updated_at_utc": now_utc_err.isoformat(),
                            },
                            "$push": {
                                "status_history": {
                                    "status": "error",
                                    "at_bkk": now_bkk_err.isoformat(),
                                    "at_utc": now_utc_err.isoformat(),
                                    "date_bkk": now_bkk_err.date().isoformat(),
                                    "by": "line_bot_handler",
                                }
                            },
                        },
                    )
                    text = (
                        f"❌ คำขอฝากเงิน\n"
                        f"📌 เหตุผล: {reason_text}\n"
                        f"📍 สถานที่: {location_text}\n"
                        f"⚠️ เกิดข้อผิดพลาดในการเริ่มต้นการฝากเงิน"
                    )
                else:
                    logger.info(f"✅ [DEPOSIT] /replenishment/start สำเร็จ: {start_data}")
                    # เก็บ session_id และ seq_no ใน user_session เพื่อใช้ในหน้าถัดไป
                    user_session[user_id]["deposit_request_id"] = deposit_request_id
                    user_session[user_id]["session_id"] = session_id
                    user_session[user_id]["seq_no"] = seq_no
                    user_session[user_id]["branch_base_url"] = base_url
                    user_session[user_id]["replenishment_status"] = "active"
                    
                    # ส่งข้อความพร้อมลิงก์ไปหน้า UI สำหรับแสดงยอดเงิน
                    text = (
                        f"✅ เริ่มต้นการฝากเงิน\n"
                        f"📌 เหตุผล: {reason_text}\n"
                        f"📍 สถานที่: {location_text}\n"
                        f"🔄 กรุณาเปิดลิงก์เพื่อดูยอดเงินที่ฝาก"
                    )
                    reply_message = TemplateSendMessage(
                        alt_text="เริ่มต้นการฝากเงิน",
                        template=ButtonsTemplate(
                            text=text,
                            actions=[
                                URITemplateAction(
                                    label="ดูยอดเงินที่ฝาก",
                                    uri=f"https://liff.line.me/2005595780-lYJx1JyJ/money/deposit-monitor?deposit_id={deposit_request_id}"
                                )
                            ]
                        )
                    )
                    reset_state(user_id)
                    return reply_message
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ [DEPOSIT] Request Exception: {str(e)}")
                now_bkk_err, now_utc_err = now_bangkok_and_utc()
                deposit_requests_collection.update_one(
                    {"deposit_request_id": deposit_request_id},
                    {
                        "$set": {
                            "status": "error",
                            "error_message": f"Request exception: {str(e)}",
                            "updated_at_bkk": now_bkk_err.isoformat(),
                            "updated_at_utc": now_utc_err.isoformat(),
                        },
                        "$push": {
                            "status_history": {
                                "status": "error",
                                "at_bkk": now_bkk_err.isoformat(),
                                "at_utc": now_utc_err.isoformat(),
                                "date_bkk": now_bkk_err.date().isoformat(),
                                "by": "line_bot_handler",
                            }
                        },
                    },
                )
                text = (
                    f"❌ คำขอฝากเงิน\n"
                    f"📌 เหตุผล: {reason_text}\n"
                    f"📍 สถานที่: {location_text}\n"
                    f"⚠️ เกิดข้อผิดพลาดในการเริ่มต้นการฝากเงิน"
                )
            except Exception as e:
                logger.error(f"❌ [DEPOSIT] Error (โนนิโกะ): {str(e)}")
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
                                "by": "line_bot_handler",
                            }
                        },
                    },
                )
                text = (
                    f"⚠️ คำขอฝากเงิน\n"
                    f"📌 เหตุผล: {reason_text}\n"
                    f"📍 สถานที่: {location_text}\n"
                    f"❌ เกิดข้อผิดพลาดในการฝากเงิน กรุณาลองใหม่อีกครั้ง"
                )
            
            reset_state(user_id)
            reply_message = TextSendMessage(text=text)
        elif  state == "waiting_for_location_deposit" and location == "cold_storage":
            # แม็ปเหตุผลให้เป็นข้อความอ่านง่าย
            if reson == "change":
                reason_text = "เงินทอน"
            elif reson == "daily_sales":
                reason_text = "ฝากยอดขาย"
            else:
                reason_text = reson if isinstance(reson, str) else str(reson)
            
            location_text = "คลังห้องเย็น"
            branch_id = "Klangfrozen"
            base_url = get_rest_api_ci_base_for_branch('Klangfrozen')
            
            # สร้าง deposit_request_id และ correlation headers
            deposit_request_id = f"d-{uuid.uuid4().hex[:8]}"
            headers, meta = build_correlation_headers(sale_id=deposit_request_id)
            trace_id = meta["trace_id"]
            request_header_id = meta["request_id"]
            
            # บันทึกคำขอฝากเงินลง MongoDB ก่อน
            now_bkk, now_utc = now_bangkok_and_utc()
            date_bkk = now_bkk.date().isoformat()
            
            # สร้าง session_id และ seq_no สำหรับ replenishment
            session_id = deposit_request_id
            seq_no = "1"
            
            deposit_doc = {
                "deposit_request_id": deposit_request_id,
                "user_id": user_id,
                "amount": int(amount) if amount else None,
                "reason_code": reson,
                "reason": reason_text,
                "location": location_text,
                "branch_id": branch_id,
                "trace_id": trace_id,
                "request_header_id": request_header_id,
                "session_id": session_id,
                "seq_no": seq_no,
                "status": "replenishment_started",
                "created_at_bkk": now_bkk.isoformat(),
                "created_at_utc": now_utc.isoformat(),
                "created_date_bkk": date_bkk,
                "channel": "line_bot",
                "status_history": [
                    {
                        "status": "replenishment_started",
                        "at_bkk": now_bkk.isoformat(),
                        "at_utc": now_utc.isoformat(),
                        "date_bkk": date_bkk,
                        "by": user_id,
                    }
                ],
            }
            
            try:
                deposit_requests_collection.insert_one(deposit_doc)
                logger.info(f"✅ [DEPOSIT] บันทึกคำขอฝากเงิน (คลังห้องเย็น): {deposit_request_id}")
            except Exception as e:
                logger.error(f"❌ [DEPOSIT] ไม่สามารถบันทึกคำขอฝากเงินได้: {str(e)}")
            
            # ยิง API /replenishment/start
            try:
                replenishment_start_url = f"{base_url}/replenishment/start"
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
                    now_bkk_err, now_utc_err = now_bangkok_and_utc()
                    deposit_requests_collection.update_one(
                        {"deposit_request_id": deposit_request_id},
                        {
                            "$set": {
                                "status": "error",
                                "error_message": f"/replenishment/start failed: {error_msg}",
                                "updated_at_bkk": now_bkk_err.isoformat(),
                                "updated_at_utc": now_utc_err.isoformat(),
                            },
                            "$push": {
                                "status_history": {
                                    "status": "error",
                                    "at_bkk": now_bkk_err.isoformat(),
                                    "at_utc": now_utc_err.isoformat(),
                                    "date_bkk": now_bkk_err.date().isoformat(),
                                    "by": "line_bot_handler",
                                }
                            },
                        },
                    )
                    text = (
                        f"❌ คำขอฝากเงิน\n"
                        f"📌 เหตุผล: {reason_text}\n"
                        f"📍 สถานที่: {location_text}\n"
                        f"⚠️ เกิดข้อผิดพลาดในการเริ่มต้นการฝากเงิน"
                    )
                else:
                    logger.info(f"✅ [DEPOSIT] /replenishment/start สำเร็จ: {start_data}")
                    # เก็บ session_id และ seq_no ใน user_session เพื่อใช้ในหน้าถัดไป
                    user_session[user_id]["deposit_request_id"] = deposit_request_id
                    user_session[user_id]["session_id"] = session_id
                    user_session[user_id]["seq_no"] = seq_no
                    user_session[user_id]["branch_base_url"] = base_url
                    user_session[user_id]["replenishment_status"] = "active"
                    
                    # ส่งข้อความพร้อมลิงก์ไปหน้า UI สำหรับแสดงยอดเงิน
                    text = (
                        f"✅ เริ่มต้นการฝากเงิน\n"
                        f"📌 เหตุผล: {reason_text}\n"
                        f"📍 สถานที่: {location_text}\n"
                        f"🔄 กรุณาเปิดลิงก์เพื่อดูยอดเงินที่ฝาก"
                    )
                    reply_message = TemplateSendMessage(
                        alt_text="เริ่มต้นการฝากเงิน",
                        template=ButtonsTemplate(
                            text=text,
                            actions=[
                                URITemplateAction(
                                    label="ดูยอดเงินที่ฝาก",
                                    uri=f"https://liff.line.me/2005595780-lYJx1JyJ/money/deposit-monitor?deposit_id={deposit_request_id}"
                                )
                            ]
                        )
                    )
                    reset_state(user_id)
                    return reply_message
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ [DEPOSIT] Request Exception: {str(e)}")
                now_bkk_err, now_utc_err = now_bangkok_and_utc()
                deposit_requests_collection.update_one(
                    {"deposit_request_id": deposit_request_id},
                    {
                        "$set": {
                            "status": "error",
                            "error_message": f"Request exception: {str(e)}",
                            "updated_at_bkk": now_bkk_err.isoformat(),
                            "updated_at_utc": now_utc_err.isoformat(),
                        },
                        "$push": {
                            "status_history": {
                                "status": "error",
                                "at_bkk": now_bkk_err.isoformat(),
                                "at_utc": now_utc_err.isoformat(),
                                "date_bkk": now_bkk_err.date().isoformat(),
                                "by": "line_bot_handler",
                            }
                        },
                    },
                )
                text = (
                    f"❌ คำขอฝากเงิน\n"
                    f"📌 เหตุผล: {reason_text}\n"
                    f"📍 สถานที่: {location_text}\n"
                    f"⚠️ เกิดข้อผิดพลาดในการเริ่มต้นการฝากเงิน"
                )
            except Exception as e:
                logger.error(f"❌ [DEPOSIT] Error (คลังห้องเย็น): {str(e)}")
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
                                "by": "line_bot_handler",
                            }
                        },
                    },
                )
                text = (
                    f"⚠️ คำขอฝากเงิน\n"
                    f"📌 เหตุผล: {reason_text}\n"
                    f"📍 สถานที่: {location_text}\n"
                    f"❌ เกิดข้อผิดพลาดในการฝากเงิน กรุณาลองใหม่อีกครั้ง"
                )
            
            reset_state(user_id)
            reply_message = TextSendMessage(text=text)
        elif state == "waiting_for_location": 
            send_summary(user_id, line_bot_api)
            return  # ไม่ reset state ที่นี่ เพราะต้องให้ตรวจสอบข้อมูลก่อน

    if reply_message:
        line_bot_api.reply_message(event.reply_token, reply_message)

def handle_text_input(event, line_bot_api):
    """ จัดการข้อความที่ผู้ใช้พิมพ์ """
    user_id = event.source.user_id
    text = event.message.text.strip()
    reply_message = None

    if user_id not in user_session:
        reset_state(user_id)

    if text.lower() == "เมนู":
        reset_state(user_id)
        handle_user_request(event, line_bot_api)
        return
    elif text.lower() == "ขอไอดี":
        reset_state(user_id)
        reply_message = TextSendMessage(text=f"⚠️ {user_id}")

    current_state = user_session[user_id]["state"]

    if current_state == "waiting_for_amount":
        if text.isdigit():
            user_session[user_id]["amount"] = text
            user_session[user_id]["state"] = "choosing_reason"
            reply_message = send_reason_menu(user_id)
        else:
            reply_message = TextSendMessage(text="⚠️ กรุณากรอกจำนวนเงินเป็นตัวเลขเท่านั้น")

    elif current_state == "waiting_for_deposit_amount":
        if text.isdigit():
            user_session[user_id]["amount"] = text
            user_session[user_id]["state"] = "choosing_reason_deposit"
            reply_message = send_reason_deposit_menu(user_id)
        else:
            reply_message = TextSendMessage(text="⚠️ กรุณากรอกจำนวนเงินเป็นตัวเลขเท่านั้น")

    elif current_state == "waiting_for_license_plate":
        if len(text.strip()) > 0:
            user_session[user_id]["license_plate"] = text
            user_session[user_id]["state"] = "waiting_for_location"
            reply_message = send_location_menu(user_id)
        else:
            reply_message = TextSendMessage(text="⚠️ กรุณากรอกหมายเลขทะเบียนรถ")

    elif current_state == "waiting_for_other_reason":
        if len(text.strip()) > 0:
            user_session[user_id]["reason"] = text
            user_session[user_id]["state"] = "waiting_for_location"
            reply_message = send_location_menu(user_id)
        else:
            reply_message = TextSendMessage(text="⚠️ กรุณากรอกเหตุผลให้ครบถ้วน")
    elif current_state == "waiting_for_location_deposit":
        if len(text.strip()) > 0:
            user_session[user_id]["reason"] = text
            user_session[user_id]["state"] = "waiting_for_location_deposit"
            reply_message = send_location_menu(user_id)
        else:
            reply_message = TextSendMessage(text="⚠️ กรุณากรอกเหตุผลให้ครบถ้วน")
    if reply_message:
        line_bot_api.reply_message(event.reply_token, reply_message)

def send_summary(user_id, line_bot_api):
    """ ตรวจสอบข้อมูลก่อนบันทึกลง MongoDB และส่งสรุปคำขอ """

    # ตรวจสอบว่าข้อมูลครบถ้วนหรือไม่
    amount = user_session[user_id].get("amount")
    reason = user_session[user_id].get("reason")
    location = user_session[user_id].get("location")
    license_plate = user_session[user_id].get("license_plate") if reason == "fuel" else None

    if not amount or not reason or not location or (reason == "fuel" and not license_plate):
        reset_state(user_id)
        line_bot_api.push_message(user_id, TextSendMessage(text="⚠️ ข้อมูลไม่ครบ กรุณากรอกข้อมูลใหม่ตั้งแต่ต้น"))
        return

    # เวลาปัจจุบันตาม timezone กรุงเทพ และ UTC
    now_bkk, now_utc = now_bangkok_and_utc()
    date_bkk = now_bkk.date().isoformat()

    request_id = generate_request_id()
    user_session[user_id]["request_id"] = request_id

    location_text = "คลังห้องเย็น" if location == "cold_storage" else "โนนิโกะ"

    request_data = {
        "request_id": request_id,
        "user_id": user_id,
        "amount": amount,
        "reason": reason,
        "license_plate": license_plate,
        "location": location_text,
        "status": "pending",
        # เวลาสร้างคำขอ
        "created_at_bkk": now_bkk.isoformat(),
        "created_at_utc": now_utc.isoformat(),
        "created_date_bkk": date_bkk,
        # ประวัติสถานะเพื่อใช้ทำรายงาน/ตรวจสอบย้อนหลัง
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
    requests_collection.insert_one(request_data)

    summary_text = (
        f"✅ คำขอเบิกเงินถูกบันทึกและรอการอนุมัติ\n"
        f"📌 หมายเลขคำขอ: {request_id}\n"
        f"💰 จำนวนเงิน: {amount} บาท\n"
        f"📌 เหตุผล: {reason}\n"
        f"🚗 หมายเลขทะเบียน: {license_plate if license_plate else '-'}\n"
        f"📍 สถานที่รับเงิน: {location_text}\n"
        f"📅 วันที่ขอ (เวลาไทย): {date_bkk}\n"
        f"🔄 กรุณารอการอนุมัติจากผู้ดูแล"
    )
    reset_state(user_id)
    line_bot_api.push_message(user_id, TextSendMessage(text=summary_text))