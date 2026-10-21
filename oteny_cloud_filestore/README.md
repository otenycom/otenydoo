# oteny_cloud_filestore

Keeps the contents of user files in an S3-compatible bucket. A production
database writes with a key from `odoo.conf`. A clone of that database reads
with a read-only key from system parameters and never writes or deletes.
When the bucket is down, files stay in the local filestore and the admins get
one Discuss message.

The skill `.claude/skills/oteny-cloud-filestore/SKILL.md` explains the modes,
the setup, the outage behaviour and the tests.
