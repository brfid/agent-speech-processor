"""Reading remains independent of audio, immutable jobs and editable text hashes."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_speech_processor.reading import BEGIN, collect, search, update_guides


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.session = self.root / "archive/processed/session"
        self.folder = self.session / "recording"
        self.folder.mkdir(parents=True)
        self.manifest = self.session / "session.json"
        self.manifest.write_text(json.dumps({"schema": 1, "reading_layout": 1, "recordings": [{
            "source": "../../originals/recording.wav", "source_sha256": "a" * 64,
            "draft": "recording", "audio_streams": [0, 1], "working_transcript": "Transcript.md"}]}))
        (self.folder / '.working-transcript.json').write_text(json.dumps({'schema': 1, 'manifest': '../session.json'}))
        self.edited = self.session / "Transcript.md"
        (self.folder / 'transcript-edited.md').write_text('Historical edited snapshot must remain untouched.\n')
        self.baseline = self.folder / "transcript-draft.md"
        self.edited.write_text("# Working transcript\n\n[00:01–00:02] Uncertain words.\n<!-- Editorial term: multiplexer -->\n")
        self.baseline.write_text("# Machine draft\nRepeated raw wording.\n")
        (self.session / "README.md").write_text("# Supplied title\n\nDocumentary alias: SWITCH.\nHuman date evidence remains tentative.\n")
        (self.folder / "README.md").write_text("# Recording\n\nHuman notes must survive.\n")

    def test_reads_without_audio_receipt_or_model_and_preserves_annotations(self):
        selection = collect([self.manifest])
        self.assertFalse(Path(selection['recordings'][0]['source']).exists())
        found = search(selection, ['multiplexer', 'switch'])
        self.assertEqual({m['role'] for m in found['matches']}, {'descriptive_note', 'working_transcript'})
        self.assertTrue(any('<!-- Editorial' in m['text'] for m in found['matches']))
        self.edited.write_text(self.edited.read_text() + '\nNew user correction.\n')
        self.assertEqual(len(collect([self.manifest])['recordings']), 1)

    def test_guide_resume_preserves_human_prose_and_transcript_bytes(self):
        before = {p: p.read_bytes() for p in [self.edited, self.baseline, self.session / 'README.md']}
        selection = collect([self.manifest])
        update_guides(selection)
        p = self.session / 'README.md'
        p.write_text(p.read_text() + '\nAdditional user interpretation.\n')
        results = update_guides(collect([self.manifest]))
        self.assertFalse(any(r['changed'] for r in results))
        self.assertIn('Additional user interpretation.', p.read_text())
        self.assertEqual(self.edited.read_bytes(), before[self.edited])
        self.assertEqual(self.baseline.read_bytes(), before[self.baseline])
        digest = hashlib.sha256(before[p]).hexdigest()
        self.assertEqual((self.session / '.reading-history' / (digest + '.md')).read_bytes(), before[p])

    def test_modified_generated_block_stops_without_overwrite(self):
        update_guides(collect([self.manifest]))
        p = self.session / 'README.md'
        p.write_text(p.read_text().replace('## Recordings', '## Human replacement'))
        before = p.read_bytes()
        with self.assertRaisesRegex(ValueError, 'edited'):
            update_guides(collect([self.manifest]))
        self.assertEqual(p.read_bytes(), before)

    def test_unsupported_markers_stop_without_adding_another_guide(self):
        update_guides(collect([self.manifest]))
        p = self.session / 'README.md'
        p.write_text(p.read_text().replace('agent-speech-processor reading guide', 'retired-command reading guide'))
        before = p.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Unsupported reading-guide marker'):
            update_guides(collect([self.manifest]))
        self.assertEqual(p.read_bytes(), before)

    def test_interruption_after_readme_before_receipt_recovers_exact_block(self):
        from agent_speech_processor import reading
        real = reading.atomic
        def interrupted(path, data, **kwargs):
            if Path(path).name == '.reading-guide.json' and b'"publication": "complete"' in data:
                raise OSError('synthetic interruption')
            return real(path, data, **kwargs)
        with patch('agent_speech_processor.reading.atomic', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interruption'):
                update_guides(collect([self.manifest]))
        update_guides(collect([self.manifest]))
        self.assertEqual((self.session / 'README.md').read_text().count(BEGIN), 1)
        record = json.loads((self.session / '.reading-guide.json').read_text())
        self.assertTrue((self.session / record['initial_readme']).is_file())

    def test_relocated_archive_keeps_links_and_selection(self):
        update_guides(collect([self.manifest]))
        moved = self.root / 'relocated'
        shutil.move(str(self.root / 'archive'), moved)
        manifest = moved / 'processed/session/session.json'
        result = collect([manifest])
        self.assertEqual(Path(result['recordings'][0]['source']), moved / 'originals/recording.wav')
        self.assertTrue(Path(result['recordings'][0]['preferred_text']).is_file())
        self.assertFalse(any(r['changed'] for r in update_guides(result)))

    def test_update_interrupted_before_readme_resumes_old_managed_block(self):
        from agent_speech_processor import reading
        update_guides(collect([self.manifest]))
        data = json.loads(self.manifest.read_text())
        data['recordings'][0]['source'] = '../../originals/changed.wav'
        self.manifest.write_text(json.dumps(data))
        real = reading.atomic
        def interrupted(path, *args, **kwargs):
            if Path(path) == self.session / 'README.md':
                raise OSError('before README publication')
            return real(path, *args, **kwargs)
        with patch('agent_speech_processor.reading.atomic', side_effect=interrupted):
            with self.assertRaises(OSError):
                update_guides(collect([self.manifest]))
        update_guides(collect([self.manifest]))
        self.assertIn('changed.wav', (self.session / 'README.md').read_text())

    def test_reversed_markers_and_shared_draft_folder_are_rejected(self):
        from agent_speech_processor.reading import END
        p = self.session / 'README.md'
        p.write_text(END + '\n' + BEGIN)
        with self.assertRaisesRegex(ValueError, 'Reversed'):
            update_guides(collect([self.manifest]))
        data = json.loads(self.manifest.read_text())
        data['recordings'].append({**data['recordings'][0], 'source': '../../originals/other.wav'})
        self.manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'exactly one|share a derivative'):
            collect([self.manifest])

    def test_missing_canonical_stops_without_selecting_snapshot_or_baseline(self):
        preserved = {p: p.read_bytes() for p in (self.folder / 'transcript-edited.md', self.baseline)}
        self.edited.unlink()
        with self.assertRaisesRegex(ValueError, 'missing; restore'):
            collect([self.manifest])
        self.assertEqual(preserved, {p: p.read_bytes() for p in preserved})

    def test_unregistered_selection_requires_promotion_without_reading_job_text(self):
        (self.folder / '.working-transcript.json').unlink()
        for canonical in (True, False):
            data = json.loads(self.manifest.read_text())
            if not canonical:
                data.pop('reading_layout')
                data['recordings'][0].pop('working_transcript')
                self.manifest.write_text(json.dumps(data))
            with patch('agent_speech_processor.reading.materialize') as fetch:
                with self.assertRaisesRegex(ValueError, 'requires a registered'):
                    collect([self.manifest])
            selected = [p for call in fetch.call_args_list for p in call.args[0]]
            self.assertNotIn(self.edited, selected)
            self.assertNotIn(self.baseline, selected)

    def test_repeated_source_variants_fail_even_when_text_differs(self):
        second = self.session / 'second.json'
        second.write_bytes(self.manifest.read_bytes())
        with self.assertRaisesRegex(ValueError, 'one current variant'):
            collect([self.manifest, second])

    def test_fetch_selects_controls_and_text_never_original_audio(self):
        with patch('agent_speech_processor.reading.materialize') as fetch, \
             patch('agent_speech_processor.working.materialize', fetch):
            collect([self.manifest], fetch=True)
        selected = [p for call in fetch.call_args_list for p in call.args[0]]
        self.assertIn(self.edited, selected)
        self.assertNotIn(self.baseline, selected)
        self.assertFalse(any(p.suffix == '.wav' for p in selected))

    def test_explicit_unusable_disposition_retains_links_but_excludes_text(self):
        note = self.folder / 'README.md'
        note.write_text('# Recording\n\nReading disposition: unusable\n\nRetain for provenance; do not rerun.\n')
        with patch('agent_speech_processor.reading.materialize') as fetch, \
             patch('agent_speech_processor.working.materialize', fetch):
            selection = collect([self.manifest], fetch=True, notes=[note])
        fetched = [p for call in fetch.call_args_list for p in call.args[0]]
        self.assertNotIn(self.edited, fetched)
        row = selection['recordings'][0]
        self.assertEqual(row['preferred_text'], str(self.edited))
        self.assertTrue(row['excluded_from_default_search'])
        self.assertEqual(search(selection, ['multiplexer'])['matching_lines'], 0)
        self.assertEqual(search(selection, ['provenance'])['matching_lines'], 1)
        update_guides(selection)
        self.assertIn('Retained unusable text', (self.session / 'README.md').read_text())
        self.assertIn('do not rerun.', note.read_text())
        self.assertEqual(self.edited.read_text().count('multiplexer'), 1)

    def test_disposition_is_explicit_not_inferred_from_free_prose(self):
        note = self.folder / 'README.md'
        note.write_text('The speaker called an old machine unusable.\n')
        self.assertFalse(collect([self.manifest])['recordings'][0]['excluded_from_default_search'])
        note.write_text('Reading disposition: useable\n')
        with self.assertRaisesRegex(ValueError, 'Reading disposition'):
            collect([self.manifest])

    def register(self):
        import os
        self.reading = self.root / 'archive/Transcripts/Interview'
        self.reading.mkdir(parents=True)
        self.current = self.reading / 'Transcript.md'
        self.current.write_text('# Transcript\nCurrent wording.\n<!-- New editorial annotation -->\n')
        self.current_manifest = self.reading / '.session.json'
        data = json.loads(self.manifest.read_text())
        data.pop('reading_layout')
        item = data['recordings'][0]
        item.pop('working_transcript')
        self.manifest.write_text(json.dumps(data))
        item['source'] = os.path.relpath(self.session / item['source'], self.reading)
        item['draft'] = os.path.relpath(self.folder, self.reading)
        item['working_transcript'] = 'Transcript.md'
        data['reading_layout'] = 1
        self.current_manifest.write_text(json.dumps(data))
        (self.folder / '.working-transcript.json').write_text(json.dumps({
            'schema': 1, 'manifest': os.path.relpath(self.current_manifest, self.folder)}))
        (self.reading / 'README.md').write_text('# Interview\n\nCurrent descriptive evidence.\n')

    def test_registered_old_and_new_selection_read_only_current_text_and_notes(self):
        self.register()
        for manifest in (self.manifest, self.current_manifest):
            selection = collect([manifest])
            self.assertEqual(set(selection['texts']), {str(self.current), str(self.reading / 'README.md')})
            self.assertEqual(search(selection, ['multiplexer'])['matching_lines'], 0)
            self.assertEqual(search(selection, ['editorial'])['matching_lines'], 1)
        self.current.write_text(self.current.read_text() + '\nAn author correction.\n')
        self.assertEqual(search(collect([self.manifest]), ['correction'])['matching_lines'], 1)
        self.current.unlink()
        for manifest in (self.manifest, self.current_manifest):
            with self.assertRaisesRegex(ValueError, 'missing'):
                collect([manifest])

    def test_registered_guide_does_not_rewrite_historical_notes(self):
        self.register()
        paths = [self.session / 'README.md', self.folder / 'README.md', self.edited, self.baseline]
        before = {p: p.read_bytes() for p in paths}
        results = update_guides(collect([self.manifest]))
        self.assertEqual(len(results), 1)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        guide = (self.reading / 'README.md').read_text()
        self.assertIn('## Recordings', guide)
        self.assertIn('Current descriptive evidence.', guide)
        self.assertNotIn('transcript-draft', guide)

    def test_registration_cannot_change_pinned_source_fields(self):
        self.register()
        data = json.loads(self.current_manifest.read_text())
        data['recordings'][0]['audio_streams'] = [0]
        self.current_manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'changed recorded audio_streams'):
            collect([self.manifest])

    def test_registered_exclusion_still_uses_explicit_record_disposition(self):
        self.register()
        (self.folder / 'README.md').write_text('Reading disposition: unusable\n')
        selection = collect([self.manifest])
        self.assertTrue(selection['recordings'][0]['excluded_from_default_search'])
        self.assertNotIn(str(self.current), selection['texts'])
        self.assertNotIn(str(self.folder / 'README.md'), selection['texts'])


if __name__ == '__main__':
    unittest.main()
