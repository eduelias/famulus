# Changelog

What changed in each release of famulus. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Write new entries under **Unreleased** as you
go; `scripts/release.py cut X.Y.Z --push` turns them into the next release's notes, and
pushing the tag tests, publishes and deploys it to du7 (`deploy/README.md`).

## [Unreleased]

## [0.3.0] - 2026-11-01

### Added

- Releases: a `vX.Y.Z` tag runs CI, publishes the wheel on GitHub with these notes and
  deploys it to du7, rolling back if the new version doesn't answer `/health`.
  `/health` now reports the running `version`.
- Lead intake: messages from numbers outside the allowlist get a fixed reply and are
  forwarded to the owner; they never reach the agent.

### Fixed

- Sunrise/sunset reminders for a city in another time zone fired at the wrong time: the
  city's local sunset was read as `FAMULUS_TZ` (#2, reported by @mattabate).
- A reminder due while WhatsApp could not deliver it was dropped instead of retried (#3).
- A turn that failed mid-way (no LLM backend, or any agent error) left its half-finished
  tool rounds in the conversation history; the whole turn is dropped now.

[Unreleased]: https://github.com/eduelias/famulus/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/eduelias/famulus/releases/tag/v0.3.0
