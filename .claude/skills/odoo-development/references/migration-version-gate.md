# Migration Version Gate

A defensive infrastructure that prevents the **single highest-impact silent-failure mode in our deploy pipeline**: an Odoo migration that ships in a feature branch but, because its folder version is `<= installed_version` on production, is silently skipped by Odoo's migration loader. The data invariant the migration was supposed to keep alive then drifts back to NULL/default on every environment that already crossed that version, and nobody notices until something downstream breaks weeks later.

This reference covers:

- The bug class this protects against, with the canonical 2026-04 NAS-import case study.
- The three-layer architecture (riverdeploy CLI + pre-commit/pre-merge-commit hook + GitHub Actions CI).
- The auto-renumber semantics (what gets renamed, what gets bumped, what gets preserved).
- A developer briefing — what changes when you add a new migration, what to do when the gate flags your merge.
- A troubleshooting Q&A for the most common failure modes.
- File-by-file reference (where each piece lives).

**Related references:** [`migrations.md`](migrations.md) (how Odoo migrations work + module-boundary rules), [`deploy-tool.md`](../../../../../radar/.claude/skills/crewradar-development/references/deploy-tool.md) (the riverdeploy CLI this gate builds on), [`xml-data-noupdate.md`](xml-data-noupdate.md) (the noupdate=1 gotcha that motivates many of the gated migrations).

---

## Why this exists

### The bug class

Odoo's migration loader runs `migrations/<version>/post-migrate.py` for every version `>` the database's installed-module version, up to the new manifest version. A migration whose folder version is `<= installed_version` is silently skipped. There is no warning, no log line, no error.

This is fine when migrations are written contemporaneously with the manifest bump. It breaks catastrophically when:

1. A developer creates a feature branch off `dev` when `dev` is at, say, `19.0.3.81`.
2. Developer adds `migrations/19.0.3.81/post-migrate.py` to fix some data invariant.
3. Meanwhile, `dev → test1 → main` deploys keep running, each bumping `dev`'s manifest. By the time the feature branch is ready to merge, `dev` is at `19.0.3.140` and `main` is at `19.0.3.139`.
4. The feature branch is merged into `dev`. Git is happy: no conflicts on the migration folder (it's a new file). The manifest version on `dev` stays at `19.0.3.140` because dev's bump value > feature's value.
5. `dev → test1 → main` ships the merged code to production. Production's `installed_version` for the module is `19.0.3.139`. After the upgrade it becomes `19.0.3.141`.
6. **Odoo skips `migrations/19.0.3.81/post-migrate.py`** because 19.0.3.81 ≤ 19.0.3.139. The data fixup never runs. The data invariant breaks silently. Months later, downstream code that depends on the invariant produces wrong output.

### The 2026-04 NAS-import case study

The orphaned `crewradar_cuneus_sign/migrations/19.0.3.82/19.0.3.81/post-migrate.py` (a renumber-tool bug) was the immediate trigger, but the diagnostic work surfaced the deeper class of bug above. After the renumber tool was fixed (`riverdeploy/module/merge.py::_safe_rename_migration_dir` + `_assert_no_nested_version_dirs`, Session 60 item 1b), this gate was the architectural answer to "could this happen another way?" Answer: yes, any time a developer's local clone hadn't run `merge-branches` recently and the dev manifest had moved on.

Full forensics in [`.claude/skills/rivercreds-docs/references/plan-live-restore-resilience.md §Q2`](../../rivercreds-docs/references/plan-live-restore-resilience.md).

### What the gate guarantees

For every merge into `dev` or `main`:

- Every migration folder added by the merge has a version `> max(dev, main) ceiling`.
- The destination's manifest version is bumped to at-or-above the highest renumbered migration.
- Operator overrides (HR-set fields, deliberate non-canonical values) are never clobbered.
- The check is enforced server-side (CI) so it can't be bypassed by `--no-verify` or a developer with hooks disabled.

What the gate does NOT guarantee:

- That migrations themselves are correct (they could still be no-ops or have bugs — that's what tests are for).
- That a **local prod restore + `-u` on a feature branch** upgrades correctly. The hook/CI guard merges into dev/main only, and the `_merge_dev_if_feature_branch` net only runs at the tail of the riverdeploy `restore_workflow()` — a manual restore (e.g. unzipping a downloaded backup straight into a local DB) bypasses all of it, so a branch migration whose folder version has been overtaken by the pipeline's auto-bumps skips silently. Prevention: author feature-branch migrations at a **higher minor** (see the Developer Briefing below).
- That data invariants written by a migration on a now-deleted feature branch are recoverable. If you delete a migration before merging, no gate can resurrect it.
- That a migration that ran successfully but was wrong is rolled back. Migrations are forward-only.

---

## Architecture

Three layers, each catching what the previous layer can't:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Layer 1 — riverdeploy CLI (riverdeploy/module/merge.py)                  │
│   The original authoring tool. Operators run                             │
│   `python -m riverdeploy merge-branches <source> <target>` to merge a    │
│   feature branch into dev safely. Renumber + commit + merge in one shot. │
│   Already exists; this gate adds the auto-discover logic + the           │
│   _apply_renumbering hoist for hook reuse.                               │
│                                                                          │
│   Scope: deliberate operator action.                                     │
└──────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────────┐
│ Layer 2 — Local hook (.git_hooks/migration_version_gate.py)              │
│   Wired in .pre-commit-config.yaml at TWO stages:                        │
│     * pre-merge-commit  — fires on every `git merge` that produces a     │
│                            merge commit (no fast-forward, no conflicts). │
│     * pre-commit        — fires on every `git commit`. The hook         │
│                            self-detects whether a merge is in progress  │
│                            (.git/MERGE_HEAD exists) and bails if not.   │
│                                                                          │
│   When a merge into dev/main introduces stale migrations, the hook      │
│   auto-renumbers IN PLACE against the in-progress merge's working tree, │
│   re-stages, exits 0 → the merge commit lands with corrected content.   │
│                                                                          │
│   Scope: every developer who ran `pre-commit install --hook-type ...`.  │
└──────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────────┐
│ Layer 3 — GitHub Actions CI (.github/workflows/migration-gate.yml)       │
│   Enforced server-side. Triggered on:                                    │
│     * pull_request to dev/main                                           │
│     * push to dev/main                                                   │
│   Read-only check via .git_hooks/ci_migration_gate.py.                   │
│   Fails the workflow + emits ::error:: annotation with the exact local   │
│   command for the developer to run.                                      │
│                                                                          │
│   Scope: catches GitHub PR Merge button merges, --no-verify bypasses,    │
│   missing pre-commit install, and any future paths into dev/main.        │
└──────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────────┐
│ Pre-existing safety net                                                  │
│   * `_safe_rename_migration_dir` (Session 60 item 1b) — prevents the     │
│     git-mv-into-existing-dir nesting bug that produced the orphaned      │
│     19.0.3.82/19.0.3.81/post-migrate.py.                                 │
│   * `_assert_no_nested_version_dirs` — belt-and-braces scan after every  │
│     renumber pass, fails loudly on any orphan shape.                     │
│   * `_merge_dev_if_feature_branch` in crewradar_db_restore.py — auto-   │
│     runs merge-branches at the tail of every db restore so a fresh       │
│     restore-and-upgrade lands on a clean migration state.                │
└──────────────────────────────────────────────────────────────────────────┘
```

### Discovery of "modules with migrations"

`riverdeploy.module.merge.discover_migration_modules(radar_path, refs=None)` is the single source of truth. Two-source union:

1. **Working tree pass**: any direct child of the radar workspace that has both a `__manifest__.py` and a `migrations/` directory.
2. **Optional ref-based pass via `git ls-tree`**: when refs are passed (the analyzer does this for source/target/main), every ref's tree is scanned too. Catches modules that exist in a branch's tree but not in the current working tree (e.g. an empty `migrations/` directory disappears across branch switches because git doesn't track empty dirs).

This is what makes the gate **future-proof**: any new radar module that grows a `migrations/` directory is picked up automatically. No constant to update.

### The renumber semantics

When stale migrations are detected (folder version `<=` the dev/main ceiling), `_apply_renumbering`:

1. For each stale folder, computes a new version = `ceiling + i + 1` (where `i` is the 0-indexed position in the sorted stale list).
2. `git mv`'s the folder via `_safe_rename_migration_dir`. This handler protects against the historical "nested-directory" bug: if the target version folder already exists, it merges files instead of nesting; if both folders contain the same migration script (e.g. both have `post-migrate.py`), it raises with consolidation guidance.
3. Runs `_assert_no_nested_version_dirs` per module — belt-and-braces scan that fails loudly on any orphan shape that slipped through.
4. If the module's manifest version is below the highest renumbered migration, bumps it via `_update_manifest_version`. The bump is two-pass:
   - **Precise replace**: replaces `"version": "<exact-feature-version>"` if found. Used by the standard CLI flow where the manifest reads from the feature branch's tip.
   - **Regex fallback**: replaces whichever `"version": "..."` string is currently in the file with the new value. Used when the hook fires mid-merge and the manifest already has dev's value (auto-merged by git).

The auto-fix is **strictly broadens-correctness, never destroys data**: it only flips broken-state → correct-state, never the reverse. Operator overrides survive because the precondition gates (`IS NULL`, `unique_per_holder = true`, etc.) match nothing once the value has been set.

### Re-entry guard

`merge-branches` itself produces a merge commit at the end of its renumber-then-merge flow. That merge would re-trigger the hook in an infinite loop. The hook short-circuits to exit 0 when the env-var `RIVERDEPLOY_MERGE_BRANCHES_RUNNING=1` is set; merge-branches sets it on entry (in `MergeManager.merge_branches`), restores prior value on exit.

### Logging

Every hook run tees its output to `.git/migration-gate.log` (newest run appended, with an ISO timestamp header). Use `cat .git/migration-gate.log` for post-mortem of "why did my merge produce that commit". The log is git-ignored by virtue of living under `.git/`.

---

## Developer Briefing

### When you add a new migration

1. Create the migration at the **next available version** above `dev`'s current manifest. Read the current dev version with `git show dev:<your_module>/__manifest__.py | grep version`. Add `+1` to the last segment.
   - **On a long-lived feature branch, bump the MINOR segment instead** (dev at `19.0.4.26` → use `19.0.5.1`, `19.0.5.2`, …). The pipeline keeps auto-incrementing dev/main's *last* segment while your branch lives; once that ceiling catches up with your folder version, a **local prod restore + `-u` on the branch silently skips your migration** — and no gate layer fires on a local restore (the hook/CI only guard merges into dev/main). A higher minor stays above any last-segment bump for the branch's lifetime; on merge, the gate leaves folders above the ceiling untouched and dev continues from your minor. Case study: the Barney branch authored its MFNL activation migration as `19.0.4.26`; dev's auto-bumps independently reached `19.0.4.26`, so the 2026-07 prod restore skipped the activation (workflow never applied). Renumbering to `19.0.5.1–4` fixed it.
2. Bump your feature branch's `__manifest__.py` to the same version. (Optional but recommended; the gate will bump it for you on merge if you forget.)
3. Push, open a PR. The CI gate will verify on every push.
4. When merging via `git merge` locally: the local hook does its work silently if you've kept `dev` reasonably up-to-date.
5. When merging via the GitHub PR "Merge" button: CI is the gate. If it fails, follow the `::error::` annotation's instructions.

### When the local hook flags your merge

The hook prints a boxed diagnosis like this:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚠ Migration version gate — stale migrations detected
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Merging  feature/X  →  dev

  These migrations would be silently skipped on production
  (folder version <= dev/main ceiling):

    crewradar/migrations/19.0.0.6/   (ceiling: 19.0.0.20)

  Auto-fix plan:

     1. Renumber  crewradar/migrations/19.0.0.6/  →  crewradar/migrations/19.0.0.21/
     2. Bump      crewradar/__manifest__.py  19.0.0.5  →  19.0.0.21

  ✓ Renumbered files staged into the in-progress merge.
  ✓ Merge will finalise with the corrected content.
  ✓ Inspect with: git log -1 --stat
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

This is normal — the hook auto-fixed and the merge commit lands with corrected content. **Do not bypass with `--no-verify`** — there is no scenario where a stale migration is intentional.

If the hook emits a fallback message (`⛔ Migration version gate — auto-fix declined`) instead, follow the printed manual command:

```
git merge --abort
python -m riverdeploy merge-branches <source> <current>
```

The fallback fires when auto-fix can't safely proceed: octopus merges (3+ parents), analyzer raised an exception, or the renumber itself raised. Each of these cases prints the specific reason in the box.

### When CI flags your PR

CI's failure surfaces as a `::error::` annotation in the PR Files tab and the workflow summary. The error tells you the exact command to run locally:

```
python -m riverdeploy merge-branches feature/X dev
```

Run that, push the resulting commit, the gate re-runs and passes. The local hook would have caught this if you'd run `git merge` locally first; CI is the safety net for PR-button merges and bypassed local hooks.

### After a renumber lands: repair the tests that name a migration folder

The renumber moves folders. It does **not** touch source that names a folder as
a string, and a test that loads a migration through
`importlib.util.spec_from_file_location` does exactly that:

```python
migration_file = (
    Path(__file__).resolve().parent.parent
    / "migrations" / "19.0.6.125" / "post-migrate.py"
)
```

So every renumber breaks those paths, silently, until the suite runs. The
failure is loud but its cause is not obvious from the message:

- The folder is gone → `FileNotFoundError` on the path.
- The folder still exists but now holds a **different** migration →
  `AttributeError: module '<name>' has no attribute '<CONSTANT>'`, naming a
  constant the test reads.
- Worst case, the wrong migration imports cleanly and its `migrate()` runs, so
  the assertion fails on a business value instead. `TestWorkPermitHandIn`
  reported `'Old Permit Returned to AB' != 'Done'` for this reason — the
  end-state rename lived four folders further on.

**Resolve each path by reading the migration, never by applying the offset.**
A renumber shifts a *contiguous run* of folders by a constant, so the offset
looks reliable and is not: a folder outside the run keeps its number, and two
tests can then disagree. Confirm each target by the constant or function the
test imports (`grep -l SUBJECT_UPDATES migrations/*/post-migrate.py`), or by the
docstring, which states the migration's original version.

The `version` argument in `mod.migrate(self.env.cr, "19.0.6.124")` is
documentation only — every `migrate(cr, version)` in this repo ignores it — but
keep it in step, because a reader uses it to place the migration in sequence.

Twice now this repair has followed a renumber as a separate commit (`34761b23`,
then `75565a65` after `23946f6b`). Treat it as part of the renumber, not as a
later surprise: after any commit that renumbers folders, run the affected
module's tests before you push.

### Onboarding setup (one-time per clone)

```sh
pip3 install pre-commit
cd ~/oteny/radar
pre-commit install --hook-type pre-commit --hook-type pre-merge-commit
```

Without `--hook-type pre-merge-commit`, the gate doesn't fire on standard `git merge` operations (only on `git commit` after `git merge --no-commit`, which is the rarer path). README enforces this in the developer-setup checklist.

### What you should NOT do

- **Don't disable the hook** with `git commit --no-verify` or `git merge --no-verify`. CI will still fail and reviewers will ask why.
- **Don't manually edit migration folder names** to "fix" the gate's complaint. Use `python -m riverdeploy merge-branches` — it knows the safe rename rules.
- **Don't delete migrations** because they're "stale". A migration that already shipped to production is part of the audit trail; deleting it leaves dev/main in an inconsistent state.
- **Don't add a new module's migrations** without verifying `discover_migration_modules` picks it up. The function auto-discovers via working-tree + ref scan, but if your module is in a non-standard location (not a direct child of the radar workspace), the auto-discovery misses it. Open an issue if you hit this.

---

## Troubleshooting Q&A

### "The hook didn't fire on my `git merge`"

Three causes, in likelihood order:

1. **Fast-forward merge.** No merge commit was created → `pre-merge-commit` doesn't fire. Safe by construction: a fast-forward feature → dev means feature was already up-to-date with dev (you ran `merge-branches` recently to pull dev into feature). Fast-forwards can't introduce stale migrations.
2. **`pre-merge-commit` hook type not installed.** Check `.git/hooks/pre-merge-commit` exists. If missing, run `pre-commit install --hook-type pre-merge-commit`.
3. **You're on a feature branch.** The hook is gated to `dev` and `main` only. Merging dev → feature is the safe direction; the hook is a no-op.

### "The hook fired but auto-fix declined — what do I do?"

Follow the printed command:

```
git merge --abort
python -m riverdeploy merge-branches <source> <target>
```

The merge-branches CLI has a richer environment than the hook can construct mid-merge — it can fetch refs, run a clean checkout, and produce the renumber commit on a quiescent working tree. If merge-branches itself errors, look at the error message: most likely a `_safe_rename_migration_dir` collision (both folders contain the same migration script) requires manual consolidation. The error message tells you which file to consolidate where.

### "CI passes but my local merge fails"

Likely a stale local clone. Run `git fetch --all` and re-attempt. If still failing, run `python -m riverdeploy merge-branches dev <your-branch> --analyze-only` to see what the analyzer thinks; the output will show whether ceilings match between local and remote.

### "I see `::error::Stale migrations detected on this branch` on every PR"

Either:

1. Your branch genuinely has stale migrations and you haven't run `merge-branches` recently. Run it and push.
2. The branch is in an unusual state (e.g. detached HEAD, missing remote tracking). Check `git log --oneline origin/<base>..HEAD -- '*/migrations/*'` for the actual diff CI sees.

### "How do I test the hook locally without doing a real merge?"

Two options:

1. Run the test suite:
   ```sh
   python .git_hooks/test_migration_version_gate.py
   ```
   Five scenarios: clean merge, stale merge auto-fix, non-gated branch, re-entry guard, no-merge-in-progress. All against throwaway temp repos.

2. Simulate by hand on a throwaway repo. The test file's `_GateHookTestBase` is the template — set up a temp repo with branches `main`/`dev`/`feature/X`, start a merge, invoke `hook_mod.main()` directly.

### "How do I test the CI script locally?"

Run it directly with `--workspace` pointing at a temp repo:

```sh
python .git_hooks/ci_migration_gate.py \
    --head feature/X --base dev \
    --workspace /tmp/test_repo
```

Exit codes: 0 (clean), 1 (stale), 2 (analyzer error).

### "What happens if `merge-branches` itself has a bug and produces a merge commit with stale migrations?"

The re-entry guard short-circuits the hook during `merge-branches`'s own merge → if the bug is in merge-branches's renumber logic, the hook won't catch it locally. CI is the backstop: it runs read-only `analyze` on the resulting merge commit and fails. Add a regression test to `riverdeploy.module.tests.test_merge_renumber` for the specific bug case.

### "Can I add a NEW data invariant to the auto-fix?"

Not via this gate. The gate's job is purely structural: ensure migration folders have versions above the dev/main ceiling. The CONTENT of migrations (what they invariant they enforce) is up to the migration author. If you need a runtime data invariant that survives skipped migrations, that's a different design — the previous (rejected) "HEAD-version safety-net pattern" attempt is documented in [`../../rivercreds-docs/references/plan-live-restore-resilience.md §2`](../../rivercreds-docs/references/plan-live-restore-resilience.md). The current consensus is that the gate (preventive) is preferred over a safety-net (recovery), since the gate makes "skipped migration" structurally impossible rather than detectable-after-the-fact.

### "Why does the hook tee to `.git/migration-gate.log`?"

For post-mortem. Hook output goes to stderr, which is captured by the pre-commit framework and shown in the terminal — but if you closed the terminal or scrolled past, the boxed diagnosis is gone. The log preserves every run with an ISO timestamp header. `cat .git/migration-gate.log | tail -100` shows the recent history.

### "What about merges into `test1`?"

Skipped — `test1` is sandwiched between two gated branches in the deploy pipeline (`dev → test1 → main`), so the marginal value of gating it is low. If you ever push directly to `test1` from a non-`dev` source (recovery scenario), no automated gate fires; the safety net is operator awareness. This is documented in the workflow YAML's comments.

### "What about the `riverdeploy/module/merge.py:MIGRATION_MODULES` list I see referenced in old docs?"

Removed. `discover_migration_modules` replaced it. Old PRs that mention "added module X to MIGRATION_MODULES" are now no-ops — discovery is automatic. Old plan documents (e.g. `plan-live-restore-resilience.md`) still reference the constant; treat those references as historical context.

---

## File-by-file reference

### Layer 1 — riverdeploy CLI

- **`riverdeploy/module/merge.py`** — the canonical implementation.
  - `discover_migration_modules(radar_path, refs=None)` — auto-discovery of migration-bearing modules, working-tree + optional ref-based union.
  - `MergeManager.analyze(source, target)` — computes ceiling = max(main, dev) and per-module stale list. Used by both the CLI, the hook, and the CI script.
  - `MergeManager._apply_renumbering(analysis)` — the rename + manifest-bump worker, hoisted from `renumber()` so the hook can call it without a checkout/commit.
  - `MergeManager._safe_rename_migration_dir(old_path, new_path)` — collision-safe `git mv` (Session 60 item 1b).
  - `MergeManager._assert_no_nested_version_dirs(module)` — orphan-shape detector (Session 60 item 1b).
  - `MergeManager._update_manifest_version(module, old, new)` — two-pass version-string replace (precise → regex fallback).
  - `MergeManager.merge_branches(source, target, analyze_only=False)` — full CLI workflow. Sets `RIVERDEPLOY_MERGE_BRANCHES_RUNNING=1` for the duration to suppress hook re-entry.

- **`riverdeploy/module/tests/test_merge_renumber.py`** — 12 tests covering rename collision protection, orphan detection, and discovery.

### Layer 2 — Local hook

- **`.git_hooks/migration_version_gate.py`** — the hook script. Exits 0 on no-op or successful auto-fix, non-zero on fallback (which aborts the merge for user action).

- **`.git_hooks/test_migration_version_gate.py`** — 5 end-to-end tests against throwaway repos.

- **`.pre-commit-config.yaml`** — wires the hook at `pre-merge-commit` and `pre-commit` stages.

### Layer 3 — CI

- **`.github/workflows/migration-gate.yml`** — workflow definition. PR + push triggers, scoped to dev/main, with `paths` filter so it only runs when migration-relevant files change.

- **`.git_hooks/ci_migration_gate.py`** — the CI-specific runner. Read-only, exits 0/1/2 with a `::error::` annotation on stale.

### Pre-existing safety net (unchanged by this gate, but referenced)

- **`riverdeploy/crewradar_db_restore.py::_merge_dev_if_feature_branch`** — auto-runs `merge-branches` at the tail of every `restore_workflow()` call. Closes the "I just restored prod and forgot to renumber" gap.

### Documentation

- **`README.md` § Migration version gate** — onboarding-level overview.
- **`CLAUDE.md` § Project guidelines** — one-line pointer for AI agents.
- **[`migrations.md`](migrations.md)** — sibling reference: how Odoo migrations work in this workspace; module-boundary rules.
- **[`deploy-tool.md`](../../../../../radar/.claude/skills/crewradar-development/references/deploy-tool.md)** — sibling reference: the riverdeploy CLI this gate builds on.
- **[`xml-data-noupdate.md`](xml-data-noupdate.md)** — sibling reference: the `noupdate=1` gotcha that motivates many of the gated migrations.

---

## Change-management contract

When modifying anything in this gate:

- **Adding a new module** → no change needed; `discover_migration_modules` picks it up automatically. Verify by running `python -m riverdeploy merge-branches dev <feature> --analyze-only` and checking the output mentions the new module.
- **Changing the analyzer's ceiling logic** → update both the renumber tests (`riverdeploy/module/tests/test_merge_renumber.py`) and the hook tests (`.git_hooks/test_migration_version_gate.py`). The hook delegates to the analyzer; the analyzer change must not break the hook's auto-fix path.
- **Adding a new failure mode to the hook's fallback path** → update the troubleshooting Q&A in this file with the new failure's symptom + remediation.
- **Changing the CI workflow's triggers or paths filter** → re-evaluate the "what falls outside CI's scope" section. If a new path can introduce stale migrations, add it to the `paths` filter.
- **Bumping the python version** → CI workflow specifies `python-version: "3.12"`. Local tests run on whatever Python is in the user's venv (`/Users/ries/odoo/venv/bin/python3` for the canonical setup); coordinate the two if you change one.
