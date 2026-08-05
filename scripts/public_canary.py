#!/usr/bin/env python3
"""Run the public Miner smoke as an external, no-state canary.

The script intentionally delegates to the release smoke checks and exits
non-zero on any failure, making it suitable for GitHub Actions, cron, or an
independent uptime runner. It never prints the Bearer token.
"""

from __future__ import annotations

from smoke_miner import main


if __name__ == "__main__":
    main()
