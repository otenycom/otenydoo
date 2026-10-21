{
    "name": "Cloud Filestore",
    "version": "19.0.1.2",
    "category": "Tools",
    "summary": "Keep the contents of user files in an S3 bucket",
    "description": """
    Stores the contents of user files (attachments) in an S3 bucket, so
    that a database copy and the files it points to stay together.
    """,
    "author": "Oteny",
    "depends": ["base", "mail", "web"],
    "external_dependencies": {"python": ["boto3"]},
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/discuss_channel.xml",
        "data/ir_cron.xml",
        "views/cloud_filestore_cache_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "post_load": "post_load",
    "installable": True,
    "application": False,
    "license": "OEEL-1",
}
