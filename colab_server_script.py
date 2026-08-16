# ==============================================================================
# VoxCPM Google Colab FastAPI Server Script
# Run this cell in Google Colab to host your VoxCPM API via ngrok
# ==============================================================================
#
# STEP 1: Install dependencies in Colab:
# !pip install torch soundfile voxcpm fastapi uvicorn pyngrok nest_asyncio
#
# STEP 2: Paste and run this script in Colab:

import nest_asyncio
import uvicorn
from fastapi import FastAPI, Request, Response
import torch
import ssl

# Fix SSL Certificate Error for ngrok download
ssl._create_default_https_context = ssl._create_unverified_context
import numpy as np
import soundfile as sf
import io
import re
from voxcpm import VoxCPM

nest_asyncio.apply()

app = FastAPI(title="VoxCPM Google Colab API Engine")

print("Loading openbmb/VoxCPM2 model on GPU...")
device = "cuda" if torch.cuda.is_available() else "cpu"
model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
if torch.cuda.is_available():
    try:
        model.to(device=device)
    except AttributeError:
        if hasattr(model, 'tts_model') and model.tts_model is not None:
            model.tts_model.to(device=device)
        if hasattr(model, 'vocoder') and model.vocoder is not None:
            model.vocoder.to(device=device)
sample_rate = getattr(getattr(model, "tts_model", None), "sample_rate", 16000)
print(f"VoxCPM Model loaded on {device} (Sample rate: {sample_rate}Hz)")

@app.get("/")
def root():
    return {"status": "ok", "service": "VoxCPM Colab API"}

@app.post("/generate")
@app.post("/")
async def generate_speech(request: Request):
    content_type = request.headers.get("content-type", "")
    raw_text = "Hello."

    if "application/json" in content_type:
        data = await request.json()
        raw_text = data.get("text", "Hello.")
        voice_mode = data.get("voice_mode", "male")
        ref_b64 = data.get("reference_audio")
    else:
        form = await request.form()
        raw_text = form.get("text", "Hello.")
        voice_mode = form.get("voice_mode", "male")
        ref_b64 = form.get("reference_audio")

    kwargs = {"cfg_value": 2.0, "inference_timesteps": 25}
    temp_ref_path = None
    
    if ref_b64:
        import base64
        import tempfile
        import os
        try:
            if ref_b64.startswith("data:"):
                ref_b64 = ref_b64.split(",")[1]
            wav_data = base64.b64decode(ref_b64)
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            temp_file.write(wav_data)
            temp_file.close()
            temp_ref_path = temp_file.name
            kwargs["reference_wav_path"] = temp_ref_path
        except Exception as e:
            print(f"Error decoding reference audio: {e}")
            raw_text = f"({voice_mode}) {raw_text}"
    else:
        raw_text = f"({voice_mode}) {raw_text}"

    chunks = [c.strip() for c in re.split(r"(?<=[.!?]) +|\n+", raw_text) if c.strip()]
    if not chunks:
        chunks = [raw_text]

    audio_segments = []
    
    # Force the local AI to be completely deterministic (no random voices)
    torch.manual_seed(42)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(42)
        
    for chunk in chunks:
        if not chunk:
            continue
        kwargs["text"] = chunk
        gen_result = model.generate(**kwargs)
        if isinstance(gen_result, tuple):
            chunk_audio, _ = gen_result
        else:
            chunk_audio = gen_result
        audio_segments.append(chunk_audio)

    if temp_ref_path and os.path.exists(temp_ref_path):
        try:
            os.remove(temp_ref_path)
        except:
            pass

    if audio_segments:
        final_audio = np.concatenate(audio_segments, axis=0)
    else:
        final_audio = np.array([], dtype=np.float32)

    buffer = io.BytesIO()
    sf.write(buffer, final_audio, sample_rate, format="WAV")
    return Response(content=buffer.getvalue(), media_type="audio/wav")

if __name__ == "__main__":
    import threading
    import subprocess
    import urllib.request
    import os
    import re

    def start_cloudflare_tunnel():
        exe_name = "cloudflared.exe" if os.name == 'nt' else "cloudflared"
        if not os.path.exists(exe_name):
            print("Downloading Cloudflare Tunnel...")
            url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" if os.name == 'nt' else "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
            urllib.request.urlretrieve(url, exe_name)
            if os.name != 'nt':
                os.chmod(exe_name, 0o755)
        
        print("Starting Cloudflare Tunnel...")
        cmd = [exe_name, "tunnel", "--url", "http://127.0.0.1:8000"]
        if os.name != 'nt': cmd[0] = f"./{exe_name}"
        
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        
        for line in iter(proc.stderr.readline, ''):
            match = re.search(r'https://[-a-zA-Z0-9]+\.trycloudflare\.com', line)
            if match:
                public_url = match.group(0)
                print("\n=======================================================")
                print(f"🚀 LOCAL SERVER TUNNEL IS LIVE!")
                print(f"URL: {public_url}")
                print("\n👉 OPTION 1: Just copy the URL above and send it as a message to your Telegram Bot!")
                print("=======================================================\n")
                
                # AUTOMATIC TUNNEL SYNC:
                # If you host your Telegram bot on Render, paste your app URL below to automatically 
                # send this new tunnel URL to your bot every time you start the server!
                RENDER_BOT_WEBHOOK = "" # Example: "https://my-tts-bot.onrender.com"
                
                if RENDER_BOT_WEBHOOK and "http" in RENDER_BOT_WEBHOOK:
                    try:
                        print(f"Syncing tunnel automatically with your bot at {RENDER_BOT_WEBHOOK} ...")
                        import requests
                        endpoint = RENDER_BOT_WEBHOOK.rstrip("/") + "/update-tunnel/"
                        requests.post(endpoint, json={"url": public_url}, timeout=5)
                        print("✅ Successfully synced local server with your Telegram Bot!")
                    except Exception as e:
                        print(f"⚠️ Failed to sync with bot webhook: {e}")
                break

    # Start the tunnel in a background thread so it doesn't block uvicorn
    threading.Thread(target=start_cloudflare_tunnel, daemon=True).start()
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
