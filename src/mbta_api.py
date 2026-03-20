# General Imports
import os, logging
from datetime import datetime
from timeparser import *
from dataclasses import dataclass
from json import dumps

# Swagger API Imports
# based on `https://api-v3.mbta.com/docs/swagger/index.html#`
import mbta_client
from mbta_client import api_client, configuration
from mbta_client.api import alert_api, prediction_api, stop_api, trip_api, vehicle_api
from mbta_client.models import alert_resource, prediction_resource, stop_resource, trip_resource, vehicle_resource

# Concurrency Imports
import threading
from concurrent.futures import ThreadPoolExecutor

## Main API Class
class MBTA_API:
    # Set Class Constants
    DEBUG = False
    MAX_TIME = 40
    MAX_LEN = 10
    ROUTE_TYPE = '0,1' # defined by `https://gtfs.org/documentation/schedule/reference/#routestxt`

    TRAIN_STATUS_MAP = {
        'STOPPED_AT': 'Boarding',
        'INCOMING_AT': 'Arriving',
        'IN_TRANSIT_TO': 'Next Stop' }

    # Prediction Dataclass With Usefull Info
    @dataclass
    class FormattedPrediction:
        line:        str
        end_station: str
        arrival:     str
        wait:        str
        direction:   int
        status:      str
        tInfo:       str


    def __init__(self, key: str = "", logger: logging.Logger = logging.Logger("MBTA API Logger")):
        if not key: key = os.environ.get("MBTA_API_KEY", "")
        if not key: raise Exception("Error: Cannot find API key")

        self._config = configuration.Configuration()
        self._config.host = "https://api-v3.mbta.com"
        self._config.api_key = {'api_key_in_header': key}
        self._config.client_side_validation = False
        self._api_client = api_client.ApiClient(configuration=self._config)

        self._alert = alert_api.AlertApi(api_client=self._api_client)
        self._prediction = prediction_api.PredictionApi(api_client=self._api_client)
        self._stop = stop_api.StopApi(api_client=self._api_client)
        self._trip = trip_api.TripApi(api_client=self._api_client)
        self._vehicle = vehicle_api.VehicleApi(api_client=self._api_client)

        self.cache = {
            "Stops": {"timestamp": None, "data": None},
            "Trips": {}  # tripId -> {"timestamp": ..., "data": ...}
        }
        self._lock = threading.Lock()
        self.logger = logger


    def getSubwayAlerts(self, routeType='0,1') -> list[alert_resource.AlertResource]:
        if self.DEBUG: self.logger.debug("Fetching subway alerts...")
        return self._alert.api_web_alert_controller_index(
            filter_route_type = routeType or self.ROUTE_TYPE ).data


    def getSubwayStops(self, routeType=None) -> list[stop_resource.StopResource]:
        cached = self.cache["Stops"]
        if cached["data"] is not None and cached["timestamp"] and not is_older_than(cached["timestamp"], 86400):
            if self.DEBUG: self.logger.debug("Returning cached subway stops")
            return cached["data"] 

        if self.DEBUG: self.logger.info("Fetching subway stops from MBTA API")
        data = self._stop.api_web_stop_controller_index(
            filter_route_type = routeType or self.ROUTE_TYPE ).data
        with self._lock:
            self.cache["Stops"] = {
                "timestamp": datetime.now(),
                "data": data }
        return data


    def getPredictionsFromStopId(self, stopId: int) -> list[prediction_resource.PredictionResource]:
        if self.DEBUG: self.logger.debug(f"Fetching predictions for stop ID: {stopId}")
        return self._prediction.api_web_prediction_controller_index(
            filter_stop=stopId,
            page_limit=self.MAX_LEN ).data


    def getTripsById(self, tripId: str) -> list[trip_resource.TripResource]:
        # if isinstance(tripId, str) and "ADDED" in tripId:
        #     if DEBUG: self.logger.warning(f"Trip ID '{tripId}' marked as 'ADDED'; returning placeholder")
        #     return {"attributes": {"headsign": "Unknown"}}

        tripCache = self.cache["Trips"].get(tripId)
        if tripCache and not is_older_than(tripCache["timestamp"], 3600):
            if self.DEBUG: self.logger.debug(f"Returning cached trip ID: {tripId}")
            return tripCache["data"]

        if self.DEBUG: self.logger.debug(f"Fetching trip ID: {tripId}")
        data = self._trip.api_web_trip_controller_index(
            filter_id=tripId ).data
        with self._lock:
            self.cache["Trips"][tripId] = {
                "timestamp": datetime.now(),
                "data": data }
        return data


    def getVehicleById(self, trainId: str) -> list[vehicle_resource.VehicleResource]:
        if self.DEBUG: self.logger.debug(f"Fetching train ID: {trainId}")
        return self._vehicle.api_web_vehicle_controller_index(
            filter_id=trainId ).data



    def getStationPredictions(self, stopName="Lechmere"):
        """
        Fetch predictions for all station descriptions matching stopName.
        Parallelized over all stopIds.
        """

        def getPredData(predInfo: list[prediction_resource.PredictionResource]):
            try:
                tripIds    = [ p.relationships.trip.data.id for p in predInfo ]
                vehicleIds = [ p.relationships.vehicle.data.id for p in predInfo ]

                tripStr    = ",".join(tripIds)
                vehicleStr = ",".join(vehicleIds)

                with ThreadPoolExecutor(max_workers=2) as executor:
                    futTrips    = executor.submit( self.getTripsById, tripStr )
                    futVehicles = executor.submit( self.getVehicleById, vehicleStr )

                    trips    = futTrips.result()
                    vehicles = futVehicles.result()
            
            except Exception as e:
                if self.DEBUG: self.logger.exception(f"Exception while trying to gedPredData: {e}")
                if self.DEBUG: self.logger.debug(predInfo)
                trips    = [None]*len(predInfo)
                vehicles = [None]*len(predInfo)

            return zip(predInfo, trips, vehicles)


        def formatPrediction( stationIds: set,
                predInfo: prediction_resource.PredictionResource,
                trip: trip_resource.TripResource,
                train: vehicle_resource.VehicleResource):
            if trip is not None: headsign = trip.attributes.headsign
            else:
                if self.DEBUG: self.logger.warning(f"Missing trip data: {trip}")
                if self.DEBUG: self.logger.debug(f"Prediction Info: {predInfo}")
                headsign = "Unknown"

            try:
                if train is None: raise Exception()
                trainStopId = train.relationships.stop.data.id
                trainStatusRaw = train.attributes.current_status
                trainStatus = self.TRAIN_STATUS_MAP.get(trainStatusRaw, "") if trainStopId in stationIds else ""
            except TypeError as e:
                if self.DEBUG: self.logger.warning(f"Incomplete train: {train}")
                if self.DEBUG: self.logger.debug(f"Prediction Info: {predInfo}")
                trainStatus = ""
            except:
                if self.DEBUG: self.logger.warning(f"Missing train data: {train}")
                if self.DEBUG: self.logger.debug(f"Prediction Info: {predInfo}")
                trainStatus = ""

            arrivalTime = predInfo.attributes.arrival_time
            departureTime = predInfo.attributes.departure_time

            if arrivalTime:
                waitMinutes = minutes_from_now(arrivalTime)
            elif departureTime:
                waitMinutes = minutes_from_now(departureTime)
            else:
                waitMinutes = 100  # Arbitrary fallback far in future

            return self.FormattedPrediction(
                predInfo.relationships.route.data.id,
                headsign, arrivalTime, f"{waitMinutes} minute(s)",
                predInfo.attributes.direction_id,
                predInfo.attributes.status, trainStatus )


        def processStop(desc, stopId):
            if self.DEBUG: self.logger.debug(f"Processing stop: {desc} (ID: {stopId})")

            preds = self.getPredictionsFromStopId(stopId)
            predInfo = getPredData(preds)

            with ThreadPoolExecutor() as sub_executor:
                futures = [sub_executor.submit(formatPrediction, stopIds, pred, trip, train) for pred, trip, train in predInfo]
                return desc, [f.result() for f in futures]


        stops = self.getSubwayStops()
        matchingStops = [stop for stop in stops if stop.attributes.name == stopName]

        stopDescToId = {stop.attributes.description: stop.id for stop in matchingStops}
        stopIds = {stop.id for stop in matchingStops}

        result = {}
        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(processStop, desc, stopId) for desc, stopId in stopDescToId.items()]
            result = dict(f.result() for f in futures)

        return result


if __name__ == '__main__':
    import time
    def pprint(data):
        if isinstance(data, list) or isinstance(data, dict): print(dumps(data, indent=2, default=str))
        else: print(dumps(data.to_dict(), indent=2, default=str))

    api = MBTA_API()

    def timeStationGets():
        statuses = set()
        tstats   = set()

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

    # alerts = api.getSubwayAlerts()
    # pprint([a.to_dict() for a in alerts])

    # stops = api.getSubwayStops()
    # pprint(stops)

    timeStationGets()
