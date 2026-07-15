import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_install_source.sh"


class CheckInstallSourceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)
        scripts = self.repo / "scripts"
        scripts.mkdir()
        shutil.copy2(CHECKER, scripts / CHECKER.name)
        (self.repo / "tracked.txt").write_text("base\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
            cwd=self.repo,
            check=True,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def run_checker(self, **environment):
        env = os.environ.copy()
        env.update(environment)
        return subprocess.run(
            ["sh", str(self.repo / "scripts" / CHECKER.name), str(self.repo)],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_accepts_clean_current_checkout(self):
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Install source verified", result.stdout)

    def test_blocks_dirty_checkout_unless_explicitly_allowed(self):
        (self.repo / "tracked.txt").write_text("dirty\n")
        blocked = self.run_checker()
        self.assertEqual(blocked.returncode, 4)
        self.assertIn("source worktree has tracked or untracked changes", blocked.stderr)

        allowed = self.run_checker(SMART_HOME_ALLOW_DIRTY_INSTALL="1")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_blocks_checkout_behind_upstream_unless_explicitly_allowed(self):
        (self.repo / "tracked.txt").write_text("upstream\n")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "upstream"], cwd=self.repo, check=True, capture_output=True)
        upstream = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True, text=True, capture_output=True
        ).stdout.strip()
        subprocess.run(["git", "update-ref", "refs/remotes/origin/main", upstream], cwd=self.repo, check=True)
        subprocess.run(["git", "reset", "--hard", "HEAD^"], cwd=self.repo, check=True, capture_output=True)

        blocked = self.run_checker()
        self.assertEqual(blocked.returncode, 3)
        self.assertIn("missing commits from origin/main", blocked.stderr)

        allowed = self.run_checker(SMART_HOME_ALLOW_STALE_INSTALL="1")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_blocks_when_upstream_cannot_be_verified(self):
        subprocess.run(["git", "update-ref", "-d", "refs/remotes/origin/main"], cwd=self.repo, check=True)
        blocked = self.run_checker()
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("cannot verify origin/main", blocked.stderr)

        allowed = self.run_checker(SMART_HOME_ALLOW_UNVERIFIED_INSTALL="1")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)


if __name__ == "__main__":
    unittest.main()
