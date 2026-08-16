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
    res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload)
    try: return res.json()
    except: return None

def send_chat_action(chat_id, action="record_voice"):
    payload = {"chat_id": chat_id, "action": action}
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction", json=payload)

def async_speech_synthesis(chat_id, user_db_id, text, selected_voice, custom_voice_b64, orig_msg_id):
    try:
        status_msg = send_telegram_msg(chat_id, f"🗣️ Synthesizing speech ({selected_voice.upper()})...")
        status_msg_id = status_msg.get("result", {}).get("message_id") if status_msg else None

        stop_action_event = threading.Event()
        def action_loop():
            while not stop_action_event.is_set():
                send_chat_action(chat_id, "record_voice")
                stop_action_event.wait(4)
        
        threading.Thread(target=action_loop, daemon=True).start()

        wav_bytes = None
        pixazo_key = os.getenv("PIXAZO_API_KEY") or os.getenv("API_SECRET_KEY") or PIXAZO_API_KEY
        
        # Load dynamic tunnel URL if available
        colab_url = os.getenv("COLAB_API_URL") or os.getenv("MODAL_API_URL")
        if os.path.exists("tunnel.json"):
            try:
                with open("tunnel.json", "r") as f:
                    colab_url = json.load(f).get("url", colab_url)
            except: pass
            
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
                    # Use absolute permanent reference audio to guarantee the voice NEVER changes
                    if selected_voice == "male":
                        ref_url = "https://raw.githubusercontent.com/smrdy5/tts_bot/main/default_male.wav"
                    else:
                        ref_url = "https://raw.githubusercontent.com/smrdy5/tts_bot/main/default_female.wav"
                    
                    payload = {
                        "text": text,
                        "reference_audio_url": ref_url,
                        "cfg_value": 1.1,
                        "dit_steps": 50,
                        "seed": 42
                    }
                    target_url = PIXAZO_CLONE_URL

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

        stop_action_event.set()
        
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
            
        # Delete the synthesizing status message
        if status_msg_id:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage", json={"chat_id": chat_id, "message_id": status_msg_id})

    except Exception as e:
        print(f"Async synthesis error: {e}")
        stop_action_event.set()
        send_telegram_msg(chat_id, "❌ Speech synthesis failed. Please try again later.")
    finally:
        connection.close()

@csrf_exempt
def update_tunnel(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            if "url" in data:
                with open("tunnel.json", "w") as f:
                    json.dump({"url": data["url"]}, f)
                return JsonResponse({"status": "updated", "url": data["url"]})
        except: pass
    return JsonResponse({"status": "failed"})

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
    orig_msg_id = message.get("message_id")
    user, _ = UserUsage.objects.get_or_create(user_id=user_id)

    # Commands Handling
    if text in ["/voice", "/start"]:
        keyboard = {"inline_keyboard": [
            [{"text": "👨 Default Male", "callback_data": "male"}],
            [{"text": "👩 Default Female", "callback_data": "female"}]
        ]}
        welcome_text = (
            "👋 Welcome to Western Text To Speech bot. សូមស្វាគម៍មកកាន់ វេស្ទីន តិច ធូ ស្ពីច.\n\n"
            "This bot converts your text into highly realistic speech.\n"
            "Bot នេះអាចបំប្លែងអត្ថបទរបស់អ្នកទៅជាសំឡេងនិយាយយ៉ាងពិរោះនិងដូចពិតៗ។\n\n"
            "Please select your preferred voice mode below.\n"
            "សូមជ្រើសរើសប្រភេទសំឡេងដែលអ្នកពេញចិត្តខាងក្រោម។\n\n"
            f"Current Mode: {user.selected_voice.upper()}"
        )
        send_telegram_msg(
            chat_id, 
            welcome_text,
            reply_markup=keyboard
        )
        return JsonResponse({"status": "ok"})

    if text == "/policy":
        policy_text = (
            "📜 **Bot Policy & Privacy**\n"
            "1. **Data Usage**: Your text is used only to generate speech. We do not permanently store your messages or audio files.\n"
            "2. **Usage Limits**: Standard users are limited to 7 requests per day to ensure fair usage.\n"
            "3. **Acceptable Use**: Please do not use this bot to generate illegal or harmful content.\n\n"
            "📜 **គោលការណ៍របស់ Bot**\n"
            "១. **ការប្រើប្រាស់ទិន្នន័យ**៖ អត្ថបទរបស់អ្នកត្រូវបានប្រើសម្រាប់តែបំប្លែងជាសំឡេងប៉ុណ្ណោះ។ យើងមិនរក្សាទុកសារ ឬឯកសារសំឡេងរបស់អ្នកទេ។\n"
            "២. **ដែនកំណត់នៃការប្រើប្រាស់**៖ អ្នកប្រើប្រាស់ធម្មតាអាចប្រើបាន 7 ដងក្នុងមួយថ្ងៃ។\n"
            "៣. **ការប្រើប្រាស់ត្រឹមត្រូវ**៖ សូមកុំប្រើប្រាស់ Bot នេះដើម្បីបង្កើតសំឡេងដែលខុសច្បាប់ ឬប៉ះពាល់ដល់អ្នកដទៃ។"
        )
        send_telegram_msg(chat_id, policy_text)
        return JsonResponse({"status": "ok"})

    if text in ["/about", "/team"]:
        about_text = (
            "👨‍💻 **Meet the Team**\n\n"
            "This bot was proudly developed by our creators:\n"
            "• Sito Eanfhong\n"
            "• Mao Chanpha\n"
            "• Sambath Makara\n"
            "• Pha Somarady"
        )
        send_telegram_msg(chat_id, about_text)
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
        
    if "trycloudflare.com" in text:
        with open("tunnel.json", "w") as f:
            json.dump({"url": text}, f)
        
        # Delete user's link message
        if orig_msg_id:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage", json={"chat_id": chat_id, "message_id": orig_msg_id})
            
        resp = send_telegram_msg(chat_id, f"✅ **Local Server Connected!**\n\nI have successfully updated the fallback engine to use your local RTX 3060.\n\n🔗 `{text}`")
        
        if resp and "result" in resp:
            bot_msg_id = resp["result"].get("message_id")
            if bot_msg_id:
                def delete_later():
                    import time
                    time.sleep(3)
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage", json={"chat_id": chat_id, "message_id": bot_msg_id})
                threading.Thread(target=delete_later, daemon=True).start()
                
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
            args=(chat_id, user.id, text, user.selected_voice, user.custom_voice_b64, orig_msg_id)
        ).start()

        return JsonResponse({"status": "ok"})

    return JsonResponse({"status": "ok"})
