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
        model.to(device=device, dtype=torch.float16)
    except AttributeError:
        if hasattr(model, 'tts_model') and model.tts_model is not None:
            model.tts_model.to(device=device, dtype=torch.float16)
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
    else:
        form = await request.form()
        raw_text = form.get("text", "Hello.")

    chunks = [c.strip() for c in re.split(r"(?<=[.!?]) +|\n+", raw_text) if c.strip()]
    if not chunks:
        chunks = [raw_text]

    audio_segments = []
    for chunk in chunks:
        if not chunk:
            continue
        gen_result = model.generate(text=chunk, cfg_value=2.0, inference_timesteps=25)
        if isinstance(gen_result, tuple):
            chunk_audio, _ = gen_result
        else:
            chunk_audio = gen_result
        audio_segments.append(chunk_audio)

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
                print(f"🚀 COPY THIS CLOUDFLARE URL TO RENDER (COLAB_API_URL):")
                print(f"   {public_url}")
                print("=======================================================\n")
                break

    # Start the tunnel in a background thread so it doesn't block uvicorn
    threading.Thread(target=start_cloudflare_tunnel, daemon=True).start()
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
