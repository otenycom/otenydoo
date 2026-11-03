# Service tree order (`display_order`)

*The order of a service tree in the Child Services list. Audience: developers.*

The Child Services list shows a tree as a flat list, so every service of a tree gets a number in depth-first order: the root is 0, and each service comes right after its parent, with its siblings sorted by deadline, priority, name and id. `_compute_display_order` numbers the whole tree (`root_id`), and the Child Services list sorts on `root_name,root_id,display_order` only. A tree can hold services of more than one subject (a work permit on the employee holds a train booking on a log entry).

Until 19.0.1.1394 the compute numbered per subject (`res_model`, `res_id`) and the list sorted on the subject first: a service whose parent had another subject was left out of the walk and sat apart from its tree. Migration 19.0.1.1394 renumbered the trees with more than one subject (239 trees on a prod copy of 3-Nov-2026).

Every number starts again at 0 in each tree, so a sort across trees uses `root_name,root_id,display_order`, never `display_order` alone (Wilma's `_ai_get_log_entry_services` in radar). Test: `test_child_services_list_shows_a_tree_with_two_subjects_in_tree_order`.
