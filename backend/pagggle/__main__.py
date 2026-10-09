import uvicorn

from .app import create_app
from .config import load_settings


def main():
    try:
        settings = load_settings()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
