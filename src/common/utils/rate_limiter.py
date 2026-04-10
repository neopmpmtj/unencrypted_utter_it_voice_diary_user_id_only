"""
Rate Limiter Utility Module

Provides per-user rate limiting functionality using Django's cache framework.
Used to prevent abuse of resource-intensive endpoints like transcription.

Key Features:
- Per-user rate limiting
- Configurable time window and request limit
- Uses Django cache for distributed compatibility
- Returns remaining time until reset

Author: [Your Name]
Date: 2026-01-27
Version: 1.0.0
"""

import time
from typing import Tuple, Optional, Dict, Any
from django.core.cache import cache

from src.common.config import get_config
from src.common.logging_utils.logging_config import get_logger

logger = get_logger('rate_limiter')

# Per-tier transcription limiters (tier -> RateLimiter). Populated on first use for each tier.
_transcription_limiters: Dict[str, "RateLimiter"] = {}


class RateLimiter:
    """
    Per-user rate limiter using Django's cache framework.
    
    This class provides simple rate limiting functionality that tracks
    request counts per user within a sliding time window.
    
    Attributes:
        cache_key_prefix: Prefix for cache keys to avoid collisions
        max_requests: Maximum number of requests allowed per window
        window_seconds: Time window in seconds
        
    Example:
        >>> limiter = RateLimiter(max_requests=10, window_seconds=3600)
        >>> allowed, info = limiter.check_rate_limit(user_id=123)
        >>> if not allowed:
        ...     print(f"Rate limited. Try again in {info['retry_after_seconds']} seconds")
    """
    
    def __init__(
        self,
        cache_key_prefix: str = "rate_limit",
        max_requests: int = 10,
        window_seconds: int = 3600
    ):
        """
        Initialize the rate limiter.
        
        Args:
            cache_key_prefix: Prefix for cache keys (default: "rate_limit")
            max_requests: Maximum requests per window (default: 10)
            window_seconds: Time window in seconds (default: 3600 = 1 hour)
        """
        self.cache_key_prefix = cache_key_prefix
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        
        logger.debug(
            f"RateLimiter initialized: max_requests={max_requests}, "
            f"window_seconds={window_seconds}, prefix={cache_key_prefix}"
        )
    
    def _get_cache_key(self, user_id: int) -> str:
        """
        Generate cache key for a specific user.
        
        Args:
            user_id: User ID to generate key for
            
        Returns:
            str: Cache key string
        """
        return f"{self.cache_key_prefix}:{user_id}"
    
    def check_rate_limit(self, user_id: int) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if user has exceeded rate limit.
        
        This method checks the current request count for the user and returns
        whether the request is allowed. If allowed, it increments the counter.
        
        Args:
            user_id: User ID to check
            
        Returns:
            Tuple[bool, Dict]: (allowed, info_dict)
                - allowed: True if request is within rate limit
                - info_dict contains:
                    - requests_made: Number of requests in current window
                    - requests_remaining: Number of requests remaining
                    - window_seconds: Time window in seconds
                    - retry_after_seconds: Seconds until reset (if rate limited)
        """
        try:
            return self._check_rate_limit_impl(user_id)
        except Exception as e:
            logger.error(f"Rate limit cache error, failing open: {e}")
            return True, {}

    def _check_rate_limit_impl(self, user_id: int) -> Tuple[bool, Dict[str, Any]]:
        cache_key = self._get_cache_key(user_id)
        current_time = time.time()
        
        # Get current rate limit data from cache
        rate_data = cache.get(cache_key)
        
        if rate_data is None:
            # First request in this window
            rate_data = {
                'count': 1,
                'window_start': current_time
            }
            cache.set(cache_key, rate_data, timeout=self.window_seconds)
            
            logger.debug(f"Rate limit: user {user_id} - first request in window")
            
            return True, {
                'requests_made': 1,
                'requests_remaining': self.max_requests - 1,
                'window_seconds': self.window_seconds,
                'retry_after_seconds': 0
            }
        
        # Check if window has expired
        window_start = rate_data.get('window_start', 0)
        elapsed = current_time - window_start
        
        if elapsed >= self.window_seconds:
            # Window expired, reset counter
            rate_data = {
                'count': 1,
                'window_start': current_time
            }
            cache.set(cache_key, rate_data, timeout=self.window_seconds)
            
            logger.debug(f"Rate limit: user {user_id} - window expired, reset counter")
            
            return True, {
                'requests_made': 1,
                'requests_remaining': self.max_requests - 1,
                'window_seconds': self.window_seconds,
                'retry_after_seconds': 0
            }
        
        # Check if limit exceeded
        current_count = rate_data.get('count', 0)
        
        if current_count >= self.max_requests:
            # Rate limit exceeded
            retry_after = int(self.window_seconds - elapsed)
            
            logger.warning(
                f"Rate limit exceeded: user {user_id} - "
                f"{current_count}/{self.max_requests} requests, "
                f"retry after {retry_after}s"
            )
            
            return False, {
                'requests_made': current_count,
                'requests_remaining': 0,
                'window_seconds': self.window_seconds,
                'retry_after_seconds': retry_after
            }
        
        # Increment counter
        rate_data['count'] = current_count + 1
        remaining_ttl = int(self.window_seconds - elapsed)
        cache.set(cache_key, rate_data, timeout=remaining_ttl)
        
        logger.debug(
            f"Rate limit: user {user_id} - "
            f"{rate_data['count']}/{self.max_requests} requests"
        )
        
        return True, {
            'requests_made': rate_data['count'],
            'requests_remaining': self.max_requests - rate_data['count'],
            'window_seconds': self.window_seconds,
            'retry_after_seconds': 0
        }
    
    def get_remaining_requests(self, user_id: int) -> int:
        """
        Get the number of remaining requests for a user.
        
        Args:
            user_id: User ID to check
            
        Returns:
            int: Number of remaining requests in current window
        """
        cache_key = self._get_cache_key(user_id)
        current_time = time.time()
        
        rate_data = cache.get(cache_key)
        
        if rate_data is None:
            return self.max_requests
        
        window_start = rate_data.get('window_start', 0)
        elapsed = current_time - window_start
        
        if elapsed >= self.window_seconds:
            return self.max_requests
        
        current_count = rate_data.get('count', 0)
        return max(0, self.max_requests - current_count)
    
    def reset_user_limit(self, user_id: int) -> None:
        """
        Reset the rate limit for a specific user.
        
        Useful for administrative purposes or testing.
        
        Args:
            user_id: User ID to reset
        """
        cache_key = self._get_cache_key(user_id)
        cache.delete(cache_key)
        logger.info(f"Rate limit reset for user {user_id}")


def _get_transcription_limiter_for_tier(tier: str) -> RateLimiter:
    """Get or create a RateLimiter for the given tier. Uses config for limits."""
    if tier not in _transcription_limiters:
        config = get_config()
        max_requests, window_seconds = config.transcription_limits.get_limits_for_tier(tier)
        _transcription_limiters[tier] = RateLimiter(
            cache_key_prefix=f"transcription_rate:{tier}",
            max_requests=max_requests,
            window_seconds=window_seconds,
        )
    return _transcription_limiters[tier]


def check_transcription_rate_limit(user) -> Tuple[bool, Dict[str, Any]]:
    """
    Check if the user is within their tier's transcription rate limit.
    If user is None, returns (True, {}) (no limit applied).
    App admin users bypass rate limits.
    When user.tier exists later, tier is read from user; until then tier is 'free'.
    """
    if user is None:
        return True, {}
    if getattr(user, "is_app_admin", False):
        return True, {}
    tier = getattr(user, "tier", None) or "free"
    limiter = _get_transcription_limiter_for_tier(tier)
    return limiter.check_rate_limit(user.id)


class IdentifierRateLimiter:
    """
    Rate limiter for unauthenticated endpoints using string identifiers (IP, email).

    Same sliding-window logic as RateLimiter but uses a string key instead of user_id.
    Used for password reset, resend verification, and login attempts by IP.
    """

    def __init__(
        self,
        cache_key_prefix: str = "rate_limit",
        max_requests: int = 10,
        window_seconds: int = 3600
    ):
        self.cache_key_prefix = cache_key_prefix
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def _get_cache_key(self, identifier: str) -> str:
        return f"{self.cache_key_prefix}:{identifier}"

    def check_rate_limit(self, identifier: str) -> Tuple[bool, Dict[str, Any]]:
        try:
            return self._check_rate_limit_impl(identifier)
        except Exception as e:
            logger.error(f"Rate limit cache error, failing open: {e}")
            return True, {}

    def _check_rate_limit_impl(self, identifier: str) -> Tuple[bool, Dict[str, Any]]:
        cache_key = self._get_cache_key(identifier)
        current_time = time.time()
        rate_data = cache.get(cache_key)

        if rate_data is None:
            rate_data = {'count': 1, 'window_start': current_time}
            cache.set(cache_key, rate_data, timeout=self.window_seconds)
            return True, {
                'requests_made': 1,
                'requests_remaining': self.max_requests - 1,
                'window_seconds': self.window_seconds,
                'retry_after_seconds': 0
            }

        window_start = rate_data.get('window_start', 0)
        elapsed = current_time - window_start

        if elapsed >= self.window_seconds:
            rate_data = {'count': 1, 'window_start': current_time}
            cache.set(cache_key, rate_data, timeout=self.window_seconds)
            return True, {
                'requests_made': 1,
                'requests_remaining': self.max_requests - 1,
                'window_seconds': self.window_seconds,
                'retry_after_seconds': 0
            }

        current_count = rate_data.get('count', 0)
        if current_count >= self.max_requests:
            retry_after = int(self.window_seconds - elapsed)
            logger.warning(
                f"Rate limit exceeded: {self.cache_key_prefix} - "
                f"{current_count}/{self.max_requests} requests"
            )
            return False, {
                'requests_made': current_count,
                'requests_remaining': 0,
                'window_seconds': self.window_seconds,
                'retry_after_seconds': retry_after
            }

        rate_data['count'] = current_count + 1
        remaining_ttl = int(self.window_seconds - elapsed)
        cache.set(cache_key, rate_data, timeout=remaining_ttl)
        return True, {
            'requests_made': rate_data['count'],
            'requests_remaining': self.max_requests - rate_data['count'],
            'window_seconds': self.window_seconds,
            'retry_after_seconds': 0
        }

    def reset_limit(self, identifier: str) -> None:
        cache_key = self._get_cache_key(identifier)
        cache.delete(cache_key)


# Auth-related rate limiters (IP-based)
password_reset_limiter = IdentifierRateLimiter(
    cache_key_prefix="auth_password_reset",
    max_requests=5,
    window_seconds=3600
)
resend_verification_limiter = IdentifierRateLimiter(
    cache_key_prefix="auth_resend_verification",
    max_requests=3,
    window_seconds=3600
)
login_attempt_limiter = IdentifierRateLimiter(
    cache_key_prefix="auth_login",
    max_requests=10,
    window_seconds=3600
)
