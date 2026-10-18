"""Agent speech processor: local drafts, source timecodes, and passage playback."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import __version__

LOCAL_CONFIG = Path.home() / ".config/agent-speech-processor/workspace.json"


def context_setting(value):
    if value == "default":
        return None
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a nonnegative integer or 'default'") from exc
    if count < 0:
        raise argparse.ArgumentTypeError("Text context must be nonnegative")
    return count


def workspace(path):
    path = Path(path).expanduser()
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("schema") != 1 or any(not isinstance(value, str) for key, value in data.items() if key != "schema"):
        raise ValueError("Workspace configuration needs schema 1 and string paths/settings")
    return data


def parser():
    p = argparse.ArgumentParser(prog="agent-speech-processor", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", type=Path, default=LOCAL_CONFIG, help="Local paths/player settings; keep this file outside Git")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("workspace", help="Show configured archive, workspace and model locations")
    promote = sub.add_parser("promote", help="Publish a completed session into a plain reading folder; preserve historical jobs")
    promote.add_argument("--session", required=True, help="Exact completed session manifest")
    promote.add_argument("--output", required=True, help="New interview reading folder")
    promote.add_argument("--notes", help="Optional prepared README Markdown; fetched exactly with --fetch")
    promote.add_argument("--fetch", action="store_true", help="Fetch exact controls and text only; no original audio")
    roles = sub.add_parser("roles", help="Prepare/apply contextual role decisions, or check editorial completion; no audio or model")
    roles.add_argument("--session", required=True, help="Exact promoted session or its registered historical manifest")
    mode = roles.add_mutually_exclusive_group()
    mode.add_argument("--output", help="Exclusively create a review packet; otherwise print it")
    mode.add_argument("--decisions", help="Apply all contextual role decisions from a completed packet")
    mode.add_argument("--check", action="store_true", help="Exit 2 while contextual role review is pending")
    roles.add_argument("--fetch", action="store_true", help="Fetch exact selected controls/text; no original audio")
    reading = sub.add_parser("reading", help="List/search explicitly selected preferred transcripts and notes without audio or models")
    reading.add_argument("--session", action="append", help="Explicit session manifest; repeat for a bounded selection (default: active session)")
    reading.add_argument("--notes", action="append", default=[], help="Additional exact session notes to include in discovery")
    reading.add_argument("--query", action="append", default=[], help="Literal case-insensitive term; repeat for spelling variants")
    reading.add_argument("--limit", type=int, default=100, help="Maximum matching lines returned")
    reading.add_argument("--context", type=int, default=2, help="Surrounding lines per match, 0–20")
    reading.add_argument("--fetch", action="store_true", help="Read only selected manifests, notes and preferred text through icmat; no audio fetch")
    reading.add_argument("--update-guides", action="store_true", help="Add/update managed README reading links while preserving human prose and exact prior notes")
    passage = sub.add_parser("passage", help="Prepare/play up to 30 seconds of source audio; no model or draft verification")
    passage.add_argument("--source", required=True)
    passage.add_argument("--start", required=True, help="Original recording seconds or HH:MM:SS.mmm")
    passage.add_argument("--end", required=True, help="Original recording seconds or HH:MM:SS.mmm")
    passage.add_argument("--context-seconds", type=float, default=0, help="Nearby seconds on each side; clipped at recording edges")
    passage.add_argument("--audio-stream-index", type=int, action="append", dest="audio_stream_indices", help="Zero-based audio stream; repeat to mix (default: 0)")
    passage.add_argument("--fetch", action="store_true", help="Use icmat on this exact source; required for iCloud")
    destination = passage.add_mutually_exclusive_group(required=True)
    destination.add_argument("--play", action="store_true", help="Play synchronously, then remove temporary audio")
    destination.add_argument("--output", help="Exclusively create a WAV for a chat/local player; caller owns this file")
    draft = sub.add_parser("draft", help="Create/resume a local Whisper draft")
    draft.add_argument("--source", required=True)
    draft.add_argument("--output", required=True)
    draft.add_argument("--model", help="Required for new recognition; saved completed/prepared results need no model")
    draft.add_argument("--model-receipt", help="Optional installation receipt with matching model SHA-256/revision")
    draft.add_argument("--source-sha256", help="Expected original identity; stop before recognition on mismatch")
    draft.add_argument("--language", default="en")
    draft.add_argument("--gpu", action="store_true")
    draft.add_argument("--audio-stream-index", type=int, action="append", dest="audio_stream_indices")
    draft.add_argument("--max-context", type=context_setting, default=0, help="Text context (default: 0); 'default' uses the recognizer default")
    draft.add_argument("--chunk-seconds", type=int, default=120)
    draft.add_argument("--keep-audio", action="store_true", help="Retain debug WAVs; normally only PCM hashes and extraction recipes remain")
    draft.add_argument("--limit-chunks", type=int)
    draft.add_argument("--fetch", action="store_true")
    inspect = sub.add_parser("inspect", help="Verify every source and imported draft in an explicit session")
    inspect.add_argument("--session", help="Defaults to active_session in local workspace configuration")
    inspect.add_argument("--fetch", action="store_true")
    inspect.add_argument("--output", help="Exclusively create an intake report at this file path")
    check = sub.add_parser("verify", help="Rehash a job's sources and all completed outputs")
    check.add_argument("--output", required=True)
    check.add_argument("--fetch", action="store_true")
    prune = sub.add_parser("prune-audio", help="Preview recorded chunk WAV cleanup; --apply requires exact recreation before removal")
    prune.add_argument("--output", required=True)
    prune.add_argument("--fetch", action="store_true")
    prune.add_argument("--apply", action="store_true", help="Verify all evidence, recreate WAVs exactly, preserve receipts, then remove only listed WAVs")
    experimental = sub.add_parser("experimental", help="Unqualified audio-review research; optional dependencies")
    experiments = experimental.add_subparsers(dest="experiment", required=True)
    models = experiments.add_parser("install-model", help="Install a pinned experimental model; does not install Whisper")
    models.add_argument("name", choices=("moss", "qwen2-audio", "gemma3n"))
    models.add_argument("--root", type=Path)
    review = experiments.add_parser("review", help="Review an explicit session; publication requires a passing qualification")
    review.add_argument("--session", help="Defaults to active_session in local workspace configuration")
    review.add_argument("--variant", required=True)
    review.add_argument("--fetch", action="store_true")
    review.add_argument("--pilot", action="store_true", help="Experimental only; never publish transcript-reviewed.md")
    review.add_argument("--qualification")
    review.add_argument("--limit-windows", type=int, help="Maximum new windows per recording on this invocation")
    review.add_argument("--retry-failed", action="store_true", help="Preserve failed attempts and make a new attempt")
    evaluate = experiments.add_parser("evaluate", help="Qualify the exact reviewer recipe on a fixed reference manifest")
    evaluate.add_argument("--references", required=True)
    evaluate.add_argument("--whisper-model", required=True)
    evaluate.add_argument("--output", required=True)
    for command in (review, evaluate):
        command.add_argument("--reviewer", choices=("qwen2-audio", "gemma3n"), default="qwen2-audio")
        command.add_argument("--reviewer-model", required=True)
        command.add_argument("--diarizer-model", required=True)
        command.add_argument("--core-seconds", type=int, default=20)
        command.add_argument("--context-seconds", type=int, default=2)
    return p


def dispatch(args):
    config = workspace(args.config)
    if args.command == "workspace":
        return {"config": str(args.config), "paths": config}, 0
    if args.command == "promote":
        from .promotion import promote
        notes = None
        if args.notes:
            from .inputs import materialize, prepare_sources
            path = prepare_sources([args.notes])[0]
            materialize([path], args.fetch)
            notes = path.read_text()
        return promote(args.session, args.output, readme_text=notes, fetch=args.fetch), 0
    if args.command == "reading":
        from .reading import process
        return process(args, config)
    if args.command == "roles":
        from .roles import process
        return process(args)
    if args.command == "passage":
        from .passage import process
        return process(args, config), 0
    if args.command == "draft":
        from .whisper import process
        if args.limit_chunks is not None and args.limit_chunks < 1:
            raise ValueError("--limit-chunks must be positive")
        result = process(args)
        from .role_text import file_status
        editorial = result.get("editorial")
        result["role_review"] = (file_status(editorial["path"]) if editorial else
                                 {"complete": False, "status": "pending", "reason": "Finish recognition, then promote and review roles"})
        return result, 0
    if args.command == "prune-audio":
        from .retention import process
        return process(args), 0
    if args.command == "inspect":
        from .session import inspect
        args.session = args.session or config.get("active_session")
        if not args.session:
            raise ValueError("Supply --session or configure active_session in the local workspace file")
        return inspect(args), 0
    if args.command == "verify":
        from .verification import process
        return process(args)
    if args.command == "experimental":
        from .experimental.commands import dispatch as experimental_dispatch
        return experimental_dispatch(args, config)
    raise ValueError("Unknown command: " + args.command)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result, code = dispatch(args)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return code
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"agent-speech-processor: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("agent-speech-processor: cancelled; active command stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
