# General Imports
import os, logging
from json import dumps

# Typing Imports
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict

# Swagger API Imports
# based on `https://api-v3.mbta.com/docs/swagger/index.html#`
import mbta_client
from mbta_client import api_client, configuration
from mbta_client.api import alert_api, prediction_api, stop_api, trip_api, vehicle_api, route_api
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

    MY_LATITUDE  =  42.3564
    MY_LONGITUDE = -71.0622

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


    @dataclass
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

        # Initialize API endpoints
        self._alert      = alert_api.AlertApi(api_client=self._api_client)
        self._prediction = prediction_api.PredictionApi(api_client=self._api_client)
        self._stop       = stop_api.StopApi(api_client=self._api_client)
        self._trip       = trip_api.TripApi(api_client=self._api_client)
        self._vehicle    = vehicle_api.VehicleApi(api_client=self._api_client)
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


    def getSubwayAlerts(self, routeType=None) -> list[alert_resource.AlertResource]:
        """Get subway alerts with caching and HTTP last modified headers."""

        if self.DEBUG: self.logger.debug("Fetching subway alerts...")
        exp, cached = self.cache.get("Alerts", ttl_seconds=self.ALERTS_CACHE_TTL)

        if not exp and cached is not None:
            if self.DEBUG: self.logger.debug("Returning cached subway alerts")
            return cached.data

        if self.DEBUG: self.logger.info("Fetching alerts from MBTA API")
        httpResponse = self._alert.api_web_alert_controller_index_with_http_info(
            filter_route_type = routeType or self.ROUTE_TYPE,
            _headers = self.cache.get_cache_headers("Alerts") )

        if httpResponse.status_code == 304:
            self.cache.refresh("Alerts")
            if not cached: raise ValueError("Received 304 Not Modified for alerts but no cached data is available.")
            if self.DEBUG: self.logger.debug("Received 304 Not Modified for alerts, returning cached data")
            return cached.data
        elif 200 <= httpResponse.status_code < 300:
            self._cacheResponse("Alerts", httpResponse)
            return httpResponse.data.data
        else:
            self.logger.error(f"Failed to fetch alerts. HTTP status code: {httpResponse.status_code}")
            return []


    def getSubwayStops(self, routeType=None) -> list[stop_resource.StopResource]:
        """Get subway stops with caching and HTTP last modified headers."""

        if self.DEBUG: self.logger.debug("Fetching subway stops...")
        exp, cached = self.cache.get("Stops", ttl_seconds=self.STOPS_CACHE_TTL)

        if not exp and cached is not None:
            if self.DEBUG: self.logger.debug("Returning cached subway stops")
            return cached.data

        if self.DEBUG: self.logger.info("Fetching subway stops from MBTA API")
        httpResponse = self._stop.api_web_stop_controller_index_with_http_info(
            filter_route_type = routeType or self.ROUTE_TYPE,
            _headers = self.cache.get_cache_headers("Stops") )

        if httpResponse.status_code == 304:
            self.cache.refresh("Stops")
            if self.DEBUG: self.logger.debug("Received 304 Not Modified for subway stops, returning cached data")
            return cached.data
        elif 200 <= httpResponse.status_code < 300:
            self._cacheResponse("Stops", httpResponse)
            return httpResponse.data.data
        else:
            self.logger.error(f"Failed to fetch subway stops. HTTP status code: {httpResponse.status_code}")
            return []


    def getStopsByIds(self, stopIds: str) -> list[stop_resource.StopResource]:
        """Get multiple stops by comma-separated IDs."""

        if self.DEBUG: self.logger.debug(f"Fetching stops by IDs: {stopIds}")
        stopIds = set(stopIds.split(","))
        return [ stop for stop in self.getSubwayStops() if stop.id in stopIds ]


    def getStopsNearLocation(self, lat: float = MY_LATITUDE, lon: float = MY_LONGITUDE, radiusMiles: float = 0.8, overrideRouteType: Optional[str] = None) -> list[stop_resource.StopResource]:
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


    def getPredictionsFromStopId(self, stopId: str) -> list[prediction_resource.PredictionResource]:
        """Get predictions for a specific stop ID with caching and HTTP last modified headers."""

        if self.DEBUG: self.logger.debug(f"Fetching predictions for stop ID: {stopId}")
        cacheStr    = f"Predictions_{stopId}"
        exp, cached = self.cache.get(cacheStr, ttl_seconds=self.PREDICTIONS_CACHE_TTL)

        if not exp and cached is not None:
            if self.DEBUG: self.logger.debug("Returning cached predictions")
            return cached.data

        if self.DEBUG: self.logger.info("Fetching predictions from MBTA API")
        httpResponse = self._prediction.api_web_prediction_controller_index_with_http_info(
            filter_stop = stopId, page_limit = self.MAX_LEN,
            _headers = self.cache.get_cache_headers(cacheStr) )

        if httpResponse.status_code == 304:
            self.cache.refresh(cacheStr)
            if self.DEBUG: self.logger.debug("Received 304 Not Modified for predictions, returning cached data")
            return cached.data
        elif 200 <= httpResponse.status_code < 300:
            self._cacheResponse(cacheStr, httpResponse)
            return httpResponse.data.data
        else:
            self.logger.error(f"Failed to fetch predictions for stop ID {stopId}. HTTP status code: {httpResponse.status_code}")
            return []


    def getTripsById(self, tripId: str) -> list[trip_resource.TripResource]:
        """Get trip details by ID with caching and HTTP last modified headers."""

        ## In the short term, the benefits of caching trip data are limited
        ## as trip changes too frequently. So for now we'll just fetch it fresh.

        ## TODO: Future improvement: loop through trip IDs and cache them


        if self.DEBUG: self.logger.debug(f"Fetching trip ID from API: {tripId}")
        ret = self._trip.api_web_trip_controller_index(
            filter_id=tripId ).data
        if self.DEBUG: self.logger.debug(f"Found trip matching ID: {tripId}")
        return ret


    def getVehicleById(self, trainId: str) -> list[vehicle_resource.VehicleResource]:
        """Get vehicle details by ID with caching and HTTP last modified headers."""

        ## NOTE: Same as trips, vehicle data changes frequently
        ## so caching is not very beneficial in the short term.

        if self.DEBUG: self.logger.debug(f"Fetching train ID: {trainId}")
        return self._vehicle.api_web_vehicle_controller_index(
            filter_id=trainId ).data


    def getRouteById(self, routeId: str) -> route_resource.RouteResource:
        """Get route details by ID."""

        # cache routes individually with 1hr ttl
        cacheStr = f"Route_{routeId}"
        exp, cached = self.cache.get(cacheStr, ttl_seconds=self.TRIPS_CACHE_TTL)

        if not exp and cached is not None:
            if self.DEBUG: self.logger.debug(f"Returning cached route for ID: {routeId}")
            return cached.data[0]

        if self.DEBUG: self.logger.info("Fetching routes from MBTA API")
        httpResponse = self._route.api_web_route_controller_index_with_http_info(
            filter_id = routeId, page_limit = self.MAX_LEN,
            _headers = self.cache.get_cache_headers(cacheStr) )

        if httpResponse.status_code == 304:
            self.cache.refresh(cacheStr)
            if self.DEBUG: self.logger.debug(f"Received 304 Not Modified for route {routeId}, returning cached data")
            return cached.data[0]
        elif 200 <= httpResponse.status_code < 300:
            self._cacheResponse(cacheStr, httpResponse)
            return httpResponse.data.data[0]
        else:
            self.logger.error(f"Failed to fetch route {routeId}. HTTP status code: {httpResponse.status_code}")
            return []


    def getRoutesByIds(self, routeIds: str):
        """Get multiple routes by comma-separated IDs."""

        routeList = routeIds.split(",")
        routeFutures = [ self._executor.submit(
            self.getRouteById, rId
        ) for rId in routeList ]

        return [ f.result() for f in routeFutures ]


    def zgetStationPredictions(self, stationName: str = "Lechmere") -> Dict[str, List[CurrentStopInfo]]:
        """
        Refactored to avoid nested deadlock by batching API calls.
        Uses existing class methods: getStops, getPredictions, getTrips, getVehicles, getRoutes.
        """
        if self.DEBUG: self.logger.debug(f"Fetching predictions for station: {stationName}")

        # 1. Get all stops (platforms/bus berths) for this station name
        allStops = self.getStops(filter_name=stationName)
        
        # 2. Filter by allowed route types (e.g., Subway and Bus)
        allowedTypes = [int(x.strip()) for x in self.ROUTE_TYPE.split(",")]
        relevantStops = [s for s in allStops if s.attributes.vehicle_type in allowedTypes]
        
        if not relevantStops:
            return {}

        # Create a map for quick lookup and a comma-separated string of IDs for the API
        stopIdMap = {s.id: s for s in relevantStops}
        stopIdsStr = ",".join(stopIdMap.keys())

        # 3. Batch fetch ALL predictions for all stops at once
        # The MBTA API 'filter[stop]' parameter accepts a comma-separated list.
        allPredictions = self.getPredictions(stopIdsStr)
        
        if not allPredictions:
            # Return empty lists for each platform so the UI shows "No Arrivals" instead of a blank page
            return {s.attributes.platform_name or s.attributes.name: [] for s in relevantStops}

        # 4. Collect all unique IDs for relationships across the entire station
        tripIds = set()
        vehicleIds = set()
        routeIds = set()

        for pred in allPredictions:
            if pred.relationships.trip.data:    tripIds.add(pred.relationships.trip.data.id)
            if pred.relationships.vehicle.data: vehicleIds.add(pred.relationships.vehicle.data.id)
            if pred.relationships.route.data:   routeIds.add(pred.relationships.route.data.id)

        # 5. Fetch all relationship data in parallel batches
        # These call the existing methods in your class that already handle lists of IDs
        futures = {
            'trips':    self._executor.submit(self.getTrips, list(tripIds)),
            'vehicles': self._executor.submit(self.getVehicles, list(vehicleIds)),
            'routes':   self._executor.submit(self.getRoutes, list(routeIds))
        }

        # Build lookup maps from the results
        # No nested .submit() calls here = No deadlock.
        tripMap    = {t.id: t for t in futures['trips'].result()}
        vehicleMap = {v.id: v for v in futures['vehicles'].result()}
        routeMap   = {r.id: r for r in futures['routes'].result()}

        # 6. Organize predictions by platform name
        finalData = {}
        for stop in relevantStops:
            platformKey = stop.attributes.platform_name or stop.attributes.name
            
            # Find predictions associated with this specific stop ID
            stopPreds = [p for p in allPredictions if p.relationships.stop.data.id == stop.id]
            
            infoList = []
            for pred in stopPreds:
                # Resolve the related objects using our pre-fetched maps
                t_id = pred.relationships.trip.data.id if pred.relationships.trip.data else None
                v_id = pred.relationships.vehicle.data.id if pred.relationships.vehicle.data else None
                r_id = pred.relationships.route.data.id if pred.relationships.route.data else None

                infoList.append(self.CurrentStopInfo(
                    prediction = pred,
                    trip       = tripMap.get(t_id),
                    vehicle    = vehicleMap.get(v_id),
                    stop       = stop,
                    route      = routeMap.get(r_id)
                ))
            
            finalData[platformKey] = infoList

        return finalData


    def getStationPredictions(self, stopName="Lechmere"):
        """
        Fetch predictions for all station descriptions matching stopName.
        Parallelized over all stopIds.
        """

        def getPredData(predInfo: list[prediction_resource.PredictionResource]):
            """Fetch trip and vehicle data for predictions in parallel."""
            try:
                ### Start by reoganizing input data

                # Subset of PredictionResourceRelationships.__properties
                importantRelTypes = [
                    "vehicle",
                    "trip",
                #   "stop",
                    "route"]

                # [ ( pred, { "vehicle": vID, "trip": tID, "stop": sID, "route": rID } ), ... ]
                expandedInfo = [ (
                    p, {
                        relType: getattr(p.relationships, relType).data.id
                        if (p.relationships is not None) and (getattr(p.relationships, relType) is not None) and (getattr(p.relationships, relType).data is not None)
                        else None for relType in importantRelTypes } )
                    for p in predInfo ]

                # { "vehicle" : { vID ... }, "trip" { tID ... }, "stop" { sID ... }, "route": { rID ... } }
                allRelationshipIds = {
                    relType: { data[relType] for _, data in expandedInfo if data[relType] is not None }
                    for relType in importantRelTypes }

                # Fetch data in parallel
                allRelationshipData = {}
                for relType in importantRelTypes:
                    relIds    = allRelationshipIds[relType]
                    haveAnyId = (len(relIds) > 0)

                    if haveAnyId:
                        if self.DEBUG: self.logger.debug(f"Fetching related {relType} data for {len(relIds)} unique IDs: {relIds}")

                        correctFunction = {
                            "vehicle": self.getVehicleById,
                            "trip":    self.getTripsById,
                            "stop":    self.getStopsByIds,
                            "route":   self.getRoutesByIds
                        }[relType]

                        relFutures = self._executor.submit(
                            correctFunction, ",".join(list(relIds)) )
                        allRelationshipData[relType] = (haveAnyId, relFutures)

                        if self.DEBUG: self.logger.debug(f"Done: Fetching related {relType} data for {len(relIds)} unique IDs: {relIds}")
                    else:
                        allRelationshipData[relType] = (haveAnyId, None)

                # Collect data
                for relType in importantRelTypes:
                    haveAnyId, relFutures = allRelationshipData[relType]
                    if haveAnyId:
                        dataMap = { i.id: i for i in relFutures.result() }
                        allRelationshipData[relType] = (haveAnyId, dataMap)

                # Create output
                return [
                    self.CollectedPrediction(
                        prediction = p,
                        trip       = allRelationshipData["trip"][1][data["trip"]]       if ("trip" in data) and (data["trip"] is not None)       else None,
                        vehicle    = allRelationshipData["vehicle"][1][data["vehicle"]] if ("vehicle" in data) and (data["vehicle"] is not None) else None,
                    #   stop       = allRelationshipData["stop"][1][data["stop"]]       if ("stop" in data) and (data["stop"] is not None)       else None,
                        route      = allRelationshipData["route"][1][data["route"]]     if ("route" in data) and (data["route"] is not None)     else None
                    ) for p, data in expandedInfo ]

            except Exception as e:
                self.logger.exception(f"Exception while trying to gedPredData: {e}")
                if self.DEBUG: self.logger.debug(predInfo)
                return [ self.CollectedPrediction(p) for p in predInfo ]


        def processStop(stop: MBTA_API.CurrentStopInfo):
            """Process a single stop to get predictions."""
            if self.DEBUG: self.logger.debug(f"Processing stop: {stop.description} (ID: {stop.stopId})")

            preds    = self.getPredictionsFromStopId(stop.stopId)
            predInfo = getPredData(preds)

            return stop, predInfo


        # Get all subway stops
        stops = self.getSubwayStops()
        matchingStops = { MBTA_API.CurrentStopInfo ( 
                            stopId   = stop.id,
                            stopName = stop.attributes.name,
                            description = stop.attributes.description,
                            routeType = stop.attributes.vehicle_type,
                            platName = stop.attributes.platform_name,
                            stopColor = stop.attributes.description.split("-")[1].strip() if (stop.attributes.vehicle_type in (self.ROUTE_TYPE_LIGHTRAIL, self.ROUTE_TYPE_SUBWAY)) else None
                        ) for stop in stops if stop.attributes.name == stopName }

        # Use ThreadPoolExecutor to process stops in parallel
        futures = [
            self._executor.submit(processStop, stop)
            for stop in matchingStops ]

        return dict(f.result() for f in futures)


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
        stops = api.getStopsNearLocation()
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
