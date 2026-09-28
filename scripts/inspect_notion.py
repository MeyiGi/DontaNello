"""Read-only discovery of the supplied Notion pages and nested databases."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dontanello.integrations.notion.client import NotionClient
from dontanello.platform.settings import load_settings

TARGETS = {
    "Tasks": "84e43782a882828f82ac0165bbbcb5ac",
    "Life Planner": "3e643782a882812a98b8c1592fdbeef2",
    "Work": "ba4f7f4d708f4ed59c61dd6cef6e0da9",
}


def discover(api, block_id, visited=None):
    visited = visited if visited is not None else set()
    if block_id in visited:
        return
    visited.add(block_id)
    for block in api.list_all("GET", f"blocks/{block_id}/children"):
        kind = block["type"]
        if kind == "child_database":
            db = api.request("GET", "databases/" + block["id"])
            for source in db.get("data_sources", []):
                schema = api.request("GET", "data_sources/" + source["id"])
                yield {
                    "id": source["id"],
                    "database_id": block["id"],
                    "name": block[kind]["title"],
                    "properties": {k: v["type"] for k, v in schema["properties"].items()},
                }
        elif block.get("has_children"):
            yield from discover(api, block["id"], visited)


if __name__ == "__main__":
    settings = load_settings(ROOT)
    api = NotionClient(settings.notion_token)
    for name, entity_id in TARGETS.items():
        try:
            try:
                db = api.request("GET", "databases/" + entity_id)
            except RuntimeError as error:
                if "Notion 404" not in str(error) and "is a page, not a database" not in str(error):
                    raise
                sources = list(discover(api, entity_id))
            else:
                sources = []
                for source in db.get("data_sources", []):
                    schema = api.request("GET", "data_sources/" + source["id"])
                    sources.append(
                        {
                            "id": source["id"],
                            "name": source["name"],
                            "properties": {k: v["type"] for k, v in schema["properties"].items()},
                        }
                    )
            print(json.dumps({"target": name, "sources": sources}, ensure_ascii=False))
        except RuntimeError as error:
            print(json.dumps({"target": name, "error": str(error)}, ensure_ascii=False))
