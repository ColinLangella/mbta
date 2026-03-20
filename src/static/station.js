document.addEventListener("DOMContentLoaded", function () {
    const stationName = document.getElementById("station-name").textContent;
    const contentDiv = document.getElementById("content");

    const lineColors = {
        "Red": "#D13438",
        "Green": "#007A33",
        "Orange": "#F57921",
        "Blue": "#003DA5",
        "Silver": "#A7A9AC",
        "Mattapan": "#A51C30",
        // fallback
        "Default": "#cccccc"
    };

    function getLineColor(lineName) {
        const match = Object.keys(lineColors).find(color =>
            lineName.toLowerCase().includes(color.toLowerCase())
        );
        return lineColors[match] || lineColors["Default"];
    }


    function createTable(line, end1, end2, preds1, preds2) {
        function formatCellContent(pred) {
            const div = document.createElement("div");

            const destination = document.createElement("span");
            destination.textContent = pred["End Station"];

            const timeBadge = document.createElement("span");
            timeBadge.className = "time-badge";
            timeBadge.textContent = pred["Wait"];

            div.appendChild(destination);
            div.appendChild(timeBadge);

            // Add red status badge if present
            if (pred["Status"]) {
                const statusBadge = document.createElement("span");
                statusBadge.className = "status-badge";
                statusBadge.textContent = pred["Status"];
                div.appendChild(statusBadge);
            }

            return div;
        }

        const table = document.createElement("table");

        const caption = document.createElement("caption");
        const color = getLineColor(line);
        caption.innerHTML = `<h2 style="color: ${color};">${line}</h2>`;
        table.appendChild(caption);

        const header = document.createElement("tr");
        header.innerHTML = `<th>To ${end1}</th><th>To ${end2}</th>`;
        table.appendChild(header);

        const rows = Math.max(preds1.length, preds2.length);
        for (let i = 0; i < rows; i++) {
            const row = document.createElement("tr");

            const cell1 = document.createElement("td");
            if (i < preds1.length) {
                cell1.appendChild(formatCellContent(preds1[i]));
            }

            const cell2 = document.createElement("td");
            if (i < preds2.length) {
                cell2.appendChild(formatCellContent(preds2[i]));
            }

            row.appendChild(cell1);
            row.appendChild(cell2);
            table.appendChild(row);
        }

        return table;
    }

    let lastGoodData = null;

    async function fetchAndRender() {
        try {
            const response = await fetch(`/station_info/${encodeURIComponent(stationName)}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();

            // Save last good data
            lastGoodData = data;

            // Render fresh content
            contentDiv.innerHTML = "";
            for (const [line, info] of Object.entries(data)) {
                const table = createTable(line, info["End 1"], info["End 2"], info["Direction 1"], info["Direction 2"]);
                contentDiv.appendChild(table);
            }
        } catch (err) {
            console.error("Error fetching data:", err);
            if (lastGoodData) {
                console.warn("Using previously cached data.");
                contentDiv.innerHTML = "";
                for (const [line, info] of Object.entries(lastGoodData)) {
                    const table = createTable(line, info["End 1"], info["End 2"], info["Direction 1"], info["Direction 2"]);
                    contentDiv.appendChild(table);
                }
            }
            // If no cached data, do nothing and keep current content
        }
    }

    fetchAndRender();
    setInterval(fetchAndRender, 10000);
});
