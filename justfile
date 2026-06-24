set dotenv-load := true
set export := true
set positional-arguments := true

NAME := "dnf-plugin-anyrepo"
PYTHON := "python3"

# Show available tasks.
default:
    @just --list

#
# clean
#

# Remove generated files so local validation can start from a clean tree.
clean:
    rm -rf build dist *.egg-info
    find . -type d -name __pycache__ -prune -exec rm -rf {} +

#
# lint
#

# Check Python 3.6-compatible syntax and bytecode compilation.
lint:
    #!/usr/bin/env bash
    set -euo pipefail
    {{PYTHON}} - <<'PY'
    import ast
    from pathlib import Path

    paths = (
        list(Path("dnf_plugin_anyrepo").rglob("*.py"))
        + list(Path("plugins").rglob("*.py"))
        + list(Path("tests").rglob("*.py"))
        + [Path("setup.py")]
    )
    for path in paths:
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, 6))
    print("python 3.6 syntax ok")
    PY
    {{PYTHON}} -m compileall -q dnf_plugin_anyrepo plugins tests setup.py

# Run unit tests with unittest.
test:
    {{PYTHON}} -m unittest discover -s tests -v

# Run e2e tests.
test-e2e: snapshot
    #!/usr/bin/env bash
    set -euo pipefail
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    mkdir -p "$tmp/repos.d"
    dnf_opts=(--setopt="reposdir=$tmp/repos.d")
    . /etc/os-release
    distro_major="${VERSION_ID%%.*}"
    rpm_path="./dist/dnf-plugin-anyrepo-"*".el${distro_major}.noarch.rpm"
    if ! compgen -G "$rpm_path" >/dev/null; then
        echo "no RPM artifact found for EL${distro_major}" >&2
        exit 1
    fi

    sudo dnf "${dnf_opts[@]}" --noplugins remove -y dnf-plugin-anyrepo || true
    sudo dnf "${dnf_opts[@]}" --noplugins install -y $rpm_path
    sudo dnf-anyrepo add https://github.com/jfut/prec --minimum-release-age 0 --force
    sudo dnf-anyrepo list
    sudo dnf "${dnf_opts[@]}" list prec | tee "$tmp/dnf-list.txt"
    grep 'github.com:jfut:prec' "$tmp/dnf-list.txt"
    host_arch="$(arch)"
    if [ "$host_arch" = "amd64" ]; then
        host_arch="x86_64"
    elif [ "$host_arch" = "arm64" ]; then
        host_arch="aarch64"
    fi
    grep "prec\\.$host_arch" "$tmp/dnf-list.txt"
    if grep -E 'prec\.(x86_64|aarch64)' "$tmp/dnf-list.txt" | grep -v "prec\\.$host_arch"; then
        echo "unexpected foreign architecture package on $host_arch host" >&2
        exit 1
    fi
    sudo dnf "${dnf_opts[@]}" install -y prec
    sudo dnf "${dnf_opts[@]}" remove -y prec
    sudo dnf-anyrepo remove prec --purge-cache
    sudo dnf "${dnf_opts[@]}" --noplugins remove -y dnf-plugin-anyrepo
    sudo rm -f /etc/dnf/plugins/anyrepo.conf.rpmsave /etc/yum.repos.d/anyrepo.repo.rpmsave

# Run CLI smoke tests against a temporary configuration file.
exec-test:
    #!/usr/bin/env bash
    set -euo pipefail
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    {{PYTHON}} -m dnf_plugin_anyrepo.cli --config "$tmp/anyrepo.conf" add https://github.com/jfut/prec --minimum-release-age 30m
    {{PYTHON}} -m dnf_plugin_anyrepo.cli --config "$tmp/anyrepo.conf" global set cache_dir "$tmp/cache"
    {{PYTHON}} -m dnf_plugin_anyrepo.cli --config "$tmp/anyrepo.conf" list
    {{PYTHON}} -m dnf_plugin_anyrepo.cli --config "$tmp/anyrepo.conf" global
    {{PYTHON}} -m dnf_plugin_anyrepo.cli --config "$tmp/anyrepo.conf" repo prec
    {{PYTHON}} -m dnf_plugin_anyrepo.cli --config "$tmp/anyrepo.conf" global set minimum_release_age 1h
    test "$({{PYTHON}} -m dnf_plugin_anyrepo.cli --config "$tmp/anyrepo.conf" global get minimum_release_age)" = "3600"
    {{PYTHON}} -m dnf_plugin_anyrepo.cli --config "$tmp/anyrepo.conf" repo prec set enabled false
    {{PYTHON}} -m dnf_plugin_anyrepo.cli --config "$tmp/anyrepo.conf" repo prec unset enabled
    {{PYTHON}} -m dnf_plugin_anyrepo.cli --config "$tmp/anyrepo.conf" remove prec --purge-cache

# Run lint, unit tests, and smoke tests.
check: lint test exec-test

#
# build
#

# Build a local Python source distribution.
build: clean
    {{PYTHON}} setup.py sdist

#
# release
#

# Build a local snapshot release without publishing.
snapshot: clean
    goreleaser release --skip=publish --clean --snapshot

# Build release artifacts without publishing.
release: clean
    goreleaser release --skip=publish --clean --skip=validate
