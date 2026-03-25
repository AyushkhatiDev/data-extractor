import threading
from typing import Dict, Optional

class TaskManager:
    """
    Singleton manager to track running extraction tasks.
    Allows for cooperative cancellation of background threads via threading.Event.
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TaskManager, cls).__new__(cls)
                cls._instance._tasks: Dict[int, threading.Event] = {}
        return cls._instance
        
    def register_task(self, task_id: int) -> threading.Event:
        """Register a new task and return its stop event."""
        with self._lock:
            stop_event = threading.Event()
            self._tasks[task_id] = stop_event
            return stop_event
            
    def stop_task(self, task_id: int) -> bool:
        """Signal a task to stop. Returns True if task was running."""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].set()
                return True
            return False
            
    def is_stopped(self, task_id: int) -> bool:
        """Check if a task has been signaled to stop."""
        with self._lock:
            if task_id in self._tasks:
                return self._tasks[task_id].is_set()
            return False
            
    def cleanup_task(self, task_id: int):
        """Remove a task from the manager."""
        with self._lock:
            self._tasks.pop(task_id, None)

# Global singleton instance
task_manager = TaskManager()


def build_list_extractor(task_id: int, list_type: str, stop_event=None, ai_options=None):
    """Build the proper extractor instance for a list type."""
    from app.extraction.list_extractor import ListExtractor
    from app.extraction.us_list_types import get_list_type_config

    config = get_list_type_config(list_type or '') or {}
    extractor_class = config.get('extractor_class') or ListExtractor

    if extractor_class is ListExtractor:
        return ListExtractor(
            task_id,
            list_type=list_type,
            stop_event=stop_event,
            ai_options=ai_options,
        )

    return extractor_class(
        task_id=task_id,
        seed_urls=config.get('seed_urls', []),
        target_domains=config.get('target_domains', []),
        follow_links=config.get('follow_links', True),
        max_depth=config.get('max_depth', 2),
        max_pages=config.get('max_pages', 40),
        email_patterns=config.get('email_patterns', []),
        stop_event=stop_event,
        ai_options=ai_options,
    )
