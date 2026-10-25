import hashlib
import re
from datetime import timedelta

from odoo import api, fields, models

# One record holds at most this many bytes of text, counted in UTF-8 over all its text
# fields: 1 MiB, the limit of the Enterprise Documents route that saved the same text as
# a file.
MAX_RECORD_BYTES = 1 << 20
# The short fields are cut to these lengths in characters; the message and the traceback
# share the rest of the record limit.
MAX_TITLE_CHARS = 200
MAX_URL_CHARS = 2000
CUT_LINE = "\n[cut: an Oops Log record holds at most 1 MB]"
# The same user who meets the same fault again within this time counts on the record that
# is already there, so an error that opens the dialog in a loop gives one record.
REPEAT_MINUTES = 10
DEFAULT_MAX_AGE_DAYS = 30

# A Python frame: File "<file>", line <number>, in <function>.
PYTHON_FRAME = re.compile(r'^\s*File "([^"]+)", line \d+, in (\S+)', re.MULTILINE)
# A JavaScript frame with a function name: "at <function> (<url>:<line>:<column>)".
JS_FRAME = re.compile(r"^\s*at (\S+) \(", re.MULTILINE)


def _cut(text, limit):
    """``text`` in at most ``limit`` UTF-8 bytes: unchanged when it fits, else its
    start and the cut line."""
    data = text.encode()
    if len(data) <= limit:
        return text
    keep = max(limit - len(CUT_LINE.encode()), 0)
    # A cut in the middle of a character leaves a partial byte sequence; it is dropped.
    return data[:keep].decode(errors="ignore") + CUT_LINE


class OtenyOopsLog(models.Model):
    """One fault that a user met in the web client: the text of the error dialog,
    with the user, the page and the time.

    The browser sends it to /oteny_oops/log when the error dialog opens. It sends
    it in a new request, because the request that failed has rolled back its
    transaction. Only Settings administrators read the records: a traceback can
    hold personal data.
    """

    _name = "oteny.oops.log"
    _description = "Oops Log"
    _order = "id desc"

    # The text comes from the dialog; nobody edits it by hand. The administrator only
    # archives a record when its fault is handled.
    name = fields.Char(required=True, readonly=True, help="The exception and the first line of its message.")
    error_type = fields.Selection(
        [("server", "Server"), ("client", "Client"), ("network", "Network")],
        string="Type",
        required=True,
        readonly=True,
        default="client",
        help="Server: the server failed. Client: the code in the browser failed. "
        "Network: the browser could not load a script or send a request.",
    )
    exception_name = fields.Char(string="Exception", readonly=True)
    message = fields.Text(readonly=True)
    traceback = fields.Text(
        readonly=True,
        help="The text of the error dialog: the server traceback first, then the client traceback.",
    )
    model = fields.Char(readonly=True, help="The model of the call that failed.")
    url = fields.Char(string="URL", readonly=True, help="The page that the user had open.")
    user_id = fields.Many2one("res.users", string="User", readonly=True, index=True)
    company_id = fields.Many2one("res.company", string="Company", readonly=True)
    user_agent = fields.Char(string="Browser", readonly=True)
    fingerprint = fields.Char(
        readonly=True,
        index=True,
        help="The same for every record of one fault, so the list can group the users who met it.",
    )
    occurrence_count = fields.Integer(
        string="Count",
        readonly=True,
        default=1,
        help="How often this user met the fault, with less than 10 minutes between two times.",
    )
    last_date = fields.Datetime(
        string="Last Seen",
        required=True,
        readonly=True,
        index=True,
        default=fields.Datetime.now,
        help="The last time this user met the fault. The daily clean-up counts the maximum age from here.",
    )
    active = fields.Boolean(default=True)

    @api.model
    def _fingerprint(self, exception_name, message, traceback):
        """The same for every record of one fault: the SHA-1 of the exception name
        and the frames of the traceback, without line numbers and asset URLs,
        because a deploy changes them.

        When the traceback has Python frames, only those count: the client part of
        a server error is the same call machinery for every server fault, and it
        differs from screen to screen. Without any frame, the first line of the
        message stands in.
        """
        frames = [f"{path} {function}" for path, function in PYTHON_FRAME.findall(traceback)]
        if not frames:
            frames = JS_FRAME.findall(traceback)
        if not frames:
            frames = message.strip().splitlines()[:1]
        return hashlib.sha1("\n".join([exception_name, *frames]).encode()).hexdigest()

    @api.model
    def _log_error(self, values):
        """Write a fault to the Oops Log and return its record.

        ``values`` holds the text of the dialog (``exception_name``, ``message``,
        ``traceback``, ``error_type``, ``model``, ``url``) and the ``user_id``,
        ``company_id`` and ``user_agent`` of the request. When the same user met
        the same fault in the last REPEAT_MINUTES, the record that is there counts
        one more instead. A new record holds at most MAX_RECORD_BYTES of text: the
        traceback is cut first, then the message, and each keeps its start.
        """
        exception_name = (values.get("exception_name") or "Error")[:MAX_TITLE_CHARS]
        message = values.get("message") or ""
        traceback = values.get("traceback") or ""
        fingerprint = self._fingerprint(exception_name, message, traceback)
        now = fields.Datetime.now()
        repeat = self.search(
            [
                ("user_id", "=", values.get("user_id")),
                ("fingerprint", "=", fingerprint),
                ("last_date", ">=", now - timedelta(minutes=REPEAT_MINUTES)),
            ],
            limit=1,
        )
        if repeat:
            repeat.write({"occurrence_count": repeat.occurrence_count + 1, "last_date": now})
            return repeat

        first_line = message.strip().splitlines()[0] if message.strip() else ""
        error_type = values.get("error_type")
        texts = {
            "name": (f"{exception_name}: {first_line}" if first_line else exception_name)[:MAX_TITLE_CHARS],
            "exception_name": exception_name,
            "error_type": error_type if error_type in dict(self._fields["error_type"].selection) else "client",
            "model": (values.get("model") or "")[:MAX_TITLE_CHARS],
            "url": (values.get("url") or "")[:MAX_URL_CHARS],
            "user_agent": (values.get("user_agent") or "")[:MAX_URL_CHARS],
            "fingerprint": fingerprint,
        }
        budget = MAX_RECORD_BYTES - sum(len(text.encode()) for text in texts.values())
        # The traceback gives way first. When it does not fit beside the message, at
        # least its cut line stays, and the message gets the rest.
        texts["traceback"] = _cut(traceback, max(budget - len(message.encode()), len(CUT_LINE.encode())))
        texts["message"] = _cut(message, budget - len(texts["traceback"].encode()))
        return self.create(
            {
                **{field: text or False for field, text in texts.items()},
                "user_id": values.get("user_id"),
                "company_id": values.get("company_id"),
                "last_date": now,
            }
        )

    @api.autovacuum
    def _gc_old_records(self):
        """Delete the records whose fault nobody met for oteny_oops.max_age_days
        days, archived records too. The daily auto-vacuum of Odoo runs it."""
        max_age_days = int(
            self.env["ir.config_parameter"].sudo().get_param("oteny_oops.max_age_days", DEFAULT_MAX_AGE_DAYS)
        )
        oldest = fields.Datetime.now() - timedelta(days=max_age_days)
        self.with_context(active_test=False).search([("last_date", "<", oldest)]).unlink()
