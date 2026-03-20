import os, requests
from json import dumps
from datetime import datetime

import threading
from concurrent.futures import ThreadPoolExecutor

from flask import current_app, has_app_context
from timeparser import is_now_within_bounds, minutes_from_now, is_older_than

import logging

MAX_TIME = 40
MAX_LEN = 10

TRAIN_STATUS_MAP = {
    'STOPPED_AT': 'Boarding',
    'INCOMING_AT': 'Arriving',
    'IN_TRANSIT_TO': 'Next Stop'
}

DEBUG = False


class MBTA_API:
    def __init__(self, key: str = "", logger=None):
        if not key:
            key = os.environ.get("MBTA_API_KEY", "")
        if not key:
            raise Exception("Error: Cannot find API key")

        self.url = "https://api-v3.mbta.com"
        self.headers = {
            'Content-Type': 'application/json',
            'X-API-Key': key
        }

        self.cache = {
            "Stops": {"timestamp": None, "data": None},
            "Trips": {}  # tripId -> {"timestamp": ..., "data": ...}
        }
        self._lock = threading.Lock()
        self.logger = logger or logging.getLogger(__name__)

    def _makeRequest(self, method, endpoint, data=None, params=None):
        url = f"{self.url}/{endpoint}"
        try:
            response = requests.request(method, url, json=data, params=params, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f"API request failed: {e}")
            return {'error': getattr(e.response, 'status_code', None)}

    def _get(self, endpoint, params=None):
        return self._makeRequest('GET', endpoint, params=params)

    def _post(self, endpoint, data=None):
        return self._makeRequest('POST', endpoint, data=data)

    def getSubwayAlerts(self, routeType='0,1'):
        if DEBUG: self.logger.debug("Fetching subway alerts...")
        return self._get("alerts", params={'filter[route_type]': routeType}).get('data', [])

    def getSubwayStops(self, routeType='0,1'):
        cached = self.cache["Stops"]
        if cached["data"] is not None and cached["timestamp"] and not is_older_than(cached["timestamp"], 86400):
            if DEBUG: self.logger.debug("Returning cached subway stops")
            return cached["data"]

        if DEBUG: self.logger.info("Fetching subway stops from MBTA API")
        data = self._get("stops", params={'filter[route_type]': routeType}).get('data', [])
        with self._lock:
            self.cache["Stops"] = {
                "timestamp": datetime.now(),
                "data": data
            }
        return data

    def getPredictionsFromStopId(self, stopId: int):
        if DEBUG: self.logger.debug(f"Fetching predictions for stop ID: {stopId}")
        return self._get("predictions", params={"filter[stop]": stopId}).get("data", [])

    def getTripById(self, tripId: int | str):
        if isinstance(tripId, str) and "ADDED" in tripId:
            if DEBUG: self.logger.warning(f"Trip ID '{tripId}' marked as 'ADDED'; returning placeholder")
            return {"attributes": {"headsign": "Unknown"}}

        tripCache = self.cache["Trips"].get(tripId)
        if tripCache and not is_older_than(tripCache["timestamp"], 3600):
            if DEBUG: self.logger.debug(f"Returning cached trip ID: {tripId}")
            return tripCache["data"]

        if DEBUG: self.logger.debug(f"Fetching trip ID: {tripId}")
        rVal = self._get("trips", params={"filter[id]": str(tripId)}).get("data", [])
        result = rVal[0] if rVal else None
        with self._lock:
            self.cache["Trips"][tripId] = {
                "timestamp": datetime.now(),
                "data": result
            }
        return result

    def getTrainFromId(self, trainId: int):
        if DEBUG: self.logger.debug(f"Fetching train ID: {trainId}")
        rVal = self._get("vehicles", params={"filter[id]": trainId}).get("data", [])
        return rVal[0] if rVal else None


def formatPrediction(predInfo, api: MBTA_API = MBTA_API(), stationIds: set = set()):
    tripId = predInfo["relationships"]["trip"]["data"]["id"]
    vehicleId = predInfo["relationships"]["vehicle"]["data"]["id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_trip = executor.submit(api.getTripById, tripId)
        future_train = executor.submit(api.getTrainFromId, vehicleId)
        trip = future_trip.result()
        train = future_train.result()

    if trip is not None:
        headsign = trip["attributes"]["headsign"]
    else:
        if DEBUG: current_app.logger.warning(f"Missing trip data for id = {tripId}")
        if DEBUG: current_app.logger.debug(f"Prediction Info: {dumps(predInfo, indent=2)}")
        headsign = ""

    try:
        if train is None: raise Exception()
        trainStopId = train["relationships"]["stop"]["data"]["id"]
        trainStatusRaw = train["attributes"].get("current_status", "")
        trainStatus = TRAIN_STATUS_MAP.get(trainStatusRaw, "") if trainStopId in stationIds else ""
    except TypeError as e:
        if DEBUG: current_app.logger.warning(f"Incomplete train data for id = {vehicleId}")
        if DEBUG: current_app.logger.debug(f"Prediction Info: {dumps(predInfo, indent=2)}")
        trainStatus = ""
    except:
        if DEBUG: current_app.logger.warning(f"Missing train data for id = {vehicleId}")
        if DEBUG: current_app.logger.debug(f"Prediction Info: {dumps(predInfo, indent=2)}")
        trainStatus = ""

    arrivalTime = predInfo["attributes"].get("arrival_time")
    departureTime = predInfo["attributes"].get("departure_time")

    if arrivalTime:
        waitMinutes = minutes_from_now(arrivalTime)
    elif departureTime:
        waitMinutes = minutes_from_now(departureTime)
    else:
        waitMinutes = 100  # Arbitrary fallback far in future

    return {
        "Line": predInfo["relationships"]["route"]["data"]["id"],
        "End Station": headsign,
        "Arrival": arrivalTime,
        "Wait": f"{waitMinutes} minute(s)",
        "Direction": predInfo["attributes"].get("direction_id", -1),
        "Status": predInfo["attributes"].get("status"),
        "TInfo": trainStatus
    }


def getStationPredictions(stopName="Lechmere", api: MBTA_API = MBTA_API(), app=None):
    """
    Fetch predictions for all station descriptions matching stopName.
    Parallelized over all stopIds.
    """
    stops = api.getSubwayStops()
    matchingStops = [stop for stop in stops if stop["attributes"]["name"] == stopName]

    stopDescToId = {stop["attributes"]["description"]: stop["id"] for stop in matchingStops}
    stopIds = {stop["id"] for stop in matchingStops}

    def processStop(desc, stopId):
        if app:
            if DEBUG: app.logger.debug(f"Processing stop: {desc} (ID: {stopId})")
        preds = api.getPredictionsFromStopId(stopId)
        with ThreadPoolExecutor() as sub_executor:
            futures = [sub_executor.submit(formatPrediction, pred, api, stopIds) for pred in preds]
            return desc, [f.result() for f in futures]

    result = {}
    with ThreadPoolExecutor() as executor:
        futures = [executor.submit(processStop, desc, stopId) for desc, stopId in stopDescToId.items()]
        result = dict(f.result() for f in futures)

    return result


def formatStationPredictions(stopName="Lechmere", api: MBTA_API = MBTA_API(), app=None):
    """
    Organize predictions for display based on line and direction.
    """
    if app and DEBUG: app.logger.info(f"Formatting station predictions for: {stopName}")
    rawData = getStationPredictions(stopName, api=api, app=app)

    organized = {}  # {line: {direction: [ [ends], [predictions] ]}}

    for desc, preds in rawData.items():
        try:
            parts = [x.strip() for x in desc.split("-")]
            if len(parts) < 3:
                if app and DEBUG: app.logger.warning(f"Unexpected station format in '{desc}'")
                continue

            line = parts[1]
            end = parts[2]

            directionKey = preds[0]["Direction"] if preds and preds[0]["Direction"] != -1 else end

            if line not in organized:
                organized[line] = {}
            if directionKey not in organized[line]:
                organized[line][directionKey] = [[], []]

            organized[line][directionKey][0].append(end)
            organized[line][directionKey][1] += preds

        except Exception as e:
            if app and DEBUG: app.logger.error(f"Skipping malformed entry '{desc}': {e}")

    for line, directions in organized.items():
        for dirKey in directions:
            directions[dirKey][0] = " \\ ".join(directions[dirKey][0])

    def cleanPredictions(preds):
        def toMinutes(pred):
            if pred["Wait"] == "Boarding": return -100
            if pred["Wait"] == "Arriving": return -99
            if pred["Wait"] == "Next Stop": return -98
            try:
                return int(pred["Wait"].split()[0])
            except Exception:
                return 999

        preds = [p for p in preds if toMinutes(p) <= MAX_TIME]
        for p in preds:
            if p["TInfo"]:
                p["Wait"] = p["TInfo"]
        preds = sorted(preds, key=toMinutes)[:MAX_LEN]
        return preds

    finalData = {}
    for line, directions in organized.items():
        dirKeys = list(directions.keys())
        if not dirKeys:
            continue
        dir1, dir2 = dirKeys[0], dirKeys[1] if len(dirKeys) > 1 else dirKeys[0]

        finalData[line] = {
            "Direction 1": cleanPredictions(directions[dir1][1]),
            "Direction 2": cleanPredictions(directions[dir2][1]),
            "End 1": directions[dir1][0],
            "End 2": directions[dir2][0]
        }

    return finalData


if __name__ == "__main__":
    import time

    api = MBTA_API("")

    statuses = set()
    tstats   = set()

    s = time.time()
    for station in ["Lechmere", "State", "Downtown Crossing", "Park Street", "North Station"]:
        dat = getStationPredictions(station, api=api)
        print(dumps(dat, indent=2))
        for _, val in dat.items():
            for v in val:
                statuses.add(v["Status"])
                tstats.add(v["TInfo"])
    t = time.time() - s

    print(statuses)
    print(tstats)
    print(f"Took {t:.4f}s to run.")

    # ## Alerts Logic

    # alerts = api.getSubwayAlerts()
    # curr = []
    # futr = []

    # for alert in alerts:
    #     attr = alert.get("attributes")
    #     peds = attr.get("active_period")

    #     aData = {
    #         "header": attr.get("header", None)
    #     }

    #     if any([is_now_within_bounds(p) for p in peds]):
    #         curr.append(aData)
    #     else: futr.append(aData)

    # print("Current")
    # print(dumps(curr, indent=2))
    # print("\nFuture")
    # print(dumps(futr, indent=2))

    # ## Lines Logic

    # lines = api._get("lines", params={"include":"routes"}).get("data", [])
    # for l in lines:
    #     if l["attributes"]["long_name"] == "Green Line": greenLine = l
    # print(dumps(greenLine, indent=2))

    # ## Stops Logic

    # stops = api.getSubwayStops()
    # for s in sorted(list(stops), key=lambda x: x["attributes"]["name"]):
    #     print(dumps({"name": s["attributes"]["description"], "id": s["id"]}, indent=2))
    #     print(dumps(s, indent=2))

    # lech = [stop for stop in stops if stop["attributes"]["name"] == "Lechmere"]
    # print(dumps(lech, indent=2))
    # lIds = [stop["id"] for stop in lech]
    # print(lIds)

    # outbound = api._get("predictions", params={"filter[stop]": lIds[0]}).get("data", [])
    # inbound  = api._get("predictions", params={"filter[stop]": lIds[1]}).get("data", [])

    # print("----- ONE -----")
    # for p in outbound: print(dumps(pred(p, api), indent=2))
    # print("----- TWO -----")
    # for p in inbound:  print(dumps(pred(p, api), indent=2))
