import os
import re
import io
import base64
import tempfile
import numpy as np
import modal
from fastapi import Request, Response

app = modal.App("voxcpm-cloud-api")

image = (
    modal.Image.debian_slim(python_version="3.10")
    .pip_install("torch", "soundfile", "voxcpm", "fastapi", "numpy")
    .add_local_dir("./assets", remote_path="/assets")
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

        print("Loading VoxCPM2 model...")
        self.model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
        self.model.to(device="cuda", dtype=torch.float16)
        self.sample_rate = getattr(getattr(self.model, "tts_model", None), "sample_rate", 16000)

    @modal.fastapi_endpoint(method="POST")
    async def generate(self, request: Request):
        expected_key = os.getenv("API_SECRET_KEY")
        if expected_key and request.headers.get("X-API-Key") != expected_key:
            return Response(content="Unauthorized", status_code=401)

        import soundfile as sf

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

        chunks = [c.strip() for c in re.split(r"(?<=[.!?]) +|\n+", raw_text) if c.strip()]
        if not chunks:
            chunks = [raw_text]

        audio_segments = []
        try:
            for chunk in chunks:
                if not chunk:
                    continue
                kwargs["text"] = chunk
                gen_result = self.model.generate(**kwargs)
                if isinstance(gen_result, tuple):
                    chunk_audio, sr = gen_result
                    self.sample_rate = sr
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
        sf.write(buffer, final_audio, self.sample_rate, format="WAV")

        return Response(content=buffer.getvalue(), media_type="audio/wav")