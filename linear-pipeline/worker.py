from typing import Dict, Any, Optional

TRIGGER_TAGS = {"plan", "implement"}


def should_trigger_on_tag(event: Dict[str, Any]) -> bool:
    issue = event.get("issue", {})
    labels = issue.get("labels", {}).get("nodes", [])
    tag_names = {label.get("name", "").lower() for label in labels}
    return bool(tag_names & TRIGGER_TAGS)


def trigger_worker(event: Dict[str, Any]) -> Optional[str]:
    """
    Trigger the worker for a valid event.
    """
    if should_trigger_on_tag(event):
        return f"Triggered worker for event {event.get('id')} on issue {event.get('issue', {}).get('id')}"
    return None