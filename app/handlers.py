from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ButtonsTemplate, TemplateSendMessage, PostbackAction
)
from config import Config
import json

def handle_user_request(event, line_bot_api):
    """ แสดงเมนูตัวเลือกหลักเสมอ """
    
    reply_message = TemplateSendMessage(
        alt_text="กรุณาเลือกเมนู",
        template=ButtonsTemplate(
            text="กรุณาเลือกเมนูที่ต้องการ",
            actions=[
                PostbackAction(label="ขอเบิกเงินสด", data="menu|request_cash"),
                PostbackAction(label="ตรวจสอบสถานะคำขอ", data="menu|check_status")
            ]
        )
    )

    line_bot_api.reply_message(event.reply_token, reply_message)

def handle_postback(event, line_bot_api):
    """ จัดการปุ่มกดจากเมนู """
    
    data = event.postback.data
    user_id = event.source.user_id
    
    if data == "menu|request_cash":
        reply_message = TemplateSendMessage(
            alt_text="เลือกจำนวนเงินที่ต้องการเบิก",
            template=ButtonsTemplate(
                text="เลือกจำนวนเงินที่ต้องการเบิก",
                actions=[
                    PostbackAction(label="1,000 บาท", data="request_cash|1000"),
                    PostbackAction(label="5,000 บาท", data="request_cash|5000"),
                    PostbackAction(label="10,000 บาท", data="request_cash|10000"),
                    PostbackAction(label="กำหนดเอง", data="request_cash|custom")
                ]
            )
        )

    elif data.startswith("request_cash|"):
        amount = data.split("|")[1]
        if amount == "custom":
            reply_message = TextSendMessage(text="กรุณาพิมพ์จำนวนเงินที่ต้องการเบิก (ตัวเลขเท่านั้น)")
        else:
            reply_message = TextSendMessage(text=f"คุณเลือกเบิกเงินสด {amount} บาท\nกรุณารอการอนุมัติ")

    elif data == "menu|check_status":
        reply_message = TextSendMessage(text="🔎 ฟีเจอร์ตรวจสอบสถานะกำลังพัฒนา...")

    else:
        reply_message = TextSendMessage(text="❌ ไม่พบคำสั่ง กรุณาลองใหม่")

    line_bot_api.reply_message(event.reply_token, reply_message)