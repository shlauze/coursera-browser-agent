import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from v2.store import Store


class ConnectionFault:
    """Inject access failure at SQLite's lazy-open boundaries, using a real DB."""

    def __init__(self, connection, *, sql_prefix=None, error=None, commit_after=False):
        self.connection = connection
        self.sql_prefix = sql_prefix
        self.error = error or sqlite3.OperationalError('unable to open database file')
        self.commit_after = commit_after
        self.closed = False
        self.failed = False
        self.commit_calls = 0

    @property
    def row_factory(self):
        return self.connection.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.connection.row_factory = value

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, *args, **kwargs):
        if self.sql_prefix and sql.upper().startswith(self.sql_prefix.upper()) and not self.failed:
            self.failed = True
            raise self.error
        return self.connection.execute(sql, *args, **kwargs)

    def commit(self):
        self.commit_calls += 1
        if self.commit_after:
            self.connection.commit()
            raise self.error
        return self.connection.commit()

    def close(self):
        self.closed = True
        return self.connection.close()


class DatabaseRecoveryTests(unittest.TestCase):
    def test_transient_open_failure_retries_before_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.sqlite3')
            connect=sqlite3.connect
            calls=[]
            def flaky(*args,**kwargs):
                calls.append(1)
                if len(calls)==1: raise sqlite3.OperationalError('unable to open database file')
                return connect(*args,**kwargs)
            with patch('v2.store.sqlite3.connect',side_effect=flaky), patch('v2.store.time.sleep'):
                store.set_meta('example','once')
            self.assertEqual(len(calls),2)
            self.assertEqual(store.get_meta('example'),'once')
    def test_persistent_failure_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.sqlite3')
            with patch('v2.store.sqlite3.connect',side_effect=sqlite3.OperationalError('unable to open database file')) as connect, patch('v2.store.time.sleep'):
                with self.assertRaises(sqlite3.OperationalError):store.get_control_state()
                self.assertEqual(connect.call_count,3)
    def test_transaction_failure_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.sqlite3')
            connect=sqlite3.connect
            with patch('v2.store.sqlite3.connect',wraps=connect) as spy:
                with self.assertRaises(sqlite3.OperationalError):
                    with store._connection(write=True) as conn:
                        raise sqlite3.OperationalError('unable to open database file')
                self.assertEqual(spy.call_count,1)

    def test_lazy_open_setup_failures_close_and_retry_before_body(self):
        for boundary in ('PRAGMA busy_timeout', 'PRAGMA foreign_keys',
                         'SELECT name FROM sqlite_master', 'BEGIN IMMEDIATE'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                store = Store(Path(tmp) / 'state.sqlite3')
                connect = sqlite3.connect
                connections = []

                def injected(*args, **kwargs):
                    proxy = ConnectionFault(connect(*args, **kwargs),
                                            sql_prefix=boundary if not connections else None)
                    connections.append(proxy)
                    return proxy

                with patch('v2.store.sqlite3.connect', side_effect=injected), patch('v2.store.time.sleep'):
                    event_id = store.record_event('INFO', 'ONCE', 'Exactly one event')
                self.assertEqual(len(connections), 2)
                self.assertTrue(all(connection.closed for connection in connections))
                self.assertEqual(connections[0].commit_calls, 0)
                self.assertEqual(connections[1].commit_calls, 1)
                self.assertEqual([event['id'] for event in store.recent_events()], [event_id])

    def test_extended_cantopen_code_retries_without_matching_error_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.sqlite3')
            connect = sqlite3.connect
            failure = sqlite3.OperationalError('injected extended SQLite access error')
            failure.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN | (2 << 8)
            failure.sqlite_errorname = 'SQLITE_CANTOPEN_ISDIR'
            connections = []

            def injected(*args, **kwargs):
                proxy = ConnectionFault(connect(*args, **kwargs),
                                        sql_prefix='SELECT name FROM sqlite_master' if not connections else None,
                                        error=failure)
                connections.append(proxy)
                return proxy

            with patch('v2.store.sqlite3.connect', side_effect=injected), patch('v2.store.time.sleep'):
                self.assertEqual(store.get_control_state(), 'stopped')
            self.assertEqual(len(connections), 2)
            self.assertTrue(all(connection.closed for connection in connections))

    def test_persistent_lazy_open_failure_closes_every_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.sqlite3')
            connect = sqlite3.connect
            connections = []

            def injected(*args, **kwargs):
                proxy = ConnectionFault(connect(*args, **kwargs), sql_prefix='SELECT name FROM sqlite_master')
                connections.append(proxy)
                return proxy

            with patch('v2.store.sqlite3.connect', side_effect=injected), patch('v2.store.time.sleep'):
                with self.assertRaises(sqlite3.OperationalError):
                    store.get_control_state()
            self.assertEqual(len(connections), 3)
            self.assertTrue(all(connection.closed for connection in connections))

    def test_unrelated_setup_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.sqlite3')
            connect = sqlite3.connect
            connections = []

            def injected(*args, **kwargs):
                proxy = ConnectionFault(connect(*args, **kwargs), sql_prefix='PRAGMA busy_timeout',
                                        error=sqlite3.OperationalError('database is locked'))
                connections.append(proxy)
                return proxy

            with patch('v2.store.sqlite3.connect', side_effect=injected), patch('v2.store.time.sleep') as sleep:
                with self.assertRaisesRegex(sqlite3.OperationalError, 'database is locked'):
                    store.get_control_state()
            self.assertEqual(len(connections), 1)
            self.assertTrue(connections[0].closed)
            sleep.assert_not_called()

    def test_body_failure_rolls_back_and_never_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.sqlite3')
            connect = sqlite3.connect
            body_calls = 0
            with patch('v2.store.sqlite3.connect', wraps=connect) as spy:
                with self.assertRaises(sqlite3.OperationalError):
                    with store._connection(write=True) as connection:
                        body_calls += 1
                        connection.execute("INSERT INTO metadata VALUES('uncommitted', 'true')")
                        raise sqlite3.OperationalError('unable to open database file')
                self.assertEqual(spy.call_count, 1)
            self.assertEqual(body_calls, 1)
            self.assertIsNone(store.get_meta('uncommitted'))

    def test_ambiguous_commit_is_never_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.sqlite3')
            connect = sqlite3.connect
            connections = []

            def injected(*args, **kwargs):
                proxy = ConnectionFault(connect(*args, **kwargs), commit_after=True)
                connections.append(proxy)
                return proxy

            with patch('v2.store.sqlite3.connect', side_effect=injected), patch('v2.store.time.sleep') as sleep:
                with self.assertRaises(sqlite3.OperationalError):
                    store.record_event('INFO', 'COMMIT_ONCE', 'Commit outcome must not trigger replay')
            self.assertEqual(len(connections), 1)
            self.assertEqual(connections[0].commit_calls, 1)
            self.assertTrue(connections[0].closed)
            sleep.assert_not_called()
            self.assertEqual([event['code'] for event in store.recent_events()], ['COMMIT_ONCE'])

    def test_established_missing_database_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.sqlite3'
            store = Store(path)
            path.unlink()
            with self.assertRaises(sqlite3.OperationalError):
                store.get_control_state()
            self.assertFalse(path.exists())

    def test_snapshot_batches_courses_in_one_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.sqlite3')
            for index in range(12):
                course_id = f'course-{index}'
                store.upsert_course(course_id, f'Course {index}')
                store.set_meta('course_attention:' + course_id, f'Attention {index}')
            store.set_meta('skip_file_upload_assignments', True)
            store.set_meta('user_skipped_item_kinds', ['Programming Assignment'])
            action_id = store.begin_action('course-0', 'quiz-1', 'submit', {'private': 'not displayed'})
            connect = sqlite3.connect
            with patch('v2.store.sqlite3.connect', wraps=connect) as spy:
                snapshot = store.snapshot()
                self.assertEqual(spy.call_count, 1)
            self.assertEqual(len(snapshot['courses']), 12)
            self.assertEqual({course['course_id']: course['attention_reason'] for course in snapshot['courses']},
                             {f'course-{index}': f'Attention {index}' for index in range(12)})
            self.assertEqual(snapshot['pending_actions'][0]['id'], action_id)
            self.assertNotIn('payload', snapshot['pending_actions'][0])
            self.assertTrue(store.get_meta('skip_file_upload_assignments'))
            self.assertEqual(store.get_meta('user_skipped_item_kinds'), ['Programming Assignment'])

    def test_snapshot_preserves_public_fields_defaults_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.sqlite3')
            store.upsert_course('later', 'Second', page_index=2, order_index=1,
                                url='https://example.test/private', details={'private': True})
            store.upsert_course('first', 'First', page_index=1, order_index=2,
                                progress_percent=25, status='started')
            store.set_meta('course_attention:later', None)
            store.set_meta('confirmation_required', {'kind': 'learner_review'})
            store.set_meta('groq_limits', {'retry_at': 123})
            action_id = store.begin_action('first', 'quiz', 'submit', {'private': True})
            store.resolve_action(action_id, 'unclear', {'private': True})
            event_id = store.record_event('INFO', 'STATUS', 'Public status', {'private': True})
            snapshot = store.snapshot()
            self.assertEqual(set(snapshot), {'control_state', 'confirmation_required', 'courses',
                                             'groq_limits', 'pending_actions', 'events'})
            self.assertEqual(snapshot['control_state'], 'stopped')
            self.assertEqual(snapshot['confirmation_required'], {'kind': 'learner_review'})
            self.assertEqual(snapshot['groq_limits'], {'retry_at': 123})
            self.assertEqual([course['course_id'] for course in snapshot['courses']], ['first', 'later'])
            self.assertEqual(snapshot['courses'][0]['attention_reason'], '')
            self.assertIsNone(snapshot['courses'][1]['attention_reason'])
            for course in snapshot['courses']:
                self.assertEqual(set(course), {'course_id', 'title', 'progress_percent', 'status', 'skipped',
                                               'order_index', 'page_index', 'updated_at', 'attention_reason'})
                self.assertIs(course['skipped'], False)
            self.assertEqual(snapshot['courses'][0]['progress_percent'], 25)
            self.assertEqual(snapshot['courses'][0]['status'], 'started')
            self.assertEqual(snapshot['pending_actions'][0]['id'], action_id)
            self.assertEqual(set(snapshot['pending_actions'][0]),
                             {'id', 'course_id', 'item_id', 'action_kind', 'started_at'})
            self.assertEqual(snapshot['events'][0]['id'], event_id)
            self.assertEqual(set(snapshot['events'][0]), {'id', 'at', 'level', 'code', 'message'})
