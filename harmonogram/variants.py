"""Varianty harmonogramu – ukládají se do souboru v domovské složce uživatele.

Dokument: {"active": "<id>", "variants": [{"id", "name", "state", "updated"}]}.
Stav varianty („state“) je stejný JSON, jaký používá webové rozhraní a
PlanStateMapper; správu variant (vytvořit/přejmenovat/smazat/přepnout)
dělá rozhraní, server dokument jen ověří a uloží.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class VariantStore:
    MAX_VARIANTS = 50
    MAX_NAME = 120

    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".legislativni_harmonogram" / "varianty.json"
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        return self.normalize(data)

    def save(self, data: Any) -> dict[str, Any]:
        doc = self.normalize(data)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)       # atomicky – soubor se nepoškodí při pádu
        return doc

    @classmethod
    def normalize(cls, data: Any) -> dict[str, Any]:
        variants = []
        seen = set()
        items = data.get("variants") if isinstance(data, dict) else None
        for item in (items or [])[:cls.MAX_VARIANTS]:
            if not isinstance(item, dict):
                continue
            vid = str(item.get("id") or uuid.uuid4().hex[:12])
            if vid in seen:
                vid = uuid.uuid4().hex[:12]
            seen.add(vid)
            name = str(item.get("name") or "").strip()[:cls.MAX_NAME] or f"Varianta {len(variants) + 1}"
            state = item.get("state") if isinstance(item.get("state"), dict) else None
            variants.append({"id": vid, "name": name, "state": state,
                             "updated": item.get("updated") or datetime.now().isoformat(timespec="seconds")})
        active = data.get("active") if isinstance(data, dict) else None
        if variants and active not in seen:
            active = variants[0]["id"]
        return {"active": active if variants else None, "variants": variants}
