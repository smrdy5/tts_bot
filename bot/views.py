import os
import json
import base64
import requests
from datetime import date
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import UserUsage

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODAL_API_URL = os.getenv("MODAL_API_URL")
API_SECRET_KEY = os.getenv("API_SECRET_KEY")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else ""


def send_telegram_message(chat_id, text, reply_markup=None):
    if not TELEGRAM_API_URL:
        print("TELEGRAM_TOKEN not configured.")
        return
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending Telegram message: {e}")


def send_telegram_voice(chat_id, voice_bytes):
    if not TELEGRAM_API_URL:
        print("TELEGRAM_TOKEN not configured.")
        return False
    try:
        files = {"voice": ("speech.wav", voice_bytes, "audio/wav")}
        data = {"chat_id": chat_id}
        resp = requests.post(f"{TELEGRAM_API_URL}/sendVoice", data=data, files=files, timeout=60)
        return resp.status_code == 200
    except Exception as e:
        print(f"Error sending Telegram voice: {e}")
        return False


def get_voice_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "👨 Default Male", "callback_data": "male"}],
            [{"text": "👩 Default Female", "callback_data": "female"}],
            [{"text": "🎙️ Clone My Voice", "callback_data": "custom"}],
        ]
    }


def get_or_create_user(user_id):
    user, created = UserUsage.objects.get_or_create(user_id=user_id)
    today = date.today()
    if user.last_reset_date != today:
        user.usage_count = 0
        user.last_reset_date = today
        user.save()
    return user


@csrf_exempt
def telegram_webhook(request):
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponse("Invalid JSON", status=400)

    # 1. Handle Callback Queries (Inline Keyboard Buttons)
    if "callback_query" in data:
        callback = data["callback_query"]
        callback_id = callback.get("id")
        user_id = callback.get("from", {}).get("id")
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        action = callback.get("data")

        if user_id:
            user = get_or_create_user(user_id)
            if action in ["male", "female", "custom"]:
                user.selected_voice = action
                user.save()

                voice_labels = {
                    "male": "👨 Default Male",
                    "female": "👩 Default Female",
                    "custom": "🎙️ Clone My Voice",
                }

                # Acknowledge callback query
                if TELEGRAM_API_URL:
                    try:
                        requests.post(
                            f"{TELEGRAM_API_URL}/answerCallbackQuery",
                            json={
                                "callback_query_id": callback_id,
                                "text": f"Selected voice: {voice_labels.get(action)}",
                            },
                            timeout=5,
                        )
                    except Exception:
                        pass

                if action == "custom" and not user.custom_voice_b64:
                    send_telegram_message(
                        chat_id,
                        "🎙️ You selected 'Clone My Voice'. Please record and send a short voice note so I can clone your voice!",
                    )
                else:
                    send_telegram_message(
                        chat_id,
                        f"✅ Voice set to: {voice_labels.get(action)}. Send me any text to generate speech!",
                    )

        return JsonResponse({"status": "ok"})

    # 2. Handle Messages
    message = data.get("message")
    if not message:
        return JsonResponse({"status": "ok"})

    chat_id = message.get("chat", {}).get("id")
    user_id = message.get("from", {}).get("id")
    text_content = message.get("text", "").strip()

    if not user_id or not chat_id:
        return JsonResponse({"status": "ok"})

    user = get_or_create_user(user_id)

    # Command Handling (/start, /voice)
    if text_content in ["/start", "/voice"]:
        send_telegram_message(
            chat_id,
            "👋 Welcome to VoxCPM Voice Bot!\n\nPlease select your preferred voice mode:",
            reply_markup=get_voice_keyboard(),
        )
        return JsonResponse({"status": "ok"})

    # Voice Note Upload Handling
    if "voice" in message:
        file_id = message["voice"].get("file_id")
        if not file_id or not TELEGRAM_API_URL:
            send_telegram_message(chat_id, "❌ Unable to process voice message.")
            return JsonResponse({"status": "ok"})

        try:
            # 1. Get file path from Telegram
            file_info_resp = requests.get(
                f"{TELEGRAM_API_URL}/getFile", params={"file_id": file_id}, timeout=10
            )
            file_info = file_info_resp.json()
            file_path = file_info.get("result", {}).get("file_path")

            if not file_path:
                send_telegram_message(chat_id, "❌ Failed to retrieve voice file path.")
                return JsonResponse({"status": "ok"})

            # 2. Download raw voice file bytes
            download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
            audio_resp = requests.get(download_url, timeout=30)
            if audio_resp.status_code == 200:
                # 3. Base64 encode & store
                b64_audio = base64.b64encode(audio_resp.content).decode("utf-8")
                user.custom_voice_b64 = b64_audio
                user.selected_voice = "custom"
                user.save()

                send_telegram_message(
                    chat_id,
                    "🎉 Voice cloned successfully! Custom voice mode is now active. Send me any text to generate speech in your voice.",
                )
            else:
                send_telegram_message(chat_id, "❌ Failed to download voice message.")
        except Exception as e:
            print(f"Error processing voice note: {e}")
            send_telegram_message(chat_id, "❌ Error saving voice sample.")

        return JsonResponse({"status": "ok"})

    # Text Generation Handling
    if text_content:
        # Check Rate Limit (5 generations per day)
        if user.usage_count >= 5:
            send_telegram_message(
                chat_id,
                "⚠️ You have reached your daily limit of 5 speech generations. Please try again tomorrow!",
            )
            return JsonResponse({"status": "ok"})

        # Check if custom mode active without sample
        if user.selected_voice == "custom" and not user.custom_voice_b64:
            send_telegram_message(
                chat_id,
                "🎙️ Custom voice mode is selected, but no voice sample was found.\n\nPlease record and send a voice note first!",
                reply_markup=get_voice_keyboard(),
            )
            return JsonResponse({"status": "ok"})

        if not MODAL_API_URL:
            send_telegram_message(
                chat_id,
                "⚠️ Modal API backend URL is not configured (`MODAL_API_URL`). Please contact admin.",
            )
            return JsonResponse({"status": "ok"})

        send_telegram_message(chat_id, "⏳ Generating speech with VoxCPM2... Please wait.")

        # Request Modal API
        headers = {
            "Content-Type": "application/json",
        }
        if API_SECRET_KEY:
            headers["X-API-Key"] = API_SECRET_KEY

        payload = {
            "text": text_content,
            "voice_mode": user.selected_voice,
            "reference_audio": user.custom_voice_b64 if user.selected_voice == "custom" else None,
        }

        try:
            modal_resp = requests.post(MODAL_API_URL, json=payload, headers=headers, timeout=300)
            if modal_resp.status_code == 200 and modal_resp.content:
                success = send_telegram_voice(chat_id, modal_resp.content)
                if success:
                    user.usage_count += 1
                    user.save()
                else:
                    send_telegram_message(chat_id, "❌ Failed to send voice message via Telegram.")
            else:
                print(f"Modal API Error [{modal_resp.status_code}]: {modal_resp.text}")
                send_telegram_message(
                    chat_id,
                    f"❌ Speech generation failed (Error code {modal_resp.status_code}). Please try again later.",
                )
        except Exception as e:
            print(f"Error calling Modal API: {e}")
            send_telegram_message(
                chat_id, "❌ Service timeout or network error connecting to AI engine."
            )

    return JsonResponse({"status": "ok"})
