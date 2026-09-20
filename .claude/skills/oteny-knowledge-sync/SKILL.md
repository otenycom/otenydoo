---
name: oteny-knowledge-sync
description: One-way sync of markdown skill files from .claude/skills/ to Odoo Knowledge articles. Covers the sync process, link rewriting across configured roots, image embedding, mermaid rendering, the manual-call traps, and troubleshooting. Use when editing oteny_knowledge_sync, adding a new Knowledge root, or debugging a stale/missing/warning-producing skill article.
---

# Knowledge Skill Sync

> One-way sync of markdown skill files from `.claude/skills/` to Odoo Knowledge articles.

## Overview

The skill sync feature automatically publishes skill documentation to the Odoo Knowledge app, making it accessible to all internal users directly within the business's Odoo. This provides a read-only view of skills inside Odoo while keeping the markdown files as the single source of truth.

## Key Features

| Feature | Description |
|---------|-------------|
| **One-way sync** | Markdown files → Knowledge articles (files are source of truth) |
| **Hierarchy preservation** | Folder structure becomes article parent-child relationships |
| **Alphabetical sorting** | Articles sorted A-Z within each parent |
| **Internal link conversion** | `.md` links converted to Knowledge SPA navigation |
| **Auto-cleanup** | Deleted markdown files → deleted Knowledge articles |
| **Auto-membership** | All internal users automatically see skills in their Knowledge sidebar |
| **Opt-out** | `sync_to_knowledge: false` in YAML keeps a file agent-only (no Knowledge article); omitted flag defaults to sync |
| **Image embedding** | Local `![](img/…)` images are uploaded as `ir.attachment`s and served via `/web/image/<id>` |

## How It Works

### Sync Process

1. **Discovery**: Walks `.claude/skills/` under **every configured Knowledge root**
2. **Parsing**: Converts markdown to HTML using `mistune`, extracts YAML frontmatter (including optional `sync_to_knowledge`; root `.claude/skills/SKILL.MD` always syncs and logs at INFO if flagged false)
3. **Title extraction**: Uses the first `# heading` from the markdown body as the Knowledge article name (falls back to frontmatter `name`/`title` or filename). The frontmatter `name` field is preserved for AI agent skill discovery but not used as the article title.
4. **Article Creation**: Creates/updates `knowledge.article` records with:
   - `x_skill_is_managed=True` (marks as managed by sync)
   - `x_skill_file_path` (tracks source file for updates/deletion)
   - `is_locked=True` (prevents editing in Odoo)
   - `internal_permission='read'` (all internal users can view)
5. **Hierarchy**: SKILL.md becomes the parent article for its directory; other `.md` files in the same directory become children of that SKILL.md article. Subdirectories recurse with the SKILL.md article as parent.
6. **Sorting**: Assigns sequence numbers for alphabetical display
7. **User Membership**: Adds all active internal users to root article
8. **Link Rewriting**: `.md` hrefs are converted to Knowledge article URLs (with the SPA navigation class) *inline while each article body is written*, using a path→article-id map of **every configured Knowledge root** (the article's own root last, so it wins a path collision). After every root has published, `_rewrite_internal_links` resolves forward references and sibling names that live in another root (for example a business article that still writes `../riverflow/SKILL.md`). In steady state every body is already final, so this pass changes nothing. A target that no configured root published still warns. The `../../otenydoo/.claude/skills/…` escape is not required; when an author already wrote it, the `skills/…` tail is looked up the same way.

### Performance (why it's fast on every upgrade)

The sync runs on every consuming business's upgrade, so it is written to do almost nothing when no skill file changed:

- **One query, not N+1**: all managed articles are loaded once up front (`existing_by_path` lookup + `link_map`), instead of one `search` per file.
- **Single-pass, stable bodies**: links are resolved inline so the stored body is byte-identical across runs. (Previously links were rewritten in a separate second pass, so every body oscillated between the raw `.md` href and the `/knowledge/article/N` URL — forcing two expensive writes per article on every upgrade.)
- **Skip-unchanged writes** (`_write_managed`): writes only the fields that actually differ, comparing the body against the *sanitized* form Odoo would store (`field.convert_to_cache`), and skips the write entirely when nothing changed. This avoids `knowledge.article`'s expensive write path — HTML sanitize, revision-history diffing via `difflib`, and sequence recompute — which would otherwise run for every article every time.
- **One disk read per file**: the `# heading` title is returned by `parse_skill_file` (key `heading`) from the content it already read, rather than re-opening and re-parsing each file in a separate `_extract_heading` step.

Net effect on a large two-root install (radar's `crewradar` plus its business
modules, ~150 articles): the sync dropped from ~7.4s to ~2s on a no-change
upgrade; the residual ~2s is mistune markdown parsing of every file (needed to
detect changes; not reducible without storing a per-file content hash).

> **Known issue**: the link-rewriting regex matches `href="…md"` even inside fenced ```` ```html ```` code blocks, because `html_sanitize` converts the escaped `&quot;` in code text back into real `"`. This corrupts *example* links shown in documentation (e.g. this very file) and causes that one article to be rewritten on every run. The regex should skip `<pre>`/`<code>` content — tracked as a separate fix.

### Trigger

The sync lives in `oteny_knowledge_sync` (`oteny.knowledge.sync`). It runs after an upgrade of **any module that lives under a configured Knowledge root**, via `_register_hook()` on `ir.module.module`. The hook is called once per registry load (STEP 9 in `odoo/modules/loading.py`). It checks `registry.updated_modules` and the module's parent folder against the configured roots, so a plain restart syncs nothing. A `-u crewradar` still triggers it because `crewradar` sits under the radar root.

```bash
# Any of these will trigger skill sync automatically
odoo-bin -u crewradar -d <database>
odoo-bin -i crewradar -d <database>
odoo-bin -u oteny_knowledge_sync -d <database>
```

The sync is skipped when `config['test_enable']` is `True` (i.e. `--test-enable` or `--test-tags` on the CLI), so it does not slow down unit test runs.

Previous versions triggered the sync via post-migration scripts (`migrations/*/post-migrate.py`). These are no longer needed for new versions since the `_register_hook` approach handles it automatically on every upgrade.

### Manual call from `odoo-bin shell` — **commit, or nothing lands**

`odoo-bin shell` does **not** commit, and `sync_skills_to_knowledge` does not commit
either. So a shell call returns `{'articles_synced': 162}`, logs `Updating body of
article …` for every changed file, and then **rolls the whole thing back on exit**. The
return value and the log look exactly like success.

```bash
printf "r = env['oteny.knowledge.sync'].sync_skills_to_knowledge()\nenv.cr.commit()\nprint(r)\n" \
  | python3 odoo-bin shell --addons-path=... --no-http -d <database> -r <user> -w <user>
```

Caught on a business install: a skill file edited *after* the upgrade-time sync stayed stale in
Knowledge through three "successful" manual re-syncs. Verify with the stored body, never
with the return value:

```sql
SELECT x_skill_file_path, write_date FROM knowledge_article
 WHERE x_skill_file_path = 'skills/<skill>/<file>.md';
```

The upgrade-time hook and the XML-RPC admin call below both commit, so this trap is
shell-only.

### Manual admin call (XML-RPC)

A consuming business's admin panel (radar's is `/update-skills`) hits `/xmlrpc/2/object`
(`oteny.knowledge.sync.sync_skills_to_knowledge`). Odoo 19 dumps XML-RPC with
`allow_none=False`. The method always returns a dict (`{"articles_synced": N}`
or a skip reason, plus a `roots` map) — an implicit `None` used to raise after
a successful write (first shipped on `crewradar` **19.0.10.9**, now on this
module).

If a database still runs an older tree that exposes a business-owned
`*.skill.sync` model instead of this one, the write can succeed and the log
still says articles synced — treat an XML-RPC `None`/fault as non-fatal in
that case. Do not drop the database to force a reload.

Regression: `oteny_knowledge_sync/tests/test_return.py`.

### File Path Mapping

| Source Path | Knowledge Location |
|-------------|-------------------|
| `.claude/skills/SKILL.MD` | Root skills article for that repository |
| `.claude/skills/<skill>/SKILL.md` | Child of root |
| `.claude/skills/<skill>/references/<file>.md` | Child of `<skill>` |

### Link Conversion

Markdown links are converted to use Odoo's Knowledge SPA navigation:

```html
<!-- Before (markdown) -->
<a href="odoo-development/SKILL.md">Link</a>

<!-- After (Knowledge) -->
<a href="/knowledge/article/80" 
   class="o_knowledge_article_link" 
   data-res_id="80">Link</a>
```

The `o_knowledge_article_link` class enables same-tab navigation instead of opening new tabs.

### Mermaid Diagram Rendering

Fenced code blocks with the `mermaid` language tag are rendered as interactive SVG diagrams in Knowledge articles:

- **Server side**: `OdooHTMLRenderer.block_code()` in `markdown_utils.py` emits `<pre class="mermaid">` instead of a plain `<pre>` when `info == "mermaid"`
- **Client side**: a business's `mermaid_viewer_patch.js` patches `HtmlViewer.processReadonlyContent` to detect `pre.mermaid` elements and call `mermaid.run()` on them
- **Lazy loading**: mermaid.js (v11) is loaded from jsDelivr CDN only when an article actually contains mermaid blocks — no npm/pip dependency

Authors can use `` ```mermaid `` blocks in skill files for flowcharts, sequence diagrams, state diagrams, etc. These show as raw code in markdown editors but render as diagrams in Odoo Knowledge.

### Table Rendering — never emit a `<thead>`

Markdown pipe tables render as `<table class="table table-bordered o_table">` with **every row, header included, inside a single `<tbody>`**; the header cells are `<th class="o_table_header">`. Odoo's `.o_table` has no `<thead>` support, so emitting one produces visibly misaligned column headers:

- `.o_table` is `display: block` (so wide tables scroll horizontally) and restores `display: table; width: 100%; table-layout: fixed` on **`tbody` only** — see `html_editor/static/src/main/table/table.scss`.
- A `<thead>` therefore falls outside that fixed-layout box and is laid out as its own content-sized anonymous table — narrow headers floating above, not lining up with the body columns.
- Odoo's editor never *stores* a `<thead>` either: `normalizeTableStructure` (`html_editor/static/src/main/table/table_plugin.js`) folds its rows into `<tbody>` and tags each `<th>` with `o_table_header`. But that normalization runs **only in the editor** — never in the read-only `HtmlViewer` that renders Knowledge articles, nor in Discuss chat. So HTML that relies on it renders broken for readers.

`OdooHTMLRenderer` (`table` / `table_head` / `table_body` / `table_cell` in `markdown_utils.py`) emits the normalized shape up front, matching the editor byte for byte. `th.o_table_header` also carries Odoo's grey bold header styling for free.

The same rule applies to **hand-written** HTML anywhere it will be read through Odoo's viewer (article bodies in XML data files, wizard result HTML): if the table carries `o_table`, put the header row in `<tbody>` as `<th class="o_table_header">`. A table with **no** `o_table` class is unaffected (it stays `display: table`), so plain Bootstrap tables such as `<table class="table table-sm">` may keep a `<thead>`.

### Image Embedding

Skill markdown can reference local images (`![alt](img/foo.png)`), and those must render for users viewing the synced article. mistune emits `<img src="img/foo.png">` with a **relative** path Odoo can't serve, so the sync uploads each locally-referenced image and rewrites the `src`:

- **Presentation** (`OdooHTMLRenderer.image()` in `markdown_utils.py`): renders `<img>` with `class="img-fluid o_we_custom_image"` so images scale to the article width; keeps the relative `src` for the sync to rewrite.
- **Upload + rewrite** (`oteny.knowledge.sync._embed_body_images`): resolves the `src` against the source markdown's folder (guarded against escaping the skills tree), find-or-creates an `ir.attachment` (`res_model='knowledge.article'`), and rewrites `src` to `/web/image/<id>`. Runs **inline while the body is written** (like link rewriting), so the stored body is final and byte-identical across runs. For a new article the attachment is created with `res_id=0` and backfilled to the new article id after `create`.
- **Dedupe by a content marker, not `checksum`** — an unchanged image must reuse its existing attachment so a re-sync writes nothing. Attachments are keyed on `description = "skill-img:<sha1 of the source file>"`, searched with `sudo()`. External / already-embedded srcs (`http`, `https`, `data:`, leading `/`) and missing files are left untouched (a missing file logs a warning). Orphaned attachments (image removed or changed) are left in place — no deletes. (Why not the attachment's own `checksum`: see [Duplicate image attachments](#duplicate-image-attachments-on-every-sync).)

The image folder's own catalog (`references/img/README.md`) sets `sync_to_knowledge: false` so it is not published as an article.

## Technical Implementation

### Key Files

| File | Purpose |
|------|---------|
| `oteny_knowledge_sync/models/ir_module_module.py` | `_register_hook()` that triggers sync after an upgrade of any module under a configured root |
| `oteny_knowledge_sync/models/knowledge_sync.py` | Main sync service (`oteny.knowledge.sync`) |
| `oteny_knowledge_sync/models/knowledge_article.py` | Extends `knowledge.article` with tracking fields (`x_skill_root` per Knowledge tree) |
| `oteny_knowledge_sync/tools/markdown_utils.py` | Markdown parsing and HTML conversion |
| a consuming business's own `mermaid_viewer_patch.js` | Client-side mermaid.js rendering for `<pre class="mermaid">` blocks |

### Model Extensions

The `knowledge.article` model is extended with:

```python
x_skill_file_path = fields.Char(
    string="Skill File Path",
    help="Relative path to source markdown file",
    index=True,
)
x_skill_is_managed = fields.Boolean(
    string="Managed by Skill Sync",
    help="True if article is managed by skill sync",
    index=True,
    default=False,
)
```

### Dependencies

In `oteny_knowledge_sync/__manifest__.py` (live version `19.0.2.0`):

```python
"depends": ["base", "mail", "knowledge"],
"external_dependencies": {"python": ["mistune", "pyyaml"]},
```

A consuming business's own main module depends on `oteny_knowledge_sync`. Do not re-add the sync models to that module.

## Configuration

### Skills Directory Location

Each configured Knowledge root has its own `.claude/skills/` tree. This repo
(otenydoo) is the generic root; each business repository that vendors it as a
submodule is a further root. Roots come from the setting
`oteny_knowledge_sync.roots` (one `Label=path` per line). With no setting,
every addons-path entry whose parent folder holds `.claude/skills` is a root.

```
<business repo>/
├── .claude/skills/       # ← business root
├── <business module>/
└── ...

~/oteny/otenydoo/       # ← sibling clone on the addons path
└── .claude/skills/       # ← generic root (default discovery)
```

A business article may still write `../riverflow/SKILL.md`. The rewrite looks
that path up in **every** configured root and warns only after every root has
published. Do not rewrite a business's markdown to `../../otenydoo/...`.
A target no root published still warns.

The other direction takes the other form. A generic skill in `otenydoo`
that names a business's own file writes the escape, for example
`../../../../<business-repo>/.claude/skills/<business-skill>/references/<file>.md`.
The sync resolves that escape when the business's root is published. It stays
silent on an Odoo that has no business root configured, where the sibling
form `../<business-skill>/...` warns instead. Odoo.sh marks a build with a
warning when `update.log` holds one, so a sibling link to a file that no
root publishes turns the build orange. On 2026-09-20 a batch of otenydoo
skill files, left behind when they moved out of a business repo, briefly
carried 13 such links: 22 warnings per sync before the fix and 0 after.

otenydoo does not itself link into any business repository — it is public,
and every business root that vendors it is private. A business's own skill
files are the ones that link up into otenydoo, never the other way.

## Troubleshooting

### Articles Not Appearing

1. **Check Knowledge module**: Must be installed (`knowledge` in depends)
2. **Check user membership**: User must be internal (`share=False`)
3. **Check article permissions**: `internal_permission` should be `'read'`

### Links Opening New Tabs

Links must have the `o_knowledge_article_link` class and `data-res_id` attribute. Check:

```sql
SELECT body FROM knowledge_article WHERE id = <article_id>;
-- Should contain: class="o_knowledge_article_link" data-res_id="..."
```

### Stale Articles

Articles from deleted files should be auto-removed. If not:

1. Check `x_skill_file_path` matches source file path
2. Verify file was actually deleted (not renamed)
3. Run sync again with module upgrade

### Unresolved Links

Warning logs like `Could not resolve link '../../other/SKILL.md'` indicate an in-tree `.md` link that didn't match any article in **any configured Knowledge root**:

- Target file doesn't exist (typo, deleted file, wrong path)
- Path format not recognized
- **Wrong skill folder name**: Odoo addons use underscores (e.g. `crewradar_cuneus_sign`), but Knowledge sync only knows paths under `.claude/skills/`. A business's Cuneus skill content might live in **`crewradar-cuneus/`**, not `crewradar-cuneus-sign/`. Cross-links must use the directory name that exists on disk (e.g. `../crewradar-cuneus/references/approximated-net-salary.md`).
- **Absolute `.claude/skills/...` href instead of a relative path**: authoring bug — write `../<skill>/SKILL.md` relative to the source file, not the workspace-relative `.claude/skills/<skill>/SKILL.md`.

A sibling name that points at a skill another configured root published (for example `../odoo-development/references/testing-guidelines.md` or `../../oteny-shortcut/SKILL.md` from a business article) is **not** a miss. The sync turns it into a Knowledge link. In all real-miss cases the href is kept as-is.

Tests: `oteny_knowledge_sync/tests/test_links.py` (`test_cross_root_sibling_name_becomes_knowledge_link`, `test_cross_root_missing_target_still_warns`) and `test_roots.py` (`test_two_root_sync_resolves_sibling_name_without_warning`).

**Intentionally external `.md` links** — those that resolve outside `.claude/skills/` and do **not** name a `skills/…` file another configured root published — are kept as-is **silently, with no warning** (`_escapes_skills_tree`). An escape that still ends in `skills/<file>` (for example `../../otenydoo/.claude/skills/riverflow/SKILL.md`) is looked up across configured roots and becomes a Knowledge link when that article exists. Other escapes stay as editor navigation. The warning is reserved for genuine authoring bugs (in-tree typos). Two silent-escape shapes qualify:

- **Cross-repo** — walks out of `.claude` entirely, e.g. `../../../../riverdeploy/cuneus-gcp-backup/backrest/README.md`. The normalized path starts with `..`.
- **A non-synced sibling folder of `.claude`** — e.g. `../../../commands/profile.md` (the real `/profile` command) from a skill's `references/`. Stored paths are relative to `.claude`, so this normalizes to a clean `commands/profile.md` with **no** leading `..`. This is why the check is not simply `startswith("..")`: that shape fell through and warned on *every* sync, against a file that exists. Tests: `test_sibling_claude_folder_link_no_warning` + `test_escapes_skills_tree_classification`.

**Links to intentionally agent-only skills** — in-tree links whose target is a skill file marked `sync_to_knowledge: false` (e.g. `cuneus-barney/SKILL.md` referenced from a business's root `.claude/skills/SKILL.MD` index, or `oteny-bot/SKILL.md` referenced from otenydoo's own root index) — are also kept as-is **silently, with no warning**. Such a file has no Knowledge article to target, so a warning would be noise: the exclusion is deliberate, not a broken link. The sync walk collects these excluded paths and passes them to the link rewriter, which suppresses the warning (logging at DEBUG) when a link resolves to one. In-tree links to files that simply don't exist still warn.

### Duplicate image attachments on every sync

**Symptom**: each embedded image gains a new `ir.attachment` on every upgrade (e.g. 3 identical copies per image after 3 upgrades).

**Cause**: **Odoo re-encodes images when it stores them** (e.g. a 271 KB PNG comes back ~290 KB), so `ir.attachment.checksum` is the sha1 of the *transformed* bytes, not of the source file. A dedupe that computes `sha1(file_bytes)` and searches by `checksum` never matches → a fresh attachment every run.

**Solution**: dedupe on a marker the sync controls — `description = "skill-img:<sha1 of the source file>"` — searched via `sudo()` (so `res_id=0` attachments on new articles aren't hidden by `ir.attachment` access rules). See `_embed_body_images`. To clear pre-existing duplicates after deploying the fix, run a fixed sync (which repoints the article to the marker attachments), then delete the marker-less image attachments (`description NOT LIKE 'skill-img:%'`).

## Roadmap

### Completed

- [x] Basic markdown-to-Knowledge sync
- [x] YAML frontmatter parsing for article names
- [x] Hierarchical article structure mirroring folders
- [x] Internal link conversion with SPA navigation
- [x] Alphabetical sorting within parents
- [x] Stale article cleanup on file deletion
- [x] Auto-add internal users to root article
- [x] Skills folder moved to `.claude/skills/` for AI integration
- [x] Auto-sync on every upgrade via `_register_hook()` (no more manual post-migrate calls)
- [x] Article titles from `#` heading (not frontmatter `name` slug)
- [x] Non-SKILL.md files parent to their directory's SKILL.md article (not grandparent)
- [x] Mermaid diagram rendering — `` ```mermaid `` blocks emit `<pre class="mermaid">`, JS patch lazy-loads mermaid.js from CDN and renders client-side
- [x] **Internal-only / agent-only skills** — `sync_to_knowledge: false` in front matter skips Knowledge for that file; root `.claude/skills/SKILL.MD` always syncs (INFO log if flagged false). See [Internal-only skills](#internal-only-skills) below.
- [x] **Silent handling of cross-repo `.md` links** — links whose normalized path escapes `.claude/skills/` (e.g. `../../../../riverdeploy/cuneus-gcp-backup/backrest/README.md`) are kept unchanged without a warning. In-tree unresolved links still warn so authoring bugs surface. Tests: `test_external_tree_link_no_warning` and `test_internal_broken_link_still_warns` in `oteny_knowledge_sync/tests/test_links.py`.
- [x] **Cross-root sibling names** — a business article may still name a skill that lives in the otenydoo knowledge root (`../riverflow/SKILL.md`, `../../oteny-shortcut/SKILL.md`). The sync looks the path up in every configured root. A healthy two-root upgrade does not log `Could not resolve link`. A target no root published still warns. The `../../otenydoo/.claude/skills/…` escape is not required. Tests: `test_cross_root_sibling_name_becomes_knowledge_link`, `test_cross_root_missing_target_still_warns`, `test_two_root_sync_resolves_sibling_name_without_warning`.
- [x] **Silent handling of links to agent-only skills** — in-tree links to a skill marked `sync_to_knowledge: false` (e.g. the index's `cuneus-barney/SKILL.md` link) are kept unchanged without a warning, since no Knowledge article exists to target. The sync walk collects excluded paths (`_sync_directory`/`_sync_file`) and passes them to `_rewrite_internal_links(excluded_paths)`, which logs at DEBUG instead of WARNING for those. Test: `test_agent_only_skill_link_no_warning` in `oteny_knowledge_sync/tests/test_links.py`.
- [x] **Upgrade-time performance** — single-pass inline link resolution (stable bodies), skip-unchanged writes (`_write_managed`), one up-front query instead of per-file N+1 search, and one disk read per file. Cut the sync from ~7.4s to ~2s on a no-change upgrade. Identified from a `preload_registries` profile (`sync_skills_to_knowledge` was ~28% of the whole update). Regression tests: `test_rewrite_internal_links_is_idempotent` and `test_write_managed_skips_unchanged_and_writes_only_diffs` in `oteny_knowledge_sync/tests/test_idempotent.py`.
- [x] **Image embedding** — local `![](img/…)` images are uploaded as `ir.attachment`s and `<img src>` rewritten to `/web/image/<id>` (with `img-fluid`), so screenshots in skill docs render in Knowledge. Deduped by a content marker in `description` (`skill-img:<sha1 of the source file>`), **not** `ir.attachment.checksum` (Odoo re-encodes images so its checksum ≠ sha1 of the source). Tests: `oteny_knowledge_sync/tests/test_images.py`.
- [x] **XML-RPC return is a dict** — `oteny.knowledge.sync.sync_skills_to_knowledge` always returns `{"articles_synced": N}` (or a skip reason). First shipped on `crewradar` **19.0.10.9**. Odoo 19 XML-RPC dumps with `allow_none=False`, so an implicit `None` used to raise after a successful sync. Tests: `oteny_knowledge_sync/tests/test_return.py`.

### Pending

- [ ] **Link-rewriting regex should skip `<pre>`/`<code>` content** — it currently matches `href="…md"` inside fenced code blocks (because `html_sanitize` un-escapes the `&quot;` in code text), corrupting example links in docs like this one and causing that one article to be rewritten on every run.

### Future Ideas

- [ ] **Skip parsing of unchanged files** — writes are already skipped when unchanged, but every file is still re-parsed by mistune each run (the residual ~2s) to detect changes. Storing a per-file content hash (e.g. on `knowledge.article`) would let unchanged files skip parsing entirely. Requires a new field + migration.
- [ ] Sync status dashboard in Odoo
- [ ] Preview before publish
- [ ] Conflict detection if article manually edited

### Internal-only skills

Some skills (e.g. a business's Basecamp CLI notes) are for developers and agents only. Set in YAML:

```yaml
---
name: basecamp
sync_to_knowledge: false
---
```

- **Default:** omitted or any value other than YAML `false` → file is synced to Knowledge.
- **Skipped files** are not added to `processed_paths`, so a previously synced article for that path is removed on the next run (same as deleting the file).
- **Root index** `.claude/skills/SKILL.MD` is always published; if it ever sets `sync_to_knowledge: false`, sync logs at INFO and still updates the root skills article. The same file is not re-synced as a child of itself (`_sync_directory` skips it).
- **Link rewrite:** Skipped paths are absent from the article map, so links to them stay as raw `.md` hrefs. Because the walk also tracks these excluded paths, a link to an agent-only skill (e.g. otenydoo's own `oteny-bot/SKILL.md`) is kept as-is **without a warning** — it is a deliberate exclusion, not a broken link.
