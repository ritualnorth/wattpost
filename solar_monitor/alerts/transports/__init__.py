"""Importing any transport module triggers its @register_notification_transport
decorator. The package importer pulls all three in for side effects."""
from . import ntfy        # noqa: F401
from . import discord     # noqa: F401
from . import webhook     # noqa: F401
from . import smtp        # noqa: F401
