"""Discovery and resolution of the actions that can be run for a server.

An *action* is one thing the console can execute for a single server: either a
built-in lifecycle command (shelled out to the ``nimc`` CLI) or a per-server
custom script found in ``servers/<slug>/bin/`` (e.g. ``remove-butterflies``).

The HTTP layer only ever passes a server *slug* and an *action id*. Everything
that actually gets executed is resolved here, from a fixed set of built-ins plus
the discovered scripts — never from a client-supplied path. That is the whole
security model: nothing the user types can become a command. Slugs and action
ids are matched against what exists on disk; anything else is a 404.
"""

import os
import re

from pydantic import BaseModel

from nimc.web.settings import settings

# A slug is a directory name under ``servers/``. Keep it strict so it can never
# escape that directory or smuggle anything into a command line.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Action(BaseModel):
    """One runnable action for a server."""

    id: str
    label: str
    argv: list[str]
    # Lifecycle commands (create/destroy) should keep running if the browser
    # disconnects; a log tail should be killed with the viewer.
    kill_on_disconnect: bool = False
    # Ask the user to confirm before running (destructive actions).
    confirm: bool = False


# Built-in lifecycle actions, in display order. ``argv`` is filled in per-server
# as ``<nimc> <id> <slug>``.
_BUILTINS: list[tuple[str, str, bool, bool]] = [
    # id,        label,        kill_on_disconnect, confirm
    ("create", "Create", False, False),
    ("status", "Status", False, False),
    ("tail-logs", "Tail Logs", True, False),
    ("destroy", "Destroy", False, True),
]


def _nimc_command() -> list[str]:
    """The base command used to invoke the CLI (e.g. ``nimc`` or ``uv run nimc``)."""
    return os.environ.get("NIMC_CONSOLE_COMMAND", "nimc").split()


def _humanize(name: str) -> str:
    """``remove-butterflies`` -> ``Remove Butterflies``."""
    return name.replace("-", " ").replace("_", " ").title()


def is_valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug))


def server_exists(slug: str) -> bool:
    return (
        is_valid_slug(slug) and (settings.servers_dir / slug / "server.toml").is_file()
    )


def list_servers() -> list[str]:
    """Slugs of all configured servers (directories with a ``server.toml``)."""
    base = settings.servers_dir
    if not base.is_dir():
        return []
    return sorted(
        p.name
        for p in base.iterdir()
        if is_valid_slug(p.name) and (p / "server.toml").is_file()
    )


def _custom_actions(slug: str) -> list[Action]:
    """Executable scripts in ``servers/<slug>/bin/`` become per-server actions."""
    bin_dir = settings.servers_dir / slug / "bin"
    if not bin_dir.is_dir():
        return []
    actions: list[Action] = []
    for f in sorted(bin_dir.iterdir()):
        if f.is_file() and os.access(f, os.X_OK):
            actions.append(
                Action(id=f.name, label=_humanize(f.name), argv=[str(f.resolve())])
            )
    return actions


def list_actions(slug: str) -> list[Action]:
    """All actions for a server: built-in lifecycle commands then custom scripts.

    Raises ``KeyError`` if the server does not exist.
    """
    if not server_exists(slug):
        raise KeyError(slug)
    nimc = _nimc_command()
    builtins = [
        Action(
            id=id_,
            label=label,
            argv=[*nimc, id_, slug],
            kill_on_disconnect=kod,
            confirm=confirm,
        )
        for id_, label, kod, confirm in _BUILTINS
    ]
    return builtins + _custom_actions(slug)


def resolve(slug: str, action_id: str) -> Action:
    """Resolve a (slug, action_id) pair to a concrete ``Action``.

    Raises ``KeyError`` if the server or action is unknown.
    """
    for action in list_actions(slug):
        if action.id == action_id:
            return action
    raise KeyError(action_id)
