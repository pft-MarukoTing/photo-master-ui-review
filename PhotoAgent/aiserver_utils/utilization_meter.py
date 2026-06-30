"""
UtilizationMeter - Singleton class for tracking utilization metrics with alternating time windows.

Thread-safe implementation that records process times and logs metrics at regular intervals.
"""
import os
import threading
import time
from typing import Optional
from .logger import logger
from .metrics import metrics_logger

class UtilizationMeter:
    """
    Singleton class to record utilization with alternating time windows.
    
    Uses two buckets (odd and even windows) to collect process times.
    Logs metrics every window interval using the bucket from the previous window.
    
    Thread-safe implementation using locks.
    """
    
    _instance: Optional['UtilizationMeter'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """
        Create singleton instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """
        Initialize the UtilizationMeter.
        
        The window size in seconds for metric logging intervals is configured
        via the UTILIZATION_WINDOW_SECONDS environment variable (default: 300).
        """
        # Prevent re-initialization of singleton
        if self._initialized:
            return
        
        self.window = int(os.getenv("UTILIZATION_WINDOW_SECONDS", "300")) * 1000  # Convert to milliseconds
        self.start_ts = time.time() * 1000  # Start timestamp in milliseconds
        
        # Two buckets for alternating windows
        self.odd_window_value = 0.0
        self.even_window_value = 0.0
        
        # Lock for thread-safe operations
        self._data_lock = threading.Lock()
        
        # Background thread for logging metrics
        self._stop_event = threading.Event()
        self._logging_thread = threading.Thread(target=self._log_metrics_loop, daemon=True)
        self._logging_thread.start()
        
        self._initialized = True
        logger.log(f"UtilizationMeter initialized with window={self.window}ms")
    
    def _get_current_window_number(self) -> int:
        """
        Calculate the current window number based on elapsed time.
        
        Returns:
            Window number (0, 1, 2, ...)
        """
        elapsed = time.time() * 1000 - self.start_ts
        return int(elapsed // self.window)
    
    def _is_odd_window(self) -> bool:
        """
        Check if current window is odd.
        
        Returns:
            True if odd window, False if even window
        """
        return self._get_current_window_number() % 2 == 1
    
    def record(self, process_time: float) -> None:
        """
        Record process time to the appropriate bucket based on current window.
        
        Thread-safe operation that adds process_time to either odd or even bucket.
        
        Args:
            process_time: Process time in milliseconds to record
        """
        if process_time < 0:
            return  # Ignore negative values
        
        # Snapshot the window at the start of the call so that the value is
        # attributed consistently, even if a window transition occurs while
        # this method is running.
        start_window = self._get_current_window_number()
        
        with self._data_lock:
            end_window = self._get_current_window_number()
            # If a window transition occurred during this call, attribute the
            # recorded time to the window at the start of the call.
            window_to_use = start_window if end_window != start_window else end_window
            if window_to_use % 2 == 1:
                self.odd_window_value += process_time
            else:
                self.even_window_value += process_time
    
    def _log_metrics_loop(self) -> None:
        """
        Background thread function that logs metrics every window milliseconds.
        
        On even window: logs and resets odd_window_value
        On odd window: logs and resets even_window_value
        """
        last_logged_window = -1
        
        while not self._stop_event.is_set():
            try:
                current_window = self._get_current_window_number()
                
                # Check if we've entered a new window
                if current_window > last_logged_window:
                    with self._data_lock:
                        utilization = 0.0
                        if current_window % 2 == 0:
                            # Even window: log and reset odd bucket
                            utilization = self.odd_window_value / self.window
                            self.odd_window_value = 0.0
                            logger.log(f"[Window {current_window}] Utilization (from odd bucket): {(utilization * 100):.3f}%")
                        else:
                            # Odd window: log and reset even bucket
                            utilization = self.even_window_value / self.window
                            self.even_window_value = 0.0
                            logger.log(f"[Window {current_window}] Utilization (from even bucket): {(utilization * 100):.3f}%")
                        metrics_logger.log_metrics(type="gauge", name="host_utilization", effect="", value=utilization)                    
                    last_logged_window = current_window
                
                # Calculate time until next window boundary
                elapsed = time.time() * 1000 - self.start_ts
                time_in_current_window = elapsed % self.window
                sleep_duration = self.window - time_in_current_window
                
                # Wait until next window (or until stop event is set)
                self._stop_event.wait(timeout=sleep_duration/1000)  # Convert to seconds
                
            except Exception as e:
                logger.log(f"Error in utilization meter logging loop: {e}")
    
    def stop(self) -> None:
        """
        Stop the background logging thread.
        
        Call this to gracefully shutdown the meter.
        """
        logger.log("Stopping UtilizationMeter...")
        self._stop_event.set()
        if self._logging_thread.is_alive():
            self._logging_thread.join(timeout=2.0)
        logger.log("UtilizationMeter stopped")
    
    def get_current_utilization(self) -> dict:
        """
        Get current utilization values from both buckets.
        
        Returns:
            Dictionary with current window info and bucket values
        """
        with self._data_lock:
            return {
                "current_window": self._get_current_window_number(),
                "is_odd_window": self._is_odd_window(),
                "odd_window_value": self.odd_window_value,
                "even_window_value": self.even_window_value,
                "elapsed_time": time.time() * 1000 - self.start_ts
            }
    
    @classmethod
    def reset_instance(cls) -> None:
        """
        Reset the singleton instance (useful for testing).
        """
        with cls._lock:
            if cls._instance is not None:
                cls._instance.stop()
                cls._instance = None

utilization_meter = UtilizationMeter()