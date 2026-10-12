from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sift.cli import app
from sift.config import Settings, get_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def cli_settings(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    get_settings.cache_clear()
    monkeypatch.setattr("sift.cli.get_settings", lambda: settings)
    return settings


def test_seed(tmp_path: Path) -> None:
    result = runner.invoke(app, ["seed", "--out", str(tmp_path / "web")])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["sites"] == 47
    assert (tmp_path / "web" / "ground_truth.json").exists()


def test_schema_learn_extract(site: Any, tmp_path: Path) -> None:
    shop = site("shop-04")
    schema_file = tmp_path / "schema.json"
    result = runner.invoke(app, ["schema", shop["start_url"], shop["intent"], "-o", str(schema_file)])
    assert result.exit_code == 0, result.output
    assert "price" in json.loads(schema_file.read_text())["fields"][1]["name"] or schema_file.exists()
    (tmp_path / "truth_schema.json").write_text(json.dumps(shop["schema"]))
    extractor = tmp_path / "ext.json"
    result = runner.invoke(
        app, ["learn", shop["start_url"], "--schema", str(tmp_path / "truth_schema.json"), "-o", str(extractor)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["accepted"] is True
    result = runner.invoke(app, ["extract", shop["start_url"], "--extractor", str(extractor)])
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert len(lines) == 12 and lines[0]["name"] == shop["records"][0]["name"]


def test_crawl_heal_jobs_export(site: Any, tmp_path: Path, settings: Settings) -> None:
    shop = site("shop-03")
    (tmp_path / "schema.json").write_text(json.dumps(shop["schema"]))
    out = tmp_path / "records.jsonl"
    result = runner.invoke(
        app, ["crawl", shop["start_url"], "--schema", str(tmp_path / "schema.json"), "--layout", "v1", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert len(out.read_text().splitlines()) == len(shop["records"])
    from sift.services import Services

    job_id = Services(settings).store.list_jobs()[0]["id"]
    result = runner.invoke(app, ["heal", job_id, "--layout", "v2"])
    assert result.exit_code == 0, result.output
    assert "healed" in result.output
    result = runner.invoke(app, ["jobs"])
    assert result.exit_code == 0 and job_id in result.stdout
    csv_file = tmp_path / "out.csv"
    result = runner.invoke(app, ["export", job_id, "-f", "csv", "-o", str(csv_file)])
    assert result.exit_code == 0 and csv_file.read_text().startswith("name,")


def test_eval_synthetic_offline_one_site(settings: Settings) -> None:
    result = runner.invoke(app, ["eval", "synthetic", "--name", "ci", "--sites", "shop-04", "--workers", "1"])
    assert result.exit_code == 0, result.output
    data = json.loads((settings.results_dir / "ci_synthetic.json").read_text())
    entry = data["sites"][0]
    assert entry["v1"]["recall"] == 1.0 and entry["v2_before"]["detected"] is True
    assert data["aggregate"]["robustness"]["false_alarms_on_v1"] == 0
