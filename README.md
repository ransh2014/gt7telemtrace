# TRACE — GT7 Telemetry Analytics

**Telemetry Racing Analytics & Comparative Engine**

Live telemetry dashboard + 14-chart lap analysis tool for Gran Turismo 7. Reads the UDP stream straight from your PS4/PS5 over your local network.

---

## Files

| File | Description |
|---|---|
| `gt7telem.py` | Live telemetry dashboard — speed, RPM, gear, tyre temps, inputs, track map |
| `lap_analyst.py` | 14-chart lap analysis tool with A vs B compare mode and sector timing |
| `gt7udp.py` | UDP receive + Salsa20 decryption (core data layer) |
| `config.py` | Configuration — set your PS4/PS5 IP here |
| `requirements_telem.txt` | Dependencies for the live dashboard |
| `requirements_analyst.txt` | Dependencies for the lap analyst |

---

## Setup

> Roughly 10 minutes start to finish.

### Step 1 — Download

Download this repo as a zip or clone it:

```
git clone https://github.com/ransh2014/gt7telemtrace.git
cd gt7telemtrace
```

---

### Step 2 — Extract / Navigate (pick your OS)

**Windows**
Right-click the zip → Extract All → choose somewhere you'll remember (e.g. Documents). Then open that folder in Command Prompt:
```
cd "C:\Users\YourName\Documents\gt7telemtrace"
```

**macOS**
Double-click the zip in Finder — it extracts automatically. Then in Terminal:
```
cd ~/Documents/gt7telemtrace
```

**Linux**
```
cd ~/Downloads
unzip gt7telemtrace.zip -d ~/gt7telemtrace
cd ~/gt7telemtrace
```
> `unzip` not installed? Run `sudo apt install unzip` first.

---

### Step 3 — Install Python (skip if you already have 3.10+)

**Windows / macOS**
Go to [python.org](https://python.org), download Python 3.10 or later, run the installer.
> **Windows:** On the first installer screen, tick **"Add Python to PATH"** before clicking Install Now.

**Linux**
```bash
# Debian / Ubuntu / Mint
sudo apt update && sudo apt install python3 python3-pip

# Fedora / RHEL
sudo dnf install python3 python3-pip

# Arch
sudo pacman -S python python-pip
```

---

### Step 4 — Set Your Console IP

Open `config.py` in any text editor. Replace the example IP with your PS4/PS5's actual local IP:

```python
PS_IP = "192.168.1.1"   # ← replace with your console's IP
```

> **Finding your IP:** PS4/PS5 → Settings → Network → View Connection Status → IP Address

---

### Step 5 — Install Dependencies

```bash
# dashboard dependencies
pip install -r requirements_telem.txt

# lap analyst dependencies
pip install -r requirements_analyst.txt
```

> **macOS / Linux:** use `pip3` instead of `pip` if the above doesn't work.
> **pycryptodome failing?** Try: `pip install pycryptodome --user`

---

### Step 6 — Run the Dashboard

Make sure GT7 is open and running on your PS4/PS5 first, then:

```bash
python gt7telem.py
# macOS / Linux:
python3 gt7telem.py
```

> **Shows OFFLINE?** Check your IP in `config.py` and make sure your PC and console are on the same network. GT7 needs to be in-game, not on the home screen.

---

### Step 7 — Analyse Your Laps

After recording some laps with the dashboard running, your data is saved automatically. To open the lap analyst:

```bash
python lap_analyst.py
# macOS / Linux:
python3 lap_analyst.py
```

14 chart groups, A vs B comparison mode, sector timing, driver ratings.

---

## Optional Settings

Open `config.py` to tweak these:

```python
LAPS_FOLDER = "laps"   # where lap JSONs are saved (relative to gt7telem/)
SAMPLE_RATE = 60       # telemetry samples per second
```

Windows example: `"C:\Users\YourName\Documents\laps"`
macOS / Linux example: `"/home/yourname/documents/laps"`

---

## FAQ

**Does this work on PS5?**
Yes. The telemetry UDP stream is identical on PS4 and PS5.

**The dashboard says OFFLINE — what do I check?**
Two most common causes: wrong IP in `config.py`, or your PC and PS4/PS5 are on different subnets (e.g. one on 2.4 GHz, one on 5 GHz). GT7 must be in-game, not on the PS home screen.

**pycryptodome install fails?**
Try `pip install pycryptodome --user`. On macOS/Linux use `pip3`. If still failing on Windows, make sure you ticked "Add Python to PATH" during install and restarted your terminal.

**macOS says "python" not found.**
Use `python3` and `pip3` for all commands — that's what the python.org installer provides.

**Linux: tkinter window doesn't open.**
tkinter isn't always bundled. Fix with:
`sudo apt install python3-tk` (Debian/Ubuntu) or `sudo dnf install python3-tkinter` (Fedora)

**Where does the telemetry protocol come from?**
The GT7 UDP stream was reverse-engineered by [Bornhall](https://github.com/Bornhall/gt7telemetry), who figured out the Salsa20 decryption key and full packet structure. This project's core data layer is built on that work. Please go star the original repo.

---

## Credits

Core UDP protocol and Salsa20 decryption by **[Bornhall — gt7telemetry](https://github.com/Bornhall/gt7telemetry)**.
Not affiliated with Polyphony Digital or Sony Interactive Entertainment.
