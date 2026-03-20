from flask import Flask, render_template
from mbta_api import MBTA_API
from StationFormater import StationDataFormater
from dataclasses import asdict
import logging

app = Flask(__name__)

app.logger.setLevel(logging.DEBUG)
handler   = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
handler.setFormatter(formatter)
app.logger.addHandler(handler)

api = MBTA_API(logger=app.logger)
api.DEBUG = False

StationFormater = StationDataFormater(api)

# Route to serve the main HTML page
@app.route('/station/<station_name>')
def station_monitor(station_name):
    # This renders the index.html file located in the 'templates' folder.
    # We pass the station_name so the HTML/JS knows which data to fetch.
    return render_template('station_data.html', station_name=station_name)


# API route to get station prediction data in JSON format
@app.route('/station_info/<station_name>')
def station_info(station_name):
    try:
        station_name = station_name.replace("_", " ")
        station_data = api.getStationPredictions(station_name)
        return asdict(StationFormater.createFormattedStationData(station_name, station_data)), 200
    except Exception as e:
        app.logger.exception(f"Failed to fetch predictions for station: {station_name}")
        return {"error": "Internal Server Error"}, 500


# API route to get station prediction raw data
@app.route('/station_info_raw/<station_name>')
def station_info_raw(station_name):
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
        app.logger.exception(f"Failed to fetch predictions for station: {station_name}")
        return {"error": "Internal Server Error"}, 500


# @app.route('/alerts')
# def alerts_view():
#     try: return {"alerts": formatAlerts(api)}, 200
#     except Exception as e:
#         app.logger.exception("Failed to fetch alerts")
#         return {"error": "Internal Server Error"}, 500


import argparse
def parse_args():
    parser = argparse.ArgumentParser()

    # --debug: optional bool flag, default False
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode (default: False)" )

    # validator/parser for route types
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

    args = parser.parse_args()

    return args


if __name__ == '__main__':
    args = parse_args()
    api.ROUTE_TYPE = args.route_type

    app.logger.info("Starting MBTA API server...")
    app.logger.info("Version: 0.5")
    app.logger.info("Args: " + str(args))

    if args.debug:
        api.DEBUG = True
        app.logger.setLevel(logging.DEBUG)
    else:
        app.logger.setLevel(logging.INFO)

    app.run(host="0.0.0.0", port="5000", debug=api.DEBUG)
