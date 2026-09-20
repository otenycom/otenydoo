{
    "name": "Parallel Test Runner",
    "version": "19.0.1.6",
    "author": "Oteny",
    "category": "Technical",
    "summary": "Parallelizes Odoo test execution by cloning the test database and running batches in worker subprocesses",
    "description": """
Patches Odoo's test runner to detect multiple test classes and, when found,
clone the test database and distribute test classes across parallel worker
processes. Each worker runs its assigned batch on its own database clone.

Configuration via environment variables:
- ODOO_TEST_WORKERS: number of parallel workers (default: half CPU count, max 8)
- ODOO_TEST_PARALLEL: auto | always | never (default: auto)
- ODOO_TEST_KEEP_CLONES: true | false (default: false)
""",
    "depends": ["base"],
    "installable": True,
    "auto_install": False,
    "license": "LGPL-3",
}
