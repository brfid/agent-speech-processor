"""Contextual role handoff: exact text preservation, stale decisions and interruption."""
import fcntl
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from agent_speech_processor import roles, role_text
from agent_speech_processor.cli import dispatch, parser
from agent_speech_processor.promotion import promote
from agent_speech_processor.reading import collect
from agent_speech_processor.records import atomic, json_bytes
from agent_speech_processor.transcripts import edited_text, header
import test_promotion


class RoleTests(unittest.TestCase):
    def setUp(self):
        test_promotion.PromotionTests.setUp(self)
        self.body = ('## Source window 00:00:00.000–00:02:00.000\n\n'
                     '[00:00:01.000–00:00:03.000] **Unclear speakers:** Was it deployed?\n\n'
                     '[00:00:03.000–00:00:06.000] **Unclear speakers:** No; only a trial. I asked, "Was it ready?"\n\n'
                     '<!-- Preserve this interpretation, not speech. -->\n\n'
                     '[00:00:06.000–00:00:09.000] **Unclear speakers:** Right. But what happened? Well, I left.\n\n').encode()
        self.old.write_bytes(header(self.source, self.job, 120, [0, 1], edited=True, baseline='c' * 64).encode()
                             + role_text.PENDING.encode() + self.body)
        self.promotion = promote(self.manifest, self.output)
        self.current = self.output / 'Transcript.md'
        self.before = self.current.read_bytes()

    def decisions(self):
        result = roles.packet(self.manifest)
        result['method'] = 'Sequential contextual text-only review of every block and annotation.'
        for record in result['recordings']:
            for block, role in zip(record['blocks'], role_text.LABELS):
                block['role'] = role
        return result

    def test_generation_uses_unclear_pending_seed_without_invented_roles(self):
        raw = self.root / 'chunk.json'
        raw.write_text(json.dumps({'transcription': [{'offsets': {'from': 0, 'to': 1000}, 'text': 'Why? I asked myself.'}]}))
        seed, _ = edited_text(self.source, self.root, 1, [0],
                              [{'start_seconds': 0, 'end_seconds': 1, 'json': raw.name}], 'a' * 64)
        self.assertIn(role_text.PENDING, seed)
        self.assertIn('**Unclear speakers:** Why? I asked myself.', seed)
        self.assertNotIn('**Unassigned speaker(s):**', seed)
        self.assertFalse(role_text.status(seed)['complete'])

    def test_packet_includes_all_context_and_never_preassigns_roles(self):
        packet = roles.packet(self.manifest)
        item = packet['recordings'][0]
        self.assertEqual(item['transcript_markdown'].encode(), self.before)
        self.assertIn('<!-- Preserve', item['transcript_markdown'])
        self.assertTrue(packet['notes'])
        self.assertIn('Working transcripts', packet['notes'][0]['markdown'])
        self.assertEqual([b['role'] for b in item['blocks']], [None, None, None])
        self.assertFalse(self.source.exists())
        self.assertFalse((self.output / '.role-review').exists())

    def test_apply_preserves_speech_annotations_and_historical_controls(self):
        protected = [self.old, self.manifest, self.job / 'transcript-draft.md',
                     self.job / '.processing.json', self.job / '.working-transcript.json', self.output / '.session.json']
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected}
        result = roles.apply(self.manifest, self.decisions())
        self.assertTrue(result['complete'])
        self.assertEqual(result['recordings'][0]['counts'], dict.fromkeys(role_text.LABELS, 1))
        self.assertTrue(self.current.read_bytes().endswith(self.body.replace(b'**Unclear speakers:**', b'**Interviewer:**', 1)
                        .replace(b'**Unclear speakers:**', b'**Interviewee:**', 1)))
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected})
        self.assertEqual(collect([self.manifest])['recordings'][0]['role_review']['status'], 'text_inferred')

    def test_partial_duplicate_boolean_unknown_and_changed_decisions_stop_without_edits(self):
        for change in ('partial', 'duplicate', 'boolean', 'named', 'words', 'span', 'markdown'):
            with self.subTest(change=change):
                d = self.decisions()
                record = d['recordings'][0]
                b = record['blocks'][1]
                if change == 'partial': record['blocks'].pop()
                elif change == 'duplicate': b['block'] = 1
                elif change == 'boolean': record['blocks'][0]['block'] = True
                elif change == 'named': b['role'] = 'Alex'
                elif change == 'words': b['text'] = 'Yes, deployed everywhere.'
                elif change == 'span': b['span'] = '[00:00:02.000–00:00:06.000]'
                else: record['transcript_markdown'] = record['transcript_markdown'].replace('not speech', 'speech')
                with self.assertRaises(ValueError): roles.apply(self.manifest, d)
                self.assertEqual(self.current.read_bytes(), self.before)
                self.assertFalse((self.output / '.role-review').exists())

    def test_stale_decisions_preserve_user_edits_and_reading_stays_available(self):
        d = self.decisions()
        self.current.write_bytes(self.before + b'<!-- New user annotation. -->\n')
        edited = self.current.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Current working text changed'):
            roles.apply(self.manifest, d)
        self.assertEqual(self.current.read_bytes(), edited)
        self.assertEqual(collect([self.manifest])['texts'][str(self.current)], edited.decode())
        self.assertTrue(roles.apply(self.manifest, self.decisions())['complete'])

    def test_interrupted_publication_resumes_from_exact_preserved_evidence(self):
        d = self.decisions()
        def interrupted(path, data, **kwargs):
            atomic(path, data, **kwargs)
            if Path(path) == self.current: raise KeyboardInterrupt()
        with patch('agent_speech_processor.roles.atomic', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt): roles.apply(self.manifest, d)
        after = self.current.read_bytes()
        self.assertTrue(roles.apply(self.manifest, d)['complete'])
        self.assertEqual(self.current.read_bytes(), after)
        self.current.write_bytes(after + b'<!-- Later editorial note. -->\n')
        with self.assertRaisesRegex(ValueError, 'Current working text changed'):
            roles.apply(self.manifest, d)
        self.assertIn(b'Later editorial note', self.current.read_bytes())

    def test_modified_history_and_symlinked_history_are_rejected(self):
        d = self.decisions()
        roles.apply(self.manifest, d)
        history = next((self.output / '.role-review').iterdir())
        (history / 'before.md').write_text('Changed evidence')
        after = self.current.read_bytes()
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            roles.apply(self.manifest, d)
        self.assertEqual(after, self.current.read_bytes())
        shutil.rmtree(self.output / '.role-review')
        (self.output / '.role-review').symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            roles.apply(self.manifest, d)

    def test_notes_inside_comments_or_code_do_not_claim_role_review(self):
        text = self.before.decode().replace(role_text.PENDING, '')
        for quoted in ('<!-- ' + role_text.NOTE + ' -->\n', '```\n' + role_text.NOTE + '```\n'):
            with self.subTest(quoted=quoted[:4]):
                self.assertFalse(role_text.status(text.replace('# Transcript\n\n', '# Transcript\n\n' + quoted))['complete'])
        self.current.write_text(text + '<!-- ' + role_text.PENDING + role_text.NOTE + ' -->\n')
        roles.apply(self.manifest, self.decisions())
        self.assertIn('<!-- ' + role_text.PENDING + role_text.NOTE + ' -->', self.current.read_text())

    def test_unsupported_unassigned_seed_stops_without_role_application(self):
        self.current.write_bytes(self.before.replace(b'**Unclear speakers:**', b'**Unassigned speaker(s):**'))
        before = self.current.read_bytes()
        self.assertFalse(roles.session_status(self.manifest)['complete'])
        with self.assertRaisesRegex(ValueError, 'supported transcript format'):
            roles.packet(self.manifest)
        self.assertEqual(self.current.read_bytes(), before)

    def test_unsupported_pending_note_stops_without_becoming_role_complete(self):
        self.current.write_bytes(self.before.replace(b'`agent-speech-processor roles`', b'`retired-command roles`'))
        before = self.current.read_bytes()
        self.assertFalse(roles.session_status(self.manifest)['complete'])
        with self.assertRaisesRegex(ValueError, 'Unsupported role-review explanatory note'):
            roles.packet(self.manifest)
        self.assertEqual(self.current.read_bytes(), before)

    def test_promotion_and_cli_make_pending_and_complete_states_distinct(self):
        self.assertFalse(self.promotion['role_review']['complete'])
        args = parser().parse_args(['roles', '--session', str(self.manifest), '--check'])
        with patch('agent_speech_processor.cli.workspace', return_value={}):
            result, code = dispatch(args)
        self.assertEqual(code, 2)
        roles.apply(self.manifest, self.decisions())
        with patch('agent_speech_processor.cli.workspace', return_value={}):
            result, code = dispatch(args)
        self.assertEqual(code, 0)
        self.assertTrue(result['complete'])
        self.assertTrue(promote(self.manifest, self.output)['role_review']['complete'])

    def test_role_packet_output_never_overwrites_an_existing_file(self):
        path = self.root / 'packet.json'
        args = parser().parse_args(['roles', '--session', str(self.manifest), '--output', str(path)])
        with patch('agent_speech_processor.cli.workspace', return_value={}): dispatch(args)
        before = path.read_bytes()
        with patch('agent_speech_processor.cli.workspace', return_value={}), self.assertRaises(FileExistsError): dispatch(args)
        self.assertEqual(path.read_bytes(), before)

    def test_promotion_writer_lock_prevents_concurrent_role_publication(self):
        with (self.output / '.promotion.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'Another writer'):
                roles.apply(self.manifest, self.decisions())
        self.assertEqual(self.current.read_bytes(), self.before)

    def test_existing_reviewed_text_is_recognized_and_reapplication_preserves_mtime(self):
        roles.apply(self.manifest, self.decisions())
        before, modified = self.current.read_bytes(), self.current.stat().st_mtime_ns
        self.assertTrue(roles.session_status(self.manifest)['complete'])
        roles.apply(self.manifest, self.decisions())
        self.assertEqual(self.current.read_bytes(), before)
        self.assertEqual(self.current.stat().st_mtime_ns, modified)

    def test_every_recording_is_validated_before_any_current_text_is_changed(self):
        # A second registered reading folder sharing a historical session selection.
        second = self.session / 'recording-two'
        second.mkdir()
        source = self.source.parent / 'second.wav'
        item = dict(self.item, source='../../originals/second.wav', draft='recording-two', source_sha256='d' * 64)
        (second / 'transcript-edited.md').write_bytes(
            header(source, second, 120, [0, 1], edited=True, baseline='c' * 64).encode() + role_text.PENDING.encode() + self.body)
        (second / 'README.md').write_text('# Second recording\n')
        # Build a new, explicit two-recording selection without changing the first publication.
        manifest = self.session / 'two.json'
        manifest.write_bytes(json_bytes({'schema': 1, 'recordings': [self.item, item]}))
        (self.job / '.working-transcript.json').unlink()
        out = self.root / 'two-reading'
        promote(manifest, out)
        d = roles.packet(manifest)
        d['method'] = 'Full contextual review of both recordings.'
        for row in d['recordings']:
            for block in row['blocks']: block['role'] = 'Unclear speakers'
        d['recordings'][1]['blocks'][1]['text'] = 'Changed speech'
        before = {p: p.read_bytes() for p in out.glob('Transcript*.md')}
        with self.assertRaisesRegex(ValueError, 'changed role decision'):
            roles.apply(manifest, d)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_archive_move_keeps_packet_identity_and_relative_mappings(self):
        d = self.decisions()
        moved = self.root / 'moved'
        shutil.copytree(self.root / 'archive', moved)
        new_manifest = moved / 'processed/session/session.json'
        self.assertTrue(roles.apply(new_manifest, d)['complete'])
        self.assertEqual(self.current.read_bytes(), self.before)

    def test_fetch_selects_exact_text_and_controls_including_promotion_not_audio(self):
        fetched = []
        def materialize(paths, fetch):
            fetched.extend(Path(p) for p in paths)
        with patch('agent_speech_processor.inputs.materialize', side_effect=materialize), \
             patch('agent_speech_processor.reading.materialize', side_effect=materialize), \
             patch('agent_speech_processor.working.materialize', side_effect=materialize):
            roles.packet(self.manifest, fetch=True)
        self.assertIn(self.output / '.promotion.json', fetched)
        self.assertIn(self.current, fetched)
        self.assertNotIn(self.source, fetched)
        self.assertNotIn(self.job / 'transcript-draft.md', fetched)

    def test_interrupted_promotion_is_recovered_before_role_review(self):
        ledger = self.output / '.promotion.json'
        d = json.loads(ledger.read_text())
        d['publication'] = 'prepared'
        ledger.write_bytes(json_bytes(d))
        with self.assertRaisesRegex(ValueError, 'interrupted promotion'):
            roles.packet(self.manifest)
        self.assertEqual(self.current.read_bytes(), self.before)

    def test_changed_or_altered_context_is_rejected_before_any_transcript_write(self):
        decisions = self.decisions()
        guide = self.output / 'README.md'
        old = guide.read_bytes()
        guide.write_bytes(old + b'\nCorrection: the participants have different roles.\n')
        with self.assertRaisesRegex(ValueError, 'context or canonical selection changed'):
            roles.apply(self.manifest, decisions)
        self.assertEqual(self.current.read_bytes(), self.before)
        self.assertFalse((self.output / '.role-review').exists())
        guide.write_bytes(old)
        for change in ('alter', 'remove', 'path', 'hash'):
            d = copy.deepcopy(decisions)
            if change == 'alter': d['notes'][0]['markdown'] += ' Changed context.'
            elif change == 'remove': d['notes'].pop()
            elif change == 'path': d['notes'][0]['file'] = 'different.md'
            else: d['notes'][0]['sha256'] = '0' * 64
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'context or canonical selection changed'):
                roles.apply(self.manifest, d)
            self.assertEqual(self.current.read_bytes(), self.before)

    def test_guide_or_canonical_mapping_changes_at_lock_entry_stop_before_write(self):
        for change in ('guide', 'manifest', 'registration'):
            with self.subTest(change=change):
                d = self.decisions()
                manifest = self.output / '.session.json'
                sidecar = self.job / '.working-transcript.json'
                guide = self.output / 'README.md'
                preserved = {p: p.read_bytes() for p in (manifest, sidecar, guide)}
                changed = False
                real = roles.lock
                @contextmanager
                def changed_lock(path):
                    nonlocal changed
                    if not changed:
                        changed = True
                        if change == 'guide': guide.write_bytes(preserved[guide] + b'\nUpdated participant context.\n')
                        elif change == 'manifest':
                            alternate = self.output / 'Current.md'
                            alternate.write_bytes(self.before)
                            value = json.loads(preserved[manifest])
                            value['recordings'][0]['working_transcript'] = alternate.name
                            manifest.write_bytes(json_bytes(value))
                        else:
                            value = json.loads(preserved[sidecar])
                            value['operator_note'] = 'Registration changed during preflight.'
                            sidecar.write_bytes(json_bytes(value))
                    with real(path): yield
                with patch('agent_speech_processor.roles.lock', side_effect=changed_lock), self.assertRaisesRegex(ValueError, 'context or canonical selection changed'):
                    roles.apply(self.manifest, d)
                self.assertEqual(self.current.read_bytes(), self.before)
                if change == 'manifest': self.assertEqual((self.output / 'Current.md').read_bytes(), self.before)
                self.assertFalse((self.output / '.role-review').exists())
                for p, value in preserved.items(): p.write_bytes(value)

    def test_context_changes_after_staging_preserve_evidence_and_allow_fresh_review(self):
        d = self.decisions()
        real = roles.same_or_create
        changed = False
        def change_guide(path, data):
            nonlocal changed
            real(path, data)
            if not changed and Path(path).name == 'review.json':
                changed = True
                guide = self.output / 'README.md'
                guide.write_bytes(guide.read_bytes() + b'\nNew participant context.\n')
        with patch('agent_speech_processor.roles.same_or_create', side_effect=change_guide), self.assertRaisesRegex(ValueError, 'context or canonical selection changed'):
            roles.apply(self.manifest, d)
        self.assertEqual(self.current.read_bytes(), self.before)
        self.assertEqual(len(list((self.output / '.role-review').iterdir())), 1)
        self.assertTrue(roles.apply(self.manifest, self.decisions())['complete'])
        self.assertEqual(len(list((self.output / '.role-review').iterdir())), 2)

    def test_disposition_or_promotion_state_changes_after_staging_stop_before_publication(self):
        note = self.job / 'README.md'
        ledger = self.output / '.promotion.json'
        original = {p: p.read_bytes() for p in (note, ledger)}
        for change in ('disposition', 'new-disposition', 'removed-disposition', 'promotion-state', 'removed-promotion'):
            with self.subTest(change=change):
                for p, data in original.items(): p.write_bytes(data)
                if change == 'new-disposition': note.unlink()
                d = self.decisions()
                real = roles.same_or_create
                changed = False
                def change_policy(path, data):
                    nonlocal changed
                    real(path, data)
                    if not changed and Path(path).name == 'review.json':
                        changed = True
                        if change in ('disposition', 'new-disposition'):
                            note.write_text('Reading disposition: unusable\n')
                        elif change == 'removed-disposition': note.unlink()
                        elif change == 'removed-promotion': ledger.unlink()
                        else:
                            value = json.loads(ledger.read_bytes())
                            value['publication'] = 'prepared'
                            ledger.write_bytes(json_bytes(value))
                with patch('agent_speech_processor.roles.same_or_create', side_effect=change_policy), \
                     self.assertRaisesRegex(ValueError, 'context or canonical selection changed'):
                    roles.apply(self.manifest, d)
                self.assertEqual(self.current.read_bytes(), self.before)
                self.assertTrue(list((self.output / '.role-review').glob('*/review.json')))

    def test_icloud_apply_keeps_explicit_fetch_authorization_for_locked_reread(self):
        d = self.decisions()
        moved = self.root / 'com~apple~CloudDocs/archive'
        shutil.copytree(self.root / 'archive', moved)
        manifest = moved / 'processed/session/session.json'
        fetched = []
        def fetch(paths):
            fetched.extend(Path(p) for p in paths)
            return {'synthetic': True}, True
        with patch('agent_speech_processor.inputs.fetch', side_effect=fetch):
            self.assertTrue(roles.apply(manifest, d, fetch=True)['complete'])
        self.assertTrue(fetched)
        self.assertTrue(all(p.suffix in ('.json', '.md') for p in fetched))
        self.assertNotIn(moved / 'originals/recording.wav', fetched)
        self.assertEqual(self.current.read_bytes(), self.before)

    def test_guide_added_after_staging_requires_a_fresh_contextual_review(self):
        guide = self.output / 'README.md'
        guide.unlink()
        d = self.decisions()
        self.assertFalse(d['notes'])
        real = roles.same_or_create
        def add_guide(path, data):
            real(path, data)
            if Path(path).name == 'review.json':
                guide.write_text('# Supplied correction\n\nThe questioner is the other participant.\n')
        with patch('agent_speech_processor.roles.same_or_create', side_effect=add_guide), \
             self.assertRaisesRegex(ValueError, 'context or canonical selection changed'):
            roles.apply(self.manifest, d)
        self.assertEqual(self.current.read_bytes(), self.before)
        self.assertTrue(roles.apply(self.manifest, self.decisions())['complete'])

    def test_promotion_and_role_check_use_current_disposition_without_rewriting_ledger_evidence(self):
        ledger = self.output / '.promotion.json'
        recorded = json.loads(ledger.read_bytes())['recordings']
        note = self.job / 'README.md'
        for text, complete in (('Reading disposition: unusable\n', True), ('# Usable again\n', False)):
            with self.subTest(text=text):
                note.write_text(text)
                result = promote(self.manifest, self.output)['role_review']
                self.assertEqual(result, roles.session_status(self.manifest))
                self.assertEqual(result['complete'], complete)
                self.assertEqual(json.loads(ledger.read_bytes())['recordings'], recorded)
                self.assertEqual(self.current.read_bytes(), self.before)

    def test_crlf_transcripts_and_guides_round_trip_without_normalizing_bytes(self):
        before = self.before.replace(b'\n', b'\r\n')
        self.current.write_bytes(before)
        guide = self.output / 'README.md'
        guide.write_bytes(guide.read_bytes().replace(b'\n', b'\r\n'))
        original_guide = guide.read_bytes()
        d = self.decisions()
        self.assertEqual(d['recordings'][0]['transcript_markdown'].encode(), before)
        self.assertEqual(d['notes'][0]['markdown'].encode(), original_guide)
        self.assertTrue(roles.apply(self.manifest, d)['complete'])
        after = self.current.read_bytes()
        self.assertNotIn(b'\n', after.replace(b'\r\n', b''))
        self.assertEqual(role_text._speech(after.decode()), role_text._speech(before.decode()))
        self.assertEqual(guide.read_bytes(), original_guide)
        self.assertTrue(roles.session_status(self.manifest)['complete'])

    def test_annotations_and_indented_link_examples_do_not_change_role_readiness(self):
        roles.apply(self.manifest, self.decisions())
        for extra in ('\n    [indented example](elsewhere.md)\n', '\n<!-- Annotation:\n```\nunclosed-looking fence inside a closed comment\n-->\n'):
            with self.subTest(extra=extra):
                before = self.current.read_text() + extra
                self.current.write_text(before)
                self.assertTrue(role_text.status(before)['complete'])
                self.assertTrue(roles.apply(self.manifest, self.decisions())['complete'])
                self.assertEqual(self.current.read_text(), before)

    def test_documented_wrapped_role_note_is_a_completed_supported_example(self):
        import re
        example = (Path(__file__).parents[1] / 'examples/reading-example.md').read_text()
        text = re.search(r'```markdown\n(.*?)\n```', example, re.S)[1]
        self.assertTrue(role_text.status(text)['complete'])
