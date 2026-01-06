from flask import Flask, request, render_template_string, redirect, url_for, session, jsonify
from datetime import datetime, timezone, timedelta
import sqlite3
from pathlib import Path
from functools import wraps
import logging
from logging.handlers import TimedRotatingFileHandler
import sys
import atexit
import os

app = Flask(__name__)
app.secret_key = "xdrip_secret_key_2026"  # Chiave per le sessioni

DB_PATH = Path("/home/matteo/xdrip/xdrip.db")
SECRET = "Vale2011"  # la tua secret nel path: /xdrip/Vale2011/...
DASHBOARD_PASSWORD = "Vale2011!"  # Password per accedere alla dashboard

# Configurazione path dei log (modificabile)
LOGS_DIR = Path("/home/matteo/xdrip/logs")  # Path della cartella logs

# Titolo personalizzabile per la dashboard
DASHBOARD_TITLE = "Glice Vale"

# Limiti target glicemia per il grafico
TARGET_MIN = 70  # Limite minimo target (mg/dL)
TARGET_MAX = 180  # Limite massimo target (mg/dL)


def setup_logging():
    """Configura il sistema di logging con rotazione 24h"""
    # Crea la directory dei log se non esiste
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Configurazione del logger principale
    logger = logging.getLogger('xdrip')
    logger.setLevel(logging.INFO)
    
    # Rimuovi handler esistenti per evitare duplicati
    logger.handlers.clear()
    
    # Handler per file con rotazione ogni 24 ore (mezzanotte)
    log_file = LOGS_DIR / "xdrip.log"
    file_handler = TimedRotatingFileHandler(
        log_file,
        when='midnight',
        interval=1,
        backupCount=30,  # Mantieni 30 giorni di log
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    
    # Formato del log
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    
    # Handler per console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    # Aggiungi gli handler al logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


# Inizializza il logger
logger = setup_logging()


def log_shutdown():
    """Funzione chiamata alla chiusura dell'applicazione"""
    logger.info("========== Applicazione xDrip in chiusura ==========")
    logging.shutdown()


# Registra la funzione di shutdown
atexit.register(log_shutdown)


def daemonize():
    """Trasforma il processo in un daemon (solo Linux/Unix)"""
    try:
        # Primo fork
        pid = os.fork()
        if pid > 0:
            # Esce dal processo padre
            sys.exit(0)
    except OSError as e:
        logger.error(f"Errore nel primo fork: {e}")
        sys.exit(1)
    
    # Dissocia dal terminale padre
    os.chdir('/')
    os.setsid()
    os.umask(0)
    
    # Secondo fork
    try:
        pid = os.fork()
        if pid > 0:
            # Esce dal secondo processo padre
            sys.exit(0)
    except OSError as e:
        logger.error(f"Errore nel secondo fork: {e}")
        sys.exit(1)
    
    # Reindirizza stdin, stdout, stderr
    sys.stdout.flush()
    sys.stderr.flush()
    
    # Salva il PID
    pid_file = LOGS_DIR / "xdrip.pid"
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    logger.info(f"Daemon avviato con PID: {os.getpid()}")


def init_db():
    try:
        logger.info("Inizializzazione database...")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device TEXT,
                date_ms INTEGER,
                timestamp_utc TEXT,
                sgv INTEGER,
                direction TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_timestamp ON entries(timestamp_utc);"
        )
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_date_ms_unique ON entries(date_ms);"
        )
        
        # Tabella per device status
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS devicestatus (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device TEXT,
                battery INTEGER,
                uploader_type TEXT,
                timestamp_utc TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_devicestatus_timestamp ON devicestatus(timestamp_utc);"
        )
        
        conn.commit()
        conn.close()
        logger.info("Database inizializzato con successo")
    except Exception as e:
        logger.error(f"Errore durante l'inizializzazione del database: {e}", exc_info=True)
        raise


def check_secret_in_path(secret_from_path: str) -> bool:
    return secret_from_path == SECRET


def login_required(f):
    """Decorator per proteggere le route con login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def get_last_hours_data(hours=4):
    """Recupera i dati delle ultime N ore dal database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Calcola il timestamp di N ore fa
    hours_ago = datetime.now(timezone.utc) - timedelta(hours=hours)
    hours_ago_iso = hours_ago.isoformat()
    
    c.execute(
        """
        SELECT timestamp_utc, sgv, direction
        FROM entries
        WHERE timestamp_utc >= ?
        ORDER BY timestamp_utc ASC
        """,
        (hours_ago_iso,)
    )
    
    rows = c.fetchall()
    conn.close()
    
    return rows


def save_devicestatus_to_db(data: dict):
    """Salva device status nel DB SQLite."""
    device = data.get("device")
    uploader = data.get("uploader", {})
    battery = uploader.get("battery")
    uploader_type = uploader.get("type")
    
    # Usa il timestamp corrente
    timestamp_utc = datetime.now(timezone.utc).isoformat()
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute(
            """
            INSERT INTO devicestatus (device, battery, uploader_type, timestamp_utc)
            VALUES (?, ?, ?, ?)
            """,
            (device, battery, uploader_type, timestamp_utc),
        )
        conn.commit()
        logger.info(f"Device status salvato: device={device}, battery={battery}%, type={uploader_type}")
    except Exception as e:
        logger.error(f"Errore nel salvare devicestatus: {e}", exc_info=True)
    finally:
        conn.close()


def save_entry_to_db(entry: dict):
    """Salva UNA entry xDrip nel DB SQLite."""
    device = entry.get("device")
    date_ms = entry.get("date")
    sgv = entry.get("sgv")
    direction = entry.get("direction")

    # Converte ms epoch in datetime UTC ISO8601
    timestamp_utc = None
    if date_ms is not None:
        ts_sec = int(date_ms) / 1000.0
        dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
        timestamp_utc = dt.isoformat()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute(
            """
            INSERT INTO entries (device, date_ms, timestamp_utc, sgv, direction)
            VALUES (?, ?, ?, ?, ?)
            """,
            (device, date_ms, timestamp_utc, sgv, direction),
        )
        conn.commit()
        logger.info(f"Entry salvata: sgv={sgv}, direction={direction}, timestamp={timestamp_utc}")
    except sqlite3.IntegrityError:
        # Ignora duplicati (stesso date_ms già presente)
        logger.debug(f"Entry duplicata ignorata: date_ms={date_ms}")
    except Exception as e:
        logger.error(f"Errore nel salvare entry: {e}", exc_info=True)
    finally:
        conn.close()


# --- GLICEMIE / ENTRIES ---

@app.route("/xdrip/<secret>/entries", methods=["POST"])
def receive_xdrip_entries(secret):
    if not check_secret_in_path(secret):
        logger.warning(f"Tentativo di accesso non autorizzato alla route entries")
        return "Unauthorized", 401

    try:
        data = request.get_json(force=True)

        # xDrip può mandare singolo oggetto o array; normalizziamo a lista
        if isinstance(data, dict):
            entries = [data]
        else:
            entries = data

        logger.info(f"Ricevute {len(entries)} entries da xDrip")
        for entry in entries:
            save_entry_to_db(entry)

        return "OK", 200
    except Exception as e:
        logger.error(f"Errore nella gestione delle ENTRIES: {e}", exc_info=True)
        return "Bad Request", 400


# --- DEVICE STATUS (IGNORATO, MA PROTETTO) ---

@app.route("/xdrip/<secret>/devicestatus", methods=["POST"])
def receive_xdrip_devicestatus(secret):
    if not check_secret_in_path(secret):
        logger.warning(f"Tentativo di accesso non autorizzato alla route devicestatus")
        return "Unauthorized", 401

    try:
        data = request.get_json(force=True)
        logger.info(f"Ricevuto DEVICESTATUS da xDrip")
        save_devicestatus_to_db(data)
    except Exception as e:
        logger.error(f"Errore nel parsing DEVICESTATUS: {e}", exc_info=True)
    
    return "OK", 200


# --- INDEX / TEST ---

@app.route("/", methods=["GET"])
def index():
    return "xDrip Flask receiver attivo"


# --- DASHBOARD LOGIN ---

@app.route("/dashboard/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password")
        if password == DASHBOARD_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            return render_template_string(LOGIN_TEMPLATE, error="Password errata", title=DASHBOARD_TITLE)
    
    return render_template_string(LOGIN_TEMPLATE, error=None, title=DASHBOARD_TITLE)


@app.route("/dashboard/logout")
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template_string(DASHBOARD_TEMPLATE, 
                                  target_min=TARGET_MIN, 
                                  target_max=TARGET_MAX,
                                  title=DASHBOARD_TITLE)


@app.route("/dashboard/api/data")
@login_required
def get_data():
    """API endpoint per ottenere i dati del grafico"""
    hours = request.args.get('hours', default=4, type=int)
    # Valida che hours sia uno dei valori consentiti
    if hours not in [4, 8, 12, 18, 24, 48]:
        hours = 4
    
    rows = get_last_hours_data(hours)
    
    data = []
    for row in rows:
        timestamp_utc, sgv, direction = row
        data.append({
            'timestamp': timestamp_utc,
            'value': sgv,
            'direction': direction
        })
    
    return jsonify(data)


@app.route("/dashboard/api/current")
@login_required
def get_current():
    """API endpoint per ottenere il valore corrente e il delta"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Prendi gli ultimi 2 valori
    c.execute(
        """
        SELECT timestamp_utc, sgv, direction
        FROM entries
        ORDER BY timestamp_utc DESC
        LIMIT 2
        """
    )
    
    rows = c.fetchall()
    conn.close()
    
    if len(rows) == 0:
        return jsonify({'error': 'No data'}), 404
    
    current = rows[0]
    delta = 0
    if len(rows) > 1:
        previous = rows[1]
        delta = current[1] - previous[1]  # sgv corrente - sgv precedente
    
    return jsonify({
        'timestamp': current[0],
        'value': current[1],
        'direction': current[2],
        'delta': delta,
        'in_range': TARGET_MIN <= current[1] <= TARGET_MAX
    })


@app.route("/dashboard/api/battery")
@login_required
def get_battery():
    """API endpoint per ottenere il livello di batteria"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute(
        """
        SELECT battery, uploader_type, timestamp_utc
        FROM devicestatus
        ORDER BY timestamp_utc DESC
        LIMIT 1
        """
    )
    
    row = c.fetchone()
    conn.close()
    
    if row is None:
        return jsonify({'battery': None})
    
    return jsonify({
        'battery': row[0],
        'type': row[1],
        'timestamp': row[2]
    })


@app.route("/dashboard/display")
@login_required
def display():
    """Pagina display grande per visualizzazione glicemia"""
    return render_template_string(DISPLAY_TEMPLATE,
                                  target_min=TARGET_MIN,
                                  title=DASHBOARD_TITLE,
                                  target_max=TARGET_MAX)


# --- TEMPLATES ---

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ title }} - Login</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
        .login-container {
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            width: 300px;
        }
        h2 {
            text-align: center;
            color: #333;
            margin-bottom: 30px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            color: #555;
            font-weight: bold;
        }
        input[type="password"] {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 14px;
            box-sizing: border-box;
        }
        button {
            width: 100%;
            padding: 12px;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
            transition: background 0.3s;
        }
        button:hover {
            background: #5568d3;
        }
        .error {
            color: #e74c3c;
            text-align: center;
            margin-bottom: 15px;
            font-size: 14px;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h2>{{ title }}</h2>
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        <form method="POST">
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required autofocus>
            </div>
            <button type="submit">Accedi</button>
        </form>
    </div>
</body>
</html>
"""

DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ title }}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/date-fns@3.0.0/cdn.min.js"></script>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }
        .header-left {
            display: flex;
            align-items: center;
            gap: 20px;
        }
        h1 {
            margin: 0;
            color: #333;
        }
        .time-selector {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .time-selector label {
            color: #555;
            font-weight: bold;
        }
        .time-selector select {
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 14px;
            cursor: pointer;
            background: white;
        }
        .display-btn {
            padding: 10px 20px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 5px;
            transition: background 0.3s;
        }
        .display-btn:hover {
            background: #5568d3;
        }
        .logout-btn {
            padding: 10px 20px;
            background: #e74c3c;
            color: white;
            text-decoration: none;
            border-radius: 5px;
            transition: background 0.3s;
        }
        .logout-btn:hover {
            background: #c0392b;
        }
        .chart-container {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
            position: relative;
            height: 500px;
        }
        .stats {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
        }
        .stat-card {
            flex: 1;
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
            text-align: center;
        }
        .stat-value {
            font-size: 32px;
            font-weight: bold;
            color: #667eea;
            margin: 10px 0;
        }
        .stat-label {
            color: #777;
            font-size: 14px;
        }
        .loading {
            text-align: center;
            padding: 50px;
            color: #999;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-left">
            <h1>{{ title }}</h1>
            <div class="time-selector">
                <label for="timeRange">Periodo:</label>
                <select id="timeRange" onchange="changeTimeRange()">
                    <option value="4" selected>Ultime 4 ore</option>
                    <option value="8">Ultime 8 ore</option>
                    <option value="12">Ultime 12 ore</option>
                    <option value="18">Ultime 18 ore</option>
                    <option value="24">Ultime 24 ore</option>
                    <option value="48">Ultime 48 ore</option>
                </select>
            </div>
        </div>
        <div style="display: flex; gap: 10px;">
            <a href="/dashboard/display" class="display-btn">Display Grande</a>
            <a href="/dashboard/logout" class="logout-btn">Logout</a>
        </div>
    </div>
    
    <div class="stats">
        <div class="stat-card">
            <div class="stat-label">Valore Attuale</div>
            <div class="stat-value" id="current-value">--</div>
            <div class="stat-label" id="current-direction">--</div>
        </div>
        <div class="stat-card">
            <div class="stat-label" id="avg-label">Media 4 Ore</div>
            <div class="stat-value" id="avg-value">--</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Batteria</div>
            <div class="stat-value" id="battery-value">--</div>
            <div class="stat-label" id="battery-type">--</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Ultimo Aggiornamento</div>
            <div class="stat-value" style="font-size: 18px;" id="last-update">--</div>
        </div>
    </div>
    
    <div class="chart-container">
        <canvas id="glucoseChart"></canvas>
    </div>
    
    <script>
        const TARGET_MIN = {{ target_min }};
        const TARGET_MAX = {{ target_max }};
        let chart = null;
        let currentHours = 4;
        
        function changeTimeRange() {
            currentHours = parseInt(document.getElementById('timeRange').value);
            // Aggiorna anche la label della media
            document.getElementById('avg-label').textContent = `Media ${currentHours} Ore`;
            loadData();
        }
        
        // Plugin personalizzato per disegnare la fascia target verde
        const targetRangePlugin = {
            id: 'targetRange',
            beforeDatasetsDraw(chart) {
                const { ctx, chartArea: { top, bottom, left, right }, scales: { y } } = chart;
                
                // Calcola le posizioni Y per i limiti target
                const yMin = y.getPixelForValue(TARGET_MIN);
                const yMax = y.getPixelForValue(TARGET_MAX);
                
                // Disegna il rettangolo verde chiaro
                ctx.save();
                ctx.fillStyle = 'rgba(16, 185, 129, 0.1)';
                ctx.fillRect(left, yMax, right - left, yMin - yMax);
                
                // Disegna le linee di bordo della fascia target
                ctx.strokeStyle = 'rgba(16, 185, 129, 0.4)';
                ctx.lineWidth = 1;
                ctx.setLineDash([5, 5]);
                
                // Linea superiore (TARGET_MAX)
                ctx.beginPath();
                ctx.moveTo(left, yMax);
                ctx.lineTo(right, yMax);
                ctx.stroke();
                
                // Linea inferiore (TARGET_MIN)
                ctx.beginPath();
                ctx.moveTo(left, yMin);
                ctx.lineTo(right, yMin);
                ctx.stroke();
                
                ctx.restore();
            }
        };
        
        function formatTime(isoString) {
            const date = new Date(isoString);
            return date.toLocaleTimeString('it-IT', { 
                hour: '2-digit', 
                minute: '2-digit',
                day: '2-digit',
                month: '2-digit'
            });
        }
        
        function getTimeAgo(isoString) {
            const date = new Date(isoString);
            const now = new Date();
            const diffMs = now - date;
            const diffMins = Math.floor(diffMs / 60000);
            
            if (diffMins < 1) {
                return 'ora';
            } else if (diffMins === 1) {
                return '1 min fa';
            } else if (diffMins < 60) {
                return `${diffMins} min fa`;
            } else {
                const diffHours = Math.floor(diffMins / 60);
                if (diffHours === 1) {
                    return '1 ora fa';
                } else {
                    return `${diffHours} ore fa`;
                }
            }
        }
        
        function getDirectionArrow(direction) {
            const arrows = {
                'DoubleUp': '⇈',
                'SingleUp': '↑',
                'FortyFiveUp': '↗',
                'Flat': '→',
                'FortyFiveDown': '↘',
                'SingleDown': '↓',
                'DoubleDown': '⇊'
            };
            return arrows[direction] || direction;
        }
        
        async function loadBattery() {
            try {
                const response = await fetch('/dashboard/api/battery');
                const data = await response.json();
                
                if (data.battery !== null) {
                    document.getElementById('battery-value').textContent = data.battery + '%';
                    document.getElementById('battery-type').textContent = data.type || '';
                } else {
                    document.getElementById('battery-value').textContent = '--';
                    document.getElementById('battery-type').textContent = 'N/D';
                }
            } catch (error) {
                console.error('Errore nel caricamento batteria:', error);
            }
        }
        
        async function loadData() {
            try {
                const response = await fetch(`/dashboard/api/data?hours=${currentHours}`);
                const data = await response.json();
                
                if (data.length === 0) {
                    document.querySelector('.chart-container').innerHTML = 
                        `<div class="loading">Nessun dato disponibile nelle ultime ${currentHours} ore</div>`;
                    return;
                }
                
                // Aggiorna statistiche
                const lastReading = data[data.length - 1];
                
                // Calcola delta se ci sono almeno 2 valori
                let delta = 0;
                if (data.length >= 2) {
                    const previousReading = data[data.length - 2];
                    delta = lastReading.value - previousReading.value;
                }
                const deltaDisplay = delta >= 0 ? `+${delta}` : `${delta}`;
                
                // Aggiorna il titolo del browser
                const arrow = getDirectionArrow(lastReading.direction);
                document.title = `${lastReading.value} ${arrow} (${deltaDisplay}) - {{ title }}`;
                
                // Aggiorna statistiche cards
                document.getElementById('current-value').textContent = `${lastReading.value} (${deltaDisplay})`;
                document.getElementById('current-direction').textContent = arrow;
                
                const avgValue = Math.round(
                    data.reduce((sum, d) => sum + d.value, 0) / data.length
                );
                document.getElementById('avg-value').textContent = avgValue;
                
                const formattedTime = formatTime(lastReading.timestamp);
                const timeAgo = getTimeAgo(lastReading.timestamp);
                document.getElementById('last-update').innerHTML = 
                    `${formattedTime}<br><small style="font-size: 14px; opacity: 0.7;">${timeAgo}</small>`;
                
                // Prepara dati per il grafico
                const labels = data.map(d => formatTime(d.timestamp));
                const values = data.map(d => d.value);
                
                // Colora i punti in base al target range
                const pointColors = values.map(v => 
                    (v >= TARGET_MIN && v <= TARGET_MAX) ? '#10b981' : '#f97316'
                );
                
                // Crea o aggiorna il grafico
                const ctx = document.getElementById('glucoseChart').getContext('2d');
                
                if (chart) {
                    chart.destroy();
                }
                
                chart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Glicemia (mg/dL)',
                            data: values,
                            borderColor: '#667eea',
                            backgroundColor: 'rgba(102, 126, 234, 0.1)',
                            borderWidth: 3,
                            fill: false,
                            tension: 0.4,
                            pointRadius: 6,
                            pointHoverRadius: 8,
                            pointBackgroundColor: pointColors,
                            pointBorderColor: '#fff',
                            pointBorderWidth: 2
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                display: true,
                                position: 'top'
                            },
                            tooltip: {
                                mode: 'index',
                                intersect: false,
                                callbacks: {
                                    afterLabel: function(context) {
                                        const index = context.dataIndex;
                                        return getDirectionArrow(data[index].direction);
                                    }
                                }
                            }
                        },
                        scales: {
                            y: {
                                beginAtZero: false,
                                title: {
                                    display: true,
                                    text: 'Glicemia (mg/dL)'
                                },
                                grid: {
                                    color: function(context) {
                                        // Evidenzia la fascia target con sfondo verde chiaro
                                        const value = context.tick.value;
                                        if (value >= TARGET_MIN && value <= TARGET_MAX) {
                                            return 'rgba(16, 185, 129, 0.1)';
                                        }
                                        return 'rgba(0, 0, 0, 0.05)';
                                    }
                                },
                                afterBuildTicks: function(axis) {
                                    // Assicura che i limiti target siano visibili come tick
                                    const ticks = axis.ticks;
                                    if (!ticks.find(t => t.value === TARGET_MIN)) {
                                        ticks.push({ value: TARGET_MIN });
                                    }
                                    if (!ticks.find(t => t.value === TARGET_MAX)) {
                                        ticks.push({ value: TARGET_MAX });
                                    }
                                    ticks.sort((a, b) => a.value - b.value);
                                }
                            },
                            x: {
                                title: {
                                    display: true,
                                    text: 'Orario'
                                },
                                grid: {
                                    color: 'rgba(0, 0, 0, 0.05)'
                                }
                            }
                        },
                        interaction: {
                            mode: 'nearest',
                            axis: 'x',
                            intersect: false
                        }
                    },
                    plugins: [targetRangePlugin]
                });
                
            } catch (error) {
                console.error('Errore nel caricamento dei dati:', error);
                document.querySelector('.chart-container').innerHTML = 
                    '<div class="loading">Errore nel caricamento dei dati</div>';
            }
        }
        
        // Carica i dati iniziali
        loadData();
        loadBattery();
        
        // Aggiorna ogni 60 secondi
        setInterval(() => {
            loadData();
            loadBattery();
        }, 60000);
    </script>
</body>
</html>
"""

DISPLAY_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ title }} - Display</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            transition: background 0.5s;
        }
        body.in-range {
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
        }
        body.out-of-range {
            background: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
        }
        .container {
            text-align: center;
            color: white;
            padding: 40px;
        }
        .glucose-value {
            font-size: 180px;
            font-weight: bold;
            line-height: 1;
            text-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
            margin-bottom: 20px;
        }
        .trend-info {
            font-size: 120px;
            margin-bottom: 40px;
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 30px;
        }
        .arrow {
            font-size: 140px;
        }
        .delta {
            font-size: 100px;
            font-weight: bold;
        }
        .timestamp {
            font-size: 32px;
            opacity: 0.9;
            margin-top: 20px;
        }
        .back-btn {
            position: fixed;
            top: 20px;
            left: 20px;
            padding: 15px 30px;
            background: rgba(255, 255, 255, 0.2);
            color: white;
            text-decoration: none;
            border-radius: 8px;
            font-size: 18px;
            transition: background 0.3s;
            backdrop-filter: blur(10px);
        }
        .back-btn:hover {
            background: rgba(255, 255, 255, 0.3);
        }
        .loading {
            font-size: 48px;
            color: white;
        }
        .error {
            font-size: 36px;
            color: white;
            opacity: 0.8;
        }
    </style>
</head>
<body>
    <a href="/dashboard" class="back-btn">← Dashboard</a>
    
    <div class="container">
        <div id="content">
            <div class="loading">Caricamento...</div>
        </div>
    </div>
    
    <script>
        const TARGET_MIN = {{ target_min }};
        const TARGET_MAX = {{ target_max }};
        
        function getDirectionArrow(direction) {
            const arrows = {
                'DoubleUp': '⇈',
                'SingleUp': '↑',
                'FortyFiveUp': '↗',
                'Flat': '→',
                'FortyFiveDown': '↘',
                'SingleDown': '↓',
                'DoubleDown': '⇊'
            };
            return arrows[direction] || '→';
        }
        
        function formatTimestamp(isoString) {
            const date = new Date(isoString);
            return date.toLocaleString('it-IT', {
                day: '2-digit',
                month: '2-digit',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
        }
        
        function getTimeAgo(isoString) {
            const date = new Date(isoString);
            const now = new Date();
            const diffMs = now - date;
            const diffMins = Math.floor(diffMs / 60000);
            
            if (diffMins < 1) {
                return 'ora';
            } else if (diffMins === 1) {
                return '1 min fa';
            } else if (diffMins < 60) {
                return `${diffMins} min fa`;
            } else {
                const diffHours = Math.floor(diffMins / 60);
                if (diffHours === 1) {
                    return '1 ora fa';
                } else {
                    return `${diffHours} ore fa`;
                }
            }
        }
        
        async function updateDisplay() {
            try {
                const response = await fetch('/dashboard/api/current');
                if (!response.ok) {
                    throw new Error('Nessun dato disponibile');
                }
                
                const data = await response.json();
                
                // Aggiorna sfondo in base al range
                document.body.className = data.in_range ? 'in-range' : 'out-of-range';
                
                // Costruisci HTML per il display
                const arrow = getDirectionArrow(data.direction);
                const deltaDisplay = data.delta >= 0 ? `+${data.delta}` : data.delta;
                const timestamp = formatTimestamp(data.timestamp);
                const timeAgo = getTimeAgo(data.timestamp);
                
                document.getElementById('content').innerHTML = `
                    <div class="glucose-value">${data.value}</div>
                    <div class="trend-info">
                        <span class="arrow">${arrow}</span>
                        <span class="delta">${deltaDisplay}</span>
                    </div>
                    <div class="timestamp">${timestamp} (${timeAgo})</div>
                `;
                
            } catch (error) {
                console.error('Errore nel caricamento dei dati:', error);
                document.getElementById('content').innerHTML = 
                    '<div class="error">Errore: ' + error.message + '</div>';
            }
        }
        
        // Carica i dati iniziali
        updateDisplay();
        
        // Aggiorna ogni 30 secondi
        setInterval(updateDisplay, 30000);
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    # Controlla se deve girare come daemon (background)
    daemon_mode = False
    dev_mode = False
    
    if len(sys.argv) > 1:
        if 'daemon' in sys.argv or 'background' in sys.argv:
            daemon_mode = True
            if os.name != 'posix':
                print("ERRORE: La modalità daemon è disponibile solo su Linux/Unix")
                print("Su Windows usa: pythonw xdrip.py")
                sys.exit(1)
        if 'dev' in sys.argv:
            dev_mode = True
    
    # Se modalità daemon, fai il fork prima di inizializzare il logger
    if daemon_mode:
        # Logger temporaneo per il messaggio iniziale
        print(f"Avvio in modalità daemon...")
        print(f"Log disponibili in: {LOGS_DIR}/xdrip.log")
        print(f"PID salvato in: {LOGS_DIR}/xdrip.pid")
        daemonize()
    
    logger.info("========== Avvio applicazione xDrip ==========")
    logger.info(f"Path database: {DB_PATH}")
    logger.info(f"Path logs: {LOGS_DIR}")
    if daemon_mode:
        logger.info("Modalità: DAEMON (background)")
    
    try:
        init_db()
        
        # Prova prima Gunicorn (Linux), poi Waitress (multipiattaforma)
        if dev_mode:
            # Modalità sviluppo: flask development server
            logger.info("Modalità SVILUPPO - Flask development server")
            logger.info("Server in avvio su http://0.0.0.0:3000")
            app.run(host="0.0.0.0", port=3000, debug=True)
        else:
            # Modalità produzione
            try:
                from waitress import serve
                logger.info("Modalità PRODUZIONE - Waitress server")
                logger.info("Server in esecuzione su http://0.0.0.0:3000")
                if not daemon_mode:
                    logger.info("Premi CTRL+C per fermare il server")
                serve(app, host="0.0.0.0", port=3000, threads=4)
            except ImportError:
                logger.error("ERRORE: Waitress non installato")
                logger.error("Installa con: pip install waitress")
                logger.error("Oppure usa Gunicorn su Linux: gunicorn -w 4 -b 0.0.0.0:3000 xdrip:app")
                sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interruzione da tastiera ricevuta (CTRL+C)")
    except Exception as e:
        logger.critical(f"Errore critico durante l'avvio dell'applicazione: {e}", exc_info=True)
        sys.exit(1)
