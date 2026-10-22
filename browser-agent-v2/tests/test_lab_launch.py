import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
from v2.notebooks import wait_for_notebook
from v2.handlers import HandlerError

class LaunchTests(unittest.TestCase):
 def setup_lab(self):
  self.clock=0
  self.page=Mock();self.page.context.pages=[self.page]
  self.page.wait_for_timeout.side_effect=lambda ms:setattr(self,'clock',self.clock+ms/1000)
  self.button=Mock();self.button.count.side_effect=lambda: int(self.clock>=4)
  self.button.is_enabled.return_value=True
  self.absent=Mock();self.absent.count.return_value=0
  self.origin=urlsplit('https://www.coursera.org/learn/c/programming/id/title')
  self.store=Mock()
 def run_wait(self,find):
  with patch('v2.notebooks.time.monotonic',side_effect=lambda:self.clock), patch('v2.notebooks._named_control',side_effect=lambda p,n:self.button if n=='Launch lab' else self.absent), patch('v2.notebooks.find_notebook',side_effect=find):
   return wait_for_notebook(self.page,lambda:None,self.store,{self.page},self.origin,self.origin.path)
 def test_delayed_button_clicked_once_before_editor(self):
  self.setup_lab()
  result=self.run_wait(lambda _: (self.page,'frame') if self.button.click.called and self.clock>=6 else None)
  self.assertEqual(result,(self.page,'frame'));self.button.click.assert_called_once()
  self.assertEqual(self.clock,6)
 def test_missing_launch_fails_early(self):
  self.setup_lab();self.button.count.side_effect=lambda:0
  with self.assertRaisesRegex(HandlerError,'editor was not opened'):self.run_wait(lambda _:None)
  self.assertEqual(self.clock,30);self.button.click.assert_not_called()
 def test_existing_editor_is_not_relaunched(self):
  self.setup_lab();self.run_wait(lambda _:(self.page,'frame'));self.button.click.assert_not_called()
 def test_launch_only_once_when_kernel_never_connects(self):
  self.setup_lab()
  with self.assertRaisesRegex(HandlerError,'Lab launched but'):self.run_wait(lambda _:None)
  self.button.click.assert_called_once();self.assertEqual(self.clock,124)

class KernelReadyTests(unittest.TestCase):
 def test_wait_reads_only_then_returns_idle(self):
  from v2.notebooks import NotebookExecutor
  executor=Mock()
  executor.deadline=999999999999
  executor.call.side_effect=[dict(path='a.ipynb',signature='same',kernel='starting'),dict(path='a.ipynb',signature='same',kernel='idle')]
  result=NotebookExecutor.ready_snapshot(executor)
  self.assertEqual(result['kernel'],'idle')
  self.assertEqual([c.args for c in executor.call.call_args_list],[('snapshot',),('snapshot',)])
 def test_changed_notebook_aborts(self):
  from v2.notebooks import NotebookExecutor
  executor=Mock();executor.deadline=999999999999
  executor.call.side_effect=[dict(path='a.ipynb',signature='same',kernel='busy'),dict(path='a.ipynb',signature='changed',kernel='idle')]
  with self.assertRaisesRegex(HandlerError,'Notebook changed'):NotebookExecutor.ready_snapshot(executor)
 def test_busy_timeout_does_not_interrupt(self):
  from v2.notebooks import NotebookExecutor
  executor=Mock();executor.deadline=0;executor.call.return_value=dict(path='a.ipynb',signature='same',kernel='busy')
  with self.assertRaisesRegex(HandlerError,'no execution started'):NotebookExecutor.ready_snapshot(executor)
  executor.call.assert_called_once_with('snapshot')
