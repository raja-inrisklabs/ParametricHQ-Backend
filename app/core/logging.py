import logging
import sys
from app.core.config import settings


class _QuietJobPollFilter(logging.Filter):
    """The page polls a running job every few seconds. Do not log each 202."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "/datasets/jobs/" not in message:
            return True
        # uvicorn.access: '... "GET /api/datasets/jobs/{id} HTTP/1.1" 202'
        return not message.rstrip().endswith(" 202")


def setup_logging():
    log_level = logging.DEBUG if settings.DEBUG else logging.INFO
    log_format = "%(asctime)s | %(levelname)-7s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"

    root = logging.getLogger()
    root.setLevel(log_level)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(log_format))
        root.addHandler(handler)

    # Silence overly verbose third-party loggers.
    # cdsapi/ecmwf add their own handler and print the same status on every poll.
    access = logging.getLogger("uvicorn.access")
    access.setLevel(logging.INFO)
    if not any(isinstance(item, _QuietJobPollFilter) for item in access.filters):
        access.addFilter(_QuietJobPollFilter())
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    for name in ("cdsapi", "ecmwf", "ecmwf.datastores", "multiurl"):
        third_party = logging.getLogger(name)
        third_party.setLevel(logging.WARNING)
        third_party.propagate = False


logger = logging.getLogger("parametric_platform")
