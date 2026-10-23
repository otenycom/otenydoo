import { useState, useSubEnv } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { usePager } from "@web/search/pager_hook";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";
import { ArticleSidebar } from "./article_sidebar";

/**
 * The article form with the tree of contents on the left. An article that the
 * tree opens loads in place, as the pager of a form does: the form saves its
 * changes first, and the URL follows the open article. In a dialog (an article
 * opened from a many2one) the form has no left column.
 */
export class ArticleFormController extends FormController {
    static template = "oteny_article.ArticleFormView";
    static components = { ...FormController.components, ArticleSidebar };

    setup() {
        super.setup();
        // A save can change a title or the parent: the tree then reloads.
        this.sidebar = useState({ version: 0 });
        // The tree replaces the record pager ("1 / 1" with arrows).
        usePager(() => null);
        // The page has its own reading width and no chatter, so the wide or
        // narrow toggle that a module may put on every form sheet (for
        // example oteny_shortcut, which keys it on env.formWide) has no use.
        useSubEnv({ formWide: undefined });
    }

    get className() {
        return { ...super.className, o_oteny_article_form: !this.env.inDialog };
    }

    async onRecordSaved(record, changes) {
        await super.onRecordSaved(...arguments);
        this.sidebar.version++;
    }

    async openArticle(resId) {
        const root = this.model.root;
        if (resId === root.resId) {
            return;
        }
        if ((await root.isDirty()) && !(await root.save())) {
            return;
        }
        await this.model.load({ resId, resIds: [resId] });
    }
}

export const articleFormView = {
    ...formView,
    Controller: ArticleFormController,
};

registry.category("views").add("oteny_article_form", articleFormView);
