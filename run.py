#!/usr/bin/env python3
"""Run the Login Tester server."""

import uvicorn

from app.config import settings


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        use_colors=False,
    )


if __name__ == "__main__":
    main()
