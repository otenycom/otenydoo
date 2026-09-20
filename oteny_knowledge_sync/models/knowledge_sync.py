"""Publish agent skills as Knowledge articles, one tree per configured root.

Walks ``<root>/.claude/skills/`` for every configured root and creates or updates
locked Knowledge articles, preserving the folder hierarchy under one top-level
article per root (``<Label> Skills``). A file may set ``sync_to_knowledge: false``
in its YAML front matter to stay agent-only; on a folder's ``SKILL.md`` that
excludes the whole subtree, so a reference never outlives the skill that gives it
context. The root index ``.claude/skills/SKILL.MD`` always syncs.

Roots come from the setting ``oteny_knowledge_sync.roots`` (one ``Label=path``
per line); with no setting, every addons-path entry whose parent folder holds
``.claude/skills`` is a root, labelled by that folder's name. A root that leaves
the list has its tree unpublished on the next sync.

The sync runs after every upgrade of a module under a root, so it does as little
as possible when nothing changed: internal ``.md`` links are resolved to
Knowledge URLs inline while each body is written, and :meth:`_write_managed`
writes only fields that differ.

A CrewRadar article may still name a sibling skill (``riverflow/SKILL.md``,
``../odoo-development/references/…``) after that skill moved to another
configured knowledge root. The rewrite looks up the path in every configured
root, same knowledge root first, then the others. A truly missing target still
warns. An author does not need to rewrite the href to
``../../otenydoo/.claude/skills/…``.
"""

import base64
import hashlib
import logging
import mimetypes
import posixpath
import re
from pathlib import Path

from odoo import api, models, _
from odoo.tools import config

from ..tools import markdown_utils

_logger = logging.getLogger(__name__)


class KnowledgeSync(models.AbstractModel):
    """Service to sync skill markdown files to Knowledge articles."""

    _name = "oteny.knowledge.sync"
    _description = "Knowledge Sync of Agent Skills"

    _MD_LINK_RE = re.compile(r'href="([^"]*\.md)"')
    _IMG_TAG_RE = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"[^>]*>')
    _SKILLS_TREE_PREFIX = "skills/"

    # ------------------------------------------------------------------ roots

    @api.model
    def _skill_roots(self):
        """``[(label, path)]`` of the configured roots, or the addons-path default.

        The setting holds one root per line as ``Label=path`` (``path`` alone
        labels the root by its folder name). A relative path is resolved against
        every addons-path entry's parent. Roots without a ``.claude/skills`` folder
        are logged and skipped.
        """
        raw = (self.env["ir.config_parameter"].sudo().get_param("oteny_knowledge_sync.roots") or "").strip()
        raw_addons = config.get("addons_path") or ""
        if isinstance(raw_addons, str):
            raw_addons = raw_addons.split(",")
        addons = [Path(str(p)).expanduser() for p in raw_addons if str(p).strip()]
        roots = []
        if raw:
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                label, sep, path = line.partition("=")
                if not sep:
                    label, path = "", line
                path = Path(path.strip()).expanduser()
                if not path.is_absolute():
                    for entry in addons:
                        candidate = (entry.parent / path).resolve()
                        if (candidate / ".claude" / "skills").is_dir():
                            path = candidate
                            break
                label = (label.strip() or path.name)
                roots.append((label, path))
        else:
            # An addons-path entry is a repository root: its direct children are the
            # modules, and its ``.claude/skills`` is the skills tree.
            seen = set()
            for entry in addons:
                entry = entry.resolve()
                if entry in seen:
                    continue
                seen.add(entry)
                if (entry / ".claude" / "skills").is_dir():
                    roots.append((entry.name, entry))
        out = []
        for label, path in roots:
            if (Path(path) / ".claude" / "skills").is_dir():
                out.append((label, Path(path)))
            else:
                _logger.warning("Knowledge sync root %r has no .claude/skills at %s; skipped", label, path)
        return out

    # ------------------------------------------------------------------ entry

    def sync_skills_to_knowledge(self):
        """Sync every configured root. Always returns a dict (XML-RPC needs a value)."""
        if "knowledge.article" not in self.env:
            _logger.info("Knowledge module not installed, skipping skill sync")
            return {"articles_synced": 0, "skipped": "knowledge_not_installed"}
        roots = self._skill_roots()
        if not roots:
            _logger.warning("Knowledge sync: no root with .claude/skills configured or found")
            return {"articles_synced": 0, "skipped": "no_roots"}
        total = 0
        per_root = {}
        excluded_paths = set()
        for label, path in roots:
            count, excluded = self._sync_root(label, path / ".claude" / "skills")
            excluded_paths |= excluded
            per_root[label] = count
            total += count
        self._unpublish_roots_not_in([label for label, _p in roots])
        self._sort_articles_alphabetically()
        # Warn only after every configured knowledge root has published, so a
        # sibling name that lives in another root is a Knowledge link, not a miss.
        self._rewrite_internal_links(excluded_paths=excluded_paths)
        _logger.info("Knowledge sync completed: %d articles over %d root(s)", total, len(roots))
        return {"articles_synced": total, "roots": per_root}

    def _sync_root(self, label, skills_dir):
        """Sync one root's ``skills_dir`` into its own Knowledge tree.

        Returns ``(count, excluded_paths)``. The caller rewrites links after
        every configured knowledge root has published.
        """
        Article = self.env["knowledge.article"]
        self._adopt_legacy_articles(label, skills_dir)
        managed = Article.search([("x_skill_is_managed", "=", True), ("x_skill_root", "=", label)])
        existing_by_path = {}
        for art in managed:
            if art.x_skill_file_path:
                existing_by_path.setdefault(art.x_skill_file_path, art)
        all_managed = Article.search([("x_skill_is_managed", "=", True)])
        maps_by_root = self._link_maps_by_knowledge_root(all_managed)
        link_map = self._configured_link_map(label, maps_by_root)
        processed_paths = set()
        excluded_paths = set()
        root_article = self._ensure_root_article(label, skills_dir, processed_paths, link_map)
        self._sync_directory(
            label, skills_dir, skills_dir, root_article, processed_paths, excluded_paths,
            existing_by_path, link_map
        )
        self._cleanup_stale_articles(label, processed_paths)
        self._add_internal_users_to_root_article(root_article)
        _logger.info("Knowledge sync root %r: %d articles", label, len(processed_paths))
        return len(processed_paths), excluded_paths

    def _adopt_legacy_articles(self, label, skills_dir):
        """Managed articles without a root (written before roots existed) join the
        first root whose tree holds their source file, so they update in place and
        keep their ids and URLs instead of being deleted and recreated."""
        Article = self.env["knowledge.article"]
        legacy = Article.search([("x_skill_is_managed", "=", True), ("x_skill_root", "=", False)])
        if not legacy:
            return
        claude_dir = skills_dir.parent
        adopted = legacy.filtered(
            lambda a: a.x_skill_file_path and (claude_dir / a.x_skill_file_path).is_file())
        # The root article of the legacy tree carries the index path or no path at all.
        adopted |= legacy.filtered(lambda a: not a.parent_id and not a.x_skill_file_path)
        if adopted:
            adopted.write({"x_skill_root": label})
            _logger.info("Knowledge sync root %r adopted %d legacy article(s)", label, len(adopted))

    def _unpublish_roots_not_in(self, labels):
        """Delete the managed trees of roots that left the configuration."""
        Article = self.env["knowledge.article"]
        gone = Article.search([("x_skill_is_managed", "=", True), ("x_skill_root", "not in", labels)])
        if gone:
            _logger.info("Knowledge sync: unpublishing %d article(s) of roots %s",
                         len(gone), sorted(set(gone.mapped("x_skill_root"))))
            for article in gone.sorted(key=lambda a: len(a.parent_path or ""), reverse=True):
                article.unlink()

    # ------------------------------------------------------------- link maps

    def _build_link_map(self, articles):
        """Map skill-file paths (lowercased, with and without ``skills/``) to article ids."""
        link_map = {}
        for article in articles:
            self._add_to_link_map(link_map, article.x_skill_file_path, article.id)
        return link_map

    def _link_maps_by_knowledge_root(self, articles):
        """``{knowledge_root: link_map}`` for the given managed articles."""
        maps_by_root = {}
        for article in articles:
            knowledge_root = article.x_skill_root or ""
            link_map = maps_by_root.setdefault(knowledge_root, {})
            self._add_to_link_map(link_map, article.x_skill_file_path, article.id)
        return maps_by_root

    def _configured_link_map(self, own_knowledge_root, maps_by_root):
        """Every configured knowledge root, with ``own_knowledge_root`` last so it wins."""
        combined = {}
        for knowledge_root, link_map in maps_by_root.items():
            if knowledge_root != own_knowledge_root:
                combined.update(link_map)
        combined.update(maps_by_root.get(own_knowledge_root) or {})
        return combined

    @staticmethod
    def _add_to_link_map(link_map, path, article_id):
        if not path:
            return
        link_map[path.lower()] = article_id
        if path.startswith("skills/"):
            link_map[path[7:].lower()] = article_id

    def _rewrite_body_links(self, body, article_dir, link_map, article_name, warn=True, excluded_lower=None):
        """Rewrite relative ``.md`` hrefs in ``body`` to Knowledge article URLs.

        Resolution order: relative to the article's own folder, then the stored path
        as-is, then with a ``skills/`` prefix. ``link_map`` holds every configured
        knowledge root (same root last). A path that escapes this tree is tried as
        a ``skills/…`` tail against that map, so
        ``../../otenydoo/.claude/skills/riverflow/SKILL.md`` can become a
        Knowledge link when that skill is published. An escape with no skill tail
        stays untouched. Returns ``(body, count)``.
        """
        if not body:
            return body, 0
        rewritten = 0

        def knowledge_href(article_id):
            return (
                f'href="/knowledge/article/{article_id}" '
                f'class="o_knowledge_article_link" data-res_id="{article_id}"'
            )

        def replace_link(match):
            nonlocal rewritten
            original_href = match.group(1)
            if original_href.startswith(("http://", "https://")):
                return match.group(0)
            resolved_lower = None
            if article_dir:
                resolved = posixpath.normpath(posixpath.join(article_dir, original_href))
                if self._escapes_skills_tree(resolved):
                    suffix = self._skills_tree_suffix(resolved)
                    if suffix and suffix in link_map:
                        rewritten += 1
                        return knowledge_href(link_map[suffix])
                    return match.group(0)
                resolved_lower = resolved.lower()
                if resolved_lower in link_map:
                    rewritten += 1
                    return knowledge_href(link_map[resolved_lower])
            if original_href.lower() in link_map:
                rewritten += 1
                return knowledge_href(link_map[original_href.lower()])
            skills_path = f"skills/{original_href}".lower()
            if skills_path in link_map:
                rewritten += 1
                return knowledge_href(link_map[skills_path])
            if excluded_lower:
                candidates = {original_href.lower(), skills_path}
                if resolved_lower:
                    candidates.add(resolved_lower)
                if candidates & excluded_lower:
                    _logger.debug("Link %r in article %r points to an agent-only skill; left as-is",
                                  original_href, article_name)
                    return match.group(0)
            if warn:
                _logger.warning("Could not resolve link %r in article %r", original_href, article_name)
            return match.group(0)

        return self._MD_LINK_RE.sub(replace_link, body), rewritten

    @classmethod
    def _escapes_skills_tree(cls, resolved_path):
        """True when a resolved link lies outside the synced ``skills/`` tree."""
        norm = resolved_path.replace("\\", "/").lower()
        return norm.startswith("..") or not norm.startswith(cls._SKILLS_TREE_PREFIX)

    @classmethod
    def _skills_tree_suffix(cls, resolved_path):
        """The ``skills/…`` tail of ``resolved_path``, or ``None``.

        An href that walks into another repository still names a skill file when
        that tail is present. The rewrite then looks the tail up across configured
        knowledge roots.
        """
        norm = resolved_path.replace("\\", "/").lower()
        marker = "/" + cls._SKILLS_TREE_PREFIX
        idx = norm.find(marker)
        if idx != -1:
            return norm[idx + 1:]
        if norm.startswith(cls._SKILLS_TREE_PREFIX):
            return norm
        return None

    @staticmethod
    def _build_excluded_lookup(excluded_paths):
        excluded_lower = set()
        for path in excluded_paths or ():
            excluded_lower.add(path.lower())
            if path.startswith("skills/"):
                excluded_lower.add(path[7:].lower())
        return excluded_lower

    def _embed_body_images(self, body, md_dir, skills_root, article):
        """Upload local images referenced in ``body`` and rewrite their ``<img src>``
        to ``/web/image/<attachment_id>`` so Knowledge can serve them.

        Runs inline while the body is built (like :meth:`_rewrite_body_links`), so the
        stored body is final and byte-identical across runs. Attachments are
        de-duplicated by a **content marker** — ``skill-img:<sha1 of the source
        file>`` stored in ``description`` — so an unchanged image reuses its existing
        attachment (same URL) and a re-sync writes nothing. (We cannot key on the
        attachment's own ``checksum``: Odoo re-encodes images on store, so the stored
        bytes — and thus ``checksum`` — differ from the source file.) Orphaned
        attachments (image removed or changed) are intentionally left in place — no
        deletes.

        Args:
            body: the article body HTML.
            md_dir: absolute folder of the source markdown file (to resolve
                relative ``src`` values like ``img/foo.png``).
            skills_root: absolute boundary the resolved image must stay within.
            article: the target ``knowledge.article`` record, or ``None`` when it
                does not exist yet (new file). For ``None``, freshly created
                attachments get ``res_id=0`` and their ids are returned so the
                caller can backfill ``res_id`` once the article is created.

        Returns ``(new_body, created_attachment_ids)``.
        """
        if not body:
            return body, []

        # sudo(): the reuse-search must see every managed image attachment
        # regardless of ir.attachment access rules (e.g. res_id=0 on new articles).
        Attachment = self.env["ir.attachment"].sudo()
        try:
            root = skills_root.resolve()
        except OSError:
            root = skills_root
        created_ids = []
        article_label = article.name if article else "(new article)"

        def replace_img(match):
            tag = match.group(0)
            src = match.group(1)

            # Skip anything already absolute, embedded, or external.
            if src.startswith(("http://", "https://", "data:", "/")):
                return tag

            try:
                resolved = (md_dir / src).resolve()
            except (OSError, ValueError):
                _logger.warning("Could not resolve image '%s' in article '%s'", src, article_label)
                return tag

            # Keep resolution inside the synced tree (no path traversal).
            if not resolved.is_relative_to(root):
                _logger.warning("Image '%s' escapes the skills tree in '%s'; skipping", src, article_label)
                return tag

            if not resolved.is_file():
                _logger.warning("Image file not found for '%s' (%s) in '%s'", src, resolved, article_label)
                return tag

            data = resolved.read_bytes()

            # Reuse an existing managed image attachment with the same source
            # content, keyed on a marker we control (see docstring — Odoo re-encodes
            # images so its own checksum can't be used).
            content_key = "skill-img:%s" % hashlib.sha1(data).hexdigest()
            att = Attachment.search(
                [("description", "=", content_key), ("res_model", "=", "knowledge.article")],
                limit=1,
            )
            if not att:
                att = Attachment.create({
                    "name": resolved.name,
                    "datas": base64.b64encode(data),
                    "mimetype": mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
                    "res_model": "knowledge.article",
                    "res_id": article.id if article else 0,
                    "description": content_key,
                })
                created_ids.append(att.id)

            return tag.replace('src="%s"' % src, 'src="/web/image/%d"' % att.id, 1)

        return self._IMG_TAG_RE.sub(replace_img, body), created_ids



    # ------------------------------------------------------------ articles

    def _write_managed(self, record, values):
        """Write only the fields that differ; skip the write when nothing changed."""
        changed = {}
        for fname, new_val in values.items():
            if fname == "article_member_ids":
                continue
            field = record._fields.get(fname)
            if field is not None and field.type == "html":
                if field.convert_to_cache(new_val, record) != record[fname]:
                    changed[fname] = new_val
            elif field is not None and field.type == "many2one":
                if record[fname].id != (new_val or False):
                    changed[fname] = new_val
            elif record[fname] != new_val:
                changed[fname] = new_val
        if changed:
            record.write(changed)

    def _ensure_root_article(self, label, skills_dir, processed_paths, link_map):
        """Find or create the root ``<Label> Skills`` article of one root."""
        Article = self.env["knowledge.article"]
        root_name = f"{label} Skills"
        root_article = Article.search(
            [("x_skill_is_managed", "=", True), ("x_skill_root", "=", label), ("parent_id", "=", False)],
            limit=1,
        )
        admin_partner = self.env.ref("base.partner_admin")
        skill_md = skills_dir / "SKILL.MD"
        parsed = None
        file_path = None
        if skill_md.exists():
            parsed = markdown_utils.parse_skill_file(skill_md)
            file_path = str(skill_md.relative_to(skills_dir.parent))
            if not parsed["sync_to_knowledge"]:
                _logger.info("Ignoring sync_to_knowledge=false on %s; the root index always syncs", file_path)
            processed_paths.add(file_path)
        if root_article:
            values = {"name": root_name}
            if parsed:
                body_html, _c = self._rewrite_body_links(
                    parsed["body_html"], str(Path(file_path).parent), link_map, root_name, warn=False)
                values.update({"body": body_html, "x_skill_file_path": file_path})
            self._write_managed(root_article, values)
            if root_article.x_skill_file_path:
                processed_paths.add(root_article.x_skill_file_path)
            return root_article
        base_values = {
            "name": root_name,
            "icon": "📚",
            "is_locked": True,
            "internal_permission": "read",
            "article_member_ids": [(0, 0, {"partner_id": admin_partner.id, "permission": "write"})],
            "x_skill_is_managed": True,
            "x_skill_root": label,
        }
        if parsed:
            body_html, _c = self._rewrite_body_links(
                parsed["body_html"], str(Path(file_path).parent), link_map, root_name, warn=False)
            base_values.update({"body": body_html, "x_skill_file_path": file_path})
        else:
            base_values["body"] = f"<p>{label} documentation and skill files.</p>"
        root_article = Article.create(base_values)
        _logger.info("Created root article: %s", root_name)
        self._add_to_link_map(link_map, root_article.x_skill_file_path, root_article.id)
        return root_article

    @staticmethod
    def _is_repo_skills_root_index(relative_path):
        """True for ``.claude/skills/SKILL.MD`` only (case-insensitive filename)."""
        parts = relative_path.replace("\\", "/").split("/")
        return (len(parts) == 3 and parts[0].lower() == ".claude"
                and parts[1].lower() == "skills" and parts[2].lower() == "skill.md")

    def _effective_sync_to_knowledge(self, parsed, relative_path):
        if self._is_repo_skills_root_index(relative_path):
            return True
        return parsed["sync_to_knowledge"]

    def _sync_directory(self, label, base_dir, current_dir, parent_article, processed_paths,
                        excluded_paths, existing_by_path, link_map):
        """Recursively sync one directory; ``SKILL.md`` becomes the parent of its folder."""
        md_files = list(current_dir.glob("*.md"))
        skill_md = next((f for f in md_files if f.name.upper() == "SKILL.MD"), None)
        if skill_md:
            rel_skill = str(skill_md.relative_to(base_dir.parent))
            if self._is_repo_skills_root_index(rel_skill):
                subdir_parent = parent_article
            else:
                article = self._sync_file(
                    label, skill_md, base_dir.parent, parent_article, processed_paths,
                    excluded_paths, existing_by_path, link_map)
                if not article and rel_skill in excluded_paths:
                    self._exclude_subtree(current_dir, base_dir.parent, excluded_paths)
                    return
                subdir_parent = article if article else parent_article
        else:
            subdir_parent = parent_article
        for md_file in md_files:
            if md_file.name.upper() != "SKILL.MD":
                self._sync_file(label, md_file, base_dir.parent, subdir_parent, processed_paths,
                                excluded_paths, existing_by_path, link_map)
        for subdir in sorted(p for p in current_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            self._sync_directory(label, base_dir, subdir, subdir_parent, processed_paths,
                                 excluded_paths, existing_by_path, link_map)

    def _exclude_subtree(self, current_dir, base_path, excluded_paths):
        """Mark every markdown file under ``current_dir`` as agent-only."""
        for md_file in current_dir.rglob("*.md"):
            excluded_paths.add(str(md_file.relative_to(base_path)))

    def _sync_file(self, label, file_path, base_path, parent_article, processed_paths,
                   excluded_paths, existing_by_path, link_map):
        """Sync one markdown file to an article of root ``label``. Returns the article or None."""
        Article = self.env["knowledge.article"]
        try:
            parsed = markdown_utils.parse_skill_file(file_path)
            relative_path = str(file_path.relative_to(base_path))
            if not self._effective_sync_to_knowledge(parsed, relative_path):
                excluded_paths.add(relative_path)
                return None
            processed_paths.add(relative_path)
            existing = existing_by_path.get(relative_path)
            body_html = parsed["body_html"]
            if parsed.get("description"):
                from markupsafe import escape
                body_html = f"<p>{escape(parsed['description'])}</p>{body_html}"
            body_html, _c = self._rewrite_body_links(
                body_html, str(Path(relative_path).parent), link_map, parsed["name"], warn=False)
            body_html, new_image_attachment_ids = self._embed_body_images(
                body_html, file_path.parent, base_path, existing)
            article_name = parsed.get("heading") or parsed["name"]
            values = {
                "name": article_name,
                "body": body_html,
                "icon": parsed.get("icon", "📄"),
                "is_locked": True,
                "internal_permission": "read",
                "parent_id": parent_article.id if parent_article else False,
                "x_skill_file_path": relative_path,
                "x_skill_is_managed": True,
                "x_skill_root": label,
            }
            if existing:
                self._write_managed(existing, values)
                return existing
            admin_partner = self.env.ref("base.partner_admin")
            values["article_member_ids"] = [(0, 0, {"partner_id": admin_partner.id, "permission": "write"})]
            article = Article.create(values)
            if new_image_attachment_ids:
                self.env["ir.attachment"].browse(new_image_attachment_ids).write({"res_id": article.id})
            self._add_to_link_map(link_map, relative_path, article.id)
            return article
        except Exception as exc:  # noqa: BLE001 — one bad file never stops the sync
            _logger.error("Error syncing file %s: %s", file_path, exc)
            return None

    def _add_internal_users_to_root_article(self, root_article):
        """Every active internal user reads the root article from the Knowledge sidebar."""
        internal_users = self.env["res.users"].search([("active", "=", True), ("share", "=", False)])
        existing = set(root_article.article_member_ids.mapped("partner_id.id"))
        new_members = [
            {"article_id": root_article.id, "partner_id": user.partner_id.id, "permission": "read"}
            for user in internal_users if user.partner_id.id not in existing
        ]
        if new_members:
            self.env["knowledge.article.member"].create(new_members)

    def _sort_articles_alphabetically(self):
        """Sort every managed article alphabetically within its parent."""
        managed = self.env["knowledge.article"].search([("x_skill_is_managed", "=", True)])
        by_parent = {}
        for article in managed:
            by_parent.setdefault(article.parent_id.id if article.parent_id else False, []).append(article)
        for _parent_id, articles in by_parent.items():
            for idx, article in enumerate(sorted(articles, key=lambda a: (a.name or "").lower())):
                new_seq = (idx + 1) * 10
                if article.sequence != new_seq:
                    article.sequence = new_seq

    def _cleanup_stale_articles(self, label, processed_paths):
        """Delete this root's articles whose source files no longer exist."""
        managed = self.env["knowledge.article"].search(
            [("x_skill_is_managed", "=", True), ("x_skill_root", "=", label)])
        stale = managed.filtered(lambda a: a.x_skill_file_path and a.x_skill_file_path not in processed_paths)
        if stale:
            _logger.info("Deleting %d stale article(s) of root %r", len(stale), label)
            for article in stale.sorted(key=lambda a: len(a.parent_path or ""), reverse=True):
                article.unlink()

    def _rewrite_internal_links(self, label=None, excluded_paths=None, warn=True):
        """Resolve ``.md`` links using every managed article, not only one root.

        When ``label`` is set, only that knowledge root's articles are rewritten.
        Their hrefs may target any configured knowledge root. When ``label`` is
        omitted, every managed article is rewritten. Same-root paths win when two
        roots publish the same relative skill path.
        """
        excluded_lower = self._build_excluded_lookup(excluded_paths)
        all_managed = self.env["knowledge.article"].search([("x_skill_is_managed", "=", True)])
        maps_by_root = self._link_maps_by_knowledge_root(all_managed)
        targets = all_managed.filtered(lambda a: a.x_skill_root == label) if label else all_managed
        _logger.debug(
            "Knowledge sync: resolving links in %d article(s) using %d configured root(s)",
            len(targets), len(maps_by_root),
        )
        by_root_targets = {}
        for article in targets:
            knowledge_root = article.x_skill_root or ""
            by_root_targets.setdefault(knowledge_root, self.env["knowledge.article"])
            by_root_targets[knowledge_root] |= article
        for knowledge_root, articles in by_root_targets.items():
            link_map = self._configured_link_map(knowledge_root, maps_by_root)
            rewritten_count = 0
            articles_updated = 0
            for article in articles:
                if not article.body:
                    continue
                article_dir = str(Path(article.x_skill_file_path).parent) if article.x_skill_file_path else ""
                new_body, count = self._rewrite_body_links(
                    article.body, article_dir, link_map, article.name,
                    warn=warn, excluded_lower=excluded_lower)
                rewritten_count += count
                if new_body != article.body:
                    article.body = new_body
                    articles_updated += 1
            _logger.info("Knowledge sync root %r: rewrote %d link(s) in %d article(s)",
                         knowledge_root, rewritten_count, articles_updated)
