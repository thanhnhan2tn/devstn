import time
from typing import Dict, Any, Optional

from linear_pipeline.tag_change_handler import should_trigger_on_tag


_processed_events: Dict[str, float] = {}
DEDUP_TTL = 300  # 5 minutes


def process_event(event: Dict[str, Any]) -> Optional[str]:
    if not isinstance(event, dict):
        raise ValueError("event must be a dict")
    event_id = event.get('id')
    if not event_id:
        raise ValueError("event must have an 'id' field")

    if _is_duplicate(event_id):
        return None

    _mark_processed(event_id)

    if should_trigger_on_tag(event):
        return _trigger_worker(event)
    return None


def _is_duplicate(event_id: str) -> bool:
    now = time.time()
    expired = [eid for eid, ts in _processed_events.items() if now - ts > DEDUP_TTL]
    for eid in expired:
        del _processed_events[eid]
    return event_id in _processed_events


def _mark_processed(event_id: str) -> None:
    _processed_events[event_id] = time.time()


def _trigger_worker(event: Dict[str, Any]) -> str:
    return f"Triggered worker for event {event.get('id')} on issue {event.get('issue', {}).get('id')}"