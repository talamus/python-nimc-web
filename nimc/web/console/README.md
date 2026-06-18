# `nimc.web.console`

Run server-management scripts from the browser. Streams a script's stdout
(ANSI colours intact) over Server-Sent Events and lets the user stop it.

This is the productionised version of the `main.py` streaming prototype. The
two important differences:

- **No arbitrary paths.** The API takes a server *slug* and an *action id*, and
  resolves them against a fixed registry (built-in lifecycle commands + custom
  scripts discovered on disk). Nothing the client sends becomes a command.
- **Survives multiple workers.** Each run is its own process group, with a
  pidfile, so `/stop` works no matter which gunicorn worker handles it, and the
  whole `nimc → ssh/curl` subtree gets signalled at once.

## Integrate (one call)

In `nimc/web/app.py`, after the templates are configured:

```python
from .console import init_console

init_console(app, templates)
```

That mounts `/console-static`, makes `console.html` includable from your
templates, and registers the router behind `get_current_user_or_fail`.

## Use in a page

Load the assets once (e.g. in `base.html`'s `extra_scripts` block):

```html
<link rel="stylesheet" href="/console-static/css/console.css">
<script type="module" src="/console-static/js/console.js"></script>
```

Then render a panel per server — either lazily over HTMX:

```html
<div hx-get="/console/hasturian/panel" hx-trigger="load"></div>
```

or inline in a template you already have the actions for:

```python
actions = registry.list_actions(slug)  # pass to the template context
```

```html
{% with slug=slug, actions=actions %}{% include "console.html" %}{% endwith %}
```

## Actions

For a server `hasturian`:

| id | source | command |
| --- | --- | --- |
| `create`, `status`, `tail-logs`, `destroy` | built-in | `nimc <id> hasturian` |
| anything in `servers/hasturian/bin/` (executable) | discovered | the script itself |

`destroy` asks for confirmation; `tail-logs` is killed when its viewer
disconnects; lifecycle commands keep running if the browser goes away.

## Endpoints

| method | path | purpose |
| --- | --- | --- |
| `GET` | `/console/{slug}/panel` | render the panel partial |
| `GET` | `/console/{slug}/run/{action_id}` | SSE stream of the run |
| `POST` | `/console/{slug}/stop/{run_id}` | signal the run (default SIGINT) |

## Configuration (env)

- `NIMC_CONSOLE_COMMAND` — base CLI command, default `nimc` (e.g. `uv run nimc`).
- `NIMC_CONSOLE_RUNTIME_DIR` — where pidfiles live, default a temp dir.

Servers are read from `settings.servers_dir`.
