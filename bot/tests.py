import json
import base64
from unittest.mock import patch, MagicMock
from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from bot.models import UserUsage

class UserUsageModelTest(TestCase):
    def test_create_user_usage(self):
        user = UserUsage.objects.create(user_id=123456)
        self.assertEqual(user.user_id, 123456)
        self.assertEqual(user.usage_count, 0)
        self.assertEqual(user.selected_voice, "male")
        self.assertIsNone(user.custom_voice_b64)
        self.assertEqual(str(user), "User 123456 - Voice: male - Count: 0")

class TelegramWebhookTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.webhook_url = reverse("webhook")

    def test_non_post_request(self):
        response = self.client.get(self.webhook_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @patch("bot.views.requests.post")
    def test_start_command(self, mock_post):
        mock_post.return_value.status_code = 200
        payload = {
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/start"
            }
        }
        response = self.client.post(self.webhook_url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        user = UserUsage.objects.get(user_id=1001)
        self.assertIsNotNone(user)
        mock_post.assert_called_once()

    @patch("bot.views.requests.post")
    def test_myvoice_and_resetvoice_commands(self, mock_post):
        mock_post.return_value.status_code = 200
        user = UserUsage.objects.create(user_id=1006, selected_voice="custom", custom_voice_b64="VGVzdEF1ZGlvQmFzZTY0RGF0YQ==")
        
        # Test /myvoice
        payload_myvoice = {"message": {"chat": {"id": 1006}, "from": {"id": 1006}, "text": "/myvoice"}}
        res_myvoice = self.client.post(self.webhook_url, data=json.dumps(payload_myvoice), content_type="application/json")
        self.assertEqual(res_myvoice.status_code, 200)

        # Test /resetvoice
        payload_reset = {"message": {"chat": {"id": 1006}, "from": {"id": 1006}, "text": "/resetvoice"}}
        res_reset = self.client.post(self.webhook_url, data=json.dumps(payload_reset), content_type="application/json")
        self.assertEqual(res_reset.status_code, 200)
        user.refresh_from_db()
        self.assertIsNone(user.custom_voice_b64)
        self.assertEqual(user.selected_voice, "male")

    @patch("bot.views.requests.post")
    def test_callback_query_voice_selection(self, mock_post):
        mock_post.return_value.status_code = 200
        payload = {
            "callback_query": {
                "id": "cb_1",
                "message": {"chat": {"id": 1002}},
                "from": {"id": 1002},
                "data": "female"
            }
        }
        response = self.client.post(self.webhook_url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        user = UserUsage.objects.get(user_id=1002)
        self.assertEqual(user.selected_voice, "female")

    @patch("bot.views.requests.get")
    @patch("bot.views.requests.post")
    def test_voice_note_upload_saves_base64(self, mock_post, mock_get):
        # 1st get: getFile info, 2nd get: download file bytes
        mock_info_resp = MagicMock()
        mock_info_resp.json.return_value = {"ok": True, "result": {"file_path": "voice/file_1.oga"}}
        
        mock_audio_bytes_resp = MagicMock()
        mock_audio_bytes_resp.status_code = 200
        mock_audio_bytes_resp.content = b"FAKE_AUDIO_SAMPLE_DATA"

        mock_get.side_effect = [mock_info_resp, mock_audio_bytes_resp]
        mock_post.return_value.status_code = 200

        payload = {
            "message": {
                "chat": {"id": 1003},
                "from": {"id": 1003},
                "voice": {"file_id": "file_123"}
            }
        }
        response = self.client.post(self.webhook_url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        user = UserUsage.objects.get(user_id=1003)
        self.assertEqual(user.selected_voice, "custom")
        expected_b64 = base64.b64encode(b"FAKE_AUDIO_SAMPLE_DATA").decode("utf-8")
        self.assertEqual(user.custom_voice_b64, expected_b64)

    @patch("bot.views.requests.get")
    @patch("bot.views.requests.post")
    def test_modal_tts_generation_success(self, mock_post, mock_get):
        # Test generation with MODAL_API_URL set
        with patch.dict("os.environ", {"MODAL_API_URL": "https://modal.example.com/generate", "API_SECRET_KEY": "secret"}):
            mock_modal_resp = MagicMock()
            mock_modal_resp.status_code = 200
            mock_modal_resp.content = b"RIFF....WAVE"

            mock_post.side_effect = [
                MagicMock(status_code=200), # send_telegram_msg "Synthesizing..."
                mock_modal_resp,            # Modal API POST
                MagicMock(status_code=200)  # Telegram sendVoice
            ]

            payload = {
                "message": {
                    "chat": {"id": 1004},
                    "from": {"id": 1004},
                    "text": "Hello world"
                }
            }
            response = self.client.post(self.webhook_url, data=json.dumps(payload), content_type="application/json")
            self.assertEqual(response.status_code, 200)
            user = UserUsage.objects.get(user_id=1004)
            self.assertEqual(user.usage_count, 1)

    @patch("bot.views.requests.post")
    def test_daily_rate_limit(self, mock_post):
        mock_post.return_value.status_code = 200
        user = UserUsage.objects.create(user_id=1005, usage_count=7, last_reset_date=date.today())
        
        payload = {
            "message": {
                "chat": {"id": 1005},
                "from": {"id": 1005},
                "text": "Over limit text"
            }
        }
        response = self.client.post(self.webhook_url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.usage_count, 7)
