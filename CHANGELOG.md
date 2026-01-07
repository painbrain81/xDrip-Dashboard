# Changelog

## [3.0.0] - 2026-01-07

### Added
- **Multi-unit support (mg/dL and mmol/L)**
  - Toggle button to switch between mg/dL and mmol/L in real-time
  - Unit preference saved in browser localStorage
  - Automatic conversion of all values (glucose, delta, statistics, charts)
  - Separate customizable target ranges for each unit
  - Proper rounding: integers for mg/dL, 1 decimal for mmol/L
  - Configuration variable `XDRIP_UNIT` to match xDrip app settings
- **Automatic unit conversion system**
  - Incoming data converted to mg/dL for database storage
  - Display conversion based on user preference
  - Consistent conversion across all dashboard pages
- **Enhanced time period statistics**
  - Analysis by daily time slots (night, morning, afternoon, evening)
  - Period-specific averages and variability metrics
  - Mini TIR charts for each time period
- **Customizable target ranges**
  - Separate target limits for mg/dL and mmol/L
  - `TARGET_MIN_MMOL` and `TARGET_MAX_MMOL` configuration variables
  - Severe hypoglycemia and hyperglycemia thresholds for both units

### Changed
- Improved xDrip configuration documentation with unit setting instructions
- Enhanced data storage architecture with automatic mg/dL normalization
- Updated chart visualization to support both unit systems
- Refined statistics calculation for multi-unit support

## [2.5.0] - 2026-01-05

### Added
- **Dark mode support**
  - Toggle button available on all pages
  - Preference saved in browser localStorage
  - Smooth transitions between light and dark themes
  - Optimized color scheme for night viewing
  - Dark mode for dashboard, statistics, and all UI components
- **Large display page enhancements**
  - Dynamic background color based on glucose range
  - Green background when in target range (70-180 mg/dL)
  - Orange background when out of range
  - Full-screen optimized visualization
  - Ideal for always-on monitors

### Changed
- Improved UI consistency across all pages
- Enhanced color contrast for better readability
- Updated chart colors for dark mode compatibility

## [2.0.0] - 2026-01-03

### Added
- **Advanced statistics page**
  - Multiple time period analysis (24h, 3 days, 7 days, 30 days, all data)
  - Complete glucose metrics (average, median, standard deviation)
  - GMI (Glucose Management Indicator / estimated HbA1c) calculation
  - Coefficient of Variation (CV) for glucose variability
  - Min/max values tracking
- **Time In Range (TIR) visualization**
  - Interactive bar chart with 5 segments
  - Detailed ranges: Very Low (<54), Low (54-70), In Range (70-180), High (180-250), Very High (>250)
  - Percentages and absolute counts for each range
  - Color-coded visualization for quick assessment
- **Time period analysis**
  - Statistics by daily time slot (night, morning, afternoon, evening)
  - Period-specific TIR charts
  - Comparative analysis across different times of day
- Default statistics view set to 7 days

### Changed
- Enhanced statistics calculation engine
- Improved data aggregation for multiple time periods
- Updated dashboard navigation with statistics page link

## [1.8.0] - 2026-01-01

### Added
- **Login attempt protection system**
  - Maximum 3 failed login attempts per IP address
  - 10-minute automatic block after exceeding limit
  - Real-time feedback on remaining attempts
  - Automatic reset after successful login
  - Block expiration handling with automatic cleanup
  - Complete logging of failed attempts and blocks
- IP-based tracking dictionary for login attempts
- Enhanced security logging for unauthorized access

### Changed
- Improved login page with attempt counter display
- Enhanced error messages for blocked IPs
- Updated session security handling

## [1.5.0] - 2025-12-28

### Added
- **Smoothed trend line feature**
  - Toggle checkbox to enable/disable trend line
  - Moving average algorithm for smoothing glucose fluctuations
  - Orange colored line for easy distinction from data points
  - Real-time toggle without page reload
  - Follows real trends while reducing noise
- Interactive chart enhancements
- Preference persistence for trend line visibility

### Changed
- Improved chart visualization with multiple data series
- Enhanced Chart.js configuration for better performance

## [1.2.0] - 2025-12-25

### Added
- **Background daemon mode (Linux/Unix)**
  - Process daemonization with double-fork technique
  - Detached from terminal for persistent operation
  - PID file creation in logs directory (`xdrip.pid`)
  - Graceful shutdown handling
  - Survives terminal closure
  - Command line argument `daemon` or `background` to activate
- Windows compatibility notes (use `pythonw` for background execution)
- Systemd service configuration example
- Daemon process management documentation

### Changed
- Enhanced application startup with mode detection
- Improved logging for daemon mode
- Updated shutdown procedures with `atexit` registration

## [1.0.0] - 2025-12-20

### Added
- **Advanced logging system**
  - Automatic rotation every 24 hours (midnight)
  - Retention of last 30 days of logs
  - Dual output: file and console
  - TimedRotatingFileHandler implementation
  - Structured log format with timestamps
  - Comprehensive event logging:
    - Application start and shutdown
    - xDrip data reception
    - Errors and exceptions
    - Unauthorized access attempts
- Configurable logs directory path
- `setup_logging()` function with robust configuration
- Graceful shutdown logging

### Changed
- Improved error tracking throughout the application
- Enhanced debugging capabilities
- Better production monitoring support

## [0.9.0] - 2025-12-15

### Added
- **Interactive web dashboard**
  - Multi-period visualization (4, 8, 12, 18, 24, 48 hours)
  - Chart.js integration for data visualization
  - Highlighted target glucose range (70-180 mg/dL)
  - Color-coded data points based on glucose range
  - Directional arrows for glucose trends
- **Real-time statistics**
  - Current glucose value with delta
  - Average for selected time period
  - Device battery level display
  - Last update timestamp with time elapsed
- **Password-protected dashboard**
  - Login page with session management
  - `login_required` decorator for route protection
  - Flask session handling
  - Configurable dashboard password
- Customizable dashboard title

### Changed
- Enhanced UI with responsive design
- Improved data presentation with visual indicators

## [0.5.0] - 2025-12-10

### Added
- **SQLite database integration**
  - `entries` table for glucose readings
  - `devicestatus` table for battery and device info
  - Automatic table creation with indexes
  - Unique constraint on `date_ms` to prevent duplicates
  - Timestamp indexing for query optimization
- Database initialization function `init_db()`
- Data retrieval functions for time-based queries
- Statistics calculation engine
  - Mean, median, min, max calculations
  - Range-based counting (in range, below, above)
  - Percentage calculations for TIR metrics

### Changed
- Migrated from in-memory storage to persistent SQLite database
- Improved data persistence across application restarts
- Enhanced query performance with proper indexing

## [0.3.0] - 2025-12-05

### Added
- **xDrip+ API endpoints**
  - `/xdrip/<secret>/entries` - Glucose data reception
  - `/xdrip/<secret>/devicestatus` - Device status and battery
  - Secret key authentication for API security
- POST request handling with JSON parsing
- Data normalization (single object or array)
- Entry saving to database with duplicate handling
- Device status logging

### Changed
- Enhanced API security with secret validation
- Improved error handling for malformed requests
- Better logging for API interactions

## [0.2.0] - 2025-12-01

### Added
- **Flask web framework integration**
  - Basic Flask application setup
  - Configuration for production server (Waitress)
  - Development mode support
  - Route structure for API and dashboard
- Index route for health check
- Application configuration variables
  - Database path
  - Secret key
  - Dashboard password
  - Target glucose limits

### Changed
- Project structure organized for web application
- Added production-ready server support

## [0.1.0] - 2025-11-28

### Added
- Initial project setup
- Basic Python script structure
- Configuration section with customizable variables
- README.md with project description
- Requirements specification (Flask, Waitress)
- Project documentation
  - Installation instructions
  - Configuration guide
  - Usage examples
- MIT License

### Project Goals
- Create a blood glucose monitoring system for xDrip+
- Provide web-based visualization dashboard
- Enable secure data collection and storage
- Support multiple display modes and time periods


