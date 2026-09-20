{
    "name": "Oteny Backup Trigger",
    "version": "19.0.1.6",
    "summary": "Minimal module to trigger Odoo.sh backups",
    "description": """
        This module exists solely to trigger backups on Odoo.sh.
        
        When we need to create a backup, we increment only this module's version
        and push to the main branch. Odoo.sh will then create a backup before
        performing the module upgrade.
        
        This keeps the version numbers of actual modules stable while still
        leveraging Odoo.sh's automatic backup mechanism.
    """,
    "category": "Hidden",
    "author": "Oteny",
    "depends": ["base"],
    "data": [],
    "installable": True,
    "auto_install": False,
    "application": False,
    "license": "OEEL-1",
}
