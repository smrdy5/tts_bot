# PROJECT OVERVIEW: VoxCPM Telegram Voice Bot Architecture

You are an expert AI developer. Your task is to build a production-ready Telegram Bot architecture from scratch. This project connects a Django webhook backend (hosted on Render) to the Openbmb VoxCPM 2.0 API (hosted on Pixazo Gateway).

## TECH STACK
* **AI GPU Engine:** Pixazo Gateway (Openbmb VoxCPM 2.0 API)
* **Backend:** Django 5.x, Gunicorn, Requests
* **Database:** PostgreSQL (via Render Postgres / Supabase, accessed via Django ORM)
* **Frontend:** Telegram Bot API (Webhook mode)
* **Backend:** Django 5.x, Gunicorn, Requests
* **Database:** PostgreSQL (via Supabase, accessed via Django ORM)
* **Frontend:** Telegram Bot API (Webhook mode)

## SYSTEM ARCHITECTURE & REQUIREMENTS

### 1. Environment Variables Needed
The system relies on these variables (assume they are provided in the environment):
* `TELEGRAM_TOKEN`: Telegram Bot API token.
* `DATABASE_URL`: PostgreSQL connection string.
* `MODAL_API_URL`: The deployed Modal API endpoint.
* `API_SECRET_KEY`: A shared secret to authenticate Django -> Modal requests.

---

### PHASE 1: The AI Inference Engine (Modal)
Create a file named `modal_api.py` in the root directory.

**Requirements for `modal_api.py`:**
1. Use `modal` to define an app `voxcpm-cloud-api`.
2. Image dependencies: `torch`, `soundfile`, `voxcpm`, `fastapi`.
3. Mount two local audio files into the container at `/assets/`: `default_male.wav` and `default_female.wav`.
4. Use a T4 GPU with a 300s timeout. Set a secret `API_SECRET_KEY`.
5. **Initialization:** Load `openbmb/VoxCPM2` (`load_denoiser=False`), move to `cuda`, `float16`.
6. **API Endpoint (`@modal.web_endpoint(method="POST")`):**
    * Authenticate via `X-API-Key` header matching `API_SECRET_KEY`.
    * Accept JSON: `{"text": str, "voice_mode": str ("male"|"female"|"custom"), "reference_audio": str (base64, optional)}`.
    * **Voice Routing:**
        * If `male`, set reference audio to `/assets/default_male.wav`.
        * If `female`, set reference audio to `/assets/default_female.wav`.
        * If `custom`, decode `reference_audio` base64 to a temporary `.wav` file.
    * **Chunking:** Split the input `text` by punctuation (periods, newlines) using regex to prevent CUDA Out-Of-Memory errors.
    * **Generation:** Iterate over text chunks, call `model.generate(text=chunk, reference_wav_path=..., cfg_value=2.0, inference_timesteps=10)`, and collect results.
    * Clean up temp files. Concatenate audio chunks using `numpy`, convert to WAV via `soundfile` and `io.BytesIO`, and return as `audio/wav` response.

---

### PHASE 2: The Django Backend
Create a standard Django project named `config` and an app named `bot`.

**1. `requirements.txt`**
Include: `Django`, `requests`, `dj-database-url`, `psycopg2-binary`, `gunicorn`.

**2. `config/settings.py`**
* Add `'bot'` to `INSTALLED_APPS`.
* Set `ALLOWED_HOSTS = ['*']`.
* Configure `DATABASES['default']` using `dj_database_url.config(default=os.getenv("DATABASE_URL"), conn_max_age=600, ssl_require=True)`.

**3. `bot/models.py`**
Create a `UserUsage` model to track users:
* `user_id` (BigIntegerField, unique)
* `usage_count` (IntegerField, default 0)
* `last_reset_date` (DateField, auto_now_add=True)
* `selected_voice` (CharField, max_length=10, default "male")
* `custom_voice_b64` (TextField, null=True, blank=True)

**4. `bot/views.py`**
Implement the Telegram Webhook logic (`@csrf_exempt`).
* Parse incoming JSON. Handle both standard messages and `callback_query` (Inline Keyboard).
* **Rate Limiting:** Check the user's `usage_count` for today. Limit is 5 per day. Reset count if `last_reset_date` is not today.
* **Commands (`/start` or `/voice`):** Reply with an Inline Keyboard containing 3 buttons: 
    * "👨 Default Male" (callback: "male")
    * "👩 Default Female" (callback: "female")
    * "🎙️ Clone My Voice" (callback: "custom")
* **Callback Query Handler:** Update `user.selected_voice` in the DB. If "custom" without a saved `custom_voice_b64`, ask the user to upload a voice note.
* **Voice Note Handler:** If a user sends a voice note, download it from Telegram, convert to base64, save to `user.custom_voice_b64`, and set `user.selected_voice = "custom"`.
* **Text Handler (Generation):**
    * If `text`, check if user has custom mode active but no custom voice saved (fail gracefully).
    * Send JSON request to `MODAL_API_URL` with `X-API-Key` header, passing `text`, `voice_mode`, and `reference_audio` (if custom).
    * Receive the `.wav` file and send it back to the user via Telegram's `sendVoice` API.
    * Increment `usage_count` and save.

**5. `config/urls.py`**
Route `/telegram-webhook/` to the view in `bot/views.py`.

---

### EXECUTION INSTRUCTIONS FOR AI AGENT
1. Generate the `modal_api.py` script completely.
2. Generate the Django project structure, `requirements.txt`, `settings.py`, `models.py`, `views.py`, and `urls.py`.
3. Provide final instructions on how to run migrations and deploy (e.g., Koyeb run command: `python manage.py migrate && gunicorn config.wsgi:application --bind 0.0.0.0:8000`).

Begin implementation now.