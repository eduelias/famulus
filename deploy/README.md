# Releasing famulus to du7

The owner's own deployment: famulus on the Raspberry Pi `du7`, in the docker compose stack
at `~esaleh/famulus-stack` (famulus, searxng and the plugin sidecars). Nothing here is
needed to run famulus yourself; see the top-level README for that.

## Cutting a release

```bash
# write what changed under "## [Unreleased]" in CHANGELOG.md, commit it, then:
scripts/release.py cut 0.3.0 --push
```

`cut` moves the Unreleased notes under the new version, bumps `pyproject.toml`, commits,
tags `v0.3.0` and pushes `main` and the tag together. The tag starts `release.yml`:

1. **CI**: the same tests and lint as every push, then the wheel is built, installed in a
   clean venv, started and asked for `/health`.
2. **Publish**: the GitHub release `v0.3.0` with the CHANGELOG notes, the wheel and
   `SHA256SUMS`. Releases are immutable once published.
3. **Deploy** (`deploy.yml`): the wheel goes to du7's runner, which runs
   `sudo -n /usr/local/sbin/famulus-deploy 0.3.0 <wheel> SHA256SUMS`:
   - the wheel is checked against `SHA256SUMS` and kept in `release/0.3.0/`;
   - the image is rebuilt with that wheel and the plugins from their `main` branches,
     and tagged `famulus-stack-famulus:0.3.0`;
   - the container is recreated, and `/health` must answer `"version": "0.3.0"` within
     a minute;
   - if it doesn't, the previous image is put back and the job fails.
4. A failure at any stage sends a WhatsApp template alert (the `alerts` environment).

Restarting the container drops in-flight conversations and pending confirmations
(they are in memory), the same as any restart.

## Redeploying or rolling back

- Any published release: **Actions → Deploy → Run workflow** on `main`, version `X.Y.Z`.
- Straight back to the previous one, on du7: `sudo famulus-deploy --rollback`.
- What is live: `sudo famulus-deploy --status`; history in `~/famulus-stack/release/deploy.log`
  and `journalctl -t famulus-deploy`.

A plain `docker compose up -d --build` in the stack still works and rebuilds the live
release (the wheel in `release/current/`) with the plugins' latest `main`. Plugin changes
are picked up that way, or by the next famulus release.

## How du7 is set up

- **Runner.** `gh-runner` (no shell, no groups) runs a GitHub Actions runner for this
  repository, labels `du7,famulus`, in `/opt/gh-runner/actions-runner-famulus`, beside the
  Pauta and Dominion ones. Its only sudo rule is `/usr/local/sbin/famulus-deploy`, which
  checks every argument.
- **Job guard.** This repository is public, and a self-hosted runner runs whatever a
  workflow tells it to. A systemd drop-in makes the runner call
  `/usr/local/lib/famulus/runner-job-guard` before every job. The guard refuses everything
  except job `du7` of `release.yml` on a `v*` tag push, and of `deploy.yml` run by hand on
  `main`. Refusals go to the journal (`journalctl -t famulus-runner-guard`).
- **Repository settings** (GitHub): a "release tags" ruleset so that only the owner
  creates, moves or deletes `v*` tags; workflows from fork pull requests need the owner's
  approval to run; `production` and `alerts` environments deployable only from `main` and
  `v*` tags; immutable releases.
- **The stack's Dockerfile** installs `release/current/*.whl` instead of famulus from git:

  ```dockerfile
  COPY release/current/ /tmp/famulus-release/
  RUN pip install --no-cache-dir /tmp/famulus-release/*.whl \
        "famulus-msgraph @ git+https://github.com/eduelias/famulus-msgraph@main" \
        ...
  ```

  `famulus-deploy` refuses to run until it does.

Setting it up again from scratch: copy `deploy/` to du7 and run, as root,

```bash
gh api -X POST repos/eduelias/famulus/actions/runners/registration-token --jq .token > token
sudo deploy/provision-du7.sh RUNNER_VERSION RUNNER_SHA256 token
```
