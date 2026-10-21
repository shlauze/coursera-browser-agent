import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from v2.store import Store

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
