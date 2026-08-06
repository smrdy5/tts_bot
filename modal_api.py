import modal
from fastapi import Request, Response
import io
import base64
import os
import re
import numpy as np

app = modal.App("voxcpm-cloud-api")

# Modal 1.0 Image definition with mounted audio files
image = (
    modal.Image.debian_slim()
    .pip_install("torch", "soundfile", "voxcpm", "fastapi")
    .add_local_file("default_male.wav", remote_path="/assets/default_male.wav")
    .add_local_file("default_female.wav", remote_path="/assets/default_female.wav")
)

@app.cls(
    image=image, 
    gpu="T4", 
    timeout=300,
    secrets=[modal.Secret.from_name("voxcpm-secret")] if hasattr(modal.Secret, "from_name") else []
)
class VoxCPMService:
    @modal.enter()
    def load_model(self):
        import torch
        from voxcpm import VoxCPM
        self.model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
        self.model.to(device="cuda", dtype=torch.float16)
        self.sample_rate = self.model.tts_model.sample_rate

    @modal.fastapi_endpoint(method="POST")
    async def generate(self, request: Request):
        if request.headers.get("X-API-Key") != os.getenv("API_SECRET_KEY"):
            return Response(content="Unauthorized", status_code=401)
            
        import soundfile as sf
        import tempfile
        
        data = await request.json()
        raw_text = data.get("text", "Hello.")
        voice_mode = data.get("voice_mode", "male")
        ref_audio_b64 = data.get("reference_audio", None)
        
        kwargs = {"cfg_value": 2.0, "inference_timesteps": 10}
        temp_wav = None

        if voice_mode == "male":
            kwargs["reference_wav_path"] = "/assets/default_male.wav"
        elif voice_mode == "female":
            kwargs["reference_wav_path"] = "/assets/default_female.wav"
        elif voice_mode == "custom" and ref_audio_b64:
            temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            temp_wav.write(base64.b64decode(ref_audio_b64))
            temp_wav.close()
            kwargs["reference_wav_path"] = temp_wav.name

        chunks = [c.strip() for c in re.split(r'(?<=[.!?]) +|\n+', raw_text) if c.strip()]
        if not chunks: chunks = [raw_text]
        
        audio_segments = []
        for chunk in chunks:
            if not chunk: continue 
            kwargs["text"] = chunk
            audio_segments.append(self.model.generate(**kwargs))
            
        if temp_wav: os.remove(temp_wav.name)

        final_audio = np.concatenate(audio_segments, axis=0)
        buffer = io.BytesIO()
        sf.write(buffer, final_audio, self.sample_rate, format='WAV')
        
        return Response(content=buffer.getvalue(), media_type="audio/wav")