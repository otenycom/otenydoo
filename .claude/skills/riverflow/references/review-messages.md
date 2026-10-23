---
description: User guide — how replies from outside senders reach a team, how a team finds them on the Radar (the inbox favourites), and how Mark as Reviewed clears them.
---

# Reviewing messages: the Radar inbox and Mark as Reviewed

*User guide for every team that works on the Radar. Technical reference: the
Chatter Mixin section of the riverflow skill and
[the plan of the chatter button](../plans/mark-as-reviewed-in-chatter.md).*

## What counts as a message to review

A record counts a **message to review** when a person outside the company replies
to it by email: a client, a supplier, an authority. The reply lands in the
record's chatter. These do not count:

- a log note;
- a message that a colleague sends or posts;
- a system notification (a state change, a field change).

The count holds every outside reply that arrived after the last **Mark as
Reviewed** on that record. A new reply raises it again.

Records that count replies: every service, and every record of an app that
takes the review mixin (in Crewradar, for example, employees, ships, log entries
and credentials). A service always belongs to such a record, its **subject**.

## Send from the service, not from its subject

**Write to people outside the company from the service that the message is
about** (in Crewradar, for example, the work permit or the A1 service), never
from the subject the service belongs to (for example the employee or the ship).
Use the email steps and Send message of that service.

The service is where the work lives, so the reply comes back to the right place:

- the reply lands on the service, next to its state, deadline and documents;
- the service has a Responsible team, so the reply reaches that team's inbox and
  Discuss channel;
- whoever handles the reply can move the service on in the same screen;
- the history of one case stays together, instead of spread over the subject's
  chatter.

A message sent from the subject brings its reply back to the subject.
There it is mixed with all other history, it has no deadline and no state, and
when the record has no Responsible team it reaches no inbox at all. When there
is no service for the work yet, start one first (Add Service on the subject),
then write from it.

A subject still counts and shows a reply that arrives there
(an outside sender can always answer an old email), so the button below works
there too: read it, move the work to the right service, and mark it as reviewed.

## Where you find the messages

- **The chatter of the service** (or of the record that received the reply). The top bar shows "N to review" and the button
  **Mark as Reviewed**, next to Send message and Log note. It shows only while
  there is something to review, and it updates when a new reply arrives while you
  look at the record (when you follow the record).
- **The Radar.** The column **Review** shows the count of each row. The filter
  **Needs Review** shows every row with a count above 0.
- **Your team's inbox favourite.** A team has a saved Radar favourite, for example
  an inbox favourite of the team: Review above 0 and Responsible = the team. It lists the records your
  team must look at.
- **Your team's Discuss channel.** A copy of each outside reply is posted there,
  with a link to the record, so the team sees it at once.

**The Responsible field decides the team.** A record reaches a team's inbox and
channel only when its **Responsible** is that team. A record without a Responsible
team reaches no inbox. Set the Responsible on the record, or ask the Radar
administrators when many records lack it.

## Mark as Reviewed

Click **Mark as Reviewed** in the chatter when you have read the replies and the
follow-up is done or taken by you. The click means "I own this": a colleague sees
that nobody else needs to act, and the record leaves the inbox.

- The click clears all replies of the record up to now, not one message.
- The messages stay in the chatter. Mark as Reviewed hides nothing and deletes
  nothing.
- Some transition wizards (for example *Supplier Confirmed*) offer a switch "Mark
  external messages as reviewed", on by default, so the same step clears them.

## What not to do

- **Do not send an outside email from the subject.** Send it from the service
  of the case, so the reply comes back to that service.
- **Do not archive the record to empty the inbox.** That archives the record
  itself (the service, or its subject).
- **Do not change the inbox favourite to hide a kind of record** (for example to
  show only services). New replies on the hidden records then reach nobody.
- **Do not try to delete or archive a message.** Chatter messages stay as the
  record's history.

## A clean inbox

Clear an old inbox once: open each record, read the replies, act or note who acts,
then click Mark as Reviewed. After that, work the inbox every day; an empty inbox
means every outside reply has an owner.
