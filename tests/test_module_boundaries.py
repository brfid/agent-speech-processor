"""Supported commands stay independent of optional review code and packages."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
import zipfile

from agent_speech_processor.cli import parser
from agent_speech_processor.provenance import code_snapshot
from agent_speech_processor.records import digest


class ModuleBoundaries(unittest.TestCase):
    def test_supported_commands_work_with_experimental_imports_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.wav"
            with wave.open(str(source), "wb") as sound:
                sound.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                sound.writeframes(b"\0\0" * 16000)
            model = root / "synthetic-model"
            model.write_bytes(b"fake weights for the fake recognizer")
            engine = root / "whisper-cli"
            engine.write_text(
                f"#!{sys.executable}\n"
                "import json, pathlib, sys\n"
                "if '--help' in sys.argv:\n"
                "    print('usage: synthetic whisper-cli')\n"
                "    sys.exit(0)\n"
                "prefix = sys.argv[sys.argv.index('-of') + 1]\n"
                "record = {'transcription': [{'offsets': {'from': 0, 'to': 1000}, 'text': 'synthetic words'}]}\n"
                "for suffix in ('json', 'srt', 'txt'):\n"
                "    pathlib.Path(prefix + '.' + suffix).write_text(json.dumps(record) if suffix == 'json' else 'synthetic words')\n")
            engine.chmod(0o755)
            script = '''
import importlib.abc, json, os, pathlib, sys
class RejectReview(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.startswith('agent_speech_processor.experimental') or fullname.split('.')[0] in
                {'mlx', 'mlx_audio', 'mlx_lm', 'mlx_vlm', 'webrtcvad', 'huggingface_hub', 'transformers'}):
            raise AssertionError('Supported command imported optional review code: ' + fullname)
sys.meta_path.insert(0, RejectReview())
from agent_speech_processor.cli import main
from agent_speech_processor.records import digest
root = pathlib.Path(sys.argv[1])
os.environ['PATH'] = str(root) + os.pathsep + os.environ['PATH']
config = ['--config', str(root / 'absent-config.json')]
source, draft = root / 'synthetic.wav', root / 'draft'
assert main(config + ['passage', '--source', str(source), '--start', '0.1', '--end', '0.3',
                      '--output', str(root / 'passage.wav')]) == 0
assert main(config + ['draft', '--source', str(source), '--output', str(draft),
                      '--model', str(root / 'synthetic-model')]) == 0
assert main(config + ['verify', '--output', str(draft)]) == 0
state = json.loads((draft / '.processing.json').read_text())
manifest = root / 'session.json'
manifest.write_text(json.dumps({'schema': 1, 'recordings': [{
    'source': str(source), 'draft': str(draft),
    'source_sha256': next(iter(state['sources'].values()))['sha256'],
    'draft_recipe_sha256': digest(state['specification']['settings']), 'audio_streams': [0]}]}))
assert main(config + ['inspect', '--session', str(manifest)]) == 0
assert main(config + ['promote', '--session', str(manifest), '--output', str(root / 'Transcripts' / 'Synthetic')]) == 0
assert main(config + ['reading', '--session', str(manifest), '--query', 'synthetic']) == 0
working = root / 'Transcripts' / 'Synthetic' / 'Transcript.md'
assert main(config + ['roles', '--session', str(manifest), '--check']) == 2
packet = root / 'role-decisions.json'
assert main(config + ['roles', '--session', str(manifest), '--output', str(packet)]) == 0
decisions = json.loads(packet.read_text())
decisions['method'] = 'Synthetic contextual-review fixture; preserve all text.'
for recording in decisions['recordings']:
    for block in recording['blocks']:
        block['role'] = 'Unclear speakers'
packet.write_text(json.dumps(decisions))
assert main(config + ['roles', '--session', str(manifest), '--decisions', str(packet)]) == 0
assert main(config + ['roles', '--session', str(manifest), '--check']) == 0
working.write_text(working.read_text() + '\\n<!-- preserve this user note -->\\n')
before = working.read_bytes()
assert main(config + ['reading', '--session', str(manifest), '--query', 'preserve this']) == 0
assert main(config + ['draft', '--source', str(source), '--output', str(draft)]) == 0
assert working.read_bytes() == before
assert main(config + ['verify', '--output', str(draft)]) == 0
'''
            result = subprocess.run([sys.executable, "-c", script, str(root)],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue((root / "passage.wav").is_file())
            self.assertTrue((root / "draft/transcript-edited.md").is_file())

    def test_experimental_commands_have_an_explicit_namespace_without_aliases(self):
        args = parser().parse_args(["experimental", "install-model", "moss"])
        self.assertEqual((args.command, args.experiment, args.name), ("experimental", "install-model", "moss"))
        old_commands = [
            ["install-model", "moss"],
            ["review", "--variant", "synthetic", "--reviewer-model", "reviewer", "--diarizer-model", "diarizer"],
            ["evaluate", "--references", "references.json", "--whisper-model", "whisper", "--output", "output",
             "--reviewer-model", "reviewer", "--diarizer-model", "diarizer"],
        ]
        for command in old_commands:
            with self.subTest(command=command[0]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    parser().parse_args(command)
                self.assertEqual(stopped.exception.code, 2)

    def test_archival_snapshot_includes_nested_reviewer_source(self):
        hashes, snapshot = code_snapshot()
        with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
            for name in ("experimental/worker.py", "experimental/pipeline.py", "audio.py", "whisper.py"):
                self.assertIn(name, hashes)
                self.assertEqual(archive.read("agent_speech_processor/" + name),
                                 (Path(__file__).resolve().parents[1] / "src/agent_speech_processor" / name).read_bytes())
        self.assertEqual(snapshot, code_snapshot()[1])

    def test_canonical_digest_is_stable_and_rejects_nonfinite_values(self):
        settings = {"chunk_seconds": 120, "overlap_seconds": 5, "audio_stream": "0:a:0", "language": "café"}
        self.assertEqual(digest(settings), "46ed70c80c77591e644c78e2973c44cfd17bbd9d9442c9682d3fe89aaf3dc17f")
        self.assertEqual(digest(settings), digest(dict(reversed(list(settings.items())))))
        with self.assertRaises(ValueError):
            digest({"timestamp": float("nan")})


if __name__ == "__main__":
    unittest.main()
