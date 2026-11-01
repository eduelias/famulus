#!/usr/bin/env bash
# One-time (and re-runnable) setup of du7 for famulus release deploys. Run as root on du7
# from a copy of this deploy/ directory:
#
#   sudo deploy/provision-du7.sh RUNNER_VERSION RUNNER_SHA256 [TOKEN_FILE]
#
# It installs famulus-deploy, its sudo rule and the job guard, and (with TOKEN_FILE, a file
# holding a runner registration token from
# `gh api -X POST repos/eduelias/famulus/actions/runners/registration-token --jq .token`)
# registers a GitHub Actions runner for eduelias/famulus as the locked-down user gh-runner
# that the Pauta and Dominion runners already use. famulus is a public repository, so the
# runner is started only once the guard is in place. The stack's Dockerfile is changed by
# hand once (deploy/README.md); famulus-deploy refuses to run until it has been.
set -euo pipefail

RUNNER_VERSION=${1:?runner version, e.g. 2.338.2}
RUNNER_SHA256=${2:?sha256 of actions-runner-linux-arm64-<version>.tar.gz}
TOKEN_FILE=${3:-}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO_URL=https://github.com/eduelias/famulus
RUNNER_USER=gh-runner
RUNNER_HOME=/opt/gh-runner
SERVICE_USER=esaleh
STACK=/home/$SERVICE_USER/famulus-stack

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
[ "$(uname -m)" = aarch64 ] || { echo "expected an arm64 Pi" >&2; exit 1; }
[ -f "$STACK/docker-compose.yml" ] || { echo "no famulus stack at $STACK" >&2; exit 1; }

# --- the deploy script and the release dir -------------------------------------------------
install -o root -g root -m 0755 "$HERE/famulus-deploy" /usr/local/sbin/famulus-deploy
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 "$STACK/release"

# --- the runner user: no shell, no groups, one more sudo rule ------------------------------
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$RUNNER_HOME" --create-home --shell /usr/sbin/nologin \
        --comment "GitHub Actions runner (du7 deploys)" "$RUNNER_USER"
fi
chmod 0750 "$RUNNER_HOME"
rule=/etc/sudoers.d/gh-runner-famulus
cat > "$rule.tmp" <<EOF
# The GitHub Actions runner may deploy famulus releases, and nothing else, as root.
Defaults:$RUNNER_USER !requiretty
$RUNNER_USER ALL=(root) NOPASSWD: /usr/local/sbin/famulus-deploy
EOF
chmod 0440 "$rule.tmp"
visudo -cf "$rule.tmp" >/dev/null
mv "$rule.tmp" "$rule"

# --- the job guard: the runner refuses every job but the deploy ----------------------------
# Root-owned, outside the runner's own (gh-runner-writable) directory, and set through a
# systemd drop-in, so no job can switch it off for the jobs after it.
install -d -o root -g root -m 0755 /usr/local/lib/famulus
install -o root -g root -m 0755 "$HERE/runner-job-guard" /usr/local/lib/famulus/runner-job-guard
unit=actions.runner.eduelias-famulus.du7-famulus.service
guard() {
    install -d -o root -g root -m 0755 "/etc/systemd/system/$unit.d"
    cat > "/etc/systemd/system/$unit.d/job-guard.conf" <<EOF
# Every job on du7 first passes /usr/local/lib/famulus/runner-job-guard (deploy/README.md).
[Service]
Environment=ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/lib/famulus/runner-job-guard
EOF
    systemctl daemon-reload
}

# --- the runner itself (its own directory; a runner serves one repository) -----------------
dir=$RUNNER_HOME/actions-runner-famulus
if [ ! -x "$dir/config.sh" ]; then
    tarball=actions-runner-linux-arm64-$RUNNER_VERSION.tar.gz
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/$tarball" \
        "https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/$tarball"
    echo "$RUNNER_SHA256  $tmp/$tarball" | sha256sum -c -
    install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 "$dir"
    tar -xzf "$tmp/$tarball" -C "$dir"
    chown -R "$RUNNER_USER:$RUNNER_USER" "$dir"
    rm -rf "$tmp"
    "$dir/bin/installdependencies.sh"
fi
if [ -n "$TOKEN_FILE" ] && [ ! -f "$dir/.runner" ]; then
    token=$(cat "$TOKEN_FILE")
    rm -f "$TOKEN_FILE"
    (cd "$dir" && runuser -u "$RUNNER_USER" -- ./config.sh --unattended --replace \
        --url "$REPO_URL" --token "$token" --name du7-famulus --labels du7,famulus \
        --work _work)
    (cd "$dir" && ./svc.sh install "$RUNNER_USER")
    guard   # before the first start: a public repo's runner never runs unguarded
    (cd "$dir" && ./svc.sh start)
elif [ -f "/etc/systemd/system/$unit" ]; then
    guard
    systemctl restart "$unit"
fi

echo "provisioned: famulus-deploy, sudo rule, job guard, runner $(cd "$dir" && runuser -u "$RUNNER_USER" -- ./config.sh --version)"
