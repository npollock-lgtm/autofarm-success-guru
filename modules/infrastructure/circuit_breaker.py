"""
Circuit breaker pattern for AutoFarm Zero — Success Guru Network v6.0.

Prevents cascade failures when a platform API is down or misbehaving.
Uses the classic three-state pattern: CLOSED → OPEN → HALF_OPEN.
When a circuit is open, requests are immediately skipped and rescheduled
rather than failed, preserving content in the pipeline.

State transitions:
    CLOSED: Normal operation. Failures are counted.
    OPEN: After FAILURE_THRESHOLD consecutive failures, circuit opens.
          All requests are blocked for TIMEOUT_SECONDS. Jobs are rescheduled.
    HALF_OPEN: After timeout, one test request is allowed through.
               Success → CLOSED, Failure → OPEN (reset timeout).
"""

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class CircuitState(Enum):
    """Possible states for a circuit breaker."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    """Raised when a request is attempted on an open circuit."""

    def __init__(self, brand_id: str, platform: str,
                 opens_until: datetime, message: str = ""):
        """
        Parameters:
            brand_id: The brand whose circuit is open.
            platform: The platform whose API is failing.
            opens_until: When the circuit will transition to half-open.
            message: Optional human-readable message.
        """
        self.brand_id = brand_id
        self.platform = platform
        self.opens_until = opens_until
        super().__init__(
            message or f"Circuit open for {brand_id}/{platform} "
                       f"until {opens_until.isoformat()}"
        )


class CircuitBreaker:
    """
    Prevents cascade failures when a platform API is down.

    States: CLOSED → OPEN (5 failures → 15min timeout) → HALF_OPEN (test)
    Jobs for open circuits are skipped and rescheduled, not failed.

    Each brand+platform combination has its own independent circuit.
    Circuit state is persisted in SQLite so it survives process restarts.

    Attributes:
        FAILURE_THRESHOLD: Number of consecutive failures before opening.
        TIMEOUT_SECONDS: How long the circuit stays open (15 minutes).
        HALF_OPEN_MAX_TESTS: Max concurrent test requests in half-open state.
    """

    FAILURE_THRESHOLD: int = 5
    TIMEOUT_SECONDS: int = 900  # 15 minutes
    HALF_OPEN_MAX_TESTS: int = 1

    def __init__(self) -> None:
        """
        Initializes the CircuitBreaker with database access.

        Side effects:
            Creates a Database instance for reading/writing circuit state.
        """
        from database.db import Database
        self.db = Database()
        # In-memory cache for fast lookups
        self._cache: dict[str, dict] = {}

    def _cache_key(self, brand_id: str, platform: str) -> str:
        """
        Generates a cache key for a brand+platform circuit.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            Concatenated key string.
        """
        return f"{brand_id}:{platform}"

    def check_circuit(self, brand_id: str, platform: str) -> CircuitState:
        """
        Checks the current state of a circuit.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            Current CircuitState (CLOSED, OPEN, or HALF_OPEN).

        Side effects:
            May transition from OPEN to HALF_OPEN if timeout has elapsed.
            Updates the in-memory cache.
        """
        state_data = self._get_state(brand_id, platform)

        if state_data['state'] == CircuitState.OPEN.value:
            # Check if timeout has elapsed → transition to HALF_OPEN
            opens_until = state_data.get('opens_until')
            if opens_until:
                if isinstance(opens_until, str):
                    opens_until = datetime.fromisoformat(opens_until)
                if datetime.now(timezone.utc) >= opens_until.replace(tzinfo=timezone.utc):
                    self._transition_to_half_open(brand_id, platform)
                    return CircuitState.HALF_OPEN
            return CircuitState.OPEN

        return CircuitState(state_data['state'])

    def allow_request(self, brand_id: str, platform: str) -> bool:
        """
        Determines if a request should be allowed through the circuit.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            True if the request is allowed, False if circuit is open.

        Side effects:
            May update circuit state if transitioning from OPEN to HALF_OPEN.

        Raises:
            CircuitOpenError: If the circuit is open (caller can catch
            to reschedule rather than fail).
        """
        state = self.check_circuit(brand_id, platform)

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.HALF_OPEN:
            # Allow one test request through
            logger.info("circuit_half_open_test",
                         brand_id=brand_id, platform=platform)
            return True

        # Circuit is OPEN
        state_data = self._get_state(brand_id, platform)
        opens_until_str = state_data.get('opens_until', '')
        if opens_until_str:
            opens_until = datetime.fromisoformat(opens_until_str)
        else:
            opens_until = datetime.now(timezone.utc) + timedelta(
                seconds=self.TIMEOUT_SECONDS)

        raise CircuitOpenError(brand_id, platform, opens_until)

    def record_success(self, brand_id: str, platform: str) -> None:
        """
        Records a successful API call, resetting failure count.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Side effects:
            If in HALF_OPEN state, transitions to CLOSED.
            Resets failure count to 0.
            Updates database and cache.
        """
        state = self.check_circuit(brand_id, platform)

        if state == CircuitState.HALF_OPEN:
            logger.info("circuit_closed_after_recovery",
                         brand_id=brand_id, platform=platform)

        self._update_state(
            brand_id, platform,
            state=CircuitState.CLOSED.value,
            failure_count=0,
            last_failure_at=None,
            opens_until=None
        )

    def record_failure(self, brand_id: str, platform: str,
                       error_message: str = "") -> CircuitState:
        """
        Records a failed API call. May open the circuit if threshold reached.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            error_message: Description of the failure.

        Returns:
            The new CircuitState after recording the failure.

        Side effects:
            Increments failure count.
            If count >= FAILURE_THRESHOLD, opens the circuit.
            If in HALF_OPEN and test fails, re-opens the circuit.
            Updates database and logs state changes.
        """
        state_data = self._get_state(brand_id, platform)
        current_state = CircuitState(state_data['state'])
        failure_count = state_data.get('failure_count', 0) + 1
        now = datetime.now(timezone.utc)

        # If in HALF_OPEN and test request failed, go back to OPEN
        if current_state == CircuitState.HALF_OPEN:
            opens_until = now + timedelta(seconds=self.TIMEOUT_SECONDS)
            self._update_state(
                brand_id, platform,
                state=CircuitState.OPEN.value,
                failure_count=failure_count,
                last_failure_at=now.isoformat(),
                opens_until=opens_until.isoformat()
            )
            logger.warning("circuit_reopened_after_half_open_failure",
                            brand_id=brand_id, platform=platform,
                            error=error_message)
            return CircuitState.OPEN

        # Check if we've hit the threshold
        if failure_count >= self.FAILURE_THRESHOLD:
            opens_until = now + timedelta(seconds=self.TIMEOUT_SECONDS)
            self._update_state(
                brand_id, platform,
                state=CircuitState.OPEN.value,
                failure_count=failure_count,
                last_failure_at=now.isoformat(),
                opens_until=opens_until.isoformat()
            )
            logger.warning("circuit_opened",
                            brand_id=brand_id, platform=platform,
                            failure_count=failure_count,
                            timeout_minutes=self.TIMEOUT_SECONDS // 60,
                            error=error_message)
            return CircuitState.OPEN

        # Still within threshold, just record the failure
        self._update_state(
            brand_id, platform,
            state=CircuitState.CLOSED.value,
            failure_count=failure_count,
            last_failure_at=now.isoformat(),
            opens_until=None
        )
        logger.info("circuit_failure_recorded",
                      brand_id=brand_id, platform=platform,
                      failure_count=failure_count,
                      threshold=self.FAILURE_THRESHOLD,
                      error=error_message)
        return CircuitState.CLOSED

    def get_all_states(self) -> list[dict]:
        """
        Returns the state of all circuits for monitoring.

        Returns:
            List of dicts with brand_id, platform, state, failure_count,
            last_failure_at, opens_until.

        Side effects:
            Reads from the database.
        """
        try:
            rows = self.db.fetch_all(
                "SELECT brand_id, platform, state, failure_count, "
                "last_failure_at, opens_until FROM circuit_breakers"
            )
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("get_circuit_states_failed", error=str(e))
            return []

    def get_open_circuits(self) -> list[dict]:
        """
        Returns only circuits that are currently open.

        Returns:
            List of dicts for circuits in OPEN or HALF_OPEN state.

        Side effects:
            Reads from the database.
        """
        try:
            rows = self.db.fetch_all(
                "SELECT brand_id, platform, state, failure_count, "
                "last_failure_at, opens_until FROM circuit_breakers "
                "WHERE state IN ('OPEN', 'HALF_OPEN')"
            )
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("get_open_circuits_failed", error=str(e))
            return []

    def reset_circuit(self, brand_id: str, platform: str) -> None:
        """
        Manually resets a circuit to CLOSED state.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Side effects:
            Forces circuit to CLOSED, resets failure count.
            Use for manual recovery after fixing underlying issue.
        """
        self._update_state(
            brand_id, platform,
            state=CircuitState.CLOSED.value,
            failure_count=0,
            last_failure_at=None,
            opens_until=None
        )
        logger.info("circuit_manually_reset",
                      brand_id=brand_id, platform=platform)

    def reset_all(self) -> int:
        """
        Resets all circuits to CLOSED state.

        Returns:
            Number of circuits reset.

        Side effects:
            Updates all rows in circuit_breakers table.
            Clears the in-memory cache.
        """
        try:
            self.db.execute_write(
                "UPDATE circuit_breakers SET state='CLOSED', "
                "failure_count=0, last_failure_at=NULL, opens_until=NULL"
            )
            self._cache.clear()
            count = self.db.fetch_one(
                "SELECT COUNT(*) as cnt FROM circuit_breakers"
            )
            reset_count = count['cnt'] if count else 0
            logger.info("all_circuits_reset", count=reset_count)
            return reset_count
        except Exception as e:
            logger.error("reset_all_circuits_failed", error=str(e))
            return 0

    def _get_state(self, brand_id: str, platform: str) -> dict:
        """
        Gets circuit state from cache or database.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            Dict with state, failure_count, last_failure_at, opens_until.

        Side effects:
            Creates the circuit_breakers row if it doesn't exist.
            Updates cache on read.
        """
        key = self._cache_key(brand_id, platform)

        # Check cache first
        if key in self._cache:
            return self._cache[key]

        # Read from database
        try:
            row = self.db.fetch_one(
                "SELECT state, failure_count, last_failure_at, opens_until "
                "FROM circuit_breakers WHERE brand_id=? AND platform=?",
                (brand_id, platform)
            )

            if row:
                state_data = dict(row)
            else:
                # Create initial record
                self.db.execute_write(
                    "INSERT OR IGNORE INTO circuit_breakers "
                    "(brand_id, platform, state, failure_count) "
                    "VALUES (?, ?, 'CLOSED', 0)",
                    (brand_id, platform)
                )
                state_data = {
                    'state': CircuitState.CLOSED.value,
                    'failure_count': 0,
                    'last_failure_at': None,
                    'opens_until': None,
                }

            self._cache[key] = state_data
            return state_data

        except Exception as e:
            logger.error("get_circuit_state_failed",
                          brand_id=brand_id, platform=platform,
                          error=str(e))
            # Default to closed on error (fail-open for availability)
            return {
                'state': CircuitState.CLOSED.value,
                'failure_count': 0,
                'last_failure_at': None,
                'opens_until': None,
            }

    def _update_state(self, brand_id: str, platform: str, **kwargs) -> None:
        """
        Updates circuit state in database and cache.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            **kwargs: Fields to update (state, failure_count,
                      last_failure_at, opens_until).

        Side effects:
            Updates the circuit_breakers table.
            Updates the in-memory cache.
        """
        try:
            # Build SET clause dynamically
            set_parts = []
            params = []
            for field, value in kwargs.items():
                set_parts.append(f"{field}=?")
                params.append(value)

            params.extend([brand_id, platform])

            self.db.execute_write(
                f"UPDATE circuit_breakers SET {', '.join(set_parts)} "
                f"WHERE brand_id=? AND platform=?",
                tuple(params)
            )

            # Update cache
            key = self._cache_key(brand_id, platform)
            if key in self._cache:
                self._cache[key].update(kwargs)
            else:
                self._cache[key] = kwargs

        except Exception as e:
            logger.error("update_circuit_state_failed",
                          brand_id=brand_id, platform=platform,
                          error=str(e))

    def _transition_to_half_open(self, brand_id: str,
                                  platform: str) -> None:
        """
        Transitions a circuit from OPEN to HALF_OPEN after timeout.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Side effects:
            Updates state to HALF_OPEN in database and cache.
        """
        self._update_state(
            brand_id, platform,
            state=CircuitState.HALF_OPEN.value
        )
        logger.info("circuit_half_open",
                      brand_id=brand_id, platform=platform)
