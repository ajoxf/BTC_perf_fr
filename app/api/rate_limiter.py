"""
Rate limiter for API requests
"""
import time
import threading
from collections import deque


class RateLimiter:
    """Token bucket rate limiter"""

    def __init__(self, requests_per_second: int = 10):
        self.requests_per_second = requests_per_second
        self.min_interval = 1.0 / requests_per_second
        self.request_times = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        """Wait if necessary to respect rate limit"""
        with self.lock:
            now = time.time()

            # Remove old requests outside the window
            while self.request_times and self.request_times[0] < now - 1.0:
                self.request_times.popleft()

            # Check if we're at the limit
            if len(self.request_times) >= self.requests_per_second:
                sleep_time = self.request_times[0] + 1.0 - now
                if sleep_time > 0:
                    time.sleep(sleep_time)

            self.request_times.append(time.time())

    def can_request(self) -> bool:
        """Check if a request can be made without waiting"""
        with self.lock:
            now = time.time()

            # Remove old requests
            while self.request_times and self.request_times[0] < now - 1.0:
                self.request_times.popleft()

            return len(self.request_times) < self.requests_per_second
