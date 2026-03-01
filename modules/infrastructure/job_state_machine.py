"""
Job state machine for AutoFarm Zero — Success Guru Network v6.0.

Tracks content jobs through their lifecycle from trend discovery to publication.
All state transitions MUST go through this module — direct state updates forbidden.

States:
    TREND_FOUND -> SCRIPT_DRAFT -> SCRIPT_SAFETY_CHECK -> TTS_QUEUED -> TTS_DONE
    -> VIDEO_ASSEMBLY -> VIDEO_ASSEMBLED -> QUALITY_CHECK -> QUALITY_PASSED
    -> REVIEW_PENDING -> REVIEW_APPROVED -> SCHEDULED -> PUBLISHING -> PUBLISHED

Failed jobs can be retried from their last successful state.
Orphaned jobs (stuck in non-terminal states >24h) are flagged by daily cleanup.
"""

import logging
from datetime import datetime, timedelta
from enum import Enum

from database.db import Database

logger = logging.getLogger(__name__)


class JobState(Enum):
    """All possible states for a content job in the pipeline."""
    TREND_FOUND = "trend_found"
    SCRIPT_DRAFT = "script_draft"
    SCRIPT_SAFETY_CHECK = "script_safety_check"
    TTS_QUEUED = "tts_queued"
    TTS_DONE = "tts_done"
    VIDEO_ASSEMBLY = "video_assembly"
    VIDEO_ASSEMBLED = "video_assembled"
    QUALITY_CHECK = "quality_check"
    QUALITY_PASSED = "quality_passed"
    REVIEW_PENDING = "review_pending"
    REVIEW_APPROVED = "review_approved"
    REVIEW_REJECTED = "review_rejected"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


# Valid state transitions map
VALID_TRANSITIONS: dict[JobState, list[JobState]] = {
    JobState.TREND_FOUND: [JobState.SCRIPT_DRAFT, JobState.FAILED],
    JobState.SCRIPT_DRAFT: [JobState.SCRIPT_SAFETY_CHECK, JobState.FAILED],
    JobState.SCRIPT_SAFETY_CHECK: [
        JobState.TTS_QUEUED, JobState.SCRIPT_DRAFT, JobState.FAILED
    ],
    JobState.TTS_QUEUED: [JobState.TTS_DONE, JobState.FAILED],
    JobState.TTS_DONE: [JobState.VIDEO_ASSEMBLY, JobState.FAILED],
    JobState.VIDEO_ASSEMBLY: [JobState.VIDEO_ASSEMBLED, JobState.FAILED],
    JobState.VIDEO_ASSEMBLED: [JobState.QUALITY_CHECK, JobState.FAILED],
    JobState.QUALITY_CHECK: [
        JobState.QUALITY_PASSED, JobState.SCRIPT_DRAFT, JobState.FAILED
    ],
    JobState.QUALITY_PASSED: [JobState.REVIEW_PENDING],
    JobState.REVIEW_PENDING: [JobState.REVIEW_APPROVED, JobState.REVIEW_REJECTED],
    JobState.REVIEW_APPROVED: [JobState.SCHEDULED],
    JobState.REVIEW_REJECTED: [JobState.SCRIPT_DRAFT, JobState.FAILED],
    JobState.SCHEDULED: [JobState.PUBLISHING, JobState.FAILED],
    JobState.PUBLISHING: [JobState.PUBLISHED, JobState.FAILED],
    # FAILED can retry from any earlier state
    JobState.FAILED: [
        JobState.TREND_FOUND, JobState.SCRIPT_DRAFT, JobState.TTS_QUEUED,
        JobState.VIDEO_ASSEMBLY, JobState.QUALITY_CHECK, JobState.REVIEW_PENDING,
        JobState.SCHEDULED, JobState.PUBLISHING,
    ],
}

# Terminal states — jobs in these states are complete
TERMINAL_STATES = {JobState.PUBLISHED, JobState.REVIEW_REJECTED}


class InvalidTransition(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, job_id: int, current_state: JobState, target_state: JobState):
        self.job_id = job_id
        self.current_state = current_state
        self.target_state = target_state
        super().__init__(
            f"Invalid transition for job {job_id}: "
            f"{current_state.value} -> {target_state.value}"
        )


class JobStateMachine:
    """
    Tracks content jobs through their lifecycle.

    All state transitions are validated against the VALID_TRANSITIONS map.
    Failed jobs can be retried from their last successful state.
    Orphan detection flags jobs stuck in non-terminal states beyond 24h.
    """

    def __init__(self):
        """
        Initializes the JobStateMachine with a database connection.
        """
        self.db = Database()

    def create_job(self, job_id: int, job_type: str, brand_id: str,
                   initial_state: JobState = JobState.TREND_FOUND) -> int:
        """
        Creates a new job tracking record.

        Parameters:
            job_id: The content job ID (typically script_id or video_id).
            job_type: Type of job ('content_generation', 'publish', etc.).
            brand_id: Brand this job belongs to.
            initial_state: Starting state for the job.

        Returns:
            The job_states row ID.

        Side effects:
            Inserts a new record into job_states table.
        """
        row_id = self.db.save_job_state(
            job_id=job_id,
            job_type=job_type,
            brand_id=brand_id,
            state=initial_state.value,
        )
        logger.info(
            "Job created",
            extra={
                'job_id': job_id,
                'job_type': job_type,
                'brand_id': brand_id,
                'state': initial_state.value,
            }
        )
        return row_id

    def transition(self, job_id: int, new_state: JobState,
                   error_message: str = None) -> bool:
        """
        Validates and records a state transition.

        Parameters:
            job_id: The content job ID.
            new_state: Target state to transition to.
            error_message: Error details (required for FAILED transitions).

        Returns:
            True if the transition was successful.

        Raises:
            InvalidTransition: If the transition is not valid.

        Side effects:
            Updates the job_states table with the new state.
            Logs the transition.
        """
        current = self.get_current_state(job_id)
        if current is None:
            logger.error(f"Job {job_id} not found in state machine")
            return False

        current_state = JobState(current['state'])

        # Validate transition
        allowed = VALID_TRANSITIONS.get(current_state, [])
        if new_state not in allowed:
            raise InvalidTransition(job_id, current_state, new_state)

        # Record the transition
        self.db.save_job_state(
            job_id=job_id,
            job_type=current['job_type'],
            brand_id=current['brand_id'],
            state=new_state.value,
            previous_state=current_state.value,
            error_message=error_message,
        )

        logger.info(
            "Job state transition",
            extra={
                'job_id': job_id,
                'brand_id': current['brand_id'],
                'from_state': current_state.value,
                'to_state': new_state.value,
                'error': error_message,
            }
        )
        return True

    def get_current_state(self, job_id: int) -> dict | None:
        """
        Retrieves the current state record for a job.

        Parameters:
            job_id: The content job ID.

        Returns:
            Job state dict with keys: job_id, job_type, brand_id, state,
            previous_state, error_message, retry_count, created_at, updated_at.
            None if not found.
        """
        return self.db.query_one(
            "SELECT * FROM job_states WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1",
            (job_id,)
        )

    def get_jobs_in_state(self, state: JobState) -> list[dict]:
        """
        Retrieves all jobs currently in a given state.

        Parameters:
            state: The JobState to filter by.

        Returns:
            List of job state dicts.
        """
        return self.db.query(
            "SELECT * FROM job_states WHERE state = ? ORDER BY updated_at DESC",
            (state.value,)
        )

    def get_retryable_jobs(self) -> list[dict]:
        """
        Returns jobs in FAILED state that can be retried.

        Returns:
            List of job state dicts with state 'failed' and retry_count < 3.
        """
        return self.db.query(
            """SELECT * FROM job_states
               WHERE state = 'failed' AND retry_count < 3
               ORDER BY updated_at ASC"""
        )

    def retry_job(self, job_id: int, target_state: JobState) -> bool:
        """
        Retries a failed job from a specified state.

        Parameters:
            job_id: The job to retry.
            target_state: State to retry from (must be earlier in pipeline).

        Returns:
            True if retry was initiated successfully.

        Raises:
            InvalidTransition: If the retry target is not valid from FAILED.

        Side effects:
            Increments retry_count on the job.
            Transitions to the target state.
        """
        current = self.get_current_state(job_id)
        if current is None or current['state'] != JobState.FAILED.value:
            logger.warning(f"Cannot retry job {job_id} — not in FAILED state")
            return False

        # Increment retry count
        self.db.pool.write_with_lock(
            "UPDATE job_states SET retry_count = retry_count + 1 WHERE job_id = ?",
            (job_id,)
        )

        return self.transition(job_id, target_state)

    def get_orphaned_jobs(self, max_age_hours: int = 24) -> list[dict]:
        """
        Returns jobs stuck in non-terminal states beyond max_age.

        Parameters:
            max_age_hours: Maximum hours a job can be in a non-terminal state.

        Returns:
            List of job state dicts that appear to be orphaned.
        """
        terminal_states = [s.value for s in TERMINAL_STATES]
        terminal_str = ', '.join([f"'{s}'" for s in terminal_states])

        return self.db.query(
            f"""SELECT * FROM job_states
                WHERE state NOT IN ({terminal_str})
                AND state != 'failed'
                AND updated_at < datetime('now', ?)
                ORDER BY updated_at ASC""",
            (f'-{max_age_hours} hours',)
        )

    def cleanup_orphans(self, max_age_hours: int = 24) -> int:
        """
        Marks old orphaned jobs as FAILED and schedules cleanup.

        Daily job: finds jobs stuck in non-terminal states beyond max_age,
        marks them as FAILED with an error message, and flags associated
        partial files for deletion.

        Parameters:
            max_age_hours: Maximum hours before a job is considered orphaned.

        Returns:
            Number of orphaned jobs cleaned up.

        Side effects:
            Transitions orphaned jobs to FAILED state.
            Logs each orphaned job.
        """
        orphans = self.get_orphaned_jobs(max_age_hours)
        cleaned = 0

        for orphan in orphans:
            try:
                self.db.save_job_state(
                    job_id=orphan['job_id'],
                    job_type=orphan['job_type'],
                    brand_id=orphan['brand_id'],
                    state=JobState.FAILED.value,
                    previous_state=orphan['state'],
                    error_message=f"Orphaned: stuck in {orphan['state']} for >{max_age_hours}h",
                )
                cleaned += 1
                logger.warning(
                    "Cleaned orphaned job",
                    extra={
                        'job_id': orphan['job_id'],
                        'brand_id': orphan['brand_id'],
                        'stuck_state': orphan['state'],
                        'age_hours': max_age_hours,
                    }
                )
            except Exception as e:
                logger.error(f"Failed to clean orphan job {orphan['job_id']}: {e}")

        if cleaned > 0:
            logger.info(f"Cleaned {cleaned} orphaned jobs")
        return cleaned

    def get_pipeline_stats(self) -> dict:
        """
        Returns statistics about the current pipeline state.

        Returns:
            Dictionary with counts per state and overall metrics.
        """
        rows = self.db.query(
            "SELECT state, COUNT(*) as count FROM job_states GROUP BY state"
        )
        state_counts = {row['state']: row['count'] for row in rows}

        total = sum(state_counts.values())
        in_progress = sum(
            count for state, count in state_counts.items()
            if state not in [s.value for s in TERMINAL_STATES] and state != 'failed'
        )

        return {
            'state_counts': state_counts,
            'total_jobs': total,
            'in_progress': in_progress,
            'failed': state_counts.get('failed', 0),
            'published': state_counts.get('published', 0),
        }
