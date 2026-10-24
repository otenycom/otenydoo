---
name: oteny-article
description: Generic Odoo 19 Community module oteny_article, a tree of HTML articles that replaces the Enterprise Knowledge app for prompts, rules, manuals and synced skills. Covers the Articles screen (a left column with a search box and the tree of contents, a reading page, the menu that opens the first article), locked articles, the order of the top-level articles, the install hook that takes over Knowledge articles (workspace and shared only, never private pages or gallery templates), the files and the tests. Use when a user cannot find or open an article, when you change the article form, or when you move a database from Knowledge to oteny_article.
---

# Articles (oteny_article)

The module holds the articles that the Enterprise Knowledge app held: the
parsing prompts, the rules of an AI assistant, a user manual, and the skills
that `oteny_knowledge_sync` publishes from git. It runs on Odoo Community.

## When to Use This Skill

- **Key users**: find an article, read it, edit an article that is not locked.
- **AI agents**: link an article, explain why an article is read-only.
- **Developers**: change the article screen, move a database from Knowledge.

## The Articles Screen

The Articles menu opens the top-level article with the lowest sequence. The
order of the top-level articles is their sequence; set it in the list (All
articles) with the drag handle. A business puts its manual first, so that the
menu opens on it.

The screen has two parts:

- **The left column**: a search box and the tree of contents. The tree opens
  the branch of the open article; a chevron opens or closes any other branch.
  A click opens the article in the same screen. When the open article has
  unsaved changes, the screen saves them first; when the save fails, the
  article stays open. The search box shows the articles whose title holds the
  text at once, and then the articles whose body holds it, each with its
  place in the tree. Enter opens the first match; Escape clears the search.
  "All articles" at the foot of the column opens the list.
- **The page**: the parent article, the emoji and the title, one line of
  facts (read-only or the date of the last change), the body, and the list of
  child articles when the article has any. The page has no record pager and
  no wide or narrow toggle: the tree replaces the pager, and the page has its
  own reading width and no chatter.

Every link `/odoo/action-oteny_article.action_oteny_article/<id>` opens the
same screen with the tree. A link of the former Knowledge app,
`/knowledge/article/<id>`, redirects to it (same id). In a dialog, for
example an article opened from a many2one, the form shows no left column.
From 768 px wide the left column stands beside the page. On a phone it is a
drawer: the button with the panel icon, beside the cog menu, slides it in over
the page; a tap on an article or beside the drawer closes it. The drawer keeps
its open branches and its search text.

**Locked articles.** Code or module data owns them, for example the synced
skills. The page shows them read-only with the line "Read-only: code or
module data owns this article". An administrator sees the Locked switch.

## The Move From Knowledge

When `oteny_article` is installed on a database that still has
`knowledge_article`, the install hook (`hooks.py`) copies each **workspace and
shared** article with the same id, moves the xmlids of the modules that ship
articles and the attachments of the copied articles, and computes the parent
paths. It does not copy:

- a **private** article: the page of one user that nobody else sees, for
  example the "Welcome <user>" page that Knowledge makes for every new user;
- a gallery template of Knowledge (`is_template`).

The uninstall of Knowledge deletes what stays behind. A private page that a
user moved into the workspace is a workspace article and is copied.

## Technical Reference

### Key Files

- `models/oteny_article.py`: the model; `action_open_articles()` returns the
  form action of the first top-level article (the list when there is none).
- `views/oteny_article_views.xml`: list, form (`js_class="oteny_article_form"`),
  search, the window action `action_oteny_article`, the server action
  `action_oteny_article_home` of the menu.
- `static/src/article_form/article_form_view.js`: `ArticleFormController`.
  `openArticle(resId)` saves a dirty record, then calls
  `model.load({ resId, resIds: [resId] })`, so the URL follows. It replaces the
  pager with `usePager(() => null)` and sets `env.formWide` to `undefined`
  for its children, which hides the wide or narrow toggle of
  [oteny-shortcut](../oteny-shortcut/references/form-wide-toggle.md). A save
  bumps `sidebar.version`, and the column reloads the tree. `sidebar.open`
  shows the drawer on a phone; `toggleSidebar()` opens or closes it, and
  `openArticle()` closes it first.
- `static/src/article_form/article_sidebar.js`: `ArticleSidebar` and
  `buildArticleTree()`. The tree is one `search_read` of all articles; the body
  search is a debounced `search` with `body ilike` (limit 50).
- `static/src/article_form/article_form.scss`: colours from Odoo and Bootstrap
  variables only, so the dark bundle needs no `.dark.scss`. The row needs four
  classes, `.o_web_client > .o_action_manager > .o_form_view.o_oteny_article_form`,
  because Odoo's column rule for every action outranks three. Below 768 px the
  column is absolute and moved out with `translateX(-100%)`, above
  `.o_oteny_article_backdrop`.
- `hooks.py`: the take-over from Knowledge.

### Testing

- Python: `--test-tags=oteny_article` (the tree, the rights, no chatter, the
  menu, the Knowledge redirect).
- Hoot: the suite `@oteny_article`: `article_form.test.js` (desktop: the tree,
  a click with a save, the search, the pager, the column beside the page below
  the XXL size) and `article_form_mobile.test.js` (mobile: the drawer opens and
  closes, New stays visible). The mobile file runs only at a phone viewport:
  radar's `TestHootSuitesMobile`, or `--test-tags
  "/web:MobileWebSuite.test_unit_mobile[@oteny_article/article_form_mobile]"`.
  A business repository that runs hoot suites from the command line must list
  `@oteny_article`.
- The install hook has no unit test: it runs once per database, so it is
  proven on a copy of a production backup that still has Knowledge.

## Roadmap

**Status key:** `[x]` done · `[~]` in progress · `[ ]` not started

### Future Ideas

- [x] The left column on a phone: a drawer, as in Odoo Knowledge (built
  24-Oct-2026).
- [ ] Create a child article from the tree (today: the child list on the page,
  or New and the parent field).
