from odoo import fields, models

# The client action a held call returns. The browser registers a function
# action under this tag: it returns nothing, so the dialog stays open, and it
# tells the open form to reload, so the findings show.
HOLD_TAG = "riverflow_save_check_hold"


def _findings_key(findings):
    """What makes two sets of findings 'the same' for the confirmation.

    The user confirmed what the box said: the level, the message and what it
    is about. The machine code (``kind``) and the widget link (``ref``) are
    not shown, so they do not count."""
    return [(f.get("level"), f.get("message"), f.get("subject") or "") for f in findings or []]


def _guard(model_class, name, origin):
    """Wrap a guarded method of one registry class with the save check gate."""

    def guarded(self, *args, **kwargs):
        # A model with its own _name that inherits a guarded model has the
        # parent's registry class as a base class, so an override's super()
        # reaches the parent's wrapper too. Only the wrapper of the record's
        # own class gates: the check runs once per call, before every
        # override, and never again halfway down the super() chain after an
        # override has done its work.
        if type(self) is model_class:
            held = self._save_check_gate()
            if held:
                return held
        return origin(self, *args, **kwargs)

    guarded.__name__ = name
    guarded.__doc__ = origin.__doc__
    guarded.origin = origin
    guarded._save_check_guarded = True
    return guarded


class RiverflowSaveCheckMixin(models.AbstractModel):
    """Save check: confirm warnings before save, for any form or wizard.

    A form often has to tell the user something before it saves. An *error*
    stops the save: the message says how to fix it. A *warning* lets the
    save go on, but only when the user ticks the confirmation "I confirm —
    save with these warnings". Before this mixin, eight wizards did this
    each in their own way (five checkbox names, four save behaviours);
    plan: radar ``plans/save-check.md``.

    A consumer overrides ``_save_check()`` and returns its findings. The
    gate runs that check on every call of a guarded method
    (``_save_check_methods``, default ``action_save``: the OK button of
    every transition wizard), when the user clicks it, never live on change.
    There is no fingerprint of the values: every call checks again, so the
    findings are never stale. A consumer with an expensive step (an AI scan)
    caches that step itself.

    A finding is a dict::

        {
            "level": "error" | "warning",
            "message": "...",          # what is wrong, and how to fix it
            "kind": "...",             # optional machine code
            "subject": "...",          # optional: what it is about (a file name)
            "ref": "ir.attachment,7",  # optional: for a consumer widget
        }

    There is no "info" level on purpose: an info needs no confirmation but
    would still stop the first save. An info stays a toast after the dialog
    closes.
    """

    _name = "riverflow.save.check.mixin"
    _description = "Save check: confirm warnings before save"

    # The methods the gate wraps. A consumer with another button adds it here.
    _save_check_methods = ("action_save",)

    save_check_state = fields.Selection(
        [("warning", "Warnings"), ("error", "Errors")],
        help="The worst level of the findings last shown. Empty: nothing to show.",
    )
    save_check_findings = fields.Json(
        help="The findings of the save check, as the dialog last showed them.",
    )
    save_check_confirmed = fields.Boolean(
        string="I confirm — save with these warnings",
        help="Valid for the warnings shown. A different set of findings clears it.",
    )

    def _save_check(self):
        """The findings for this one record. A consumer overrides this, calls
        super() and adds its own. The base has none."""
        self.ensure_one()
        return []

    def _register_hook(self):
        """Wrap each guarded method on the final registry class.

        The wrapper is the outermost layer, so the check runs before every
        override, in any module load order. This matters because overrides
        do real work before their super() call (an import, an upload): a
        gate inside the base method would run after that work. Precedent:
        base_automation patches create and write the same way."""
        super()._register_hook()
        if self._abstract:
            return
        model_class = self.env.registry[self._name]
        for name in self._save_check_methods:
            if getattr(model_class.__dict__.get(name), "_save_check_guarded", False):
                continue
            method = getattr(model_class, name, None)
            if method is None:
                continue
            setattr(model_class, name, _guard(model_class, name, method))

    def _unregister_hook(self):
        """Remove the wrappers. Odoo calls this before it sets the models up
        again (a module install in a running registry), and then calls
        _register_hook, so the wrapper always wraps the current method."""
        super()._unregister_hook()
        if self._abstract:
            return
        model_class = self.env.registry[self._name]
        for name in self._save_check_methods:
            if getattr(model_class.__dict__.get(name), "_save_check_guarded", False):
                delattr(model_class, name)

    def _save_check_store(self, findings):
        """Store the findings. Return True when the guarded call may go on.

        No findings: go on (and clear what an earlier hold stored). An
        error: hold. Warnings only: go on when the user confirmed exactly
        these warnings, else hold. A hold always clears the confirmation, so
        a stale tick never lets a different warning through."""
        self.ensure_one()
        if not findings:
            if self.save_check_state or self.save_check_findings or self.save_check_confirmed:
                self.write({
                    "save_check_state": False,
                    "save_check_findings": False,
                    "save_check_confirmed": False,
                })
            return True
        has_error = any(f.get("level") == "error" for f in findings)
        same = _findings_key(findings) == _findings_key(self.save_check_findings)
        if not has_error and same and self.save_check_confirmed:
            return True
        self.write({
            "save_check_state": "error" if has_error else "warning",
            "save_check_findings": findings,
            "save_check_confirmed": False,
        })
        return False

    def _save_check_gate(self):
        """Run the save check on each record. The hold action of the first
        record that holds, or None when the call may go on.

        A hold returns normally instead of raising: a UserError would roll
        back the stored findings (the box would stay empty) and a consumer's
        cache (the next OK would pay for an AI scan again). Nothing of the
        guarded method has run yet, so the transaction holds only what the
        check wrote."""
        for record in self:
            if not record._save_check_store(record._save_check() or []):
                return record._save_check_hold_action()
        return None

    def _save_check_hold_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": HOLD_TAG,
            "params": {
                "res_model": self._name,
                "res_id": self.id,
                "state": self.save_check_state,
                "findings": self.save_check_findings or [],
            },
        }

    def action_save_check(self):
        """A 'Check now' button: run the check, show the result, stay open."""
        self.ensure_one()
        self._save_check_store(self._save_check() or [])
        return self._save_check_hold_action()

    def _save_check_is_held(self, result):
        """True when the answer of a guarded method is a hold."""
        return isinstance(result, dict) and result.get("tag") == HOLD_TAG
