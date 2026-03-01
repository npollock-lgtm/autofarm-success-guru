"""
Resource-aware job scheduler for AutoFarm Zero — Success Guru Network v6.0.

Controls concurrency of heavy jobs based on system resource availability.
Prevents OOM kills on the 20GB content-vm by checking RAM, CPU, and disk
before starting resource-intensive operations.

Resource thresholds:
- Video assembly: requires 4GB free RAM, <70% CPU
- TTS generation: requires 2GB free RAM
- LLM inference: requires 6GB free RAM (Ollama model loading)
- Background download: requires 1GB free RAM, <80% disk used

Concurrency rules:
- Only 1 video assembly job runs at a time
- TTS and LLM never run concurrently with video assembly
- Background downloads are throttled when disk is filling up
"""

import os
import time
import threading
import logging
from datetime import datetime, timezone
from typing import Optional

import psutil
import structlog

logger = structlog.get_logger(__name__)


class ResourceScheduler:
    """
    Controls concurrency of heavy jobs based on system resources.
    Prevents OOM kills on the 20GB content-vm.

    Provides two main methods:
    - can_start_job(): Non-blocking check if resources are available.
    - wait_for_resources(): Blocks until resources are available or timeout.

    Also manages a concurrency lock for video assembly to ensure
    only one runs at a time.

    Attributes:
        THRESHOLDS: Per-job-type resource requirements.
        MAX_CONCURRENT_VIDEO_ASSEMBLY: Max concurrent video assemblies (1).
        DEFAULT_WAIT_TIMEOUT: Default timeout for wait_for_resources().
    """

    THRESHOLDS: dict = {
        'video_assembly': {
            'min_free_ram_gb': 4,
            'max_cpu_percent': 70,
            'exclusive': True,  # Only 1 at a time
        },
        'tts_generation': {
            'min_free_ram_gb': 2,
            'max_cpu_percent': 80,
            'conflicts_with': ['video_assembly'],
        },
        'llm_inference': {
            'min_free_ram_gb': 6,
            'max_cpu_percent': 80,
            'conflicts_with': ['video_assembly'],
        },
        'background_download': {
            'min_free_ram_gb': 1,
            'max_disk_percent': 80,
        },
        'trend_scanning': {
            'min_free_ram_gb': 0.5,
            'max_cpu_percent': 90,
        },
        'analytics_pull': {
            'min_free_ram_gb': 0.5,
            'max_cpu_percent': 90,
        },
    }

    MAX_CONCURRENT_VIDEO_ASSEMBLY: int = 1
    DEFAULT_WAIT_TIMEOUT: int = 600  # 10 minutes

    def __init__(self) -> None:
        """
        Initializes the ResourceScheduler with concurrency tracking.

        Side effects:
            Creates threading locks and active job tracking.
        """
        self._active_jobs: dict[str, dict] = {}
        self._lock: threading.Lock = threading.Lock()
        self._video_assembly_semaphore: threading.Semaphore = \
            threading.Semaphore(self.MAX_CONCURRENT_VIDEO_ASSEMBLY)
        self._job_counter: int = 0

    def can_start_job(self, job_type: str) -> tuple[bool, str]:
        """
        Checks if system resources allow starting a job of this type.

        Parameters:
            job_type: Type of job (must be a key in THRESHOLDS or
                      any arbitrary string for unconstrained jobs).

        Returns:
            Tuple of (can_start: bool, reason: str).
            If can_start is False, reason explains why.

        Side effects:
            Reads psutil metrics (CPU with 1-second interval).
        """
        if job_type not in self.THRESHOLDS:
            return True, "No resource constraints for this job type"

        thresholds = self.THRESHOLDS[job_type]

        # Check exclusive lock (video assembly)
        if thresholds.get('exclusive'):
            with self._lock:
                active_of_type = sum(
                    1 for j in self._active_jobs.values()
                    if j['job_type'] == job_type
                )
                if active_of_type >= self.MAX_CONCURRENT_VIDEO_ASSEMBLY:
                    return False, (
                        f"Max concurrent {job_type} jobs reached "
                        f"({self.MAX_CONCURRENT_VIDEO_ASSEMBLY})"
                    )

        # Check conflicts
        conflicts_with = thresholds.get('conflicts_with', [])
        if conflicts_with:
            with self._lock:
                for conflict_type in conflicts_with:
                    active_conflicts = sum(
                        1 for j in self._active_jobs.values()
                        if j['job_type'] == conflict_type
                    )
                    if active_conflicts > 0:
                        return False, (
                            f"{job_type} conflicts with active "
                            f"{conflict_type} job"
                        )

        # Check RAM
        mem = psutil.virtual_memory()
        free_ram_gb = mem.available / (1024 ** 3)
        min_ram = thresholds.get('min_free_ram_gb', 0)
        if free_ram_gb < min_ram:
            return False, (
                f"Insufficient RAM: {free_ram_gb:.1f}GB free, "
                f"need {min_ram}GB"
            )

        # Check CPU
        max_cpu = thresholds.get('max_cpu_percent', 100)
        if max_cpu < 100:
            cpu_percent = psutil.cpu_percent(interval=1)
            if cpu_percent > max_cpu:
                return False, (
                    f"CPU too high: {cpu_percent:.1f}%, "
                    f"max {max_cpu}%"
                )

        # Check disk
        max_disk = thresholds.get('max_disk_percent', 100)
        if max_disk < 100:
            try:
                disk = psutil.disk_usage('/app')
            except FileNotFoundError:
                disk = psutil.disk_usage('/')
            if disk.percent > max_disk:
                return False, (
                    f"Disk too full: {disk.percent:.1f}%, "
                    f"max {max_disk}%"
                )

        return True, "Resources available"

    def wait_for_resources(self, job_type: str,
                           max_wait_seconds: int = None) -> bool:
        """
        Blocks until resources are available or timeout.

        Parameters:
            job_type: Type of job to wait for resources.
            max_wait_seconds: Maximum wait time. Defaults to DEFAULT_WAIT_TIMEOUT.

        Returns:
            True if resources became available, False if timed out.

        Side effects:
            Blocks the calling thread.
            Logs periodic status updates.
            Checks shutdown handler to abort early.
        """
        if max_wait_seconds is None:
            max_wait_seconds = self.DEFAULT_WAIT_TIMEOUT

        start = time.time()
        check_interval = 30  # Check every 30 seconds
        attempt = 0

        while time.time() - start < max_wait_seconds:
            # Check if system is shutting down
            try:
                from modules.infrastructure.shutdown_handler import \
                    is_shutting_down
                if is_shutting_down():
                    logger.info("resource_wait_aborted_shutdown",
                                  job_type=job_type)
                    return False
            except ImportError:
                pass

            can_start, reason = self.can_start_job(job_type)
            if can_start:
                return True

            attempt += 1
            elapsed = time.time() - start
            logger.info("waiting_for_resources",
                          job_type=job_type,
                          reason=reason,
                          attempt=attempt,
                          elapsed_seconds=round(elapsed, 1),
                          max_wait_seconds=max_wait_seconds)

            time.sleep(check_interval)

        logger.warning("resource_wait_timeout",
                         job_type=job_type,
                         waited_seconds=max_wait_seconds)
        return False

    def acquire_job_slot(self, job_type: str,
                          brand_id: str = "",
                          description: str = "") -> Optional[str]:
        """
        Acquires a resource slot for a job. Must be released when done.

        Parameters:
            job_type: Type of job being started.
            brand_id: Brand this job is for (for logging/tracking).
            description: Human-readable job description.

        Returns:
            Job slot ID if acquired, None if resources unavailable.

        Side effects:
            Registers the job as active.
            Acquires semaphore for exclusive job types.
            Records resource snapshot.
        """
        can_start, reason = self.can_start_job(job_type)
        if not can_start:
            logger.info("job_slot_denied",
                          job_type=job_type,
                          brand_id=brand_id,
                          reason=reason)
            return None

        # Acquire exclusive semaphore if needed
        thresholds = self.THRESHOLDS.get(job_type, {})
        if thresholds.get('exclusive'):
            acquired = self._video_assembly_semaphore.acquire(blocking=False)
            if not acquired:
                logger.info("exclusive_slot_busy",
                              job_type=job_type)
                return None

        with self._lock:
            self._job_counter += 1
            slot_id = f"{job_type}_{self._job_counter}"

            self._active_jobs[slot_id] = {
                'job_type': job_type,
                'brand_id': brand_id,
                'description': description,
                'started_at': datetime.now(timezone.utc).isoformat(),
                'exclusive': thresholds.get('exclusive', False),
            }

        # Record resource snapshot
        self._record_resource_snapshot(job_type, brand_id, 'acquired')

        logger.info("job_slot_acquired",
                      slot_id=slot_id,
                      job_type=job_type,
                      brand_id=brand_id,
                      active_jobs=len(self._active_jobs))

        return slot_id

    def release_job_slot(self, slot_id: str) -> None:
        """
        Releases a previously acquired job slot.

        Parameters:
            slot_id: The slot ID returned by acquire_job_slot().

        Side effects:
            Removes job from active tracking.
            Releases semaphore for exclusive job types.
            Records resource snapshot.
        """
        with self._lock:
            job_info = self._active_jobs.pop(slot_id, None)

        if job_info is None:
            logger.warning("unknown_slot_release", slot_id=slot_id)
            return

        # Release exclusive semaphore
        if job_info.get('exclusive'):
            self._video_assembly_semaphore.release()

        # Record resource snapshot
        self._record_resource_snapshot(
            job_info['job_type'],
            job_info.get('brand_id', ''),
            'released'
        )

        logger.info("job_slot_released",
                      slot_id=slot_id,
                      job_type=job_info['job_type'],
                      brand_id=job_info.get('brand_id', ''),
                      active_jobs=len(self._active_jobs))

    def get_active_jobs(self) -> dict[str, dict]:
        """
        Returns currently active jobs for monitoring.

        Returns:
            Dict mapping slot_id to job info.

        Side effects:
            None (read-only with lock).
        """
        with self._lock:
            return dict(self._active_jobs)

    def get_resource_status(self) -> dict:
        """
        Returns current system resource status.

        Returns:
            Dict with cpu_percent, ram_free_gb, ram_total_gb,
            disk_used_percent, active_jobs, and per-job-type availability.

        Side effects:
            Reads psutil metrics.
        """
        mem = psutil.virtual_memory()
        try:
            disk = psutil.disk_usage('/app')
        except FileNotFoundError:
            disk = psutil.disk_usage('/')

        status = {
            'cpu_percent': psutil.cpu_percent(interval=0.1),
            'ram_free_gb': round(mem.available / (1024 ** 3), 2),
            'ram_total_gb': round(mem.total / (1024 ** 3), 2),
            'ram_percent': mem.percent,
            'disk_used_percent': disk.percent,
            'disk_free_gb': round(disk.free / (1024 ** 3), 2),
            'active_jobs': len(self._active_jobs),
            'active_job_details': self.get_active_jobs(),
        }

        # Check availability for each job type
        availability = {}
        for job_type in self.THRESHOLDS:
            can_start, reason = self.can_start_job(job_type)
            availability[job_type] = {
                'available': can_start,
                'reason': reason,
            }
        status['job_availability'] = availability

        return status

    def _record_resource_snapshot(self, job_type: str,
                                   brand_id: str,
                                   event: str) -> None:
        """
        Records a resource snapshot to the database for analysis.

        Parameters:
            job_type: Type of job that triggered the snapshot.
            brand_id: Brand the job is for.
            event: Event type ('acquired' or 'released').

        Side effects:
            Inserts a row into resource_snapshots table.
        """
        try:
            from database.db import Database
            db = Database()

            mem = psutil.virtual_memory()
            try:
                disk = psutil.disk_usage('/app')
            except FileNotFoundError:
                disk = psutil.disk_usage('/')

            db.execute_write(
                """INSERT INTO resource_snapshots
                   (cpu_percent, ram_used_gb, ram_free_gb, disk_used_percent,
                    active_jobs, event_type, job_type, brand_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    psutil.cpu_percent(interval=0),
                    round(mem.used / (1024 ** 3), 2),
                    round(mem.available / (1024 ** 3), 2),
                    disk.percent,
                    len(self._active_jobs),
                    event,
                    job_type,
                    brand_id,
                )
            )
        except Exception as e:
            # Don't fail jobs because of metrics recording
            logger.debug("resource_snapshot_failed", error=str(e))


# Global scheduler instance
_scheduler: Optional[ResourceScheduler] = None


def get_scheduler() -> ResourceScheduler:
    """
    Returns the global ResourceScheduler singleton.

    Returns:
        ResourceScheduler instance, created on first call.

    Side effects:
        Creates the singleton on first invocation.
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = ResourceScheduler()
    return _scheduler
