#!/usr/bin/env python3
"""Generate VitePress markdown pages from docs/ source files.

Run this after modifying docs/ files, then push to rebuild the site:
    python scripts/sync_docs.py && git add website/ && git commit -m "docs: sync from source" && git push

Mapping:
  docs/01-Architecture.md     → website/guide/architecture.md
  docs/02-Installation.md     → website/guide/installation.md
  docs/03-Operations.md       → website/guide/operations.md
  docs/04-Workflow.md         → website/guide/workflow.md
  docs/05-AI-Integration.md   → website/ai/integration-guide.md
"""

import os
import re
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
DOCS = ROOT / "docs"
WEBSITE = ROOT / "website"

MAPPINGS = [
    # (source_path, dest_path, frontmatter_yaml)
    (
        "docs/01-Architecture.md",
        "website/guide/architecture.md",
        "title: Architecture\nsidebar:\n  '/guide/':\n    - path: /guide/architecture\n      text: Architecture\n",
    ),
    (
        "docs/02-Installation.md",
        "website/guide/installation.md",
        "title: Installation\nsidebar:\n  '/guide/':\n    - path: /guide/installation\n      text: Installation\n",
    ),
    (
        "docs/03-Operations.md",
        "website/guide/operations.md",
        "title: Operations Guide\nsidebar:\n  '/guide/':\n    - path: /guide/operations\n      text: Operations Guide\n",
    ),
    (
        "docs/04-Workflow.md",
        "website/guide/workflow.md",
        "title: Workflow\nsidebar:\n  '/guide/':\n    - path: /guide/workflow\n      text: Workflow\n",
    ),
    (
        "docs/05-AI-Integration.md",
        "website/ai/integration-guide.md",
        "title: AI Integration Guide\nsidebar:\n  '/ai/':\n    - path: /ai/integration-guide\n      text: AI Integration\n",
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


def clean_old_files():
    """Clean old markdown files from website directories before syncing new ones."""
    for folder in ["guide", "reference", "ai"]:
        target_dir = WEBSITE / folder
        if target_dir.exists():
            for f in target_dir.glob("*.md"):
                f.unlink()


def main():
    print("Cleaning old website documentation...")
    clean_old_files()
    
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
