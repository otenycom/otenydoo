from odoo.http import request
import ipaddress
import time
import logging
from functools import wraps
import colorsys

_logger = logging.getLogger(__name__)


def is_neutralized_or_development():
    """
    Returns true if the current database is neutralized or if the request is coming from localhost.
    """
    if not request:
        return False

    # only true if the script to neutralize the database was run,
    # not true if Db was created from blank during development
    is_neutralized = request.env["ir.config_parameter"].sudo().get_param("database.is_neutralized", False)

    # Check if the request is coming from local net (normally only in a development environment)
    is_development_network = ipaddress.ip_address(request.httprequest.remote_addr).is_private

    return is_neutralized or is_development_network


def log_execution_time(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        _logger.info(f"Starting {func.__name__}")
        result = func(*args, **kwargs)
        end_time = time.time()
        duration = end_time - start_time
        _logger.info(f"Finished {func.__name__}. Duration: {duration:.2f} seconds")
        return result

    return wrapper


def pdf_preview_action(attachment):
    """The client action that shows a PDF attachment in the riverflow preview
    dialog, with Print, Download and Close (static/src/components/pdf_preview).

    It closes the dialog that returned it, so a wizard button can save and then
    show its PDF. Use it instead of an act_url with target "new": the browser
    blocks that window as a popup (Ries, 5-Nov-2026).
    """
    return {
        "type": "ir.actions.client",
        "tag": "riverflow_pdf_preview",
        "params": {"attachment_id": attachment.id, "title": attachment.name},
    }
