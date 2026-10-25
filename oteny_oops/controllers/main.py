from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.http import request

# The form of a record; the error dialog shows it as a link to copy.
RECORD_URL = "/odoo/action-oteny_oops.action_oteny_oops_log/{}"


class OopsLogController(http.Controller):
    # A record holds at most 1 MiB of text. JSON escapes every line break and quote, so
    # the request for a full record is a little larger. The server does not read a
    # request above 2 MiB: it answers with the error RequestEntityTooLarge.
    @http.route("/oteny_oops/log", type="jsonrpc", auth="user", methods=["POST"], max_content_length=2 << 20)
    def oteny_oops_log(self, exception_name="", message="", traceback="", error_type="", model="", url=""):
        """Write the fault of the user's error dialog to the Oops Log; return the
        id of the record and its link. Only an internal user sends errors."""
        if not request.env.user._is_internal():
            raise Forbidden()
        record = (
            request.env["oteny.oops.log"]
            .sudo()
            ._log_error(
                {
                    "exception_name": exception_name,
                    "message": message,
                    "traceback": traceback,
                    "error_type": error_type,
                    "model": model,
                    "url": url,
                    "user_id": request.env.uid,
                    "company_id": request.env.company.id,
                    "user_agent": request.httprequest.headers.get("User-Agent", ""),
                }
            )
        )
        return {"id": record.id, "url": RECORD_URL.format(record.id)}
