# Security Policy

famulus is designed to hold personal credentials (WhatsApp tokens, and —
via plugins — email and home-automation access). Take vulnerabilities in it
seriously; we do.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private vulnerability reporting on this repository
(Security tab → "Report a vulnerability"). You'll get an acknowledgement
within a week.

## Scope notes

- The webhook endpoint is the only network-exposed surface; it requires a
  valid `X-Hub-Signature-256` from Meta.
- Prompt-injection through tool content (web pages, emails) is mitigated by
  system-prompt policy and by gating all state-changing actions behind owner
  confirmation — but LLM-based mitigations are probabilistic, not proofs.
  Reports of practical injection→action chains are very welcome.
- Secrets live in `.env` and `data/`; both are gitignored. If you find a code
  path that logs or transmits them, that's a vulnerability — report it.
