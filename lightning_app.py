import os
import re
import io
import base64
import tempfile
import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, Header, HTTPException, Response, Request
from voxcpm import VoxCPM

app = FastAPI(title="VoxCPM AI Engine for Lightning AI Studio")

model = None
sample_rate = 16000


@app.on_event("startup")
def load_voxcpm_model():
    global model, sample_rate
    print("Loading openbmb/VoxCPM2 model on GPU...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
    model.to(device=device, dtype=dtype)
    sample_rate = getattr(getattr(model, "tts_model", None), "sample_rate", 16000)
    print(f"VoxCPM2 Model loaded successfully on {device} with sample rate {sample_rate}!")


@app.get("/")
def health_check():
    return {"status": "ok", "service": "VoxCPM Lightning AI Engine"}


@app.post("/generate")
async def generate_speech(request: Request, x_api_key: str = Header(None, alias="X-API-Key")):
    expected_key = os.getenv("API_SECRET_KEY")
    if expected_key and x_api_key != expected_key:
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await request.json()
    raw_text = data.get("text", "Hello.")
    voice_mode = data.get("voice_mode", "male")
    ref_audio_b64 = data.get("reference_audio", None)

    kwargs = {"cfg_value": 2.0, "inference_timesteps": 10}
    temp_wav = None

    assets_dir = os.path.abspath("assets")

    if voice_mode == "male":
        kwargs["reference_wav_path"] = os.path.join(assets_dir, "default_male.wav")
    elif voice_mode == "female":
        kwargs["reference_wav_path"] = os.path.join(assets_dir, "default_female.wav")
    elif voice_mode == "custom" and ref_audio_b64:
        temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        temp_wav.write(base64.b64decode(ref_audio_b64))
        temp_wav.close()
        kwargs["reference_wav_path"] = temp_wav.name

    chunks = [c.strip() for c in re.split(r"(?<=[.!?]) +|\n+", raw_text) if c.strip()]
    if not chunks:
        chunks = [raw_text]

    audio_segments = []
    try:
        for chunk in chunks:
            if not chunk:
                continue
            kwargs["text"] = chunk
            gen_result = model.generate(**kwargs)
            if isinstance(gen_result, tuple):
                chunk_audio, sr = gen_result
            else:
                chunk_audio = gen_result
            audio_segments.append(chunk_audio)
    finally:
        if temp_wav and os.path.exists(temp_wav.name):
            try:
                os.remove(temp_wav.name)
            except Exception:
                pass

    if audio_segments:
        final_audio = np.concatenate(audio_segments, axis=0)
    else:
        final_audio = np.array([], dtype=np.float32)

    buffer = io.BytesIO()
    sf.write(buffer, final_audio, sample_rate, format="WAV")

    return Response(content=buffer.getvalue(), media_type="audio/wav")
