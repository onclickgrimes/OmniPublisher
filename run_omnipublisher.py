from __future__ import annotations

import uvicorn

from app.config import OMNIPUBLISHER_HOST, OMNIPUBLISHER_PORT


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=OMNIPUBLISHER_HOST,
        port=OMNIPUBLISHER_PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
