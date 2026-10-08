from odoo import api, models


class CheckResultsMixin(models.AbstractModel):
    _name = "riverflow.check.results.mixin"
    _description = "Check Results Mixin"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self._refresh_check_results_on_create(records)
        return records

    def _refresh_check_results_on_create(self, records):
        pass

    def write(self, vals):
        before__write_result = self._refresh_check_results_on_before__write(vals)
        result = super().write(vals)
        self._refresh_check_results_on_after__write(before__write_result)
        return result

    def _refresh_check_results_on_before__write(self, vals):
        pass

    def _refresh_check_results_on_after__write(self, before__write_result):
        pass

    def _sync_check_results(self, existing_check_results, new_check_results, index_field):
        CheckResult = self.env["riverflow.check.result"]

        # A retired row (to_be_deleted) is never the live result: a caller in an
        # active_test=False env finds it, and compare() must not keep it. The
        # sync creates a new row instead (no revive).
        existing_check_results = existing_check_results.filtered(lambda r: not r.to_be_deleted)

        # Index existing check results
        existing_indexed = {}
        for result in existing_check_results:
            index = getattr(result, index_field).id
            existing_indexed.setdefault(index, []).append(result)

        to_keep = self.env["riverflow.check.result"]
        to_create = []

        for new_result in new_check_results:
            new_index = new_result[index_field]
            matching_result = None

            if new_index in existing_indexed:
                existing_results = existing_indexed[new_index]
                matching_result = next(
                    (result for result in existing_results if result.compare(new_result)),
                    None,
                )

            if matching_result:
                to_keep |= matching_result
            else:
                to_create.append(new_result)

        created_results = CheckResult.create(to_create)

        # Retire, never unlink: this runs inside a compute, and unlink() would
        # run every pending compute and clear the cache (riverflow.tombstone.mixin).
        to_retire = existing_check_results - to_keep
        to_retire._retire()

    def base_check_results(self):
        result = self.check_result_ids.filtered(lambda r: r.check_type in ["gap", "overlap", "reversed"])
        return result

    def print_check_results(self):
        """
        Debug method to print a table of check results for this log entry.
        """
        print(f"\nCheck Results for Log Entry: {self.name}")
        print(
            "{:<10} {:<12} {:<12} {:<10} {:<50}".format("Type", "Start Date", "End Date", "Severity", "Name")
        )
        print("-" * 94)
        for result in self.check_result_ids:
            print(
                "{:<10} {:<12} {:<12} {:<10} {:<50}".format(
                    result.check_type,
                    (result.start_date.strftime("%Y-%m-%d") if result.start_date else "N/A"),
                    result.end_date.strftime("%Y-%m-%d") if result.end_date else "N/A",
                    result.severity,
                    (result.name[:47] + "..." if len(result.name) > 50 else result.name),
                )
            )
        print()
