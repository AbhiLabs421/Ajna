"""Minimal MCP (Model Context Protocol) client over stdio.

Enough of the protocol to be useful: initialize, tools/list, tools/call.
Transport is newline-delimited JSON-RPC on a subprocess's stdin/stdout, which
is what the overwhelming majority of MCP servers speak.

Configure servers in ~/.ajna/config.json:

    "mcp_servers": {
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "..."}
      }
    }

Discovered tools are registered as `<server>_<tool>`. Servers are started only
when at least one is configured, so an empty config costs nothing at startup.
"""

import json
import os
import subprocess
import threading
import time

from . import ui
from .tools import Result, register

PROTOCOL_VERSION = "2024-11-05"
CALL_TIMEOUT = 120


class MCPError(Exception):
    pass


class Server:
    """One MCP server subprocess."""

    def __init__(self, name, spec):
        self.name = name
        self.command = spec.get("command")
        self.args = list(spec.get("args") or [])
        self.env = dict(spec.get("env") or {})
        self.cwd = spec.get("cwd")
        self.proc = None
        self.tools = []
        self._id = 0
        self._lock = threading.Lock()
        self._stderr = []

    # -- lifecycle ------------------------------------------------------

    def start(self, timeout=30):
        if not self.command:
            raise MCPError("server %r has no command" % self.name)

        env = dict(os.environ)
        env.update({k: str(v) for k, v in self.env.items()})
        # Servers that print banners to stdout break the protocol; most respect this.
        env.setdefault("NO_COLOR", "1")

        try:
            self.proc = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=self.cwd, env=env,
                bufsize=0,
                # npx and friends are batch files on Windows.
                shell=(os.name == "nt" and not os.path.isabs(self.command)),
            )
        except OSError as exc:
            raise MCPError("cannot start %r: %s" % (self.name, exc))

        threading.Thread(target=self._drain_stderr, daemon=True).start()

        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "ajna", "version": "0.1.0"},
        }, timeout=timeout)
        self._notify("notifications/initialized", {})

        result = self._request("tools/list", {}, timeout=timeout)
        self.tools = result.get("tools") or []
        return self.tools

    def stop(self):
        if not self.proc:
            return
        proc, self.proc = self.proc, None
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        # The stderr drain thread holds a handle; close both pipes explicitly
        # so the interpreter does not warn about leaking them.
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except Exception:
                pass

    def _drain_stderr(self):
        """Keep the pipe from filling, and hold the tail for error reports."""
        try:
            for line in self.proc.stderr:
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    self._stderr.append(text)
                    del self._stderr[:-40]
        except Exception:
            pass

    # -- json-rpc -------------------------------------------------------

    def _send(self, payload):
        blob = (json.dumps(payload) + "\n").encode("utf-8")
        self.proc.stdin.write(blob)
        self.proc.stdin.flush()

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method, params, timeout=CALL_TIMEOUT):
        if not self.proc or self.proc.poll() is not None:
            raise MCPError("%s is not running%s" % (self.name, self._tail()))

        with self._lock:
            self._id += 1
            request_id = self._id
            try:
                self._send({"jsonrpc": "2.0", "id": request_id,
                            "method": method, "params": params})
            except (OSError, ValueError) as exc:
                raise MCPError("%s: write failed: %s" % (self.name, exc))

            deadline = time.time() + timeout
            while time.time() < deadline:
                line = self.proc.stdout.readline()
                if not line:
                    raise MCPError("%s closed the connection%s"
                                   % (self.name, self._tail()))
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue     # non-protocol noise on stdout
                if message.get("id") != request_id:
                    continue     # a notification or an out-of-order reply
                if message.get("error"):
                    raise MCPError("%s: %s" % (
                        self.name, json.dumps(message["error"])[:300]))
                return message.get("result") or {}

        raise MCPError("%s timed out after %ds on %s" % (self.name, timeout, method))

    def _tail(self):
        return ("\nserver stderr:\n  " + "\n  ".join(self._stderr[-6:])
                if self._stderr else "")

    def call(self, tool_name, arguments, timeout=CALL_TIMEOUT):
        return self._request("tools/call",
                             {"name": tool_name, "arguments": arguments or {}},
                             timeout=timeout)


# --- registry integration ---------------------------------------------------

SERVERS = {}


def connect_all(cfg, quiet=False):
    """Start every configured server and register its tools. Returns a report."""
    specs = cfg.get("mcp_servers") or {}
    if not specs:
        return []

    report = []
    for name, spec in specs.items():
        if spec.get("disabled"):
            continue
        server = Server(name, spec)
        try:
            tools = server.start()
        except MCPError as exc:
            if not quiet:
                ui.warn("mcp %s: %s" % (name, exc))
            report.append((name, 0, str(exc)))
            continue
        SERVERS[name] = server
        for tool in tools:
            _register_remote(server, tool)
        report.append((name, len(tools), None))
        if not quiet:
            ui.info("mcp %s: %d tool%s" % (name, len(tools),
                                           "" if len(tools) == 1 else "s"))
    return report


def _register_remote(server, spec):
    """Wrap one remote tool so it looks like any other tool to the agent."""
    remote_name = spec.get("name") or ""
    if not remote_name:
        return
    local_name = "%s_%s" % (server.name, remote_name)
    schema = spec.get("inputSchema") or {"type": "object", "properties": {}}
    description = (spec.get("description") or
                   "Tool %r from the %s MCP server." % (remote_name, server.name))

    def run(ctx, _server=server, _remote=remote_name, _local=local_name, **kwargs):
        if not ctx.permit("mcp", _local, ui.c("  " + _local + " " +
                                              json.dumps(kwargs)[:200], "byellow")):
            return Result.fail("user declined the call to %s" % _local)
        try:
            result = _server.call(_remote, kwargs)
        except MCPError as exc:
            return Result.fail(str(exc))
        text = _flatten_content(result)
        if result.get("isError"):
            return Result.fail(text or "the server reported an error")
        return Result(text or "(the server returned no content)",
                      display=ui.c("  %d chars from %s" % (len(text), _server.name),
                                   "grey"))

    register(
        local_name,
        description.strip(),
        schema.get("properties") or {},
        required=schema.get("required") or [],
        concurrent=True,
        mutating=True,      # a remote tool may do anything, so it asks first
        summary=lambda a, n=local_name: n,
    )(run)


def _flatten_content(result):
    """MCP returns a list of typed content blocks; render them as text."""
    blocks = result.get("content")
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return json.dumps(result)[:20000] if result else ""

    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(block.get("text", ""))
        elif kind == "resource":
            resource = block.get("resource") or {}
            parts.append(resource.get("text") or resource.get("uri", ""))
        elif kind == "image":
            parts.append("[image: %s, not readable by this model]"
                         % block.get("mimeType", "unknown"))
        else:
            parts.append(json.dumps(block)[:2000])
    return "\n".join(p for p in parts if p)


def shutdown():
    for server in list(SERVERS.values()):
        server.stop()
    SERVERS.clear()
