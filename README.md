# xDrip Dashboard

Sistema di monitoraggio glicemia per xDrip+ con dashboard web interattiva e logging avanzato.

## 📋 Funzionalità

### 🔄 Ricezione Dati xDrip
- Endpoint REST API per ricevere dati glicemici da xDrip+
- Salvataggio automatico nel database SQLite
- Supporto entries (valori glicemia) e devicestatus (batteria)
- Protezione con secret key

### 📊 Dashboard Web
- **Grafico interattivo** con Chart.js
  - Visualizzazione multi-periodo (4, 8, 12, 18, 24, 48 ore)
  - Fascia target glicemica evidenziata (70-180 mg/dL)
  - Punti colorati in base al range target
  - Frecce direzionali per trend
  
- **Statistiche in tempo reale**
  - Valore corrente con delta
  - Media periodo selezionato
  - Livello batteria dispositivo
  - Ultimo aggiornamento con tempo trascorso

- **Display Grande**
  - Visualizzazione a schermo intero
  - Sfondo dinamico (verde in target, arancione fuori target)
  - Ideale per monitor sempre accesi

### 🔐 Sicurezza
- Autenticazione con password per dashboard
- Secret key per API xDrip
- Sessioni Flask protette

### 📝 Logging
- Rotazione automatica ogni 24 ore (mezzanotte)
- Conservazione ultimi 30 giorni
- Log su file e console
- Tracciamento di:
  - Avvio e chiusura applicazione
  - Ricezione dati xDrip
  - Errori ed eccezioni
  - Tentativi di accesso non autorizzati

## 🚀 Installazione

### Requisiti
- Python 3.7+
- SQLite3

### Dipendenze
```bash
pip install flask waitress
```

### Configurazione
Modifica le seguenti variabili all'inizio di `xdrip.py`: (dove UTENTE è il proprio utente)

```python
DB_PATH = Path("/home/UTENTE/xdrip/xdrip.db")  # Path database
SECRET = "MIASECRET"  # Secret per API xDrip
DASHBOARD_PASSWORD = "MIAPASSWORD"  # Password dashboard
LOGS_DIR = Path("/home/UTENTE/xdrip/logs")  # Cartella log
DASHBOARD_TITLE = "xDrip personale"  # Titolo personalizzato
TARGET_MIN = 70  # Limite minimo target (mg/dL)
TARGET_MAX = 180  # Limite massimo target (mg/dL)
```

## 💻 Utilizzo

### Avvio Standard
```bash
python3 xdrip.py
```
Server in produzione su http://0.0.0.0:3000 (Waitress)

### Avvio in Background (Linux/Unix)
```bash
python3 xdrip.py daemon
```
- Processo in background che libera il terminale
- PID salvato in `logs/xdrip.pid`
- Log solo su file

### Modalità Sviluppo
```bash
python3 xdrip.py dev
```
- Flask development server
- Debug attivo
- Auto-reload al cambio file

### Controllo Processo Daemon
```bash
# Verifica se è attivo
ps aux | grep xdrip

# Visualizza il PID
cat /home/UTENTE/xdrip/logs/xdrip.pid

# Ferma il processo
kill $(cat /home/UTENTE/xdrip/logs/xdrip.pid)

# Visualizza log in tempo reale
tail -f /home/UTENTE/xdrip/logs/xdrip.log
```

## 🔧 Configurazione xDrip+

### Upload API
1. Apri xDrip+ → Settings → Cloud Upload
2. Abilita "REST API Upload"
3. Imposta URL base: `http://TUO_SERVER:3000/xdrip/MIASECRET`
4. Sostituisci `MIASECRET` con il tuo SECRET configurato

### Endpoint API
- **Entries**: `POST /xdrip/<secret>/entries`
- **Device Status**: `POST /xdrip/<secret>/devicestatus`

## 🌐 Accesso Dashboard

### Login
`http://TUO_SERVER:3000/dashboard/login`
- Username: nessuno
- Password: configurata in `DASHBOARD_PASSWORD`

### Dashboard Principale
`http://TUO_SERVER:3000/dashboard`
- Grafico interattivo
- Selezione periodo
- Statistiche complete

### Display Grande
`http://TUO_SERVER:3000/dashboard/display`
- Visualizzazione a schermo intero
- Aggiornamento automatico ogni 30 secondi
- Sfondo colorato in base al target

### Logout
`http://TUO_SERVER:3000/dashboard/logout`

## 📁 Struttura File

```
xdripApi/
├── xdrip.py          # Script principale
├── README.md         # Questa documentazione
└── [configurazione]
    ├── xdrip.db      # Database SQLite (creato automaticamente)
    └── logs/
        ├── xdrip.log       # Log corrente
        ├── xdrip.log.2026-01-05  # Log precedenti
        └── xdrip.pid       # PID processo daemon
```

## 🗄️ Database

### Tabella `entries`
Valori glicemici ricevuti da xDrip+
```sql
- id: INTEGER PRIMARY KEY
- device: TEXT
- date_ms: INTEGER (UNIQUE)
- timestamp_utc: TEXT
- sgv: INTEGER (mg/dL)
- direction: TEXT
- created_at: TIMESTAMP
```

### Tabella `devicestatus`
Stato dispositivo (batteria, etc.)
```sql
- id: INTEGER PRIMARY KEY
- device: TEXT
- battery: INTEGER
- uploader_type: TEXT
- timestamp_utc: TEXT
- created_at: TIMESTAMP
```

## 📊 Aggiornamenti Automatici

- **Dashboard**: ogni 60 secondi
- **Display**: ogni 30 secondi
- **Log rotation**: mezzanotte ogni giorno

## 🔍 Risoluzione Problemi

### Server non si avvia
```bash
# Verifica che Waitress sia installato
pip install waitress

# Controlla i log
cat /home/UTENTE/xdrip/logs/xdrip.log
```

### xDrip non invia dati
1. Verifica URL e secret key
2. Controlla che il server sia raggiungibile dalla rete
3. Verifica i log per errori di autenticazione

### Processo daemon non parte
```bash
# Solo Linux/Unix supporta daemon mode
# Su Windows usa: pythonw xdrip.py
```

### Errori di permessi
```bash
# Assicurati che le cartelle siano scrivibili
chmod -R 755 /home/UTENTE/xdrip
```

## 🎨 Personalizzazione

### Modificare i colori della dashboard
Modifica i CSS nei template `DASHBOARD_TEMPLATE` e `DISPLAY_TEMPLATE`

### Modificare intervalli di aggiornamento
```javascript
// Dashboard (default 60 secondi)
setInterval(() => { loadData(); }, 60000);

// Display (default 30 secondi)
setInterval(updateDisplay, 30000);
```

### Aggiungere nuovi periodi di visualizzazione
Modifica l'array in `get_data()`:
```python
if hours not in [4, 8, 12, 18, 24, 48, 72]:  # Aggiunto 72 ore
```

## 📝 Log

I log includono:
- `INFO`: Operazioni normali (avvio, ricezione dati, salvataggio)
- `WARNING`: Tentativi di accesso non autorizzati
- `ERROR`: Errori gestiti (DB, parsing JSON)
- `CRITICAL`: Errori fatali

Formato log:
```
2026-01-06 15:30:45 - INFO - Entry salvata: sgv=120, direction=Flat
2026-01-06 15:31:00 - WARNING - Tentativo di accesso non autorizzato
```

## 🚦 Limiti Target

I limiti target glicemici sono configurabili:
```python
TARGET_MIN = 70   # mg/dL - Limite inferiore
TARGET_MAX = 180  # mg/dL - Limite superiore
```

Valori dentro il range: **verde**  
Valori fuori range: **arancione/rosso**

## 📞 Supporto

Per problemi o domande:
1. Controlla i log: `tail -f logs/xdrip.log`
2. Verifica la configurazione
3. Testa gli endpoint API manualmente

## 📄 Licenza

Uso personale - Non per distribuzione commerciale

---

**Versione**: 1.0  
**Data**: Gennaio 2026
