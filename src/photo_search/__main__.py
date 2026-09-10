"""Local development entry point."""

import uvicorn


def main() -> None:
    """Run the local-only P0-01 health endpoint."""
    uvicorn.run("photo_search.app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
