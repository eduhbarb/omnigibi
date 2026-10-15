"""Ponto de entrada: ``python run.py`` (ou ``python run.py --reload``)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def main() -> None:
    parser = argparse.ArgumentParser(description="Omnigibi - monitor de precos de HQs")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="recarrega ao alterar o codigo")
    args = parser.parse_args()

    import uvicorn

    print("=" * 62)
    print("  Omnigibi - monitor de precos de HQs")
    print(f"  Interface:  http://{args.host}:{args.port}/")
    print(f"  API docs:   http://{args.host}:{args.port}/docs")
    print("=" * 62)

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(BASE_DIR / "app"), str(BASE_DIR / "web")] if args.reload else None,
    )


if __name__ == "__main__":
    main()
