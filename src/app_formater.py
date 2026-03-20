from mbta_api import MBTA_API

from dataclasses import asdict
from typing import Dict, List, Tuple, Any
from collections import defaultdict

def formatStationPredictions(api: MBTA_API, stopName: str = "Lechmere") -> Dict[str, Any]:
    """
    Organize predictions for display based on line and direction.

    Args:
        api: MBTA_API instance
        stopName: Name of the station to get predictions for

    Returns:
        Dict with organized predictions by line and direction
    """
    if api.DEBUG: api.logger.info(f"Formatting station predictions for: {stopName}")

    rawData = api.getStationPredictions(stopName)
    if not rawData: return {}

    # Use defaultdict to avoid key checking
    organized = defaultdict(lambda: defaultdict(lambda: [[], []]))

    for desc, preds in rawData.items():
        if not preds: continue

        try:
            line, end, direction_key = _parse_description(desc, preds, api)
            if line is None:  # Skip malformed entries
                continue

            organized[line][direction_key][0].append(end)
            organized[line][direction_key][1].extend(preds)

        except Exception as e:
            if api.DEBUG: api.logger.error(f"Skipping malformed entry '{desc}': {e}")
            continue

    # Join endpoints and clean predictions
    return _build_final_data(organized, api)


def _parse_description(desc: str, preds: List[MBTA_API.FormattedPrediction], api: MBTA_API) -> Tuple[str, str, str]:
    """Parse station description string to extract line, end, and direction."""
    parts = [x.strip() for x in desc.split("-")]
    
    if len(parts) == 3:
        line = parts[1]
        end = parts[2]
    elif len(parts) == 2:
        ##TODO Bus case barly works
        line = parts[1]
        end = parts[1]
    else:
        if api.DEBUG: api.logger.warning(f"Unexpected station format in '{desc}'")
        return None, None, None

    direction_key = preds[0].direction if preds and preds[0].direction != -1 else end

    return line, end, direction_key


def _build_final_data(organized: Dict, api: MBTA_API) -> Dict[str, Any]:
    """Build the final formatted data structure."""
    final_data = {}

    for line, directions in organized.items():
        if not directions: continue
    
        # Join endpoints with separator
        for direction_data in directions.values():
            direction_data[0] = " \\ ".join(direction_data[0])

        # Get direction keys
        dir_keys = list(directions.keys())
        dir1 = dir_keys[0]
        dir2 = dir_keys[1] if len(dir_keys) > 1 else dir_keys[0]
        
        final_data[line] = {
            "Direction 1": _clean_predictions(directions[dir1][1], api),
            "Direction 2": _clean_predictions(directions[dir2][1], api),
            "End 1": directions[dir1][0],
            "End 2": directions[dir2][0] }

    return final_data


def _clean_predictions(preds: List[MBTA_API.FormattedPrediction], api: MBTA_API) -> List[Dict]:
    """Clean and sort predictions, converting to dictionaries."""
    if not preds: return []

    # Pre-filter predictions by time
    valid_preds = [p for p in preds if _to_minutes(p) <= api.MAX_TIME]

    # Update wait times with transport info
    for pred in valid_preds:
        if pred.tInfo:
            pred.wait = pred.tInfo

    # Sort and limit results
    sorted_preds = sorted(valid_preds, key=_to_minutes)[:api.MAX_LEN]

    # Convert to dictionaries
    return [asdict(pred) for pred in sorted_preds]


def _to_minutes(pred: MBTA_API.FormattedPrediction) -> int:
    """Convert prediction wait time to minutes for sorting."""
    # Use a lookup table for special cases
    special_cases = {
        "Boarding": -100,
        "Arriving": -99,
        "Next Stop": -98
    }

    if pred.wait in special_cases:
        return special_cases[pred.wait]

    try:
        return int(pred.wait.split()[0])
    except (ValueError, IndexError, AttributeError):
        return 999  # Put invalid entries at the end