"""Sequential, resumable experimental audio review and qualification gates."""
import json
from pathlib import Path
import re

from ..inputs import prepare_sources
from ..job import job
from ..records import checksum, digest, json_bytes
from ..drafts import import_draft
from ..session import load_manifest, intake
from .audio import windows, prepare_window
from . import review, render
from .metrics import coverage


def window_result(tmp, source, span, streams, baseline, vocabulary, recipe):
    audio = prepare_window(source, span, streams, tmp)
    diarization = review.call_model(tmp, "diarizer", "moss", recipe["diarizer"]["path"], audio[:1])
    result = {"span": span, "accepted": False, "audio": audio}
    try:
        candidates = review.moss_segments(diarization, span["end"] - span["start"])
    except (ValueError, KeyError, TypeError) as exc:
        result["diarization_error"] = str(exc)
        candidates = []
    prompt = review.request_prompt(span, baseline, candidates, vocabulary)
    response = review.call_model(tmp, "reviewer", recipe["reviewer_backend"], recipe["reviewer"]["path"], audio, prompt)
    result["diarization"] = candidates
    try:
        result["review"] = review.validate_response(response, span, baseline, candidates, vocabulary, audio)
        # A failed second pass must remain visible; do not downgrade the method silently.
        if "diarization_error" in result:
            raise ValueError("Diarization failed: " + result["diarization_error"])
        result["accepted"] = True
    except (ValueError, KeyError, TypeError) as exc:
        result["error"] = str(exc)
    (tmp / "result.json").write_bytes(json_bytes(result))
    return result


def check_qualification(path, recipe):
    state = json.loads(Path(path).read_text())
    metrics = state.get("metrics", [])
    if (state.get("recipe_sha256") != digest(recipe) or not state.get("passed")
            or state.get("cases", 0) < 3 or len(metrics) != state["cases"]
            or not state.get("reference_sha256") or not any(m.get("challenge") for m in metrics)
            or not all(m.get("accepted") and m["corrected"]["errors"] <= m["baseline"]["errors"]
                       and m["corrected_speakers"]["errors"] <= m["baseline_speakers"]["errors"]
                       and (not m.get("challenge") or m["corrected"]["errors"] < m["baseline"]["errors"])
                       for m in metrics)):
        raise ValueError("Reviewer has no passing reference qualification for this exact recipe")
    return checksum(path)


def process(args, recipe):
    manifest_path, manifest = load_manifest(args.session, args.fetch)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}", args.variant):
        raise ValueError("Variant must be a short single folder name")
    gate = None
    if not args.pilot:
        if not args.qualification:
            raise ValueError("A passing --qualification is required; --pilot never publishes a reviewed transcript")
        gate = check_qualification(args.qualification, recipe)
    if args.limit_windows is not None and args.limit_windows < 1:
        raise ValueError("--limit-windows must be positive")
    summaries = []
    for entry in manifest["recordings"]:
        source, derivative = entry["source"], entry["draft"]
        draft, media, fetch_receipts = intake(entry, args.fetch)
        output = derivative / "reviews" / args.variant
        settings = {"recipe": recipe, "manifest_sha256": checksum(manifest_path),
                    "draft_receipt_sha256": draft["receipt_sha256"],
                    "qualification_sha256": gate, "pilot": args.pilot}
        with job(output, "audio-review", prepare_sources([source, manifest_path]), settings, args.fetch) as current:
            if current.source_checksum(source) != entry["source_sha256"]:
                raise ValueError("Original changed after intake")
            def save_intake(tmp):
                (tmp / "intake.json").write_bytes(json_bytes({"media": media, "draft": draft,
                                                              "fetch_receipts": fetch_receipts}))
                return {"intake.json": tmp / "intake.json"}, {"verified": True}
            current.step("intake", save_intake)
            results, new = [], 0
            for span in windows(draft["duration"], recipe["core_seconds"], recipe["context_seconds"]):
                base = f"window-{span['index']:06d}"
                attempts = sorted(k for k in current.state["steps"] if k.startswith(base + "-attempt-"))
                name = attempts[-1] if attempts else base + "-attempt-001"
                old = current.state["steps"].get(name)
                if (old and old.get("status") == "done" and not old["details"].get("accepted") and args.retry_failed):
                    name = f"{base}-attempt-{len(attempts) + 1:03d}"
                done = current.completed(name)
                if args.limit_windows is not None and new >= args.limit_windows and not done:
                    break
                def build(tmp, span=span, name=name):
                    result = window_result(tmp, source, span, entry["audio_streams"], draft["segments"],
                                           manifest.get("vocabulary", []), recipe)
                    # Temporary clip bytes are disposable; hashes/locators and raw responses persist.
                    files = {f"windows/{name}/{p.name}": p for p in tmp.iterdir() if p.suffix != ".wav"}
                    return files, {"accepted": result["accepted"], "result": f"windows/{name}/result.json"}
                detail = current.step(name, build)
                result = json.loads(current.output_path(detail["result"]).read_text())
                results.append(result)
                new += not done
                print(json.dumps({"recording": source.name, "window": span["index"],
                                  "accepted": detail["accepted"], "reused": done}), flush=True)
            report = {"schema": 1, "source": str(source), "source_sha256": entry["source_sha256"],
                      "recipe_sha256": digest(recipe), "qualification_sha256": gate,
                      "pilot": args.pilot, "coverage": coverage(draft["duration"], results), "windows": results}
            report["publishable"] = bool(gate and report["coverage"]["complete"])
            name = "report-" + digest(report)[:16]
            content = render.transcript(source, report, recipe)
            def report_files(tmp):
                (tmp / "report.json").write_bytes(json_bytes(report))
                (tmp / "report.md").write_text(content)
                return {f"reports/{name}.json": tmp / "report.json", f"reports/{name}.md": tmp / "report.md"}, {"publishable": report["publishable"]}
            current.step(name, report_files)
            current.verify_inputs_unchanged()
            # Recheck imported receipts/artifacts, so a concurrent draft edit cannot be published.
            import_draft(derivative, source, entry["source_sha256"], entry["draft_recipe_sha256"])
            published = None
            if report["publishable"]:
                published = render.publish(derivative, content, digest(recipe), entry["source_sha256"])
            current.write_readme("# Automated audio review variant\n\n"
                                 "Original and draft remain unchanged. See `reports/` for exact coverage, "
                                 "model method, unresolved spans and anonymous speaker evidence; "
                                 "`windows/` retains every model request and response. `intake.json` "
                                 "records source/draft verification. A pilot is experimental, not qualified.\n")
            summaries.append({"source": str(source), "output": str(output), "report": str(output / f"reports/{name}.json"),
                              "coverage": report["coverage"], "published": published})
    return {"recordings": summaries, "complete": all(r["published"] for r in summaries)}
