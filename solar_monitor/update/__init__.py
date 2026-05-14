"""Self-update *check* — polls the cloud manifest daily and exposes
the result on Settings → About.

v1 is read-only: the daemon learns about new releases but the user
applies them manually (re-flash, or, eventually, the auto-apply
path). v2 will do the atomic-venv-swap auto-apply with Ed25519
signature verification; see BACKLOG.md "Self-update mechanism".
"""
from .checker import UpdateChecker  # noqa: F401
