"""Lock file management for preventing concurrent access.

Provides PID-based locking with automatic stale lock detection.
"""
import os
import json
import logging
import socket
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class LockFile:
    """Manages a PID-based lock file with stale lock detection.

    Example usage:
        lock = LockFile("/path/to/.chl.lock")

        # Acquire lock (server startup)
        if not lock.acquire():
            print("Server already running!")
            sys.exit(1)

        # Check if locked (export/import scripts)
        is_locked, info = lock.is_locked()
        if is_locked:
            print(f"Server is running (PID {info['pid']})")
            sys.exit(1)

        # Release lock (server shutdown)
        lock.release()
    """

    def __init__(self, lock_path: Path):
        """Initialize lock file manager.

        Args:
            lock_path: Path to the lock file
        """
        self.lock_path = Path(lock_path)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

    def acquire(self) -> bool:
        """Acquire lock for current process using atomic file creation.

        Uses O_CREAT | O_EXCL for atomic lock acquisition to prevent race conditions
        where two processes starting simultaneously might both think they acquired the lock.

        Returns:
            True if lock acquired successfully, False if already locked
        """
        # Check if already locked by another process
        is_locked, info = self.is_locked()
        if is_locked:
            logger.error(
                f"Lock file already exists (PID {info['pid']} on {info.get('hostname', 'unknown')})"
            )
            return False

        # Remove stale lock if present
        if self.lock_path.exists():
            logger.info("Removing stale lock file")
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                # Another process removed it, that's fine
                pass

        # Create new lock atomically using O_CREAT | O_EXCL
        # This ensures only one process succeeds if multiple try simultaneously
        lock_data = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "hostname": socket.gethostname()
        }

        try:
            # Open with O_CREAT | O_EXCL - fails atomically if file exists
            fd = os.open(
                str(self.lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644
            )
            try:
                # Write JSON data to the file descriptor
                lock_json = json.dumps(lock_data, indent=2)
                os.write(fd, lock_json.encode('utf-8'))
            finally:
                os.close(fd)

            logger.info(f"Acquired lock (PID {os.getpid()})")
            return True

        except FileExistsError:
            # Another process created the lock between our check and creation (race condition)
            logger.error("Lock file was created by another process during acquisition attempt")
            return False

        except Exception as e:
            logger.error(f"Failed to create lock file: {e}")
            return False

    def release(self) -> bool:
        """Release lock held by current process.

        Returns:
            True if lock released, False if not held by current process
        """
        if not self.lock_path.exists():
            return True

        try:
            # Verify we own the lock
            with open(self.lock_path, 'r') as f:
                lock_data = json.load(f)

            if lock_data.get('pid') != os.getpid():
                logger.warning(
                    f"Cannot release lock owned by PID {lock_data.get('pid')} "
                    f"(current PID: {os.getpid()})"
                )
                return False

            # Remove lock file
            self.lock_path.unlink()
            logger.info(f"Released lock (PID {os.getpid()})")
            return True

        except Exception as e:
            logger.error(f"Failed to release lock: {e}")
            return False

    def is_locked(self) -> Tuple[bool, Optional[dict]]:
        """Check if lock is held by a running process.

        Returns:
            Tuple of (is_locked, lock_info)
            - is_locked: True if actively locked by running process
            - lock_info: Lock metadata dict or None
        """
        if not self.lock_path.exists():
            return False, None

        try:
            # Read lock file
            with open(self.lock_path, 'r') as f:
                lock_data = json.load(f)

            pid = lock_data.get('pid')
            if pid is None:
                logger.warning("Lock file missing PID, treating as stale")
                return False, lock_data

            # Check if process is running
            if self._is_process_running(pid):
                return True, lock_data
            else:
                logger.info(f"Lock file exists but PID {pid} is not running (stale lock)")
                return False, lock_data

        except json.JSONDecodeError:
            logger.warning("Lock file is corrupted, treating as stale")
            return False, None
        except Exception as e:
            logger.error(f"Error reading lock file: {e}")
            # Err on the side of caution - assume locked
            return True, None

    def force_remove(self) -> bool:
        """Force remove lock file regardless of state.

        WARNING: Only use this if you're certain no process is running.

        Returns:
            True if removed successfully
        """
        if not self.lock_path.exists():
            return True

        try:
            self.lock_path.unlink()
            logger.warning("Force removed lock file")
            return True
        except Exception as e:
            logger.error(f"Failed to force remove lock: {e}")
            return False

    @staticmethod
    def _is_process_running(pid: int) -> bool:
        """Check if a process with given PID is running.

        Args:
            pid: Process ID to check

        Returns:
            True if process is running
        """
        try:
            # Send signal 0 (doesn't actually send a signal, just checks if process exists)
            os.kill(pid, 0)
            return True
        except OSError:
            # Process doesn't exist
            return False
        except Exception:
            # Other errors - assume process might be running
            return True

    def get_info(self) -> Optional[dict]:
        """Get lock file information without checking if process is running.

        Returns:
            Lock metadata dict or None if no lock file
        """
        if not self.lock_path.exists():
            return None

        try:
            with open(self.lock_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading lock file: {e}")
            return None
