"""famulus — a self-hosted personal AI assistant on WhatsApp."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("famulus")  # pyproject.toml is the one place it is set
except PackageNotFoundError:  # running from a bare source tree
    __version__ = "0+unknown"
