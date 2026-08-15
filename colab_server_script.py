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
import numpy as np
import soundfile as sf
import io
import re
from voxcpm import VoxCPM
from pyngrok import ngrok

nest_asyncio.apply()

app = FastAPI(title="VoxCPM Google Colab API Engine")

print("Loading openbmb/VoxCPM2 model on GPU...")
device = "cuda" if torch.cuda.is_available() else "cpu"
model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
if torch.cuda.is_available():
    model.to(device=device, dtype=torch.float16)
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
    # If using ngrok authtoken, un-comment line below:
    # ngrok.set_auth_token("YOUR_NGROK_AUTHTOKEN")
    
    public_url = ngrok.connect(8000)
    print("\n=======================================================")
    print(f"🚀 COPY THIS NGROK URL TO RENDER (COLAB_API_URL):")
    print(f"   {public_url}")
    print("=======================================================\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
