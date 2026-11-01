"""/health says which release is running: the deploy checks it before it trusts a switch."""
import asyncio
from importlib.metadata import version

from famulus import __version__
from famulus import main as fmain


def test_health_reports_the_installed_version():
    body = asyncio.run(fmain.health())
    assert body["ok"] is True
    assert body["version"] == __version__ == version("famulus")
