import configparser
import os

from ..tools.cloud_store import CloudStoreUnavailable

CONFIG_VARIABLE = "OTENY_CLOUD_FILESTORE_TEST_CONFIG"


class FakeCloudStore:
    """In-memory stand-in for CloudStore, with the same four methods.

    Tests set `down` to simulate an outage, and read `puts` and `deletes`
    to see which names the code under test sent to the store.
    """

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.down = False
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def _check_up(self):
        if self.down:
            raise CloudStoreUnavailable("fake store is down")

    def put(self, name, data):
        self._check_up()
        self.puts.append(name)
        self.objects[name] = data

    def get(self, name):
        self._check_up()
        return self.objects.get(name)

    def head(self, name):
        self._check_up()
        data = self.objects.get(name)
        return None if data is None else len(data)

    def delete(self, name):
        self._check_up()
        self.deletes.append(name)
        self.objects.pop(name, None)


def live_store_config():
    """Read the live test store settings from the INI file named by the
    environment variable; None when the variable or the file is missing."""
    path = os.environ.get(CONFIG_VARIABLE)
    if not path:
        return None
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return None
    parser = configparser.ConfigParser()
    parser.read(path)
    if not parser.has_section("s3"):
        return None
    return dict(parser["s3"])
