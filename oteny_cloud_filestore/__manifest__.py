{
    "name": "Cloud Filestore",
    "version": "19.0.1.0",
    "category": "Tools",
    "summary": "Keep the contents of user files in an S3 bucket",
    "description": """
    Stores the contents of user files (attachments) in an S3 bucket, so
    that a database copy and the files it points to stay together.
    """,
    "author": "Oteny",
    "depends": ["base", "mail", "web"],
    "external_dependencies": {"python": ["boto3"]},
    "data": [],
    "installable": True,
    "application": False,
    "license": "OEEL-1",
}
