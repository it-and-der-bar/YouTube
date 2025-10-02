import logging, os, sys
from logging.handlers import RotatingFileHandler
from .config import LOG_DIR

def configure_logging(level: str = "INFO"):
    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            "%Y-%m-%d %H:%M:%S"
        )
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)

        fh = RotatingFileHandler(
            os.path.join(LOG_DIR, "screeny.log"),
            maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)


def _attach_uvicorn_file_handlers():
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")

    acc = logging.getLogger("uvicorn.access")
    if not any(isinstance(h, RotatingFileHandler) and h.baseFilename.endswith("web_access.log") for h in acc.handlers):
        fh = RotatingFileHandler(os.path.join(LOG_DIR, "web_access.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        acc.addHandler(fh)
    acc.propagate = False 

    err = logging.getLogger("uvicorn.error")
    if not any(isinstance(h, RotatingFileHandler) and h.baseFilename.endswith("web_error.log") for h in err.handlers):
        fh = RotatingFileHandler(os.path.join(LOG_DIR, "web_error.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        err.addHandler(fh)
    err.propagate = False