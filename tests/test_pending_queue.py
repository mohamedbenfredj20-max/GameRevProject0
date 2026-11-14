import os
import tempfile
import unittest
from unittest import mock
import backup

class PendingQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: getattr(backup, k) for k in ("WORKSPACE_DIR", "INPUT_DIR", "OUTPUT_DIR", "AREAS", "PENDING_DIR", "TMP_DIR", "STATE_FILE")}
        backup.WORKSPACE_DIR = os.path.join(self.tmp.name, "workspace")
        backup.INPUT_DIR = os.path.join(backup.WORKSPACE_DIR, "input")
        backup.OUTPUT_DIR = os.path.join(backup.WORKSPACE_DIR, "output")
        backup.AREAS = {"input": backup.INPUT_DIR, "output": backup.OUTPUT_DIR}
        backup.PENDING_DIR = os.path.join(backup.WORKSPACE_DIR, "backups_pending")
        backup.TMP_DIR = os.path.join(backup.WORKSPACE_DIR, ".backup_tmp")
        backup.STATE_FILE = os.path.join(backup.WORKSPACE_DIR, ".backup_state.json")
        os.makedirs(backup.INPUT_DIR)
        os.makedirs(backup.OUTPUT_DIR)
        with open(os.path.join(backup.INPUT_DIR, "sample.bin"), "wb") as f:
            f.write(b"important project data" * 50)

    def tearDown(self):
        for k, v in self.old.items():
            setattr(backup, k, v)
        self.tmp.cleanup()

    def test_drive_unavailable_still_creates_persistent_local_backup(self):
        with mock.patch.object(backup, "check_drive", return_value=backup.DriveStatus(False, "network", "offline")):
            self.assertFalse(backup.run_backup())
        pending = backup._pending_runs()
        self.assertEqual(len(pending), 1)
        self.assertTrue(any(n.endswith(".zip") for n in os.listdir(pending[0])))
        self.assertTrue(any(n.endswith(".manifest.json") for n in os.listdir(pending[0])))

    def test_failed_upload_does_not_delete_existing_pending_backup(self):
        os.makedirs(backup.PENDING_DIR)
        old = os.path.join(backup.PENDING_DIR, "older-pending")
        os.makedirs(old)
        with open(os.path.join(old, "keep.txt"), "w") as f:
            f.write("must remain")
        with mock.patch.object(backup, "check_drive", return_value=backup.DriveStatus(True)), \
             mock.patch.object(backup, "list_remote_dir", return_value=(True, [])), \
             mock.patch.object(backup, "upload_backup", side_effect=backup.RcloneError("network", "offline")):
            backup.run_backup()
        self.assertTrue(os.path.isfile(os.path.join(old, "keep.txt")))
        self.assertGreaterEqual(len(backup._pending_runs()), 2)

    def test_default_interval_is_five_minutes(self):
        self.assertEqual(backup.DEFAULT_INTERVAL, 300)

if __name__ == "__main__":
    unittest.main()
