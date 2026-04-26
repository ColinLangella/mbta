# General Imports
import os, json, logging
from json import dumps
from typing import Callable, Any

# Typing Imports
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict

# Swagger API Imports
# based on `https://api-v3.mbta.com/docs/swagger/index.html#`
import mbta_client
from mbta_client import api_client, configuration
from mbta_client.api import alert_api, prediction_api, stop_api, route_api
from mbta_client.models import alert_resource, prediction_resource, stop_resource, trip_resource, vehicle_resource, route_resource
from mbta_client.api_response import ApiResponse

# Concurrency Imports
import threading
from concurrent.futures import ThreadPoolExecutor
from cache_manager import CacheManager

## Main API Class
class MBTA_API:
    """MBTA API client."""

    # Set Class Constants
    DEBUG      = False
    MAX_TIME   = 40
    MAX_LEN    = 10
    ROUTE_TYPE = '0,1,3' # https://gtfs.org/documentation/schedule/reference/#routestxt

    ROUTE_TYPE_LIGHTRAIL = 0
    ROUTE_TYPE_SUBWAY    = 1
    ROUTE_TYPE_RAIL      = 2
    ROUTE_TYPE_BUS       = 3
    ROUTE_TYPE_FERRY     = 4

    TRAIN_STATUS_MAP = {
        'STOPPED_AT': 'Boarding',
        'INCOMING_AT': 'Arriving',
        'IN_TRANSIT_TO': 'Next Stop' }

    # Cache TTL settings (seconds)
    STOPS_CACHE_TTL  = 86400    # 24 hours
    TRIPS_CACHE_TTL  = 3600     # 1 hour
    ALERTS_CACHE_TTL = 60       # 1 minute for static data
    PREDICTIONS_CACHE_TTL = 10  # 10 seconds for real-time data

    # Thread pool settings
    MAX_WORKERS = 64


    @dataclass(frozen=True)
    class CurrentStopInfo:
        """Dataclass to hold current stop information."""
        stopId:      str
        stopName:    str
        description: str
        routeType:   int
        platName:    Optional[str] = None
        stopColor:   Optional[str] = None


    @dataclass(frozen=True)
    class CollectedPrediction:
        """Dataclass to hold raw important information about a prediction"""
        prediction: prediction_resource.PredictionResource
        trip:       Optional[trip_resource.TripResource]       = None
        vehicle:    Optional[vehicle_resource.VehicleResource] = None
        stop:       Optional[stop_resource.StopResource]       = None
        route:      Optional[route_resource.RouteResource]     = None


    ### Setup and Destructor Scripts
    def __init__(self, key: str = "", logger: Optional[logging.Logger] = None):
        """Initialize MBTA API client."""
        self.logger = logger or logging.getLogger("MBTA_API")
        self._setup_api_client(key)
        self.cache = CacheManager()
        self._executor = ThreadPoolExecutor(max_workers=self.MAX_WORKERS)


    def _setup_api_client(self, key: str) -> None:
        """Setup API client configuration."""
        if not key: key = os.environ.get("MBTA_API_KEY", "")
        if not key: raise ValueError("MBTA API key is required. Set MBTA_API_KEY environment variable or pass key parameter.")

        self._config = configuration.Configuration()
        self._config.host = "https://api-v3.mbta.com"
        self._config.api_key = {'api_key_in_header': key}
        self._config.client_side_validation = False

        self._api_client = api_client.ApiClient(configuration=self._config)
        self._api_client.set_default_header('Accept-Encoding', 'gzip')

        # Initialize API endpoints
        self._alert      = alert_api.AlertApi(api_client=self._api_client)
        self._prediction = prediction_api.PredictionApi(api_client=self._api_client)
        self._stop       = stop_api.StopApi(api_client=self._api_client)
        self._route      = route_api.RouteApi(api_client=self._api_client)


    def __enter__(self):
        return self


    def __exit__(self, exc_type, exc_val, exc_tb):
        self._close()


    def _close(self):
        """Close the API client and executor."""
        if self._executor:
            self._executor.shutdown(wait=True)
        self.logger.info("MBTA API client closed.")


    ### API Methods
    def _cacheResponse(self, key: str, response: ApiResponse):
        """Cache the API response."""
        if not response or not response.data:
            self.logger.warning(f"Empty response for key '{key}', not caching.")
            return

        self.cache.set(key, response.data.data, last_modified=response.headers.get('last-modified'))


    def _cached_request(self, cache_key: str, ttl_seconds: int, fetch_fn: Callable[[], ApiResponse], extract_fn: Callable[[ApiResponse], Any] = None) -> Any:
        """Fetch with TTL cache and HTTP 304 support. extract_fn defaults to r.data.data."""
        if extract_fn is None:
            extract_fn = lambda r: r.data.data

        exp, cached = self.cache.get(cache_key, ttl_seconds=ttl_seconds)
        if not exp and cached is not None:
            if self.DEBUG: self.logger.debug(f"Cache hit for '{cache_key}'")
            return cached.data

        response = fetch_fn()

        if response.status_code == 304:
            self.cache.refresh(cache_key)
            if cached is None:
                raise ValueError(f"Received 304 Not Modified for '{cache_key}' but no cached data available.")
            if self.DEBUG: self.logger.debug(f"304 Not Modified for '{cache_key}', returning cached data")
            return cached.data
        elif 200 <= response.status_code < 300:
            remaining = response.headers.get('x-ratelimit-remaining')
            if remaining is not None and int(remaining) < 20:
                self.logger.warning(f"Rate limit low: {remaining} requests remaining (resets: {response.headers.get('x-ratelimit-reset')})")
            self._cacheResponse(cache_key, response)
            return extract_fn(response)
        else:
            self.logger.error(f"Failed to fetch '{cache_key}'. HTTP status code: {response.status_code}")
            return []


    def _parse_included(self, raw_data: bytes) -> Dict[str, Dict[str, Any]]:
        """Parse the 'included' array from a raw JSONAPI response into typed model objects.
        Returns { "trip": {id: TripResource}, "vehicle": {id: VehicleResource}, ... }
        """
        result: Dict[str, Dict[str, Any]] = {"trip": {}, "vehicle": {}, "route": {}, "stop": {}}
        try:
            body = json.loads(raw_data)
        except (json.JSONDecodeError, TypeError):
            return result

        type_map = {
            "trip":    trip_resource.TripResource.from_dict,
            "vehicle": vehicle_resource.VehicleResource.from_dict,
            "route":   route_resource.RouteResource.from_dict,
            "stop":    stop_resource.StopResource.from_dict,
        }

        for item in body.get("included", []):
            rtype = item.get("type")
            rid   = item.get("id")
            if rtype in type_map and rid:
                try:
                    result[rtype][rid] = type_map[rtype](item)
                except Exception as e:
                    self.logger.warning(f"Failed to parse included {rtype} {rid}: {e}")

        return result


    def _rel_id(self, pred: "prediction_resource.PredictionResource", rel_type: str) -> Optional[str]:
        """Extract the relationship ID from a prediction resource safely."""
        rel = getattr(pred.relationships, rel_type, None) if pred.relationships else None
        return rel.data.id if rel and rel.data else None


    def _fetch_predictions_with_related(self, stopId: str) -> "list[MBTA_API.CollectedPrediction]":
        """Fetch predictions for a stop with trip, vehicle, route in one call.
        Replaces the separate prediction + fan-out pattern. Handles TTL cache and HTTP 304.
        """
        cacheKey = f"PredictionsRelated_{stopId}"
        exp, cached = self.cache.get(cacheKey, ttl_seconds=self.PREDICTIONS_CACHE_TTL)
        if not exp and cached is not None:
            if self.DEBUG: self.logger.debug(f"Cache hit for '{cacheKey}'")
            return cached.data

        if self.DEBUG: self.logger.debug(f"Fetching predictions+related for stop {stopId}")

        response = self._prediction.api_web_prediction_controller_index_with_http_info(
            filter_stop = stopId,
            include     = "trip,vehicle,route",
            page_limit  = self.MAX_LEN,
            _headers    = self.cache.get_cache_headers(cacheKey) )

        if response.status_code == 304:
            self.cache.refresh(cacheKey)
            if cached is None:
                raise ValueError(f"Received 304 for '{cacheKey}' but no cached data available.")
            if self.DEBUG: self.logger.debug(f"304 Not Modified for '{cacheKey}'")
            return cached.data

        if not (200 <= response.status_code < 300):
            self.logger.error(f"Failed to fetch predictions for stop {stopId}: HTTP {response.status_code}")
            return []

        remaining = response.headers.get('x-ratelimit-remaining')
        if remaining is not None and int(remaining) < 20:
            self.logger.warning(f"Rate limit low: {remaining} requests remaining (resets: {response.headers.get('x-ratelimit-reset')})")

        predictions = response.data.data or []
        included    = self._parse_included(response.raw_data)

        collected = [
            self.CollectedPrediction(
                prediction = p,
                trip    = included["trip"].get(   self._rel_id(p, "trip")    ),
                vehicle = included["vehicle"].get( self._rel_id(p, "vehicle") ),
                route   = included["route"].get(   self._rel_id(p, "route")   ),
            ) for p in predictions
        ]

        self.cache.set(cacheKey, collected, last_modified=response.headers.get('last-modified'))
        return collected


    def getSubwayAlerts(self, routeType=None) -> list[alert_resource.AlertResource]:
        """Get subway alerts with caching and HTTP 304 support."""
        if self.DEBUG: self.logger.debug("Fetching subway alerts...")
        return self._cached_request(
            cache_key   = "Alerts",
            ttl_seconds = self.ALERTS_CACHE_TTL,
            fetch_fn    = lambda: self._alert.api_web_alert_controller_index_with_http_info(
                filter_route_type = routeType or self.ROUTE_TYPE,
                _headers          = self.cache.get_cache_headers("Alerts") ) )


    def getSubwayStops(self, routeType=None) -> list[stop_resource.StopResource]:
        """Get subway stops with caching and HTTP 304 support."""
        if self.DEBUG: self.logger.debug("Fetching subway stops...")
        return self._cached_request(
            cache_key   = "Stops",
            ttl_seconds = self.STOPS_CACHE_TTL,
            fetch_fn    = lambda: self._stop.api_web_stop_controller_index_with_http_info(
                filter_route_type = routeType or self.ROUTE_TYPE,
                _headers          = self.cache.get_cache_headers("Stops") ) )


    def getStopsByIds(self, stopIds: str) -> list[stop_resource.StopResource]:
        """Get multiple stops by comma-separated IDs."""

        if self.DEBUG: self.logger.debug(f"Fetching stops by IDs: {stopIds}")
        stopIds = set(stopIds.split(","))
        return [ stop for stop in self.getSubwayStops() if stop.id in stopIds ]


    def getStopsNearLocation(self, lat: float, lon: float, radiusMiles: float = 0.8, overrideRouteType: Optional[str] = None) -> list[stop_resource.StopResource]:
        """Get stops near a specific latitude and longitude within a given radius."""

        # Convert radius from miles to degrees (approximate specifically for MBTA area)
        radiusDegrees = radiusMiles * 0.02

        if self.DEBUG: self.logger.debug(f"Fetching stops near location ({lat}, {lon}) within {radiusMiles} miles...")

        return self._stop.api_web_stop_controller_index(
            filter_latitude   = str(lat),
            filter_longitude  = str(lon),
            filter_radius     = radiusDegrees,
            filter_route_type = overrideRouteType or self.ROUTE_TYPE,
            sort = 'distance'
        ).data


    def getRoutesByIds(self, routeIds: str) -> list[route_resource.RouteResource]:
        """Get multiple routes by comma-separated IDs in a single batch request."""
        if self.DEBUG: self.logger.debug(f"Fetching routes by IDs: {routeIds}")
        cacheKey = f"Routes_{routeIds}"
        return self._cached_request(
            cache_key   = cacheKey,
            ttl_seconds = self.TRIPS_CACHE_TTL,
            fetch_fn    = lambda: self._route.api_web_route_controller_index_with_http_info(
                filter_id  = routeIds,
                page_limit = self.MAX_LEN,
                _headers   = self.cache.get_cache_headers(cacheKey) ) )


    def getStationPredictions(self, stopName="Lechmere"):
        """Fetch predictions for all stops matching stopName, with related resources included."""
        stops = self.getSubwayStops()
        matchingStops = { MBTA_API.CurrentStopInfo(
                            stopId      = stop.id,
                            stopName    = stop.attributes.name,
                            description = stop.attributes.description,
                            routeType   = stop.attributes.vehicle_type,
                            platName    = stop.attributes.platform_name,
                            stopColor   = stop.attributes.description.split("-")[1].strip() if (stop.attributes.vehicle_type in (self.ROUTE_TYPE_LIGHTRAIL, self.ROUTE_TYPE_SUBWAY)) else None
                        ) for stop in stops if stop.attributes.name == stopName }

        futures = [ (stop, self._executor.submit(self._fetch_predictions_with_related, stop.stopId))
                    for stop in matchingStops ]

        return { stop: f.result() for stop, f in futures }


if __name__ == '__main__':
    import time, logging

    api = MBTA_API(logger=logging.getLogger("MBTA_API"))
    api.logger.setLevel(logging.DEBUG)
    handler   = logging.StreamHandler()
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
    handler.setFormatter(formatter)
    api.logger.addHandler(handler)


    def pprint(data):
        if isinstance(data, list) or isinstance(data, dict): print(dumps(data, indent=2, default=str))
        else: print(dumps(data.to_dict(), indent=2, default=str))


    def sendRequests():
        """Send three quick requests to get stops to ensure 304 responses are received."""
        api.DEBUG = True
        api.STOPS_CACHE_TTL = 2

        api.logger.info("Sending initial request to get subway stops...")
        # stops = api._stop.api_web_stop_controller_index(
        #         filter_route_type='0,1',  # Default route type for subway
        #         _headers=None )  # No additional headers for initial request
        stops = api.getSubwayStops()

        # Send three requests to ensure we get 304 responses
        for _ in range(6):
            print("----+----")
            stops = api.getSubwayStops()
            time.sleep(1)
            #pprint(stops)


    def timeStationGets():
        """Measure time taken to get predictions for multiple stations."""
        statuses = set()
        tstats   = set()
        #api.DEBUG = False

        s = time.time()
        for station in ["Lechmere", "State", "Downtown Crossing", "Park Street", "North Station"]:
            dat = api.getStationPredictions(station)
            pprint(dat)
            for _, val in dat.items():
                for v in val:
                    statuses.add(v.status)
                    tstats.add(v.tInfo)
        t = time.time() - s

        print(statuses)
        print(tstats)
        print(f"Took {t:.4f}s to run.")


    def timeWithNWorkers(max_workers=16):
        """Measure time taken to get predictions with a specific number of workers."""
        api._executor = ThreadPoolExecutor(max_workers=max_workers)
        api.cache = CacheManager()  # Reset cache for clean test
        s = time.time()
        for station in ["Lechmere", "State", "Downtown Crossing", "Park Street", "North Station"]:
            api.getStationPredictions(station)
        t = time.time() - s
        print(f"Took {t:.4f}s with {max_workers} workers.")


    def loopTimeWithNWorkers():
        """Loop through different worker counts to measure performance."""
        timeWithNWorkers()
        for workers in [8, 12, 16, 24, 32]:
            timeWithNWorkers(workers)
            time.sleep(1)
            timeWithNWorkers(workers)
            time.sleep(1)
            timeWithNWorkers(workers)
            time.sleep(1)
            timeWithNWorkers(workers)
            time.sleep(1)

    def testGetRoutesById():
        api.DEBUG = True
        for r in api.getRoutesByIds("Red,Green-E,69"):
            pprint(r)
        for r in api.getRoutesByIds("Red,Green-E,69,70,Blue"):
            pprint(r)

    def tmp():
        api.ROUTE_TYPE = "0,1,3"
        v = api.getStationPredictions()
        for k, i in v.items():
            print(f"--- {k} ---")
            for p in i:
                if not p.vehicle: continue
                pprint(p.prediction.to_dict() if p.prediction else ["No prediction"])
                pprint(p.trip.to_dict() if p.trip else ["No trip"])
                pprint(p.vehicle.to_dict() if p.vehicle else ["No vehicle"])
                pprint(p.stop.to_dict() if p.stop else ["No stop"])
                pprint(p.route.to_dict() if p.route else ["No route"])
                print("\n\n")
                break

    def testGetStopsNearLocation():
        api.DEBUG = True
        stops = api.getStopsNearLocation(lat=42.3564, lon=-71.0622)
        for stop in stops:
            print("\n----------------------------\n")
            pprint(stop.to_dict())

    def getAlerts():
        api.DEBUG = True
        alerts = api.getSubwayAlerts()
        for alert in alerts:
            print("\n----------------------------\n")
            # pprint(alert.to_dict())
            print(alert.to_dict()["attributes"]["timeframe"]) if alert.to_dict()["attributes"]["timeframe"] else "NONE"
            print(alert.to_dict()["attributes"]["lifecycle"]) if alert.to_dict()["attributes"]["lifecycle"] else "NONE"
            print(alert.to_dict()["attributes"]["effect"]) if alert.to_dict()["attributes"]["effect"] else "NONE"

    api.DEBUG = True
    print( api.getStationPredictions("Sullivan Square") )
