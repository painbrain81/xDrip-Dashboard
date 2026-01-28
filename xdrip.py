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
import statistics

app = Flask(__name__)
app.secret_key = "my_xdrip_secret_key_2026"  # Key for sessions
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # Session duration: 30 days

DB_PATH = Path("/home/USER/xdrip/xdrip.db")
SECRET = "MYSECRET"  # your secret in path: /xdrip/MYSECRET/...
DASHBOARD_PASSWORD = "MYPASSWORD"  # Password to access dashboard

# Log path configuration (customizable)
LOGS_DIR = Path("/home/USER/xdrip/logs")  # Logs folder path

# Customizable title for dashboard
DASHBOARD_TITLE = "MY DASHBOARD"

# xDrip data unit configuration
# ================================
# IMPORTANT: Set this to match the unit configuration in your xDrip app
# 
# To check xDrip configuration:
# 1. Open xDrip app
# 2. Go to Settings > Less Common Settings > Extra Settings > Glucose Units
# 
# Set XDRIP_UNIT to:
# - "mg/dl" if xDrip shows glucose in mg/dL (default, most common)
# - "mmol/L" if xDrip shows glucose in mmol/L (common in Europe)
# 
# The system will automatically convert incoming data to mg/dL for storage,
# and you can still view data in either unit in the dashboard.
XDRIP_UNIT = "mg/dl"  # Options: "mg/dl" or "mmol/L"

# Target glucose limits for chart
TARGET_MIN = 70  # Minimum target limit (mg/dL)
TARGET_MAX = 180  # Maximum target limit (mg/dL)
TARGET_MIN_MMOL = 3.9  # Minimum target limit (mmol/L)
TARGET_MAX_MMOL = 10.0  # Maximum target limit (mmol/L)

# Severe hypoglycemia and hyperglycemia thresholds
VERY_LOW_THRESHOLD = 54  # mg/dL - Severe hypoglycemia
VERY_HIGH_THRESHOLD = 250  # mg/dL - Severe hyperglycemia
VERY_LOW_THRESHOLD_MMOL = 3.0  # mmol/L - Severe hypoglycemia
VERY_HIGH_THRESHOLD_MMOL = 13.9  # mmol/L - Severe hyperglycemia


def setup_logging():
    """Configure logging system with 24h rotation"""
    # Create log directory if it doesn't exist
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Main logger configuration
    logger = logging.getLogger('xdrip')
    logger.setLevel(logging.INFO)
    
    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # File handler with rotation every 24 hours (midnight)
    log_file = LOGS_DIR / "xdrip.log"
    file_handler = TimedRotatingFileHandler(
        log_file,
        when='midnight',
        interval=1,
        backupCount=30,  # Keep 30 days of logs
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    
    # Log format
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


# Initialize logger
logger = setup_logging()

# Login attempts tracking
# Dictionary structure: {ip_address: {'attempts': count, 'blocked_until': datetime}}
login_attempts = {}


def log_shutdown():
    """Function called at application shutdown"""
    logger.info("========== xDrip Application shutting down ==========")
    logging.shutdown()


# Register shutdown function
atexit.register(log_shutdown)


def daemonize():
    """Transform process into daemon (Linux/Unix only)"""
    try:
        # First fork
        pid = os.fork()
        if pid > 0:
            # Exit parent process
            sys.exit(0)
    except OSError as e:
        logger.error(f"Error in first fork: {e}")
        sys.exit(1)
    
    # Detach from parent terminal
    os.chdir('/')
    os.setsid()
    os.umask(0)
    
    # Second fork
    try:
        pid = os.fork()
        if pid > 0:
            # Exit second parent process
            sys.exit(0)
    except OSError as e:
        logger.error(f"Error in second fork: {e}")
        sys.exit(1)
    
    # Redirect stdin, stdout, stderr
    sys.stdout.flush()
    sys.stderr.flush()
    
    # Save PID
    pid_file = LOGS_DIR / "xdrip.pid"
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    logger.info(f"Daemon started with PID: {os.getpid()}")


def init_db():
    try:
        logger.info("Initializing database...")
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
        
        # Table for device status
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
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error during database initialization: {e}", exc_info=True)
        raise


def check_secret_in_path(secret_from_path: str) -> bool:
    return secret_from_path == SECRET


def convert_xdrip_value_to_mgdl(value):
    """Convert glucose value from xDrip unit to mg/dL for database storage."""
    if value is None:
        return None
    
    if XDRIP_UNIT == "mmol/L":
        # Convert mmol/L to mg/dL: mg/dL = mmol/L * 18.0
        return round(float(value) * 18.0)
    else:
        # Already in mg/dL
        return value


def login_required(f):
    """Decorator to protect routes with login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def get_last_hours_data(hours=4):
    """Retrieve data from last N hours from database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Calculate timestamp from N hours ago
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


def calculate_statistics(data):
    """Calculate statistics on glucose data"""
    if not data:
        return None
    
    sgv_values = [row[1] for row in data if row[1] is not None]
    
    if not sgv_values:
        return None
    
    # Basic statistics
    mean_glucose = statistics.mean(sgv_values)
    median_glucose = statistics.median(sgv_values)
    std_dev = statistics.stdev(sgv_values) if len(sgv_values) > 1 else 0
    min_glucose = min(sgv_values)
    max_glucose = max(sgv_values)
    
    # Count by range
    in_range = sum(1 for v in sgv_values if TARGET_MIN <= v <= TARGET_MAX)
    below_range = sum(1 for v in sgv_values if v < TARGET_MIN)
    above_range = sum(1 for v in sgv_values if v > TARGET_MAX)
    very_low = sum(1 for v in sgv_values if v < 54)  # Severe hypoglycemia
    very_high = sum(1 for v in sgv_values if v > 250)  # Severe hyperglycemia
    
    total_readings = len(sgv_values)
    
    # Percentages
    percent_in_range = (in_range / total_readings * 100) if total_readings > 0 else 0
    percent_below = (below_range / total_readings * 100) if total_readings > 0 else 0
    percent_above = (above_range / total_readings * 100) if total_readings > 0 else 0
    percent_very_low = (very_low / total_readings * 100) if total_readings > 0 else 0
    percent_very_high = (very_high / total_readings * 100) if total_readings > 0 else 0
    
    # Coefficient of Variation (CV) - glucose variability index
    cv = (std_dev / mean_glucose * 100) if mean_glucose > 0 else 0
    
    # Calculate GMI (Glucose Management Indicator) - HbA1c estimate
    # Formula: GMI = 3.31 + 0.02392 × mean_glucose
    gmi = 3.31 + (0.02392 * mean_glucose)
    
    return {
        'total_readings': total_readings,
        'mean': round(mean_glucose, 1),
        'median': round(median_glucose, 1),
        'std_dev': round(std_dev, 1),
        'cv': round(cv, 1),
        'min': min_glucose,
        'max': max_glucose,
        'gmi': round(gmi, 2),
        'in_range': in_range,
        'below_range': below_range,
        'above_range': above_range,
        'very_low': very_low,
        'very_high': very_high,
        'percent_in_range': round(percent_in_range, 1),
        'percent_below': round(percent_below, 1),
        'percent_above': round(percent_above, 1),
        'percent_very_low': round(percent_very_low, 1),
        'percent_very_high': round(percent_very_high, 1)
    }


def get_time_period_stats(data, start_hour, end_hour, period_name):
    """Calculate statistics for a specific time period of the day"""
    filtered_data = []
    for row in data:
        timestamp_str = row[0]
        if timestamp_str:
            dt = datetime.fromisoformat(timestamp_str)
            hour = dt.hour
            if start_hour <= hour < end_hour:
                filtered_data.append(row)
    
    stats = calculate_statistics(filtered_data)
    if stats:
        stats['period_name'] = period_name
    return stats


def get_all_data_from_db():
    """Retrieve all data from database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute(
            """
            SELECT timestamp_utc, sgv, direction
            FROM entries
            WHERE sgv IS NOT NULL
            ORDER BY timestamp_utc ASC
            """
        )
        
        rows = c.fetchall()
        conn.close()
        
        return rows
    except Exception as e:
        logger.error(f"Error retrieving complete data: {e}", exc_info=True)
        return []


def save_devicestatus_to_db(data: dict):
    """Save device status to SQLite DB."""
    device = data.get("device")
    uploader = data.get("uploader", {})
    battery = uploader.get("battery")
    uploader_type = uploader.get("type")
    
    # Use current timestamp
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
        logger.info(f"Device status saved: device={device}, battery={battery}%, type={uploader_type}")
    except Exception as e:
        logger.error(f"Error saving devicestatus: {e}", exc_info=True)
    finally:
        conn.close()


def save_entry_to_db(entry: dict):
    """Save ONE xDrip entry to SQLite DB."""
    device = entry.get("device")
    date_ms = entry.get("date")
    sgv_raw = entry.get("sgv")
    direction = entry.get("direction")
    
    # Convert glucose value to mg/dL if xDrip sends in mmol/L
    sgv = convert_xdrip_value_to_mgdl(sgv_raw)

    # Convert ms epoch to datetime UTC ISO8601
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
        log_msg = f"Entry saved: sgv={sgv} mg/dL"
        if XDRIP_UNIT == "mmol/L" and sgv_raw is not None:
            log_msg += f" (received: {sgv_raw} mmol/L)"
        log_msg += f", direction={direction}, timestamp={timestamp_utc}"
        logger.info(log_msg)
    except sqlite3.IntegrityError:
        # Ignore duplicates (same date_ms already present)
        logger.debug(f"Duplicate entry ignored: date_ms={date_ms}")
    except Exception as e:
        logger.error(f"Error saving entry: {e}", exc_info=True)
    finally:
        conn.close()


# --- GLUCOSE / ENTRIES ---

@app.route("/xdrip/<secret>/entries", methods=["POST"])
def receive_xdrip_entries(secret):
    if not check_secret_in_path(secret):
        logger.warning(f"Unauthorized access attempt to entries route")
        return "Unauthorized", 401

    try:
        data = request.get_json(force=True)

        # xDrip can send single object or array; normalize to list
        if isinstance(data, dict):
            entries = [data]
        else:
            entries = data

        logger.info(f"Received {len(entries)} entries from xDrip")
        for entry in entries:
            save_entry_to_db(entry)

        return "OK", 200
    except Exception as e:
        logger.error(f"Error handling ENTRIES: {e}", exc_info=True)
        return "Bad Request", 400


# --- DEVICE STATUS (LOGGED BUT PROTECTED) ---

@app.route("/xdrip/<secret>/devicestatus", methods=["POST"])
def receive_xdrip_devicestatus(secret):
    if not check_secret_in_path(secret):
        logger.warning(f"Unauthorized access attempt to devicestatus route")
        return "Unauthorized", 401

    try:
        data = request.get_json(force=True)
        logger.info(f"Received DEVICESTATUS from xDrip")
        save_devicestatus_to_db(data)
    except Exception as e:
        logger.error(f"Error parsing DEVICESTATUS: {e}", exc_info=True)
    
    return "OK", 200


# --- INDEX / TEST ---

@app.route("/", methods=["GET"])
def index():
    return "xDrip Flask receiver active"


# --- DASHBOARD LOGIN ---

@app.route("/dashboard/login", methods=["GET", "POST"])
def login():
    client_ip = request.remote_addr
    
    # Check if IP is currently blocked
    if client_ip in login_attempts:
        blocked_until = login_attempts[client_ip].get('blocked_until')
        if blocked_until and datetime.now(timezone.utc) < blocked_until:
            remaining_time = (blocked_until - datetime.now(timezone.utc)).total_seconds()
            remaining_minutes = int(remaining_time / 60) + 1
            logger.warning(f"Login blocked for IP {client_ip}. Remaining time: {remaining_minutes} minutes")
            return render_template_string(LOGIN_TEMPLATE, 
                                        error=f"Too many failed attempts. Try again in {remaining_minutes} minute(s).", 
                                        title=DASHBOARD_TITLE)
        elif blocked_until and datetime.now(timezone.utc) >= blocked_until:
            # Block period expired, reset attempts
            login_attempts.pop(client_ip, None)
    
    if request.method == "POST":
        password = request.form.get("password")
        if password == DASHBOARD_PASSWORD:
            # Successful login - reset attempts for this IP
            login_attempts.pop(client_ip, None)
            session.permanent = True  # Keep session even after browser close
            session['logged_in'] = True
            logger.info(f"Successful login from IP {client_ip}")
            return redirect(url_for('dashboard'))
        else:
            # Failed login attempt
            if client_ip not in login_attempts:
                login_attempts[client_ip] = {'attempts': 0, 'blocked_until': None}
            
            login_attempts[client_ip]['attempts'] += 1
            attempts = login_attempts[client_ip]['attempts']
            
            logger.warning(f"Failed login attempt from IP {client_ip}. Total attempts: {attempts}")
            
            if attempts >= 3:
                # Block for 10 minutes
                block_duration = timedelta(minutes=10)
                login_attempts[client_ip]['blocked_until'] = datetime.now(timezone.utc) + block_duration
                logger.warning(f"IP {client_ip} blocked for 10 minutes after {attempts} failed attempts")
                return render_template_string(LOGIN_TEMPLATE, 
                                            error="Too many failed attempts. Access blocked for 10 minutes.", 
                                            title=DASHBOARD_TITLE)
            else:
                remaining_attempts = 3 - attempts
                return render_template_string(LOGIN_TEMPLATE, 
                                            error=f"Incorrect password. {remaining_attempts} attempt(s) remaining.", 
                                            title=DASHBOARD_TITLE)
    
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
                                  target_min_mmol=TARGET_MIN_MMOL,
                                  target_max_mmol=TARGET_MAX_MMOL,
                                  title=DASHBOARD_TITLE)


@app.route("/dashboard/api/data")
@login_required
def get_data():
    """API endpoint to get chart data"""
    hours = request.args.get('hours', default=4, type=int)
    # Validate that hours is one of the allowed values
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
    """API endpoint to get current value and delta"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get last 2 values
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
    """API endpoint to get battery level"""
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


@app.route("/dashboard/statistics")
@login_required
def statistics_dashboard():
    """Statistics dashboard page"""
    return render_template_string(STATISTICS_TEMPLATE,
                                  target_min=TARGET_MIN,
                                  target_max=TARGET_MAX,
                                  target_min_mmol=TARGET_MIN_MMOL,
                                  target_max_mmol=TARGET_MAX_MMOL,
                                  title=DASHBOARD_TITLE)


@app.route("/dashboard/api/stats/<int:hours>")
@login_required
def get_stats(hours):
    """API to get statistics for a specific period"""
    try:
        data = get_last_hours_data(hours)
        stats = calculate_statistics(data)
        
        if stats:
            return jsonify({
                'success': True,
                'period_hours': hours,
                'stats': stats
            })
        else:
            return jsonify({
                'success': False,
                'error': 'No data available'
            }), 404
    except Exception as e:
        logger.error(f"Error API stats: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route("/dashboard/api/stats/all")
@login_required
def get_all_stats():
    """API to get complete statistics"""
    try:
        data = get_all_data_from_db()
        stats = calculate_statistics(data)
        
        if stats:
            return jsonify({
                'success': True,
                'stats': stats
            })
        else:
            return jsonify({
                'success': False,
                'error': 'No data available'
            }), 404
    except Exception as e:
        logger.error(f"Error API complete stats: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route("/dashboard/api/stats/periods")
@login_required
def get_period_stats():
    """API to get statistics divided by time period of the day"""
    try:
        # Retrieve data from last 30 days
        data = get_last_hours_data(hours=720)
        
        periods = {
            'night': get_time_period_stats(data, 0, 6, 'Night (00:00-06:00)'),
            'morning': get_time_period_stats(data, 6, 12, 'Morning (06:00-12:00)'),
            'afternoon': get_time_period_stats(data, 12, 18, 'Afternoon (12:00-18:00)'),
            'evening': get_time_period_stats(data, 18, 24, 'Evening (18:00-24:00)')
        }
        
        return jsonify({
            'success': True,
            'periods': periods
        })
    except Exception as e:
        logger.error(f"Error API period stats: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route("/dashboard/display")
@login_required
def display():
    """Large display page for glucose visualization"""
    return render_template_string(DISPLAY_TEMPLATE,
                                  target_min=TARGET_MIN,
                                  target_max=TARGET_MAX,
                                  target_min_mmol=TARGET_MIN_MMOL,
                                  target_max_mmol=TARGET_MAX_MMOL,
                                  title=DASHBOARD_TITLE)


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
            <button type="submit">Login</button>
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
            transition: background 0.3s, color 0.3s;
        }
        body.dark-mode {
            background: #1a1a2e;
            color: #eee;
        }
        body.dark-mode .header {
            background: #16213e !important;
        }
        body.dark-mode .stat-card,
        body.dark-mode .chart-container {
            background: #0f3460 !important;
            color: #eee;
        }
        body.dark-mode .stat-label {
            color: #aaa !important;
        }
        body.dark-mode .stat-value {
            color: #667eea !important;
        }
        body.dark-mode h1 {
            color: #eee !important;
        }
        body.dark-mode .time-selector label {
            color: #ccc !important;
        }
        body.dark-mode .time-selector select {
            background: #16213e;
            color: #eee;
            border-color: #555;
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
        .time-selector label {
            display: flex;
            align-items: center;
            gap: 5px;
            cursor: pointer;
        }
        .time-selector input[type="checkbox"] {
            cursor: pointer;
            width: 18px;
            height: 18px;
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
        body.dark-mode {
            background: #1a1a2e;
            color: #eee;
        }
        body.dark-mode .header {
            background: #16213e !important;
        }
        body.dark-mode .stat-card,
        body.dark-mode .chart-container {
            background: #0f3460 !important;
            color: #eee;
        }
        body.dark-mode .stat-label {
            color: #aaa !important;
        }
        body.dark-mode .stat-value {
            color: #667eea !important;
        }
        body.dark-mode h1 {
            color: #eee !important;
        }
        body.dark-mode .time-selector label {
            color: #ccc !important;
        }
        body.dark-mode .time-selector select {
            background: #16213e;
            color: #eee;
            border-color: #555;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-left">
            <h1>{{ title }}</h1>
            <div class="time-selector">
                <label for="timeRange">Period:</label>
                <select id="timeRange" onchange="changeTimeRange()">
                    <option value="4" selected>Last 4 hours</option>
                    <option value="8">Last 8 hours</option>
                    <option value="12">Last 12 hours</option>
                    <option value="18">Last 18 hours</option>
                    <option value="24">Last 24 hours</option>
                    <option value="48">Last 48 hours</option>
                </select>
            </div>
            <div class="time-selector">
                <label>
                    <input type="checkbox" id="showSmoothed" onchange="toggleSmoothedLine()" checked>
                    Show smoothed line
                </label>
            </div>
        </div>
        <div style="display: flex; gap: 10px;">
            <button onclick="toggleDarkMode()" class="display-btn" style="border: none; cursor: pointer;">🌙 Dark</button>
            <button onclick="toggleUnit()" class="display-btn" style="border: none; cursor: pointer;" id="unitToggleBtn">📊 mmol/L</button>
            <a href="/dashboard/statistics" class="display-btn">Statistics</a>
            <a href="/dashboard/display" class="display-btn">Large Display</a>
            <a href="/dashboard/logout" class="logout-btn">Logout</a>
        </div>
    </div>
    
    <div class="stats">
        <div class="stat-card">
            <div class="stat-label">Current Value</div>
            <div class="stat-value" id="current-value">--</div>
            <div class="stat-label" id="current-direction">--</div>
        </div>
        <div class="stat-card">
            <div class="stat-label" id="avg-label">4-Hour Average</div>
            <div class="stat-value" id="avg-value">--</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Battery</div>
            <div class="stat-value" id="battery-value">--</div>
            <div class="stat-label" id="battery-type">--</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Last Update</div>
            <div class="stat-value" style="font-size: 18px;" id="last-update">--</div>
        </div>
    </div>
    
    <div class="chart-container">
        <canvas id="glucoseChart"></canvas>
    </div>
    
    <script>
        const TARGET_MIN = {{ target_min }};
        const TARGET_MAX = {{ target_max }};
        const TARGET_MIN_MMOL = {{ target_min_mmol }};
        const TARGET_MAX_MMOL = {{ target_max_mmol }};
        let chart = null;
        let currentHours = 4;
        let showSmoothed = true;
        let currentUnit = 'mg/dl'; // 'mg/dl' or 'mmol/L'
        
        // Conversion functions
        function mgdlToMmol(mgdl) {
            return (mgdl / 18.0).toFixed(1);
        }
        
        function getTargetMinConverted() {
            return currentUnit === 'mmol/L' ? TARGET_MIN_MMOL : TARGET_MIN;
        }
        
        function getTargetMaxConverted() {
            return currentUnit === 'mmol/L' ? TARGET_MAX_MMOL : TARGET_MAX;
        }
        
        function convertValue(value) {
            if (currentUnit === 'mmol/L') {
                return parseFloat(mgdlToMmol(value));
            }
            return Math.round(value);
        }
        
        function getUnitLabel() {
            return currentUnit === 'mmol/L' ? 'mmol/L' : 'mg/dL';
        }
        
        function toggleUnit() {
            currentUnit = currentUnit === 'mg/dl' ? 'mmol/L' : 'mg/dl';
            localStorage.setItem('glucoseUnit', currentUnit);
            
            // Update button text
            const btn = document.getElementById('unitToggleBtn');
            btn.textContent = currentUnit === 'mg/dl' ? '📊 mmol/L' : '📊 mg/dL';
            
            // Reload data to update display
            loadData();
        }
        
        // Dark mode functions
        function toggleDarkMode() {
            document.body.classList.toggle('dark-mode');
            const isDark = document.body.classList.contains('dark-mode');
            localStorage.setItem('darkMode', isDark ? 'enabled' : 'disabled');
            
            // Update chart colors if chart exists
            if (chart) {
                updateChartColors(isDark);
            }
        }
        
        function updateChartColors(isDark) {
            if (!chart) return;
            
            const gridColor = isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.05)';
            const textColor = isDark ? '#eee' : '#666';
            
            chart.options.scales.y.grid.color = function(context) {
                const value = context.tick.value;
                if (value >= TARGET_MIN && value <= TARGET_MAX) {
                    return isDark ? 'rgba(16, 185, 129, 0.2)' : 'rgba(16, 185, 129, 0.1)';
                }
                return gridColor;
            };
            chart.options.scales.x.grid.color = gridColor;
            chart.options.scales.y.ticks.color = textColor;
            chart.options.scales.x.ticks.color = textColor;
            chart.options.scales.y.title.color = textColor;
            chart.options.scales.x.title.color = textColor;
            chart.options.plugins.legend.labels.color = textColor;
            
            chart.update();
        }
        
        // Check for saved dark mode preference
        if (localStorage.getItem('darkMode') === 'enabled') {
            document.body.classList.add('dark-mode');
        }
        
        // Function to calculate smoothed line using moving average
        function calculateSmoothedLine(values) {
            const windowSize = 5; // Window size for moving average
            const smoothedValues = [];
            
            for (let i = 0; i < values.length; i++) {
                let sum = 0;
                let count = 0;
                
                // Calculate window boundaries
                const start = Math.max(0, i - Math.floor(windowSize / 2));
                const end = Math.min(values.length, i + Math.ceil(windowSize / 2));
                
                // Sum values in the window
                for (let j = start; j < end; j++) {
                    sum += values[j];
                    count++;
                }
                
                // Calculate average
                smoothedValues.push(sum / count);
            }
            
            return smoothedValues;
        }
        
        function changeTimeRange() {
            currentHours = parseInt(document.getElementById('timeRange').value);
            // Also update average label
            document.getElementById('avg-label').textContent = `${currentHours}-Hour Average`;
            loadData();
        }
        
        function toggleSmoothedLine() {
            showSmoothed = document.getElementById('showSmoothed').checked;
            if (chart) {
                chart.data.datasets[1].hidden = !showSmoothed;
                chart.update();
            }
        }
        
        // Custom plugin to draw target range band
        const targetRangePlugin = {
            id: 'targetRange',
            beforeDatasetsDraw(chart) {
                const { ctx, chartArea: { top, bottom, left, right }, scales: { y } } = chart;
                
                // Calculate Y positions for target limits
                const yMin = y.getPixelForValue(getTargetMinConverted());
                const yMax = y.getPixelForValue(getTargetMaxConverted());
                
                // Draw light green rectangle
                ctx.save();
                ctx.fillStyle = 'rgba(16, 185, 129, 0.1)';
                ctx.fillRect(left, yMax, right - left, yMin - yMax);
                
                // Draw target band border lines
                ctx.strokeStyle = 'rgba(16, 185, 129, 0.4)';
                ctx.lineWidth = 1;
                ctx.setLineDash([5, 5]);
                
                // Upper line (TARGET_MAX)
                ctx.beginPath();
                ctx.moveTo(left, yMax);
                ctx.lineTo(right, yMax);
                ctx.stroke();
                
                // Lower line (TARGET_MIN)
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
                        `<div class="loading">No data available in the last ${currentHours} hours</div>`;
                    return;
                }
                
                // Update statistics
                const lastReading = data[data.length - 1];
                
                // Calculate delta if there are at least 2 values
                let delta = 0;
                if (data.length >= 2) {
                    const previousReading = data[data.length - 2];
                    delta = lastReading.value - previousReading.value;
                }
                const deltaDisplay = delta >= 0 ? `+${delta}` : `${delta}`;
                
                // Update browser title
                const arrow = getDirectionArrow(lastReading.direction);
                document.title = `${lastReading.value} ${arrow} (${deltaDisplay}) - {{ title }}`;
                
                // Update statistics cards
                const currentValueConverted = convertValue(lastReading.value);
                const deltaConverted = currentUnit === 'mmol/L' ? parseFloat(mgdlToMmol(Math.abs(delta))) : Math.round(Math.abs(delta));
                const deltaSign = delta >= 0 ? '+' : '-';
                const deltaDisplayConverted = `${deltaSign}${deltaConverted}`;
                
                document.getElementById('current-value').textContent = `${currentValueConverted} ${getUnitLabel()} (${deltaDisplayConverted})`;
                document.getElementById('current-direction').textContent = arrow;
                
                const avgValue = data.reduce((sum, d) => sum + d.value, 0) / data.length;
                const avgConverted = convertValue(avgValue);
                document.getElementById('avg-value').textContent = `${avgConverted} ${getUnitLabel()}`;
                
                const formattedTime = formatTime(lastReading.timestamp);
                const timeAgo = getTimeAgo(lastReading.timestamp);
                document.getElementById('last-update').innerHTML = 
                    `${formattedTime}<br><small style="font-size: 14px; opacity: 0.7;">${timeAgo}</small>`;
                
                // Prepare data for chart
                const labels = data.map(d => formatTime(d.timestamp));
                const rawValues = data.map(d => d.value);
                const values = rawValues.map(v => currentUnit === 'mmol/L' ? parseFloat(mgdlToMmol(v)) : v);
                
                // Color points based on target range (using current unit values)
                const pointColors = values.map(v => 
                    (v >= getTargetMinConverted() && v <= getTargetMaxConverted()) ? '#10b981' : '#f97316'
                );
                
                // Calculate smoothed line using moving average
                const smoothedValues = calculateSmoothedLine(values);
                
                // Create or update chart
                const ctx = document.getElementById('glucoseChart').getContext('2d');
                
                if (chart) {
                    chart.destroy();
                }
                
                const isDark = document.body.classList.contains('dark-mode');
                const gridColor = isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.05)';
                const textColor = isDark ? '#eee' : '#666';
                
                chart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: `Glucose (${getUnitLabel()})`,
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
                        },
                        {
                            label: 'Smoothed Average',
                            data: smoothedValues,
                            borderColor: '#f59e0b',
                            backgroundColor: 'rgba(245, 158, 11, 0.05)',
                            borderWidth: 3,
                            fill: false,
                            tension: 0.4,
                            pointRadius: 0,
                            pointHoverRadius: 0,
                            hidden: !showSmoothed
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                display: true,
                                position: 'top',
                                labels: {
                                    color: textColor
                                }
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
                                min: currentUnit === 'mmol/L' 
                                    ? Math.min(getTargetMinConverted() - 1, Math.min(...values) - 0.5)
                                    : Math.min(TARGET_MIN - 20, Math.min(...values) - 10),
                                max: currentUnit === 'mmol/L'
                                    ? Math.max(getTargetMaxConverted() + 1, Math.max(...values) + 0.5)
                                    : Math.max(TARGET_MAX + 20, Math.max(...values) + 10),
                                grid: {
                                    color: function(context) {
                                        // Highlight target range with light green background
                                        const value = context.tick.value;
                                        const targetMin = getTargetMinConverted();
                                        const targetMax = getTargetMaxConverted();
                                        if (value >= targetMin && value <= targetMax) {
                                            return isDark ? 'rgba(16, 185, 129, 0.2)' : 'rgba(16, 185, 129, 0.1)';
                                        }
                                        return gridColor;
                                    }
                                },
                                ticks: {
                                    color: textColor
                                },
                                title: {
                                    display: true,
                                    text: `Glucose (${getUnitLabel()})`,
                                    color: textColor
                                },
                                afterBuildTicks: function(axis) {
                                    // Ensure target limits are visible as ticks
                                    const ticks = axis.ticks;
                                    const targetMin = getTargetMinConverted();
                                    const targetMax = getTargetMaxConverted();
                                    if (!ticks.find(t => t.value === targetMin)) {
                                        ticks.push({ value: targetMin });
                                    }
                                    if (!ticks.find(t => t.value === targetMax)) {
                                        ticks.push({ value: targetMax });
                                    }
                                    ticks.sort((a, b) => a.value - b.value);
                                }
                            },
                            x: {
                                title: {
                                    display: true,
                                    text: 'Time',
                                    color: textColor
                                },
                                grid: {
                                    color: gridColor
                                },
                                ticks: {
                                    color: textColor
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
                console.error('Error loading data:', error);
                document.querySelector('.chart-container').innerHTML = 
                    '<div class="loading">Error loading data</div>';
            }
        }
        
        // Check for saved unit preference
        if (localStorage.getItem('glucoseUnit')) {
            currentUnit = localStorage.getItem('glucoseUnit');
            const btn = document.getElementById('unitToggleBtn');
            if (btn) {
                btn.textContent = currentUnit === 'mg/dl' ? '📊 mmol/L' : '📊 mg/dL';
            }
        }
        
        // Load initial data
        loadData();
        loadBattery();
        
        // Update every 60 seconds
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
            <div class="loading">Loading...</div>
        </div>
    </div>
    
    <script>
        const TARGET_MIN = {{ target_min }};
        const TARGET_MAX = {{ target_max }};
        const TARGET_MIN_MMOL = {{ target_min_mmol }};
        const TARGET_MAX_MMOL = {{ target_max_mmol }};
        let currentUnit = 'mg/dl'; // 'mg/dl' or 'mmol/L'
        
        // Conversion functions
        function mgdlToMmol(mgdl) {
            return (mgdl / 18.0).toFixed(1);
        }
        
        function convertValue(value) {
            if (currentUnit === 'mmol/L') {
                return parseFloat(mgdlToMmol(value));
            }
            return Math.round(value);
        }
        
        function getUnitLabel() {
            return currentUnit === 'mmol/L' ? 'mmol/L' : 'mg/dL';
        }
        
        function getTargetMinConverted() {
            return currentUnit === 'mmol/L' ? TARGET_MIN_MMOL : TARGET_MIN;
        }
        
        function getTargetMaxConverted() {
            return currentUnit === 'mmol/L' ? TARGET_MAX_MMOL : TARGET_MAX;
        }
        
        function checkInRange(value) {
            const convertedValue = currentUnit === 'mmol/L' ? parseFloat(mgdlToMmol(value)) : value;
            return convertedValue >= getTargetMinConverted() && convertedValue <= getTargetMaxConverted();
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
                return 'now';
            } else if (diffMins === 1) {
                return '1 min ago';
            } else if (diffMins < 60) {
                return `${diffMins} min ago`;
            } else {
                const diffHours = Math.floor(diffMins / 60);
                if (diffHours === 1) {
                    return '1 hour ago';
                } else {
                    return `${diffHours} hours ago`;
                }
            }
        }
        
        async function updateDisplay() {
            try {
                const response = await fetch('/dashboard/api/current');
                if (!response.ok) {
                    throw new Error('No data available');
                }
                
                const data = await response.json();
                
                // Update background based on range (using current unit)
                const inRange = checkInRange(data.value);
                document.body.className = inRange ? 'in-range' : 'out-of-range';
                
                // Build HTML for display
                const arrow = getDirectionArrow(data.direction);
                const valueConverted = convertValue(data.value);
                const deltaConverted = currentUnit === 'mmol/L' ? parseFloat(mgdlToMmol(Math.abs(data.delta))) : Math.round(Math.abs(data.delta));
                const deltaSign = data.delta >= 0 ? '+' : '-';
                const deltaDisplay = `${deltaSign}${deltaConverted}`;
                const timestamp = formatTimestamp(data.timestamp);
                const timeAgo = getTimeAgo(data.timestamp);
                const unitLabel = getUnitLabel();
                
                document.getElementById('content').innerHTML = `
                    <div class="glucose-value">${valueConverted} <span style="font-size: 80px; opacity: 0.8;">${unitLabel}</span></div>
                    <div class="trend-info">
                        <span class="arrow">${arrow}</span>
                        <span class="delta">${deltaDisplay}</span>
                    </div>
                    <div class="timestamp">${timestamp} (${timeAgo})</div>
                `;
                
            } catch (error) {
                console.error('Error loading data:', error);
                document.getElementById('content').innerHTML = 
                    '<div class="error">Error: ' + error.message + '</div>';
            }
        }
        
        // Check for saved unit preference
        if (localStorage.getItem('glucoseUnit')) {
            currentUnit = localStorage.getItem('glucoseUnit');
        }
        
        // Load initial data
        updateDisplay();
        
        // Update every 30 seconds
        setInterval(updateDisplay, 30000);
    </script>
</body>
</html>
"""

STATISTICS_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }} - Statistics</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #f5f7fa;
            padding: 20px;
            transition: background 0.3s, color 0.3s;
        }
        body.dark-mode {
            background-color: #1a1a2e;
            color: #eee;
        }
        body.dark-mode .header {
            background: linear-gradient(135deg, #4a5ba8 0%, #5a3d7a 100%) !important;
        }
        body.dark-mode .period-selector,
        body.dark-mode .stat-card,
        body.dark-mode .range-card,
        body.dark-mode .period-card {
            background: #0f3460 !important;
            color: #eee;
        }
        body.dark-mode .stat-label,
        body.dark-mode .period-stat-label,
        body.dark-mode .period-tir-label {
            color: #aaa !important;
        }
        body.dark-mode .stat-value,
        body.dark-mode .period-stat-value {
            color: #eee !important;
        }
        body.dark-mode .range-card h2,
        body.dark-mode .period-card h3,
        body.dark-mode .period-selector h3 {
            color: #eee !important;
        }
        body.dark-mode .period-btn {
            background: #16213e;
            color: #ccc;
            border-color: #555;
        }
        body.dark-mode .period-btn:hover {
            background: #1f2f4f;
        }
        body.dark-mode .period-btn.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        body.dark-mode .legend-text {
            color: #ccc !important;
        }
        body.dark-mode .period-stat {
            border-bottom-color: #333 !important;
        }
        body.dark-mode .period-card h3 {
            border-bottom-color: #333 !important;
        }
        body.dark-mode .period-tir-chart {
            border-top-color: #333 !important;
            border-bottom-color: #333 !important;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 15px;
            margin-bottom: 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 {
            font-size: 32px;
        }
        .back-btn {
            background: rgba(255,255,255,0.2);
            color: white;
            padding: 10px 20px;
            border: 2px solid white;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 600;
            transition: all 0.3s;
        }
        .back-btn:hover {
            background: white;
            color: #667eea;
        }
        .period-selector {
            background: white;
            padding: 20px;
            border-radius: 15px;
            margin-bottom: 30px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .period-buttons {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        .period-btn {
            padding: 12px 24px;
            background: #f0f0f0;
            border: 2px solid #ddd;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            transition: all 0.3s;
        }
        .period-btn:hover {
            background: #e0e0e0;
        }
        .period-btn.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-color: #667eea;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            transition: transform 0.3s;
        }
        .stat-card:hover {
            transform: translateY(-5px);
        }
        .stat-label {
            color: #777;
            font-size: 14px;
            margin-bottom: 10px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .stat-value {
            font-size: 36px;
            font-weight: bold;
            color: #333;
        }
        .stat-unit {
            font-size: 18px;
            color: #999;
            margin-left: 5px;
        }
        .stat-subtext {
            margin-top: 10px;
            font-size: 14px;
            color: #999;
        }
        .range-card {
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            margin-bottom: 30px;
        }
        .range-card h2 {
            margin-bottom: 20px;
            color: #333;
        }
        .range-bar {
            display: flex;
            height: 40px;
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 20px;
        }
        .range-segment {
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: 600;
            font-size: 14px;
            transition: flex 0.5s;
        }
        .range-very-low {
            background-color: #dc3545;
        }
        .range-low {
            background-color: #ffc107;
        }
        .range-normal {
            background-color: #28a745;
        }
        .range-high {
            background-color: #ff9800;
        }
        .range-very-high {
            background-color: #dc3545;
        }
        .range-legend {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
        }
        .legend-item {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .legend-color {
            width: 20px;
            height: 20px;
            border-radius: 4px;
        }
        .legend-text {
            font-size: 14px;
            color: #555;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: #999;
            font-size: 18px;
        }
        .error {
            background-color: #fee;
            color: #c33;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
            margin: 20px 0;
        }
        .periods-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-top: 30px;
        }
        .period-card {
            background: white;
            padding: 20px;
            border-radius: 15px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .period-card h3 {
            color: #333;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #eee;
        }
        .period-stat {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #f0f0f0;
        }
        .period-stat:last-child {
            border-bottom: none;
        }
        .period-stat-label {
            color: #777;
            font-size: 14px;
        }
        .period-stat-value {
            font-weight: 600;
            color: #333;
        }
        .period-tir-chart {
            margin: 15px 0;
            padding: 10px 0;
            border-top: 1px solid #f0f0f0;
            border-bottom: 1px solid #f0f0f0;
        }
        .period-tir-label {
            font-size: 12px;
            color: #777;
            margin-bottom: 8px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .mini-tir-bar {
            display: flex;
            height: 25px;
            border-radius: 5px;
            overflow: hidden;
        }
        .mini-tir-segment {
            transition: flex 0.3s;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 {{ title }} - Statistics</h1>
        <div style="display: flex; gap: 10px;">
            <button onclick="toggleDarkMode()" class="back-btn" style="border: none; cursor: pointer;">🌙 Dark</button>
            <button onclick="toggleUnit()" class="back-btn" style="border: none; cursor: pointer;" id="unitToggleBtn">📊 mmol/L</button>
            <a href="/dashboard" class="back-btn">← Dashboard</a>
        </div>
    </div>

    <div class="period-selector">
        <h3 style="margin-bottom: 15px; color: #333;">Select Period:</h3>
        <div class="period-buttons">
            <button class="period-btn" onclick="loadStats(24, event)">24 Hours</button>
            <button class="period-btn" onclick="loadStats(72, event)">3 Days</button>
            <button class="period-btn active" onclick="loadStats(168, event)">7 Days</button>
            <button class="period-btn" onclick="loadStats(720, event)">30 Days</button>
            <button class="period-btn" onclick="loadAllStats(event)">All</button>
            <button class="period-btn" onclick="loadPeriodStats(event)">By Time Periods</button>
        </div>
    </div>

    <div id="content">
        <div class="loading">Loading statistics...</div>
    </div>

    <script>
        let currentView = 'stats';
        let currentUnit = 'mg/dl'; // 'mg/dl' or 'mmol/L'
        
        // Conversion functions
        function mgdlToMmol(mgdl) {
            return (mgdl / 18.0).toFixed(1);
        }
        
        function convertValue(value) {
            if (currentUnit === 'mmol/L') {
                return parseFloat(mgdlToMmol(value));
            }
            return Math.round(value);
        }
        
        function getUnitLabel() {
            return currentUnit === 'mmol/L' ? 'mmol/L' : 'mg/dL';
        }
        
        function toggleUnit() {
            currentUnit = currentUnit === 'mg/dl' ? 'mmol/L' : 'mg/dl';
            localStorage.setItem('glucoseUnit', currentUnit);
            
            // Update button text
            const btn = document.getElementById('unitToggleBtn');
            btn.textContent = currentUnit === 'mg/dl' ? '📊 mmol/L' : '📊 mg/dL';
            
            // Reload current view
            if (currentView === 'stats') {
                // Find active button and reload
                const activeBtn = document.querySelector('.period-btn.active');
                if (activeBtn) {
                    const onclick = activeBtn.getAttribute('onclick');
                    if (onclick.includes('loadAllStats')) {
                        loadAllStats();
                    } else {
                        const match = onclick.match(/loadStats\((\d+)/);
                        if (match) {
                            loadStats(parseInt(match[1]));
                        }
                    }
                }
            } else if (currentView === 'periods') {
                loadPeriodStats();
            }
        }
        
        // Dark mode functions
        function toggleDarkMode() {
            document.body.classList.toggle('dark-mode');
            const isDark = document.body.classList.contains('dark-mode');
            localStorage.setItem('darkMode', isDark ? 'enabled' : 'disabled');
        }
        
        // Check for saved dark mode preference
        if (localStorage.getItem('darkMode') === 'enabled') {
            document.body.classList.add('dark-mode');
        }
        
        // Check for saved unit preference
        if (localStorage.getItem('glucoseUnit')) {
            currentUnit = localStorage.getItem('glucoseUnit');
            const btn = document.getElementById('unitToggleBtn');
            if (btn) {
                btn.textContent = currentUnit === 'mg/dl' ? '📊 mmol/L' : '📊 mg/dL';
            }
        }

        function setActiveButton(button) {
            document.querySelectorAll('.period-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            if (button) {
                button.classList.add('active');
            }
        }

        async function loadStats(hours, evt) {
            currentView = 'stats';
            
            let button = null;
            if (evt && evt.target) {
                button = evt.target;
            } else {
                const buttons = document.querySelectorAll('.period-btn');
                for (let btn of buttons) {
                    const onclick = btn.getAttribute('onclick');
                    if (onclick && onclick.includes('loadStats(' + hours)) {
                        button = btn;
                        break;
                    }
                }
            }
            
            setActiveButton(button);

            document.getElementById('content').innerHTML = '<div class="loading">Loading statistics...</div>';

            try {
                const response = await fetch(`/dashboard/api/stats/${hours}`);
                const data = await response.json();

                if (data.success) {
                    displayStats(data.stats, hours + ' hours');
                } else {
                    document.getElementById('content').innerHTML = `<div class="error">${data.error}</div>`;
                }
            } catch (error) {
                document.getElementById('content').innerHTML = `<div class="error">Loading error: ${error.message}</div>`;
            }
        }

        async function loadAllStats(evt) {
            currentView = 'stats';
            const button = evt ? evt.target : null;
            setActiveButton(button);

            document.getElementById('content').innerHTML = '<div class="loading">Loading complete statistics...</div>';

            try {
                const response = await fetch('/dashboard/api/stats/all');
                const data = await response.json();

                if (data.success) {
                    displayStats(data.stats, 'all data');
                } else {
                    document.getElementById('content').innerHTML = `<div class="error">${data.error}</div>`;
                }
            } catch (error) {
                document.getElementById('content').innerHTML = `<div class="error">Loading error: ${error.message}</div>`;
            }
        }

        async function loadPeriodStats(evt) {
            currentView = 'periods';
            const button = evt ? evt.target : null;
            setActiveButton(button);

            document.getElementById('content').innerHTML = '<div class="loading">Loading time period statistics...</div>';

            try {
                const response = await fetch('/dashboard/api/stats/periods');
                const data = await response.json();

                if (data.success) {
                    displayPeriodStats(data.periods);
                } else {
                    document.getElementById('content').innerHTML = `<div class="error">${data.error}</div>`;
                }
            } catch (error) {
                document.getElementById('content').innerHTML = `<div class="error">Loading error: ${error.message}</div>`;
            }
        }

        function displayStats(stats, period) {
            const unitLabel = getUnitLabel();
            const meanConverted = convertValue(stats.mean);
            const medianConverted = convertValue(stats.median);
            const stdDevConverted = convertValue(stats.std_dev);
            const minConverted = convertValue(stats.min);
            const maxConverted = convertValue(stats.max);
            
            const html = `
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-label">Average Glucose</div>
                        <div class="stat-value">${meanConverted}<span class="stat-unit">${unitLabel}</span></div>
                        <div class="stat-subtext">Median: ${medianConverted} ${unitLabel}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">GMI (Estimated HbA1c)</div>
                        <div class="stat-value">${stats.gmi}<span class="stat-unit">%</span></div>
                        <div class="stat-subtext">Glucose Management Indicator</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Standard Deviation</div>
                        <div class="stat-value">${stdDevConverted}<span class="stat-unit">${unitLabel}</span></div>
                        <div class="stat-subtext">CV: ${stats.cv}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Range</div>
                        <div class="stat-value">${minConverted} - ${maxConverted}</div>
                        <div class="stat-subtext">${stats.total_readings} readings</div>
                    </div>
                </div>

                <div class="range-card">
                    <h2>Time In Range (TIR) - Period: ${period}</h2>
                    <div class="range-bar">
                        <div class="range-segment range-very-low" style="flex: ${stats.percent_very_low}">
                            ${stats.percent_very_low > 5 ? stats.percent_very_low.toFixed(1) + '%' : ''}
                        </div>
                        <div class="range-segment range-low" style="flex: ${stats.percent_below - stats.percent_very_low}">
                            ${(stats.percent_below - stats.percent_very_low) > 5 ? (stats.percent_below - stats.percent_very_low).toFixed(1) + '%' : ''}
                        </div>
                        <div class="range-segment range-normal" style="flex: ${stats.percent_in_range}">
                            ${stats.percent_in_range > 5 ? stats.percent_in_range.toFixed(1) + '%' : ''}
                        </div>
                        <div class="range-segment range-high" style="flex: ${stats.percent_above - stats.percent_very_high}">
                            ${(stats.percent_above - stats.percent_very_high) > 5 ? (stats.percent_above - stats.percent_very_high).toFixed(1) + '%' : ''}
                        </div>
                        <div class="range-segment range-very-high" style="flex: ${stats.percent_very_high}">
                            ${stats.percent_very_high > 5 ? stats.percent_very_high.toFixed(1) + '%' : ''}
                        </div>
                    </div>
                    <div class="range-legend">
                        <div class="legend-item">
                            <div class="legend-color range-very-low"></div>
                            <div class="legend-text">Very Low (&lt;54): ${stats.percent_very_low.toFixed(1)}% (${stats.very_low})</div>
                        </div>
                        <div class="legend-item">
                            <div class="legend-color range-low"></div>
                            <div class="legend-text">Low (54-70): ${(stats.percent_below - stats.percent_very_low).toFixed(1)}% (${stats.below_range - stats.very_low})</div>
                        </div>
                        <div class="legend-item">
                            <div class="legend-color range-normal"></div>
                            <div class="legend-text">In Range (70-180): ${stats.percent_in_range.toFixed(1)}% (${stats.in_range})</div>
                        </div>
                        <div class="legend-item">
                            <div class="legend-color range-high"></div>
                            <div class="legend-text">High (180-250): ${(stats.percent_above - stats.percent_very_high).toFixed(1)}% (${stats.above_range - stats.very_high})</div>
                        </div>
                        <div class="legend-item">
                            <div class="legend-color range-very-high"></div>
                            <div class="legend-text">Very High (&gt;250): ${stats.percent_very_high.toFixed(1)}% (${stats.very_high})</div>
                        </div>
                    </div>
                </div>
            `;

            document.getElementById('content').innerHTML = html;
        }

        function displayPeriodStats(periods) {
            let html = '<div class="periods-grid">';

            for (const [key, stats] of Object.entries(periods)) {
                if (stats && stats.total_readings > 0) {
                    const lowPercent = stats.percent_below - stats.percent_very_low;
                    const highPercent = stats.percent_above - stats.percent_very_high;
                    
                    html += `
                        <div class="period-card">
                            <h3>${stats.period_name}</h3>
                            <div class="period-stat">
                                <span class="period-stat-label">Readings:</span>
                                <span class="period-stat-value">${stats.total_readings}</span>
                            </div>
                            <div class="period-stat">
                                <span class="period-stat-label">Average:</span>
                                <span class="period-stat-value">${convertValue(stats.mean)} ${getUnitLabel()}</span>
                            </div>
                            
                            <div class="period-tir-chart">
                                <div class="period-tir-label">Time In Range</div>
                                <div class="mini-tir-bar">
                                    <div class="mini-tir-segment range-very-low" style="flex: ${stats.percent_very_low}" title="Very Low: ${stats.percent_very_low.toFixed(1)}%"></div>
                                    <div class="mini-tir-segment range-low" style="flex: ${lowPercent}" title="Low: ${lowPercent.toFixed(1)}%"></div>
                                    <div class="mini-tir-segment range-normal" style="flex: ${stats.percent_in_range}" title="In Range: ${stats.percent_in_range.toFixed(1)}%"></div>
                                    <div class="mini-tir-segment range-high" style="flex: ${highPercent}" title="High: ${highPercent.toFixed(1)}%"></div>
                                    <div class="mini-tir-segment range-very-high" style="flex: ${stats.percent_very_high}" title="Very High: ${stats.percent_very_high.toFixed(1)}%"></div>
                                </div>
                            </div>
                            
                            <div class="period-stat">
                                <span class="period-stat-label">In Range:</span>
                                <span class="period-stat-value">${stats.percent_in_range}%</span>
                            </div>
                            <div class="period-stat">
                                <span class="period-stat-label">Below:</span>
                                <span class="period-stat-value">${stats.percent_below}%</span>
                            </div>
                            <div class="period-stat">
                                <span class="period-stat-label">Above:</span>
                                <span class="period-stat-value">${stats.percent_above}%</span>
                            </div>
                            <div class="period-stat">
                                <span class="period-stat-label">Std Dev:</span>
                                <span class="period-stat-value">${convertValue(stats.std_dev)} ${getUnitLabel()}</span>
                            </div>
                            <div class="period-stat">
                                <span class="period-stat-label">CV:</span>
                                <span class="period-stat-value">${stats.cv}%</span>
                            </div>
                        </div>
                    `;
                }
            }

            html += '</div>';
            document.getElementById('content').innerHTML = html;
        }

        // Check for saved unit preference
        if (localStorage.getItem('glucoseUnit')) {
            currentUnit = localStorage.getItem('glucoseUnit');
            const btn = document.getElementById('unitToggleBtn');
            if (btn) {
                btn.textContent = currentUnit === 'mg/dl' ? '📊 mmol/L' : '📊 mg/dL';
            }
        }

        // Load default statistics on startup
        window.onload = () => {
            loadStats(168);
        };
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    # Check if should run as daemon (background)
    daemon_mode = False
    dev_mode = False
    
    if len(sys.argv) > 1:
        if 'daemon' in sys.argv or 'background' in sys.argv:
            daemon_mode = True
            if os.name != 'posix':
                print("ERROR: Daemon mode is only available on Linux/Unix")
                print("On Windows use: pythonw xdrip.py")
                sys.exit(1)
        if 'dev' in sys.argv:
            dev_mode = True
    
    # If daemon mode, fork before initializing logger
    if daemon_mode:
        # Temporary logger for initial message
        print(f"Starting in daemon mode...")
        print(f"Logs available in: {LOGS_DIR}/xdrip.log")
        print(f"PID saved in: {LOGS_DIR}/xdrip.pid")
        daemonize()
    
    logger.info("========== xDrip Application starting ==========")
    logger.info(f"Database path: {DB_PATH}")
    logger.info(f"Logs path: {LOGS_DIR}")
    if daemon_mode:
        logger.info("Mode: DAEMON (background)")
    
    try:
        init_db()
        
        # Try Gunicorn (Linux) first, then Waitress (multiplatform)
        if dev_mode:
            # Development mode: flask development server
            logger.info("DEVELOPMENT Mode - Flask development server")
            logger.info("Server starting on http://0.0.0.0:3000")
            app.run(host="0.0.0.0", port=3000, debug=True)
        else:
            # Production mode
            try:
                from waitress import serve
                logger.info("PRODUCTION Mode - Waitress server")
                logger.info("Server running on http://0.0.0.0:3000")
                if not daemon_mode:
                    logger.info("Press CTRL+C to stop server")
                serve(app, host="0.0.0.0", port=3000, threads=4)
            except ImportError:
                logger.error("ERROR: Waitress not installed")
                logger.error("Install with: pip install waitress")
                logger.error("Or use Gunicorn on Linux: gunicorn -w 4 -b 0.0.0.0:3000 xdrip:app")
                sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received (CTRL+C)")
    except Exception as e:
        logger.critical(f"Critical error during application startup: {e}", exc_info=True)
        sys.exit(1)
