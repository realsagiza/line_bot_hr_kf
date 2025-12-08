import uuid
import logging
import requests
from pymongo import MongoClient
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ButtonsTemplate, TemplateSendMessage, PostbackAction
)
from config import Config
from http_utils import build_correlation_headers, get_rest_api_ci_base_for_branch
from db import requests_collection  # ✅ ใช้ connection pool
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
            text = (
                f"✅ คำขอฝากเงิน\n"
                f"💰 จำนวนเงิน: {amount} บาท\n"
                f"📌 เหตุผล: {reson}\n"
                f"📍 สถานที่รับเงิน: {location}\n"
                f"🔄 ฝากเงินทอนสำเร็จแล้ว"
            )
            api_url = f"{get_rest_api_ci_base_for_branch('NONIKO')}/bot/deposit"
            payload = {
                "amount": int(amount),  # ✅ แปลงเป็น int
                "machine_id": "line_bot_audit_kf",
                "branch_id": "NONIKO"
            }
            # Generate correlation headers (no pre-existing sale id for this quick flow)
            headers, _meta = build_correlation_headers()

            response = requests.post(api_url, json=payload, headers=headers, timeout=3600)
            reset_state(user_id)
            reply_message = TextSendMessage(text=text)
        elif  state == "waiting_for_location_deposit" and location == "cold_storage":
            text = (
                f"✅ คำขอฝากเงิน\n"
                f"💰 จำนวนเงิน: {amount} บาท\n"
                f"📌 เหตุผล: {reson}\n"
                f"📍 สถานที่รับเงิน: {location}\n"
                f"🔄 ฝากเงินทอนสำเร็จแล้ว"
            )
            api_url = f"{get_rest_api_ci_base_for_branch('Klangfrozen')}/bot/deposit"
            payload = {
                "amount": int(amount),  # ✅ แปลงเป็น int
                "machine_id": "line_bot_audit_kf",
                "branch_id": "Klangfrozen"
            }
            headers, _meta = build_correlation_headers()

            response = requests.post(api_url, json=payload, headers=headers, timeout=3600)
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