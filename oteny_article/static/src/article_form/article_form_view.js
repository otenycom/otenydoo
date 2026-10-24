import { onPatched, useState, useSubEnv } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { closestScrollableY } from "@web/core/utils/scrolling";
import { usePager } from "@web/search/pager_hook";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";
import { ArticleSidebar } from "./article_sidebar";

/**
 * The article form with the tree of contents on the left. An article that the
 * tree opens loads in place, as the pager of a form does: the form saves its
 * changes first, the URL follows the open article, and the page shows the top
 * of the article. In a dialog (an article opened from a many2one) the form has
 * no left column. On a phone the left column is a drawer: a button in the
 * control panel opens it, and it closes when an article opens or on a tap
 * beside it.
 */
export class ArticleFormController extends FormController {
    static template = "oteny_article.ArticleFormView";
    static components = { ...FormController.components, ArticleSidebar };

    setup() {
        super.setup();
        // A save can change a title or the parent: the tree then reloads.
        // `open` shows the drawer on a phone.
        this.sidebar = useState({ version: 0, open: false });
        // The tree replaces the record pager ("1 / 1" with arrows).
        usePager(() => null);
        // The page has its own reading width and no chatter, so the wide or
        // narrow toggle that a module may put on every form sheet (for
        // example oteny_shortcut, which keys it on env.formWide) has no use.
        useSubEnv({ formWide: undefined });
        // The form keeps the scroll position when it loads another record, as
        // its pager does, so the page scrolls up once it shows the article
        // that the tree opened. Only then: on a breadcrumb back, Odoo restores
        // the scroll position that it saved.
        this.articleToTop = null;
        onPatched(() => {
            if (this.articleToTop === this.model.root.resId) {
                this.articleToTop = null;
                const page = closestScrollableY(this.rootRef.el.querySelector(".o_form_sheet"));
                if (page) {
                    page.scrollTop = 0;
                }
            }
        });
    }

    get className() {
        return {
            ...super.className,
            o_oteny_article_form: !this.env.inDialog,
            o_oteny_article_drawer_open: this.sidebar.open,
        };
    }

    async onRecordSaved(record, changes) {
        await super.onRecordSaved(...arguments);
        this.sidebar.version++;
    }

    toggleSidebar(open = !this.sidebar.open) {
        this.sidebar.open = open;
    }

    async openArticle(resId) {
        this.toggleSidebar(false);
        const root = this.model.root;
        if (resId === root.resId) {
            return;
        }
        if ((await root.isDirty()) && !(await root.save())) {
            return;
        }
        this.articleToTop = resId;
        await this.model.load({ resId, resIds: [resId] });
    }
}

export const articleFormView = {
    ...formView,
    Controller: ArticleFormController,
};

registry.category("views").add("oteny_article_form", articleFormView);
