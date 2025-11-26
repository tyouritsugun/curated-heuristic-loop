"""Session store for tracking viewed entries across search/read operations.

Phase 2: Implements session memory with LRU cache (500 sessions, 60m TTL).
"""

import time
from typing import Dict, Set, Optional
from collections import OrderedDict
from threading import RLock
import secrets


class SessionStore:
    """Thread-safe LRU cache for session memory.

    Each session tracks:
    - viewed_ids: Set of entity IDs the user has seen
    - last_accessed: Timestamp for TTL enforcement

    Configuration:
    - Max 500 sessions (LRU eviction)
    - 60 minute TTL (idle timeout)
    """

    def __init__(self, max_sessions: int = 500, ttl_seconds: int = 3600):
        """Initialize session store.

        Args:
            max_sessions: Maximum number of sessions to keep (default 500)
            ttl_seconds: Time-to-live in seconds (default 3600 = 60 minutes)
        """
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds

        # OrderedDict for LRU behavior
        # Key: session_id (str)
        # Value: dict with 'viewed_ids' (set) and 'last_accessed' (float)
        self._sessions: OrderedDict[str, Dict] = OrderedDict()
        self._lock = RLock()

    def generate_session_id(self) -> str:
        """Generate a new session ID.

        Returns:
            Cryptographically secure random session ID (32 hex chars)
        """
        return secrets.token_hex(16)

    def get_viewed_ids(self, session_id: str) -> Set[str]:
        """Get set of viewed entity IDs for a session.

        Args:
            session_id: Session identifier

        Returns:
            Set of entity IDs (empty set if session not found or expired)
        """
        with self._lock:
            self._evict_expired()

            if session_id not in self._sessions:
                return set()

            session = self._sessions[session_id]

            # Check TTL
            if time.time() - session['last_accessed'] > self.ttl_seconds:
                # Session expired
                del self._sessions[session_id]
                return set()

            # Update access time (LRU)
            session['last_accessed'] = time.time()
            self._sessions.move_to_end(session_id)

            return session['viewed_ids'].copy()

    def add_viewed_ids(self, session_id: str, entity_ids: Set[str]) -> None:
        """Add entity IDs to a session's viewed set.

        Creates session if it doesn't exist. Updates access time.

        Args:
            session_id: Session identifier
            entity_ids: Set of entity IDs to mark as viewed
        """
        with self._lock:
            self._evict_expired()

            if session_id not in self._sessions:
                # Create new session
                self._sessions[session_id] = {
                    'viewed_ids': set(),
                    'last_accessed': time.time()
                }

                # Enforce max_sessions limit (LRU eviction)
                if len(self._sessions) > self.max_sessions:
                    # Remove oldest session (first item in OrderedDict)
                    self._sessions.popitem(last=False)

            session = self._sessions[session_id]
            session['viewed_ids'].update(entity_ids)
            session['last_accessed'] = time.time()

            # Move to end (most recently used)
            self._sessions.move_to_end(session_id)

    def get_session_info(self, session_id: str) -> Optional[Dict]:
        """Get session information (for diagnostics/debugging).

        Args:
            session_id: Session identifier

        Returns:
            Dict with 'viewed_count' and 'last_accessed', or None if not found
        """
        with self._lock:
            self._evict_expired()

            if session_id not in self._sessions:
                return None

            session = self._sessions[session_id]

            # Check TTL
            if time.time() - session['last_accessed'] > self.ttl_seconds:
                del self._sessions[session_id]
                return None

            return {
                'viewed_count': len(session['viewed_ids']),
                'last_accessed': session['last_accessed']
            }

    def clear_session(self, session_id: str) -> bool:
        """Clear a session's viewed IDs (reset).

        Args:
            session_id: Session identifier

        Returns:
            True if session was cleared, False if not found
        """
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False

    def _evict_expired(self) -> None:
        """Evict expired sessions (internal helper, assumes lock held)."""
        now = time.time()
        expired_ids = [
            sid for sid, sess in self._sessions.items()
            if now - sess['last_accessed'] > self.ttl_seconds
        ]
        for sid in expired_ids:
            del self._sessions[sid]

    def get_stats(self) -> Dict:
        """Get store statistics (for diagnostics).

        Returns:
            Dict with 'active_sessions', 'max_sessions', 'ttl_seconds'
        """
        with self._lock:
            self._evict_expired()
            return {
                'active_sessions': len(self._sessions),
                'max_sessions': self.max_sessions,
                'ttl_seconds': self.ttl_seconds
            }


# Global session store instance
_session_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    """Get or create global session store instance.

    Returns:
        SessionStore singleton instance
    """
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store


__all__ = ["SessionStore", "get_session_store"]
