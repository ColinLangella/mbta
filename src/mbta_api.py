# General Imports
import os, logging
from datetime import datetime, timezone
from json import dumps

# Typing Imports
from dataclasses import dataclass
from typing import Optional

# Swagger API Imports
# based on `https://api-v3.mbta.com/docs/swagger/index.html#`
import mbta_client
from mbta_client import api_client, configuration
from mbta_client.api import alert_api, prediction_api, stop_api, trip_api, vehicle_api
from mbta_client.models import alert_resource, prediction_resource, stop_resource, trip_resource, vehicle_resource
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
    ROUTE_TYPE = '0,1' # https://gtfs.org/documentation/schedule/reference/#routestxt

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
    MAX_WORKERS = 16


    @dataclass
    class FormattedPrediction:
        """Dataclass to hold important information about a prediction"""
        line:        str
        end_station: str
        arrival:     str
        wait:        str
        direction:   int
        status:      str
        tInfo:       str


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
            if self.DEBUG: self.logger.debug("Received 304 Not Modified for alerts, returning cached data")
            return cached.data
        else:
            self._cacheResponse("Alerts", httpResponse)
            return httpResponse.data.data


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
        else:
            self._cacheResponse("Stops", httpResponse)
            return httpResponse.data.data


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
        else:
            self._cacheResponse(cacheStr, httpResponse)
            return httpResponse.data.data


    def getTripsById(self, tripId: str) -> list[trip_resource.TripResource]:
        """Get trip details by ID with caching and HTTP last modified headers."""

        ## In the short term, the benefits of caching trip data are limited
        ## as trip changes too frequently. So for now we'll just fetch it fresh.

        ## TODO: Future improvement: loop through trip IDs and cache them

        if self.DEBUG: self.logger.debug(f"Fetching trip ID from API: {tripId}")
        return self._trip.api_web_trip_controller_index(
            filter_id=tripId ).data


    def getVehicleById(self, trainId: str) -> list[vehicle_resource.VehicleResource]:
        """Get vehicle details by ID with caching and HTTP last modified headers."""

        ## NOTE: Same as trips, vehicle data changes frequently
        ## so caching is not very beneficial in the short term.

        if self.DEBUG: self.logger.debug(f"Fetching train ID: {trainId}")
        return self._vehicle.api_web_vehicle_controller_index(
            filter_id=trainId ).data



    def getStationPredictions(self, stopName="Lechmere"):
        """
        Fetch predictions for all station descriptions matching stopName.
        Parallelized over all stopIds.
        """

        def getPredData(predInfo: list[prediction_resource.PredictionResource]):
            """Fetch trip and vehicle data for predictions in parallel."""
            try:
                tripIds    = [ p.relationships.trip.data.id for p in predInfo ]
                vehicleIds = [ p.relationships.vehicle.data.id for p in predInfo ]

                # Fetch data in parallel
                trips, vehicles = [], []
                if tripIds or vehicleIds:
                    if tripIds:
                        tripFutures = self._executor.submit(
                            self.getTripsById, ",".join(tripIds) )

                    if vehicleIds:
                        vehicleFutures = self._executor.submit(
                            self.getVehicleById, ",".join(vehicleIds) )

                    # Collect results
                    if tripIds:    trips    = tripFutures.result()
                    if vehicleIds: vehicles = vehicleFutures.result()
            
            except Exception as e:
                self.logger.exception(f"Exception while trying to gedPredData: {e}")
                if self.DEBUG: self.logger.debug(predInfo)
                trips    = [None]*len(predInfo)
                vehicles = [None]*len(predInfo)

            return zip(predInfo, trips, vehicles)


        def formatPrediction( stationIds: set,
                predInfo: prediction_resource.PredictionResource,
                trip: trip_resource.TripResource,
                train: vehicle_resource.VehicleResource):
            """Format a single prediction into a structured object."""
            if trip is not None: headsign = trip.attributes.headsign
            else:
                if self.DEBUG: self.logger.warning(f"Missing trip data: {trip}")
                if self.DEBUG: self.logger.debug(f"Prediction Info: {predInfo}")
                headsign = "Unknown"

            try:
                if train is None: raise Exception()
                trainStopId = train.relationships.stop.data.id
                trainStatusRaw = train.attributes.current_status

                # Ensure that train status applies to one of current station IDs
                trainStatus = self.TRAIN_STATUS_MAP.get(trainStatusRaw, "") if trainStopId in stationIds else ""
            except TypeError as e:
                if self.DEBUG: self.logger.warning(f"Incomplete train: {train}")
                if self.DEBUG: self.logger.debug(f"Prediction Info: {predInfo}")
                trainStatus = ""
            except:
                if self.DEBUG: self.logger.warning(f"Missing train data: {train}")
                if self.DEBUG: self.logger.debug(f"Prediction Info: {predInfo}")
                trainStatus = ""

            arrivalTime   = predInfo.attributes.arrival_time
            departureTime = predInfo.attributes.departure_time
            currentTime   = datetime.now(tz=timezone.utc)

            if arrivalTime:
                waitTime = (datetime.fromisoformat(arrivalTime) - currentTime).total_seconds()
                if waitTime < 0: waitTime = 0  # Ensure non-negative wait
            elif departureTime:
                waitTime = (datetime.fromisoformat(departureTime) - currentTime).total_seconds()
                if waitTime < 0: waitTime = 0  # Ensure non-negative wait
            else:
                waitTime = 6000  # Arbitrary fallback far in future

            return self.FormattedPrediction(
                predInfo.relationships.route.data.id,
                headsign, arrivalTime, f"{int(waitTime//60)} minute(s)",
                predInfo.attributes.direction_id,
                predInfo.attributes.status, trainStatus )


        def processStop(desc, stopId):
            """Process a single stop to get predictions."""
            if self.DEBUG: self.logger.debug(f"Processing stop: {desc} (ID: {stopId})")

            preds = self.getPredictionsFromStopId(stopId)
            predInfo = getPredData(preds)

            futures = [
                self._executor.submit(formatPrediction, stopIds, pred, trip, train)
                for pred, trip, train in predInfo ]
            return desc, [f.result() for f in futures]


        # Get all subway stops
        stops = self.getSubwayStops()
        matchingStops = [stop for stop in stops if stop.attributes.name == stopName]

        stopDescToId = {stop.attributes.description: stop.id for stop in matchingStops}
        stopIds = {stop.id for stop in matchingStops}


        # Use ThreadPoolExecutor to process stops in parallel
        futures = [
            self._executor.submit(processStop, desc, stopId)
            for desc, stopId in stopDescToId.items() ]
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

    timeStationGets()
