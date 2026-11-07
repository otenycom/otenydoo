def end_transaction(env):
    """Do to the audit state what a commit does, inside a test's single transaction.

    Since 19.0.1.723 a change of a record in the transaction of its create goes into the
    insert snapshot (cut E of plans/audit-storage-reduction.md). A test that checks an
    UPDATE row writes after this call, as a user does in a later transaction.
    """
    env.flush_all()
    env.cr.precommit.data.pop("oteny_audit", None)
