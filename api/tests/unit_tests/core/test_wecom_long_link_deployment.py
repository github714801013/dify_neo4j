from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_dedicated_wecom_entrypoint_bypasses_celery_supervisor() -> None:
    entrypoint = (REPO_ROOT / "api/docker/entrypoint.sh").read_text(encoding="utf-8")

    assert 'if [[ "${MODE}" == "wecom_long_link" ]]' in entrypoint
    assert "exec python -m core.wecom_long_link.worker" in entrypoint


def test_compose_defines_single_dedicated_wecom_service() -> None:
    deploy_script = (REPO_ROOT / "docker/deploy.sh").read_text(encoding="utf-8")

    assert "  wecom_worker:" in deploy_script
    assert "      MODE: wecom_long_link" in deploy_script
    assert '      WECOM_LONG_LINK_ENABLED: "true"' in deploy_script
    assert '      MIGRATION_ENABLED: "false"' in deploy_script
    assert '      WECOM_LONG_LINK_ENABLED: "false"' in deploy_script
    assert "core.wecom_long_link.worker" in deploy_script
    wecom_block = deploy_script.split("  wecom_worker:", 1)[1].split("  worker_beat:", 1)[0]
    assert "    healthcheck:" in wecom_block
