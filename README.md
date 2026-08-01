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
          web search    weather        famulus-*      your own
          (SearXNG)     (Open-Meteo)   (pip install)  (~50 lines)
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

- [ ] OpenAI-compatible LLM backends
- [ ] Persistent conversation state (SQLite)
- [ ] Media messages (voice note transcription, image understanding)
- [ ] Official plugins: Gmail, Microsoft Graph (Outlook), LinkedIn,
      Home Assistant
- [ ] Per-sender permission profiles

## License

Apache-2.0 — see [LICENSE](LICENSE).
