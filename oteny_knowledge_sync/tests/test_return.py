"""XML-RPC marshalability of sync_skills_to_knowledge: always a dict, never None
(Odoo 19 dumps XML-RPC with allow_none=False)."""
import tempfile
import xmlrpc.client
from pathlib import Path
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from odoo.addons.oteny_knowledge_sync.models.knowledge_sync import KnowledgeSync


@tagged("oteny_knowledge_sync", "post_install", "-at_install")
class TestSyncReturn(TransactionCase):
    def _assert_xmlrpc_dumpable(self, result):
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)
        xmlrpc.client.dumps((result,), allow_none=False)

    @mute_logger("odoo.addons.oteny_knowledge_sync.models.knowledge_sync")
    def test_sync_returns_xmlrpc_dumpable_without_roots(self):
        Sync = self.env["oteny.knowledge.sync"]
        with patch.object(KnowledgeSync, "_skill_roots", return_value=[]):
            result = Sync.sync_skills_to_knowledge()
        self._assert_xmlrpc_dumpable(result)
        self.assertEqual(result["articles_synced"], 0)
        self.assertEqual(result["skipped"], "no_roots")

    def test_sync_returns_xmlrpc_dumpable_summary(self):
        Sync = self.env["oteny.knowledge.sync"]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(KnowledgeSync, "_skill_roots", return_value=[("T", Path(tmp))]),
                patch.object(KnowledgeSync, "_sync_root", return_value=(1, set())),
                patch.object(KnowledgeSync, "_unpublish_roots_not_in"),
                patch.object(KnowledgeSync, "_sort_articles_alphabetically"),
                patch.object(KnowledgeSync, "_rewrite_internal_links"),
            ):
                result = Sync.sync_skills_to_knowledge()
        self._assert_xmlrpc_dumpable(result)
        self.assertEqual(result["articles_synced"], 1)
        self.assertEqual(result["roots"], {"T": 1})
        self.assertNotIn("skipped", result)
