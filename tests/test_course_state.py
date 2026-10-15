"""Offline state integrity, URL/list validation and exclusive-run checks."""
import copy
import ctypes
import json
from pathlib import Path
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import course_state as state

COURSES = [{'id':'philosophy', 'url':'https://www.coursera.org/learn/philosophy',
            'title':'Philosophy', 'modules':None}]


class CourseListTests(unittest.TestCase):
    def load(self, value, extension='.json'):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ('courses' + extension)
            path.write_text(json.dumps(value) if extension == '.json' else value, encoding='utf-8-sig')
            return state.load_courses(path)

    def test_plain_list_comments_bom_and_json_object(self):
        text = '# Personal list\n\nhttps://coursera.org/learn/philosophy/\n'
        rows = self.load(text, '.txt')
        self.assertEqual(rows[0]['id'], 'philosophy')
        self.assertEqual(rows[0]['url'], COURSES[0]['url'])
        self.assertEqual(self.load({'courses':[{'url':COURSES[0]['url'], 'modules':[1,2]}]})[0]['modules'], [1,2])

    def test_reject_wrong_url_ambiguous_identity_and_repeated_courses(self):
        for url in ['http://coursera.org/learn/philosophy', 'https://evil.example/learn/philosophy',
                    'https://coursera.org.evil.example/learn/philosophy',
                    'https://user:password@coursera.org/learn/philosophy',
                    'https://coursera.org:444/learn/philosophy',
                    'https://coursera.org/learn/philosophy/lecture/abc',
                    'https://coursera.org/specializations/philosophy', 'not a URL']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.load([url])
        with self.assertRaises(ValueError):
            self.load([COURSES[0]['url'], 'https://coursera.org/learn/philosophy/'])

    def test_reject_empty_malformed_too_large_or_invalid_module_list(self):
        for value in [[], {}, {'courses':'bad'}, [None], [True],
                      [f'https://coursera.org/learn/course-{i}' for i in range(state.MAX_COURSES + 1)]]:
            with self.subTest(value=str(value)[:60]), self.assertRaises(ValueError):
                self.load(value)
        for numbers in [[], [0], [201], [True], ['1'], [1,1], [2,1], [2], [1,3]]:
            with self.subTest(numbers=numbers), self.assertRaises(ValueError):
                self.load([{'url':COURSES[0]['url'], 'modules':numbers}])


class AtomicJsonRetryTests(unittest.TestCase):
    def windows_error(self, number):
        error = OSError('Simulated Windows file conflict')
        error.winerror = number
        return error

    def test_transient_windows_replace_failures_retry_without_rewriting_temp(self):
        for number in (5, 32, 33):
            with self.subTest(winerror=number), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'queue.json'
                state.atomic_json(path, {'old':True})
                replace, fsync = state.os.replace, state.os.fsync
                attempts = []
                def conflicting_replace(source, destination):
                    attempts.append(Path(source))
                    self.assertEqual(json.loads(Path(source).read_text(encoding='utf-8')), {'new':'value'})
                    if len(attempts) < 3:
                        raise self.windows_error(number)
                    return replace(source, destination)
                with patch.object(state.os, 'replace', side_effect=conflicting_replace), \
                     patch.object(state.os, 'fsync', wraps=fsync) as synced, \
                     patch('time.sleep') as sleep:
                    state.atomic_json(path, {'new':'value'})
                self.assertEqual(len(attempts), 3)
                self.assertEqual(len(set(attempts)), 1)
                self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.025, 0.05])
                synced.assert_called_once()
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'new':'value'})
                self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_permanent_error_is_immediate_preserves_old_file_and_other_temp(self):
        for error in [self.windows_error(2), self.windows_error(87), self.windows_error(112), PermissionError('No winerror')]:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'queue.json'
                state.atomic_json(path, {'old':True})
                unrelated = Path(directory) / 'queue.json.another-writer.tmp'
                unrelated.write_text('another owner', encoding='utf-8')
                with patch.object(state.os, 'replace', side_effect=error) as replace, patch('time.sleep') as sleep:
                    with self.assertRaises(OSError) as caught:
                        state.atomic_json(path, {'new':True})
                self.assertIs(caught.exception, error)
                replace.assert_called_once()
                sleep.assert_not_called()
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'old':True})
                self.assertEqual(list(Path(directory).glob('*.tmp')), [unrelated])
                self.assertEqual(unrelated.read_text(encoding='utf-8'), 'another owner')

    def test_exhausted_transient_retry_keeps_old_file_and_cleans_own_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            state.atomic_json(path, {'old':True})
            error = self.windows_error(32)
            with patch.object(state.os, 'replace', side_effect=error) as replace, patch('time.sleep') as sleep:
                with self.assertRaises(OSError) as caught:
                    state.atomic_json(path, {'new':True})
            self.assertIs(caught.exception, error)
            self.assertEqual(replace.call_count, 8)
            delays = [call.args[0] for call in sleep.call_args_list]
            self.assertEqual(len(delays), 7)
            self.assertTrue(all(0 < delay <= 0.25 for delay in delays))
            self.assertLessEqual(sum(delays), 1.2)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'old':True})
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_cleanup_failure_does_not_hide_original_replace_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            state.atomic_json(path, {'old':True})
            original = self.windows_error(87)
            with patch.object(state.os, 'replace', side_effect=original), \
                 patch.object(Path, 'unlink', side_effect=PermissionError('cleanup blocked')):
                with self.assertRaises(OSError) as caught:
                    state.atomic_json(path, {'new':True})
            self.assertIs(caught.exception, original)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'old':True})

    def test_nested_writes_use_independent_temp_names(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            replace = state.os.replace
            sources = []
            def nested_replace(source, destination):
                sources.append(Path(source))
                if len(sources) == 1:
                    state.atomic_json(path, {'writer':'inner'})
                    self.assertEqual(json.loads(Path(source).read_text(encoding='utf-8')), {'writer':'outer'})
                return replace(source, destination)
            with patch.object(state.os, 'replace', side_effect=nested_replace):
                state.atomic_json(path, {'writer':'outer'})
            self.assertEqual(len(set(sources)), 2)
            self.assertTrue(all(source.parent == path.parent for source in sources))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'writer':'outer'})
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_concurrent_writers_do_not_share_or_delete_each_others_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            replace = state.os.replace
            barrier = threading.Barrier(2)
            sources = []
            seen = set()
            gate = threading.Lock()
            def concurrent_replace(source, destination):
                source = Path(source)
                with gate:
                    first = source not in seen
                    sources.append(source)
                    seen.add(source)
                # Only synchronize the initial writes; a Windows replacement
                # conflict can legitimately retry after the other writer ends.
                if first:
                    barrier.wait(timeout=3)
                return replace(source, destination)
            with patch.object(state.os, 'replace', side_effect=concurrent_replace), ThreadPoolExecutor(max_workers=2) as pool:
                tasks = [pool.submit(state.atomic_json, path, {'writer':number}) for number in (1,2)]
                for task in tasks:
                    task.result(timeout=5)
            self.assertEqual(len(set(sources)), 2)
            self.assertIn(json.loads(path.read_text(encoding='utf-8')), [{'writer':1}, {'writer':2}])
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows file sharing semantics')
    def test_real_windows_reader_releases_share_lock_and_atomic_write_succeeds(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            state.atomic_json(path, {'old':True})
            # Read/write sharing is allowed, delete/replace sharing is withheld.
            handle = kernel.CreateFileW(str(path), 0x80000000, 0x00000001 | 0x00000002,
                                        None, 3, 0x80, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value, ctypes.get_last_error())
            closed = threading.Event()
            def release():
                kernel.CloseHandle(handle)
                closed.set()
            timer = threading.Timer(0.1, release)
            timer.start()
            try:
                state.atomic_json(path, {'new':True})
            finally:
                timer.join(timeout=2)
                if not closed.is_set():
                    timer.cancel()
                    release()
            self.assertTrue(closed.is_set())
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'new':True})
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])


class DurableStateTests(unittest.TestCase):
    def test_restart_preserves_budget_feedback_and_requires_completion_recheck(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            queue = state.QueueStore(path, copy.deepcopy(COURSES))
            checkpoint = {'attempts':{'assignment:test':{'count':1, 'pending':False,
                'passed':False, 'feedback_ready':True}}, 'feedback':{'assignment:test':'Question 1\nIncorrect'}, 'modules':{}}
            queue.update('philosophy', status='completed', checkpoint=checkpoint)
            loaded = state.QueueStore(path, copy.deepcopy(COURSES))
            self.assertEqual(loaded.course('philosophy')['checkpoint'], checkpoint)
            self.assertEqual(loaded.course('philosophy')['status'], 'pending')
            self.assertEqual(loaded.course('philosophy')['previous_status'], 'completed')

    def test_verified_certificate_completion_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            queue = state.QueueStore(path, copy.deepcopy(COURSES))
            queue.update('philosophy', status='completed', completion_method='certificate',
                         certificate_verified_at='2026-10-15T12:00:00+05:00',
                         certificate_page_url='https://www.coursera.org/account/accomplishments/verify/ABC12345')
            loaded = state.QueueStore(path, copy.deepcopy(COURSES))
            self.assertEqual(loaded.course('philosophy')['status'], 'completed')
            self.assertEqual(loaded.course('philosophy')['previous_status'], 'completed')

    def test_removed_completed_course_and_sanitized_limits_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            another = {'id':'another-course', 'url':'https://www.coursera.org/learn/another-course',
                       'title':'Another', 'modules':None}
            queue = state.QueueStore(path, [*copy.deepcopy(COURSES), another])
            queue.update('philosophy', status='completed', checkpoint={'modules':{'1':{
                'verified_complete':True, 'items':{'lecture:test':{'verified_complete':True}}}}})
            queue.data['limits'] = {'groq_requests_remaining':4, 'groq_updated_at':'2026-10-14T18:00:00+05:00',
                                    'groq_tokens_remaining':'secret', 'api_key':'never-save'}
            queue.save()
            loaded = state.QueueStore(path, [another])
            historical = loaded.course('philosophy')
            self.assertTrue(historical['removed_from_queue'])
            self.assertEqual(historical['status'], 'completed')
            self.assertTrue(historical['checkpoint']['modules']['1']['verified_complete'])
            self.assertEqual(loaded.data['limits'], {'groq_requests_remaining':4,
                'groq_updated_at':'2026-10-14T18:00:00+05:00'})
            restarted = state.QueueStore(path, [another])
            self.assertEqual(restarted.course('philosophy')['status'], 'completed')

    def test_removed_unstarted_course_does_not_reappear_on_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            another = {'id':'another-course', 'url':'https://www.coursera.org/learn/another-course',
                       'title':'Another', 'modules':None}
            state.QueueStore(path, [*copy.deepcopy(COURSES), another])
            loaded = state.QueueStore(path, [another])
            self.assertEqual([row['id'] for row in loaded.data['courses']], ['another-course'])

    def test_historical_checkpoint_id_cannot_escape_state_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            state.atomic_json(path, {'schema_version':1, 'events':[], 'courses':[
                {'id':'../outside', 'url':COURSES[0]['url'], 'status':'completed',
                 'checkpoint_saved':True}]})
            with self.assertRaisesRegex(ValueError, 'идентификатор'):
                state.QueueStore(path, [])

    def test_corrupt_json_or_unknown_schema_is_never_overwritten(self):
        for raw in ['{broken JSON', '{"schema_version":2,"courses":[]}', '[]']:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'queue.json'
                path.write_text(raw, encoding='utf-8')
                with self.assertRaises((ValueError, AttributeError, TypeError)):
                    state.QueueStore(path, copy.deepcopy(COURSES))
                self.assertEqual(path.read_text(encoding='utf-8'), raw)

    def test_failed_atomic_replace_preserves_old_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            state.atomic_json(path, {'original':True})
            with patch.object(state.os, 'replace', side_effect=PermissionError('file in use')):
                with self.assertRaises(PermissionError):
                    state.atomic_json(path, {'original':False})
            self.assertEqual(json.loads(path.read_text()), {'original':True})
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_corrupt_checkpoint_cannot_reset_attempt_budget_or_modify_summary(self):
        variants = ['{truncated', {'attempts':{'assignment:x':{'count':-1}}},
                    {'attempts':{'assignment:x':{'count':True}}},
                    {'attempts':{'assignment:x':{'count':1, 'pending':'false'}}},
                    {'feedback':{'assignment:x':['wrong type']}}, {'modules':[]}]
        for broken in variants:
            with self.subTest(broken=broken), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'queue.json'
                state.QueueStore(path, copy.deepcopy(COURSES))
                summary = path.read_bytes()
                checkpoint = path.parent / 'checkpoints/philosophy.json'
                raw = broken if isinstance(broken, str) else json.dumps(broken)
                checkpoint.write_text(raw, encoding='utf-8')
                with self.assertRaises(ValueError):
                    state.QueueStore(path, copy.deepcopy(COURSES))
                self.assertEqual(checkpoint.read_text(encoding='utf-8'), raw)
                self.assertEqual(path.read_bytes(), summary)

    def test_missing_previously_saved_checkpoint_does_not_reset_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.json'
            store = state.QueueStore(path, copy.deepcopy(COURSES))
            store.update('philosophy', checkpoint={'attempts':{'assignment:x':{
                'count':1, 'pending':True}}, 'feedback':{}, 'modules':{}})
            self.assertTrue(json.loads(path.read_text(encoding='utf-8'))['courses'][0]['checkpoint_saved'])
            original = path.read_bytes()
            checkpoint = path.parent / 'checkpoints/philosophy.json'
            checkpoint.unlink()
            with self.assertRaises(ValueError):
                state.QueueStore(path, copy.deepcopy(COURSES))
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(checkpoint.exists())

    def test_exclusive_lock_blocks_second_owner_then_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runner.lock'
            with state.RunLock(path):
                with self.assertRaises(RuntimeError):
                    with state.RunLock(path):
                        self.fail('Second writer acquired an active lock')
            with state.RunLock(path):
                pass


if __name__ == '__main__':
    unittest.main(verbosity=2)
