import json
import threading
import time

from ai_hair.timing import CallTimer, TaskTiming, TimedClient, export_timing


def test_busy_seconds_counts_overlapping_calls_once():
    timer = CallTimer()
    timer.record("images.edit", 0.0, 2.0)
    timer.record("images.edit", 1.0, 3.0)
    timer.record("responses.parse", 5.0, 6.0)
    assert timer.busy_seconds() == 4.0
    assert timer.summary() == {"images.edit": {"count": 2, "sec": 4.0},
                               "responses.parse": {"count": 1, "sec": 1.0}}


class _Group:
    def __init__(self, delay=0.0, fail=False):
        self.delay, self.fail = delay, fail
        self.other = "kept"

    def edit(self, **kwargs):
        time.sleep(self.delay)
        if self.fail:
            raise ValueError("boom")
        return kwargs

    parse = edit


class _Client:
    def __init__(self, **options):
        self.options = options
        self.images = _Group(0.01)
        self.responses = _Group(0.01)
        self.models = "untimed"

    def with_options(self, **options):
        return _Client(**options)


def test_timed_client_times_edit_and_parse_through_with_options():
    timer = CallTimer()
    client = TimedClient(_Client(), timer)
    assert client.images.edit(prompt="p") == {"prompt": "p"}
    analyst = client.with_options(timeout=600)
    assert analyst.options == {"timeout": 600}
    analyst.responses.parse(model="m")
    assert client.models == "untimed" and client.images.other == "kept"
    assert set(timer.summary()) == {"images.edit", "responses.parse"}
    assert timer.busy_seconds() >= 0.02


def test_failed_calls_are_timed_too():
    timer = CallTimer()
    inner = _Client()
    inner.images = _Group(fail=True)
    client = TimedClient(inner, timer)
    try:
        client.images.edit()
    except ValueError:
        pass
    assert timer.summary()["images.edit"]["count"] == 1


def test_task_timing_sections_and_close():
    timing = TaskTiming(task_id="t")
    with timing.section("generate"):
        time.sleep(0.01)
    try:
        with timing.section("postprocess"):
            raise RuntimeError
    except RuntimeError:
        pass
    timing.close()
    assert timing["generate_sec"] >= 0.01
    assert "postprocess_sec" in timing
    assert timing["total_sec"] >= timing["generate_sec"]
    assert timing["time"].endswith("Z")


def test_export_timing_appends_one_json_line_per_task(tmp_path):
    path = tmp_path / "log" / "timing.jsonl"
    threads = [threading.Thread(target=export_timing, args=({"task_id": str(i)}, str(path)))
               for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert sorted(json.loads(line)["task_id"] for line in lines) == sorted(map(str, range(20)))
