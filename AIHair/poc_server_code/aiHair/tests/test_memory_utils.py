import memory_utils


MEMINFO = {
    'MemTotal': '819200 kB',
    'MemFree': '102400 kB',
    'MemAvailable': '614400 kB',
    'Buffers': '1024 kB',
    'Cached': '2048 kB',
    'Dirty': '3072 kB',
    'AnonPages': '4096 kB',
    'Mapped': '5120 kB',
    'Shmem': '6144 kB',
    'Slab': '7168 kB',
    'SReclaimable': '8192 kB',
    'SUnreclaim': '9216 kB',
    'KernelStack': '10240 kB',
    'PageTables': '11264 kB',
}


def _patch_memory_sources(monkeypatch, monitor, rss_values):
    def fake_read_kv_file(path):
        if path == '/proc/self/status':
            return {'VmRSS': '%d kB' % next(rss_values)}
        return MEMINFO

    monkeypatch.setattr(monitor, '_read_kv_file', fake_read_kv_file)
    monkeypatch.setattr(
        monitor,
        '_get_cgroup_mem',
        lambda: (200 * memory_utils.BYTES_PER_MIB, 400 * memory_utils.BYTES_PER_MIB),
    )


def test_log_memory_usage_reports_full_system_information_without_tracemalloc(monkeypatch):
    messages = []
    monitor = memory_utils.MemoryMonitor(messages.append)
    _patch_memory_sources(monkeypatch, monitor, iter([100 * 1024]))

    monitor.log_memory_usage()

    assert messages == [
        '[memusage] rss=100MB peak_rss=100MB cg=200MB cg_limit=400MB '
        'cg_pct=50.0 host_used_pct=25.0 host_avail=600MB',
        '[meminfo] MemTotal=800MB MemFree=100MB MemAvailable=600MB '
        'Buffers=1MB Cached=2MB Dirty=3MB AnonPages=4MB Mapped=5MB '
        'Shmem=6MB Slab=7MB SReclaimable=8MB SUnreclaim=9MB '
        'KernelStack=10MB PageTables=11MB',
    ]
    assert not any('average_rss' in message or 'memprof' in message for message in messages)


def test_log_memory_usage_tracks_process_lifetime_peak(monkeypatch):
    messages = []
    monitor = memory_utils.MemoryMonitor(messages.append)
    _patch_memory_sources(monkeypatch, monitor, iter([100 * 1024, 150 * 1024, 120 * 1024]))

    monitor.log_memory_usage()
    monitor.log_memory_usage()
    monitor.log_memory_usage()

    assert messages[-2].startswith(
        '[memusage] rss=120MB peak_rss=150MB cg=200MB cg_limit=400MB'
    )


def test_memory_monitor_logs_a_final_snapshot_when_stopped(monkeypatch):
    messages = []
    monitor = memory_utils.MemoryMonitor(messages.append, interval_seconds=60)
    _patch_memory_sources(monkeypatch, monitor, iter([100 * 1024, 125 * 1024]))

    monitor.start()
    monitor.stop()

    assert messages[0].startswith(
        '[memusage] rss=125MB peak_rss=125MB cg=200MB cg_limit=400MB'
    )
    assert messages[1].startswith('[meminfo] MemTotal=800MB MemFree=100MB')
