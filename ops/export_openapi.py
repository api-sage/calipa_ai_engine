"""Write the API contract to ops/openapi.json, for the UI's generated types.

    uv run python ops/export_openapi.py
    npx openapi-typescript ops/openapi.json -o <ui>/src/lib/calipa-api.d.ts

A test fails when this file is out of date with the API.
"""

from __future__ import annotations

import json
from pathlib import Path

from calipa.api.app import ApiDeps, create_app
from calipa.reference import load_entitlements

OUT = Path(__file__).parent / "openapi.json"


def main() -> None:
    def no_database() -> None:
        raise RuntimeError("exporting the schema needs no database")

    app = create_app(
        ApiDeps(
            connect=no_database,  # type: ignore[arg-type]
            entitlements=load_entitlements(),
            identity="none",
            cors_origins=[],
        )
    )
    OUT.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
