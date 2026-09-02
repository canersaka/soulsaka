from __future__ import annotations

from typer.testing import CliRunner

from soulsaka.cli import app

runner = CliRunner()


def test_init_note_stats_memory(data_dir):
    r = runner.invoke(app, ["init", "--name", "Caner", "--email", "me@example.com"])
    assert r.exit_code == 0, r.output
    assert (data_dir / "config.toml").exists()
    r = runner.invoke(app, ["note", "remember the wifi password is hunter2"])
    assert r.exit_code == 0, r.output
    assert "captured (done)" in r.output
    r = runner.invoke(app, ["memory", "list"])
    assert r.exit_code == 0 and "hunter2" in r.output
    r = runner.invoke(app, ["stats"])
    assert r.exit_code == 0 and "words of you" in r.output
    r = runner.invoke(app, ["memory", "add", "Likes oat milk", "--kind", "preference"])
    assert r.exit_code == 0 and "added" in r.output
    r = runner.invoke(app, ["pair"])
    assert r.exit_code == 0 and "pairing code" in r.output


def test_train_and_eval_cli_smoke(data_dir):
    from soulsaka.config import get_settings
    from soulsaka.hub.state import HubState
    from tests.test_train_dataset import _seed

    state = HubState(get_settings())
    _seed(state, n_convs=4)
    state.close()
    r = runner.invoke(app, ["train", "preview", "--n", "1"])
    assert r.exit_code == 0, r.output
    assert "training examples" in r.output and "target" in r.output
    r = runner.invoke(app, ["train", "run", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "v1 planned" in r.output
    r = runner.invoke(app, ["train", "list"])
    assert r.exit_code == 0 and "v1" in r.output
    r = runner.invoke(app, ["eval", "report"])
    assert r.exit_code == 0 and "fidelity" in r.output
    r = runner.invoke(app, ["self-model", "--regenerate", "--no-llm"])
    assert r.exit_code == 0 and "Style fingerprint" in r.output
    r = runner.invoke(app, ["config", "show"])
    assert r.exit_code == 0 and "[train]" in r.output


def test_serve_llm_print_only_explains_missing_pieces(data_dir):
    r = runner.invoke(app, ["train", "serve-llm", "--print-only"])
    # No llama-server here: the command must fail with a clear message, not a traceback.
    assert r.exit_code != 0
    assert "llama-server not found" in str(r.exception) or "llama-server not found" in r.output
