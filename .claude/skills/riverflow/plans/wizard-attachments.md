# Plan: attachments that stay on a wizard

Status: analysis done on 7-Nov-2026, read-only, on `crmain` restored from `~/Downloads/cr.zip`
(production backup of 27-Oct-2026). Design decided by Ries on 7-Nov-2026 (§ 7). Not built. Basecamp: [BC 10487076751](https://3.basecamp.com/5527227/buckets/38887735/todos/10487076751).

**Builds on its own** (Ries, 7-Nov-2026). This plan is about files, not about the audit log. No
part of it depends on the audit plan (oteny-audit `plans/audit-storage-reduction.md`, where
the problem was found, § 12), and the audit plan does not depend on it. Either one can be
built, released and tested first.

## 1. What is wrong

A user uploads a file in a wizard (a transient model) through its `attachment_ids` field. Odoo
creates the `ir.attachment` before the wizard is saved, with `res_model` = the wizard model and
`res_id = 0`. Unless code moves it, it stays like that after Odoo has vacuumed the wizard.

On the backup, 10,499 attachments without `res_field` point to a transient model, all with
`res_id = 0` except the 5 of `crewradar_sign.fill.wizard`. The oldest is from 27-Sep-2025,
and 671 are from October 2026, so it still happens.

They are two different problems:

| Kind | Attachments | What it means |
|---|---|---|
| **Linked to a chatter message** (`message_attachment_rel`) | 8,382 | A live file in the chatter of a service, which still says it belongs to a wizard |
| **Pinned as the sent file of a service** (`riverflow_service.sent_attachment_id`) | 49 (of which some also on a message) | Same, for the form that `crewradar_sign` sent |
| **Linked to nothing** outside a wizard | 2,117 | A real orphan |

Each linked attachment belongs to exactly one service (`mail_message.model` is
`riverflow.service` for all of them, and no attachment is on messages of two records).

## 2. Why: the code path of each wizard

Odoo's `message_post` moves an attachment to the record only when its `res_model` is
`mail.compose.message` or `mail.scheduled.message` and the current user created it
(`mail/models/mail_thread.py`, `_process_attachments_for_post`). Odoo's own composer also has an
autovacuum that deletes its `res_id = 0` uploads after one day
(`mail/wizard/mail_compose_message.py`, `_gc_lost_attachments`). A custom wizard gets neither.

| Wizard | Orphans | Linked | Code path |
|---|---|---|---|
| `riverflow.service.email.sender.wizard` (riverflow) | 7,946 | 7,503 on a message, 49 pinned | `_send_email` passes `self.attachment_ids.ids` to `message_post` and never moves them (`riverflow/wizards/riverflow_service_email_sender.py`, around line 440). **Still happens:** 454 since 1-Oct-2026. |
| `riverflow.enter.airline.ticket.wizard` (riverflow) | 628 | 571 | Moves its uploads to the service after `message_post` since commit `e145009` of 28-Feb-2026 (comment "Re-link attachments from the transient wizard"). All 571 linked ones are from before 2-Mar-2026. The other 57 are uploads of a wizard that was closed without saving, or that used the existing attachment. |
| `riverflow.enter.train.ticket.wizard` (riverflow) | 359 | 308 | Same as the airline wizard, same date. 51 unlinked. |
| `rivercreds.credential.wizard` (rivercreds) | 822 | 0 | Copies the bytes into the import queue (`queue_documents_for_import`, `rivercreds/wizards/rivercreds_credential_wizard.py`, around line 460), which saves its own attachment. The upload stays behind. |
| `rivercreds.document.import.wizard` (rivercreds) | 399 | 0 | Same: copies the bytes into the queue (around line 289). |
| `cuneus.wp.at_applied.wizard`, `cuneus.wp.upload.permit.wizard` (crewradar_cuneus_sign) | 163, 157 | 0 | Copy into the queue or into a new attachment; the upload stays. |
| `cuneus.weekly.bulk.upload.wizard` (crewradar_cuneus_sign) | 15 | 0 | Copies the bytes (around line 212); the upload stays. |
| `crewradar_sign.fill.wizard` | 5 | 0 | Binds its preview to the wizard record itself (`res_id` = the wizard id). |
| `mail.compose.message` | 3 | 0 | Odoo's own autovacuum deletes these after one day. |
| `rivercreds.credential.upload.wizard`, `riverflow.service.wizard` | 1, 1 | 0 | Single old cases. |

## 3. What it costs today

- **The chatter files of a service are not the service's attachments.** Every search by
  `res_model` and `res_id` misses them. That includes Odoo's paperclip counter
  `message_attachment_count` (`mail_thread.py`, `_compute_message_attachment_count`), which the
  ticket wizards also test (`service.message_attachment_count == 0`).
- **A user who is not an administrator cannot read them.** `ir.attachment._check_access`
  (`base/models/ir_attachment.py`) lets only the creator and administrators read an attachment
  with `res_id = 0`. Tested on attachment 68945 (a file sent from service 39384): `bambam`,
  `khinmaungsoe@cuneusmm.com` and `r.lafeber@cuneuscrew.com` can read the service but not the
  attachment. 17 of the 21 active internal users are administrators, which hides the fault. The
  chatter itself still shows and downloads the file, because the chatter sends a
  `raw_access_token` with each attachment; an API call or a bot that reads the file with its
  own rights fails.
- **Storage.** The 2,117 real orphans have a `file_size` of 2.2 GB in all, but most of their
  content is also stored under another attachment (the queue copies the same bytes). Deleting
  them frees 222 files, 122 MB. With the Cloud filestore, the bucket object goes after its hold
  time (`hold_days`, default 365 days); the local file goes at Odoo's next file garbage
  collection.

The earlier estimate of 4.9 GB (oteny-audit plan, § 12) counted the linked chatter files too,
and did not remove the shared content. It was wrong.

## 4. The fix

### 4.1 Root fix: the email sender wizard moves its own uploads

In `riverflow.service.email.sender.wizard._send_email`, after `message_post`, move the
attachments that the wizard itself holds (`res_model` = the wizard model) to the service:
`res_model = "riverflow.service"`, `res_id = service.id`. This is the same fix that the ticket
wizards got on 28-Feb-2026. Attachments of the mail template (`res_model = "mail.template"`)
and the form that `crewradar_sign` already moved stay as they are, because their `res_model` is
not the wizard.

### 4.2 Safety net: a garbage collection for wizard uploads

An `@api.autovacuum` method on `ir.attachment` deletes an attachment when:
- its `res_model` is a transient model and `res_field` is empty;
- `create_date` and `write_date` are more than one day old (the rule of Odoo's
  `_gc_lost_attachments`, so a wizard that is still open keeps its files);
- no record of a model that is not transient points to it. The method finds these links from
  the database: every foreign key to `ir_attachment`, minus the tables of transient models and
  the relation tables whose other column points to such a table. On the backup that leaves 22
  links, among them `message_attachment_rel`, `riverflow_service.sent_attachment_id`,
  `rivercreds_credential_attachment.attachment_id` and
  `rivercreds_document_import_job.file_attachment_id`.

This also covers the copying wizards of rivercreds and crewradar_cuneus_sign, every wizard that
is closed without saving, and every wizard that is added later, without code in each wizard.

### 4.3 One-time repair of the linked files

A riverflow migration moves each attachment of a transient model that a chatter message links
to the record of that message (`res_model`, `res_id` from `mail_message.model` and `res_id`):
8,382 attachments. The 49 pinned as `riverflow_service.sent_attachment_id` move to that service.
It runs as SQL, so it writes no audit rows; it changes two columns of 8,4xx rows.

After the repair, the garbage collection of § 4.2 deletes the 2,117 real orphans at its first
run.

## 5. Tests

- 4.1: send from the email sender wizard with an uploaded file and a template file: the upload
  has `res_model = "riverflow.service"` and the `res_id` of the service, the template file is
  unchanged, and a user who can read the service but is not an administrator can read the
  upload. `message_attachment_count` of the service counts it.
- 4.2: an upload on a wizard older than one day and linked to nothing is deleted; the same
  upload younger than one day stays; an old upload that a `mail.message`, a credential
  attachment or an import job links to stays.
- 4.3: on `crmain`, after the migration, no attachment of a transient model is linked to a
  chatter message, and the counts match § 1.

## 6. Where it lives

All three parts live in riverflow (decided by Ries on 7-Nov-2026): 4.1 and 4.3 because the email
sender wizard and the ticket wizards are riverflow code, and 4.2 because every module with such
a wizard depends on riverflow, while `oteny_base` only holds a menu. The change needs a version
bump of riverflow only.

## 7. Decisions (Ries, 7-Nov-2026)

1. **The garbage collection lives in riverflow**, not in `oteny_base`.
2. **The repair runs:** a riverflow migration moves the 8,431 linked files to their service.
3. **The copying wizards get no code of their own:** the credential, import and permit wizards
   keep leaving their upload behind, and the garbage collection deletes it after one day.

## 8. Roadmap

- [ ] **Wizard uploads: move them to the service, clean up the rest** (this plan, § 4): the email sender wizard moves its uploads to the service; a nightly garbage collection deletes wizard uploads that are older than one day and linked to nothing; a migration moves the 8,431 chatter files that still point to a wizard to their service. Riverflow only. · [BC 10487076751](https://3.basecamp.com/5527227/buckets/38887735/todos/10487076751)
