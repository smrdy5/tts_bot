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

CURRENT_KEY_INDEX = 0

def get_pixazo_keys():
    keys_str = os.getenv("PIXAZO_API_KEYS") or os.getenv("PIXAZO_API_KEY") or os.getenv("API_SECRET_KEY") or ""
    return [k.strip() for k in keys_str.split(",") if k.strip()]

def send_telegram_msg(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup: 
        payload["reply_markup"] = reply_markup
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload)

def send_chat_action(chat_id, action="record_voice"):
    payload = {"chat_id": chat_id, "action": action}
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction", json=payload)

def async_speech_synthesis(chat_id, user_db_id, text, selected_voice, custom_voice_b64):
    try:
        send_telegram_msg(chat_id, f"🗣️ Synthesizing speech ({selected_voice.upper()})...")
        send_chat_action(chat_id, "record_voice")

        wav_bytes = None
        pixazo_key = os.getenv("PIXAZO_API_KEY") or os.getenv("API_SECRET_KEY") or PIXAZO_API_KEY
        colab_url = os.getenv("COLAB_API_URL") or os.getenv("MODAL_API_URL")
        hf_space = os.getenv("HF_SPACE_ID") or os.getenv("HF_SPACE_NAME")
        pixazo_error = None

        # 1. Hugging Face Space API Engine (if HF_SPACE_ID is configured)
        if hf_space:
            try:
                from gradio_client import Client
                print(f"Connecting to Hugging Face Space: {hf_space}")
                client = Client(hf_space)
                res_file = client.predict(
                    text=text,
                    voice_mode=selected_voice,
                    reference_audio=custom_voice_b64 if selected_voice == "custom" else None,
                    api_name="/predict"
                )
                if res_file and os.path.exists(res_file):
                    with open(res_file, "rb") as f:
                        wav_bytes = f.read()
            except Exception as hf_e:
                print(f"Hugging Face Space API error: {hf_e}")

        # 2. Pixazo Gateway API (VoxCPM 2.0)
        pixazo_keys = get_pixazo_keys()
        if not wav_bytes and pixazo_keys:
            global CURRENT_KEY_INDEX
            res = None
            
            for key_attempt in range(len(pixazo_keys)):
                current_key = pixazo_keys[CURRENT_KEY_INDEX % len(pixazo_keys)]
                headers = {
                    "Content-Type": "application/json",
                    "Cache-Control": "no-cache",
                    "Ocp-Apim-Subscription-Key": current_key,
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
                    global PIXAZO_MALE_B64, PIXAZO_FEMALE_B64
                    if 'PIXAZO_MALE_B64' not in globals(): globals()['PIXAZO_MALE_B64'] = None
                    if 'PIXAZO_FEMALE_B64' not in globals(): globals()['PIXAZO_FEMALE_B64'] = None
                    
                    cached_b64 = globals()[f'PIXAZO_{selected_voice.upper()}_B64']
                    
                    if not cached_b64:
                        ref_text = f"({selected_voice}) សួស្តី ខ្ញុំជាសំឡេងយោងសម្រាប់ប្រព័ន្ធនេះ។ ខ្ញុំអាចនិយាយបានយ៉ាងច្បាស់។"
                        ref_payload = {"text": ref_text, "cfg_value": 2.0, "dit_steps": 25}
                        try:
                            ref_res = requests.post(PIXAZO_TTS_URL, json=ref_payload, headers=headers, timeout=60)
                            if ref_res.status_code == 200:
                                ref_url_out = ref_res.json().get("output") or ref_res.json().get("url") or ref_res.json().get("audio_url")
                                if ref_url_out:
                                    wav_resp = requests.get(ref_url_out, timeout=30)
                                    if wav_resp.status_code == 200:
                                        cached_b64 = base64.b64encode(wav_resp.content).decode("utf-8")
                                        globals()[f'PIXAZO_{selected_voice.upper()}_B64'] = cached_b64
                        except Exception as e:
                            print(f"Failed to generate Pixazo reference: {e}")

                    if cached_b64:
                        payload = {
                            "text": text,
                            "reference_audio_url": f"data:audio/wav;base64,{cached_b64}"
                        }
                        target_url = PIXAZO_CLONE_URL
                    else:
                        payload = {
                            "text": f"({selected_voice}) {text}",
                            "cfg_value": 2.0,
                            "dit_steps": 25
                        }
                        target_url = PIXAZO_TTS_URL

                for attempt in range(3):
                    try:
                        res = requests.post(target_url, json=payload, headers=headers, timeout=60)
                        if res.status_code == 200:
                            break
                        elif res.status_code in [502, 503, 504, 522] and attempt < 2:
                            import time
                            time.sleep(2)
                            continue
                        else:
                            break
                    except Exception as req_e:
                        pixazo_error = f"Pixazo request error: {req_e}"
                        res = None

                if res and res.status_code == 200:
                    res_data = res.json()
                    audio_result_url = res_data.get("output") or res_data.get("url") or res_data.get("audio_url")
                    if audio_result_url:
                        wav_resp = requests.get(audio_result_url, timeout=30)
                        if wav_resp.status_code == 200:
                            wav_bytes = wav_resp.content
                    break
                elif res and res.status_code in [401, 402, 403]:
                    print(f"Key {current_key[:5]}... failed with {res.status_code}, rotating...")
                    CURRENT_KEY_INDEX += 1
                else:
                    break

            if not wav_bytes and res is not None:
                status_code = res.status_code
                clean_text = res.text[:120].replace("\n", " ").strip()
                if status_code in [502, 503, 504, 522]:
                    pixazo_error = "⏳ Pixazo GPU servers are currently undergoing temporary maintenance or high load."
                elif status_code in [401, 403]:
                    pixazo_error = f"🔑 Pixazo API Key Error ({status_code}): All configured keys are invalid."
                elif status_code == 402:
                    pixazo_error = f"⚠️ Pixazo Account Balance Low ({status_code}): All configured keys are out of credit!"
                else:
                    pixazo_error = f"❌ Pixazo API Error ({status_code}): {clean_text}"
        elif not pixazo_keys:
            pixazo_error = "⚠️ `PIXAZO_API_KEY` is missing in Render Environment Variables."

        # Fallback Engine: Google Colab / Modal API (if configured and Pixazo failed)
        if not wav_bytes and colab_url and "YOUR-NGROK-URL" not in colab_url:
            base_url = colab_url.rstrip("/")
            endpoints = [base_url if base_url.endswith("/generate") else f"{base_url}/generate", base_url]
            colab_headers = {
                "ngrok-skip-browser-warning": "69420",
                "User-Agent": "Mozilla/5.0",
            }
            local_ref_b64 = globals().get(f'PIXAZO_{selected_voice.upper()}_B64')
            colab_payload = {
                "text": text,
                "voice_mode": selected_voice,
                "reference_audio": local_ref_b64
            }
            for ep in endpoints:
                try:
                    c_res = requests.post(ep, json=colab_payload, headers=colab_headers, timeout=60)
                    if c_res.status_code == 200 and not c_res.content.startswith(b"<!DOCTYPE") and not c_res.content.startswith(b"<html"):
                        wav_bytes = c_res.content
                        break
                    c_res = requests.post(ep, data={"text": text}, headers=colab_headers, timeout=60)
                    if c_res.status_code == 200 and not c_res.content.startswith(b"<!DOCTYPE") and not c_res.content.startswith(b"<html"):
                        wav_bytes = c_res.content
                        break
                except Exception:
                    pass

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
            final_err = pixazo_error or "⚠️ Pixazo VoxCPM synthesis failed. Please try again in a few moments."
            print(f"Synthesis error: {final_err}")
            send_telegram_msg(chat_id, final_err)

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
        if data in ["male", "female"]:
            user.selected_voice = data
            user.save()
            voice_label = f"DEFAULT {data.upper()}"
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
            [{"text": "👩 Default Female", "callback_data": "female"}]
        ]}
        send_telegram_msg(
            chat_id, 
            f"Please select your preferred voice mode.\n\nCurrent Mode: {user.selected_voice.upper()}", 
            reply_markup=keyboard
        )
        return JsonResponse({"status": "ok"})


    if text.startswith("/tester"):
        password = os.getenv("TESTER_PASSWORD", "unlimited").strip()
        parts = text.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        
        if arg == password:
            user.is_tester = True
            user.save()
            send_telegram_msg(chat_id, "✅ Tester mode enabled! You now have unlimited requests.")
        elif arg == "off":
            user.is_tester = False
            user.save()
            send_telegram_msg(chat_id, "❌ Tester mode disabled.")
        else:
            send_telegram_msg(chat_id, "⚠️ Invalid tester command or password.")
        return JsonResponse({"status": "ok"})


    # Handle Text-to-Speech Synthesis
    if text:
        today = date.today()
        if user.last_reset_date != today:
            user.usage_count = 0
            user.last_reset_date = today
            user.save()

        if user.usage_count >= DAILY_LIMIT and not user.is_tester:
            send_telegram_msg(chat_id, f"⚠️ Daily limit reached ({DAILY_LIMIT}/{DAILY_LIMIT}). Try again tomorrow.")
            return JsonResponse({"status": "ok"})

        if user.selected_voice == "custom" and not user.custom_voice_b64:
            send_telegram_msg(chat_id, "❌ No custom voice saved yet! Record and send a voice note first to clone your speech voice.")
            return JsonResponse({"status": "ok"})

        # Spawn asynchronous thread so Webhook responds 200 OK immediately (< 50ms)
        threading.Thread(
            target=async_speech_synthesis,
            args=(chat_id, user.id, text, user.selected_voice, user.custom_voice_b64)
        ).start()

        return JsonResponse({"status": "ok"})

    return JsonResponse({"status": "ok"})
