"""Process and host memory reporting for the aiHair worker."""

import threading


BYTES_PER_MIB = 1024 ** 2
MEMORY_MONITOR_INTERVAL_SECONDS = 5


class MemoryMonitor:
    """Sample and report the complete process memory snapshot in one thread."""

    def __init__(self, log_func, interval_seconds=MEMORY_MONITOR_INTERVAL_SECONDS):
        self.log_func = log_func
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.current_rss_bytes = None
        self.peak_rss_bytes = None

    def start(self):
        self.log_memory_usage(emit=False)
        self.thread = threading.Thread(target=self._monitor, name='memory-monitor')
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        if self.thread is not None:
            self.stop_event.set()
            self.thread.join()
        self.log_memory_usage()

    def _monitor(self):
        while not self.stop_event.wait(self.interval_seconds):
            self.log_memory_usage()

    def log_memory_usage(self, emit=True):
        """Collect current and process-lifetime peak RSS.

        When ``emit`` is True, also collect and emit the complete cgroup,
        host, and /proc/meminfo report. When ``emit`` is False, update RSS
        state silently without emitting the report.
        """
        try:
            status = self._read_kv_file('/proc/self/status')
            meminfo = self._read_kv_file('/proc/meminfo')

            rss_bytes = self._kb_str_to_bytes(status.get('VmRSS', ''))
            with self.lock:
                self.current_rss_bytes = rss_bytes
                if rss_bytes is not None:
                    if self.peak_rss_bytes is None:
                        self.peak_rss_bytes = rss_bytes
                    else:
                        self.peak_rss_bytes = max(self.peak_rss_bytes, rss_bytes)
                current_rss_bytes = self.current_rss_bytes
                peak_rss_bytes = self.peak_rss_bytes

            if not emit:
                return

            cg_usage, cg_limit = self._get_cgroup_mem()
            cg_pct = round(cg_usage * 100.0 / cg_limit, 1) if (cg_usage is not None and cg_limit) else None

            mem_total_mb = self._kb_str_to_mb(meminfo.get('MemTotal', ''))
            mem_avail_mb = self._kb_str_to_mb(meminfo.get('MemAvailable', ''))
            host_used_pct = None
            if mem_total_mb and mem_avail_mb is not None:
                host_used_pct = round((mem_total_mb - mem_avail_mb) * 100.0 / mem_total_mb, 1)

            self._emit('[memusage] rss=%s peak_rss=%s cg=%s cg_limit=%s cg_pct=%s host_used_pct=%s host_avail=%s' % (
                self._bytes_to_mb(current_rss_bytes), self._bytes_to_mb(peak_rss_bytes),
                self._bytes_to_mb(cg_usage), self._bytes_to_mb(cg_limit),
                cg_pct if cg_pct is not None else 'NA',
                host_used_pct if host_used_pct is not None else 'NA',
                self._format_kb_mb(meminfo.get('MemAvailable', ''))))

            fields = (
                'MemTotal', 'MemFree', 'MemAvailable', 'Buffers', 'Cached', 'Dirty',
                'AnonPages', 'Mapped', 'Shmem', 'Slab', 'SReclaimable', 'SUnreclaim',
                'KernelStack', 'PageTables',
            )
            self._emit('[meminfo] ' + ' '.join(
                '%s=%s' % (key, self._format_kb_mb(meminfo.get(key, '')))
                for key in fields))
        except Exception as error:
            if emit:
                try:
                    self._emit('[memusage] failed to collect memory info: %s' % error)
                except Exception:
                    pass

    def _emit(self, message):
        if self.log_func is not None:
            self.log_func(message)
        else:
            print(message)

    @staticmethod
    def _read_kv_file(path):
        """Read a key/value file such as /proc/meminfo."""
        result = {}
        try:
            with open(path) as file_obj:
                for line in file_obj:
                    if ':' not in line:
                        continue
                    key, value = line.split(':', 1)
                    result[key.strip()] = value.strip()
        except Exception:
            pass
        return result

    @staticmethod
    def _kb_str_to_mb(value):
        """Convert a procfs value such as ``123456 kB`` to whole MB."""
        try:
            return int(value.split()[0]) // 1024
        except Exception:
            return None

    @staticmethod
    def _kb_str_to_bytes(value):
        try:
            return int(value.split()[0]) * 1024
        except Exception:
            return None

    @classmethod
    def _format_kb_mb(cls, value):
        value_mb = cls._kb_str_to_mb(value)
        return '%dMB' % value_mb if value_mb is not None else 'NA'

    @staticmethod
    def _bytes_to_mb(value):
        return '%dMB' % (value // BYTES_PER_MIB) if value is not None else 'NA'

    @staticmethod
    def _read_int_file(path):
        try:
            with open(path) as file_obj:
                return int(file_obj.read().strip())
        except Exception:
            return None

    @classmethod
    def _get_cgroup_mem(cls):
        """Return cgroup usage and limit in bytes for cgroup v2 or v1."""
        usage = cls._read_int_file('/sys/fs/cgroup/memory.current')
        limit = None
        if usage is not None:
            try:
                with open('/sys/fs/cgroup/memory.max') as file_obj:
                    raw_limit = file_obj.read().strip()
                limit = None if raw_limit == 'max' else int(raw_limit)
            except Exception:
                limit = None
            return usage, limit

        usage = cls._read_int_file('/sys/fs/cgroup/memory/memory.usage_in_bytes')
        limit = cls._read_int_file('/sys/fs/cgroup/memory/memory.limit_in_bytes')
        if limit is not None and limit >= (1 << 62):
            limit = None
        return usage, limit
