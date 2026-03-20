from flask import Flask, render_template
from mbta_api import MBTA_API, formatStationPredictions
import logging

app = Flask(__name__)

app.logger.setLevel(logging.DEBUG)  # or INFO in production
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
handler.setFormatter(formatter)
app.logger.addHandler(handler)

@app.route('/station/<station_name>')
def station_view(station_name):
    station_name = station_name.replace("_", " ")
    return render_template("station.html", station=station_name)

    # try:
    #     station_name = station_name.replace("_", " ")
    #     final_data = formatStationPreds(stopName=station_name)

    #     return render_template("station.html", station=station_name, data=final_data)
    # except Exception as e:
    #     return f"<h2>Error: {str(e)}</h2>", 500


@app.route('/station_info/<station_name>')
def station_info(station_name):
    station_name = station_name.replace("_", " ")
    try:
        return formatStationPredictions(stopName=station_name, app=app)
    except Exception as e:
        app.logger.exception(f"Failed to fetch predictions for station: {station_name}")
        return {"error": "Internal Server Error"}, 500


if __name__ == '__main__':
    app.logger.info("Starting MBTA API server...")
    app.logger.info("Version: 0.2")

    api = MBTA_API()  # assumes API key is in environment variable
    with app.app_context():
        app.run(host="0.0.0.0", port="5000", debug=True)
