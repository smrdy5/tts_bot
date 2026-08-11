import os
import requests
import io
import telebot

# Read tokens and URLs from environment variables
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")

# Google Colab ngrok URL (e.g. "https://xxxx-xx-xx-xx.ngrok-free.app")
COLAB_API_URL = os.environ.get("COLAB_API_URL", "https://YOUR-NGROK-URL.ngrok-free.app")

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is missing.")

bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    bot.reply_to(message, "Generating audio with VoxCPM... 🎙️")
    
    try:
        endpoint = COLAB_API_URL.rstrip("/")
        if not endpoint.endswith("/generate"):
            endpoint = f"{endpoint}/generate"

        # Send text to Colab API endpoint
        response = requests.post(
            endpoint, 
            data={"text": message.text},
            timeout=60
        )
        
        if response.status_code == 200:
            # Send the synthesized voice note back to Telegram
            audio_bytes = io.BytesIO(response.content)
            audio_bytes.name = "voice.wav"
            bot.send_voice(message.chat.id, audio_bytes)
        else:
            bot.reply_to(message, f"Error: Colab server returned status code {response.status_code}.")
            
    except Exception as e:
        bot.reply_to(message, "Connection failed. Make sure your Google Colab notebook is running!")
        print(f"Error connecting to Colab API: {e}")

if __name__ == "__main__":
    print(f"Bot started polling... Target Colab API: {COLAB_API_URL}")
    bot.infinity_polling()
