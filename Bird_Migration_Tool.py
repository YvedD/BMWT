import streamlit as st
from geopy.geocoders import Nominatim, OpenCage
import folium
import requests
from streamlit_folium import st_folium
import pandas as pd
from datetime import date, datetime, timedelta, timezone
from dateutil.parser import parse
import pytz
from io import BytesIO
import time
import math
import colorsys
import concurrent.futures
from geopy.exc import GeocoderUnavailable
import streamlit.components.v1 as components
from timezonefinder import TimezoneFinder
from soorten_geluiden import iframe_data

_TF = TimezoneFinder()


st.set_page_config(
    page_title="Bird Migration Weather Tool",
    page_icon='images//Milvus1.png',  # Emoji of pad naar icoon
    layout="wide",
    initial_sidebar_state="expanded"
)

# Injecteer CSS om het menu en de footer te verbergen
hide_streamlit_style = """
    <style>
        # MainMenu {visibility: hidden;} /* Verberg het menu rechtsboven */
        footer {visibility: hidden;}    /* Verberg de footer onderaan */
        header {visibility: hidden;}    /* Optioneel: verberg de header */
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# Configuratie voor API headers
API_HEADERS = {
    "User-Agent": "Bird Migration Weather Tool (contact: ydsdsy@gmail.com)",  # Pas hier je contactgegevens aan
    "From": "ydsdsy@gmail.com"  # Dit geeft aan wie contact kan worden opgenomen
}

# CSS toevoegen om de sidebar-breedte aan te passen
st.markdown("""
    <style>
        [data-testid="stSidebar"] {
            min-width: 300px; /* Pas de breedte hier aan */
            max-width: 300px;
        }
    </style>
""", unsafe_allow_html=True)

# Laad de Font Awesome bibliotheek (eenmalig bovenaan je script)
st.markdown(
    """
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css" rel="stylesheet">
    """,
    unsafe_allow_html=True,
)

# Lijst van Europese landen
eu_landen = [
    "Kies een land","Belgie","Nederland","Duitsland","Denemarken","Frankrijk","Armenie","Azerbeidzjan","Albanie","Andorra",
    "Bosnie en Herzegovina","Bulgarije","Cyprus","Estland","Finland","Georgie","Gibraltar","Griekenland","Hongarije",
    "IJsland","Ierland","Israel","Italië","Kazachstan","Kosovo","Kroatie","Letland","Liechtenstein","Litouwen",
    "Luxemburg","Malta","Moldavië","Monaco","Montenegro","Noorwegen","Oekraïne","Oostenrijk","Polen",
    "Portugal","Roemenië","San Marino","Servië","Slovenië","Slowakije","Spanje","Tsjechië","Turkije",
    "Vaticaanstad","Verenigd Koninkrijk","Wit-Rusland","Zweden","Canada","Verenigde staten","Mexico"
]

# Standaardwaarden voor locatie, datum en uren
default_land = "Belgie"
default_locatie = ""
default_datum = date.today()
default_hours = (6, 19)
default_start = (6)
default_end=(19)

# Sidebar configuratie
land_keuze = st.sidebar.selectbox("Land", eu_landen, index=eu_landen.index(default_land))
locatie_keuze = st.sidebar.text_input("Locatie", value=default_locatie)
geselecteerde_datum = st.sidebar.date_input("Datum (vandaag of max. 2 jaar eerder !):", value=default_datum, min_value=date(2000, 1, 1))

# Functie om graden naar windrichting te converteren
def graden_naar_windrichting(graden):
    richtingen = [
        "N", "NNO", "NO", "ONO", "O", "OZO", "ZO", "ZZO",
        "Z", "ZZW", "ZW", "WZW", "W", "WNW", "NW", "NNW", "N"
    ]
    index = round(graden / 22.5) % 16
    return richtingen[index]

# Functie om windsnelheid in km/h naar Beaufort te converteren
def kmh_naar_beaufort(kmh):
    grenzen = [1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 103, 118]
    for i, grens in enumerate(grenzen):
        if kmh <= grens:
            return f"{i}"
    return "12Bf"

# Functie om geolocatie op te zoeken
def toon_geolocatie_op_kaart(locatie):
    # Probeer eerst Nominatim
    geolocator_nominatim = Nominatim(user_agent="Bird_Migration_Weather_Tool")
    try:
        locatie_data = geolocator_nominatim.geocode(locatie, exactly_one=True, language="en")
        if locatie_data:
            return locatie_data.latitude, locatie_data.longitude, locatie_data.address
        else:
            st.error(f"De locatie {locatie} kan niet gevonden worden.")
            return None, None, None
    except GeocoderUnavailable:
        # Als Nominatim niet beschikbaar is, probeer OpenCage
        st.warning("Nominatim is niet beschikbaar, overschakelen naar OpenCage...")
        
        geolocator_opencage = OpenCage(api_key="b1f4bbd95b90415da9c04e261fe331d7")
        try:
            locatie_data = geolocator_opencage.geocode(locatie, exactly_one=True, language="en")
            if locatie_data:
                return locatie_data.latitude, locatie_data.longitude, locatie_data.address
            else:
                st.error(f"De locatie {locatie} kan niet gevonden worden in OpenCage.")
                return None, None, None
        except GeocoderUnavailable:
            st.error("OpenCage is ook niet beschikbaar. Probeer het later opnieuw.")
            return None, None, None
        except Exception as e:
            st.error(f"Er is een onverwachte fout opgetreden: {e}")
            return None, None, None

# Functie om weergegevens op te halen
#@st.cache_data
def get_weather_data_historical(lat, lon, selected_date):
    url = f"https://historical-forecast-api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&start_date={selected_date}&end_date={selected_date}&hourly=temperature_2m,precipitation,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility,wind_speed_10m,wind_speed_80m,wind_speed_120m,wind_speed_180m,wind_direction_10m,wind_direction_180m&daily=sunrise,sunset&timezone=auto&models=best_match"
    response = requests.get(url, headers=API_HEADERS)
    if response.status_code == 200:
        return response.json()
    else:
        st.error("Serverfout bij het ophalen van weergegevens (onderhoud) - probeer het later opnieuw.")
        return None

# Functie om de zonsopgang- en zonsondergangtijd veilig op te halen
def haal_zonsopgang_en_zonsondergang(weather_data):
    if weather_data and "daily" in weather_data:
        daily_data = weather_data["daily"]
        if "sunrise" in daily_data and "sunset" in daily_data:
            # Controleer of de sunrise en sunset data correct zijn
            sunrise = daily_data["sunrise"][0] if daily_data["sunrise"] else None
            sunset = daily_data["sunset"][0] if daily_data["sunset"] else None

            # Als sunrise en sunset aanwezig zijn, verwerk deze gegevens
            if sunrise and sunset:
                return sunrise.split("T")[1][:5], sunset.split("T")[1][:5]
            else:
                st.warning("Zonsopgang- of zonsondergangtijd ontbreekt in de gegevens.")
                return None, None
        else:
            st.warning("De verwachte sleutels voor zoninformatie ontbreken in de API-gegevens.")
            return None, None
    else:
        st.warning("Weergegevens ontbreken of zijn niet correct opgehaald.")
        return None, None

# Functie voor het weergeven van de regels in een mooi formaat (zonder SVG, enkel tekst en iconen)
def format_regel_with_icons(time, temperature, precipitation, cloud_cover_low, cloud_cover_mid, cloud_cover_high, wind_direction, wind_speed_10m, wind_speed_80m, visibility):
    return (
        f"<br>🕒:{time:<4}|🌡️{temperature:>4.1f}°C|🌧️{precipitation:>2.1f}mm|"
        f"☁️L:{cloud_cover_low:>3}%|☁️M:{cloud_cover_mid:>3}%|☁️H:{cloud_cover_high:>3}%|"
        f"🧭:{wind_direction:<3}{wind_speed_10m:>2}Bf|💨@80m:{wind_speed_80m:>2}Bf|👁️:{visibility:>4.1f}km"
    )

# Functie voor het genereren van excel uitvoer
def regels_naar_excel(regels):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # Vervang "<br>" door een lege string en splits regels op het pipe-teken
        data = [regel.replace("<br>", "").split("|") for regel in regels]  # Verwijder <br>
        df = pd.DataFrame(data)  # Zet de gesplitste regels in een DataFrame
        df.to_excel(writer, index=False, sheet_name='Kopieerbare Regels', header=False)
        return output.getvalue()

# Functie om de weerdata op te halen
def get_weather_data_forecast():
    response = requests.get(API_URL)
    if response.status_code == 200:
        return response.json()
    else:
        st.error(f"Error fetching data from API: {response.status_code}")
        return None

# Functie om dataframe op te slaan als Excel
def to_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Gegevens')
        processed_data = output.getvalue()
        return processed_data


# === MIGRATIE RASTER (100 × 100 km, ankerpunt Tarifa) ===

MIGRATIE_ANCHOR_LAT = 36.0    # Tarifa, Spanje
MIGRATIE_ANCHOR_LON = -5.6    # Tarifa, Spanje
MIGRATIE_LAT_STEP   = 1.0     # ≈ 111 km per breedtegraad
MIGRATIE_LON_STEP   = 1.3     # ≈ 100 km op breedtegraad 36°N
MIGRATIE_LAT_MIN    = 35.0
MIGRATIE_LAT_MAX    = 56.0
MIGRATIE_LON_MIN    = -9.5
MIGRATIE_LON_MAX    = 15.3

# 5-daagse voorspelling: vandaag + 5 dagen = 6 kaarten
MIGRATIE_FORECAST_DAYS  = 6
MIGRATIE_FORECAST_HOURS = MIGRATIE_FORECAST_DAYS * 24   # = 144 uurlijkse waarden

# Vlieghoogte-drempelwaarden (km/h)
VLIEGHOOGTE_LAAG_MIN         = 29    # 5–6 Bf: vogels vliegen laag (waarneembaar)
VLIEGHOOGTE_MIDDEL_MIN       = 12    # 3–4 Bf: middelhoogte
VLIEGHOOGTE_GESTOPT_THRESHOLD = 50   # ≥ 7 Bf: trek grotendeels afgeremd

# Kaartcentrum voor BE/NL/DE-weergave
KAART_CENTER_LAT = 50.5
KAART_CENTER_LON = 7.5

# Bounding box voor corridoranalyse BE/NL/DE
CORRIDOR_LAT_MIN = 49.5
CORRIDOR_LAT_MAX = 55.5
CORRIDOR_LON_MIN = 2.5
CORRIDOR_LON_MAX = 15.5

# Bounding box voor BE/NL ZO-wind optimum (vogels gestuwd vanuit centraal-Frankrijk)
# ZO-wind (135°, 3–5 Bf) is de ideale windrichting voor trek langs de Noordzeekust
BENE_LAT_MIN        = 49.5    # zuidgrens BE
BENE_LAT_MAX        = 53.5    # noordgrens NL
BENE_LON_MIN        = 2.0     # westkust BE/NL
BENE_LON_MAX        = 8.0     # oost-NL / ruhr-gebied
BENE_WIND_OPT_DIR   = 135.0   # ideale windrichting ZO (graden)

# Asymmetrisch verval van de windrichtingsscore rond ZO (135°):
#   Richting ZZO/Z (met Z-component): trager verval → ZZO scoort hoger dan OZO
#   Richting OZO/O (met O-component): sneller verval → scoort lager dan ZZO
BENE_WIND_FALLOFF_S = 225.0   # graden: score daalt naar 0 bij W (315°, 180° + 135° = W)
BENE_WIND_FALLOFF_E = 135.0   # graden: score daalt naar 0 bij N (0°, 135° terug van ZO)

# Windkrachtbereiken voor BE/NL (Beaufort → km/h)
BENE_WIND_SPEED_1BF =  1.0    # Bf 1 ondergrens
BENE_WIND_SPEED_3BF = 12.0    # Bf 3 ondergrens (= optimum ondergrens)
BENE_WIND_SPEED_5BF = 38.0    # Bf 5 bovengrens (= optimum bovengrens)
BENE_WIND_SPEED_7BF = 50.0    # Bf 7 ondergrens (= trek grotendeels afgeremd)


def migratie_is_geldig_punt(lat: float, lon: float) -> bool:
    """Return True als het rasterpunt op land valt en niet in het Verenigd Koninkrijk.

    TimezoneFinder retourneert None voor oceanen, maar Etc/GMT* voor open zee.
    Beide worden als 'in zee' beschouwd.
    """
    tz = _TF.timezone_at(lat=lat, lng=lon)
    if tz is None:
        return False          # punt in oceaan / diepe zee
    if tz.startswith("Etc/"):
        return False          # open zee (UTC-offset tijdzones)
    if tz == "Europe/London":
        return False          # Verenigd Koninkrijk
    return True


def migratie_genereer_rasterpunten():
    """Genereer rasterpunten van ~100×100 km met Tarifa als ankerpunt.
    Punten in zee en het Verenigd Koninkrijk worden automatisch uitgefilterd."""
    lats = set()
    n = 0
    while True:
        lat = round(MIGRATIE_ANCHOR_LAT + n * MIGRATIE_LAT_STEP, 1)
        if lat > MIGRATIE_LAT_MAX:
            break
        if lat >= MIGRATIE_LAT_MIN:
            lats.add(lat)
        n += 1
    n = -1
    while True:
        lat = round(MIGRATIE_ANCHOR_LAT + n * MIGRATIE_LAT_STEP, 1)
        if lat < MIGRATIE_LAT_MIN:
            break
        if lat <= MIGRATIE_LAT_MAX:
            lats.add(lat)
        n -= 1

    lons = set()
    n = 0
    while True:
        lon = round(MIGRATIE_ANCHOR_LON + n * MIGRATIE_LON_STEP, 1)
        if lon > MIGRATIE_LON_MAX:
            break
        if lon >= MIGRATIE_LON_MIN:
            lons.add(lon)
        n += 1
    n = -1
    while True:
        lon = round(MIGRATIE_ANCHOR_LON + n * MIGRATIE_LON_STEP, 1)
        if lon < MIGRATIE_LON_MIN:
            break
        if lon <= MIGRATIE_LON_MAX:
            lons.add(lon)
        n -= 1

    punten = []
    for lat in sorted(lats):
        for lon in sorted(lons):
            if migratie_is_geldig_punt(lat, lon):
                punten.append({"latitude": lat, "longitude": lon})
    return punten


def migratie_bereken_score(weer):
    """
    Bereken migratiescore (0.0 = extreem ongunstig, 1.0 = extreem gunstig).

    Gewichten (conform AI-Predictor.md):
      - Windrichting  40 %  (zuidenwind = ideale rugwind voor noordwaartse trek)
      - Neerslag      25 %  (droog = gunstig)
      - Windkracht    15 %  (matige wind = optimaal)
      - Zicht         10 %  (helder = gunstig)
      - Temperatuur   10 %  (8–20 °C = optimaal)
    """
    if not weer:
        return 0.5

    wind_kracht   = float(weer.get("wind_speed_10m", 0))
    wind_richting = float(weer.get("wind_direction_10m", 180))
    temperatuur   = float(weer.get("temperature_2m", 12))
    neerslag      = float(weer.get("precipitation", 0))
    zicht         = float(weer.get("visibility", 10000))

    # Windrichting: zuidenwind (180°) = ideale rugwind voor noordwaartse trek
    # cos(0°) = 1  →  (1-1)/2 = 0 = slecht (noordenwind = tegenstander)
    # cos(180°) = -1  →  (1+1)/2 = 1 = goed (zuidenwind = rugwind)
    wind_richting_score = (1.0 - math.cos(math.radians(wind_richting))) / 2.0

    # Windkracht: optimaal 5–25 km/h
    if wind_kracht <= 5:
        wind_kracht_score = wind_kracht / 5.0
    elif wind_kracht <= 25:
        wind_kracht_score = 1.0
    else:
        wind_kracht_score = max(0.0, 1.0 - (wind_kracht - 25) / 35.0)

    # Neerslag: droog = maximaal gunstig
    neerslag_score = max(0.0, 1.0 - neerslag / 5.0)

    # Zicht: 10 km of meer = maximaal
    zicht_score = min(1.0, zicht / 10000.0)

    # Temperatuur: 8–20 °C = optimaal voor voorjaarstrek
    if 8 <= temperatuur <= 20:
        temp_score = 1.0
    elif temperatuur < 8:
        temp_score = max(0.0, (temperatuur + 5) / 13.0)
    else:
        temp_score = max(0.0, 1.0 - (temperatuur - 20) / 15.0)

    score = (
        0.40 * wind_richting_score
        + 0.15 * wind_kracht_score
        + 0.25 * neerslag_score
        + 0.10 * zicht_score
        + 0.10 * temp_score
    )
    return round(min(1.0, max(0.0, score)), 3)


def migratie_score_naar_klasse(score):
    """Vertaal migratiescore naar tekstlabel."""
    if score >= 0.75:
        return "TOP 🔴"
    elif score >= 0.50:
        return "GOED 🟠"
    elif score >= 0.25:
        return "MATIG 🟡"
    else:
        return "LAAG 🔵"


def migratie_score_naar_kleur(score):
    """Converteer migratiescore naar hex-kleur: rood (gunstig) → blauw (ongunstig)."""
    hue = (1.0 - score) * 240.0 / 360.0   # 0° (rood) bij score=1, 240° (blauw) bij score=0
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def _haal_weer_rasterpunt(punt):
    """Haal actueel weer op voor één rasterpunt (geen Streamlit-aanroepen)."""
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": punt["latitude"],
                "longitude": punt["longitude"],
                "current": (
                    "temperature_2m,wind_speed_10m,wind_direction_10m,"
                    "precipitation,visibility,cloud_cover"
                ),
                "timezone": "UTC",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("current")
    except Exception:
        pass
    return None


@st.cache_data(ttl=1800)
def laad_migratie_rasterdata():
    """
    Haal weerdata op voor alle rasterpunten (gecacheerd voor 30 minuten).
    Gebruikt threadpool voor parallelle API-aanroepen.
    """
    punten = migratie_genereer_rasterpunten()

    def verwerk_punt(punt):
        weer = _haal_weer_rasterpunt(punt)
        score = migratie_bereken_score(weer)
        wind_richting_txt = ""
        wind_kracht_txt   = ""
        temp_txt          = "?"
        neerslag_txt      = "?"
        if weer:
            wind_richting_txt = graden_naar_windrichting(float(weer.get("wind_direction_10m", 0)))
            wind_kracht_txt   = kmh_naar_beaufort(float(weer.get("wind_speed_10m", 0)))
            temp_txt          = f"{float(weer.get('temperature_2m', 0)):.1f}"
            neerslag_txt      = f"{float(weer.get('precipitation', 0)):.1f}"
        return {
            "latitude":      punt["latitude"],
            "longitude":     punt["longitude"],
            "score":         score,
            "klasse":        migratie_score_naar_klasse(score),
            "kleur":         migratie_score_naar_kleur(score),
            "wind_richting": wind_richting_txt,
            "wind_kracht":   wind_kracht_txt,
            "temperatuur":   temp_txt,
            "neerslag":      neerslag_txt,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        resultaten = list(executor.map(verwerk_punt, punten))

    opgehaald_om = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return resultaten, opgehaald_om


# === UITGEBREIDE 5-DAAGSE MIGRATIEVOORSPELLING ===

# Nederlandse dag- en maandafkortingen voor datumopmaak
_NL_WEEKDAGEN = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]
_NL_MAANDEN   = ["jan", "feb", "mrt", "apr", "mei", "jun",
                  "jul", "aug", "sep", "okt", "nov", "dec"]


def _dag_label_nl(d: date) -> str:
    return f"{_NL_WEEKDAGEN[d.weekday()]} {d.day} {_NL_MAANDEN[d.month - 1]} {d.year}"


def migratie_vlieghoogte(wind_speed_kmh: float) -> tuple[str, str, int]:
    """
    Bepaal de verwachte vlieghoogte van trekvogels op basis van windkracht.

    Hogere wind (5–6 Bf, 29–49 km/h) duwt vogels naar lagere vlieghoogtes,
    waardoor ze beter waarneembaar zijn. Bij weinig wind op gunstige trekdagen
    vliegen vogels juist hoog en worden ze minder opgemerkt.

    Returns (label, toelichting, marker_radius).
      0–2 Bf (< 12 km/h)    : hoog   — moeilijk te zien      → kleine cirkel
      3–4 Bf (12–28 km/h)   : middel — matig zichtbaar        → middel cirkel
      5–6 Bf (29–49 km/h)   : laag   — goed waarneembaar      → grote cirkel
      ≥ 7 Bf (≥ 50 km/h)    : gestopt — trek afgeremd         → kleine cirkel
    """
    if wind_speed_kmh >= VLIEGHOOGTE_GESTOPT_THRESHOLD:
        return "Trek beperkt ⛔", "Wind ≥ 7 Bf — trek grotendeels afgeremd", 4
    elif wind_speed_kmh >= VLIEGHOOGTE_LAAG_MIN:
        return "Laag 🔽", "Wind 5–6 Bf — vogels vliegen laag, goed waarneembaar", 10
    elif wind_speed_kmh >= VLIEGHOOGTE_MIDDEL_MIN:
        return "Middel ↕️", "Wind 3–4 Bf — middelhoogte, matig zichtbaar", 7
    else:
        return "Hoog 🔼", "Wind 0–2 Bf — vogels vliegen hoog, minder zichtbaar", 5


def _haal_weer_forecast_rasterpunt(punt: dict) -> dict | None:
    """Haal 6-daagse uurlijkse weervoorspelling op voor één rasterpunt."""
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":  punt["latitude"],
                "longitude": punt["longitude"],
                "hourly": (
                    "temperature_2m,wind_speed_10m,wind_direction_10m,"
                    "precipitation,visibility,cloud_cover,"
                    "pressure_msl,cape,boundary_layer_height"
                ),
                "timezone": "UTC",
                "forecast_days": 6,
            },
            timeout=20,
        )
        if resp.status_code == 200:
            return resp.json().get("hourly")
    except Exception:
        pass
    return None


def migratie_bereken_score_uitgebreid(
    weer: dict | None,
    lat: float = 0.0,
    lon: float = 0.0,
) -> float:
    """
    Bereken migratiescore (0.0–1.0) inclusief luchtdruk, thermiek en regenfronten.

    Gewichten:
      35 % windrichting   (regionaal gecorrigeerd — zie hieronder)
      20 % neerslag       (droog = gunstig; regenfronten = stoppers)
      10 % luchtdruk MSL  (hogedrukgebied > 1015 hPa = stabiele omstandigheden)
      10 % zicht
      10 % windkracht     (regionaal gecorrigeerd — zie hieronder)
       5 % temperatuur    (8–20 °C)
       5 % grenslaagdikte (BLH > 1500 m = goede thermiek voor zwevers & roofvogels)
       5 % CAPE           (convectieve beschikbare energie = thermiekindicator)

    Regionale correctie BE/NL (BENE_LAT/LON_MIN/MAX):
      De beste trekdagen voor België en Nederland worden bepaald door ZO-wind
      (≈ 135°, 3–5 Bf). Vogels worden dan vanuit centraal-Frankrijk naar de
      Noordzeekust gestuwd. De windrichting-formule verschuift het optimum van
      180° (Z, algemeen) naar 135° (ZO, BE/NL):
        score = (1 - cos(wind_richting + 45°)) / 2  → max bij 135°
      De windkracht-drempel verschuift naar 3–5 Bf (12–38 km/h).
    """
    if not weer:
        return 0.5

    wind_kracht   = float(weer.get("wind_speed_10m", 0))
    wind_richting = float(weer.get("wind_direction_10m", 180))
    temperatuur   = float(weer.get("temperature_2m", 12))
    neerslag      = float(weer.get("precipitation", 0))
    zicht         = float(weer.get("visibility", 10000))
    druk          = float(weer.get("pressure_msl", 1013))
    cape          = float(weer.get("cape", 0))
    blh           = float(weer.get("boundary_layer_height", 500))

    in_bene = (
        BENE_LAT_MIN <= lat <= BENE_LAT_MAX
        and BENE_LON_MIN <= lon <= BENE_LON_MAX
    )

    if in_bene:
        # --- BE/NL asymmetric wind direction score ---
        # Peak at ZO (135°). Angular distance from ZO:
        #   positive δ  = clockwise toward ZZO → Z → ZW → W  (southerly component)
        #   negative δ  = counter-clockwise toward OZO → O → N (easterly component)
        # Slower decay toward the south (ZZO scores higher than OZO at equal angular
        # distance), faster decay toward the east, so:
        #   ZO (135°) > ZZO (157.5°) > OZO (112.5°) > Z (180°) > O (90°) > N/W ≈ 0
        delta = ((wind_richting - BENE_WIND_OPT_DIR) + 180.0) % 360.0 - 180.0
        if delta >= 0:
            # South side: reach 0 at BENE_WIND_FALLOFF_S degrees past ZO (= near W)
            wind_richting_score = max(
                0.0, math.cos(math.radians(delta * 180.0 / BENE_WIND_FALLOFF_S))
            )
        else:
            # East side: reach 0 at BENE_WIND_FALLOFF_E degrees before ZO (= near N)
            wind_richting_score = max(
                0.0, math.cos(math.radians(abs(delta) * 180.0 / BENE_WIND_FALLOFF_E))
            )

        # --- BE/NL tiered wind speed score ---
        # Prioriteiten (beste → slechtste):
        #   3–5 Bf (12–38 km/h) = optimaal  → score 1.0
        #   1–3 Bf  (1–12 km/h) = goed      → score 0.2 oplopend naar 1.0
        #   0  Bf   (< 1 km/h)  = kalm      → score 0.2  (vogels vliegen hoog)
        #   6  Bf  (38–50 km/h) = afnemend  → score 1.0 → 0.3
        #   7+ Bf  (≥ 50 km/h)  = afgeremd  → score ≤ 0.3
        if wind_kracht < BENE_WIND_SPEED_1BF:
            wind_kracht_score = 0.2
        elif wind_kracht < BENE_WIND_SPEED_3BF:
            wind_kracht_score = 0.2 + (
                (wind_kracht - BENE_WIND_SPEED_1BF)
                / (BENE_WIND_SPEED_3BF - BENE_WIND_SPEED_1BF)
            ) * 0.8
        elif wind_kracht <= BENE_WIND_SPEED_5BF:
            wind_kracht_score = 1.0
        elif wind_kracht < BENE_WIND_SPEED_7BF:
            wind_kracht_score = max(
                0.3, 1.0 - (wind_kracht - BENE_WIND_SPEED_5BF)
                / (BENE_WIND_SPEED_7BF - BENE_WIND_SPEED_5BF) * 0.7
            )
        else:
            wind_kracht_score = max(0.0, 0.3 - (wind_kracht - BENE_WIND_SPEED_7BF) / 30.0)
    else:
        # Algemeen: Z-wind (180°) = ideale rugwind; N (0°/360°) = tegenwind
        wind_richting_score = (1.0 - math.cos(math.radians(wind_richting))) / 2.0

        # Algemeen: 5–25 km/h = optimaal
        if wind_kracht <= 5:
            wind_kracht_score = wind_kracht / 5.0
        elif wind_kracht <= 25:
            wind_kracht_score = 1.0
        else:
            wind_kracht_score = max(0.0, 1.0 - (wind_kracht - 25) / 35.0)

    # Neerslag: droog = gunstig; regenfronten veroorzaken stoppers
    neerslag_score = max(0.0, 1.0 - neerslag / 5.0)

    # Zicht
    zicht_score = min(1.0, zicht / 10000.0)

    # Temperatuur: 8–20 °C = optimaal voor voorjaarstrek
    if 8 <= temperatuur <= 20:
        temp_score = 1.0
    elif temperatuur < 8:
        temp_score = max(0.0, (temperatuur + 5) / 13.0)
    else:
        temp_score = max(0.0, 1.0 - (temperatuur - 20) / 15.0)

    # Luchtdruk: hogedrukgebied gunstig — 1025+ hPa ≈ 1.0, < 995 hPa ≈ 0.0
    druk_score = max(0.0, min(1.0, (druk - 995.0) / 30.0))

    # Grenslaagdikte (BLH): hoge waarden = goede thermiek voor zwevers
    blh_score = min(1.0, blh / 1500.0)

    # CAPE: matige convectieve energie gunstig voor thermiekzwevers;
    # te hoog (> 1500 J/kg) = onweersrisico
    if cape <= 0:
        cape_score = 0.2
    elif cape <= 500:
        cape_score = 0.4 + (cape / 500.0) * 0.5
    elif cape <= 1500:
        cape_score = 0.9 - ((cape - 500) / 1000.0) * 0.5
    else:
        cape_score = max(0.0, 0.4 - (cape - 1500) / 1500.0)

    # BE/NL: windrichting weegt zwaarder (40 %) en windkracht lichter (5 %)
    # zodat ZO 1–3Bf boven ZZO 3–5Bf uitkomt — richting is dé discriminator.
    # Algemeen: 35 % windrichting, 10 % windkracht.
    if in_bene:
        score = (
            0.40 * wind_richting_score
            + 0.05 * wind_kracht_score
            + 0.20 * neerslag_score
            + 0.10 * zicht_score
            + 0.05 * temp_score
            + 0.10 * druk_score
            + 0.05 * blh_score
            + 0.05 * cape_score
        )
    else:
        score = (
            0.35 * wind_richting_score
            + 0.10 * wind_kracht_score
            + 0.20 * neerslag_score
            + 0.10 * zicht_score
            + 0.05 * temp_score
            + 0.10 * druk_score
            + 0.05 * blh_score
            + 0.05 * cape_score
        )
    return round(min(1.0, max(0.0, score)), 3)


@st.cache_data(ttl=1800)
def laad_migratie_rasterdata_6daags():
    """
    Haal 6-daagse weervoorspelling op voor alle geldige rasterpunten
    (vandaag + 5 dagen). Zeepunten en het VK zijn al uitgefilterd door
    migratie_genereer_rasterpunten(). Retourneert
    (days_data, dag_datums, opgehaald_om).
    days_data[i] = lijst van punt-dicts op basis van middagwaarden (12:00 UTC).
    """
    punten = migratie_genereer_rasterpunten()
    vandaag = date.today()
    dag_datums = [_dag_label_nl(vandaag + timedelta(days=i)) for i in range(MIGRATIE_FORECAST_DAYS)]

    def verwerk_punt(punt: dict) -> list[dict]:
        hourly = _haal_weer_forecast_rasterpunt(punt)
        dag_punten = []
        for dag_idx in range(MIGRATIE_FORECAST_DAYS):
            middag_idx = dag_idx * 24 + 12   # 12:00 UTC per dag
            if hourly:
                try:
                    cape_lijst = hourly.get("cape") or [0] * MIGRATIE_FORECAST_HOURS
                    blh_lijst  = hourly.get("boundary_layer_height") or [500] * MIGRATIE_FORECAST_HOURS
                    weer = {
                        "temperature_2m":       hourly["temperature_2m"][middag_idx],
                        "wind_speed_10m":        hourly["wind_speed_10m"][middag_idx],
                        "wind_direction_10m":    hourly["wind_direction_10m"][middag_idx],
                        "precipitation":         hourly["precipitation"][middag_idx],
                        "visibility":            hourly["visibility"][middag_idx],
                        "cloud_cover":           hourly["cloud_cover"][middag_idx],
                        "pressure_msl":          hourly["pressure_msl"][middag_idx],
                        "cape":                  cape_lijst[middag_idx],
                        "boundary_layer_height": blh_lijst[middag_idx],
                    }
                except (IndexError, KeyError, TypeError):
                    weer = None
            else:
                weer = None

            score = migratie_bereken_score_uitgebreid(
                weer, lat=punt["latitude"], lon=punt["longitude"]
            )
            in_bene = (
                BENE_LAT_MIN <= punt["latitude"] <= BENE_LAT_MAX
                and BENE_LON_MIN <= punt["longitude"] <= BENE_LON_MAX
            )
            wind_richting_txt = ""
            wind_kracht_txt   = ""
            temp_txt          = "?"
            neerslag_txt      = "?"
            druk_txt          = "?"
            blh_txt           = "?"
            vlieghoogte_lbl   = "?"
            vlieghoogte_tip   = ""
            marker_radius     = 7
            if weer:
                wind_speed_raw = float(weer.get("wind_speed_10m", 0))
                wind_richting_txt = graden_naar_windrichting(
                    float(weer.get("wind_direction_10m", 0))
                )
                wind_kracht_txt = kmh_naar_beaufort(wind_speed_raw)
                temp_txt     = f"{float(weer.get('temperature_2m', 0)):.1f}"
                neerslag_txt = f"{float(weer.get('precipitation', 0)):.1f}"
                druk_txt     = f"{float(weer.get('pressure_msl', 1013)):.0f}"
                blh_txt      = f"{int(float(weer.get('boundary_layer_height', 0)))}"
                vlieghoogte_lbl, vlieghoogte_tip, marker_radius = migratie_vlieghoogte(
                    wind_speed_raw
                )
            dag_punten.append({
                "latitude":         punt["latitude"],
                "longitude":        punt["longitude"],
                "score":            score,
                "klasse":           migratie_score_naar_klasse(score),
                "kleur":            migratie_score_naar_kleur(score),
                "wind_richting":    wind_richting_txt,
                "wind_kracht":      wind_kracht_txt,
                "temperatuur":      temp_txt,
                "neerslag":         neerslag_txt,
                "druk":             druk_txt,
                "blh":              blh_txt,
                "vlieghoogte":      vlieghoogte_lbl,
                "vlieghoogte_tip":  vlieghoogte_tip,
                "marker_radius":    marker_radius,
                "be_nl_zone":       in_bene,
            })
        return dag_punten

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        alle_punt_resultaten = list(executor.map(verwerk_punt, punten))

    # Herorganiseer: [punt_idx][dag_idx] → [dag_idx][punt_idx]
    days_data: list[list[dict]] = [[] for _ in range(MIGRATIE_FORECAST_DAYS)]
    for punt_resultaten in alle_punt_resultaten:
        for dag_idx, dag_punt in enumerate(punt_resultaten):
            days_data[dag_idx].append(dag_punt)

    opgehaald_om = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return days_data, dag_datums, opgehaald_om


# Controleer wijzigingen in invoer (gebruik session_state)
if "last_locatie" not in st.session_state:
    st.session_state.last_locatie = default_locatie
    st.session_state.last_datum = default_datum
    st.session_state.last_hours = default_hours

# Update alleen bij wijziging van locatie, datum of uren
if (
        locatie_keuze != st.session_state.last_locatie
        or geselecteerde_datum != st.session_state.last_datum
        or default_hours != st.session_state.last_hours
):
    lat, lon, adres = toon_geolocatie_op_kaart(f"{locatie_keuze}, {land_keuze}")
    if lat and lon:
        gps_format = f"{round(lat, 2)}°{'N' if lat >= 0 else 'S'} {round(lon, 2)}°{'E' if lon >= 0 else 'W'}"
        weather_data = get_weather_data_historical(lat, lon, geselecteerde_datum)
        st.session_state.last_locatie = locatie_keuze
        st.session_state.last_datum = geselecteerde_datum
        st.session_state.last_hours = default_hours
        st.session_state.weather_data = weather_data
        st.session_state.lat = lat
        st.session_state.lon = lon
        st.session_state.adres = adres
        st.session_state.gps_format = gps_format

# Toon GPS-gegevens en tijden in de sidebar
if "gps_format" in st.session_state:

    # Splits de string op basis van de komma's
    adresdelen = st.session_state.adres.split(',')

    # Haal de eerste (stad) en de laatste (land) delen van het adres
    stad = adresdelen[0].strip()  # Bruges
    land = adresdelen[-1].strip()  # Belgium


    # Haal zonsopgang- en zonsondergangtijden op
    if "weather_data" in st.session_state:
        weather_data = st.session_state.weather_data
        sunrise, sunset = haal_zonsopgang_en_zonsondergang(weather_data)

        if sunrise and sunset:
            st.sidebar.markdown(
                f"""
                <div style="display: flex; justify-content: space-between; align-items: center; font-size: 16px;">
                    <div style="display: flex; align-items: center;">
                        <i class="fas fa-sun" style="color: orange; margin-right: 8px;"></i>
                        <span><b>{sunrise}</b></span>
                    </div>
                    <div style="display: flex; align-items: center;">
                        <i class="fas fa-moon" style="color: lightblue; margin-right: 8px;"></i>
                        <span><b>{sunset}</b></span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        if "lat" in st.session_state and "lon" in st.session_state:
            # Maak een nieuwe kaart met de opgegeven coördinaten
            m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=9)

            # Maak een marker met een groene kleur en een rood 'binocular' icoon
            #marker = folium.Marker(
            #    location=[st.session_state.lat, st.session_state.lon],
            #    icon=Icon(icon="fa-binoculars", prefix='fa', color='green', icon_color='white')
            #    # Font Awesome 'binoculars' icoon
            # Gebruik een aangepaste afbeelding als icoon
            icon_path = 'images//Milvus1.png'  # Vervang dit door een URL of pad naar jouw afbeelding
            eagle_icon = folium.CustomIcon(icon_path, icon_size=(25, 38))

            # Voeg de marker toe aan de kaart
            marker1 = folium.Marker(
                location=[st.session_state.lat, st.session_state.lon],
                icon=eagle_icon, icon_anchor=(12.5, 38),
                popup=locatie_keuze
            ).add_to(m)

            # Voeg de marker toe aan de kaart
            #marker1.add_to(m)

            # Toon de kaart in Streamlit
            #st_folium(m, width=700, height=250)
        with st.sidebar:
             st_folium(m, width=300, height=300)  # Pas grootte hier aan

# Functie om opnames op te halen van xeno-canto
def get_recordings(genus, lat, lon, max_results=6):
    query = f'gen:{genus} type:"flight call" q:A'
    url = f'https://xeno-canto.org/api/2/recordings?query={query}'
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        recordings = data.get('recordings', [])[:max_results]
        return recordings
    else:
        return []


# Titel en beschrijving boven de tabbladen
st.title("Bird Migration Weather Tool")
st.markdown("""
Welkom bij het interactieve weergegevens dashboard. 
Gebruik de tabbladen hieronder om de gegevens te verkennen en aan te passen naar wens.
""")


# Hoofdvenster met tabbladen
#tabs = st.tabs(["Weergegevens", "Voorspellingen", "Vliegbeelden", "Geluiden-zangvogels", "Geluiden-steltlopers", "CROW project", "BIRDTAM project", "Trektellen.nl (read only)", "Crane Radar", "Gebruiksaanwijzing"])
tabs = st.tabs(["Weergegevens", "Voorspellingen", "🦅 Migratie Raster", "CROW project", "Kraanvogel Radar", "🎧 Vluchtroepen","Gebruiksaanwijzing"])


# Tab 0: Weergeven van de gegevens
# Tab 0: Weergeven van de gegevens
with tabs[0]: #dit is het meest linkse tabblad
    # Data ophalen en verwerken
    if "weather_data" in st.session_state:
        weather_data = st.session_state.weather_data

        # Maak een DataFrame van de weergegevens
        weather_df = pd.DataFrame(weather_data["hourly"])
        weather_df["time"] = weather_df["time"].str.split("T").str[1]

        # Default slider range van 08:00 tot 18:00 uur
        default_start = 5  # 05:00 uur
        default_end = 22   # 22:00 uur
        if "last_hours" not in st.session_state:
            st.session_state.last_hours = default_hours  # Zorg ervoor dat er altijd een standaardwaarde is
        # Verkrijg het tijdsbereik van de slider in de sidebar (default tussen 08:00 en 18:00 uur)

        # Controleer of de sliderwaarden van start_end veranderd zijn
        if (
                locatie_keuze != st.session_state.last_locatie
                or geselecteerde_datum != st.session_state.last_datum
                or default_hours != st.session_state.last_hours
        ):
            lat, lon, adres = toon_geolocatie_op_kaart(f"{locatie_keuze}, {land_keuze}")
            if lat and lon:
                gps_format = f"{round(lat, 2)}°{'N' if lat >= 0 else 'S'} {round(lon, 2)}°{'E' if lon >= 0 else 'W'}"
                weather_data = get_weather_data_historical(lat, lon, geselecteerde_datum)

                # Update de session_state met de nieuwe waarden
                st.session_state.last_locatie = locatie_keuze
                st.session_state.last_datum = geselecteerde_datum
                st.session_state.last_hours = start_end
                st.session_state.weather_data = weather_data
                st.session_state.lat = lat
                st.session_state.lon = lon
                st.session_state.adres = adres
                st.session_state.gps_format = gps_format


        start_end = st.sidebar.slider("Selecteer het tijdsbereik", 0, 23, (default_start, default_end), format = "%d:00", key="sidebaronder")
        #min_value = 0,
        #max_value = 23,
        #value = default_hours,
        #format = "%d:00",
        st.sidebar.write(f"**{land}**, {stad}")
        st.sidebar.write(f"**GPS:** {st.session_state.gps_format}")
        #st.sidebar.write(f"{lat}, {lon}")
        #st.sidebar.write(f"{st.session_state.lat}, {st.session_state.lon}")

        # Filter de gegevens op basis van de slider
        filtered_data = weather_df.iloc[start_end[0]:start_end[1] + 1]

        # Maak een lijst van de kopieerbare regels
        kopieerbare_regels = [
            format_regel_with_icons(
                pd.to_datetime(row['time'], format='%H:%M').strftime('%H:%M'),
                row['temperature_2m'], row['precipitation'],
                row['cloud_cover_low'], row['cloud_cover_mid'], row['cloud_cover_high'],
                graden_naar_windrichting(row['wind_direction_10m']),
                kmh_naar_beaufort(row['wind_speed_10m']),
                kmh_naar_beaufort(row['wind_speed_80m']),
                row['visibility'] / 1000
            )
            for _, row in filtered_data.iterrows()
        ]


# Gebruiker kiest hoe gegevens worden gekopieerd
        kopieer_optie = st.radio("Hoe wil je de gegevens kopiëren?", ["Alles in één blok", "Regel per regel"])

        if kopieer_optie == "Alles in één blok":
            # Combineer alle regels in één tekstblok en toon het als code
            alle_regels_text = "\n".join(kopieerbare_regels)
            st.code(alle_regels_text, language="text")  # Gebruik st.code() voor kopieerbare tekst

        elif kopieer_optie == "Regel per regel":
            # Toon elke regel apart zonder extra ruimte
            for regel in kopieerbare_regels:
                # Gebruik st.markdown voor inline weergave en st.code voor kopieerbare tekst
                st.code(regel, language="text")  # Zorg ervoor dat elke regel apart gekopieerd kan worden


        # Controleer of er regels zijn
        if kopieerbare_regels:
            # Exporteer de regels naar Excel
            excel_data = regels_naar_excel(kopieerbare_regels)

            # Downloadknop voor Excel
            st.download_button(
                label="Export Excel",
                data=excel_data,
                file_name="kopieerbare_regels.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.write("Geen regels beschikbaar om te exporteren.")


# hier de code voor het tweede tabblad (voorspellingen)
with tabs[1]:
    st.text("""
        Handleiding voor de Windy widget:
        Kies een locatie naar keuze om onderaan de weerkaart de meest recente voorspellingen te verkrijgen.
        Zoom in of uit op de kaart om een preciezere locatie te kiezen voor deze voorspellingen.
        Zoom uit om een breder weerbeeld te verkrijgen.
        Klik bovenaan rechts om andere lagen te verkrijgen (bewolking, temperatuur, wind, sateliet, ...)"""
    )

    # Beschikbare overlays en de corresponderende Windy API-waarden
    overlays = {
        "Wind": "wind",
        "Mist": "fog",
        "Lage bewolking": "lclouds",
        "Middelbare bewolking": "mclouds",
        "Neerslag": "rain",
        "Thermiek": "ccl",
        "Zicht": "visibility"
    }

    # Initieer session_state als het nog niet bestaat
    if "windy_overlay" not in st.session_state:
        st.session_state.windy_overlay = "Wind"  # Standaard overlay

    # Callback functie om de overlay aan te passen
    def update_windy():
        st.session_state.windy_overlay = st.session_state.overlay_select

    # Dropdown voor overlay-selectie met `on_change`
    st.selectbox(
        "Kies een overlay:", 
        list(overlays.keys()), 
        index=list(overlays.keys()).index(st.session_state.windy_overlay),
        key="overlay_select", 
        on_change=update_windy
    )

    # Controleer of sessiestatus waarden bevat
    if "lat" not in st.session_state or "lon" not in st.session_state:
        st.error("Latitude en Longitude zijn niet ingesteld. Stel eerst een locatie in.")
    else:
        # Haal waarden op uit sessiestatus
        latitude = st.session_state.lat
        longitude = st.session_state.lon

        # API-aanroep voor weersvoorspellingen
        API_URL = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={latitude}"
            f"&longitude={longitude}"
            "&hourly=temperature_2m,precipitation,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,"
            "visibility,wind_speed_10m,wind_speed_80m,wind_direction_10m"
            "&daily=sunrise,sunset"
            "&timezone=auto"
            "&past_days=0"
            "&forecast_days=16"
        )

        # Haal de weerdata op
        weather_data_forecast = get_weather_data_forecast()

        # Haal latitude en longitude op uit session_state of stel defaults in
        lat = st.session_state.get("lat", 50.681)  # Standaardwaarde als lat niet is ingesteld
        lon = st.session_state.get("lon", 4.768)   # Standaardwaarde als lon niet is ingesteld

        # Maak de dynamische Windy widget URL
        windy_url = f"https://embed.windy.com/embed.html?type=map&location=coordinates&metricRain=mm&metricTemp=°C&metricWind=bft&zoom=7&overlay={overlays[st.session_state.windy_overlay]}&product=ecmwf&level=surface&lat={lat}&lon={lon}&detailLat={lat}&detailLon={lon}&detail=true&pressure=true"

        # Streamlit Iframe in Markdown
        st.markdown(
            f"""
            <iframe width="100%" height="1000" src="{windy_url}" frameborder="0"></iframe>
            """,
            unsafe_allow_html=True
        )

        if weather_data_forecast:
            # Toon de dagelijkse voorspelling
            hourly_data = weather_data_forecast['hourly']

            # Functie om windrichting te converteren naar een compasrichting
            def richting_to_compas(graden):
                richtingen = ['N', 'NNO', 'NO', 'ONO', 'O', 'OZO', 'ZO', 'ZZO', 'Z', 'ZZW', 'ZW', 'WZW', 'W', 'WNW', 'NW', 'NNW']
                index = int((graden % 360) / 22.5)  # Elke richting dekt 22.5 graden
                return richtingen[index]

            # Zet de data om naar een DataFrame
            hourly_df = pd.DataFrame({
                'Time': pd.to_datetime(hourly_data['time']),
                'Temperatuur (°C)': [f"{temp:.1f} °C" for temp in hourly_data['temperature_2m']],
                'Neerslag (mm)': [f"{rain:.1f}mm" for rain in hourly_data['precipitation']],
                'Bewolking Laag (%)': [f"{cloud:.0f}%" for cloud in hourly_data['cloud_cover_low']],
                'Bewolking Middel (%)': [f"{cloud:.0f}%" for cloud in hourly_data['cloud_cover_mid']],
                'Bewolking Hoog (%)': [f"{cloud:.0f}%" for cloud in hourly_data['cloud_cover_high']],
                'Bewolking (%)': [f"{cloud:.0f}%" for cloud in hourly_data['cloud_cover']],
                'Wind Richting': [richting_to_compas(dir) for dir in hourly_data['wind_direction_10m']],
                'Windkracht op 10m (Bf)': [kmh_naar_beaufort(snelheid) for snelheid in hourly_data['wind_speed_10m']],
                'Windkracht op 80m (Bf)': [kmh_naar_beaufort(snelheid) for snelheid in hourly_data['wind_speed_80m']],
                'Zichtbaarheid (km)': [f"{int(vis / 1000)} km" for vis in hourly_data['visibility']]
            })

            # Voeg datum en uur toe
            hourly_df['Datum'] = hourly_df['Time'].dt.date
            hourly_df['Uur'] = hourly_df['Time'].dt.strftime('%H:%M')

            # Kolomtitels aanpassen met iconen
            hourly_df = hourly_df.rename(columns={
                'Temperatuur (°C)': '🌡️ °C',
                'Neerslag (mm)': '🌧️ mm',
                'Bewolking Laag (%)': '☁️@Low %',
                'Bewolking Middel (%)': '☁️@Mid %',
                'Bewolking Hoog (%)': '☁️@High %',
                'Bewolking (%)': '☁️@tot %',
                'Wind Richting': '🧭',
                'Windkracht op 10m (Bf)': '💨@10m',
                'Windkracht op 80m (Bf)': '💨@80m',
                'Zichtbaarheid (km)': '👁️ km'
            })

            # Streamlit Titel
            st.title("Weergegevens per Uur")

            # Multiselect voor kolommen
            beschikbare_kolommen = [col for col in hourly_df.columns if col not in ['Datum', 'Uur']]
            geselecteerde_kolommen = st.multiselect(
                "Selecteer de kolommen die je wilt zien (en in welke volgorde)",
                beschikbare_kolommen,
                default=beschikbare_kolommen
            )

            if geselecteerde_kolommen:
                geselecteerde_kolommen = ['Uur'] + geselecteerde_kolommen
                ordered_df = hourly_df[['Datum'] + geselecteerde_kolommen].copy()

                def highlight_windrichting(rij):
                    kleur = ''
                    richting = rij.get('🧭')

                    if richting == 'NNO':
                        kleur = 'background-color: #e0ffb2'
                    elif richting == 'NO':
                        kleur = 'background-color: #ffde7f'
                    elif richting == 'ONO':
                        kleur = 'background-color: #fff671'
                    elif richting == 'O':
                        kleur = 'background-color: #ffe853'
                    elif richting == 'OZO':
                        kleur = 'background-color: #ff4d00'
                    if richting == 'ZO':
                        kleur = 'background-color: #ff4d00'
                    elif richting == 'ZZO':
                        kleur = 'background-color: #ff4d00'
                    elif richting == 'Z':
                        kleur = 'background-color: #ffe853'
                    elif richting == 'ZZW':
                        kleur = 'background-color: #e8ff7f'
                    elif richting == 'ZW':
                        kleur = 'background-color: #e0ffb2'

                    if kleur:
                        return [kleur] * len(rij)
                    else:
                        return [''] * len(rij)

                # Toon per dag gegroepeerd
                for day, group in ordered_df.groupby('Datum'):
                    st.write(f"### **{day}**")
                    styled_group = group.drop(columns='Datum').style.apply(highlight_windrichting, axis=1)
                    st.dataframe(styled_group, use_container_width=True)
            else:
                st.write("Selecteer ten minste één kolom om te tonen.")


with tabs[2]:
    st.header("🦅 Migratie Raster — 5-Daagse Voorspelling")
    st.markdown("""
    Vijfdaagse migratievoorspelling op basis van weergegevens voor een **~100 × 100 km raster**
    over **België, Nederland en Duitsland** (en omgeving).
    Rasterpunten in zee en het Verenigd Koninkrijk worden buiten beschouwing gelaten.

    **Ankerpunt:** Tarifa (Spanje) — de klassieke doortochtpoort vanuit Afrika (Gibraltar-corridor).

    **Wetenschappelijke factoren (waarden op 12:00 UTC):**
    - 🧭 **Windrichting** (35 %): zuidenwind = rugwind voor noordwaartse voorjaarstrek
    - 🌧️ **Neerslag** (20 %): droog = gunstig — regenfronten zorgen voor stoppers
    - 📊 **Luchtdruk** (10 %): hogedrukgebied (> 1015 hPa) = stabiele omstandigheden
    - 👁️ **Zicht** (10 %): helder zicht = gunstig
    - 💨 **Windkracht** (10 %): matige wind (5–25 km/h) = optimaal voor trek
    - 🌡️ **Temperatuur** (5 %): 8–20 °C = optimaal voor voorjaarstrek
    - 🌀 **Grenslaagdikte / BLH** (5 %): > 1500 m = goede thermiek voor zwevers & roofvogels
    - ⛈️ **CAPE** (5 %): convectieve beschikbare energie — thermiekindicator voor ooievaars, buizerds…

    🌟 **BE/NL regiocorrectie** (zone 49.5–53.5°N, 2–8°E) — vogels gestuwd vanuit centraal-Frankrijk
    naar de Noordzeekust. Windrichting- én windkrachtscore zijn aangepast:

    | Prioriteit | Windrichting | Windkracht |
    |:---:|:---:|:---:|
    | 1 | ZO (135°) | 3–5 Bf (12–38 km/h) |
    | 2 | ZO (135°) | 1–3 Bf (1–12 km/h) |
    | 3 | ZZO (157.5°) | 3–5 Bf |
    | 4 | ZZO (157.5°) | 1–3 Bf |
    | 5 | OZO (112.5°) | 3–5 Bf |
    | 6 | OZO (112.5°) | 1–3 Bf |
    | 7 | elke Z- of O-component | — |

    Technisch: asymmetrische cosinus gecentreerd op 135° — trager verval naar ZZO/Z,
    sneller verval naar OZO/O, zodat de volgorde ZO > ZZO > OZO > Z > O gegarandeerd is.

    **Vlieghoogte & zichtbaarheid (cirkelgrootte op de kaart):**
    Op *gunstige trekdagen met weinig wind* vliegen vogels **hoog** en worden ze minder opgemerkt.
    Een hogere windkracht (< 7 Bf) duwt vogels naar **lagere hoogtes** en maakt ze beter waarneembaar.
    De cirkelgrootte geeft dit aan: 🔵 *groot* = vogels laag & zichtbaar · 🔵 *klein* = vogels hoog of trek beperkt.

    **Kleurschaal:** 🔴 TOP ≥ 75 · 🟠 GOED 50–75 · 🟡 MATIG 25–50 · 🔵 LAAG < 25

    *Gegevens gecacheerd voor 30 minuten. Klik op "Ververs nu" voor actuele data.*
    """)

    _, col_btn = st.columns([5, 1])
    with col_btn:
        if st.button("🔄 Ververs nu", key="ververs_raster_6d"):
            laad_migratie_rasterdata_6daags.clear()
            st.rerun()

    with st.spinner("Weervoorspelling ophalen voor 6-daags migratieraster — even geduld..."):
        days_data, dag_datums, opgehaald_om = laad_migratie_rasterdata_6daags()

    n_punten = len(days_data[0]) if days_data else 0
    st.caption(
        f"⏱️ Gegevens opgehaald om **{opgehaald_om} UTC** — "
        f"{n_punten} rasterpunten per dag (zee & VK uitgesloten) · "
        f"{MIGRATIE_LAT_STEP:.0f}° × {MIGRATIE_LON_STEP}° ≈ 100 × 100 km"
    )

    # Gedeelde kleurlegende (eenmalig boven alle 6 kaarten)
    st.markdown(
        """
        <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:center;
                    margin-bottom:8px;font-size:14px;">
          <span><span style="background:#ff0000;padding:2px 12px;border-radius:4px;
                color:white;">●</span>&nbsp;TOP ≥ 75</span>
          <span><span style="background:#ffaa00;padding:2px 12px;border-radius:4px;
                color:white;">●</span>&nbsp;GOED 50–75</span>
          <span><span style="background:#aaff00;padding:2px 12px;border-radius:4px;
                color:black;">●</span>&nbsp;MATIG 25–50</span>
          <span><span style="background:#00ffff;padding:2px 12px;border-radius:4px;
                color:black;">●</span>&nbsp;LAAG 10–25</span>
          <span><span style="background:#0000ff;padding:2px 12px;border-radius:4px;
                color:white;">●</span>&nbsp;ONGUNSTIG &lt; 10</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 6 kaarten onder elkaar — vandaag + dag +1 t/m +5
    for dag_idx, (raster_dag, dag_label) in enumerate(zip(days_data, dag_datums)):
        dag_titel = "📅 **Vandaag**" if dag_idx == 0 else f"📅 **Dag +{dag_idx}**"
        gem_score = (
            round(sum(p["score"] for p in raster_dag) / len(raster_dag) * 100)
            if raster_dag else 0
        )
        # Vlieghoogte-samenvatting voor de dag (meest voorkomende categorie)
        if raster_dag:
            vh_teller: dict[str, int] = {}
            for p in raster_dag:
                lbl = p.get("vlieghoogte", "?")
                vh_teller[lbl] = vh_teller.get(lbl, 0) + 1
            vh_meest = max(vh_teller, key=lambda k: vh_teller[k])
        else:
            vh_meest = "?"
        st.markdown(
            f"### {dag_titel} — {dag_label}  ·  gem. score: {gem_score}/100  ·  vlieghoogte: {vh_meest}"
        )

        m_dag = folium.Map(location=[KAART_CENTER_LAT, KAART_CENTER_LON], zoom_start=5, tiles="CartoDB positron")

        for punt in raster_dag:
            score_pct    = int(punt["score"] * 100)
            kleur        = punt["kleur"]
            radius       = punt.get("marker_radius", 7)
            vh_lbl       = punt.get("vlieghoogte", "?")
            vh_tip       = punt.get("vlieghoogte_tip", "")
            popup_html = (
                f"<div style='font-size:13px;min-width:210px;'>"
                f"<b>Migratiecode: {score_pct}/100</b><br>"
                f"<b>Klasse: {punt['klasse']}</b><br>"
                f"📍 {punt['latitude']}°N, {punt['longitude']}°E<br>"
                f"🧭 Wind: {punt['wind_richting']} {punt['wind_kracht']} Bf<br>"
                f"🌡️ Temp: {punt['temperatuur']} °C<br>"
                f"🌧️ Neerslag: {punt['neerslag']} mm<br>"
                f"📊 Druk: {punt['druk']} hPa<br>"
                f"🌀 BLH: {punt['blh']} m<br>"
                f"<b>✈️ Vlieghoogte: {vh_lbl}</b><br>"
                f"<i style='font-size:11px;color:#555'>{vh_tip}</i>"
                + (
                    "<br><span style='color:#c47000;font-size:11px;'>"
                    "🌟 BE/NL zone: ZO-wind (3–5 Bf) = optimaal</span>"
                    if punt.get("be_nl_zone") else ""
                )
                + "</div>"
            )
            tooltip_tekst = (
                f"{dag_label} | {score_pct}/100 ({punt['klasse']}) "
                f"| {punt['latitude']}°N {punt['longitude']}°E "
                f"| {punt['wind_richting']} {punt['wind_kracht']} Bf "
                f"| {punt['druk']} hPa | ✈️ {vh_lbl}"
            )
            folium.CircleMarker(
                location=[punt["latitude"], punt["longitude"]],
                radius=radius,
                color=kleur,
                fill=True,
                fill_color=kleur,
                fill_opacity=0.82,
                weight=1,
                popup=folium.Popup(popup_html, max_width=260),
                tooltip=tooltip_tekst,
            ).add_to(m_dag)

        st_folium(
            m_dag, height=500, returned_objects=[],
            use_container_width=True, key=f"raster_dag_{dag_idx}",
        )
        st.divider()

    # Corridoranalyse voor BE / NL / DE
    with st.expander("📊 Corridoranalyse — België · Nederland · Duitsland (dag per dag)"):
        for dag_idx, (raster_dag, dag_label) in enumerate(zip(days_data, dag_datums)):
            corridor = sorted(
                [
                    p for p in raster_dag
                    if CORRIDOR_LAT_MIN <= p["latitude"] <= CORRIDOR_LAT_MAX
                    and CORRIDOR_LON_MIN <= p["longitude"] <= CORRIDOR_LON_MAX
                ],
                key=lambda x: (x["latitude"], x["longitude"]),
            )
            dag_kop = "Vandaag" if dag_idx == 0 else f"Dag +{dag_idx}"
            st.markdown(f"**{dag_kop} — {dag_label}**")
            if corridor:
                corridor_df = pd.DataFrame([{
                    "Lat":              p["latitude"],
                    "Lon":              p["longitude"],
                    "Score":            f"{int(p['score'] * 100)}/100",
                    "Klasse":           p["klasse"],
                    "Wind":             f"{p['wind_richting']} {p['wind_kracht']} Bf",
                    "Temp (°C)":        p["temperatuur"],
                    "Neerslag (mm)":    p["neerslag"],
                    "Druk (hPa)":       p["druk"],
                    "BLH (m)":          p["blh"],
                    "✈️ Vlieghoogte":   p.get("vlieghoogte", "?"),
                    "🌟 BE/NL opt.":    "ZO 3–5Bf" if p.get("be_nl_zone") else "",
                } for p in corridor])
                st.dataframe(corridor_df, use_container_width=True)

with tabs[3]:
    st.header("CROW project")
    # Maak de dynamische CROW widget URL
    CROW_url = f"https://www.meteo.be/services/birdDetection/#/"

    # Streamlit Iframe in Markdown
    st.markdown(
        f"""
        <iframe width="100%" height="1000" src="{CROW_url}" frameborder="1"></iframe>
        """,
        unsafe_allow_html=True
    )
with tabs[4]:
    st.header("Kraanvogel radar")
    # Maak de dynamische Crane widget URL
    #CROW_url = f"https://www.meteo.be/services/birdDetection/#/"
    Crane_url = f"https://analytical.sensingclues.org/cranes/"  # URL van de externe website
    # Streamlit Iframe in Markdown
    st.markdown(
        f"""
        <iframe width="100%" height="1000" src="{Crane_url}" frameborder="1"></iframe>
        """,
        unsafe_allow_html=True
    )


with tabs[5]:
    # Dropdown met soorten uit de config
    geselecteerde_soort = st.selectbox("Kies een soort:", list(iframe_data.keys()))

    st.text(f"{geselecteerde_soort} – 6 flightcalls")

    # HTML bouwen voor de gekozen soort
    iframes = iframe_data[geselecteerde_soort]
    iframe_html = "<div style='display: flex; flex-direction: column; gap: 15px;'>"

    for url in iframes:
        iframe_html += f"<iframe src='{url}' scrolling='no' frameborder='0' width='400' height='220'></iframe>"

    iframe_html += "</div>"

    # Spelers tonen
    components.html(iframe_html, height=2400)


with tabs[6]:
    st.header("Handleiding")
    # Eenvoudige handleiding
    st.text("""
        Handleiding voor deze applicatie:
        1. Kies een land en een locatie via de sidebar, een geldige locatie is een stad of een gemeente, vergelijkbaar met de opzoekmogelijkheden in bijvoorbeeld google maps.
        2. Selecteer een datum voor het opvragen van historische weergegevens, dit kan vanaf vandaag tot 1 jaar terug.
        3. Gebruik de slider in de sidebar om het begin en start uur van de waarnemingen te filteren.
        4. Bekijk de weersgegevens in het tabblad "Weergegevens", hier kan je kiezen om de gegevens "regel per regel" te kopiëren of als "1 blok" te kopiëren (manueel kopiëren werkt ook d.m.v. sleepbeweging.
           Deze gegevens zijn reeds zo opgemaakt dat ze zonder tussenstap via kopiëren/plakken in het vak "Opmerkingen weer" kunnen geplakt worden in de website van Trektellen.nl.
        5. In het tabblad "Voorspellingen" kan je de weersverwachtingen vnden voor het gekozen land en locatie. Je kan deze gegevens ook downloaden voor verwerking in bijvoorbeeld Excel.
           Bovenaan de voorspellingen kan je eenvoudig kiezen welke voorspellingen je wenst te zien.
           Naast de kolom met de voorspellingen kan je ook een kaart van het gekozen land en locatie raadplegen via een aantal (uit te schakelen) layers.
        6. In de andere tabbladen kom je terecht op een aantal bekende, belangrijke, informatieplatformen zoals het CROW project en BIRDTAM project waar de dichtheden van migratiestromen weergegeven worden.
           Uiteraard kan je in deze context ook terecht op de webpagina van Trektellen.nl, echter kan je geen gegevens wijzigen op deze site, het weergeven van trektellen.nl is hier puur informatief bedoeld.
        7. Voor meldingen, opmerkingen en vragen kan je terecht via mail : ydsdsy@gmail.com""")
        # Een mailto-link toevoegen

