# Contributing to WattPost

Thanks for stopping by. WattPost is the appliance half of a small
off-grid solar monitoring product. Patches that add vendor drivers,
fix bugs, or improve the dashboard are all welcome.

## Quick start

```bash
git clone git@github.com:ritualnorth/wattpost.git
cd wattpost
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Run the test suite (or any individual verify script)
.venv/bin/python -m pytest tests/
.venv/bin/python scripts/verify_renogy.py
```

## Pre-commit hooks (please install)

A handful of hooks keep the tree tidy and catch common voice / hygiene
issues before they hit the history:

```bash
pipx install pre-commit            # or: pip install --user pre-commit
pre-commit install                 # wires .git/hooks/pre-commit
pre-commit install --hook-type commit-msg
```

Hooks: trailing whitespace, YAML/JSON parse, large-file block, secret
scan (gitleaks), no em-dashes in source/docs, no `[[memory-name]]`
patterns, no "Why this matters" preambles, no AI-attribution
trailers in commit messages.

## Adding a vendor driver

See [docs/adding-a-vendor.md](docs/adding-a-vendor.md). The short
version:

1. Drop a folder under `solar_monitor/vendors/<your-vendor>/`
2. One `DeviceDriver` subclass per device kind (battery, charger, shunt, etc.)
3. Register the vendor in your `__init__.py`
4. Add one import line to `vendors/__init__.py`
5. Write a `scripts/verify_<vendor>.py` that exercises the parser
   against synthetic fixtures, so we catch regressions without
   needing the physical hardware

## Pull requests

- Small, focused PRs land fastest.
- One logical change per PR.
- Update [CHANGELOG.md](CHANGELOG.md) under `[Unreleased]`.
- Commit messages: present-tense, what + why. No AI trailers.

If you're adding a driver and don't have the physical hardware, mark
it `experimental: true` in the vendor `INFO` block. The first customer
report becomes the validation set.

## Licence

By contributing, you agree your contributions are licensed under
Apache 2.0 (the project's licence). See [LICENSE](LICENSE) and
[NOTICE](NOTICE).
