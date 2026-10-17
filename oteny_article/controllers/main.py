from odoo import http
from odoo.http import request


class OtenyArticleController(http.Controller):
    @http.route("/knowledge/article/<int:article_id>", type="http", auth="user")
    def knowledge_article_redirect(self, article_id, **kwargs):
        """Keep the links of the former Knowledge app working.

        The move to Community copies every Knowledge article into oteny.article
        with the same id, so a link that Basecamp or an old article body holds
        (/knowledge/article/<id>) opens the same article here.
        """
        return request.redirect(f"/odoo/action-oteny_article.action_oteny_article/{article_id}")
