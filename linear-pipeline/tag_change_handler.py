import time
from typing import Dict, Optional, Any

TRIGGER_TAGS = {"plan", "implement"}


def should_trigger_on_tag(event: Dict[str, Any]) -> bool:
    issue = event.get("issue", {})
    labels = issue.get("labels", {}).get("nodes", [])
    tag_names = {label.get("name", "").lower() for label in labels}
    return bool(tag_names & TRIGGER_TAGS)


_processed_events: Dict[str, float] = {}
DEDUP_TTL = 300


def process_event(event: Dict[str, Any]) -> Optional[str]:
    """
    Process a ticket event from webhook.
    Returns a trigger message if worker should be triggered, else None.
    Deduplicates events based on event ID.
    """
    if not isinstance(event, dict):
        raise ValueError("event must be a dict")
    event_id = event.get('id')
    if not event_id:
        raise ValueError("event must have an 'id' field")

    if _is_duplicate(event_id):
        return None

    _mark_processed(event_id)
    return trigger_worker(event)


def _is_duplicate(event_id: str) -> bool:
    now = time.time()
    expired = [eid for eid, ts in _processed_events.items() if now - ts > DEDUP_TTL]
    for eid in expired:
        del _processed_events[eid]
    return event_id in _processed_events


def _mark_processed(event_id: str) -> None:
    _processed_events[event_id] = time.time()


def trigger_worker(event: Dict[str, Any]) -> Optional[str]:
    if should_trigger_on_tag(event):
        return f"Triggered worker for event {event.get('id')} on issue {event.get('issue', {}).get('id')}"
    return None