import json, base64, os, requests
from datetime import date
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import UserUsage

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODAL_API_URL = os.getenv("MODAL_API_URL")
API_SECRET_KEY = os.getenv("API_SECRET_KEY")
DAILY_LIMIT = 5

def send_telegram_msg(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup: payload["reply_markup"] = reply_markup
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload)

@csrf_exempt
def telegram_webhook(request):
    if request.method != "POST": return JsonResponse({"status": "ok"})
    update = json.loads(request.body)
    
    if "callback_query" in update:
        query = update["callback_query"]
        chat_id = query["message"]["chat"]["id"]
        user_id = query["from"]["id"]
        data = query["data"]
        
        user, _ = UserUsage.objects.get_or_create(user_id=user_id)
        if data in ["male", "female", "custom"]:
            user.selected_voice = data
            user.save()
            if data == "custom" and not user.custom_voice_b64:
                send_telegram_msg(chat_id, "🎙️ Custom Voice selected! Send me a 5-10 second voice note so I can clone it.")
            else:
                send_telegram_msg(chat_id, f"✅ Voice changed to: {data.upper()}")
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery", json={"callback_query_id": query["id"]})
        return JsonResponse({"status": "ok"})

    message = update.get("message")
    if not message: return JsonResponse({"status": "ok"})
    chat_id = message.get("chat", {}).get("id")
    user_id = message.get("from", {}).get("id")
    text = message.get("text", "")
    user, _ = UserUsage.objects.get_or_create(user_id=user_id)

    if text in ["/voice", "/start"]:
        keyboard = {"inline_keyboard": [
            [{"text": "👨 Default Male", "callback_data": "male"}],
            [{"text": "👩 Default Female", "callback_data": "female"}],
            [{"text": "🎙️ Clone My Voice", "callback_data": "custom"}]
        ]}
        send_telegram_msg(chat_id, "Select your preferred voice:", reply_markup=keyboard)
        return JsonResponse({"status": "ok"})

    if message.get("voice") or message.get("audio"):
        attachment = message.get("voice") or message.get("audio")
        file_path_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={attachment['file_id']}").json()
        audio_bytes = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path_info['result']['file_path']}").content
        user.custom_voice_b64 = base64.b64encode(audio_bytes).decode('utf-8')
        user.selected_voice = "custom"
        user.save()
        send_telegram_msg(chat_id, "✅ Custom voice saved! Send text to hear it cloned.")
        return JsonResponse({"status": "ok"})

    if text:
        today = date.today()
        if user.last_reset_date != today:
            user.usage_count = 0
            user.last_reset_date = today
        if user.usage_count >= DAILY_LIMIT:
            send_telegram_msg(chat_id, "⚠️ Daily limit reached.")
            return JsonResponse({"status": "ok"})

        send_telegram_msg(chat_id, f"🗣️ Synthesizing ({user.selected_voice})...")
        payload = {"text": text, "voice_mode": user.selected_voice}
        
        if user.selected_voice == "custom":
            if not user.custom_voice_b64:
                send_telegram_msg(chat_id, "❌ Upload a custom voice note first!")
                return JsonResponse({"status": "ok"})
            payload["reference_audio"] = user.custom_voice_b64
            
        try:
            res = requests.post(MODAL_API_URL, json=payload, headers={"X-API-Key": API_SECRET_KEY}, timeout=120)
            res.raise_for_status()
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVoice", data={"chat_id": chat_id}, files={"voice": ("voice.wav", res.content, "audio/wav")})
            user.usage_count += 1
            user.save()
        except Exception:
            send_telegram_msg(chat_id, "❌ Cloud API failed.")

    return JsonResponse({"status": "ok"})
