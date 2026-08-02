# famulus

**A self-hosted personal AI assistant on WhatsApp — your models, your tools, your rules.**

*Famulus* (Latin): a scholar's — or wizard's — assistant.

Message your own WhatsApp number and get an assistant that runs entirely on
your hardware: a local LLM (via [Ollama](https://ollama.com)) with tools for
web search, weather, and whatever plugins you add — email, home automation,
anything that fits the plugin contract. Anything that *acts* on the world
(sends, publishes, changes state) requires your explicit confirmation first.

```
you  ▶  "do I need an umbrella tomorrow?"
bot  ▶  "No — 21°C and partly cloudy in Amsterdam, 10% rain chance."

you  ▶  "post on LinkedIn that we're hiring"          (with the linkedin plugin)
bot  ▶  "⚠️ Confirmation needed — reply YES to execute, NO to cancel:
         Publish LinkedIn post: ..."
```

## Why this exists

- **Official API, no ban roulette.** Uses Meta's WhatsApp Business Cloud API,
  not a reverse-engineered client.
- **Private by construction.** Your messages go Meta → your webhook → your
  LLM on your GPU. Web searches go through your own SearXNG instance. No
  third-party AI cloud sees anything.
- **Deliberate actions.** Tools that mutate the world are *gated*: the bot
  shows you exactly what it wants to do and waits for YES.
- **Small.** The core is a few hundred lines of readable Python.

## Architecture

```
WhatsApp ⇄ Meta Cloud API ⇄ your webhook (famulus, FastAPI)
                                   │
                        agent loop (Ollama, tool-calling)
                                   │
              ┌────────────┬───────┴──────┬──────────────┐
          built-in:     built-in:      plugins:       plugins:
          web search    weather        mail · home    your own
          (SearXNG)     (Open-Meteo)   automation ·   (~50 lines)
                                       LinkedIn
```

## Quickstart

Prerequisites: Docker, an [Ollama](https://ollama.com) server with a
tool-calling model (`ollama pull qwen3:8b`), and a Meta developer account.

```bash
git clone https://github.com/eduelias/famulus && cd famulus
./setup.sh                  # creates .env + SearXNG secret
# edit .env  (see docs/setup-meta.md for the WhatsApp side)
docker compose up -d --build
```

Expose `https://<your-host>/webhook` to the internet (Cloudflare Tunnel,
Tailscale Funnel, or a reverse proxy), register it in your Meta app with your
`WA_VERIFY_TOKEN`, subscribe to the `messages` field — and message your bot.

The full Meta walkthrough (app, test number, tokens, webhook) lives in
[docs/setup-meta.md](docs/setup-meta.md).

## Model failover

Point famulus at more than one Ollama server and it tries them in order — a
fast GPU box first, a small always-on model as a backstop:

```bash
LLM_BACKENDS=http://192.168.1.50:11434|qwen3:8b,http://localhost:11434|qwen3:4b
```

If the GPU machine is asleep, unreachable, or its model is missing, the next
backend answers instead. Only if *every* backend fails does the owner get a
plain "I can't reach my language model right now" — never a stack trace.

Failover is fast: the connect timeout is 5s (`LLM_CONNECT_TIMEOUT`), because a
*sleeping* host silently drops packets rather than refusing the connection and
would otherwise stall for the full read timeout.

The fallback model still needs tool-calling support to use plugins. Small
models choose tools less reliably than big ones — measured on a Raspberry Pi 5
(CPU only): `qwen3:1.7b` ≈ 7 tok/s and emits valid tool calls, while
`qwen3:4b` ≈ 2.8 tok/s, which is too slow to be useful.

> **Running Ollama on Windows?** If you start it from Task Scheduler, set the
> task's *execution time limit* to unlimited. The default is 72 hours, after
> which Windows silently kills the server — and an "at startup" trigger will
> not restart it until the machine reboots. See
> [docs/ollama-windows.md](docs/ollama-windows.md).

## Official plugins

| Package | Adds | Gated actions |
|---|---|---|
| [famulus-google](https://github.com/eduelias/famulus-google) | Gmail: search, read, organize | sending mail |
| [famulus-msgraph](https://github.com/eduelias/famulus-msgraph) | Outlook (personal MS account): search, read, move | sending mail |
| [famulus-linkedin](https://github.com/eduelias/famulus-linkedin) | LinkedIn posting | every post |
| [famulus-homeassistant](https://github.com/eduelias/famulus-homeassistant) | Home Assistant: devices, music, automations | automation writes, locks/covers |

```bash
# not on PyPI yet — install from git
pip install \
  "git+https://github.com/eduelias/famulus-google" \
  "git+https://github.com/eduelias/famulus-msgraph" \
  "git+https://github.com/eduelias/famulus-linkedin" \
  "git+https://github.com/eduelias/famulus-homeassistant"
```

Each has its own one-time authorization step — see its README.

## Writing a plugin

A plugin is a pip-installable package exposing an object in the
`famulus.plugins` entry-point group:

```python
# my_plugin.py
from famulus.plugins import BasePlugin, spec

class CoinFlip(BasePlugin):
    name = "coinflip"
    tools = [spec("flip_coin", "Flip a coin.", {}, [])]

    def execute(self, tool, args):
        import random
        return random.choice(["heads", "tails"])
```

```toml
# pyproject.toml of your plugin package
[project.entry-points."famulus.plugins"]
coinflip = "my_plugin:CoinFlip"
```

Install it next to famulus and it's live — no core changes. Tools listed in
`gated` (or for which `is_gated()` returns True) trigger the owner-confirmation
flow automatically. See [src/famulus/plugins/base.py](src/famulus/plugins/base.py)
for the full contract.

## Security model

- Webhook requests are verified against Meta's `X-Hub-Signature-256` HMAC
  (your app secret). Unsigned traffic is rejected unless you explicitly opt
  out for local development.
- Only numbers in `ALLOWED_WA_NUMBERS` are ever answered.
- Tool output (web pages, emails) is treated as untrusted data in the system
  prompt: content is summarized, instructions inside it are not followed.
- Gated actions require a fresh YES from the owner, per action.
- `web_fetch` refuses private/loopback addresses so the model can't be talked
  into probing your LAN.

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## Honest limitations (v0.1)

- **Single-process, in-memory state**: conversation history and pending
  confirmations are lost on restart.
- **Single-owner design**: the allowlist gates access, but all senders share
  the same tool permissions.
- **Ollama-specific** chat API (the `think`/`keep_alive` options); an
  OpenAI-compatible backend is on the roadmap.
- Text messages only (no voice notes, images or documents yet).

## Roadmap

- [x] Model failover across multiple Ollama backends
- [ ] OpenAI-compatible LLM backends
- [ ] Persistent conversation state (SQLite)
- [ ] Media messages (voice note transcription, image understanding)
- [x] Official plugins: Gmail, Microsoft Graph (Outlook), LinkedIn, Home Assistant
- [ ] Publish to PyPI (today: install from git)
- [ ] Per-sender permission profiles

## License

Apache-2.0 — see [LICENSE](LICENSE).
