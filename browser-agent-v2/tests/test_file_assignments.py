import unittest,tempfile,hashlib
from pathlib import Path
from unittest.mock import Mock
from v2.file_assignments import require_file_source,upload_and_submit
from v2.handlers import HandlerError
class FileTests(unittest.TestCase):
 def test_missing_source_never_uploads(self):
  s=Mock();s.get_meta.return_value=None;p=Mock(url='https://www.coursera.org/learn/example/assignment-submission/abc/test/attempt')
  with self.assertRaisesRegex(HandlerError,'explicitly assigned'):upload_and_submit(p,lambda:None,s,1)
  p.locator.assert_not_called()
 def test_source_bound_to_assignment_and_digest(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'essay.txt';p.write_text('My assignment')
   url='https://www.coursera.org/learn/example/assignment-submission/abc/test'
   s=Mock();s.get_meta.return_value={'assignment_url':url,'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
   self.assertEqual(require_file_source(s,url+'/attempt'),p)
   p.write_text('Changed')
   with self.assertRaisesRegex(HandlerError,'changed'):require_file_source(s,url)
 def test_uncertain_submission_never_uploads_again(self):
  s=Mock();s.get_meta.return_value='submitting';p=Mock()
  with self.assertRaisesRegex(HandlerError,'uncertain'):upload_and_submit(p,lambda:None,s,1)
  p.locator.assert_not_called()
