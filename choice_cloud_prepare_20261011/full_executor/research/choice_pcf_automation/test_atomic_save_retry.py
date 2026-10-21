import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import watch_engine


class AtomicSave(unittest.TestCase):
    def test_windows_reader_lock_retries_only_replace(self):
        error=PermissionError('sharing violation');error.winerror=32
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json';real=watch_engine.os.replace
            with patch.object(watch_engine.os,'replace',side_effect=[error,None]) as replace,patch.object(watch_engine.time,'sleep'):
                watch_engine.save(path,{'ready':True})
                self.assertEqual(replace.call_count,2)
            real(path.with_suffix('.json.tmp'),path)
            self.assertIn('true',path.read_text())
    def test_persistent_permission_error_still_fails_after_bounded_retries(self):
        error=PermissionError('denied');error.winerror=5
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(watch_engine.os,'replace',side_effect=error) as replace,patch.object(watch_engine.time,'sleep'):
                with self.assertRaises(PermissionError):watch_engine.save(Path(directory)/'state.json',{})
                self.assertEqual(replace.call_count,10)


if __name__=='__main__':unittest.main()
