import os

from artifact_store import S3ArtifactStore


class FakeLogger:
    def __init__(self):
        self.errors = []

    def error(self, task_id, message):
        self.errors.append((task_id, message))


class ImmediateS3Accessor:
    def __init__(self):
        self.uploads = []
        self.downloads = []

    def upload_file(self, callback, filepath, dstKey, task_id):
        self.uploads.append((filepath, dstKey, task_id))
        callback({"task_id": task_id})

    def download_file(self, callback, key, filepath, task_id):
        self.downloads.append((key, filepath, task_id))
        callback({"task_id": task_id, "img_path": filepath})


def test_s3_callback_updates_known_task_and_adds_done_status(tmp_path):
    logger = FakeLogger()
    store = S3ArtifactStore(ImmediateS3Accessor(), logger, str(tmp_path))
    store.s3_result["task-1"] = {"status": "uploading"}
    response = {"task_id": "task-1"}

    store.s3_callback(response)

    assert response == {"status": "done", "end_ts": store.s3_result["task-1"]["end_ts"]}
    assert store.s3_result["task-1"]["status"] == "done"
    assert logger.errors == []


def test_s3_callback_logs_unknown_task(tmp_path):
    logger = FakeLogger()
    store = S3ArtifactStore(ImmediateS3Accessor(), logger, str(tmp_path))

    store.s3_callback({"task_id": "missing"})

    assert logger.errors == [("missing", "Invalid task_id in callback!!")]


def test_upload_file_uses_accessor_callback_and_clears_tracking(tmp_path):
    accessor = ImmediateS3Accessor()
    store = S3ArtifactStore(accessor, FakeLogger(), str(tmp_path))

    store.upload_file("/local/song.mp3", "dst/song.mp3")

    assert len(accessor.uploads) == 1
    assert accessor.uploads[0][0:2] == ("/local/song.mp3", "dst/song.mp3")
    assert store.s3_result == {}


def test_download_file_uses_accessor_callback_and_returns_local_path(tmp_path):
    accessor = ImmediateS3Accessor()
    store = S3ArtifactStore(accessor, FakeLogger(), str(tmp_path))

    path = store.download_file("src/input.wav", folder_name="server_output/task/input")

    assert len(accessor.downloads) == 1
    assert accessor.downloads[0][0] == "src/input.wav"
    assert path == os.path.join(str(tmp_path), "server_output/task/input", "input.wav")
    assert store.s3_result == {}
