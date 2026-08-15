import os
import re
import tempfile
import numpy as np
import soundfile as sf
import torch
import gradio as gr
import spaces
from voxcpm import VoxCPM

# Load model globally
print("Loading openbmb/VoxCPM2 model on Hugging Face ZeroGPU...")
device = "cuda" if torch.cuda.is_available() else "cpu"
model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
if torch.cuda.is_available():
    try:
        model.to(device="cuda")
    except AttributeError:
        if hasattr(model, 'tts_model') and model.tts_model is not None:
            model.tts_model.to(device="cuda")
        if hasattr(model, 'vocoder') and model.vocoder is not None:
            model.vocoder.to(device="cuda")
sample_rate = getattr(getattr(model, "tts_model", None), "sample_rate", 16000)


@spaces.GPU
def predict(text: str, voice_mode: str, reference_audio: str = None):
    if not text:
        text = "Hello."

    kwargs = {"cfg_value": 2.0, "inference_timesteps": 25}

    assets_dir = os.path.abspath("assets")
    if not os.path.exists(assets_dir):
        assets_dir = os.path.abspath(".")

    if voice_mode == "male":
        kwargs["reference_wav_path"] = os.path.join(assets_dir, "default_male.wav")
    elif voice_mode == "female":
        kwargs["reference_wav_path"] = os.path.join(assets_dir, "default_female.wav")
    elif voice_mode == "custom" and reference_audio:
        kwargs["reference_wav_path"] = reference_audio
    else:
        kwargs["reference_wav_path"] = os.path.join(assets_dir, "default_male.wav")

    chunks = [c.strip() for c in re.split(r"(?<=[.!?]) +|\n+", text) if c.strip()]
    if not chunks:
        chunks = [text]

    audio_segments = []
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

    if audio_segments:
        final_audio = np.concatenate(audio_segments, axis=0)
    else:
        final_audio = np.array([], dtype=np.float32)

    out_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    sf.write(out_file.name, final_audio, sample_rate, format="WAV")
    out_file.close()

    return out_file.name


demo = gr.Interface(
    fn=predict,
    inputs=[
        gr.Textbox(label="Text to Synthesize", value="Hello from VoxCPM!"),
        gr.Radio(choices=["male", "female", "custom"], label="Voice Mode", value="male"),
        gr.Audio(label="Reference Audio (for Custom Voice)", type="filepath"),
    ],
    outputs=gr.Audio(label="Generated Audio", type="filepath"),
    title="VoxCPM Voice Generator (Hugging Face ZeroGPU)",
    api_name="predict",
)

if __name__ == "__main__":
    demo.launch()
