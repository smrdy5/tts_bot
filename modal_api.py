import os
import re
import io
import tempfile
import base64
import numpy as np
import soundfile as sf
import modal
from fastapi import Header, HTTPException, Response

# Define container image dependencies and mount local assets
image = (
    modal.Image.debian_slim(python_version="3.10")
    .pip_install(
        "torch",
        "soundfile",
        "voxcpm",
        "fastapi",
        "numpy",
    )
    .add_local_dir("./assets", remote_path="/assets")
)

# Define Modal App
app = modal.App("voxcpm-cloud-api")


@app.cls(
    gpu="T4",
    timeout=300,
    image=image,
    secrets=[modal.Secret.from_name("voxcpm-secret")] if hasattr(modal.Secret, "from_name") else []
)
class VoxCPMService:
    @modal.enter()
    def load_model(self):
        import torch
        from voxcpm import VoxCPM

        print("Loading openbmb/VoxCPM2 model...")
        self.model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
        self.model.to(device="cuda", dtype=torch.float16)
        self.model.eval()

    @modal.fastapi_endpoint(method="POST")
    def generate_speech(self, req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
        # Authentication check
        expected_api_key = os.environ.get("API_SECRET_KEY")
        if expected_api_key and x_api_key != expected_api_key:
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid API key")

        text = req.get("text", "")
        voice_mode = req.get("voice_mode", "male")
        reference_audio_b64 = req.get("reference_audio")

        if not text:
            raise HTTPException(status_code=400, detail="Missing required field: text")

        temp_audio_path = None
        try:
            # Voice Routing
            if voice_mode == "male":
                ref_wav_path = "/assets/default_male.wav"
            elif voice_mode == "female":
                ref_wav_path = "/assets/default_female.wav"
            elif voice_mode == "custom":
                if not reference_audio_b64:
                    raise HTTPException(
                        status_code=400, detail="Custom voice mode requires reference_audio base64"
                    )

                # Decode base64 to temporary .wav file
                audio_bytes = base64.b64decode(reference_audio_b64)
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
                temp_file.write(audio_bytes)
                temp_file.close()
                temp_audio_path = temp_file.name
                ref_wav_path = temp_audio_path
            else:
                raise HTTPException(status_code=400, detail=f"Invalid voice_mode: {voice_mode}")

            # Chunk text by punctuation (periods, exclamations, question marks, newlines)
            raw_chunks = re.split(r"[\.\!\?\;\n]+", text)
            chunks = [c.strip() for c in raw_chunks if c.strip()]
            if not chunks:
                chunks = [text]

            audio_chunks = []
            sample_rate = 16000

            # Generate audio chunks sequentially to avoid CUDA OOM
            for chunk in chunks:
                gen_result = self.model.generate(
                    text=chunk,
                    reference_wav_path=ref_wav_path,
                    cfg_value=2.0,
                    inference_timesteps=10,
                )
                if isinstance(gen_result, tuple):
                    chunk_audio, chunk_sr = gen_result
                    sample_rate = chunk_sr
                else:
                    chunk_audio = gen_result

                audio_chunks.append(chunk_audio)

            if audio_chunks:
                full_audio = np.concatenate(audio_chunks, axis=0)
            else:
                full_audio = np.array([], dtype=np.float32)

            # Encode audio to WAV format
            buffer = io.BytesIO()
            sf.write(buffer, full_audio, sample_rate, format="WAV")
            buffer.seek(0)
            wav_bytes = buffer.getvalue()

            return Response(content=wav_bytes, media_type="audio/wav")

        finally:
            if temp_audio_path and os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                except Exception:
                    pass
