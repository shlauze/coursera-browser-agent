"""Offline setup regressions; only public dummy keys in temporary directories."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import configure_new_pc as configure
import groq_credentials as credentials

DUMMY_KEY = 'gsk_' + 'public_dummy_key_for_offline_tests_only'


class NameSettingsTests(unittest.TestCase):
    def test_valid_name_is_trimmed_and_internal_controls_are_rejected(self):
        self.assertEqual(configure.validate_name('  Test Learner  '), 'Test Learner')
        for name in ['', '   ', 'x' * 161, 'Test\nLearner', 'Test\rLearner',
                     'Test\tLearner', 'Test\x00Learner', 'Test\x1fLearner']:
            with self.subTest(name=repr(name)), self.assertRaises(ValueError):
                configure.validate_name(name)

    def test_missing_settings_has_no_override_and_valid_settings_create_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            self.assertIsNone(configure.load_notice(path))
            path.write_text(json.dumps({'learner_name':' Test Learner '}), encoding='utf-8-sig')
            self.assertEqual(configure.load_notice(path), 'I, Test Learner, understand and agree.')

    def test_invalid_settings_are_rejected_and_not_modified(self):
        for value in ['{incomplete JSON', '[]', '{}', '{"learner_name":123}',
                      json.dumps({'learner_name':'Test\nLearner'}), json.dumps({'learner_name':''})]:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'settings.json'
                path.write_text(value, encoding='utf-8')
                with self.assertRaises(ValueError):
                    configure.load_notice(path)
                self.assertEqual(path.read_text(encoding='utf-8'), value)


@unittest.skipUnless(sys.platform == 'win32', 'Real DPAPI round trip requires Windows')
class NewComputerWizardTests(unittest.TestCase):
    def test_wizard_saves_encrypted_dummy_key_and_new_name_without_api_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key_file = root / '.groq-key.dpapi'
            output = io.StringIO()
            with patch.object(configure, 'ROOT', root), patch.object(credentials, 'KEY_FILE', key_file), \
                 patch.object(sys, 'argv', ['configure_new_pc.py', '--skip-api-check']), \
                 patch.object(configure.getpass, 'getpass', side_effect=['invalid', DUMMY_KEY]), \
                 patch('builtins.input', side_effect=['', '  Test Learner  ']), \
                 patch.object(configure.subprocess, 'run', side_effect=AssertionError('No subprocess permitted')) as child, \
                 contextlib.redirect_stdout(output):
                self.assertEqual(configure.main(), 0)
                self.assertEqual(credentials.load_key(), DUMMY_KEY)
                self.assertEqual(configure.load_notice(), 'I, Test Learner, understand and agree.')
            child.assert_not_called()
            self.assertTrue(key_file.is_file())
            self.assertNotIn(DUMMY_KEY.encode('utf-8'), key_file.read_bytes())
            self.assertEqual(json.loads((root / 'settings.json').read_text(encoding='utf-8')),
                             {'learner_name':'Test Learner'})
            self.assertNotIn(DUMMY_KEY, output.getvalue())

    def test_api_check_child_uses_saved_key_and_removes_ambient_key_from_environment(self):
        for returncode in (0, 7):
            with self.subTest(returncode=returncode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                key_file = root / '.groq-key.dpapi'
                with patch.object(configure, 'ROOT', root), patch.object(credentials, 'KEY_FILE', key_file), \
                     patch.object(sys, 'argv', ['configure_new_pc.py']), \
                     patch.object(configure.getpass, 'getpass', return_value=DUMMY_KEY), \
                     patch('builtins.input', return_value='Second Learner'), \
                     patch.dict(configure.os.environ, {'GROQ_API_KEY':'PUBLIC_AMBIENT_TEST_VALUE',
                                                        'OFFLINE_CONFIG_TEST':'retained'}), \
                     patch.object(configure.subprocess, 'run', return_value=SimpleNamespace(returncode=returncode)) as child, \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(configure.main(), returncode)
                    self.assertEqual(credentials.load_key(), DUMMY_KEY)
                    self.assertEqual(configure.load_notice(), 'I, Second Learner, understand and agree.')
                    self.assertEqual(configure.os.environ['GROQ_API_KEY'], 'PUBLIC_AMBIENT_TEST_VALUE')
                child.assert_called_once()
                command = child.call_args.args[0]
                self.assertEqual(command, [sys.executable, '-X', 'utf8', str(root / 'coursera_start.py'), '--check-api'])
                self.assertEqual(child.call_args.kwargs['cwd'], root)
                self.assertNotIn('GROQ_API_KEY', child.call_args.kwargs['env'])
                self.assertEqual(child.call_args.kwargs['env']['OFFLINE_CONFIG_TEST'], 'retained')
                self.assertNotIn(DUMMY_KEY.encode('utf-8'), key_file.read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
