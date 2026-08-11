import os
import requests
import io
import telebot

# Read tokens and URLs from environment variables
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
COLAB_API_URL = os.environ.get("COLAB_API_URL", "https://YOUR-NGROK-URL.ngrok-free.app")

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN or TELEGRAM_TOKEN environment variable is missing.")

bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    if "YOUR-NGROK-URL" in COLAB_API_URL:
        bot.reply_to(message, "⚠️ `COLAB_API_URL` is set to placeholder.\nPlease set your active ngrok URL in Render Environment Variables!")
        return

    bot.reply_to(message, "Generating audio with VoxCPM... 🎙️")
    
    headers = {
        "ngrok-skip-browser-warning": "69420",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }

    base_url = COLAB_API_URL.rstrip("/")
    endpoints = [base_url if base_url.endswith("/generate") else f"{base_url}/generate", base_url]

    audio_bytes = None
    last_err = None

    for ep in endpoints:
        try:
            # 1. Try sending JSON payload
            resp = requests.post(ep, json={"text": message.text}, headers=headers, timeout=60)
            if resp.status_code == 200 and not resp.content.startswith(b"<!DOCTYPE") and not resp.content.startswith(b"<html"):
                audio_bytes = io.BytesIO(resp.content)
                break

            # 2. Try sending Form Data payload
            resp = requests.post(ep, data={"text": message.text}, headers=headers, timeout=60)
            if resp.status_code == 200 and not resp.content.startswith(b"<!DOCTYPE") and not resp.content.startswith(b"<html"):
                audio_bytes = io.BytesIO(resp.content)
                break

            last_err = f"HTTP {resp.status_code}: {resp.text[:100]}"
        except Exception as e:
            last_err = str(e)

    if audio_bytes:
        audio_bytes.name = "voice.wav"
        bot.send_voice(message.chat.id, audio_bytes)
    else:
        bot.reply_to(message, f"❌ Connection failed ({last_err}). Make sure your Google Colab notebook & ngrok tunnel are running!")

if __name__ == "__main__":
    print(f"Bot started polling... Target Colab API: {COLAB_API_URL}")
    bot.infinity_polling()
