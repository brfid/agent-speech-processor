"""Synthetic transcript promotion: editorial preservation and resumable handoff."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from agent_speech_processor import promotion
from agent_speech_processor.records import json_bytes
from agent_speech_processor.transcripts import header
from agent_speech_processor.role_text import PENDING
from agent_speech_processor.working import resolve


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.session = self.root / 'archive/processed/session'
        self.job = self.session / 'recording'
        self.job.mkdir(parents=True)
        self.source = self.root / 'archive/originals/recording.wav'
        self.output = self.root / 'archive/Transcripts/Person/Session'
        self.manifest = self.session / 'session.json'
        self.item = {'source': '../../originals/recording.wav', 'source_sha256': 'a' * 64,
                     'draft': 'recording', 'draft_recipe_sha256': 'b' * 64, 'audio_streams': [0, 1]}
        self.manifest.write_bytes(json_bytes({'schema': 1, 'vocabulary': ['fictional'], 'recordings': [self.item]}))
        self.old = self.job / 'transcript-edited.md'
        self.body = ('## Source window 00:00:00.000–00:02:00.000\n\n'
                     '[00:00:01.000–00:00:03.000] **Unclear speakers:** I did not agree.\n\n'
                     '<!-- Editorial note: uncertain name; retain the denial. -->\n').encode()
        self.old.write_bytes(header(self.source, self.job, 120.0, [0, 1], edited=True, baseline='c' * 64).encode()
                             + PENDING.encode() + self.body)
        (self.job / 'transcript-draft.md').write_text('Historical machine baseline.\n')
        (self.job / '.processing.json').write_text('{"immutable": true}\n')
        (self.job / 'README.md').write_text('# Recording\n\nHuman notes.\n')
        (self.session / 'README.md').write_text('# Fictional session\n\nSupplied description.\n')

    def promote(self, **kwargs):
        return promotion.promote(self.manifest, self.output, **kwargs)

    def test_clean_folder_preserves_originals_body_annotations_and_mappings(self):
        preserved = {p: p.read_bytes() for p in (self.old, self.manifest, self.job / '.processing.json', self.job / 'transcript-draft.md')}
        result = self.promote(readme_text='# Human description\n\nExact supplied prose.\n')
        self.assertEqual(result['publication'], 'complete')
        self.assertEqual(sorted(p.name for p in self.output.iterdir() if not p.name.startswith('.')), ['README.md', 'Transcript.md'])
        canonical = self.output / 'Transcript.md'
        self.assertTrue(canonical.read_bytes().endswith(self.body))
        self.assertNotIn('agent-speech-processor passage', canonical.read_text())
        self.assertEqual((self.output / 'README.md').read_text(), '# Human description\n\nExact supplied prose.\n')
        self.assertEqual((self.output / '.promotion/before-01.md').read_bytes(), preserved[self.old])
        for path, before in preserved.items():
            self.assertEqual(path.read_bytes(), before)
        manifest = json.loads((self.output / '.session.json').read_bytes())
        self.assertEqual(manifest['vocabulary'], ['fictional'])
        self.assertEqual((self.output / manifest['recordings'][0]['source']).resolve(), self.source)
        self.assertEqual((self.output / manifest['recordings'][0]['draft']).resolve(), self.job)
        self.assertEqual(resolve(self.job)['transcript'], canonical)
        record = json.loads((self.output / '.promotion.json').read_bytes())['recordings'][0]
        self.assertEqual(record['before_body_sha256'], record['after_body_sha256'])
        self.assertEqual(record['link_rewrites'], [])

    def test_repeat_preserves_later_edits_and_missing_canonical_is_not_reseeded(self):
        self.promote()
        canonical = self.output / 'Transcript.md'
        canonical.write_text('Entirely revised by the user.\n<!-- New note. -->\n')
        (self.output / 'README.md').write_text('Revised description.\n')
        self.promote(readme_text='A new request must not replace a published README.')
        self.assertEqual(canonical.read_text(), 'Entirely revised by the user.\n<!-- New note. -->\n')
        self.assertEqual((self.output / 'README.md').read_text(), 'Revised description.\n')
        canonical.unlink()
        with self.assertRaisesRegex(ValueError, 'regular file'):
            self.promote()
        self.assertFalse(canonical.exists())

    def test_modified_historical_promotion_snapshot_stops_without_touching_canonical(self):
        self.promote()
        canonical = self.output / 'Transcript.md'
        canonical.write_text('New human wording.')
        (self.output / '.promotion/before-01.md').write_text('Altered old snapshot.')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.promote()
        self.assertEqual(canonical.read_text(), 'New human wording.')

    def test_multiple_recordings_include_explicit_unusable_snapshot(self):
        second = self.session / 'other'
        second.mkdir()
        source = self.source.with_name('other.wav')
        (second / 'transcript-edited.md').write_bytes(header(source, second, 120.0, [0], edited=True, baseline='d' * 64).encode()
                                                    + PENDING.encode() + self.body)
        (second / 'README.md').write_text('Reading disposition: unusable\n')
        data = json.loads(self.manifest.read_bytes())
        data['recordings'].append(dict(self.item, source='../../originals/other.wav', draft='other', audio_streams=[0]))
        self.manifest.write_bytes(json_bytes(data))
        self.promote()
        manifest = json.loads((self.output / '.session.json').read_bytes())
        self.assertEqual([i['working_transcript'] for i in manifest['recordings']], ['Transcript - 01.md', 'Transcript - 02 - Unusable.md'])
        self.assertIn('Retained unusable text', (self.output / 'Transcript - 02 - Unusable.md').read_text())
        self.assertTrue((self.output / 'Transcript - 02 - Unusable.md').read_bytes().endswith(self.body))

    def test_interruption_during_visible_publication_resumes_without_changing_history(self):
        real = promotion.atomic
        def interrupted(path, data, **kwargs):
            if Path(path) == self.output / 'README.md':
                raise OSError('synthetic interruption')
            return real(path, data, **kwargs)
        before = self.old.read_bytes()
        with patch('agent_speech_processor.publication.atomic', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interruption'):
                self.promote()
        self.assertTrue((self.output / 'Transcript.md').exists())
        self.assertFalse((self.job / '.working-transcript.json').exists())
        self.promote()
        self.assertEqual(self.old.read_bytes(), before)
        self.assertEqual(resolve(self.job)['transcript'], self.output / 'Transcript.md')

    def test_interrupted_registration_allows_new_canonical_edits_on_resume(self):
        with patch('agent_speech_processor.promotion._register', side_effect=OSError('registration interrupted')):
            with self.assertRaises(OSError):
                self.promote()
        canonical = self.output / 'Transcript.md'
        canonical.write_bytes(canonical.read_bytes() + b'\nA new human correction.\n')
        self.promote()
        self.assertIn('new human correction', canonical.read_text())
        self.assertEqual(resolve(self.job)['transcript'], canonical)

    def test_changed_request_or_history_blocks_incomplete_publication(self):
        with patch('agent_speech_processor.promotion._same_or_create', side_effect=OSError('preparation stopped')):
            with self.assertRaises(OSError):
                self.promote(readme_text='First description.')
        with self.assertRaisesRegex(ValueError, 'README request changed'):
            self.promote(readme_text='Other description.')
        self.old.write_bytes(self.old.read_bytes() + b'\nA new note.\n')
        with self.assertRaisesRegex(ValueError, 'changed during interrupted'):
            self.promote(readme_text='First description.')

    def test_changed_output_before_handoff_is_never_overwritten(self):
        real = promotion.atomic
        def interrupted(path, data, **kwargs):
            if Path(path) == self.output / 'README.md':
                raise OSError('stop')
            return real(path, data, **kwargs)
        with patch('agent_speech_processor.publication.atomic', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.promote()
        canonical = self.output / 'Transcript.md'
        canonical.write_text('Do not overwrite this edit.')
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.promote()
        self.assertEqual(canonical.read_text(), 'Do not overwrite this edit.')

    def test_existing_registration_and_active_writer_block_promotion(self):
        sidecar = self.job / '.working-transcript.json'
        sidecar.write_bytes(json_bytes({'schema': 1, 'manifest': 'elsewhere/.session.json'}))
        with self.assertRaisesRegex(ValueError, 'different canonical'):
            self.promote()
        sidecar.unlink()
        with (self.job / '.processing.lock').open('a') as locked:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'Another writer'):
                self.promote()
        self.assertFalse((self.output / 'Transcript.md').exists())

    def test_special_lock_file_is_rejected_without_opening_it(self):
        os.mkfifo(self.job / '.processing.lock')
        with self.assertRaisesRegex(ValueError, 'regular file'):
            self.promote()

    def test_symlink_stage_or_sidecar_stops_without_external_writes(self):
        outside = self.root / 'external'
        outside.mkdir()
        with patch('agent_speech_processor.promotion._same_or_create', side_effect=OSError('stage stopped')):
            with self.assertRaises(OSError):
                self.promote()
        (self.output / '.promotion').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.promote()
        self.assertEqual(list(outside.iterdir()), [])

    def test_source_text_changes_during_preparation_stop_before_publication(self):
        real = promotion._render
        def changed(*args, **kwargs):
            result = real(*args, **kwargs)
            self.old.write_bytes(self.old.read_bytes() + b'\nConcurrent human note.\n')
            return result
        with patch('agent_speech_processor.promotion._render', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed during promotion'):
                self.promote()
        self.assertFalse((self.output / 'Transcript.md').exists())

    def test_added_preface_annotations_survive_and_unknown_preface_stops(self):
        prefix, body = self.old.read_bytes().split(b'## Source window ', 1)
        annotation = b'<!-- A user annotation in the preface. -->\n\nUser introduction.\n\n'
        self.old.write_bytes(prefix + annotation + b'## Source window ' + body)
        self.promote()
        self.assertIn(annotation, (self.output / 'Transcript.md').read_bytes())
        self.assertTrue((self.output / 'Transcript.md').read_bytes().endswith(self.body))
        other = self.root / 'other-output'
        (self.job / '.working-transcript.json').unlink()
        self.old.write_bytes(self.old.read_bytes().replace(b'# Edited transcript', b'# Human-renamed transcript'))
        with self.assertRaisesRegex(ValueError, 'preface'):
            promotion.promote(self.manifest, other)
        self.assertFalse((other / 'Transcript.md').exists())

    def test_current_preface_preserves_body_and_label_provenance(self):
        current = self.old.read_bytes()
        after, evidence = promotion._render(current, self.old, self.source, [0, 1], self.output, False)
        self.assertTrue(after.endswith(self.body))
        self.assertEqual(evidence['recognized_preface']['command_literal'], 'agent-speech-processor')
        self.assertEqual(evidence['recognized_preface']['overlap_description'], 'anchored-case-only')
        self.assertTrue(evidence['recognized_preface']['role_review_pending_seed'])
        self.assertEqual(evidence['before_body_sha256'], evidence['after_body_sha256'])
        self.assertNotIn(b' passage ', after)
        self.assertEqual(after.count(PENDING.encode()), 1)

    def test_unsupported_generated_prefaces_and_pending_notes_stop_without_publication(self):
        current = self.old.read_bytes()
        alternatives = [current.replace(b'`agent-speech-processor passage ', b'`retired-command passage '),
                        current.replace(b'(at least four words, ignoring case only, with matching segment anchors)',
                                        b'(at least four words, ignoring case/punctuation)'),
                        current.replace(PENDING.encode(), b''),
                        current.replace(b'`agent-speech-processor roles`', b'`retired-command roles`'),
                        current.replace(b'**Unclear speakers:**', b'**Unassigned speaker(s):**')]
        for number, before in enumerate(alternatives):
            with self.subTest(format=number):
                self.old.write_bytes(before)
                output = self.root / f'rejected-{number}'
                with self.assertRaisesRegex(ValueError, 'preface|supported transcript format'):
                    promotion.promote(self.manifest, output)
                self.assertEqual(self.old.read_bytes(), before)
                self.assertFalse((output / 'Transcript.md').exists())
                self.assertFalse((self.job / '.working-transcript.json').exists())

    def test_fetch_includes_text_controls_but_never_audio_or_model(self):
        with patch('agent_speech_processor.promotion.materialize', return_value={'success': True}) as intake:
            self.promote(fetch=True)
        paths = [p for call in intake.call_args_list for p in call.args[0]]
        self.assertIn(self.manifest, paths)
        self.assertIn(self.old, paths)
        self.assertNotIn(self.source, paths)
        self.assertFalse(any(p.suffix in ('.wav', '.mov', '.mp3', '.bin') for p in paths))
        self.assertTrue(json.loads((self.output / '.promotion.json').read_bytes())['intake'])

    def test_relative_links_rebase_only_targets_with_comments_and_code_opaque(self):
        original = ('Words [nested [label]](../notes/file(2).md "Title") and [angle](<../notes/a b.md#part>).\n'
                    '[reference]: ../notes/reference.md "Title"\n'
                    '[remote](https://example.test/path(a)) [same page](#here)\n'
                    '`[code](../unchanged.md)`\n<!-- [annotation](../unchanged.md) -->\n'
                    '```markdown\n[fenced](../unchanged.md)\n```\n').encode()
        result, edits = promotion.rebase_markdown(original, self.job, self.output)
        self.assertEqual(len(edits), 3)
        restored = result
        shift = 0
        for edit in edits:
            a = edit['start'] + shift
            b = a + len(edit['after'].encode())
            self.assertEqual(restored[a:b], edit['after'].encode())
            shift += len(edit['after'].encode()) - (edit['end'] - edit['start'])
        for edit in reversed(edits):
            original_target = (self.job / unquote(edit['before'].split('#')[0])).resolve()
            new_target = (self.output / unquote(edit['after'].split('#')[0])).resolve()
            self.assertEqual(original_target, new_target)
        self.assertIn(b'<!-- [annotation](../unchanged.md) -->', result)
        self.assertIn(b'`[code](../unchanged.md)`', result)
        self.assertIn(b'[fenced](../unchanged.md)', result)

    def test_body_link_rewrites_are_audited_and_all_other_bytes_survive(self):
        self.old.write_bytes(self.old.read_bytes() + b'\nSee [my note](../notes/a(2).md).\n')
        self.promote()
        record = json.loads((self.output / '.promotion.json').read_bytes())['recordings'][0]
        self.assertEqual(len(record['link_rewrites']), 1)
        canonical = (self.output / 'Transcript.md').read_bytes()
        body = canonical[canonical.index(b'## Source window '):]
        self.assertIn(self.body, body)
        self.assertNotEqual(record['before_body_sha256'], record['after_body_sha256'])
        self.assertEqual(hashlib.sha256(body).hexdigest(), record['after_body_sha256'])

    def test_ambiguous_links_fail_without_loss(self):
        for sample in (b'[label](some path.md)', b'[label](unclosed(foo.md)',
                       b'<a href="relative.md">raw HTML</a>', b'<!-- unfinished',
                       b'Paragraph\n    [a reference](notes.md)\n'):
            with self.subTest(sample=sample):
                with self.assertRaises(ValueError):
                    promotion.rebase_markdown(sample, self.job, self.output)

    def test_orphan_link_punctuation_and_code_closing_runs_preserve_literal_bytes(self):
        original = (b'[Unclosed uncertainty note.\n\n[00:01] Literal punctuation ](should-stay).\n'
                    b'```markdown\n```not-a-close\n[still code](relative.md)\n```\n'
                    b'``literal ` and ``` and [code](relative.md)``\n')
        result, edits = promotion.rebase_markdown(original, self.job, self.output)
        self.assertEqual(result, original)
        self.assertEqual(edits, [])
        windows = original.replace(b'\n', b'\r\n')
        result, edits = promotion.rebase_markdown(windows, self.job, self.output)
        self.assertEqual(result, windows)
        self.assertEqual(edits, [])

    def test_target_and_title_brackets_cannot_change_later_literal_speech(self):
        original = b'[valid](relative[filename.md "title [") literal ](keep-this).\n'
        result, edits = promotion.rebase_markdown(original, self.job, self.output)
        self.assertEqual(len(edits), 1)
        self.assertTrue(result.endswith(b' literal ](keep-this).\n'))


if __name__ == '__main__':
    unittest.main()
