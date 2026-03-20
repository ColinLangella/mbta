from datetime import datetime
from dateutil import parser


def seconds_since(dt: datetime) -> float:
    return (datetime.now(dt.tzinfo) - dt).total_seconds()


def is_older_than(dt: datetime, seconds: int) -> bool:
    return seconds_since(dt) > seconds


def is_now_within_bounds(time_bounds: dict) -> bool:
    """
    Checks if the current time is within the start and end time defined in the `time_bounds` dictionary.

    Args:
        time_bounds (dict): A dictionary with 'start' and 'end' ISO 8601 datetime strings with timezone offsets.

    Returns:
        bool: True if current time is within the range (inclusive), False otherwise.
    """
    start_time = parser.isoparse(time_bounds['start'])
    end_time = parser.isoparse(time_bounds['end'])

    # Get current time in the same timezone as the start time
    now = datetime.now(start_time.tzinfo)

    return start_time <= now <= end_time


def minutes_from_now(iso_time_str: str) -> int:
    """
    Returns the nearest integer number of minutes between now and the given ISO 8601 time string.

    Args:
        iso_time_str (str): An ISO 8601 datetime string with timezone info, e.g. "2025-06-27T17:43:53-04:00".

    Returns:
        int: The number of minutes (rounded to the nearest whole number) between now and the given time.
             Positive if the time is in the future, negative if in the past.
    """
    target_time = parser.isoparse(iso_time_str)
    now = datetime.now(target_time.tzinfo)
    delta = target_time - now
    return round(delta.total_seconds() / 60)
