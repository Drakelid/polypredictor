"""Discord integration stub for the v1.1 deferral decision."""

from __future__ import annotations

import json


def integration_status() -> dict[str, str]:
    return {
        "status": "deferred",
        "milestone": "v1.1",
        "decision_note": "docs/decisions/discord-v1_1.md",
        "target_table": "external_events",
    }


def main() -> None:
    print(json.dumps(integration_status(), indent=2))


if __name__ == "__main__":
    main()
