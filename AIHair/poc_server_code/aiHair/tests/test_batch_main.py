"""batch_main.py on a folder: a fake effect and a fake OpenAI client, its
records, run.json and timing.jsonl; --feature's mapping and checks."""

import json
import sys

import pytest
from PIL import Image

import batch_main
from hairfx import core
from hairfx.features import EFFECTS


class _Images:
    def edit(self, **kwargs):
        return "rendered"


class FakeOpenAI:
    made = []

    def __init__(self, api_key=None, **kwargs):
        self.api_key = api_key
        self.images = _Images()
        FakeOpenAI.made.append(self)

    def with_options(self, **kwargs):
        return self


class FakeFeature(core.Feature):
    """One images.edit call, n copies of the photo; the third photo fails."""

    default_n_images = 2

    def __init__(self):
        super().__init__("fake")

    def prompts(self, opts):
        return {"edit": "p"}

    def process(self, ctx, photo, opts, result):
        result.step = "edit"
        ctx.client.images.edit(model=ctx.render.model)
        if photo.stem == "c":
            raise ValueError("bad photo")
        for i in range(self.n_images(ctx.render)):
            result.outputs[f"result_{i}"] = photo.image.copy()


@pytest.fixture
def folder(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    for name in ("a", "b", "c"):
        Image.new("RGB", (40, 30), (100, 80, 60)).save(src / f"{name}.jpg")
    return src


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["batch_main.py", *argv])
    return batch_main.main()


def test_folder_run_saves_results_records_and_timing(monkeypatch, folder, tmp_path, capsys):
    import openai

    monkeypatch.setitem(EFFECTS, "fake", FakeFeature())
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-plain")
    out = tmp_path / "out"

    code = run(monkeypatch, "--input", str(folder), "--feature", "fake", "--out", str(out),
               "--no-align", "--workers", "2")

    assert code == 1  # c failed
    assert FakeOpenAI.made[-1].api_key == "sk-plain"
    effect_dir = out / "fake"
    for name in ("a", "b"):
        assert (effect_dir / f"{name}_result_0.png").is_file()
        assert (effect_dir / f"{name}_result_1.png").is_file()
        record = json.loads((effect_dir / "debug" / name / "record.json").read_text("utf-8"))
        timing = record["timing"]
        assert timing["status"] == "success" and timing["n_results"] == 2
        for key in ("load_sec", "generate_sec", "postprocess_sec", "api_sec", "total_sec"):
            assert key in timing
        assert timing["api_calls"]["images.edit"]["count"] == 1
    failed = json.loads((effect_dir / "debug" / "c" / "record.json").read_text("utf-8"))
    assert failed["error"] == "edit failed — ValueError: bad photo"
    assert failed["timing"]["status"] == "error" and "postprocess_sec" in failed["timing"]
    lines = [json.loads(l) for l in (effect_dir / "timing.jsonl").read_text("utf-8").splitlines()]
    assert sorted(l["name"] for l in lines) == ["a", "b", "c"]
    run_json = json.loads((effect_dir / "run.json").read_text("utf-8"))
    assert all("timing" in p for p in run_json["photos"])
    printed = capsys.readouterr().out
    assert "timing:    load" in printed and "timing this run (per photo, mean / max)" in printed

    # A rerun leaves the photos done as they are.
    FakeOpenAI.made.clear()
    run(monkeypatch, "--input", str(folder), "--feature", "fake", "--out", str(out),
        "--no-align")
    assert "2 photo(s) done with these settings already" in capsys.readouterr().out


@pytest.mark.parametrize("argv,feature,run_dir,options", [
    (["--feature", "hairline"], "hairline", "hairline_lv1", ""),
    (["--feature", "HAIRLINE", "--level", "2"], "hairline", "hairline_lv2", "level=2"),
    (["--feature", "hairline", "-o", "level=2"], "hairline", "hairline_lv2", "level=2"),
    (["--feature", "rootretouch"], "rootRetouch", "rootRetouch", ""),
    (["--feature", "plump"], "plump", "plump", ""),
])
def test_feature_names_and_levels(monkeypatch, folder, tmp_path, capsys, argv, feature, run_dir,
                                  options):
    code = run(monkeypatch, "--input", str(folder), *argv, "--dry-run", "--out", str(tmp_path))
    printed = capsys.readouterr().out
    assert code == 0
    assert f"effect:    {feature}  (" in printed
    assert f"out:       {tmp_path / run_dir}" in printed
    if options:
        assert f"options:   {options}" in printed
    else:
        assert "options:" not in printed


@pytest.mark.parametrize("argv", [
    ["--feature", "soft", "--level", "2"],
    ["--feature", "hairline", "--level", "3"],
    ["--feature", "hairline", "--level", "2", "-o", "level=2"],
    ["--feature", "soft_1"],
    ["--feature", "frizzCare_4"],
    ["--feature", "curly"],
    [],
])
def test_bad_feature_arguments(monkeypatch, folder, argv):
    with pytest.raises(SystemExit) as e:
        run(monkeypatch, "--input", str(folder), *argv, "--dry-run")
    assert e.value.code == 2


def test_encrypted_key_is_decrypted(monkeypatch):
    from aiserver_utils.aescipher import AESCipher

    monkeypatch.setenv("OPENAI_API_KEY", AESCipher().encrypt("sk-secret"))
    assert batch_main._openai_api_key() == "sk-secret"
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-key")
    with pytest.raises(ValueError):
        batch_main._openai_api_key()


def test_list_shows_the_features(monkeypatch, capsys):
    assert run(monkeypatch, "--list") == 0
    printed = capsys.readouterr().out
    for name in EFFECTS:
        assert f"\n{name}  (" in "\n" + printed
    assert "-o level=1  how far" in printed
    assert "frizzCare_4" not in printed.split("\n")[0]
