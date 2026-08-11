import json, base64, os, requests, threading
from datetime import date
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import connection
from .models import UserUsage

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PIXAZO_API_KEY = os.getenv("PIXAZO_API_KEY") or os.getenv("API_SECRET_KEY")
DAILY_LIMIT = 7

PIXAZO_TTS_URL = "https://gateway.pixazo.ai/voxcpm/v1/text-to-speech"
PIXAZO_CLONE_URL = "https://gateway.pixazo.ai/voxcpm/v1/voice-cloning"

def send_telegram_msg(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup: 
        payload["reply_markup"] = reply_markup
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload)

def async_speech_synthesis(chat_id, user_db_id, text, selected_voice, custom_voice_b64):
    try:
        send_telegram_msg(chat_id, f"🗣️ Synthesizing speech ({selected_voice.upper()})...")

        wav_bytes = None
        pixazo_key = os.getenv("PIXAZO_API_KEY") or os.getenv("API_SECRET_KEY") or PIXAZO_API_KEY

        if pixazo_key:
            headers = {
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
                "Ocp-Apim-Subscription-Key": pixazo_key,
            }

            if selected_voice == "custom":
                ref_url = custom_voice_b64
                if ref_url and not (ref_url.startswith("http://") or ref_url.startswith("https://")):
                    ref_url = f"data:audio/wav;base64,{ref_url}"

                payload = {
                    "text": text,
                    "reference_audio_url": ref_url
                }
                target_url = PIXAZO_CLONE_URL
            else:
                prompt_prefix = f"({selected_voice}) "
                payload = {
                    "text": f"{prompt_prefix}{text}",
                    "cfg_value": 2.0,
                    "dit_steps": 6
                }
                target_url = PIXAZO_TTS_URL

            res = None
            for attempt in range(2):
                try:
                    res = requests.post(target_url, json=payload, headers=headers, timeout=60)
                    if res.status_code == 200:
                        break
                    elif res.status_code in [522, 504, 502, 503] and attempt == 0:
                        continue
                except Exception as req_e:
                    print(f"Pixazo attempt {attempt + 1} error: {req_e}")

            if res and res.status_code == 200:
                res_data = res.json()
                audio_result_url = res_data.get("output") or res_data.get("url") or res_data.get("audio_url")
                if audio_result_url:
                    wav_resp = requests.get(audio_result_url, timeout=30)
                    if wav_resp.status_code == 200:
                        wav_bytes = wav_resp.content

            if wav_bytes:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVoice",
                    data={"chat_id": chat_id},
                    files={"voice": ("speech.wav", wav_bytes, "audio/wav")}
                )
                user = UserUsage.objects.get(id=user_db_id)
                user.usage_count += 1
                user.save()
            else:
                status_code = res.status_code if res else "Timeout"
                if status_code in [522, 504]:
                    err_msg = "⏳ Pixazo server timed out. Please try sending your message again!"
                else:
                    clean_text = res.text[:100].replace("\n", " ").strip() if res else "No response"
                    err_msg = f"❌ Pixazo API Error ({status_code}): {clean_text}"
                send_telegram_msg(chat_id, err_msg)
        else:
            send_telegram_msg(chat_id, "⚠️ `PIXAZO_API_KEY` is missing in Render Environment Variables.")
    except Exception as e:
        print(f"Async synthesis error: {e}")
        send_telegram_msg(chat_id, "❌ Speech synthesis failed. Please try again later.")
    finally:
        connection.close()

@csrf_exempt
def telegram_webhook(request):
    if request.method != "POST":
        return JsonResponse({"status": "ok"})
    
    update = json.loads(request.body)
    
    # Handle Callback Queries (Inline Keyboards)
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
                send_telegram_msg(chat_id, "🎙️ Custom Voice mode active! Please record and send me a voice note to save your speech voice.")
            else:
                voice_label = "CLONED CUSTOM VOICE" if data == "custom" else f"DEFAULT {data.upper()}"
                send_telegram_msg(chat_id, f"✅ Voice mode set to: {voice_label}")
        
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery", json={"callback_query_id": query["id"]})
        return JsonResponse({"status": "ok"})

    message = update.get("message")
    if not message:
        return JsonResponse({"status": "ok"})
    
    chat_id = message.get("chat", {}).get("id")
    user_id = message.get("from", {}).get("id")
    text = message.get("text", "").strip()
    user, _ = UserUsage.objects.get_or_create(user_id=user_id)

    # Commands Handling
    if text in ["/voice", "/start"]:
        keyboard = {"inline_keyboard": [
            [{"text": "👨 Default Male", "callback_data": "male"}],
            [{"text": "👩 Default Female", "callback_data": "female"}],
            [{"text": "🎙️ Clone My Voice", "callback_data": "custom"}]
        ]}
        has_custom = " (Saved 🎙️)" if user.custom_voice_b64 else " (Not set)"
        send_telegram_msg(
            chat_id, 
            f"Current Voice Mode: {user.selected_voice.upper()}{has_custom}\nSelect your preferred voice mode below:", 
            reply_markup=keyboard
        )
        return JsonResponse({"status": "ok"})

    if text == "/myvoice":
        if user.custom_voice_b64:
            b64_len_kb = len(user.custom_voice_b64) // 1024
            send_telegram_msg(chat_id, f"🎙️ Saved Custom Voice Profile:\n• Mode: {user.selected_voice.upper()}\n• Voice Sample Size: {b64_len_kb} KB\n\nAll text will be synthesized using this saved voice.")
        else:
            send_telegram_msg(chat_id, "ℹ️ No custom voice profile saved yet. Send a voice note to clone your voice!")
        return JsonResponse({"status": "ok"})

    if text == "/resetvoice":
        user.custom_voice_b64 = None
        user.selected_voice = "male"
        user.save()
        send_telegram_msg(chat_id, "🔄 Custom voice deleted! Reset voice mode to DEFAULT MALE.")
        return JsonResponse({"status": "ok"})

    # Handle Voice Note / Audio Upload for Persistent Voice Saving
    if message.get("voice") or message.get("audio"):
        attachment = message.get("voice") or message.get("audio")
        file_id = attachment.get("file_id")
        
        file_info_resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
        file_path = file_info_resp.get("result", {}).get("file_path")
        
        if file_path:
            download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
            audio_resp = requests.get(download_url, timeout=30)
            if audio_resp.status_code == 200:
                audio_b64 = base64.b64encode(audio_resp.content).decode("utf-8")
                user.custom_voice_b64 = audio_b64
                user.selected_voice = "custom"
                user.save()
                send_telegram_msg(chat_id, "✅ Custom voice permanently saved! All future text messages will be synthesized with this cloned voice.")
            else:
                send_telegram_msg(chat_id, "❌ Failed to download voice note content from Telegram.")
        else:
            send_telegram_msg(chat_id, "❌ Failed to retrieve voice note path from Telegram.")
        return JsonResponse({"status": "ok"})

    # Handle Text-to-Speech Synthesis
    if text:
        today = date.today()
        if user.last_reset_date != today:
            user.usage_count = 0
            user.last_reset_date = today
            user.save()

        if user.usage_count >= DAILY_LIMIT:
            send_telegram_msg(chat_id, f"⚠️ Daily limit reached ({DAILY_LIMIT}/{DAILY_LIMIT}). Try again tomorrow.")
            return JsonResponse({"status": "ok"})

        if user.selected_voice == "custom" and not user.custom_voice_b64:
            send_telegram_msg(chat_id, "❌ No custom voice saved yet! Record and send a voice note first to clone your speech voice.")
            return JsonResponse({"status": "ok"})

        # Launch synthesis in background thread and respond 200 OK immediately (< 50ms)
        threading.Thread(
            target=async_speech_synthesis,
            args=(chat_id, user.id, text, user.selected_voice, user.custom_voice_b64)
        ).start()

        return JsonResponse({"status": "ok"})

    return JsonResponse({"status": "ok"})
