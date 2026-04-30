from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

@dataclass
class FormattedAlert:
    id: str
    header: str
    service_effect: str
    severity: int
    updated_at: str
    lifecycle: str
    timeframe: str
    min_route_type: Optional[int]
    image_url: Optional[str] = None
    image_alt: Optional[str] = None
    affected_routes: List[str] = None

class AlertDataFormatter:
    def format_alerts(self, raw_alerts: list) -> List[FormattedAlert]:
        formatted_list = []

        for alert_obj in raw_alerts:
            data = alert_obj.to_dict()
            attr = data.get("attributes", {})

            # Find the lowest route type in the informed_entities list
            entities = attr.get("informed_entity", [])
            route_types = [e.get("route_type") for e in entities if e.get("route_type") is not None]
            min_route = min(route_types) if route_types else None

            # Extract unique affected routes
            routes = list(set(
                entity.get("route")
                for entity in entities
                if entity.get("route")
            ))

            alert = FormattedAlert(
                id=data.get("id"),
                header=attr.get("header"),
                service_effect=attr.get("service_effect"),
                severity=attr.get("severity"),
                updated_at=attr.get("updated_at").isoformat() if isinstance(attr.get("updated_at"), datetime) else attr.get("updated_at"),
                lifecycle=attr.get("lifecycle", "Unknown"),
                timeframe=attr.get("timeframe", ""),
                min_route_type=min_route,
                image_url=attr.get("image"),
                image_alt=attr.get("image_alternative_text"),
                affected_routes=routes
            )
            formatted_list.append(alert)

        return sorted(formatted_list, key=lambda x: x.severity, reverse=True)
