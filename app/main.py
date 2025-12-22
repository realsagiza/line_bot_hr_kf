from flask import Flask, request
from linebot import WebhookHandler
from linebot.exceptions import InvalidSignatureError
from config import Config

app = Flask(__name__)

handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

@app.route("/money/webhook", methods=["POST"])
def webhook():
    # Deprecated: chat-based withdraw/deposit flow removed (use LIFF UI instead)
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid Signature", 400

    return "OK", 200

from approved_requests import approved_requests_bp  # noqa: E402

app.register_blueprint(approved_requests_bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5010, debug=True)