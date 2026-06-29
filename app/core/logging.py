import logging
import sys

_QUIET_LOGGERS = ("elastic_transport", "elasticsearch", "urllib3")


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    quiet_level = logging.DEBUG if debug else logging.WARNING
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(quiet_level)
