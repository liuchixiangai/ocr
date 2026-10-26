import faulthandler
import logging
import os
import re
import sys
from datetime import datetime

from dotenv import load_dotenv
from platformdirs import user_log_dir

from resource_path import resource_path

# How many log files of each kind (scoresight_<ts>.log and
# scoresight_std_<ts>.log) to keep in the log directory.
MAX_LOG_FILES = 10

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(module)s - %(message)s"

_TIMESTAMP_RE = r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_\d+)?"
# The app's own DEBUG-level log (shown in the log viewer).
_MAIN_LOG_RE = re.compile(rf"^scoresight_{_TIMESTAMP_RE}\.log$")
# The root logger's INFO-level log, which also captures third-party libraries.
_STD_LOG_RE = re.compile(rf"^scoresight_std_{_TIMESTAMP_RE}\.log$")

# The handler of this session's scoresight_std_<ts>.log (None if it couldn't be
# created), where enable_crash_traces() writes.
_std_handler = None


def get_log_dir() -> str:
    """The log directory. SCORESIGHT_LOG_DIR overrides the platform default."""
    override = os.environ.get("SCORESIGHT_LOG_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return user_log_dir("scoresight")


def _unique_log_paths(log_dir: str, current_time: str) -> tuple[str, str]:
    # Two instances started within the same second must not share a file.
    counter = 0
    while True:
        suffix = f"_{counter}" if counter else ""
        main_path = os.path.join(log_dir, f"scoresight_{current_time}{suffix}.log")
        std_path = os.path.join(log_dir, f"scoresight_std_{current_time}{suffix}.log")
        if not os.path.exists(main_path) and not os.path.exists(std_path):
            return main_path, std_path
        counter += 1


def rotate_logs(
    log_dir: str, keep: int = MAX_LOG_FILES, current_paths: tuple[str, ...] = ()
):
    """Delete all but the newest `keep` logs of each kind in log_dir.

    Main and std logs are rotated separately (sorting them together put every
    main log before every std log, so the current session's log got deleted),
    and the files in current_paths are never deleted.
    """
    protected = {os.path.normcase(os.path.abspath(p)) for p in current_paths if p}
    try:
        names = os.listdir(log_dir)
    except OSError:
        return
    for pattern in (_MAIN_LOG_RE, _STD_LOG_RE):
        # the timestamp in the name sorts chronologically
        log_files = sorted(name for name in names if pattern.match(name))
        if len(log_files) <= keep:
            continue
        for name in log_files[:-keep]:
            full_path = os.path.join(log_dir, name)
            if os.path.normcase(os.path.abspath(full_path)) in protected:
                continue
            try:
                os.remove(full_path)
            except OSError as e:
                # e.g. the log of another running instance on Windows
                logging.getLogger(__name__).warning(
                    f"Failed to remove log file {name}: {e}"
                )


def setup_logging():
    """Configure logging. Never raises: if the log files can't be created, logs go
    to stderr and log_file_path is None."""
    # Load the environment variables from the .env file (e.g. SCORESIGHT_DEBUG)
    try:
        load_dotenv(resource_path(".env"))
    except Exception as e:
        if sys.stderr:
            print(f"Error loading .env: {e}", file=sys.stderr)

    # Create a logger
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    # Create a formatter that can handle Unicode
    formatter = logging.Formatter(LOG_FORMAT)

    log_dir = None
    log_file_path = None
    std_log_path = None
    std_handler = None
    file_handler = None
    try:
        log_dir = get_log_dir()
        os.makedirs(log_dir, exist_ok=True)

        current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file_path, std_log_path = _unique_log_paths(log_dir, current_time)

        # send all logs (including third-party libraries) at INFO and above to
        # the std log file with UTF-8 encoding
        std_handler = logging.FileHandler(
            std_log_path,
            encoding="utf-8",
            errors="replace",  # Replace invalid characters instead of crashing
        )
        std_handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))

        # the app's own log, at DEBUG level, with UTF-8 encoding
        file_handler = logging.FileHandler(
            log_file_path,
            encoding="utf-8",
            errors="replace",  # Replace invalid characters instead of crashing
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
    except Exception as e:
        for handler in (std_handler, file_handler):
            if handler is not None:
                handler.close()
        if sys.stderr:
            print(
                f"Error setting up file logging, logging to stderr instead: {e}",
                file=sys.stderr,
            )
        log_dir = None
        log_file_path = None
        std_log_path = None
        std_handler = None
        # fall back to stderr so `file_handler` is always a usable handler
        file_handler = logging.StreamHandler()
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)

    if std_handler is not None:
        root_logger = logging.getLogger()
        root_logger.addHandler(std_handler)
        root_logger.setLevel(logging.INFO)

    # Add the file handler to the logger
    logger.addHandler(file_handler)

    # if the .env file has a debug flag, set the logger to output to console
    if os.getenv("SCORESIGHT_DEBUG") and log_file_path is not None:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        logger.debug("Debug mode enabled")

    # only keep the most recent log files, never the ones this session writes
    if log_dir is not None:
        rotate_logs(log_dir, MAX_LOG_FILES, (log_file_path, std_log_path))

    global _std_handler
    _std_handler = std_handler
    return logger, file_handler, log_file_path


def enable_crash_traces() -> bool:
    """If the process crashes in native code (an access violation or a
    segmentation fault, e.g. in Qt, OpenCV or Tesseract), write the Python stack
    of every thread to this session's scoresight_std_<ts>.log, or to stderr if
    there is no log file, so the crash leaves a trace. Called by the app at
    startup, not on import, so that test runners keep their own handler. Never
    raises; returns whether it is enabled."""
    stream = getattr(_std_handler, "stream", None) or sys.stderr
    if stream is None:  # a windowed build without a log file
        return False
    try:
        faulthandler.enable(file=stream, all_threads=True)
    except (AttributeError, OSError, ValueError):
        # e.g. a stream without a file descriptor
        return False
    return True


# These are always defined, even if the log files couldn't be created:
# `file_handler` then logs to stderr and `log_file_path` is None.
logger, file_handler, log_file_path = setup_logging()
