from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ButtonsTemplate, TemplateSendMessage, PostbackAction
)
from config import Config

# เก็บ state ของผู้ใช้
user_session = {}

def reset_state(user_id):
    """ รีเซ็ต state เมื่อเริ่มใหม่ """
    user_session[user_id] = {
        "state": "choosing_action",
        "amount": None,
        "reason": None,
        "location": None
    }

def handle_user_request(event, line_bot_api):
    """ เริ่มต้นใหม่เมื่อพิมพ์ 'เมนู' """
    user_id = event.source.user_id

    if user_id not in user_session:
        reset_state(user_id)

    reply_message = TemplateSendMessage(
        alt_text="กรุณาเลือกเมนู",
        template=ButtonsTemplate(
            text="📌 กรุณาเลือกเมนูที่ต้องการ",
            actions=[PostbackAction(label="เบิกเงินสด", data=f"menu_withdraw_cash|{user_id}")]
        )
    )
    line_bot_api.reply_message(event.reply_token, reply_message)

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

    elif action == "select_amount":
        amount = data[1]
        if amount == "custom":
            user_session[user_id]["state"] = "waiting_for_amount"
            reply_message = TextSendMessage(text="📌 กรุณาพิมพ์จำนวนเงินที่ต้องการเบิก (ตัวเลขเท่านั้น)")
        else:
            # ตรวจสอบว่าจำนวนเงินที่เลือกเป็นตัวเลขหรือไม่
            if not amount.isdigit():
                reply_message = TextSendMessage(text="⚠️ กรุณาเลือกจำนวนเงินให้ถูกต้อง")
            else:
                user_session[user_id]["amount"] = amount
                user_session[user_id]["state"] = "choosing_reason"
                reply_message = send_reason_menu(user_id)

    elif action == "select_reason":
        reason = data[1]
        if reason == "other":
            user_session[user_id]["state"] = "waiting_for_other_reason"
            reply_message = TextSendMessage(text="📌 กรุณาพิมพ์เหตุผลในการเบิกเงิน")
        else:
            user_session[user_id]["reason"] = reason
            user_session[user_id]["state"] = "waiting_for_location"
            reply_message = send_location_menu(user_id)

    elif action == "select_location":
        user_session[user_id]["location"] = data[1]
        send_summary(user_id, line_bot_api)
        reset_state(user_id)
        return

    if reply_message:
        line_bot_api.reply_message(event.reply_token, reply_message)

def handle_text_input(event, line_bot_api):
    """ จัดการข้อความที่ผู้ใช้พิมพ์ """
    user_id = event.source.user_id
    text = event.message.text.strip()

    if user_id not in user_session:
        reset_state(user_id)

    if text.lower() == "เมนู":
        reset_state(user_id)
        handle_user_request(event, line_bot_api)
        return

    current_state = user_session[user_id]["state"]

    if current_state == "waiting_for_amount" and text.isdigit():
        user_session[user_id]["amount"] = text
        user_session[user_id]["state"] = "choosing_reason"
        reply_message = send_reason_menu(user_id)

    elif current_state == "waiting_for_amount":
        if text.isdigit():  # ตรวจสอบว่าพิมพ์เป็นตัวเลข
            user_session[user_id]["amount"] = text
            user_session[user_id]["state"] = "choosing_reason"
            reply_message = send_reason_menu(user_id)
        else:
            reply_message = TextSendMessage(text="⚠️ กรุณากรอกจำนวนเงินเป็นตัวเลขเท่านั้น")

    elif current_state == "waiting_for_other_reason":
        if len(text.strip()) > 0:  # ตรวจสอบว่าผู้ใช้พิมพ์เหตุผลที่สมเหตุสมผล
            user_session[user_id]["reason"] = text
            user_session[user_id]["state"] = "waiting_for_location"
            reply_message = send_location_menu(user_id)
        else:
            reply_message = TextSendMessage(text="⚠️ กรุณากรอกเหตุผลให้ครบถ้วน")

    line_bot_api.reply_message(event.reply_token, reply_message)

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

def send_summary(user_id, line_bot_api):
    """ ส่งสรุปคำขอและแจ้งรออนุมัติ """
    amount = user_session[user_id]["amount"]
    reason = user_session[user_id]["reason"]
    location = "คลังห้องเย็น" if user_session[user_id]["location"] == "cold_storage" else "โนนิโกะ"

    summary_text = (
        f"✅ คำขอเบิกเงินถูกบันทึกและรอการอนุมัติ\n"
        f"💰 จำนวนเงิน: {amount} บาท\n"
        f"📌 เหตุผล: {reason}\n"
        f"📍 สถานที่รับเงิน: {location}\n"
        f"🔄 กรุณารอการอนุมัติจากผู้ดูแล"
    )

    line_bot_api.push_message(user_id, TextSendMessage(text=summary_text))