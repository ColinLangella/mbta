from dataclasses import dataclass
from typing import List, Optional
from collections import OrderedDict
from mbta_client.models.stop_resource import StopResource

@dataclass
class NearbyStation:
    id: str
    name: str
    municipality: str
    routes: List[str]
    route_types: List[int]
    primary_route_type: int

class MultiStopFormatter:
    @staticmethod
    def format_nearby_stations(raw_stops: List[StopResource], allowed_types: List[int]) -> List[NearbyStation]:
        # OrderedDict preserves the distance-based order returned by the MBTA API
        stations = OrderedDict()

        for stop_resource in raw_stops:
            stop_data = stop_resource.to_dict()
            attr = stop_data.get("attributes", {})
            rel = stop_data.get("relationships", {})

            # 1. Filter by Route Type (vehicle_type)
            route_type = attr.get("vehicle_type")
            if route_type not in allowed_types:
                continue

            # 2. Determine Uniqueness Key
            # Priority: Parent Station ID -> Station Name (fallback)
            parent_data = rel.get("parent_station", {}).get("data")
            if parent_data and parent_data.get("id"):
                unique_id = parent_data.get("id")
            else:
                unique_id = attr.get("name")

            if unique_id not in stations:
                stations[unique_id] = {
                    "id": unique_id,
                    "name": attr.get("name"),
                    "municipality": attr.get("municipality", "Unknown"),
                    "routes": set(),
                    "route_types": {route_type},
                    "min_type": route_type
                }
            else:
                stations[unique_id]["route_types"].add(route_type)
                if route_type < stations[unique_id]["min_type"]:
                    stations[unique_id]["min_type"] = route_type

            # 3. Safely Extract Route Name from Description
            # Formats: "Station - Line - Dest" or "Station - Route"
            desc = attr.get("description")
            if desc and " - " in desc:
                parts = desc.split(" - ")
                if len(parts) > 1:
                    stations[unique_id]["routes"].add(parts[1].strip())

        # 4. Finalize into dataclass objects
        formatted_list = []
        for s in stations.values():
            formatted_list.append(NearbyStation(
                id=str(s["id"]),
                name=s["name"],
                municipality=s["municipality"],
                routes=sorted(list(s["routes"])),
                route_types=list(s["route_types"]),
                primary_route_type=s["min_type"]
            ))

        return formatted_list
