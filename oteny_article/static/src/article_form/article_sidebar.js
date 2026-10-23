import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";

/**
 * The articles as a tree, in the order of the server (sequence, id). An
 * article whose parent is not in the list (archived) shows at the top level.
 */
export function buildArticleTree(articles) {
    const byId = new Map(articles.map((article) => [article.id, { ...article, children: [] }]));
    const roots = [];
    for (const node of byId.values()) {
        const parent = node.parent_id && byId.get(node.parent_id[0]);
        (parent ? parent.children : roots).push(node);
    }
    return { roots, byId };
}

/**
 * The left column of the article form: a search box and the tree of contents.
 *
 * A click on an article asks the form to open it in place. The tree opens the
 * branch of the open article; the other branches open with their chevron.
 * The search matches the title at once and the body on the server; while it
 * has text, the column shows the matches as a flat list with their place in
 * the tree.
 */
export class ArticleSidebar extends Component {
    static template = "oteny_article.ArticleSidebar";
    static props = {
        activeId: { type: [Number, { value: false }] },
        version: Number,
        openArticle: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            roots: [],
            expanded: {},
            search: "",
            bodyMatchIds: [],
        });
        this.byId = new Map();
        this.searchBodies = useDebounced(this.searchBodies, 250);
        onWillStart(() => this.loadTree(this.props));
        onWillUpdateProps((nextProps) => {
            if (nextProps.activeId !== this.props.activeId || nextProps.version !== this.props.version) {
                return this.loadTree(nextProps);
            }
        });
    }

    async loadTree({ activeId }) {
        const articles = await this.orm.searchRead("oteny.article", [], ["name", "icon", "parent_id"]);
        const { roots, byId } = buildArticleTree(articles);
        this.byId = byId;
        this.state.roots = roots;
        this.expandTo(activeId);
    }

    /** Open every ancestor of the article, and the article itself. */
    expandTo(articleId) {
        let node = this.byId.get(articleId);
        while (node) {
            this.state.expanded[node.id] = true;
            node = node.parent_id && this.byId.get(node.parent_id[0]);
        }
    }

    toggle(node) {
        this.state.expanded[node.id] = !this.state.expanded[node.id];
    }

    open(node) {
        if (node.children.length) {
            this.state.expanded[node.id] = true;
        }
        this.props.openArticle(node.id);
    }

    /** The titles of the parents of an article, top first. */
    pathOf(node) {
        const names = [];
        let parent = node.parent_id && this.byId.get(node.parent_id[0]);
        while (parent) {
            names.unshift(parent.name);
            parent = parent.parent_id && this.byId.get(parent.parent_id[0]);
        }
        return names.join(" › ");
    }

    /** The title matches first, then the body matches, each in tree order. */
    get searchResults() {
        const search = this.state.search.trim().toLowerCase();
        const nodes = [...this.byId.values()];
        const titleMatches = nodes.filter((node) => node.name.toLowerCase().includes(search));
        const bodyMatchIds = new Set(this.state.bodyMatchIds);
        const bodyMatches = nodes.filter((node) => bodyMatchIds.has(node.id) && !titleMatches.includes(node));
        return [...titleMatches, ...bodyMatches];
    }

    onSearchInput(ev) {
        this.state.search = ev.target.value;
        this.state.bodyMatchIds = [];
        if (this.state.search.trim()) {
            this.searchBodies();
        }
    }

    async searchBodies() {
        const search = this.state.search.trim();
        if (!search) {
            return;
        }
        const ids = await this.orm.search("oteny.article", [["body", "ilike", search]], { limit: 50 });
        if (this.state.search.trim() === search) {
            this.state.bodyMatchIds = ids;
        }
    }

    onSearchKeydown(ev) {
        if (ev.key === "Enter") {
            const [first] = this.searchResults;
            if (first) {
                this.openFromSearch(first);
            }
        } else if (ev.key === "Escape") {
            this.clearSearch();
        }
    }

    openFromSearch(node) {
        this.clearSearch();
        this.expandTo(node.id);
        this.props.openArticle(node.id);
    }

    clearSearch() {
        this.state.search = "";
        this.state.bodyMatchIds = [];
    }

    openList() {
        return this.action.doAction("oteny_article.action_oteny_article");
    }
}
