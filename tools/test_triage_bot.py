#!/usr/bin/env python3
"""Offline merge-gate regressions using disposable, real Git repositories."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage_bot as tb  # noqa: E402


@unittest.skipUnless(shutil.which("git"), "Git is required")
class MergeGateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }))
        self.repo = self.root / "repo"
        self.origin = self.root / "origin.git"
        self.run_git("init", "--bare", "--initial-branch=main", str(self.origin))
        self.run_git("init", "--initial-branch=main", str(self.repo))
        self.git("config", "user.name", "Merge Gate Test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")
        self.git("remote", "add", "origin", str(self.origin))
        (self.repo / "tracked.txt").write_text("base\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        self.enterContext(patch.object(tb, "__file__", str(self.repo / "tools/triage_bot.py")))

    def run_git(self, *args, check=True):
        return subprocess.run(["git", *args], check=check, text=True,
                              capture_output=True, timeout=15)

    def git(self, *args, check=True):
        return self.run_git("-C", str(self.repo), *args, check=check)

    def candidate(self, publish=True, score=1):
        # Unique even when separate test processes run concurrently. The legacy
        # path is created only by git worktree add, which refuses existing paths.
        number = uuid.uuid4().int
        if publish:
            self.git("push", "origin", "HEAD:refs/pull/%d/head" % number)
        return {"number": number, "bucket": "LAND-CANDIDATE",
                "score": score, "additions": 1}

    def dirty_legacy_worktree(self, item):
        path = Path("/tmp/tb-wt-%d" % item["number"])
        self.git("worktree", "add", "--detach", str(path), self.base)
        self.addCleanup(self.git, "worktree", "remove", "--force", str(path), check=False)
        (path / "tracked.txt").write_text("uncommitted edit\n")
        (path / "untracked.txt").write_text("untracked work\n")
        return path

    def assert_dirty_worktree_preserved(self, path):
        self.assertTrue(path.exists(), "an unrelated worktree was deleted")
        self.assertEqual((path / "tracked.txt").read_text(), "uncommitted edit\n")
        self.assertEqual((path / "untracked.txt").read_text(), "untracked work\n")

    def worktrees(self):
        return self.git("worktree", "list", "--porcelain").stdout

    def test_existing_dirty_worktree_survives_success(self):
        item = self.candidate()
        existing = self.dirty_legacy_worktree(item)
        before = self.worktrees()
        verdicts = tb.merge_gate([item], self.base, 1)
        self.assert_dirty_worktree_preserved(existing)
        self.assertEqual(verdicts[item["number"]], "MERGE-CLEAN")
        self.assertEqual(item["merge_gate"], "MERGE-CLEAN")
        self.assertEqual(self.worktrees(), before)

    def test_failed_fetch_preserves_existing_worktree_and_continues(self):
        missing = self.candidate(publish=False, score=2)
        following = self.candidate()
        existing = self.dirty_legacy_worktree(missing)
        before = self.worktrees()
        verdicts = tb.merge_gate([missing, following], self.base, 2)
        self.assert_dirty_worktree_preserved(existing)
        self.assertTrue(verdicts[missing["number"]].startswith("SETUP-FAIL: "))
        self.assertIn("remote ref", verdicts[missing["number"]])
        self.assertEqual(verdicts[following["number"]], "MERGE-CLEAN")
        self.assertEqual(self.worktrees(), before)

    def test_add_failure_is_reported_as_text(self):
        item = self.candidate()
        # A file in place of Git's worktree registry forces a genuine add
        # failure, including the stderr captured by merge_gate.
        (self.repo / ".git/worktrees").write_text("not a directory\n")
        verdicts = tb.merge_gate([item], self.base, 1)
        self.assertTrue(verdicts[item["number"]].startswith("SETUP-FAIL: "))
        self.assertIn("fatal:", verdicts[item["number"]])
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.base)
        self.assertEqual((self.repo / "tracked.txt").read_text(), "base\n")

    def test_clean_merge_cleans_up_and_preserves_checkout(self):
        item = self.candidate()
        (self.repo / "main-only.txt").write_text("main change\n")
        self.git("add", "main-only.txt")
        self.git("commit", "-m", "advance main")
        main_sha = self.git("rev-parse", "HEAD").stdout.strip()
        (self.repo / "tracked.txt").write_text("local dirty edit\n")
        before = self.worktrees()
        verdicts = tb.merge_gate([item], main_sha, 1)
        self.assertEqual(verdicts[item["number"]], "MERGE-CLEAN")
        self.assertEqual(self.worktrees(), before)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), main_sha)
        self.assertEqual((self.repo / "tracked.txt").read_text(), "local dirty edit\n")

    def test_conflict_cleans_up_and_preserves_checkout(self):
        self.git("switch", "-c", "candidate")
        (self.repo / "tracked.txt").write_text("candidate change\n")
        self.git("commit", "-am", "candidate change")
        item = self.candidate()
        self.git("switch", "main")
        (self.repo / "tracked.txt").write_text("main change\n")
        self.git("commit", "-am", "main change")
        main_sha = self.git("rev-parse", "HEAD").stdout.strip()
        before = self.worktrees()
        verdicts = tb.merge_gate([item], main_sha, 1)
        self.assertEqual(verdicts[item["number"]], "MERGE-CONFLICT")
        self.assertEqual(self.worktrees(), before)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), main_sha)
        self.assertEqual((self.repo / "tracked.txt").read_text(), "main change\n")
        self.assertEqual(self.git("status", "--porcelain").stdout, "")


if __name__ == "__main__":
    unittest.main()
