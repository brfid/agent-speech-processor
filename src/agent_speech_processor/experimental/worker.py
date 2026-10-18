"""One bounded model call. Only this child process imports MLX or holds weights."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
import time

from ..records import atomic, checksum, json_bytes


def infer(request):
    # Prevent hidden downloads while private audio is in use.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    from mlx_audio.stt.utils import load_model

    mx.set_memory_limit(9 * 1024**3)
    mx.set_cache_limit(256 * 1024**2)
    mx.random.seed(0)
    backend = request["backend"]
    if backend not in ("moss", "qwen2-audio", "gemma3n"):
        raise ValueError("Unsupported local backend")
    folder = Path(request["model"]).resolve(strict=True)
    config = json.loads((folder / "config.json").read_text())
    expected = {"moss": "moss_transcribe_diarize", "qwen2-audio": "qwen2_audio", "gemma3n": "gemma3n"}[backend]
    if config.get("model_type") != expected:
        raise ValueError("Model architecture does not match the requested backend")
    audio, consumed = [], []
    for item in request["audio"]:
        path = Path(item["path"])
        if checksum(path) != item["sha256"]:
            raise ValueError("Input audio changed before model inference")
        data, rate = sf.read(path, dtype="float32")
        duration = len(data) / rate
        if rate != 16000 or data.ndim != 1 or not 0 < duration <= 30:
            raise ValueError("Model inputs must be nonempty mono 16 kHz clips of at most 30 seconds")
        if not np.isfinite(data).all():
            raise ValueError("Audio contains nonfinite samples")
        audio.append(data)
        consumed.append(dict(item, samples=len(data), sample_rate=rate, duration=duration))
    if not audio or (backend == "moss" and len(audio) != 1) or len(audio) > 5:
        raise ValueError("MOSS takes one mix; the reviewer takes at most five simultaneous views")
    start = time.monotonic()
    if backend == "gemma3n":
        from mlx_vlm import load, generate
        from mlx_vlm.prompt_utils import apply_chat_template
        model, processor = load(str(folder), trust_remote_code=False)
        if len(audio) > 1 and not getattr(processor, "supports_multiple_audio", False):
            raise ValueError("This Gemma processor would silently discard separate audio views; refusing")
        prompt = apply_chat_template(processor, model.config, request["prompt"], num_audios=len(audio))
        result = generate(model, processor, prompt, audio=audio,
                          max_tokens=request["max_tokens"], temperature=0.0,
                          prefill_step_size=256, verbose=False)
        output_segments = []  # Only the structured reviewer response owns its times.
    else:
        model = load_model(str(folder))
        result = model.generate(audio[0] if backend == "moss" else audio,
                                prompt=request.get("prompt"),
                                max_tokens=request["max_tokens"], temperature=0.0,
                                prefill_step_size=256, verbose=False)
        output_segments = result.segments
    for item in consumed:
        if checksum(item["path"]) != item["sha256"]:
            raise ValueError("Input audio changed during model inference")
    return {"ok": True, "backend": backend, "audio_consumed": consumed,
            "text": result.text, "segments": output_segments,
            "generation_tokens": result.generation_tokens,
            "truncated": result.generation_tokens >= request["max_tokens"],
            "elapsed_seconds": time.monotonic() - start,
            "peak_memory_bytes": mx.get_peak_memory()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("response")
    args = parser.parse_args()
    # Third-party diagnostics never mix with our structured response.
    with contextlib.redirect_stdout(sys.stderr):
        result = infer(json.loads(Path(args.request).read_text()))
    atomic(args.response, json_bytes(result), replace=False)


if __name__ == "__main__":
    main()
