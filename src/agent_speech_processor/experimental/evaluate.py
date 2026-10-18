"""Fixed-reference qualification. Reference wording never enters model prompts."""
import json
from pathlib import Path
import shutil

from ..records import checksum, digest, json_bytes
from ..job import job
from ..inputs import prepare_sources
from ..processes import run
from ..audio import probe
from .audio import prepare_window
from .pipeline import window_result
from ..drafts import segments
from .metrics import errors, speaker_errors


def evaluate(args, recipe):
    manifest = Path(args.references).expanduser().resolve()
    data = json.loads(manifest.read_text())
    cases = data.get("cases", [])
    if data.get("schema") != 1 or len(cases) < 3:
        raise ValueError("Qualification needs at least three fixed reference cases")
    if not any(c.get("challenge") for c in cases) or not any(len({s['speaker'] for s in c['reference']}) > 1 for c in cases):
        raise ValueError("Include an audio-vs-draft challenge and a multi-speaker reference")
    for case in cases:
        case["source"] = (manifest.parent / case["source"]).resolve()
        if checksum(case["source"]) != case["source_sha256"]:
            raise ValueError("Reference audio changed")
    sources = prepare_sources([manifest, *dict.fromkeys(c["source"] for c in cases)])
    model = Path(args.whisper_model).expanduser().resolve()
    engine = shutil.which("whisper-cli")
    if not engine:
        raise FileNotFoundError("whisper-cli is required for the reference baseline")
    settings = {"recipe": recipe, "reference_sha256": checksum(manifest),
                "whisper_model_sha256": checksum(model), "whisper_engine_sha256": checksum(engine),
                "whisper_max_context": 0, "whisper_device": "gpu"}
    metrics = []
    with job(args.output, "review-qualification", sources, settings, False) as current:
        for index, case in enumerate(cases):
            name = f"case-{index:03d}"
            def build(tmp, case=case, name=name):
                duration = probe(case["source"])["duration"]
                if duration > recipe["core_seconds"]:
                    raise ValueError("Each qualification case must fit one review core")
                span = {"index": 0, "start": 0, "end": duration, "core_start": 0, "core_end": duration}
                baseline_dir = tmp / "baseline"
                baseline_dir.mkdir()
                audio = prepare_window(case["source"], span, case["audio_streams"], baseline_dir)
                result = run([engine, "-m", model, "-f", audio[0]["path"], "-l", "en", "-mc", 0,
                              "-oj", "-of", baseline_dir / "whisper"], timeout=300)
                (baseline_dir / "whisper.log").write_bytes(result.stderr)
                baseline = [{"start": a, "end": min(b, duration), "text": text, "speaker": "unknown"}
                            for a, b, text in segments(json.loads((baseline_dir / "whisper.json").read_text()), duration)
                            if a < min(b, duration) and text]
                if case.get("challenge"):
                    # Deliberately contradict the audio without showing the reference to the model.
                    if not baseline:
                        raise ValueError("Empty challenge baseline")
                    parts = baseline[0]["text"].split()
                    position = min(2, len(parts) - 1)
                    parts[position] = "banana"
                    baseline[0]["text"] = " ".join(parts)
                checked = window_result(tmp, case["source"], span, case["audio_streams"], baseline, [], recipe)
                # Assign baseline's speaker only by candidate time overlap; no reference leakage.
                for segment in baseline:
                    matches = [(max(0, min(segment['end'], d['end']) - max(segment['start'], d['start'])), d)
                               for d in checked["diarization"]]
                    if matches:
                        overlap, best = max(matches, key=lambda pair: pair[0])
                        if overlap > 0:
                            segment["speaker"] = best["speaker"]
                hypothesis = checked.get("review", {}).get("segments", []) if checked["accepted"] else baseline
                reference = case["reference"]
                text = lambda spans: " ".join(s["text"] for s in sorted(spans, key=lambda s:s["start"]))
                score = {"id": case["id"], "accepted": checked["accepted"],
                         "error": checked.get("error"), "challenge": bool(case.get("challenge")),
                         "baseline": errors(text(reference), text(baseline)),
                         "corrected": errors(text(reference), text(hypothesis)),
                         "baseline_speakers": speaker_errors(reference, baseline),
                         "corrected_speakers": speaker_errors(reference, hypothesis)}
                (tmp / "metrics.json").write_bytes(json_bytes(score))
                (tmp / "baseline.json").write_bytes(json_bytes(baseline))
                files = {f"{name}/{p.relative_to(tmp)}": p for p in tmp.rglob("*") if p.is_file() and p.suffix != ".wav"}
                return files, score
            metrics.append(current.step(name, build))
            print(json.dumps(metrics[-1]), flush=True)
        passed = (all(m["accepted"] and m["corrected"]["errors"] <= m["baseline"]["errors"]
                      and m["corrected_speakers"]["errors"] <= m["baseline_speakers"]["errors"] for m in metrics)
                  and all(m["corrected"]["errors"] < m["baseline"]["errors"] for m in metrics if m["challenge"]))
        result = {"schema": 1, "recipe_sha256": digest(recipe), "recipe": recipe,
                  "reference_sha256": checksum(manifest), "cases": len(cases), "passed": passed,
                  "metrics": metrics, "baseline": settings,
                  "scope": "Small fixed public speech sample and constructed speaker/overlap cases; not interview accuracy"}
        def finish(tmp):
            (tmp / "qualification.json").write_bytes(json_bytes(result))
            return {"qualification.json": tmp / "qualification.json"}, {"passed": passed}
        current.verify_inputs_unchanged()
        current.step("qualification", finish)
        return result
