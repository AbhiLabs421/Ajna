"""Model transport built on urllib only.

Three wire protocols are supported behind one interface: Ollama's native
NDJSON chat API, the OpenAI chat-completions SSE API (which also covers Groq,
Together, OpenRouter, vLLM, LM Studio, ...), and Anthropic's messages API.

`stream()` yields normalised events so the agent loop never sees protocol
differences:

    ("text", str)        assistant prose
    ("thinking", str)    reasoning trace, when the model exposes one
    ("tool_calls", [ToolCall])
    ("usage", {"in": int, "out": int})
"""

import json
import time
import urllib.error
import urllib.request

RETRY_STATUS = (408, 409, 429, 500, 502, 503, 504, 529)


class ProviderError(Exception):
    """A request failed in a way the agent loop should surface to the user."""


class ToolCall:
    __slots__ = ("id", "name", "args")

    def __init__(self, id, name, args):
        self.id = id
        self.name = name
        self.args = args if isinstance(args, dict) else _loads(args)

    def __repr__(self):
        return "ToolCall(%s, %r)" % (self.name, self.args)


def _loads(raw):
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {"value": val}
    except (ValueError, TypeError):
        return {}


class Client:
    def __init__(self, cfg):
        self.cfg = cfg
        self.kind = cfg.kind
        self.base = cfg.base_url
        self.key = cfg.api_key
        self.timeout = cfg.timeout
        self.usage = {"in": 0, "out": 0, "requests": 0}

    # -- public ---------------------------------------------------------

    def stream(self, messages, tools=None, model=None, cancel=None,
               temperature=None, max_tokens=None):
        """Yield normalised streaming events for one assistant turn."""
        model = model or self.cfg.model
        url, body, headers = self._build(
            messages, tools, model, True,
            self.cfg.temperature if temperature is None else temperature,
            self.cfg.max_tokens if max_tokens is None else max_tokens,
        )
        resp = self._request(url, body, headers)
        parser = {
            "ollama": self._parse_ollama,
            "openai": self._parse_openai,
            "anthropic": self._parse_anthropic,
        }[self.kind]
        try:
            for event in parser(resp, cancel):
                if event[0] == "usage":
                    self.usage["in"] += event[1].get("in", 0)
                    self.usage["out"] += event[1].get("out", 0)
                yield event
        finally:
            self.usage["requests"] += 1
            try:
                resp.close()
            except Exception:
                pass

    def complete(self, messages, model=None, max_tokens=1024, temperature=0.0):
        """Non-streaming helper used for compaction and session titles."""
        parts = []
        for kind, payload in self.stream(
            messages, tools=None, model=model,
            temperature=temperature, max_tokens=max_tokens,
        ):
            if kind == "text":
                parts.append(payload)
        return "".join(parts)

    def models(self):
        """Best-effort model listing; returns [] when unsupported."""
        paths = {"ollama": "/api/tags", "openai": "/v1/models",
                 "anthropic": "/v1/models"}
        req = urllib.request.Request(
            self.base + paths[self.kind], headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            return []
        items = data.get("models") or data.get("data") or []
        names = []
        for item in items:
            if isinstance(item, str):
                names.append(item)
            else:
                names.append(item.get("name") or item.get("model") or item.get("id"))
        return sorted(n for n in names if n)

    # -- transport ------------------------------------------------------

    def _headers(self):
        headers = {"Content-Type": "application/json",
                   "User-Agent": "ajna/0.1", "Accept": "*/*"}
        if self.kind == "anthropic":
            headers["x-api-key"] = self.key
            headers["anthropic-version"] = "2023-06-01"
        elif self.key:
            headers["Authorization"] = "Bearer " + self.key
        return headers

    def _request(self, url, body, headers, attempt=0):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code in RETRY_STATUS and attempt < 3:
                delay = min(2 ** attempt, 8)
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after:
                    try:
                        delay = min(float(retry_after), 20)
                    except ValueError:
                        pass
                time.sleep(delay)
                return self._request(url, body, headers, attempt + 1)
            raise ProviderError("HTTP %d from %s\n%s" % (exc.code, url, detail))
        except urllib.error.URLError as exc:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                return self._request(url, body, headers, attempt + 1)
            raise ProviderError("cannot reach %s: %s" % (url, exc.reason))

    # -- request construction -------------------------------------------

    def _build(self, messages, tools, model, stream, temperature, max_tokens):
        headers = self._headers()
        if self.kind == "ollama":
            body = {
                "model": model,
                "messages": _to_ollama(messages),
                "stream": stream,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            if tools:
                body["tools"] = _openai_tools(tools)
            return self.base + "/api/chat", body, headers

        if self.kind == "openai":
            body = {
                "model": model,
                "messages": _to_openai(messages),
                "stream": stream,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if stream:
                body["stream_options"] = {"include_usage": True}
            if tools:
                body["tools"] = _openai_tools(tools)
            return self.base + "/v1/chat/completions", body, headers

        system, msgs = _to_anthropic(messages)
        body = {
            "model": model,
            "messages": msgs,
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t["name"], "description": t["description"],
                 "input_schema": t["parameters"]}
                for t in tools
            ]
        return self.base + "/v1/messages", body, headers

    # -- response parsing -----------------------------------------------

    def _parse_ollama(self, resp, cancel):
        calls = []
        for line in resp:
            if cancel is not None and cancel.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except ValueError:
                continue
            if chunk.get("error"):
                raise ProviderError(str(chunk["error"]))
            msg = chunk.get("message") or {}
            if msg.get("thinking"):
                yield ("thinking", msg["thinking"])
            if msg.get("content"):
                yield ("text", msg["content"])
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                calls.append(ToolCall(
                    call.get("id") or "call_%d" % len(calls),
                    fn.get("name", ""), fn.get("arguments"),
                ))
            if chunk.get("done"):
                if calls:
                    yield ("tool_calls", calls)
                    calls = []
                yield ("usage", {"in": chunk.get("prompt_eval_count", 0),
                                 "out": chunk.get("eval_count", 0)})
        if calls:
            yield ("tool_calls", calls)

    def _parse_openai(self, resp, cancel):
        acc = {}       # index -> {id, name, args}
        for line in resp:
            if cancel is not None and cancel.is_set():
                break
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if chunk.get("error"):
                raise ProviderError(json.dumps(chunk["error"])[:300])
            if chunk.get("usage"):
                u = chunk["usage"]
                yield ("usage", {"in": u.get("prompt_tokens", 0),
                                 "out": u.get("completion_tokens", 0)})
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    yield ("thinking", delta["reasoning_content"])
                if delta.get("content"):
                    yield ("text", delta["content"])
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
        if acc:
            yield ("tool_calls", [
                ToolCall(s["id"] or "call_%d" % i, s["name"], s["args"])
                for i, s in sorted(acc.items())
            ])

    def _parse_anthropic(self, resp, cancel):
        blocks = {}
        for line in resp:
            if cancel is not None and cancel.is_set():
                break
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            try:
                chunk = json.loads(line[5:].strip())
            except ValueError:
                continue
            etype = chunk.get("type")
            if etype == "content_block_start":
                block = chunk.get("content_block") or {}
                if block.get("type") == "tool_use":
                    blocks[chunk["index"]] = {
                        "id": block.get("id", ""), "name": block.get("name", ""),
                        "args": "",
                    }
            elif etype == "content_block_delta":
                delta = chunk.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta":
                    yield ("text", delta.get("text", ""))
                elif dtype == "thinking_delta":
                    yield ("thinking", delta.get("thinking", ""))
                elif dtype == "input_json_delta":
                    slot = blocks.get(chunk["index"])
                    if slot is not None:
                        slot["args"] += delta.get("partial_json", "")
            elif etype in ("message_delta", "message_start"):
                usage = (chunk.get("usage")
                         or (chunk.get("message") or {}).get("usage") or {})
                if usage:
                    yield ("usage", {"in": usage.get("input_tokens", 0),
                                     "out": usage.get("output_tokens", 0)})
            elif etype == "error":
                raise ProviderError(json.dumps(chunk.get("error", {}))[:300])
        if blocks:
            yield ("tool_calls", [
                ToolCall(s["id"], s["name"], s["args"])
                for _, s in sorted(blocks.items())
            ])


# --- message serialisation --------------------------------------------------

def _openai_tools(tools):
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["parameters"]}}
            for t in tools]


def _to_ollama(messages):
    out = []
    for msg in messages:
        role = msg["role"]
        if role == "tool":
            out.append({"role": "tool", "tool_name": msg.get("name", ""),
                        "content": msg.get("content", "")})
        elif role == "assistant":
            entry = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                entry["tool_calls"] = [
                    {"function": {"name": tc["name"], "arguments": tc["args"]}}
                    for tc in msg["tool_calls"]
                ]
            out.append(entry)
        else:
            out.append({"role": role, "content": msg.get("content") or ""})
    return out


def _to_openai(messages):
    out = []
    for msg in messages:
        role = msg["role"]
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": msg.get("id", ""),
                        "content": msg.get("content", "")})
        elif role == "assistant":
            entry = {"role": "assistant", "content": msg.get("content") or None}
            if msg.get("tool_calls"):
                entry["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"],
                                  "arguments": json.dumps(tc["args"])}}
                    for tc in msg["tool_calls"]
                ]
            out.append(entry)
        else:
            out.append({"role": role, "content": msg.get("content") or ""})
    return out


def _to_anthropic(messages):
    """Anthropic keeps system separate and carries tool results as user blocks."""
    system_parts, out = [], []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            system_parts.append(msg.get("content") or "")
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": msg.get("id", ""),
                     "content": msg.get("content", "")}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif role == "assistant":
            content = []
            if msg.get("content"):
                content.append({"type": "text", "text": msg["content"]})
            for tc in msg.get("tool_calls") or []:
                content.append({"type": "tool_use", "id": tc["id"],
                                "name": tc["name"], "input": tc["args"]})
            out.append({"role": "assistant",
                        "content": content or [{"type": "text", "text": "..."}]})
        else:
            out.append({"role": "user", "content": msg.get("content") or ""})
    return "\n\n".join(p for p in system_parts if p), out
