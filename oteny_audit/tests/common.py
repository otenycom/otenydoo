def end_transaction(env):
    """Do to the audit state what a commit does, inside a test's single transaction.

    A change of a record in the transaction of its create writes no row (since 19.0.1.747;
    plans/audit-insert-id-only.md). A test that checks an UPDATE row writes after this call,
    as a user does in a later transaction.
    """
    env.flush_all()
    env.cr.precommit.data.pop("oteny_audit", None)
