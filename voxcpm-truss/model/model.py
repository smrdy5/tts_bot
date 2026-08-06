import base64
import io
import os
import tempfile
import re
import numpy as np
import soundfile as sf
import torch
from voxcpm import VoxCPM

class Model:
    def __init__(self, **kwargs):
        self._model = None
        self._sample_rate = None

    def load(self):
        # This runs once when the Baseten container boots
        self._model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
        self._model.to(device="cuda", dtype=torch.float16)
        self._sample_rate = self._model.tts_model.sample_rate

    def predict(self, request: dict):
        # This runs every time Django sends a request to Baseten
        raw_text = request.get("text", "Hello.")
        voice_mode = request.get("voice_mode", "male")
        ref_audio_b64 = request.get("reference_audio", None)

        kwargs = {"cfg_value": 2.0, "inference_timesteps": 10}
        temp_wav = None

        # Access the audio files stored in the Truss 'data' folder
        data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))

        if voice_mode == "male":
            kwargs["reference_wav_path"] = os.path.join(data_dir, "default_male.wav")
        elif voice_mode == "female":
            kwargs["reference_wav_path"] = os.path.join(data_dir, "default_female.wav")
        elif voice_mode == "custom" and ref_audio_b64:
            temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            temp_wav.write(base64.b64decode(ref_audio_b64))
            temp_wav.close()
            kwargs["reference_wav_path"] = temp_wav.name

        # Chunk the text for memory safety
        chunks = [c.strip() for c in re.split(r'(?<=[.!?]) +|\n+', raw_text) if c.strip()]
        if not chunks:
            chunks = [raw_text]

        audio_segments = []
        for chunk in chunks:
            if not chunk:
                continue
            kwargs["text"] = chunk
            audio_segments.append(self._model.generate(**kwargs))

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
        sf.write(buffer, final_audio, self._sample_rate, format='WAV')

        # Baseten requires returning base64 strings for binary data like audio
        audio_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

        return {"audio_base64": audio_b64}
