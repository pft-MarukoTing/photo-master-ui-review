import json

import pytest
from PIL import Image

import job_processor
from ai_hair.exceptions import AiHairError, C2PAUnsupportedError
from job_processor import AiHairJobProcessor, build_job_metadata, safe_task_folder, save_image


class FakeArtifactStore:
    def __init__(self, photo_path):
        self.photo_path = photo_path
        self.downloads = []
        self.uploads = []

    def download_file(self, key, folder_name=None):
        self.downloads.append((key, folder_name))
        return self.photo_path

    def upload_file(self, local_path, dst_key):
        with Image.open(local_path) as im:
            self.uploads.append((dst_key, im.format, im.size))


class FakeLogger:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, task_id, message):
        self.infos.append((task_id, message))

    def warning(self, task_id, message):
        self.warnings.append((task_id, message))


@pytest.fixture(autouse=True)
def stub_c2pa_signing(monkeypatch):
    monkeypatch.setattr(job_processor, "sign_file", lambda data, filename: data)


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (40, 30), (10, 20, 30)).save(path)
    return str(path)


def base_task(params=None, **overrides):
    task = {
        "effect": "aiHair",
        "tskId": "tsk",
        "srcKeys": ["demo/aiHair/src/job/photo.jpg"],
        "dstKeys": ["demo/aiHair/result/job/result_0.jpg", "demo/aiHair/result/job/result_1.png"],
        "acts": [{"id": 0, "params": params or {"feature": "hairline", "level": 2}}],
        "app": "app",
        "uid": "uid",
        "os": "ios",
        "appVer": "1.0",
        "verType": "prod",
        "uniqueId": "unique",
        "umaId": "uma",
    }
    task.update(overrides)
    return task


def fake_pipeline(skipped=None, error=None):
    def run(*, params, src_paths, n_images, task_id, logger, timing):
        timing["generate_sec"] = 1.5
        timing["api_sec"] = 1.25
        timing["postprocess_sec"] = 0.5
        if error:
            raise error
        photo = Image.open(src_paths[0]).convert("RGB")
        outputs = [] if skipped else [Image.new("RGB", (40, 30), (i, i, i)) for i in range(n_images)]
        return {"outputs": outputs, "photo": photo, "skipped": skipped,
                "metadata": {"feature": params["feature"]}}
    return run


def processor(tmp_path, photo):
    store = FakeArtifactStore(photo)
    logger = FakeLogger()
    log_path = tmp_path / "timing.jsonl"
    return AiHairJobProcessor(store, logger, str(tmp_path / "tmp"), str(log_path)), store, logger, log_path


def test_safe_task_folder_and_metadata_shape_match_previous_server_helpers():
    assert safe_task_folder("a/b/c") == "a_b_c"
    assert build_job_metadata(base_task()) == {
        "app": "app", "uid": "uid", "os": "ios", "appVer": "1.0", "verType": "prod",
        "uniqueId": "unique", "umaId": "uma",
    }


def test_results_uploaded_in_their_formats_and_timing_exported(monkeypatch, tmp_path, photo):
    monkeypatch.setattr(job_processor, "run_ai_hair_pipeline", fake_pipeline())
    proc, store, logger, log_path = processor(tmp_path, photo)

    results = proc.process_for_api("job/1", base_task())

    assert store.uploads == [("demo/aiHair/result/job/result_0.jpg", "JPEG", (40, 30)),
                             ("demo/aiHair/result/job/result_1.png", "PNG", (40, 30))]
    res = results["0_res0"]
    assert res["dstKeys"] == ["demo/aiHair/result/job/result_0.jpg",
                              "demo/aiHair/result/job/result_1.png"]
    assert len(res["sizes"]) == 2 and res["feature"] == "hairline" and res["skipped"] == ""
    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert record["status"] == "success"
    assert record["feature"] == "hairline" and "hairfx_effect" not in record
    assert record["level"] == 2 and record["n_images"] == 2 and record["n_results"] == 2
    assert record["generate_sec"] == 1.5 and record["postprocess_sec"] == 0.5
    assert record["api_sec"] == 1.25 and record["tsk_id"] == "tsk"
    for key in ("download_sec", "upload_sec", "total_sec", "time"):
        assert key in record
    summary = [m for _, m in logger.infos if "[timing]" in m]
    assert summary and "generate=1.500s (api 1.250s) postprocess=0.500s" in summary[0]
    assert not (tmp_path / "tmp" / "server_output" / "job_1").exists()


def test_skipped_uploads_the_photo(monkeypatch, tmp_path, photo):
    monkeypatch.setattr(job_processor, "run_ai_hair_pipeline", fake_pipeline(skipped="no grey hair"))
    proc, store, _, log_path = processor(tmp_path, photo)

    results = proc.process_for_api("job", base_task({"feature": "grayHair"}))

    assert results["0_res0"]["skipped"] == "no grey hair"
    assert [u[0] for u in store.uploads] == base_task()["dstKeys"]
    assert json.loads(log_path.read_text(encoding="utf-8"))["status"] == "skipped"


def test_failure_still_exports_timing(monkeypatch, tmp_path, photo):
    monkeypatch.setattr(job_processor, "run_ai_hair_pipeline",
                        fake_pipeline(error=AiHairError("render failed", "some_code")))
    proc, store, _, log_path = processor(tmp_path, photo)

    with pytest.raises(AiHairError) as e:
        proc.process_for_api("job", base_task({"feature": "soft"}))

    assert e.value.args[1] == "some_code"
    assert store.uploads == []
    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert record["status"] == "error" and record["error"] == "render failed"
    assert record["feature"] == "soft" and record["generate_sec"] == 1.5


def test_invalid_request_is_logged_with_no_feature(tmp_path, photo):
    proc, _, _, log_path = processor(tmp_path, photo)
    with pytest.raises(RuntimeError):
        proc.process_for_api("job", base_task({"feature": "curly"}))
    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert record["status"] == "error" and "feature" not in record


def test_unsigned_upload_on_c2pa_failure(monkeypatch, tmp_path, photo):
    def refuse(data, filename):
        raise C2PAUnsupportedError("nope")

    monkeypatch.setattr(job_processor, "sign_file", refuse)
    monkeypatch.setattr(job_processor, "run_ai_hair_pipeline", fake_pipeline())
    proc, store, logger, _ = processor(tmp_path, photo)
    proc.process_for_api("job", base_task())
    assert len(store.uploads) == 2
    assert any("C2PA sign skipped" in m for _, m in logger.warnings)


def test_save_image_converts_for_jpeg(tmp_path):
    path = save_image(Image.new("RGBA", (4, 4)), str(tmp_path / "a" / "x.jpg"))
    with Image.open(path) as im:
        assert im.format == "JPEG" and im.mode == "RGB"
