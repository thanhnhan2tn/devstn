#!/usr/bin/env python3
"""Generate VitePress markdown pages from docs/ source files.

Run this after modifying docs/ files, then push to rebuild the site:
    python scripts/sync_docs.py && git add website/ && git commit -m "docs: sync from source" && git push

Mapping:
  docs/Architecture.md              → website/guide/architecture.md
  docs/Workflow.md               → website/guide/workflow.md
  docs/DEPLOYMENT_PLAN.md         → website/guide/deployment.md
  docs/EXECUTION_GUIDE.md         → website/guide/operations.md
  docs/Tooling_Checklist.md       → website/reference/configuration.md
  docs/Self_Healing.md           → website/reference/self-healing.md
  docs/TELEGRAM_SETUP.md         → website/reference/telegram.md
  docs/AIOps_and_GitOps_Strategy.md → website/reference/aiops-gitops.md
  docs/machine-context-mini.md   → website/reference/machine-context.md
  AI_INTEGRATION_GUIDE.md         → website/ai/integration-guide.md
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
DOCS = ROOT / "docs"
WEBSITE = ROOT / "website"

MAPPINGS = [
    # (source_path, dest_path, frontmatter_yaml)
    (
        "docs/Architecture.md",
        "website/guide/architecture.md",
        "title: Architecture\nsidebar:\n  '/guide/':\n    - path: /guide/architecture\n      text: Architecture\n",
    ),
    (
        "docs/Workflow.md",
        "website/guide/workflow.md",
        "title: Workflow\nsidebar:\n  '/guide/':\n    - path: /guide/workflow\n      text: Workflow\n",
    ),
    (
        "docs/DEPLOYMENT_PLAN.md",
        "website/guide/deployment.md",
        "title: Deployment\nsidebar:\n  '/guide/':\n    - path: /guide/deployment\n      text: Deployment\n",
    ),
    (
        "docs/EXECUTION_GUIDE.md",
        "website/guide/operations.md",
        "title: Operations Guide\nsidebar:\n  '/guide/':\n    - path: /guide/operations\n      text: Operations Guide\n",
    ),
    (
        "docs/Tooling_Checklist.md",
        "website/reference/configuration.md",
        "title: Configuration Reference\nsidebar:\n  '/reference/':\n    - path: /reference/configuration\n      text: Configuration\n",
    ),
    (
        "docs/Self_Healing.md",
        "website/reference/self-healing.md",
        "title: Self-Healing\nsidebar:\n  '/reference/':\n    - path: /reference/self-healing\n      text: Self-Healing\n",
    ),
    (
        "docs/TELEGRAM_SETUP.md",
        "website/reference/telegram.md",
        "title: Telegram Bot Setup\nsidebar:\n  '/reference/':\n    - path: /reference/telegram\n      text: Telegram Bot\n",
    ),
    (
        "docs/AIOps_and_GitOps_Strategy.md",
        "website/reference/aiops-gitops.md",
        "title: AIOps & GitOps\nsidebar:\n  '/reference/':\n    - path: /reference/aiops-gitops\n      text: AIOps & GitOps\n",
    ),
    (
        "docs/machine-context-mini.md",
        "website/reference/machine-context.md",
        "title: Machine Context\nsidebar:\n  '/reference/':\n    - path: /reference/machine-context\n      text: Machine Context\n",
    ),
    (
        "AI_INTEGRATION_GUIDE.md",
        "website/ai/integration-guide.md",
        "title: AI Integration Guide\nsidebar:\n  '/reference/':\n    - path: /ai/integration-guide\n      text: AI Integration\n",
    ),
]


def strip_frontmatter(text):
    """Remove YAML frontmatter block."""
    return re.sub(r'^---\n.*?\n---\n?', '', text, flags=re.DOTALL).lstrip()


def add_frontmatter(content, fm_yaml):
    """Prepend VitePress frontmatter to content."""
    return f"---\n{fm_yaml}---\n\n{content}"


def rewrite_links(content):
    """Rewrite relative .md links to .html for VitePress."""
    # Rewrite doc links: [text](./doc.md) → [text](./doc)
    content = re.sub(r'\]\(\.\/([^)]+)\.md\)', r'](\1)', content)
    # Rewrite root doc links: [text](../docs/doc.md) → [text](./doc)
    content = re.sub(r'\]\(\.\.\/docs\/([^)]+)\.md\)', r'](\1)', content)
    return content


def sync_file(src, dst, fm_yaml):
    src_path = ROOT / src
    dst_path = ROOT / dst

    if not src_path.exists():
        print(f"  SKIP: {src} not found")
        return False

    content = src_path.read_text(encoding="utf-8")
    content = strip_frontmatter(content)
    content = rewrite_links(content)
    content = add_frontmatter(content, fm_yaml)

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(content, encoding="utf-8")
    print(f"  SYNC: {src} → {dst}")
    return True


def main():
    print("Syncing docs/ → website/ ...")
    count = 0
    for src, dst, fm in MAPPINGS:
        if sync_file(src, dst, fm):
            count += 1
    print(f"\nDone — {count} files synced.")
    print("Commit and push to rebuild the site:")
    print("  git add website/ && git commit -m 'docs: sync from source' && git push")


if __name__ == "__main__":
    main()
