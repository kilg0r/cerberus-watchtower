import uvicorn


def main() -> None:
    uvicorn.run("watchtower.api:app", host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
