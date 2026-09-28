# Team Channels — copies of chatter messages

A **team** (`riverflow.team`) has a Discuss channel (`discuss_channel_id`).
Riverflow posts a **copy** of some chatter messages into a team channel, so a
message that one person could miss is also visible to the whole team. The
original message stays on the record's chatter, and Odoo's normal delivery
(email or Inbox) does not change. The copy is extra.

Two rules decide which channel gets a copy. The first rule exists. The second
rule is agreed and not built yet (see [Roadmap](roadmap.md)).

| Rule | Message | Channel that gets the copy |
|------|---------|----------------------------|
| **External message copy** (built) | An outside sender (not a staff user) wrote it, e.g. a supplier's email reply | The channel of the record's **Responsible** team (`responsible_team_id`) |
| **Colleague message copy** (agreed) | A staff user wrote it, and a staff member of **another** team is a recipient | The channel of each **recipient's home team** |

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
  message and every @mention. A team contact (the `res.partner` of a team) can
  also be a recipient.
- **Team channel copy** — the copy that riverflow posts into a team channel.

## Agreed rules for the colleague message copy

1. **Which messages.** Any message that a staff user wrote, when a staff user
   or a team contact is a recipient. This covers the chatter "Send message"
   button, an @mention in a "Log note", the service Email Sender Wizard, and a
   staff user's email reply that the mail gateway posts on the record. These
   are the same message kinds that the external message copy looks at.
2. **Which channel.** The channel of each recipient's home team. When the
   recipient is a team contact, the channel of that team.
3. **Only between teams.** No copy goes to the sender's own home team. A
   message between two members of the same team stays email-only.
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
8. **A team without a channel gets no copy.** Give a team a home-team member
   only when its channel is a real team channel. A team that points at a
   company-wide channel must not have members, because every employee would
   see the copies.
