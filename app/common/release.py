import json
from pathlib import Path

from django.conf import settings


def local_release_status():
    root_value = getattr(settings, "LOCAL_BACKUP_ROOT", "")
    if not root_value:
        return None
    root = Path(root_value)
    result = {
        "version": getattr(settings, "RELEASE_VERSION", "development"),
        "last_backup_at": None,
        "last_backup_verified": False,
        "backup_error": "",
    }
    try:
        manifests = sorted(root.glob("*/manifest.json"), reverse=True)
        if not manifests:
            return result
        manifest_path = manifests[0]
        if manifest_path.stat().st_size > 128 * 1024:
            raise ValueError("manifest too large")
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        result["last_backup_at"] = payload.get("created_at")
        result["last_backup_verified"] = payload.get("restored") is True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        result["backup_error"] = "最近备份状态文件无法读取，请从本地管理脚本重新备份。"
    return result
