"""Console subpackage: run server-management scripts from the browser.

Streams a script's stdout (ANSI colours intact) over Server-Sent Events and
lets the user stop it. See ``routes.init_console`` for the integration call.
"""

from .routes import init_console, router

__all__ = ["init_console", "router"]
