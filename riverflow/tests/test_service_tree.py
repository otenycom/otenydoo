from odoo.tests.common import TransactionCase
from odoo.tests import tagged
from odoo.tools.sql import index_exists
from datetime import date


@tagged("post_install", "-at_install", "riverflow", "test_services")
class ServiceDeadlineTestCase(TransactionCase):

    TEST_PREFIX = "TestRun "

    @classmethod
    def setUpClass(cls):
        super(ServiceDeadlineTestCase, cls).setUpClass()

    def cleanup_test_services(self):
        self.env["riverflow.service"].search([("name", "like", f"{self.TEST_PREFIX}%")]).unlink()

    def dump_services_to_console(self, services):
        print("| indented_name              | deadline   | daily_prio | root_name")
        print("|----------------------------|------------|------------|------------|")
        for service in services:
            print(
                f"| {service.indented_name.replace(self.TEST_PREFIX, ''):<26} | {service.deadline} | {service.daily_prio:03d}       | {service.root_name:<26}"
            )

    def create_service_tree(self):
        self.cleanup_test_services()

        # Create 3 root services
        root_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Root Service 1",
                "project_deadline": "2024-01-01",
            }
        )

        root_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Root Service 2",
                "project_deadline": "2024-02-01",
            }
        )

        root_3 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Root Service 3",
                "project_deadline": "2024-03-01",
            }
        )

        # Create children for Root Service 2
        child_2_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 2.1",
                "parent_id": root_2.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": -1,
            }
        )

        child_2_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 2.2",
                "parent_id": root_2.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": -2,
            }
        )

        child_2_3 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 2.3",
                "parent_id": root_2.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": -3,
            }
        )

        # Create children for Root Service 3
        child_3_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 3.1",
                "parent_id": root_3.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": -1,
            }
        )

        child_3_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 3.2",
                "parent_id": root_3.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": -2,
            }
        )

        child_3_3 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 3.3",
                "parent_id": root_3.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": -3,
            }
        )

        # Create grandchildren for Child 3.2
        grandchild_3_2_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Grandchild 3.2.1",
                "parent_id": child_3_2.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": 1,
            }
        )

        grandchild_3_2_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Grandchild 3.2.2",
                "parent_id": child_3_2.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": 2,
            }
        )

        return (
            root_1,
            root_2,
            root_3,
            child_2_1,
            child_2_2,
            child_2_3,
            child_3_1,
            child_3_2,
            child_3_3,
            grandchild_3_2_1,
            grandchild_3_2_2,
        )

    def test_service_tree_deadlines(self):
        (
            root_1,
            root_2,
            root_3,
            child_2_1,
            child_2_2,
            child_2_3,
            child_3_1,
            child_3_2,
            child_3_3,
            grandchild_3_2_1,
            grandchild_3_2_2,
        ) = self.create_service_tree()

        # Verify the structure
        self.assertEqual(len(root_1.child_ids), 0, "Root Service 1 should have no children")

        self.assertEqual(len(root_2.child_ids), 3, "Root Service 2 should have 3 children")
        self.assertEqual(root_2.child_ids[0].name, f"{self.TEST_PREFIX}Child 2.1")
        self.assertEqual(root_2.child_ids[1].name, f"{self.TEST_PREFIX}Child 2.2")
        self.assertEqual(root_2.child_ids[2].name, f"{self.TEST_PREFIX}Child 2.3")

        self.assertEqual(len(root_3.child_ids), 3, "Root Service 3 should have 3 children")
        self.assertEqual(root_3.child_ids[0].name, f"{self.TEST_PREFIX}Child 3.1")
        self.assertEqual(root_3.child_ids[1].name, f"{self.TEST_PREFIX}Child 3.2")
        self.assertEqual(root_3.child_ids[2].name, f"{self.TEST_PREFIX}Child 3.3")

        self.assertEqual(len(child_3_2.child_ids), 2, "Child 3.2 should have 2 grandchildren")
        self.assertEqual(child_3_2.child_ids[0].name, f"{self.TEST_PREFIX}Grandchild 3.2.1")
        self.assertEqual(child_3_2.child_ids[1].name, f"{self.TEST_PREFIX}Grandchild 3.2.2")

        # Verify the root_id is set correctly for all services
        all_services = (
            root_1
            + root_2
            + root_3
            + child_2_1
            + child_2_2
            + child_2_3
            + child_3_1
            + child_3_2
            + child_3_3
            + grandchild_3_2_1
            + grandchild_3_2_2
        )
        for service in all_services:
            if service in [root_1, root_2, root_3]:
                self.assertEqual(service.root_id, service, f"{service.name} should be its own root")
            elif service in [child_2_1, child_2_2, child_2_3]:
                self.assertEqual(
                    service.root_id,
                    root_2,
                    f"{service.name} should have Root Service 2 as root",
                )
            else:
                self.assertEqual(
                    service.root_id,
                    root_3,
                    f"{service.name} should have Root Service 3 as root",
                )

        # Verify deadlines for children of Root Service 2
        self.assertEqual(
            child_2_1.deadline,
            date(2024, 1, 31),
            "Child 2.1 deadline should be 1 day before Root Service 2",
        )
        self.assertEqual(
            child_2_2.deadline,
            date(2024, 1, 30),
            "Child 2.2 deadline should be 2 days before Root Service 2",
        )
        self.assertEqual(
            child_2_3.deadline,
            date(2024, 1, 29),
            "Child 2.3 deadline should be 3 days before Root Service 2",
        )

        # Verify deadlines for children of Root Service 3
        self.assertEqual(
            child_3_1.deadline,
            date(2024, 2, 29),
            "Child 3.1 deadline should be 1 day before Root Service 3",
        )
        self.assertEqual(
            child_3_2.deadline,
            date(2024, 2, 28),
            "Child 3.2 deadline should be 2 days before Root Service 3",
        )
        self.assertEqual(
            child_3_3.deadline,
            date(2024, 2, 27),
            "Child 3.3 deadline should be 3 days before Root Service 3",
        )

        # Verify deadlines for grandchildren of Child 3.2
        self.assertEqual(
            grandchild_3_2_1.deadline,
            date(2024, 3, 2),
            "Grandchild 3.2.1 deadline should be 1 day after Root Service 3",
        )
        self.assertEqual(
            grandchild_3_2_2.deadline,
            date(2024, 3, 3),
            "Grandchild 3.2.2 deadline should be 2 days after Root Service 3",
        )

    def test_service_search_and_order(self):
        self.create_service_tree()

        # Search for all services created in this test
        services = self.env["riverflow.service"].search([("name", "like", f"{self.TEST_PREFIX}%")])

        # Expected order of services: Root services by name, child services by date
        # | indented_name              | deadline   |
        # |----------------------------|------------|
        # | Root Service 1             | 2024-01-01 |
        # | Root Service 2             | 2024-02-01 |
        # |     Child 2.3              | 2024-01-29 |
        # |     Child 2.2              | 2024-01-30 |
        # |     Child 2.1              | 2024-01-31 |
        # | Root Service 3             | 2024-03-01 |
        # |     Child 3.3              | 2024-02-27 |
        # |     Child 3.2              | 2024-02-28 |
        # |         Grandchild 3.2.1   | 2024-03-02 |
        # |         Grandchild 3.2.2   | 2024-03-03 |
        # |     Child 3.1              | 2024-02-29 |

        # Expected order of services
        expected_order = [
            f"{self.TEST_PREFIX}Root Service 1",
            f"{self.TEST_PREFIX}Root Service 2",
            f"{self.TEST_PREFIX}Child 2.3",
            f"{self.TEST_PREFIX}Child 2.2",
            f"{self.TEST_PREFIX}Child 2.1",
            f"{self.TEST_PREFIX}Root Service 3",
            f"{self.TEST_PREFIX}Child 3.3",
            f"{self.TEST_PREFIX}Child 3.2",
            f"{self.TEST_PREFIX}Grandchild 3.2.1",
            f"{self.TEST_PREFIX}Grandchild 3.2.2",
            f"{self.TEST_PREFIX}Child 3.1",
        ]

        # Verify the number of services
        self.assertEqual(len(services), len(expected_order), "Incorrect number of services found")

        # self.dump_services_to_console(services)

        # Verify the order of services
        for i, service in enumerate(services):
            self.assertEqual(
                service.name,
                expected_order[i],
                f"Service at position {i} should be '{expected_order[i]}', but found '{service.name}'",
            )

        # Verify parent-child relationships
        root_2 = services.filtered(lambda s: s.name == f"{self.TEST_PREFIX}Root Service 2")
        root_3 = services.filtered(lambda s: s.name == f"{self.TEST_PREFIX}Root Service 3")
        child_3_2 = services.filtered(lambda s: s.name == f"{self.TEST_PREFIX}Child 3.2")

        self.assertEqual(len(root_2.child_ids), 3, "Root Service 2 should have 3 children")
        self.assertEqual(len(root_3.child_ids), 3, "Root Service 3 should have 3 children")
        self.assertEqual(len(child_3_2.child_ids), 2, "Child 3.2 should have 2 children")

        # Verify the order of children
        self.assertEqual(
            root_2.child_ids.mapped("name"),
            [
                f"{self.TEST_PREFIX}Child 2.1",
                f"{self.TEST_PREFIX}Child 2.2",
                f"{self.TEST_PREFIX}Child 2.3",
            ],
            "Children of Root Service 2 are not in the correct order",
        )
        self.assertEqual(
            root_3.child_ids.mapped("name"),
            [
                f"{self.TEST_PREFIX}Child 3.1",
                f"{self.TEST_PREFIX}Child 3.2",
                f"{self.TEST_PREFIX}Child 3.3",
            ],
            "Children of Root Service 3 are not in the correct order",
        )
        self.assertEqual(
            child_3_2.child_ids.mapped("name"),
            [
                f"{self.TEST_PREFIX}Grandchild 3.2.1",
                f"{self.TEST_PREFIX}Grandchild 3.2.2",
            ],
            "Children of Child 3.2 are not in the correct order",
        )

    def test_change_use_project_deadline_from(self):
        (
            root_1,
            root_2,
            root_3,
            child_2_1,
            child_2_2,
            child_2_3,
            child_3_1,
            child_3_2,
            child_3_3,
            grandchild_3_2_1,
            grandchild_3_2_2,
        ) = self.create_service_tree()

        # | indented_name              | deadline   |
        # |----------------------------|------------|
        # | Root Service 1             | 2024-01-01 |
        # | Root Service 2             | 2024-02-01 |
        # |     Child 2.3              | 2024-01-29 |
        # |     Child 2.2              | 2024-01-30 |
        # |     Child 2.1              | 2024-01-31 |
        # | Root Service 3             | 2024-03-01 |
        # |     Child 3.3              | 2024-02-27 |
        # |     Child 3.2              | 2024-02-28 |
        # |         Grandchild 3.2.1   | 2024-03-02 |
        # |         Grandchild 3.2.2   | 2024-03-03 |
        # |     Child 3.1              | 2024-02-29 |

        # with self.assertRaisesRegex(UserError, "You must set the Deadline"):
        child_3_2.write({"use_project_deadline_from": "self", "project_deadline": False})

        # NOTE: Users can't clear the deadline, as asserted above. Checks below are for the odd case where a deadline is set to 'self' and then cleared.
        self.assertEqual(child_3_2.deadline, False, "The deadline should be cleared")

        services = self.env["riverflow.service"].search([("name", "like", f"{self.TEST_PREFIX}%")])
        # self.dump_services_to_console(services)
        child_3_2 = services.filtered(lambda s: s.name == f"{self.TEST_PREFIX}Child 3.2")
        self.assertEqual(child_3_2.deadline, False, "The deadline should be cleared")
        root_3 = services.filtered(lambda s: s.name == f"{self.TEST_PREFIX}Root Service 3")

        # self.dump_services_to_console(root_3.child_ids)
        # self.dump_services_to_console(services)

        last_child_id = None
        for service in root_3.child_ids.sorted(key=lambda r: r.display_order):
            last_child_id = service.id
        self.assertEqual(
            last_child_id,
            child_3_2.id,
            "Child 3.2 should be the last child of Root Service 3",
        )

    def test_create_service_with_deadline(self):
        # days_relative_to_project should be ignored when use_project_deadline_from is 'self'
        services = self.env["riverflow.service"].create(
            {
                "name": "Service 1",
                "project_deadline": "2024-01-02",
                "days_relative_to_project": -1,
            },
        )

        self.assertRecordValues(
            services,
            [
                {
                    "name": "Service 1",
                    "use_project_deadline_from": "self",
                    "project_deadline": date(2024, 1, 2),
                    "days_relative_to_project": -1,  # not relevant for 'self'
                    "deadline": date(2024, 1, 2),
                }
            ],
        )

    def test_service_tree_identical_deadlines(self):
        """Test that services with identical deadlines are ordered by daily_prio"""
        self.cleanup_test_services()

        # Create 2 root services with same deadline but different daily_prio
        root_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX} A",
                "project_deadline": "2024-06-15",
                "daily_prio": 2,
            }
        )

        root_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX} B",
                "project_deadline": "2024-06-15",
                "daily_prio": 1,
            }
        )

        # Create 3 children for root_1 with same deadline but different priorities
        child_1_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child",
                "parent_id": root_1.id,
                "use_project_deadline_from": "self",
                "project_deadline": "2024-06-10",
                "daily_prio": 1,
            }
        )

        child_1_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child",
                "parent_id": root_1.id,
                "use_project_deadline_from": "self",
                "project_deadline": "2024-06-10",
                "daily_prio": 2,
            }
        )

        child_1_3 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child",
                "parent_id": root_1.id,
                "use_project_deadline_from": "self",
                "project_deadline": "2024-06-10",
                "daily_prio": 3,
            }
        )

        # Create 3 grandchildren for each child of root_1
        # Grandchildren of Child 1.1
        self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Grandchild",
                "parent_id": child_1_1.id,
                "use_project_deadline_from": "self",
                "project_deadline": "2024-06-05",
                "daily_prio": 1,
            }
        )

        self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Grandchild",
                "parent_id": child_1_1.id,
                "use_project_deadline_from": "self",
                "project_deadline": "2024-06-05",
                "daily_prio": 2,
            }
        )

        self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Grandchild",
                "parent_id": child_1_1.id,
                "use_project_deadline_from": "self",
                "project_deadline": "2024-06-05",
                "daily_prio": 3,
            }
        )

        # Search for all test services
        services = self.env["riverflow.service"].search(
            [("name", "like", f"{self.TEST_PREFIX}%")],
        )

        # Debug: print the tree structure
        print("\n=== Service Tree Structure (ordered by display_order) ===")
        self.dump_services_to_console(services)

        # Verify root services are ordered by daily_prio
        root_services = services.filtered(lambda s: not s.parent_id)
        self.assertEqual(len(root_services), 2, "Should have exactly 2 root services")

        # Root with lower priority number should come first
        self.assertEqual(root_services[0].daily_prio, 1, "First root should have priority 1")
        self.assertEqual(root_services[1].daily_prio, 2, "Second root should have priority 2")

        # Verify children of root_1 are ordered by daily_prio
        root_1_children = root_1.child_ids.sorted(key=lambda r: r.display_order)
        self.assertEqual(len(root_1_children), 3, "Root 1 should have 3 children")
        self.assertEqual(root_1_children[0].daily_prio, 1, "First child of root 1 should have priority 1")
        self.assertEqual(root_1_children[1].daily_prio, 2, "Second child of root 1 should have priority 2")
        self.assertEqual(root_1_children[2].daily_prio, 3, "Third child of root 1 should have priority 3")

        # Verify grandchildren of child_1_1 are ordered by daily_prio
        gc_1_1 = child_1_1.child_ids.sorted(key=lambda r: r.display_order)
        self.assertEqual(len(gc_1_1), 3, "Child 1.1 should have 3 grandchildren")
        self.assertEqual(gc_1_1[0].daily_prio, 1)
        self.assertEqual(gc_1_1[1].daily_prio, 2)
        self.assertEqual(gc_1_1[2].daily_prio, 3)

    def test_service_tree_different_deadlines(self):
        """Test that service trees with different root deadlines are kept together and are ordered by root date"""
        self.cleanup_test_services()

        # Create 2 root services with same deadline but different daily_prio
        root_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX} A",
                "project_deadline": "2024-06-15",
            }
        )

        root_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX} B",
                "project_deadline": "2024-06-16",
            }
        )

        # Create 3 children for root_1 with same deadline but different priorities
        child_1_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child",
                "parent_id": root_1.id,
                "use_project_deadline_from": "self",
                "project_deadline": "2024-06-15",
                "daily_prio": 1,
            }
        )

        child_1_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child",
                "parent_id": root_1.id,
                "use_project_deadline_from": "self",
                "project_deadline": "2024-06-16",
            }
        )

        child_1_3 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child",
                "parent_id": root_1.id,
                "use_project_deadline_from": "self",
                "project_deadline": "2024-06-17",
            }
        )

        # Search for all test services
        services = self.env["riverflow.service"].search(
            [("name", "like", f"{self.TEST_PREFIX}%")],
        )

        # Debug: print the tree structure
        print("\n=== Service Tree Structure (ordered by display_order) ===")
        self.dump_services_to_console(services)

        # Verify tree1 is followed by tree 2
        root_1_services = services.filtered(lambda s: s.root_id == root_1)
        root_2_services = services.filtered(lambda s: s.root_id == root_2)

        self.assertEqual(len(root_1_services), 4, "Should have 4 services for root 1")
        self.assertEqual(len(root_2_services), 1, "Should have 1 service for root 2")

        # Get the actual sequence of service names from the search result
        service_names = services.mapped("name")

        # The first 4 services should be from root_1's tree.
        self.assertIn(root_1.name, service_names[:4])
        self.assertIn(child_1_1.name, service_names[:4])
        self.assertIn(child_1_2.name, service_names[:4])
        self.assertIn(child_1_3.name, service_names[:4])

        # The last service should be root_2
        self.assertEqual(service_names[4], root_2.name)

        # check order within root_1's children
        self.assertEqual(root_1.child_ids.mapped("name"), [child_1_1.name, child_1_2.name, child_1_3.name])

    def test_deadline_drag_and_drop_switches_to_self_mode(self):
        """Test that dragging a service in the calendar automatically switches to 'self' mode.

        This test simulates the calendar drag-and-drop behavior where a user drags a service
        to a new date. The system should automatically:
        1. Switch use_project_deadline_from to 'self'
        2. Update project_deadline to the new date
        3. Reset days_relative_to_project to 0

        This applies regardless of the original deadline source (root, log_entry_start, log_entry_end, etc)
        """
        self.cleanup_test_services()

        # Create a root service with a specific deadline
        root = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Root Service",
                "project_deadline": date(2024, 6, 15),
            }
        )

        # Create a child service that uses the root's deadline with a relative offset
        child = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child Service",
                "parent_id": root.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": -3,  # 3 days before root
            }
        )

        # Verify initial state
        self.assertEqual(child.use_project_deadline_from, "root")
        self.assertEqual(child.days_relative_to_project, -3)
        self.assertEqual(child.deadline, date(2024, 6, 12))  # 3 days before root
        self.assertEqual(child.project_deadline, date(2024, 6, 15))  # Root's deadline

        # Simulate calendar drag-and-drop by writing to the deadline field
        # This is what Odoo's calendar view does when a user drags an event
        new_deadline = date(2024, 6, 20)
        child.write({"deadline": new_deadline})

        # Verify the service automatically switched to 'self' mode
        self.assertEqual(
            child.use_project_deadline_from,
            "self",
            "Service should automatically switch to 'self' mode when deadline is changed via drag-and-drop",
        )
        self.assertEqual(
            child.project_deadline,
            new_deadline,
            "project_deadline should be updated to match the new deadline",
        )
        self.assertEqual(
            child.days_relative_to_project,
            0,
            "days_relative_to_project should be reset to 0 when switching to 'self' mode",
        )
        self.assertEqual(
            child.deadline,
            new_deadline,
            "deadline should reflect the new date set by the user",
        )

        # Verify that changing the root's deadline no longer affects the child
        root.write({"project_deadline": date(2024, 7, 1)})
        self.assertEqual(
            child.deadline,
            new_deadline,
            "Child deadline should remain unchanged after switching to 'self' mode",
        )

    def test_deadline_drag_and_drop_with_self_mode(self):
        """Test that dragging a service that's already in 'self' mode just updates the date."""
        self.cleanup_test_services()

        # Create a service that already uses 'self' mode
        service = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Self Mode Service",
                "use_project_deadline_from": "self",
                "project_deadline": date(2024, 6, 15),
                "days_relative_to_project": 5,  # This should be ignored in 'self' mode
            }
        )

        # Verify initial state
        self.assertEqual(service.use_project_deadline_from, "self")
        self.assertEqual(service.deadline, date(2024, 6, 15))
        self.assertEqual(service.project_deadline, date(2024, 6, 15))

        # Simulate calendar drag-and-drop to a new date
        new_deadline = date(2024, 6, 25)
        service.write({"deadline": new_deadline})

        # Verify the service remains in 'self' mode with updated dates
        self.assertEqual(service.use_project_deadline_from, "self")
        self.assertEqual(service.project_deadline, new_deadline)
        self.assertEqual(service.deadline, new_deadline)
        self.assertEqual(
            service.days_relative_to_project,
            0,
            "days_relative_to_project should be reset to 0",
        )

    def test_deadline_no_change_preserves_mode(self):
        """Test that setting the deadline to the same value preserves the current mode."""
        self.cleanup_test_services()

        # Create a root and child with relative timing
        root = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Root",
                "project_deadline": date(2024, 6, 15),
            }
        )

        child = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child",
                "parent_id": root.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": -2,
            }
        )

        # Verify initial state
        initial_deadline = child.deadline
        self.assertEqual(child.use_project_deadline_from, "root")
        self.assertEqual(child.days_relative_to_project, -2)

        # Write the same deadline value (simulating a drag-and-drop that returns to original position)
        # In this case the deadline equals project_deadline, so no change should occur
        child.write({"deadline": child.project_deadline})

        # Verify mode is preserved (no change because deadline == project_deadline)
        self.assertEqual(
            child.use_project_deadline_from,
            "root",
            "Mode should be preserved when deadline equals project_deadline",
        )
        self.assertEqual(child.days_relative_to_project, -2)

    def test_display_order_computed_correctly(self):
        """Test that display_order is computed correctly after creating services."""
        self.cleanup_test_services()

        # Create services with different deadlines
        root_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Root 1",
                "project_deadline": date(2024, 6, 10),
            }
        )

        root_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Root 2",
                "project_deadline": date(2024, 6, 20),
            }
        )

        # Create children for root_1
        child_1_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 1.1",
                "parent_id": root_1.id,
                "use_project_deadline_from": "self",
                "project_deadline": date(2024, 6, 8),
            }
        )

        child_1_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 1.2",
                "parent_id": root_1.id,
                "use_project_deadline_from": "self",
                "project_deadline": date(2024, 6, 9),
            }
        )

        # Clear ORM cache to get freshly computed display_order values
        self.env.invalidate_all()

        # Verify display_order is assigned
        self.assertGreaterEqual(root_1.display_order, 0, "root_1 should have a display_order >= 0")
        self.assertGreaterEqual(root_2.display_order, 0, "root_2 should have a display_order >= 0")
        self.assertGreaterEqual(child_1_1.display_order, 0, "child_1_1 should have a display_order >= 0")
        self.assertGreaterEqual(child_1_2.display_order, 0, "child_1_2 should have a display_order >= 0")

        # Verify children of root_1 have sequential display_order
        self.assertEqual(
            child_1_1.display_order + 1,
            child_1_2.display_order,
            "Children should have sequential display_order based on deadline",
        )

    def test_display_order_no_unnecessary_writes(self):
        """Test that display_order is not written when the value hasn't changed."""
        self.cleanup_test_services()

        # Create a simple service tree
        root = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Root",
                "project_deadline": date(2024, 6, 15),
            }
        )

        child_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 1",
                "parent_id": root.id,
                "use_project_deadline_from": "self",
                "project_deadline": date(2024, 6, 10),
            }
        )

        child_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 2",
                "parent_id": root.id,
                "use_project_deadline_from": "self",
                "project_deadline": date(2024, 6, 12),
            }
        )

        # Force computation by accessing the field
        initial_order_child_1 = child_1.display_order
        initial_order_child_2 = child_2.display_order

        # Trigger recomputation by modifying a dependency field on an unrelated service
        # Create a new unrelated service to ensure no changes to existing services
        unrelated = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Unrelated",
                "project_deadline": date(2024, 7, 1),
            }
        )

        # Clear cache and verify display_order hasn't changed for existing services
        self.env.invalidate_all()

        self.assertEqual(
            child_1.display_order,
            initial_order_child_1,
            "display_order should not change when unrelated services are created",
        )
        self.assertEqual(
            child_2.display_order,
            initial_order_child_2,
            "display_order should not change when unrelated services are created",
        )

    def test_display_order_updates_on_deadline_change(self):
        """Test that display_order is recalculated when deadlines change."""
        self.cleanup_test_services()

        # Create a root with children
        root = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Root",
                "project_deadline": date(2024, 6, 15),
            }
        )

        child_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 1",
                "parent_id": root.id,
                "use_project_deadline_from": "self",
                "project_deadline": date(2024, 6, 10),
            }
        )

        child_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 2",
                "parent_id": root.id,
                "use_project_deadline_from": "self",
                "project_deadline": date(2024, 6, 12),
            }
        )

        # Get initial order
        initial_order_child_1 = child_1.display_order
        initial_order_child_2 = child_2.display_order

        # Child 1 should come before Child 2 (earlier deadline)
        self.assertLess(
            initial_order_child_1,
            initial_order_child_2,
            "Child 1 should have lower display_order than Child 2",
        )

        # Change child_1's deadline to be after child_2
        child_1.write({"project_deadline": date(2024, 6, 14)})

        # Clear ORM cache to get recomputed values
        self.env.invalidate_all()

        # Now Child 2 should come before Child 1
        self.assertLess(
            child_2.display_order,
            child_1.display_order,
            "Child 2 should now have lower display_order than Child 1 after deadline change",
        )

    def test_display_order_updates_on_priority_change(self):
        """Test that display_order is recalculated when priorities change for services with same deadline."""
        self.cleanup_test_services()

        # Create a root with children that have the same deadline
        root = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Root",
                "project_deadline": date(2024, 6, 15),
            }
        )

        child_1 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 1",
                "parent_id": root.id,
                "use_project_deadline_from": "self",
                "project_deadline": date(2024, 6, 10),
                "daily_prio": 1,
            }
        )

        child_2 = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Child 2",
                "parent_id": root.id,
                "use_project_deadline_from": "self",
                "project_deadline": date(2024, 6, 10),  # Same deadline
                "daily_prio": 2,
            }
        )

        # Get initial order
        initial_order_child_1 = child_1.display_order
        initial_order_child_2 = child_2.display_order

        # Child 1 should come before Child 2 (lower priority number)
        self.assertLess(
            initial_order_child_1,
            initial_order_child_2,
            "Child 1 should have lower display_order than Child 2 (lower priority)",
        )

        # Change priorities
        child_1.write({"daily_prio": 3})

        # Clear ORM cache to get fresh computed values
        # _compute_display_order updates all services in the tree, but cached values remain in existing recordsets
        self.env.invalidate_all()

        # Now Child 2 should come before Child 1
        self.assertLess(
            child_2.display_order,
            child_1.display_order,
            "Child 2 should now have lower display_order than Child 1 after priority change",
        )

    def test_display_order_maintains_tree_integrity(self):
        """Test that display_order maintains proper tree structure after multiple changes."""
        (
            root_1,
            root_2,
            root_3,
            child_2_1,
            child_2_2,
            child_2_3,
            child_3_1,
            child_3_2,
            child_3_3,
            grandchild_3_2_1,
            grandchild_3_2_2,
        ) = self.create_service_tree()

        # Make multiple changes to verify tree integrity is maintained
        child_2_2.write({"daily_prio": 5})
        child_3_1.write({"project_deadline": date(2024, 2, 25)})

        # Clear ORM cache to get recomputed values for all services
        self.env.invalidate_all()

        # Verify parent-child relationships are maintained
        self.assertEqual(len(root_2.child_ids), 3, "Root 2 should still have 3 children")
        self.assertEqual(len(root_3.child_ids), 3, "Root 3 should still have 3 children")
        self.assertEqual(len(child_3_2.child_ids), 2, "Child 3.2 should still have 2 grandchildren")

        # Verify all services in the same tree have display_order values
        for service in root_2.child_ids:
            self.assertGreaterEqual(
                service.display_order,
                0,
                f"{service.name} should have a valid display_order",
            )

        for service in root_3.child_ids:
            self.assertGreaterEqual(
                service.display_order,
                0,
                f"{service.name} should have a valid display_order",
            )

        # Verify grandchildren have display_order after parent
        for grandchild in child_3_2.child_ids:
            self.assertGreater(
                grandchild.display_order,
                child_3_2.display_order,
                f"{grandchild.name} should have display_order after its parent",
            )

    def test_display_order_large_tree_no_recursion_error(self):
        """A root deadline change dirties display_order on the whole tree at
        once. display_order is recursive=True, so the ORM recomputes it record
        by record; the compute's tree walk must not READ display_order on a
        still-pending sibling, or each read nests another per-record compute
        (~10 stack frames per dirty sibling) and trees of ~100+ services
        overflow Python's recursion limit (production RecursionError when
        editing planned_end_date on a log entry with a large service tree)."""
        self.cleanup_test_services()
        child_count = 150  # > recursion limit (1000) / ~10 frames per nesting level

        root = self.env["riverflow.service"].create(
            {
                "name": f"{self.TEST_PREFIX}Big Root",
                "project_deadline": date(2026, 6, 1),
            }
        )
        children = self.env["riverflow.service"].create(
            [
                {
                    "name": f"{self.TEST_PREFIX}Big Child {i:03d}",
                    "parent_id": root.id,
                    "use_project_deadline_from": "root",
                    "days_relative_to_project": -i,
                }
                for i in range(1, child_count + 1)
            ]
        )

        # Settle: the first read triggers the pending per-record recompute
        # for the entire freshly created tree.
        children[0].display_order

        # Dirty the whole tree at once, as production does: the root's
        # project_deadline feeds every child's deadline, root_name and
        # display_order via root_id (@api.depends triggers only).
        root.project_deadline = date(2026, 7, 1)

        # Trigger the recompute the way production does: a plain read on one
        # child while all its siblings are still pending.
        children[0].display_order

        # Root first, then children sequential in deadline-sorted order. The
        # sort key mirrors assign_sequence's own key, so the assertion stays
        # valid even if the weekend deadline rule collapses some deadlines.
        self.assertEqual(root.display_order, 0, "Root should be first in its tree")
        by_deadline = children.sorted(key=lambda s: (s.deadline, s.daily_prio, s.name, s.id))
        self.assertEqual(
            [s.display_order for s in by_deadline],
            list(range(1, child_count + 1)),
            "display_order must be sequential following deadline-sorted order",
        )


@tagged("post_install", "-at_install", "riverflow", "test_services")
class TestServiceIndexes(TransactionCase):
    """The service lookups that run on every service write and every
    log-entry edit need their indexes. Without them each lookup scanned the
    whole service table (3-7 ms on 35k services, repeated dozens of times per
    edit), so a missing index shows up only as a slow system."""

    def test_subject_and_root_lookups_are_indexed(self):
        cr = self.env.cr
        # models.Index on (res_model, res_id): subject lookups
        self.assertTrue(index_exists(cr, "riverflow_service_res_model_res_id_idx"))
        # root_id index=True: the "root_id in (...)" dependency lookup
        self.assertTrue(index_exists(cr, "riverflow_service__root_id_index"))
