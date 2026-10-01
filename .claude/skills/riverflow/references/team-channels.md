# Team Channels — copies of chatter messages

A **team** (`riverflow.team`) has a Discuss channel (`discuss_channel_id`).
Riverflow posts a **copy** of some chatter messages into a team channel, so a
message that one person could miss is also visible to the whole team. The
original message stays on the record's chatter, and Odoo's normal delivery
(email or Inbox) does not change. The copy is extra.

Two rules decide which channel gets a copy. Both are built; the colleague
message copy since `riverflow` 19.0.1.1275.

| Rule | Message | Channel that gets the copy |
|------|---------|----------------------------|
| **External message copy** (built) | An outside sender (not a staff user) wrote it, e.g. a supplier's email reply | The channel of the record's **Responsible** team (`responsible_team_id`) |
| **Colleague message copy** (built) | A staff user wrote it, and a staff member of **another** team is a recipient | The channel of each **recipient's home team** |

The Responsible-team change notice ("Assigned to: …") also posts into the new
Responsible team's channel. It is not a message copy.

## Why the colleague message copy exists

Staff of two offices of one business address each other **personally** in
Odoo: "Hi Ladies, can you book the B&B for …". Staff users there have the
notification setting "Handle by Emails", so Odoo delivers such a message only
to the one person's mailbox. Nobody else sees it. When that person is away or
overlooks the email, the request is lost. The sender then has to ask again
("did below email ever reach you?") or ask someone else to help.

The external message copy does not help here. It skips every message that a
staff user wrote, on purpose, because it exists to surface outside mail.

The motivating case and its numbers are kept in the consuming business's own
skill bundle.

## Terms

- **Staff user** — an internal user (`res.users` with `share=False`).
- **Home team** — the one team a staff user belongs to for message routing.
  A staff user has at most one home team. A staff user without a home team
  receives no colleague message copies.
- **Members** — the staff users whose home team is this team. The team form
  lists them.
- **Recipient** — a partner that the message addresses: the "To" list of the
  message and every @mention (`mail.message.partner_ids`). A team contact (the
  `res.partner` of a team) can also be a recipient. A follower that was not
  addressed is not a recipient, so following a record never causes copies.
- **Team channel copy** — the copy that riverflow posts into a team channel.

## Rules for the colleague message copy

1. **Which messages.** Any message that a staff user wrote, when a staff user
   or a team contact is a recipient. This covers the chatter "Send message"
   button, an @mention in a "Log note", the service Email Sender Wizard, and a
   staff user's email reply that the mail gateway posts on the record. These
   are the same message kinds that the external message copy looks at.
2. **Which channel.** The channel of each recipient's home team. When the
   recipient is a team contact, the channel of that team.
3. **Only between teams.** No copy goes to the sender's own home team. A
   message between two members of the same team stays email-only. A sender
   without a home team (an administrator, a developer, a bot user) is in no
   team, so every recipient team gets a copy.
4. **Never to the sender.** The sender never gets a copy for their own
   message, also when they are in the recipient list.
5. **Which records.** Every record with a chatter, not only the records that
   use the review mixin (services, log entries, employees, ships,
   credentials). Messages inside a Discuss channel are chat, not chatter, so
   they are out of scope.
6. **One copy per team.** Two recipients in the same team give one copy in
   that team's channel.
7. **Setup is data, not code.** Riverflow ships the field and the Members list
   but no people. An administrator sets the home team of each staff user by
   hand, from the team form.
8. **A team without a channel gets no copy**, and neither does an archived
   team. Give a team a home-team member only when its channel is a real team
   channel. A team that points at a company-wide channel must not have members,
   because every employee would see the copies.
9. **Normal delivery does not change.** The recipient still gets the message
   by email or Inbox. The copy mentions nobody, because a mention would email
   the recipient a second time.

## What the team sees

The copy is a comment in the team channel, posted as the original sender, so it
counts as unread for the channel members. A header comes first: a link to the
record (a service shows `name | subject`), the subject of an email in bold, and
`To:` with the names of the recipients in this team. The original body follows.
Attachments are not copied; they stay on the record.

People answer **on the record**, through the link. A reply typed in the channel
does not reach the record or the sender, who is often not a member of that
channel. The external message copy has the same limit.

A copy only reaches the members of the channel. Home team and channel
membership are separate on purpose (channels overlap), so the administrator
also checks that every home-team member is a member of their team channel.

## Setting up members

Configuration > Teams > a team > **Members**. An access-rights administrator
(`base.group_erp_manager`) adds existing staff users with **Add** and removes
them with the row's remove button. Removing only clears the user's home team;
it never deletes the user. Other users see the list read-only, because a
member is stored on `res.users`, which only that group may write.

## Technical reference

| File | Purpose |
|------|---------|
| `riverflow/models/res_users.py` | `home_team_id` (Many2one `riverflow.team`, `ondelete="set null"`) |
| `riverflow/models/riverflow_team.py` | `member_ids` (One2many on `home_team_id`); `_post_colleague_message_copy(message, recipients)` builds the header and posts in the channel |
| `riverflow/models/mail_thread.py` | `mail.thread._message_create` hook; `_colleague_copy_teams(message)` holds the routing rules; `_post_colleague_message_copies` posts each copy under a savepoint |
| `riverflow/views/riverflow_team_views.xml` | Members list: an editable `widget="many2many"` node for `base.group_erp_manager`, a read-only node for `!base.group_erp_manager` |
| `riverflow/tests/test_team_channel_copy.py` | Tag `test_team_channels`: copy between teams (Send message, Log note mention, `email` post, plain `res.partner` record), no-copy cases, one copy per team, team contact, external copy unchanged, Members list |

- **An internal note is not outside mail.** The external copy skips
  `subtype_id.internal` (`mail.mt_note`). A log note posted as OdooBot
  has no staff user on the author, so the staff-author skip does not
  catch it. Without the subtype skip, the copy is posted as
  `message_type` `email` in the Responsible team channel and can email
  the channel members. The bounce-review note is an internal note and
  takes this skip.
- **Why `mail.thread`, not the review mixin.** The copy works on every record
  with a chatter. The review mixin's own `_message_create` override (the
  external copy) calls `super()`, so both hooks run; they handle disjoint
  authors (outside sender vs staff user) and never copy the same message.
- **Channels are skipped.** `_message_create` on `discuss.channel` does nothing
  here. A channel message is chat, and the copy is itself a channel post, so
  this also prevents a copy of a copy.
- **Routing runs as sudo.** The sender needs no rights on `riverflow.team` or on
  other users; routing is system machinery.
- **Named `home_team_id`, not `riverflow_team_id`.** `res.users` reaches every
  `res.partner` field through `_inherits`, and `res.partner.riverflow_team_id`
  already means "the team whose contact this partner is".
- **`ondelete="set null"` keeps users safe.** Odoo deletes a One2many line on
  unlink only when the inverse Many2one cascades; with `set null`,
  `Command.unlink` and `Command.set` clear the home team instead.
- **Failure handling.** A plain error in a copy is logged and swallowed under a
  savepoint, so the user's own message always saves. Odoo's retryable
  concurrency errors (`PG_CONCURRENCY_EXCEPTIONS_TO_RETRY`) propagate so the HTTP
  layer replays the request; a channel post bumps the `discuss_channel` row, the
  usual place of such a collision. Same pattern as the bot dispatch drain.
- **The header link works in Discuss.** `record._get_html_link()` emits
  `<a href=# data-oe-model=… data-oe-id=…>`. In the backend, the mail store's
  `handleClickOnLink` (`mail/static/src/core/web/store_service_patch.js`) opens
  such a link as a form from any message, including a Discuss channel message.
- **The sanitizer rewrites the header.** Stored message bodies go through the
  mail HTML sanitizer: it drops a `border-left` style (margin and padding stay)
  and rewrites attributes with double quotes (`data-oe-model="…"`). A test that
  looks for the link must match the sanitized form.
