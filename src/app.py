from flask import Flask, render_template
from mbta_api import MBTA_API
from app_formater import formatStationPredictions
import logging

app = Flask(__name__)

app.logger.setLevel(logging.DEBUG)
handler   = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
handler.setFormatter(formatter)
app.logger.addHandler(handler)

api = MBTA_API(logger=app.logger)
api.DEBUG = False


# Route to render the main page with station predictions
@app.route('/station/<station_name>')
def station_view(station_name):
    station_name = station_name.replace("_", " ")
    try: return render_template("station.html", station=station_name), 200
    except Exception as e:
        app.logger.exception(f"Failed to load station data for: {station_name}")
        return f"<h2>Error: {str(e)}</h2>", 500


# Route to fetch station predictions as JSON
@app.route('/station_info/<station_name>')
def station_info(station_name):
    station_name = station_name.replace("_", " ")
    try: return formatStationPredictions(api, stopName=station_name), 200
    except Exception as e:
        app.logger.exception(f"Failed to fetch predictions for station: {station_name}")
        return {"error": "Internal Server Error"}, 500


if __name__ == '__main__':
    from sys import argv
    api.ROUTE_TYPE = "0,1,3"  # Focus on subway routes
    if len(argv) > 1 and argv[1] == "debug":
        api.DEBUG = True
        app.logger.setLevel(logging.DEBUG)
    else:
        app.logger.setLevel(logging.INFO)

    app.logger.info("Starting MBTA API server...")
    app.logger.info("Version: 0.4")
    app.logger.info("Args: " + str(argv))

    app.run(host="0.0.0.0", port="5000", debug=api.DEBUG)
