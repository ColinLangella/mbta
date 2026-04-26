import logging
from mbta_api import MBTA_API

from dataclasses import dataclass
from typing import Dict, List, Optional

from datetime import datetime, timezone

@dataclass
class IndiviualPrediction:
    Wait: str
    Head: str
    Route: str
    Status: Optional[str] = None

@dataclass
class IndiviualTPlatform:
    Description: str
    Predictions: List[IndiviualPrediction]

@dataclass
class FormattedTLine:
    LineName: str
    Platform1: Optional[IndiviualTPlatform]
    Platform2: Optional[IndiviualTPlatform]
    LineColor: Optional[str] = None

@dataclass
class FormattedBusLine:
    BuswayName:  str
    Predictions: List[IndiviualPrediction]

@dataclass
class FormattedStationData:
    StationName: str
    TLines:   List[FormattedTLine]
    BusLines: List[FormattedBusLine] = None


"""Formats MBTA API data into structured formats for display."""
class StationDataFormatter:

    TRAIN_STATUS_MAP = {
        'STOPPED_AT':    'Boarding',
        'INCOMING_AT':   'Arriving',
        'IN_TRANSIT_TO': 'Next Stop' }

    def __init__(self, logger: logging.Logger, debug: bool = False):
        self.logger = logger
        self.debug  = debug


    """Processes a single prediction into an IndiviualPrediction."""
    def ProcessPrdiction(self, currentStop: MBTA_API.CurrentStopInfo, fullPred: MBTA_API.CollectedPrediction) -> IndiviualPrediction:
        # Pull out component fields
        pred    = fullPred.prediction
        trip    = fullPred.trip
        vehicle = fullPred.vehicle
        route   = fullPred.route

        # Start by determining wait time
        arrivalTime   = pred.attributes.arrival_time
        departureTime = pred.attributes.departure_time
        currentTime   = datetime.now(tz=timezone.utc)

        # Prefer arrival time
        if arrivalTime:
            waitTime = (datetime.fromisoformat(arrivalTime) - currentTime).total_seconds()
            if waitTime < 0: waitTime = 0  # Ensure non-negative wait
        elif departureTime:
            waitTime = (datetime.fromisoformat(departureTime) - currentTime).total_seconds()
            if waitTime < 0: waitTime = 0  # Ensure non-negative wait
        else:
            self.logger.warning(f"No arrival or departure time for prediction {pred.id} at stop {currentStop.stopName}. Using fallback wait time.")
            if self.debug: self.logger.debug(f"Full prediction data: {fullPred}")
            waitTime = 6000  # Arbitrary fallback far in future

        waitTime = int(waitTime)//60
        waitStr = f"{waitTime} minute(s)"

        # Arriving / Boarding current stop (added (waitTime < 30) since some busses were showing incorrectly)
        if vehicle and (vehicle.attributes.current_status in self.TRAIN_STATUS_MAP.keys()) and (waitTime < 30) \
                   and vehicle.relationships.stop and (currentStop.stopId == vehicle.relationships.stop.data.id):
            waitStr = self.TRAIN_STATUS_MAP[ vehicle.attributes.current_status ]

        # Determine head
        if trip and trip.attributes:
            head = trip.attributes.headsign
        else:
            head = "Unknown Destination"

        # End station edge case
        if head == currentStop.stopName:
            if self.debug: self.logger.debug(f"End of line detected for trip {trip.id} at stop {currentStop.stopName}. Adjusting head to other end.")
            otherDir = (trip.attributes.direction_id + 1) % 2
            head     = route.attributes.direction_destinations[ otherDir ]

        # Determine route
        if route:
            routeName = route.attributes.short_name if route.attributes.short_name else route.attributes.long_name
        else:
            routeName = "Unknown Route"

        # Determine status
        trainStatus = None

        # Current error status
        if pred.attributes.status:
            trainStatus = pred.attributes.status

        # Schedule change (https://github.com/google/transit/blob/master/gtfs-realtime/spec/en/reference.md#enum-schedulerelationship-1)
        if pred.attributes.schedule_relationship and (pred.attributes.schedule_relationship in ("CANCELLED", "DELETED")):
            trainStatus = pred.attributes.schedule_relationship.title()

        # Create output object
        return IndiviualPrediction(waitStr, head, routeName, trainStatus)


    """Processes platform data into an IndiviualTPlatform."""
    def processTPlatform(self, data: tuple[Optional[MBTA_API.CurrentStopInfo], list[MBTA_API.CollectedPrediction]]) -> Optional[IndiviualTPlatform]:
        currentStop, preds = data
        if not currentStop: return None

        return IndiviualTPlatform(
            Description = data[0].platName if currentStop.platName else currentStop.description,
            Predictions = sorted( [
                self.ProcessPrdiction(currentStop, pred) for pred in preds ],
                key=lambda p: self._to_minutes(p) ) )


    """Processes T line data into a FormattedTLine."""
    def processTData(self, lineName: str, data: tuple[Optional[tuple[MBTA_API.CurrentStopInfo, list[MBTA_API.CollectedPrediction]]],
                                                Optional[tuple[MBTA_API.CurrentStopInfo, list[MBTA_API.CollectedPrediction]]]]) -> FormattedTLine:
        tData1, tData2 = data
        if not tData1: tData1 = (None, [])
        if not tData2: tData2 = (None, [])

        lineColorSet = { dat.route.attributes.color for dat in tData1[1] + tData2[1]
                        if dat.route and dat.route.attributes and dat.route.attributes.color }
        if len(lineColorSet) > 1:
            self.logger.error(f"Multiple colors found for line {lineName}. Using first color.")
        lineColor = lineColorSet.pop() if lineColorSet else None

        return FormattedTLine(
            LineName  = lineName,
            Platform1 = self.processTPlatform(tData1),
            Platform2 = self.processTPlatform(tData2),
            LineColor = lineColor )


    """Processes bus data into a FormattedBusLine."""
    def processBusData(self, stop: MBTA_API.CurrentStopInfo, preds: List[MBTA_API.CollectedPrediction]) -> FormattedBusLine:
        return FormattedBusLine(
            BuswayName  = stop.platName if stop.platName else (stop.description if stop.description else stop.stopName),
            Predictions = sorted( [
                self.ProcessPrdiction(stop, pred) for pred in preds ],
                key=lambda p: self._to_minutes(p) ) )


    """Creates formatted station data from raw prediction data."""
    def createFormattedStationData(self, stationName: str, predictionData: dict[MBTA_API.CurrentStopInfo, list[MBTA_API.CollectedPrediction]]) -> FormattedStationData:
        TLines   = {}
        BusLines = []

        # Combine by lines color and check correct uniqueness
        for stop, preds in predictionData.items():


            # Process subway/light rail lines
            if stop.routeType in (MBTA_API.ROUTE_TYPE_LIGHTRAIL, MBTA_API.ROUTE_TYPE_SUBWAY):
                dirIds = {pred.prediction.attributes.direction_id for pred in preds}
                if len(dirIds) != 1:
                    self.logger.error(f"Multiple directions found for line at station {stationName}. Only one direction per line is supported.")
                    if self.debug:
                        self.logger.debug(f"Existing TLine: {TLines.get(stop.description)}")
                        self.logger.debug(f"New TLine attempt: {(stop, preds)}")
                    continue

                dirId = dirIds.pop()
                color = stop.stopColor

                if color not in TLines: TLines[color] = (None, None)
                tData1, tData2 = TLines[color]

                if dirId == 0:
                    if tData1 is not None:
                        self.logger.error(f"Multiple platform 1 entries found for line at station {stationName}. Only one platform 1 is supported.")
                        if self.debug:
                            self.logger.debug(f"Existing Platform 1: {tData1}")
                            self.logger.debug(f"New Platform 1 attempt: {(stop, preds)}")
                        continue
                    tData1 = (stop, preds)
                else:
                    if tData2 is not None:
                        self.logger.error(f"Multiple platform 2 entries found for line at station {stationName}. Only one platform 2 is supported.")
                        if self.debug:
                            self.logger.debug(f"Existing Platform 2: {tData2}")
                            self.logger.debug(f"New Platform 2 attempt: {(stop, preds)}")
                        continue
                    tData2 = (stop, preds)

                TLines[color] = (tData1, tData2)


            # Process bus lines
            if stop.routeType == MBTA_API.ROUTE_TYPE_BUS:
                if not preds: continue
                BusLines.append((stop, preds))


        return FormattedStationData(
            StationName = stationName,
            TLines   = [ self.processTData(color, tData) for color, tData in TLines.items() ],
            BusLines = sorted ( [ self.processBusData(BusLine[0], BusLine[1]) for BusLine in BusLines ] , key=lambda b: b.BuswayName ) )

    def _to_minutes(self, pred: IndiviualPrediction) -> int:
        """Convert prediction wait time to minutes for sorting."""
        special_cases = {
            "Boarding": -100,
            "Arriving": -99,
            "Next Stop": -98 }

        if pred.Wait in special_cases:
            return special_cases[pred.Wait]

        try:
            return int(pred.Wait.split()[0])
        except (ValueError, IndexError, AttributeError):
            return 999  # Put invalid entries at the end
