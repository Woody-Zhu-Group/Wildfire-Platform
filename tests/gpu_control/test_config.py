from services.gpu_control.config import GpuControlSettings


def test_from_env_reads_token_from_dotenv_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "GPU_CONTROL_TOKEN=from-dotenv-file\n", encoding="utf-8"
    )
    monkeypatch.delenv("GPU_CONTROL_TOKEN", raising=False)
    monkeypatch.setattr("services.gpu_control.config.REPO_ROOT", tmp_path)

    settings = GpuControlSettings.from_env()

    assert settings.control_token == "from-dotenv-file"


def test_from_env_has_no_deleted_resource_defaults(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("", encoding="utf-8")
    for name in ("GPU_INSTANCE_ID", "GPU_OLLAMA_URL", "GPU_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("services.gpu_control.config.REPO_ROOT", tmp_path)

    settings = GpuControlSettings.from_env()

    assert not settings.configured
    assert settings.missing_required == (
        "GPU_INSTANCE_ID",
        "GPU_OLLAMA_URL",
        "GPU_MODEL",
    )
