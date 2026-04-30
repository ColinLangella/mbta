from dotenv import load_dotenv
load_dotenv()

import argparse, os, json, time
from flask import Flask, render_template, Response, stream_with_context
from mbta_api import MBTA_API
from station_formatter import StationDataFormatter
from alert_formatter import AlertDataFormatter
from multi_stop_formatter import MultiStopFormatter
from dataclasses import asdict
import logging

app = Flask(__name__)

app.logger.handlers.clear()
app.logger.setLevel(logging.DEBUG)
handler   = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
handler.setFormatter(formatter)
app.logger.addHandler(handler)

try:
    MBTA_LAT = float(os.environ["MBTA_LAT"])
    MBTA_LON = float(os.environ["MBTA_LON"])
except KeyError as e:
    raise ValueError(f"Required environment variable not set: {e}. Add MBTA_LAT and MBTA_LON to your .env file.")

api = MBTA_API(logger=app.logger)

StationFormatter = StationDataFormatter(logger=app.logger)
AlertFormatter   = AlertDataFormatter()

def get_allowed_route_types() -> list[int]:
    return [int(x.strip()) for x in api.ROUTE_TYPE.split(",")]

@app.route('/api/nearby_stations')
def nearby_stations_data():
    try:
        allowed_types = get_allowed_route_types()
        raw_stops = api.getStopsNearLocation(lat=MBTA_LAT, lon=MBTA_LON)
        formatted_stations = MultiStopFormatter.format_nearby_stations(raw_stops=raw_stops, allowed_types=allowed_types)
        return {
            "stations": [asdict(s) for s in formatted_stations],
            "allowed_route_types": allowed_types
        }, 200
    except Exception as e:
        app.logger.exception("Failed to fetch nearby stations")
        return {"error": str(e)}, 500

@app.route('/')
@app.route('/stations')
def stations_page():
    return render_template('nearby_stations.html')

@app.route('/station/<path:station_name>')
def station_monitor(station_name):
    return render_template('stations.html', station_name=station_name)

def _build_route_color_map(alert_dicts: list[dict]) -> dict:
    route_ids = {r for a in alert_dicts for r in (a.get("affected_routes") or [])}
    if not route_ids:
        return {}
    routes = api.getRoutesByIds(",".join(sorted(route_ids)))
    return {r.id: "#" + r.attributes.color for r in routes if r.attributes and r.attributes.color}

def _attach_route_colors(alert_dicts: list[dict]) -> None:
    color_map = _build_route_color_map(alert_dicts)
    for alert in alert_dicts:
        alert["route_colors"] = {r: color_map[r] for r in (alert.get("affected_routes") or []) if r in color_map}

def _get_station_alerts(raw_predictions: dict) -> list[dict]:
    station_route_ids = set()
    for preds in raw_predictions.values():
        for cp in preds:
            if cp.route and cp.route.id:
                station_route_ids.add(cp.route.id)

    station_route_types = {stop.routeType for stop in raw_predictions.keys()}

    raw_alerts = api.getSubwayAlerts()
    formatted  = AlertFormatter.format_alerts(raw_alerts)

    result = []
    for alert in formatted:
        routes = alert.affected_routes or []
        if routes:
            if station_route_ids & set(routes):
                result.append(asdict(alert))
        else:
            if alert.min_route_type in station_route_types:
                result.append(asdict(alert))
    _attach_route_colors(result)
    return result

@app.route('/api/station/<path:station_name>')
def station_info(station_name):
    try:
        station_name    = station_name.replace("_", " ")
        raw_predictions = api.getStationPredictions(station_name)
        formatted       = asdict(StationFormatter.createFormattedStationData(station_name, raw_predictions))
        formatted['station_alerts'] = _get_station_alerts(raw_predictions)
        return formatted, 200
    except Exception as e:
        app.logger.exception(f"Failed to fetch predictions for station: {station_name}")
        return {"error": "Internal Server Error"}, 500


@app.route('/api/station/<path:station_name>/stream')
def station_stream(station_name):
    station_name = station_name.replace("_", " ")
    def generate():
        while True:
            try:
                raw_predictions = api.getStationPredictions(station_name)
                formatted       = asdict(StationFormatter.createFormattedStationData(station_name, raw_predictions))
                formatted['station_alerts'] = _get_station_alerts(raw_predictions)
                yield f"data: {json.dumps(formatted)}\n\n"
            except Exception:
                app.logger.exception(f"SSE error for station: {station_name}")
                yield f"event: error\ndata: {json.dumps({'error': 'Failed to fetch data'})}\n\n"
            time.sleep(api.PREDICTIONS_CACHE_TTL)
    return Response(
        stream_with_context(generate()),
        mimetype  = 'text/event-stream',
        headers   = {'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'} )


@app.route('/api/station_info_raw/<path:station_name>')
def station_info_raw(station_name):
    if not api.DEBUG:
        return {"error": "Not Found"}, 404

    def collectedPrediction_to_dict(pred: MBTA_API.CollectedPrediction) -> dict:
        return {
            "prediction": pred.prediction.to_dict(),
            "trip":       pred.trip.to_dict()    if pred.trip    else None,
            "vehicle":    pred.vehicle.to_dict() if pred.vehicle else None,
            "stop":       pred.stop.to_dict()    if pred.stop    else None,
            "route":      pred.route.to_dict()   if pred.route   else None }

    try:
        station_name = station_name.replace("_", " ")
        station_data = api.getStationPredictions(station_name)
        return [ [asdict(k), [collectedPrediction_to_dict(i) for i in v] ] for k, v in station_data.items() ], 200
    except Exception as e:
        app.logger.exception(f"Failed to fetch raw predictions for station: {station_name}")
        return {"error": "Internal Server Error"}, 500


@app.route('/health')
def health():
    return {"status": "ok", "version": "v_0.9", "route_types": get_allowed_route_types()}, 200


@app.route('/alerts')
def alerts_page():
    return render_template('alerts.html')

@app.route('/api/alerts')
def alerts_data():
    try:
        raw_alerts = api.getSubwayAlerts()
        formatted_alerts = [asdict(a) for a in AlertFormatter.format_alerts(raw_alerts)]
        _attach_route_colors(formatted_alerts)
        allowed_types = get_allowed_route_types()
        return {
            "alerts": formatted_alerts,
            "allowed_route_types": allowed_types
        }, 200
    except Exception as e:
        app.logger.exception("Failed to fetch alerts")
        return {"error": "Internal Server Error"}, 500


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode (default: False)" )

    def validate_route_types(value):
        items = value.split(",")
        for item in items:
            try:
                i = int(item)
            except ValueError:
                raise argparse.ArgumentTypeError(f"'{item}' is not an integer")
            if not (0 <= i <= 7):
                raise argparse.ArgumentTypeError("route types must be between 0 and 7")
        return value

    parser.add_argument(
        "--route_type",
        type=validate_route_types,
        default="0,1,3",
        help="Comma-separated list of route types (0-7)" )

    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    api.ROUTE_TYPE = args.route_type

    app.logger.info("Starting MBTA API server...")
    app.logger.info("Version: v_0.9")
    app.logger.info("Args: " + str(args))

    if args.debug:
        api.DEBUG = True
        StationFormatter.debug = True
        app.logger.setLevel(logging.DEBUG)
    else:
        app.logger.setLevel(logging.INFO)

    app.run(host="0.0.0.0", port="5000", debug=api.DEBUG)
