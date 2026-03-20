from flask import Flask, render_template
from mbta_api import MBTA_API
from app_formater import formatStationPredictions
import logging

app = Flask(__name__)

app.logger.setLevel(logging.DEBUG)  # or INFO in production
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
handler.setFormatter(formatter)
app.logger.addHandler(handler)

api = MBTA_API(logger=app.logger)  # assumes API key is in environment variable
api.DEBUG = False 

@app.route('/station/<station_name>')
def station_view(station_name):
    station_name = station_name.replace("_", " ")
    try:
        final_data = formatStationPredictions(api, stopName=station_name)
        return render_template("station.html", station=station_name, data=final_data)
    except Exception as e:
        app.logger.exception(f"Failed to load station data for: {station_name}")
        return f"<h2>Error: {str(e)}</h2>", 500


@app.route('/station_info/<station_name>')
def station_info(station_name):
    station_name = station_name.replace("_", " ")
    try: return formatStationPredictions(api, stopName=station_name)
    except Exception as e:
        app.logger.exception(f"Failed to fetch predictions for station: {station_name}")
        return {"error": "Internal Server Error"}, 500


if __name__ == '__main__':
    app.logger.info("Starting MBTA API server...")
    app.logger.info("Version: 0.3")

    app.run(host="0.0.0.0", port="5000", debug=api.DEBUG)
