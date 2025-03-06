from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, PostbackEvent
from config import Config
from handlers import handle_user_request, handle_postback, handle_text_input  # ✅ เพิ่ม handle_text_input ที่หายไป

app = Flask(__name__)

line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid Signature", 400

    return "OK", 200

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    handle_text_input(event, line_bot_api)  # ✅ ตอนนี้ฟังก์ชันนี้ถูก import แล้ว

@handler.add(PostbackEvent)
def handle_postback_event(event):
    handle_postback(event, line_bot_api)

if __name__ == "__main__":
    from approved_requests import approved_requests_bp  # ✅ Import blueprint สำหรับ API อนุมัติ
    app.register_blueprint(approved_requests_bp)  # ✅ เพิ่ม API สำหรับดึงรายการที่อนุมัติ
    app.run(host="0.0.0.0", port=5001, debug=True)