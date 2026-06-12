const API_KEY = "REPLACE_ME";
const N = 28;
const SCALE = 10;

const pad = document.getElementById("pad");
const view = pad.getContext("2d");
view.imageSmoothingEnabled = false;

const grid = document.createElement("canvas");
grid.width = N; grid.height = N;
const gctx = grid.getContext("2d");
gctx.lineWidth = 2.5;
gctx.lineCap = "round"; gctx.lineJoin = "round";
let drawing = false;

function render() {
    view.drawImage(grid, 0, 0, pad.width, pad.height);
}
function clearPad() {
    gctx.fillStyle = "#fff";
    gctx.fillRect(0, 0, N, N);
    render();
}
clearPad();

pad.onmousedown = e => {
    drawing = true; gctx.beginPath();
    gctx.moveTo(e.offsetX / SCALE, e.offsetY / SCALE);
};
pad.onmousemove = e => {
    if (!drawing) return;
    gctx.lineTo(e.offsetX / SCALE, e.offsetY / SCALE);
    gctx.stroke(); render();
};
pad.onmouseup = pad.onmouseleave = () => { drawing = false; };

function getPixels() {
    const data = gctx.getImageData(0, 0, N, N).data;
    const pixels = [];
    for (let y = 0; y < N; y++) {
        const row = [];
        for (let x = 0; x < N; x++)
            row.push(255 - data[(y * N + x) * 4]);
        pixels.push(row);
    }
    return pixels;
}

async function classify() {
    const res = await fetch("/api/classify", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-API-Key": API_KEY
        },
        body: JSON.stringify({ pixels: getPixels() })
    });
    if (!res.ok) {
        document.getElementById("result").textContent = "Error: " + res.status;
        return;
    }
    const data = await res.json();
    document.getElementById("result").textContent =
        "Prediction: " + data.prediction + " (" + (data.confidence * 100).toFixed(1) + "%)";
    loadHistory();
}

async function loadHistory() {
    const res = await fetch("/api/results");
    if (!res.ok) return;
    const data = await res.json();
    const ul = document.getElementById("history");
    ul.innerHTML = "";
    for (const r of data.results) {
        const li = document.createElement("li");
        li.textContent = r.prediction + " - " + new Date(r.created_at).toLocaleTimeString();
        ul.appendChild(li);
    }
}

document.getElementById("classify").onclick = classify;
document.getElementById("clear").onclick = () => {
    clearPad();
    document.getElementById("result").textContent = "";
};

loadHistory();
