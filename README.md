# moo-mcp

MCP server for LambdaMOO administration. Connects to a MOO's admin/wizard port over TCP, keeps the connection warm, and exposes structured tools for the operations you actually do when hacking on a MOO: eval expressions, list/program verbs (with auto read-back verification), add/remove properties, walk the inheritance chain.

Use with [Claude Code](https://claude.com/claude-code) or any other MCP-compatible client.

## Why

The usual MOO admin loop is "telnet in, run a command, read the response, repeat" - slow and lossy when scripted, and `@program` famously prints "0 errors" even when the verb body didn't actually get stored as you expected. `moo-mcp` wraps that loop in a persistent process with proper request/response semantics and adds an automatic read-back verify on every verb program so silent corruption is impossible.

## Status

Alpha. Tested against LambdaMOO 1.8.3-Mongoose. Should work against any MOO whose admin port speaks the standard `;eval`, `@list`, `@program ... .` protocol.

## Install

```bash
pip install moo-mcp
```

Or from source:

```bash
git clone https://github.com/<org>/moo-mcp
cd moo-mcp
pip install -e .
```

## Configure

`moo-mcp` is **config-free out of the box** - you must provide connection details via environment variables. There are no defaults pointing at any particular MOO.

| Env var | Required | Description |
|---|---|---|
| `MOO_HOST` | yes | Hostname or IP of the MOO admin port |
| `MOO_PORT` | yes | TCP port (e.g. 7777, 3500) |
| `MOO_USER` | yes | Wizard character name |
| `MOO_PASS` | yes | Wizard password |
| `MOO_TIMEOUT` | no | Seconds to wait for a response (default 15) |
| `MOO_RECONNECT` | no | `true`/`false`, auto-reconnect on drop (default `true`) |
| `MOO_TLS` | no | `true`/`false`, wrap the connection in TLS (default `false`). Use this when the MOO is fronted by stunnel or otherwise serves the admin port over TLS. |
| `MOO_TLS_INSECURE` | no | `true`/`false`, skip TLS certificate validation (default `false`). Only enable for self-signed certs on a MOO you control. |
| `MOO_USER_<NAME>` / `MOO_PASS_<NAME>` | no | Credentials for an alternate player identity, usable via `send_command(as_player="<name>")`. `<NAME>` is matched case-insensitively against `as_player`. Never pass a password through a tool call - only identities pre-configured this way are usable. |

## Use with Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "moo": {
      "command": "python",
      "args": ["-m", "moo_mcp"],
      "env": {
        "MOO_HOST": "your.moo.example.com",
        "MOO_PORT": "7777",
        "MOO_USER": "Wizard",
        "MOO_PASS": "supersecret"
      }
    }
  }
}
```

Or run directly:

```bash
MOO_HOST=... MOO_PORT=... MOO_USER=... MOO_PASS=... python -m moo_mcp
```

## Tools

| Tool | Description |
|---|---|
| `eval` | Evaluate a MOO expression; returns parsed value or structured error |
| `send_command` | Inject a literal top-level command (not eval), optionally as an alternate identity; best-effort idle-timeout capture |
| `list_verb` | Read a verb body; returns header + numbered lines |
| `program_verb` | Replace a verb body; **auto-verifies** by re-listing and diffing |
| `add_verb` / `remove_verb` / `chmod_verb` | Verb lifecycle |
| `add_property` / `remove_property` / `set_property` | Property lifecycle |
| `has_verb` / `has_property` | Check existence (returns the defining-object list) |
| `verbs` / `properties` | List local verbs/properties on an object |
| `parent` / `chparent` | Read or change an object's parent |

All tools return JSON results. MOO errors (E_PROPNF, traceback) become structured `{error: {message, traceback}}` objects rather than raw text.

## Security

`moo-mcp` is a wizard-privileged admin tool, equivalent in capability to a logged-in human wizard at the MOO admin port. Anyone who can call its tools can do anything a wizard can. Run it in a trust boundary that matches.

Specific notes:

- **The stock MOO admin protocol is plaintext.** Username and password are sent over the TCP socket unencrypted, the same way a `telnet` session would. If your MOO is not on the same host as the MCP and not already TLS-wrapped, either: (a) set `MOO_TLS=true` if your MOO is fronted by stunnel or otherwise serves the admin port over TLS, or (b) run the MCP through an SSH tunnel to the MOO host.
- **`eval_moo` gives full wizard eval access.** This is by design. Do not expose this MCP to untrusted clients.
- **`program_verb` rejects bare-dot lines** in the body (they would terminate the `@program` block and let the rest land in the command parser). If you need a literal `.` line in MOO code, indent it.
- **`add_verb` validates the verb arg-spec** (`dobj` / `iobj` must be `this`/`any`/`none`, `prep` is allowlist-character-checked) to prevent breaking out of the MOO double-quoted literal during eval splice.
- **`set_property` / `add_property` value serializer** escapes backslashes, double quotes, carriage returns, line feeds, and NUL bytes in string values so a malicious property value cannot break out of the MOO source.
- **Connection config rejects CR/LF/NUL in `MOO_USER` / `MOO_PASS`** (and `MOO_USER_<NAME>` / `MOO_PASS_<NAME>`) to prevent command injection at login time.
- **`send_command` bypasses `eval_moo`'s marker-delimited framing entirely.** Real command output isn't self-delimiting, so capture is a best-effort idle-timeout heuristic - it can include unrelated broadcast/forked-task output that happens to arrive in the same window, and it does not auto-retry on connection failure (unlike other tools), since retrying a real command risks double-executing it.
- **`as_player` identities are allowlisted by construction.** They're resolved server-side from `MOO_USER_<NAME>`/`MOO_PASS_<NAME>` env vars only - a client can select among identities the operator pre-configured, never supply new credentials through a tool call. Secondary-identity logins are **not** eval-verified like the primary connection (we can't assume a non-wizard player has programmer permissions) - only a heuristic scan of the login banner for known rejection phrasing, which is weaker than the primary's verified login and may miss a rejection on a non-stock core.
- **`search_verbs` accepts user-provided regex.** A pathological pattern can cause catastrophic backtracking. Python's `re` has no built-in timeout. Trust the caller, or pre-validate patterns if you're exposing this to untrusted input.
- **No persistent state**: no on-disk caching, no logging of passwords, no telemetry. Connection credentials live only in env vars; in-flight commands live in process memory.

## License

MIT
