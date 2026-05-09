# Contributing to Mini Dev Station

Thank you for your interest in contributing! This guide will help you get started.

## Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/mini-dev-station.git
   cd mini-dev-station
   ```
3. **Set up** the environment:
   ```bash
   cp .env.example .env
   # Fill in your API keys (see .env.example for details)
   pip3 install -r requirements.txt
   ```

## Making Changes

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Make your changes and test them locally.
3. Commit with clear, descriptive messages using [Conventional Commits](https://www.conventionalcommits.org/):
   ```
   feat: add new workflow trigger for Jira
   fix: resolve Telegram bot reconnection loop
   docs: update Quick Start for Linux support
   ```

## Pull Requests

- Open a PR against the `main` branch.
- Describe **what** changed and **why** in the PR body.
- Link any related Linear issues or GitHub issues.
- Ensure all existing tests pass before requesting review.

## Code Style

- **Shell scripts**: Follow existing patterns in `station_master.sh` and `setup_orchestrator.sh`. Use `set -euo pipefail` where appropriate.
- **Python**: Follow PEP 8. Use type hints. Match the style of `autonomous_pipeline.py` and `notification_manager.py`.
- **Documentation**: Keep docs concise and command-driven. No academic prose — focus on actionable steps and verification commands.

## Areas for Contribution

- **New LLM integrations** — add support for additional cloud or local LLM providers.
- **Platform support** — extend setup scripts for Linux.
- **Workflow templates** — create n8n workflow templates for common use cases.
- **Testing** — improve test coverage for the Python pipeline components.
- **Documentation** — improve guides, add tutorials, fix typos.

## Reporting Issues

Use [GitHub Issues](https://github.com/thanhnhan2tn/mini-dev-station/issues) to report bugs or request features. Include:
- Steps to reproduce (for bugs)
- Expected vs. actual behavior
- macOS version and hardware info
- Relevant log output

## Questions?

Open a [Discussion](https://github.com/thanhnhan2tn/mini-dev-station/discussions) or reach out via the project's Telegram group.
