# Triage merge-gate worktree cleanup

`merge_gate` can delete an existing dirty worktree at `/tmp/tb-wt-<PR number>`.
If fetch or worktree creation fails, its `finally` block still runs
`git worktree remove --force` on that path. Tracked edits and untracked files
are lost. A worktree-add failure also raises a `TypeError` while concatenating
captured bytes with the `SETUP-FAIL` string.

Each candidate now gets a unique temporary parent, and cleanup targets only
the worktree beneath it. Setup commands capture text diagnostics. The global
worktree-prune call is removed so cleanup stays scoped to this invocation.
Five offline tests use real disposable repositories and a local bare origin;
they are registered in CI and `make check`.

Validation against base `ec3fe0b769697be18e5bb9a59eb4faf0247ec485`:

- Original code: 2 passes, 1 failure (existing dirty worktree deleted), 2 errors
  (bytes/string diagnostic crash).
- Patched: all 5 regressions pass, covering preservation after success/fetch
  failure, readable add failures, clean merges, and conflicting merges.
- All 391 top-level Python tooling tests pass on Linux with Python 3.14 and
  Git 2.55.0.
- Test-registration guard and `git diff --check` pass.
- Go/Rust toolchains are unavailable locally, so compiler/runtime suites have
  not been run. Upstream CI remains required.

Reproduce with `python3 -m unittest discover -s tools -p test_triage_bot.py -v`.
The tests use no GitHub credentials or network requests.

AI-assisted contribution for consideration under the useful-improvements
track in BOUNTY.md.
