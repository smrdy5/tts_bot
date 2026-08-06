import json, base64, os, requests
from datetime import date
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import UserUsage

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PIXAZO_API_KEY = os.getenv("PIXAZO_API_KEY") or os.getenv("API_SECRET_KEY")
DAILY_LIMIT = 5

PIXAZO_TTS_URL = "https://gateway.pixazo.ai/voxcpm/v1/text-to-speech"
PIXAZO_CLONE_URL = "https://gateway.pixazo.ai/voxcpm/v1/voice-cloning"

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
                send_telegram_msg(chat_id, "🎙️ Custom Voice selected! Send me a voice note so I can clone it.")
            else:
                send_telegram_msg(chat_id, f"✅ Voice mode set to: {data.upper()}")
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
        send_telegram_msg(chat_id, "Select your preferred voice mode:", reply_markup=keyboard)
        return JsonResponse({"status": "ok"})

    # Handle Voice Note Upload for Voice Cloning
    if message.get("voice") or message.get("audio"):
        attachment = message.get("voice") or message.get("audio")
        file_path_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={attachment['file_id']}").json()
        file_path = file_path_info.get("result", {}).get("file_path")
        if file_path:
            # Store public Telegram file URL for Pixazo voice cloning
            audio_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
            user.custom_voice_b64 = audio_url
            user.selected_voice = "custom"
            user.save()
            send_telegram_msg(chat_id, "✅ Custom voice saved! Send text to synthesize speech in your voice.")
        else:
            send_telegram_msg(chat_id, "❌ Failed to save voice note.")
        return JsonResponse({"status": "ok"})

    # Handle Text-to-Speech Generation
    if text:
        today = date.today()
        if user.last_reset_date != today:
            user.usage_count = 0
            user.last_reset_date = today
        if user.usage_count >= DAILY_LIMIT:
            send_telegram_msg(chat_id, "⚠️ Daily limit reached (5/5).")
            return JsonResponse({"status": "ok"})

        send_telegram_msg(chat_id, f"🗣️ Synthesizing ({user.selected_voice})...")

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }
        if PIXAZO_API_KEY:
            headers["Ocp-Apim-Subscription-Key"] = PIXAZO_API_KEY

        try:
            audio_result_url = None

            # Custom Voice Cloning via Pixazo
            if user.selected_voice == "custom":
                if not user.custom_voice_b64:
                    send_telegram_msg(chat_id, "❌ Upload a custom voice note first!")
                    return JsonResponse({"status": "ok"})

                payload = {
                    "text": text,
                    "reference_audio_url": user.custom_voice_b64
                }
                res = requests.post(PIXAZO_CLONE_URL, json=payload, headers=headers, timeout=120)
                res.raise_for_status()
                res_data = res.json()
                audio_result_url = res_data.get("output") or res_data.get("url") or res_data.get("audio_url")

            # Standard Text to Speech via Pixazo
            else:
                prompt_prefix = "(male) " if user.selected_voice == "male" else "(female) "
                payload = {
                    "text": f"{prompt_prefix}{text}",
                    "cfg_value": 2.0,
                    "dit_steps": 10
                }
                res = requests.post(PIXAZO_TTS_URL, json=payload, headers=headers, timeout=120)
                res.raise_for_status()
                res_data = res.json()
                audio_result_url = res_data.get("output") or res_data.get("url") or res_data.get("audio_url")

            if audio_result_url:
                # Download generated WAV audio from Pixazo CDN
                wav_resp = requests.get(audio_result_url, timeout=30)
                if wav_resp.status_code == 200:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVoice",
                        data={"chat_id": chat_id},
                        files={"voice": ("speech.wav", wav_resp.content, "audio/wav")}
                    )
                    user.usage_count += 1
                    user.save()
                else:
                    send_telegram_msg(chat_id, "❌ Failed to download generated audio from Pixazo.")
            else:
                send_telegram_msg(chat_id, "❌ Pixazo API did not return audio URL.")

        except Exception as e:
            print(f"Pixazo VoxCPM API error: {e}")
            send_telegram_msg(chat_id, "❌ Pixazo VoxCPM API request failed.")

    return JsonResponse({"status": "ok"})
