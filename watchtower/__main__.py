import uvicorn

from .config import SERVER_HOST, SERVER_PORT


def main() -> None:
    uvicorn.run("watchtower.api:app", host=SERVER_HOST, port=SERVER_PORT)


if __name__ == "__main__":
    main()
