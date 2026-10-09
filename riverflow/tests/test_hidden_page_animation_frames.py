"""The animation frame fallback for a hidden page on a local server loads before
OWL (radar pipeline applicants plan, R5 follow-up; Ries, 9-Oct-2026).

OWL captures window.requestAnimationFrame once, when owl.js is evaluated, so the
fallback only works when its script comes first in web._assets_core. The script
itself is plain JavaScript; its behaviour is proven in the browser (the agent's
hidden pane), this test guards the load order, which a bundle change could break
without any visible sign.
"""

from odoo.tests import TransactionCase, tagged
from odoo.tools.misc import file_path

SCRIPT = "riverflow/static/src/core/hidden_page_animation_frames.js"


@tagged("riverflow", "post_install", "-at_install", "test_hidden_page_animation_frames")
class TestHiddenPageAnimationFrames(TransactionCase):

    def test_fallback_loads_before_owl(self):
        paths = [path for _addon, path, *_rest in self.env["ir.asset"]._get_asset_paths("web._assets_core", {})]
        script = next((i for i, path in enumerate(paths) if path.endswith(SCRIPT)), None)
        owl = next(i for i, path in enumerate(paths) if path.endswith("web/static/lib/owl/owl.js"))
        self.assertIsNotNone(script, "the fallback is in web._assets_core")
        self.assertLess(script, owl, "the fallback must run before owl.js captures requestAnimationFrame")

    def test_fallback_is_local_only(self):
        """The script acts only on a page served from localhost: production and
        the odoo.sh copies keep the browser's behaviour for hidden tabs."""
        with open(file_path(SCRIPT)) as script:
            source = script.read()
        self.assertIn('const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]", "::1"];', source)
        self.assertIn("!LOCAL_HOSTS.includes(window.location.hostname)", source)
