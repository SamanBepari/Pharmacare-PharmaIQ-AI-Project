"""
LLM provider abstraction (zero extra dependencies — uses urllib).

  offline    default. No model is called; PharmaIQ uses its rule-based planner and
             extractive answers. Everything still works.
  anthropic  Claude via https://api.anthropic.com/v1/messages
  openai     any OpenAI-compatible /chat/completions server: OpenAI, Ollama, LM Studio,
             vLLM, Groq, Hugging Face TGI … (set PHARMAIQ_BASE_URL)

The LLM is used for exactly two jobs, both behind guard-rails:
  1. PLAN    – turn a question into a structured plan (tool + args, or a SQL string)
  2. EXPLAIN – write a short explanation of rows that were ALREADY fetched

It never executes anything itself.
"""
import json
import re
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


class Provider:
    name = "offline"
    model = ""

    @property
    def available(self) -> bool:
        return False

    @property
    def label(self) -> str:
        return "offline (rule-based)"

    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        raise LLMError("No LLM provider configured.")


def _post(url, headers, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        raise LLMError(f"LLM HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"LLM connection failed: {exc}")
    except json.JSONDecodeError:
        raise LLMError("LLM returned invalid JSON.")


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, settings):
        self.key, self.model, self.timeout = settings.api_key, settings.model, settings.llm_timeout

    @property
    def available(self):
        return bool(self.key and self.model)

    @property
    def label(self):
        return f"anthropic:{self.model}"

    def complete(self, system, user, max_tokens=700):
        data = _post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {"model": self.model, "max_tokens": max_tokens, "temperature": 0, "system": system,
             "messages": [{"role": "user", "content": user}]},
            self.timeout)
        try:
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError):
            raise LLMError("Unexpected response shape from Anthropic API.")


class OpenAICompatProvider(Provider):
    name = "openai"

    def __init__(self, settings):
        self.key, self.model = settings.api_key, settings.model
        self.base, self.timeout = settings.base_url, settings.llm_timeout

    @property
    def available(self):
        # local servers (Ollama / LM Studio) don't need a key
        return bool(self.model and self.base)

    @property
    def label(self):
        return f"openai-compatible:{self.model}"

    def complete(self, system, user, max_tokens=700):
        headers = {"content-type": "application/json"}
        if self.key:
            headers["authorization"] = f"Bearer {self.key}"
        data = _post(f"{self.base}/chat/completions", headers,
                     {"model": self.model, "temperature": 0, "max_tokens": max_tokens,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                     self.timeout)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("Unexpected response shape from the LLM server.")


def get_provider(settings) -> Provider:
    if settings.provider == "anthropic":
        p = AnthropicProvider(settings)
    elif settings.provider == "openai":
        p = OpenAICompatProvider(settings)
    else:
        return Provider()
    return p if p.available else Provider()


def extract_json(text: str):
    """Pull the first balanced {...} object out of an LLM reply (tolerates ``` fences / chatter)."""
    if not text:
        raise LLMError("Empty LLM reply.")
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise LLMError("No valid JSON object in the LLM reply.")
