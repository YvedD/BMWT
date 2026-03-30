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
import concurrent.futures
import threading
from geopy.exc import GeocoderUnavailable, GeocoderRateLimited
import streamlit.components.v1 as components
from timezonefinder import TimezoneFinder
from soorten_geluiden import iframe_data
import altair as alt

_TF = TimezoneFinder()

# --- Thread-veilige cache voor dichtsbijzijnde bewoonde kern (Nominatim fallback) ---
_kern_cache: dict = {}
_kern_lock = threading.Lock()
_nominatim_semaphore = threading.Semaphore(1)  # Max 1 gelijktijdige Nominatim-aanvraag (ToS)


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

        /* Uurkeuze-radio: compacte 2-kolomsweergave */
        [data-testid="stRadioGroup"] {
            display: grid !important;
            grid-template-columns: 1fr 1fr !important;
            column-gap: 4px !important;
            row-gap: 0px !important;
        }
        [data-testid="stRadioGroup"] label {
            font-size: 11px !important;
            padding: 1px 2px !important;
            line-height: 1.3 !important;
            min-height: unset !important;
        }
        [data-testid="stRadioGroup"] label [data-testid="stMarkdownContainer"] p {
            font-size: 11px !important;
            line-height: 1.3 !important;
            margin: 0 !important;
        }
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
_BEAUFORT_DREMPELS_KMH = [1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 103, 118]


def _kmh_naar_beaufort_klasse(kmh):
    for i, grens in enumerate(_BEAUFORT_DREMPELS_KMH):
        if kmh <= grens:
            return i
    return 12


def kmh_naar_beaufort(kmh):
    bf = _kmh_naar_beaufort_klasse(kmh)
    return f"{bf}" if bf < 12 else "12Bf"

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
    except (GeocoderUnavailable, GeocoderRateLimited):
        # Als Nominatim niet beschikbaar is of rate-limiteert, probeer OpenCage
        st.warning("Nominatim is tijdelijk niet beschikbaar of rate-limiteert, overschakelen naar OpenCage...")
        
        geolocator_opencage = OpenCage(api_key="b1f4bbd95b90415da9c04e261fe331d7")
        try:
            locatie_data = geolocator_opencage.geocode(locatie, exactly_one=True, language="en")
            if locatie_data:
                return locatie_data.latitude, locatie_data.longitude, locatie_data.address
            else:
                st.error(f"De locatie {locatie} kan niet gevonden worden in OpenCage.")
                return None, None, None
        except (GeocoderUnavailable, GeocoderRateLimited):
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
MIGRATIE_FORECAST_DAYS  = 10
MIGRATIE_FORECAST_HOURS = MIGRATIE_FORECAST_DAYS * 24   # = 144 uurlijkse waarden

# Vlieghoogte-drempelwaarden (km/h)
VLIEGHOOGTE_LAAG_MIN         = 29    # 5–6 Bf: vogels vliegen laag (waarneembaar)
VLIEGHOOGTE_MIDDEL_MIN       = 12    # 3–4 Bf: middelhoogte
VLIEGHOOGTE_GESTOPT_THRESHOLD = 50   # ≥ 7 Bf: trek grotendeels afgeremd

# Kaartcentrum: rasterpunt 48.0°N 4.8°E altijd in het midden
KAART_CENTER_LAT = 48.0
KAART_CENTER_LON = 4.8

# Bounding box voor corridoranalyse BE/NL/DE
CORRIDOR_LAT_MIN = 49.5
CORRIDOR_LAT_MAX = 55.5
CORRIDOR_LON_MIN = -9.5
CORRIDOR_LON_MAX = 13.9

# Bounding box voor BE/NL ZO-wind optimum (vogels gestuwd vanuit centraal-Frankrijk)
# ZO-wind (135°, 3–5 Bf) is de ideale windrichting voor trek langs de Noordzeekust
BENE_LAT_MIN        = 43.0    # zuidgrens BE
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
BENE_WIND_SPEED_3BF_MAX = 20.0  # Bf 3 bovengrens
BENE_WIND_SPEED_5BF = 38.0    # Bf 5 bovengrens (= optimum bovengrens)
BENE_WIND_SPEED_7BF = 50.0    # Bf 7 ondergrens (= trek grotendeels afgeremd)

# Windrichting-correctiefactoren
# West-component strafterm: hoe meer West-component, hoe lager de score
WIND_WEST_PENALTY   = 0.7     # strafmultiplicator voor de West-component
# Zeemigratiebonus bij sterke NW/W wind (>6 Bf): vogels worden oostwaarts geblazen
WIND_SEA_BONUS      = 0.4     # bonusmultiplicator voor de West-component bij >6 Bf
# Richtingsbereik (graden) waarbinnen de zeemigratiebonus geldt (ZW t/m NNW)
WIND_NW_W_DIR_MIN   = 225.0   # ZW (ondergrens)
WIND_NW_W_DIR_MAX   = 330.0   # NNW (bovengrens)
VOORJAAR_WIND_NUL_ALLE_SNELHEDEN = frozenset({"W", "NW", "WNW", "NNW", "WZW"})
VOORJAAR_WIND_NUL_STRIKT_BOVEN_3BF = frozenset({"ZW", "N", "NNO"})
VOORJAAR_WIND_MAX_STRIKT_ONDER_3BF = frozenset({"ZW", "ZZW"})

# Scoregewichten (manueel aanpasbaar)
MIGRATIE_SCORE_WINDRICHTING_GEWICHT = 0.70
MIGRATIE_SCORE_TEMPERATUUR_GEWICHT  = 0.30

# Temperatuurscore via handmatig aanpasbare controlepunten (°C, score 0.0–1.0)
# Tussen de punten wordt lineair geïnterpoleerd.
TEMPERATUUR_SCORE_PUNTEN = (
    (-5.0, 0.00),
    ( 2.0, 0.05),
    ( 5.0, 0.10),
    (8.0, 0.15),
    (10.0, 0.20),
    ( 12.0, 0.35),
    ( 18.0, 0.50),
    (20.0, 0.60),
    (25.0, 0.70),
    (27.0, 0.95),
    (35.0, 0.00),
)

# ---------------------------------------------------------------------------
# Aanvoercorridor: migratieaanvoer vanuit het zuiden naar BE/NL
# Wetenschappelijke basis: migratie is een 'pijplijn'. Vogels passeren eerst
# Spanje/Marokko (Tarifa-corridor), dan Frankrijk, vóór ze België bereiken.
# Regenfronten of ongunstige winden ter hoogte van deze zones blokkeren de
# aanvoer, ook al zijn de lokale omstandigheden in België gunstig.
# Bronnen: Berthold (2001), Ellegren (1993), Schaub et al. (2004 PNAS).
# ---------------------------------------------------------------------------
SUPPLY_FRANCE_LAT_MIN   = 43.0  # Zuid-Frankrijk
SUPPLY_FRANCE_LAT_MAX   = 49.5  # Noord-Frankrijk / Belgische grens
SUPPLY_SPAIN_LAT_MIN    = 36.0  # Tarifa / Zuid-Spanje
SUPPLY_SPAIN_LAT_MAX    = 43.0  # Noord-Spanje
SUPPLY_CORRIDOR_LON_MIN = -2.0  # Westgrens migratieroute
SUPPLY_CORRIDOR_LON_MAX = 10.0  # Oostgrens migratieroute
SUPPLY_LAG_FRANCE       = 1     # 1 dag eerder: vogels in Fr. → volgende dag in BE
SUPPLY_LAG_SPAIN        = 2     # 2 dagen eerder: vogels in Sp. → 2 dagen later in BE
SUPPLY_FRANCE_WEIGHT    = 0.60  # Gewicht van de Franse aanvoer (directere impact)
SUPPLY_SPAIN_WEIGHT     = 0.40  # Gewicht van de Spaanse aanvoer
SUPPLY_FACTOR_FLOOR     = 0.30  # Minimum aanvoerfactor (altijd minimaal 30 % door)
SUPPLY_FACTOR_RANGE     = 0.70  # Werkbereik van de aanvoerfactor (1 − floor)
STANDAARD_CORRIDOR_SCORE = 0.50 # Terugvalwaarde als corridor leeg is

# Rasterresolutie voor hoge resolutie (~50×50 km)
MIGRATIE_LAT_STEP_HOGE_RES = 0.5   # ≈ 55 km per breedtegraad
MIGRATIE_LON_STEP_HOGE_RES = 0.65  # ≈ 50 km op breedtegraad 45°N

# Tijdzones die worden uitgesloten van het raster (eilanden / niet-migratiegebied)
_UITGESLOTEN_TIJDZONES = frozenset({
    "Europe/London",       # Groot-Brittannië & Noord-Ierland
    "Europe/Dublin",       # Ierland
    "Europe/Isle_of_Man",  # Man-eiland
})

# === ZEEBRIES KUSTDETECTOR — vaste kustlocaties (Saint-Malo t/m Esbjerg) ===
# Zeebries = onshore wind vanuit zee die ontstaat bij sterke opwarming van het land.
# Reikt slechts 5–15 km landinwaarts en is een echte migratie-stopper aan de kust.
ZEEBRIES_HORIZON_DAYS        =  6     # aantal voorspellingsdagen
ZEEBRIES_MAP_CENTER_LAT      = 52.5   # centrum zeebries-kaart (breedtegraad)
ZEEBRIES_MAP_CENTER_LON      =  4.0   # centrum zeebries-kaart (lengtegraad)
ZEEBRIES_MAP_ZOOM            =  5     # initieel zoomniveau zeebries-kaart

# Drempelwaarden zeebries-detectie (wetenschappelijk onderbouwd)
ZEEBRIES_DT_DREMPEL    =  4.0   # °C: land − zee temp. (ΔT ≥ 4°C → zeebries mogelijk)
ZEEBRIES_WIND_MAX_KMH  = 28.0   # km/h ≈ 3 Bf (sterkere synoptische wind onderdrukt zeebries)
ZEEBRIES_BEWOLKING_MAX = 60     # % bewolking (meer bewolking → minder opwarming land)
ZEEBRIES_UUR_BEGIN     = 10     # UTC: zeebries begint niet voor 10u (lokaal 11-12u zomertijd)
ZEEBRIES_UUR_EIND      = 20     # UTC: zeebries verdwijnt na zonsondergang (~20u UTC zomer)

# Fallback SST (°C) per maand voor de Zuidelijke Noordzee (als Marine API niet beschikbaar)
_NOORDZEE_SST_FALLBACK = {
    1: 6.0, 2: 5.5, 3: 6.0,  4:  8.0, 5: 11.0, 6: 14.0,
    7: 17.0, 8: 17.5, 9: 16.0, 10: 13.0, 11: 10.0, 12: 7.5,
}

# Vaste kustlocaties voor zeebries-detectie: French Channel coast → Danish North Sea coast.
# zee_lat/zee_lon = offshore zeepunt voor SST-opvraging via Marine API.
ZEEBRIES_VASTE_KUSTLOCATIES: list[dict] = [
    {"naam": "Saint-Malo",             "latitude": 48.649, "longitude": -2.025, "zee_lat": 48.9,  "zee_lon": -3.0 },
    {"naam": "Cherbourg-en-Contentin", "latitude": 49.633, "longitude": -1.617, "zee_lat": 50.0,  "zee_lon": -1.6 },
    {"naam": "Ouistreham",             "latitude": 49.277, "longitude": -0.260, "zee_lat": 49.8,  "zee_lon": -0.3 },
    {"naam": "Le Havre",               "latitude": 49.494, "longitude":  0.107, "zee_lat": 49.9,  "zee_lon":  0.1 },
    {"naam": "Dieppe",                 "latitude": 49.922, "longitude":  1.082, "zee_lat": 50.3,  "zee_lon":  1.1 },
    {"naam": "Boulogne-sur-Mer",       "latitude": 50.726, "longitude":  1.614, "zee_lat": 51.0,  "zee_lon":  1.0 },
    {"naam": "Calais",                 "latitude": 50.951, "longitude":  1.858, "zee_lat": 51.3,  "zee_lon":  1.9 },
    {"naam": "Koksijde",               "latitude": 51.107, "longitude":  2.654, "zee_lat": 51.2,  "zee_lon":  2.0 },
    {"naam": "Zeebrugge",              "latitude": 51.333, "longitude":  3.200, "zee_lat": 51.5,  "zee_lon":  2.5 },
    {"naam": "Breskens",               "latitude": 51.399, "longitude":  3.556, "zee_lat": 51.5,  "zee_lon":  2.8 },
    {"naam": "Westkapelle",            "latitude": 51.525, "longitude":  3.441, "zee_lat": 51.6,  "zee_lon":  2.7 },
    {"naam": "Renesse",                "latitude": 51.727, "longitude":  3.775, "zee_lat": 51.9,  "zee_lon":  3.1 },
    {"naam": "Ouddorp",                "latitude": 51.831, "longitude":  3.895, "zee_lat": 52.0,  "zee_lon":  3.2 },
    {"naam": "Hoek van Holland",       "latitude": 51.978, "longitude":  4.134, "zee_lat": 52.1,  "zee_lon":  3.5 },
    {"naam": "Katwijk",                "latitude": 52.203, "longitude":  4.397, "zee_lat": 52.3,  "zee_lon":  3.7 },
    {"naam": "IJmuiden",               "latitude": 52.462, "longitude":  4.595, "zee_lat": 52.6,  "zee_lon":  3.8 },
    {"naam": "Den Helder",             "latitude": 52.959, "longitude":  4.762, "zee_lat": 53.1,  "zee_lon":  4.0 },
    {"naam": "Oost-Vlieland",          "latitude": 53.298, "longitude":  5.082, "zee_lat": 53.5,  "zee_lon":  4.3 },
    {"naam": "Harlingen",              "latitude": 53.175, "longitude":  5.425, "zee_lat": 53.3,  "zee_lon":  4.6 },
    {"naam": "Hollum",                 "latitude": 53.447, "longitude":  5.619, "zee_lat": 53.6,  "zee_lon":  4.9 },
    {"naam": "Schiermonnikoog",        "latitude": 53.487, "longitude":  6.194, "zee_lat": 53.7,  "zee_lon":  5.4 },
    {"naam": "Lauwersmeer",            "latitude": 53.349, "longitude":  6.200, "zee_lat": 53.5,  "zee_lon":  5.5 },
    {"naam": "Borkum",                 "latitude": 53.590, "longitude":  6.661, "zee_lat": 53.7,  "zee_lon":  5.9 },
    {"naam": "Norderney",              "latitude": 53.706, "longitude":  7.149, "zee_lat": 53.9,  "zee_lon":  6.4 },
    {"naam": "Cuxhaven",               "latitude": 53.867, "longitude":  8.692, "zee_lat": 54.1,  "zee_lon":  7.8 },
    {"naam": "Büsum",                  "latitude": 54.129, "longitude":  8.858, "zee_lat": 54.3,  "zee_lon":  7.9 },
    {"naam": "Ording",                 "latitude": 54.300, "longitude":  8.633, "zee_lat": 54.5,  "zee_lon":  7.6 },
    {"naam": "Husum",                  "latitude": 54.472, "longitude":  9.052, "zee_lat": 54.6,  "zee_lon":  7.9 },
    {"naam": "Westerland",             "latitude": 54.904, "longitude":  8.302, "zee_lat": 55.1,  "zee_lon":  7.2 },
    {"naam": "Esbjerg",                "latitude": 55.476, "longitude":  8.459, "zee_lat": 55.6,  "zee_lon":  7.4 },
]


def migratie_is_geldig_punt(lat: float, lon: float) -> bool:
    """Return True als het rasterpunt op land valt en niet in een uitgesloten gebied.

    Uitgesloten: oceaan/zee, Groot-Brittannië, Noord-Ierland, Ierland, Man-eiland.
    TimezoneFinder retourneert None voor oceanen, maar Etc/GMT* voor open zee.
    Beide worden als 'in zee' beschouwd.
    """
    tz = _TF.timezone_at(lat=lat, lng=lon)
    if tz is None:
        return False          # punt in oceaan / diepe zee
    if tz.startswith("Etc/"):
        return False          # open zee (UTC-offset tijdzones)
    if tz in _UITGESLOTEN_TIJDZONES:
        return False          # uitgesloten regio's
    return True


def migratie_genereer_rasterpunten(lat_step: float = None, lon_step: float = None):
    """Genereer rasterpunten met Tarifa als ankerpunt.

    Standaard ~100×100 km; geef lat_step=0.5 / lon_step=0.65 voor ~50×50 km.
    Uitsluitend geldige landpunten worden opgenomen via migratie_is_geldig_punt().
    VK, Ierland en Man-eiland blijven altijd uitgesloten.
    """
    _lat_step = lat_step if lat_step is not None else MIGRATIE_LAT_STEP
    _lon_step = lon_step if lon_step is not None else MIGRATIE_LON_STEP

    lats = set()
    n = 0
    while True:
        lat = round(MIGRATIE_ANCHOR_LAT + n * _lat_step, 2)
        if lat > MIGRATIE_LAT_MAX:
            break
        if lat >= MIGRATIE_LAT_MIN:
            lats.add(lat)
        n += 1
    n = -1
    while True:
        lat = round(MIGRATIE_ANCHOR_LAT + n * _lat_step, 2)
        if lat < MIGRATIE_LAT_MIN:
            break
        if lat <= MIGRATIE_LAT_MAX:
            lats.add(lat)
        n -= 1

    lons = set()
    n = 0
    while True:
        lon = round(MIGRATIE_ANCHOR_LON + n * _lon_step, 2)
        if lon > MIGRATIE_LON_MAX:
            break
        if lon >= MIGRATIE_LON_MIN:
            lons.add(lon)
        n += 1
    n = -1
    while True:
        lon = round(MIGRATIE_ANCHOR_LON + n * _lon_step, 2)
        if lon < MIGRATIE_LON_MIN:
            break
        if lon <= MIGRATIE_LON_MAX:
            lons.add(lon)
        n -= 1

    # Eerste pass: verzamel alle geldige landpunten als opzoekverzameling
    geldige_land_set: set[tuple[float, float]] = set()
    for lat in lats:
        for lon in lons:
            if migratie_is_geldig_punt(lat, lon):
                geldige_land_set.add((lat, lon))

    # Bouw definitieve lijst: uitsluitend geldige landpunten
    punten = []
    for lat in sorted(lats):
        for lon in sorted(lons):
            if (lat, lon) in geldige_land_set:
                punten.append({"latitude": lat, "longitude": lon})
    return punten


# ---------------------------------------------------------------------------
# Zeebries kustdetector — vaste kustlocaties
# ---------------------------------------------------------------------------

def _haal_zeebries_voorspelling(punt: dict) -> dict:
    """Haal landweer (Open-Meteo forecast) + SST (Open-Meteo Marine) op voor één kustpunt.

    Retourneert {"hourly": dict|None, "sst": list|None}.
    Probeert elk endpoint éénmalig; falen is non-fataal.
    """
    land_params = {
        "latitude":     punt["latitude"],
        "longitude":    punt["longitude"],
        "hourly":       "temperature_2m,wind_speed_10m,wind_direction_10m,cloud_cover",
        "timezone":     "UTC",
        "forecast_days": ZEEBRIES_HORIZON_DAYS,
    }
    marine_params = {
        "latitude":     punt["zee_lat"],
        "longitude":    punt["zee_lon"],
        "hourly":       "sea_surface_temperature",
        "timezone":     "UTC",
        "forecast_days": ZEEBRIES_HORIZON_DAYS,
    }
    hourly_land = None
    sst_list    = None

    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params=land_params, timeout=20,
        )
        if r.status_code == 200:
            hourly_land = r.json().get("hourly")
    except Exception:
        pass

    try:
        r = requests.get(
            "https://marine-api.open-meteo.com/v1/marine",
            params=marine_params, timeout=20,
        )
        if r.status_code == 200:
            sst_list = r.json().get("hourly", {}).get("sea_surface_temperature")
    except Exception:
        pass

    return {"hourly": hourly_land, "sst": sst_list}


def detecteer_zeebries_uur(
    temp_land: float,
    sst: float,
    wind_speed_kmh: float,
    cloud_cover_pct: float,
    hour_utc: int,
) -> bool:
    """Voorspel of een zeebries optreedt voor dit uur.

    Zeebries-condities (wetenschappelijke drempelwaarden):
      - Land warmer dan zee: ΔT ≥ ZEEBRIES_DT_DREMPEL °C
      - Synoptische wind zwak genoeg: < ZEEBRIES_WIND_MAX_KMH km/h
      - Niet te bewolkt (zonneschijn verwarmt land): bewolking < ZEEBRIES_BEWOLKING_MAX %
      - Dagtijd (UTC): ZEEBRIES_UUR_BEGIN ≤ uur ≤ ZEEBRIES_UUR_EIND

    Noot: windrichting wordt NIET gecontroleerd — de zeebries CREËERT de onshore wind.
    Wat we voorspellen is de *conditie* voor zeebries-vorming, niet het gevolg.
    """
    if temp_land - sst < ZEEBRIES_DT_DREMPEL:
        return False
    if wind_speed_kmh >= ZEEBRIES_WIND_MAX_KMH:
        return False
    if cloud_cover_pct > ZEEBRIES_BEWOLKING_MAX:
        return False
    if not (ZEEBRIES_UUR_BEGIN <= hour_utc <= ZEEBRIES_UUR_EIND):
        return False
    return True


@st.cache_data(ttl=1800)
def laad_zeebries_kustdata() -> tuple[list[list[dict]], list[str], str]:
    """Laad zeebries-voorspelling voor vaste kustlocaties (Saint-Malo t/m Esbjerg).

    Retourneert (kustpunten_per_dag, dag_datums, opgehaald_om):
      - kustpunten_per_dag[dag_idx]: lijst van punt-dicts per dag
      - Elk punt-dict bevat: lat, lon, naam, zeebries_uren (bool[24]),
        delta_t_uren (float[24]), sst_middag, zeebries_actief,
        zeebries_start/stop/n_uren.

    Gebruikt de Open-Meteo Marine API voor SST.  Als die niet beschikbaar is,
    valt het systeem terug op klimatologische SST-waarden voor de Zuidelijke
    Noordzee.
    """
    sst_fallback = _NOORDZEE_SST_FALLBACK[date.today().month]
    punten = ZEEBRIES_VASTE_KUSTLOCATIES
    vandaag = date.today()
    dag_datums = [
        _dag_label_nl(vandaag + timedelta(days=i))
        for i in range(ZEEBRIES_HORIZON_DAYS)
    ]

    def verwerk_kustpunt(punt: dict) -> list[dict]:
        data        = _haal_zeebries_voorspelling(punt)
        hourly_land = data.get("hourly")
        sst_lijst   = data.get("sst")

        dag_dicts: list[dict] = []
        for dag_idx in range(ZEEBRIES_HORIZON_DAYS):
            uur_flags:     list[bool]  = []
            delta_t_uren:  list[float] = []

            for uur in range(24):
                h_idx = dag_idx * 24 + uur

                # --- Landweer ---
                t_land = ws = cc = None
                if hourly_land:
                    try:
                        t_raw = hourly_land["temperature_2m"][h_idx]
                        w_raw = hourly_land["wind_speed_10m"][h_idx]
                        c_raw = hourly_land["cloud_cover"][h_idx]
                        if None not in (t_raw, w_raw, c_raw):
                            t_land = float(t_raw)
                            ws     = float(w_raw)
                            cc     = float(c_raw)
                    except (IndexError, KeyError, TypeError):
                        pass

                # --- SST ---
                sst = None
                if sst_lijst and h_idx < len(sst_lijst) and sst_lijst[h_idx] is not None:
                    sst = float(sst_lijst[h_idx])
                if sst is None:
                    sst = sst_fallback   # klimatologische fallback

                # --- Zeebries-detectie ---
                if t_land is not None and ws is not None and cc is not None:
                    dt  = round(t_land - sst, 1)
                    zb  = detecteer_zeebries_uur(t_land, sst, ws, cc, uur)
                else:
                    dt  = 0.0
                    zb  = False

                delta_t_uren.append(dt)
                uur_flags.append(zb)

            uren_actief = [u for u, f in enumerate(uur_flags) if f]

            # SST bij middag van deze dag
            middag_idx = dag_idx * 24 + 12
            sst_middag = None
            if sst_lijst and middag_idx < len(sst_lijst) and sst_lijst[middag_idx] is not None:
                sst_middag = round(float(sst_lijst[middag_idx]), 1)
            if sst_middag is None:
                sst_middag = sst_fallback

            dag_dicts.append({
                "latitude":        punt["latitude"],
                "longitude":       punt["longitude"],
                "naam":            punt.get("naam", ""),
                "zeebries_uren":   uur_flags,
                "delta_t_uren":    delta_t_uren,
                "sst_middag":      sst_middag,
                "zeebries_actief": len(uren_actief) > 0,
                "zeebries_start":  min(uren_actief) if uren_actief else None,
                "zeebries_stop":   max(uren_actief) if uren_actief else None,
                "zeebries_n_uren": len(uren_actief),
            })
        return dag_dicts

    kustpunten_per_dag: list[list[dict]] = [[] for _ in range(ZEEBRIES_HORIZON_DAYS)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        alle_resultaten = list(executor.map(verwerk_kustpunt, punten))
    for dag_dicts in alle_resultaten:
        for dag_idx, dag_punt in enumerate(dag_dicts):
            kustpunten_per_dag[dag_idx].append(dag_punt)

    opgehaald_om = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return kustpunten_per_dag, dag_datums, opgehaald_om


def migratie_bereken_score(weer, lat: float = 0.0, lon: float = 0.0):
    """
    Bereken migratiescore (0.0 = extreem ongunstig, 1.0 = extreem gunstig).

    Gewichten:
      - Windrichting  70 %  (manueel aanpasbaar via constante)
      - Temperatuur   30 %  (manueel aanpasbaar via temperatuur-puntenreeks)

    Uitzondering windrichting: sterke NW/W wind (>6 Bf) levert geweldige zeemigratie
    op; in dat geval vervalt de West-straf en geldt een zeemigratiebonus.
    """
    if not weer:
        return 0.5

    wind_kracht   = float(weer.get("wind_speed_10m", 0))
    wind_richting = float(weer.get("wind_direction_10m", 180))
    temperatuur   = float(weer.get("temperature_2m", 12))
    in_bene = (
        BENE_LAT_MIN <= lat <= BENE_LAT_MAX
        and BENE_LON_MIN <= lon <= BENE_LON_MAX
    )
    override = _voorjaar_bene_wind_override(wind_richting, wind_kracht) if in_bene else None

    # Windrichting: zuidenwind (180°) = ideale rugwind voor noordwaartse trek
    #   Zuid-component verhoogt de score; West-component verlaagt de score.
    #   Uitzondering: sterke NW/W wind (>6 Bf) → geweldige migratie over zee
    #     (vogels worden oostwaarts geblazen over de Noordzee).
    if override == "zero":
        wind_richting_score = 0.0
    elif override == "max":
        wind_richting_score = 1.0
    else:
        south_score = (1.0 - math.cos(math.radians(wind_richting))) / 2.0
        west_component = max(0.0, -math.sin(math.radians(wind_richting)))
        is_nw_w_sterk = (WIND_NW_W_DIR_MIN <= wind_richting <= WIND_NW_W_DIR_MAX) and (wind_kracht >= BENE_WIND_SPEED_7BF)
        if is_nw_w_sterk:
            # Sterke NW/W: West-straf vervalt; West-component levert zeemigratiebonus op
            wind_richting_score = min(1.0, south_score + west_component * WIND_SEA_BONUS)
        else:
            # Meer West-component → lagere score; meer Zuid-component → hogere score
            wind_richting_score = max(0.0, south_score - WIND_WEST_PENALTY * west_component)

    temp_score = _temperatuur_score(temperatuur)

    score = (
        MIGRATIE_SCORE_WINDRICHTING_GEWICHT * wind_richting_score
        + MIGRATIE_SCORE_TEMPERATUUR_GEWICHT * temp_score
    )
    return round(min(1.0, max(0.0, score)), 3)


def migratie_score_naar_klasse(score):
    """Vertaal migratiescore naar tekstlabel (10 kleurschalen)."""
    if score >= 0.90:
        return "Uitstekend"
    elif score >= 0.80:
        return "Zeer goed"
    elif score >= 0.70:
        return "Goed"
    elif score >= 0.60:
        return "Vrij goed"
    elif score >= 0.50:
        return "Redelijk"
    elif score >= 0.40:
        return "Matig"
    elif score >= 0.30:
        return "Ongunstig"
    elif score >= 0.20:
        return "Slecht"
    elif score >= 0.10:
        return "Zeer slecht"
    else:
        return "Verwaarloosbaar"


def migratie_score_naar_kleur(score):
    """Converteer migratiescore naar hex-kleur via RGB-interpolatie.

    Gradient: fel donkerrood (0%) → fel citroengeel (40–50%) → fel muntgroen (100%).
    Ankerkleuren (RGB):
      - 0%  : fel donkerrood  (176,   0,   0)
      - 45% : fel citroengeel (255, 255,   0)  [midden klasse 40–50%]
      - 100%: fel muntgroen   (  0, 255, 128)
    """
    YELLOW_AT = 0.45
    R_RED = (176,   0,   0)   # fel donkerrood
    R_YEL = (255, 255,   0)   # fel citroengeel
    R_GRN = (  0, 255, 128)   # fel muntgroen
    if score >= YELLOW_AT:
        t = (score - YELLOW_AT) / (1.0 - YELLOW_AT)  # 0 = geel, 1 = groen
        r = int(R_YEL[0] + t * (R_GRN[0] - R_YEL[0]))
        g = int(R_YEL[1] + t * (R_GRN[1] - R_YEL[1]))
        b = int(R_YEL[2] + t * (R_GRN[2] - R_YEL[2]))
    else:
        t = score / YELLOW_AT                          # 0 = rood, 1 = geel
        r = int(R_RED[0] + t * (R_YEL[0] - R_RED[0]))
        g = int(R_RED[1] + t * (R_YEL[1] - R_RED[1]))
        b = int(R_RED[2] + t * (R_YEL[2] - R_RED[2]))
    return f"#{r:02x}{g:02x}{b:02x}"


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
        score = migratie_bereken_score(weer, lat=punt["latitude"], lon=punt["longitude"])
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


def _dichtstbijzijnde_bewoonde_kern(lat: float, lon: float) -> tuple[float, float]:
    """Zoek de dichtstbijzijnde bewoonde kern (stad/gemeente/dorp) via Nominatim.

    Wordt gebruikt als fallback wanneer de weers-API geen data teruggeeft voor een
    rasterpunt (bv. een bosgebied of perifeer landelijk gebied).  Het resultaat is
    gecachet zodat dezelfde coördinaten maar één keer worden opgezocht.

    Retourneert de coördinaten van de gevonden kern, of de originele coördinaten als
    geen kern gevonden wordt.
    """
    key = (lat, lon)
    with _kern_lock:
        if key in _kern_cache:
            return _kern_cache[key]

    resultaat = (lat, lon)
    with _nominatim_semaphore:
        try:
            geolocator = Nominatim(
                user_agent="Bird Migration Weather Tool (contact: ydsdsy@gmail.com)"
            )
            time.sleep(1)  # Respect Nominatim rate limit (ToS: max 1 req/s)
            loc = geolocator.reverse(
                (lat, lon), language="nl", addressdetails=True, zoom=10, timeout=10
            )
            if loc:
                addr = loc.raw.get("address", {})
                kern = (
                    addr.get("city") or addr.get("town") or
                    addr.get("village") or addr.get("hamlet") or
                    addr.get("municipality")
                )
                land = addr.get("country_code", "")
                if kern and land:
                    time.sleep(1)  # Second Nominatim request — respect rate limit
                    kern_loc = geolocator.geocode(
                        f"{kern}, {land.upper()}", exactly_one=True, timeout=10
                    )
                    if kern_loc:
                        resultaat = (round(kern_loc.latitude, 4), round(kern_loc.longitude, 4))
        except Exception:
            pass

    with _kern_lock:
        _kern_cache[key] = resultaat
    return resultaat


def _uur_waarde(lst, idx: int, standaard: float) -> float:
    """Haal veilig een uurwaarde op; geeft standaard terug als de waarde None of ontbreekt."""
    try:
        v = lst[idx]
        return standaard if v is None else float(v)
    except (IndexError, TypeError):
        return standaard


def _haal_weer_forecast_rasterpunt(punt: dict) -> dict | None:
    """Haal 6-daagse uurlijkse weervoorspelling op voor één rasterpunt.

    Probeert tot 3 keer bij tijdelijke fouten of rate-limiting (HTTP 429).
    """
    params = {
        "latitude":  punt["latitude"],
        "longitude": punt["longitude"],
        "hourly": (
            "temperature_2m,wind_speed_10m,wind_direction_10m,"
            "precipitation,visibility,cloud_cover,"
            "pressure_msl,cape,boundary_layer_height"
        ),
        "timezone": "UTC",
        "forecast_days": 6,
    }
    for poging in range(3):
        try:
            resp = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params=params,
                timeout=20,
            )
            if resp.status_code == 200:
                return resp.json().get("hourly")
            if resp.status_code == 429:
                time.sleep(2 ** poging)  # Exponential backoff: 1s, 2s, 4s
                continue
        except Exception:
            pass
        if poging < 2:
            time.sleep(1)

    # Fallback: zoek de dichtstbijzijnde bewoonde kern en probeer opnieuw
    kern_lat, kern_lon = _dichtstbijzijnde_bewoonde_kern(punt["latitude"], punt["longitude"])
    if (kern_lat, kern_lon) != (punt["latitude"], punt["longitude"]):
        # Maak een nieuwe params-dict met de kern-coördinaten (origineel blijft ongewijzigd)
        kern_params = {**params, "latitude": kern_lat, "longitude": kern_lon}
        try:
            resp = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params=kern_params,
                timeout=20,
            )
            if resp.status_code == 200:
                return resp.json().get("hourly")
        except Exception:
            pass
    return None


def _voorjaar_bene_wind_override(wind_richting: float, wind_kracht: float) -> str | None:
    richting = graden_naar_windrichting(wind_richting)
    if richting in VOORJAAR_WIND_NUL_ALLE_SNELHEDEN:
        return "zero"
    if richting in VOORJAAR_WIND_NUL_STRIKT_BOVEN_3BF and wind_kracht > BENE_WIND_SPEED_3BF_MAX:
        return "zero"
    if richting in VOORJAAR_WIND_MAX_STRIKT_ONDER_3BF and wind_kracht < BENE_WIND_SPEED_3BF:
        return "max"
    return None


def _interpoleer_score_puntsgewijs(waarde: float, punten) -> float:
    if not punten:
        return 0.0
    if waarde <= punten[0][0]:
        return max(0.0, min(1.0, punten[0][1]))
    for (x0, y0), (x1, y1) in zip(punten, punten[1:]):
        if waarde <= x1:
            if x1 == x0:
                return max(0.0, min(1.0, y1))
            verhouding = (waarde - x0) / (x1 - x0)
            return max(0.0, min(1.0, y0 + verhouding * (y1 - y0)))
    return max(0.0, min(1.0, punten[-1][1]))


def _temperatuur_score(temperatuur: float) -> float:
    return _interpoleer_score_puntsgewijs(temperatuur, TEMPERATUUR_SCORE_PUNTEN)


def migratie_bereken_score_uitgebreid(
    weer: dict | None,
    lat: float = 0.0,
    lon: float = 0.0,
) -> float:
    """
    Bereken migratiescore (0.0–1.0) op basis van windrichting en temperatuur.

    Gewichten:
      70 % windrichting   (regionaal gecorrigeerd — zie hieronder)
      30 % temperatuur    (via manueel aanpasbare temperatuur-puntenreeks)

    Windrichtings-correctie (algemeen en BE/NL):
      Zuid-component (wind uit Z, ZZO, ZZW …) verhoogt de score.
      West-component (wind uit W, ZW, NW …) verlaagt de score.
      Uitzondering: heel sterke NW/W wind (>6 Bf) levert geweldige zeemigratie
        op (vogels worden oostwaarts geblazen over de Noordzee), waardoor de
        West-straf vervalt en een zeemigratiebonus wordt toegekend.

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

    in_bene = (
        BENE_LAT_MIN <= lat <= BENE_LAT_MAX
        and BENE_LON_MIN <= lon <= BENE_LON_MAX
    )

    if in_bene:
        override = _voorjaar_bene_wind_override(wind_richting, wind_kracht)
        if override == "zero":
            wind_richting_score = 0.0
        elif override == "max":
            wind_richting_score = 1.0
        else:
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

            # --- BE/NL West-component correctie ---
            # West-component verlaagt de score; uitzondering: sterke NW/W (>6 Bf)
            # → vogels worden oostwaarts geblazen over de Noordzee (zeemigratie).
            west_component = max(0.0, -math.sin(math.radians(wind_richting)))
            is_nw_w_sterk = (WIND_NW_W_DIR_MIN <= wind_richting <= WIND_NW_W_DIR_MAX) and (wind_kracht >= BENE_WIND_SPEED_7BF)
            if is_nw_w_sterk:
                wind_richting_score = min(1.0, wind_richting_score + west_component * WIND_SEA_BONUS)
            else:
                wind_richting_score = max(0.0, wind_richting_score - WIND_WEST_PENALTY * west_component)
    else:
        # Algemeen: Z-wind (180°) = ideale rugwind; N (0°/360°) = tegenwind.
        # Zuid-component verhoogt de score; West-component verlaagt de score.
        # Uitzondering: sterke NW/W wind (>6 Bf) → geweldige migratie over zee.
        south_score = (1.0 - math.cos(math.radians(wind_richting))) / 2.0
        west_component = max(0.0, -math.sin(math.radians(wind_richting)))
        is_nw_w_sterk = (WIND_NW_W_DIR_MIN <= wind_richting <= WIND_NW_W_DIR_MAX) and (wind_kracht >= BENE_WIND_SPEED_7BF)
        if is_nw_w_sterk:
            wind_richting_score = min(1.0, south_score + west_component * WIND_SEA_BONUS)
        else:
            wind_richting_score = max(0.0, south_score - WIND_WEST_PENALTY * west_component)

    temp_score = _temperatuur_score(temperatuur)

    score = (
        MIGRATIE_SCORE_WINDRICHTING_GEWICHT * wind_richting_score
        + MIGRATIE_SCORE_TEMPERATUUR_GEWICHT * temp_score
    )
    return round(min(1.0, max(0.0, score)), 3)


# ---------------------------------------------------------------------------
# Aanvoercorrectie: migratieaanvoer vanuit het zuiden (supply chain)
# ---------------------------------------------------------------------------

def _pas_aanvoer_toe(days_data: list[list[dict]]) -> list[list[dict]]:
    """
    Pas de migratiescore voor BE/NL-punten aan op basis van aanvoer uit het zuiden.

    Wetenschappelijke basis
    ----------------------
    Migratie is een 'pijplijn'. Vogels moeten eerst door Spanje (Tarifa-corridor,
    36–43°N) en daarna door Frankrijk (43–49.5°N) passeren vóór ze België bereiken.
    Regenfronten of sterke tegenwind ter hoogte van die zones blokkeren de aanvoer
    volledig — ook al zijn de lokale omstandigheden in België die dag uitstekend.
    Dit mechanisme is wetenschappelijk onderbouwd (Berthold 2001; Ellegren 1993;
    Schaub et al. 2004 PNAS; Liechti 2006 J. Ornithol.).

    Methode
    -------
    - Aanvoer uit Frankrijk : dag-index d → gebruik score op dag max(0, d-1)
    - Aanvoer uit Spanje    : dag-index d → gebruik score op dag max(0, d-2)
    - Gecombineerde supply-factor = 0.60 × Fr + 0.40 × Sp  (Frankrijk dominanter)
    - Floor op 0.30: er trekken altijd wel een paar vogels, ook bij blokkade
    - Gecorrigeerde score = ruwe score × supply_factor

    Noot: voor dag 0 (vandaag) ontbreekt historische data voor Fr (gisteren) en
    Sp (eergisteren). Dag 0 van het raster wordt als proxy gebruikt. Dit is een
    conservatieve benadering.
    """
    n_days = len(days_data)

    # Bereken gemiddelde passeerscores per dag voor Frans en Spaans corridor
    france_gem: list[float] = []
    spanje_gem: list[float] = []
    for dag_idx in range(n_days):
        fr_scores = [
            p["score"] for p in days_data[dag_idx]
            if SUPPLY_FRANCE_LAT_MIN <= p["latitude"] <= SUPPLY_FRANCE_LAT_MAX
            and SUPPLY_CORRIDOR_LON_MIN <= p["longitude"] <= SUPPLY_CORRIDOR_LON_MAX
        ]
        sp_scores = [
            p["score"] for p in days_data[dag_idx]
            if SUPPLY_SPAIN_LAT_MIN <= p["latitude"] <= SUPPLY_SPAIN_LAT_MAX
            and SUPPLY_CORRIDOR_LON_MIN <= p["longitude"] <= SUPPLY_CORRIDOR_LON_MAX
        ]
        france_gem.append(sum(fr_scores) / len(fr_scores) if fr_scores else STANDAARD_CORRIDOR_SCORE)
        spanje_gem.append(sum(sp_scores) / len(sp_scores) if sp_scores else STANDAARD_CORRIDOR_SCORE)

    for dag_idx in range(n_days):
        fr_dag     = max(0, dag_idx - SUPPLY_LAG_FRANCE)
        sp_dag     = max(0, dag_idx - SUPPLY_LAG_SPAIN)
        fr_supply  = france_gem[fr_dag]
        sp_supply  = spanje_gem[sp_dag]
        # Gecombineerde aanvoerfactor (Frankrijk: meer directe impact)
        supply_raw    = SUPPLY_FRANCE_WEIGHT * fr_supply + SUPPLY_SPAIN_WEIGHT * sp_supply
        supply_factor = round(SUPPLY_FACTOR_FLOOR + SUPPLY_FACTOR_RANGE * supply_raw, 3)  # floor op 30 %

        for punt in days_data[dag_idx]:
            lat = punt["latitude"]
            lon = punt["longitude"]
            if BENE_LAT_MIN <= lat <= BENE_LAT_MAX and BENE_LON_MIN <= lon <= BENE_LON_MAX:
                ruwe_score = punt["score"]
                adj_score  = round(min(1.0, max(0.0, ruwe_score * supply_factor)), 3)
                punt["score"]              = adj_score
                punt["klasse"]             = migratie_score_naar_klasse(adj_score)
                punt["kleur"]              = migratie_score_naar_kleur(adj_score)
                punt["supply_factor"]      = supply_factor
                punt["supply_frankrijk"]   = round(fr_supply, 3)
                punt["supply_spanje"]      = round(sp_supply, 3)
    return days_data


@st.cache_data(ttl=1800)
def laad_migratie_rasterdata_6daags(lat_step: float = None, lon_step: float = None):
    """
    Haal 6-daagse weervoorspelling op voor alle geldige rasterpunten
    (vandaag + 5 dagen). Zeepunten, VK, Ierland en Man-eiland zijn uitgefilterd.
    Retourneert (days_data, dag_datums, opgehaald_om).
    days_data[i] = lijst van punt-dicts op basis van middagwaarden (12:00 UTC).

    lat_step / lon_step bepalen de rasterresolutie:
      None / standaard : ~100×100 km (MIGRATIE_LAT_STEP × MIGRATIE_LON_STEP)
      0.5  / 0.65      : ~50×50 km  (4× meer punten, langzamere laadtijd)
    """
    punten = migratie_genereer_rasterpunten(lat_step=lat_step, lon_step=lon_step)
    vandaag = date.today()
    dag_datums = [_dag_label_nl(vandaag + timedelta(days=i)) for i in range(MIGRATIE_FORECAST_DAYS)]

    def verwerk_punt(punt: dict) -> tuple[list[dict], list[list[float]]]:
        hourly = _haal_weer_forecast_rasterpunt(punt)
        dag_punten = []
        uurscores_per_dag: list[list[float]] = []  # 24 uurlijkse scores per dag, voor tijdlijn
        for dag_idx in range(MIGRATIE_FORECAST_DAYS):
            cape_lijst = (hourly.get("cape") or [0] * MIGRATIE_FORECAST_HOURS) if hourly else [0] * MIGRATIE_FORECAST_HOURS
            blh_lijst  = (hourly.get("boundary_layer_height") or [500] * MIGRATIE_FORECAST_HOURS) if hourly else [500] * MIGRATIE_FORECAST_HOURS

            # --- Uurlijkse scores (alle 24 uur) voor tijdlijnvisualisatie en daggemiddelde ---
            uurscores: list[float] = []
            uurweer: list = []  # uurlijkse weerwaarden voor popup (raw floats)
            for uur in range(24):
                uur_idx = dag_idx * 24 + uur
                if hourly:
                    uur_weer = {
                        "temperature_2m":       _uur_waarde(hourly.get("temperature_2m"),     uur_idx, 12.0),
                        "wind_speed_10m":        _uur_waarde(hourly.get("wind_speed_10m"),     uur_idx,  0.0),
                        "wind_direction_10m":    _uur_waarde(hourly.get("wind_direction_10m"), uur_idx, 180.0),
                        "precipitation":         _uur_waarde(hourly.get("precipitation"),      uur_idx,  0.0),
                        "visibility":            _uur_waarde(hourly.get("visibility"),         uur_idx, 10000.0),
                        "cloud_cover":           _uur_waarde(hourly.get("cloud_cover"),        uur_idx,  0.0),
                        "pressure_msl":          _uur_waarde(hourly.get("pressure_msl"),       uur_idx, 1013.0),
                        "cape":                  _uur_waarde(cape_lijst,                       uur_idx,  0.0),
                        "boundary_layer_height": _uur_waarde(blh_lijst,                        uur_idx, 500.0),
                    }
                    uurscores.append(migratie_bereken_score_uitgebreid(
                        uur_weer, lat=punt["latitude"], lon=punt["longitude"]
                    ))
                    uurweer.append({
                        "wd": float(uur_weer["wind_direction_10m"]),
                        "ws": float(uur_weer["wind_speed_10m"]),
                        "t":  float(uur_weer["temperature_2m"]),
                        "p":  float(uur_weer["precipitation"]),
                        "pr": float(uur_weer["pressure_msl"]),
                        "b":  float(uur_weer["boundary_layer_height"]),
                    })
                else:
                    uurscores.append(0.5)
                    uurweer.append(None)
            uurscores_per_dag.append(uurscores)

            # Dagelijkse score = gemiddelde over alle 24 uur (vroeger: uitsluitend 12:00 UTC)
            score = round(sum(uurscores) / len(uurscores), 3) if uurscores else 0.5

            # Weerdisplay op 12:00 UTC voor popup / tooltip
            middag_idx = dag_idx * 24 + 12
            if hourly:
                weer = {
                    "temperature_2m":       _uur_waarde(hourly.get("temperature_2m"),     middag_idx, 12.0),
                    "wind_speed_10m":        _uur_waarde(hourly.get("wind_speed_10m"),     middag_idx,  0.0),
                    "wind_direction_10m":    _uur_waarde(hourly.get("wind_direction_10m"), middag_idx, 180.0),
                    "precipitation":         _uur_waarde(hourly.get("precipitation"),      middag_idx,  0.0),
                    "visibility":            _uur_waarde(hourly.get("visibility"),         middag_idx, 10000.0),
                    "cloud_cover":           _uur_waarde(hourly.get("cloud_cover"),        middag_idx,  0.0),
                    "pressure_msl":          _uur_waarde(hourly.get("pressure_msl"),       middag_idx, 1013.0),
                    "cape":                  _uur_waarde(cape_lijst,                       middag_idx,  0.0),
                    "boundary_layer_height": _uur_waarde(blh_lijst,                        middag_idx, 500.0),
                }
            else:
                weer = None

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
                "uurscores":        uurscores,
                "uurweer":          uurweer,
            })
        return dag_punten, uurscores_per_dag

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        alle_punt_resultaten = list(executor.map(verwerk_punt, punten))

    # Herorganiseer: [punt_idx][dag_idx] → [dag_idx][punt_idx]
    days_data: list[list[dict]] = [[] for _ in range(MIGRATIE_FORECAST_DAYS)]
    alle_uurscores: list[list[list[float]]] = []  # [punt_idx][dag_idx][uur]
    for dag_punten, uurscores_per_dag in alle_punt_resultaten:
        for dag_idx, dag_punt in enumerate(dag_punten):
            days_data[dag_idx].append(dag_punt)
        alle_uurscores.append(uurscores_per_dag)

    # Pas aanvoercorrectie toe: BE/NL-scores worden verminderd als France/Spanje
    # de dag ervoor slechte omstandigheden hadden (regen, tegenwind).
    days_data = _pas_aanvoer_toe(days_data)

    # Tijdlijndata: gemiddelde uurlijkse migratiescore over alle rasterpunten per dag
    n_tijdlijn_punten = len(alle_uurscores)
    uurgemiddelden_per_dag: list[list[float]] = []
    for dag_idx in range(MIGRATIE_FORECAST_DAYS):
        if n_tijdlijn_punten:
            uurgemiddelden = [
                round(
                    sum(alle_uurscores[p][dag_idx][u] for p in range(n_tijdlijn_punten))
                    / n_tijdlijn_punten,
                    3,
                )
                for u in range(24)
            ]
        else:
            uurgemiddelden = [0.5] * 24
        uurgemiddelden_per_dag.append(uurgemiddelden)

    opgehaald_om = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return days_data, dag_datums, opgehaald_om, uurgemiddelden_per_dag


# Controleer wijzigingen in invoer (gebruik session_state)
if "weer_last_locatie" not in st.session_state:
    st.session_state.weer_last_locatie = default_locatie
    st.session_state.weer_last_datum = default_datum
    st.session_state.weer_last_hours = default_hours

# Update alleen bij wijziging van locatie, datum of uren
if (
        locatie_keuze != st.session_state.weer_last_locatie
        or geselecteerde_datum != st.session_state.weer_last_datum
        or default_hours != st.session_state.weer_last_hours
):
    lat, lon, adres = toon_geolocatie_op_kaart(f"{locatie_keuze}, {land_keuze}")
    if lat and lon:
        gps_format = f"{round(lat, 2)}°{'N' if lat >= 0 else 'S'} {round(lon, 2)}°{'E' if lon >= 0 else 'W'}"
        weather_data = get_weather_data_historical(lat, lon, geselecteerde_datum)
        st.session_state.weer_last_locatie = locatie_keuze
        st.session_state.weer_last_datum = geselecteerde_datum
        st.session_state.weer_last_hours = default_hours
        st.session_state.weer_data = weather_data
        st.session_state.weer_lat = lat
        st.session_state.weer_lon = lon
        st.session_state.weer_adres = adres
        st.session_state.weer_gps_format = gps_format

# Toon GPS-gegevens en tijden in de sidebar
if "weer_gps_format" in st.session_state:

    # Splits de string op basis van de komma's
    adresdelen = st.session_state.weer_adres.split(',')

    # Haal de eerste (stad) en de laatste (land) delen van het adres
    stad = adresdelen[0].strip()  # Bruges
    land = adresdelen[-1].strip()  # Belgium


    # Haal zonsopgang- en zonsondergangtijden op
    if "weer_data" in st.session_state:
        weather_data = st.session_state.weer_data
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
        if "weer_lat" in st.session_state and "weer_lon" in st.session_state:
            # Maak een nieuwe kaart met de opgegeven coördinaten
            m = folium.Map(location=[st.session_state.weer_lat, st.session_state.weer_lon], zoom_start=9)

            # Maak een marker met een groene kleur en een rood 'binocular' icoon
            #marker = folium.Marker(
            #    location=[st.session_state.weer_lat, st.session_state.weer_lon],
            #    icon=Icon(icon="fa-binoculars", prefix='fa', color='green', icon_color='white')
            #    # Font Awesome 'binoculars' icoon
            # Gebruik een aangepaste afbeelding als icoon
            icon_path = 'images//Milvus1.png'  # Vervang dit door een URL of pad naar jouw afbeelding
            eagle_icon = folium.CustomIcon(icon_path, icon_size=(25, 38))

            # Voeg de marker toe aan de kaart
            marker1 = folium.Marker(
                location=[st.session_state.weer_lat, st.session_state.weer_lon],
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
    if "weer_data" in st.session_state:
        weather_data = st.session_state.weer_data

        # Maak een DataFrame van de weergegevens
        weather_df = pd.DataFrame(weather_data["hourly"])
        weather_df["time"] = weather_df["time"].str.split("T").str[1]

        # Default slider range van 08:00 tot 18:00 uur
        default_start = 5  # 05:00 uur
        default_end = 22   # 22:00 uur
        if "weer_last_hours" not in st.session_state:
            st.session_state.weer_last_hours = default_hours  # Zorg ervoor dat er altijd een standaardwaarde is
        # Verkrijg het tijdsbereik van de slider in de sidebar (default tussen 08:00 en 18:00 uur)

        # Controleer of de sliderwaarden van start_end veranderd zijn
        if (
                locatie_keuze != st.session_state.weer_last_locatie
                or geselecteerde_datum != st.session_state.weer_last_datum
                or default_hours != st.session_state.weer_last_hours
        ):
            lat, lon, adres = toon_geolocatie_op_kaart(f"{locatie_keuze}, {land_keuze}")
            if lat and lon:
                gps_format = f"{round(lat, 2)}°{'N' if lat >= 0 else 'S'} {round(lon, 2)}°{'E' if lon >= 0 else 'W'}"
                weather_data = get_weather_data_historical(lat, lon, geselecteerde_datum)

                # Update de session_state met de nieuwe waarden
                st.session_state.weer_last_locatie = locatie_keuze
                st.session_state.weer_last_datum = geselecteerde_datum
                st.session_state.weer_last_hours = start_end
                st.session_state.weer_data = weather_data
                st.session_state.weer_lat = lat
                st.session_state.weer_lon = lon
                st.session_state.weer_adres = adres
                st.session_state.weer_gps_format = gps_format


        start_end = st.sidebar.slider("Selecteer het tijdsbereik", 0, 23, (default_start, default_end), format = "%d:00", key="sidebaronder")
        #min_value = 0,
        #max_value = 23,
        #value = default_hours,
        #format = "%d:00",
        st.sidebar.write(f"**{land}**, {stad}")
        st.sidebar.write(f"**GPS:** {st.session_state.weer_gps_format}")
        #st.sidebar.write(f"{lat}, {lon}")
        #st.sidebar.write(f"{st.session_state.weer_lat}, {st.session_state.weer_lon}")

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
    if "weer_lat" not in st.session_state or "weer_lon" not in st.session_state:
        st.error("Latitude en Longitude zijn niet ingesteld. Stel eerst een locatie in.")
    else:
        # Haal waarden op uit sessiestatus
        latitude = st.session_state.weer_lat
        longitude = st.session_state.weer_lon

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
        lat = st.session_state.get("weer_lat", 50.681)  # Standaardwaarde als lat niet is ingesteld
        lon = st.session_state.get("weer_lon", 4.768)   # Standaardwaarde als lon niet is ingesteld

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
    with st.expander("ℹ️ Extra informatie"):
        st.markdown("""
    Vijfdaagse migratievoorspelling op basis van weergegevens voor een configureerbaar raster
    over **België, Nederland en Duitsland** (en omgeving).
    Rasterpunten in zee, Groot-Brittannië, Ierland en het Man-eiland worden buiten beschouwing gelaten.

    **Ankerpunt:** Tarifa (Spanje) — de klassieke doortochtpoort vanuit Afrika (Gibraltar-corridor).

    **Wetenschappelijke factoren (gemiddeld over alle 24 uur per dag — dag én nacht):**
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

    📦 **Aanvoercorrectie vanuit het zuiden** (BE/NL-zone):
    Migratie is een *pijplijn*. Vogels passeren eerst Spanje (Tarifa-corridor, 36–43°N) en dan
    Frankrijk (43–49.5°N) vóór ze België bereiken. Regen of tegenwind in die zones blokkeert de
    aanvoer — ook als de lokale omstandigheden in België die dag uitstekend zijn.
    De BE/NL-scores worden daarom vermenigvuldigd met een aanvoerfactor (min. 30 %) op basis van
    de gemiddelde passeerscores van respectievelijk Frankrijk (1 dag eerder) en Spanje (2 dagen eerder).
    *(Bronnen: Berthold 2001; Ellegren 1993; Schaub et al. 2004 PNAS)*

    **Vlieghoogte & zichtbaarheid (cirkelgrootte op de kaart):**
    Op *gunstige trekdagen met weinig wind* vliegen vogels **hoog** en worden ze minder opgemerkt.
    Een hogere windkracht (< 7 Bf) duwt vogels naar **lagere hoogtes** en maakt ze beter waarneembaar.
    De cirkelgrootte geeft dit aan: 🟢 *groot* = vogels laag & zichtbaar · 🟢 *klein* = vogels hoog of trek beperkt.

    **Kleurschaal (10 banden):** 🟢 Uitstekend ≥ 90 · Zeer goed 80–90 · Goed 70–80 · Vrij goed 60–70 · Redelijk 50–60 · Matig 40–50 · Ongunstig 30–40 · Slecht 20–30 · Zeer slecht 10–20 · 🔴 Verwaarloosbaar < 10

    **🌬️ Zeebries-vlaggen (kustlocaties Saint-Malo t/m Esbjerg):**
    Op elke dagkaart zijn de zeebries-vlaggen zichtbaar voor de kustlocaties.
    De vlaggen zijn tijdsgevoelig: selecteer een uur om te zien of er zeebries verwacht wordt op dat specifieke moment.
    - 🚩 **Rode vlag** = zeebries waarschijnlijk (ΔT ≥ 4 °C, wind < 3 Bf, bewolking < 60 %)
    - 🟢 **Groene vlag** = geen zeebries verwacht
    *SST via Open-Meteo Marine API; fallback = klimatologisch maandgemiddelde Zuidelijke Noordzee.*

    *Gegevens gecacheerd voor 30 minuten. Klik op "Ververs nu" voor actuele data.*
        """)

    if st.button("🔄 Ververs nu", key="ververs_raster_6d"):
        laad_migratie_rasterdata_6daags.clear()
        laad_zeebries_kustdata.clear()
        st.rerun()

    with st.spinner("Weervoorspelling ophalen voor 6-daags migratieraster — even geduld..."):
        days_data, dag_datums, opgehaald_om, uurgemiddelden_per_dag = laad_migratie_rasterdata_6daags()

    # Laad zeebries-data eenmalig voor alle dagkaarten
    _zb_per_dag: list[list[dict]] = [[] for _ in range(ZEEBRIES_HORIZON_DAYS)]
    _zb_laad_fout = False
    try:
        _zb_per_dag, _, _ = laad_zeebries_kustdata()
    except Exception:
        _zb_laad_fout = True

    n_punten = len(days_data[0]) if days_data else 0
    _zb_n_locaties = len(_zb_per_dag[0]) if _zb_per_dag and _zb_per_dag[0] else 0
    _zb_status = (
        f"🌬️ {_zb_n_locaties} zeebries-kustpunten geladen"
        if not _zb_laad_fout and _zb_n_locaties > 0
        else "⚠️ Zeebries-data niet beschikbaar (vlaggen ontbreken)"
    )
    st.caption(
        f"⏱️ Gegevens opgehaald om **{opgehaald_om} UTC** — "
        f"{n_punten} rasterpunten per dag (~100 × 100 km, VK/Ierland/Man-eiland uitgesloten) — "
        f"{_zb_status}"
    )
    if _zb_laad_fout:
        st.warning("⚠️ Zeebries-voorspelling kon niet worden opgehaald. Kustkaarten tonen geen zeebries-vlaggen.")

    # Gedeelde kleurlegende (eenmalig boven alle 6 kaarten)
    st.markdown(
        """
        <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;
                    margin-bottom:8px;font-size:13px;">
          <span><span style="background:#17ff74;padding:2px 8px;border-radius:4px;
                color:black;">●</span>&nbsp;Uitstekend ≥ 90</span>
          <span><span style="background:#45ff5d;padding:2px 8px;border-radius:4px;
                color:black;">●</span>&nbsp;Zeer goed 80–90</span>
          <span><span style="background:#73ff45;padding:2px 8px;border-radius:4px;
                color:black;">●</span>&nbsp;Goed 70–80</span>
          <span><span style="background:#a2ff2e;padding:2px 8px;border-radius:4px;
                color:black;">●</span>&nbsp;Vrij goed 60–70</span>
          <span><span style="background:#d0ff17;padding:2px 8px;border-radius:4px;
                color:black;">●</span>&nbsp;Redelijk 50–60</span>
          <span><span style="background:#ffff00;padding:2px 8px;border-radius:4px;
                color:black;">●</span>&nbsp;Matig 40–50</span>
          <span><span style="background:#edc600;padding:2px 8px;border-radius:4px;
                color:black;">●</span>&nbsp;Ongunstig 30–40</span>
          <span><span style="background:#db8d00;padding:2px 8px;border-radius:4px;
                color:white;">●</span>&nbsp;Slecht 20–30</span>
          <span><span style="background:#ca5500;padding:2px 8px;border-radius:4px;
                color:white;">●</span>&nbsp;Zeer slecht 10–20</span>
          <span><span style="background:#b81c00;padding:2px 8px;border-radius:4px;
                color:white;">●</span>&nbsp;Verwaarloosbaar &lt; 10</span>
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

        # Initialiseer sessie-state voor uur-selectie als nog niet aanwezig
        if f"uur_radio_{dag_idx}" not in st.session_state:
            st.session_state[f"uur_radio_{dag_idx}"] = "📊 Dag"
        _uur_keuze_val = st.session_state[f"uur_radio_{dag_idx}"]
        selected_uur = None if _uur_keuze_val == "📊 Dag" else int(_uur_keuze_val.split(":")[0])

        uur_label = f" · uur {selected_uur:02d}:00 UTC" if selected_uur is not None else ""
        st.markdown(
            f"### {dag_titel} — {dag_label}  ·  gem. score: {gem_score}/100  ·  vlieghoogte: {vh_meest}{uur_label}"
        )

        col_kaart, col_uren = st.columns([4, 1])

        m_dag = folium.Map(location=[KAART_CENTER_LAT, KAART_CENTER_LON], zoom_start=4, tiles="CartoDB positron")

        for punt in raster_dag:
            # Gebruik uurlijkse score wanneer een uur geselecteerd is
            if selected_uur is not None:
                _uurscores = punt.get("uurscores", [])
                if 0 <= selected_uur < len(_uurscores):
                    uur_score = _uurscores[selected_uur]
                else:
                    uur_score = punt["score"]
                score_pct  = int(uur_score * 100)
                kleur      = migratie_score_naar_kleur(uur_score)
                klasse_lbl = migratie_score_naar_klasse(uur_score)
            else:
                score_pct  = int(punt["score"] * 100)
                kleur      = migratie_score_naar_kleur(punt["score"])
                klasse_lbl = punt["klasse"]

            # Weerwaarden voor popup: daggemiddelde als standaard, uurspecifiek indien geselecteerd
            disp_wind_richting = punt["wind_richting"]
            disp_wind_kracht   = punt["wind_kracht"]
            disp_temp          = punt["temperatuur"]
            disp_neerslag      = punt["neerslag"]
            disp_druk          = punt["druk"]
            disp_blh           = punt["blh"]
            vh_lbl             = punt.get("vlieghoogte", "?")
            vh_tip             = punt.get("vlieghoogte_tip", "")
            radius             = punt.get("marker_radius", 7)
            if selected_uur is not None:
                _uurweer = punt.get("uurweer") or []
                if 0 <= selected_uur < len(_uurweer) and _uurweer[selected_uur]:
                    _hw = _uurweer[selected_uur]
                    disp_wind_richting = graden_naar_windrichting(_hw["wd"])
                    disp_wind_kracht   = kmh_naar_beaufort(_hw["ws"])
                    disp_temp          = f"{_hw['t']:.1f}"
                    disp_neerslag      = f"{_hw['p']:.1f}"
                    disp_druk          = f"{_hw['pr']:.0f}"
                    disp_blh           = f"{int(_hw['b'])}"
                    vh_lbl, vh_tip, radius = migratie_vlieghoogte(_hw["ws"])

            score_info   = (
                f"Migratiecode: {score_pct}/100 (uur {selected_uur:02d}:00 UTC)"
                if selected_uur is not None
                else f"Migratiecode: {score_pct}/100 (daggemiddelde)"
            )
            weerdisplay_note = (
                f"Weerdisplay: {selected_uur:02d}:00 UTC"
                if selected_uur is not None
                else "Weerdisplay: 12:00 UTC · Score: 24-uurs gemiddelde"
            )
            popup_html = (
                f"<div style='font-size:13px;min-width:210px;'>"
                + f"<b>{score_info}</b><br>"
                f"<b>Klasse: {klasse_lbl}</b><br>"
                f"📍 {punt['latitude']}°N, {punt['longitude']}°E<br>"
                f"🧭 Wind: {disp_wind_richting} {disp_wind_kracht} Bf<br>"
                f"🌡️ Temp: {disp_temp} °C<br>"
                f"🌧️ Neerslag: {disp_neerslag} mm<br>"
                f"📊 Druk: {disp_druk} hPa<br>"
                f"🌀 BLH: {disp_blh} m<br>"
                f"<b>🦅 Vlieghoogte: {vh_lbl}</b><br>"
                f"<i style='font-size:11px;color:#555'>{vh_tip}</i>"
                f"<br><i style='font-size:10px;color:#888'>{weerdisplay_note}</i>"
                + (
                    f"<br><span style='color:#c47000;font-size:11px;'>"
                    f"🌟 BE/NL zone: ZO-wind (3–5 Bf) = optimaal</span>"
                    f"<br><span style='color:#0066cc;font-size:11px;'>"
                    f"📦 Aanvoer: {int(punt.get('supply_factor', 1.0) * 100)}% "
                    f"(Fr: {int(punt.get('supply_frankrijk', 0.5) * 100)}% / "
                    f"Sp: {int(punt.get('supply_spanje', 0.5) * 100)}%)</span>"
                    if punt.get("be_nl_zone") else ""
                )
                + "</div>"
            )
            tooltip_tekst = (
                f"{dag_label} | {score_pct}/100 ({klasse_lbl}) "
                f"| {punt['latitude']}°N {punt['longitude']}°E "
                f"| {disp_wind_richting} {disp_wind_kracht} Bf "
                f"| {disp_druk} hPa | ✈️ {vh_lbl}"
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

        # Zeebries-vlaggen op de kaart (tijdsgevoelig)
        _zb_dagdata = _zb_per_dag[dag_idx] if dag_idx < len(_zb_per_dag) else []
        for _zb_punt in _zb_dagdata:
            _zb_uur_flags = _zb_punt.get("zeebries_uren", [])
            if selected_uur is not None:
                _actief = (
                    _zb_uur_flags[selected_uur]
                    if 0 <= selected_uur < len(_zb_uur_flags)
                    else False
                )
            else:
                _actief = _zb_punt["zeebries_actief"]

            _n_uren  = _zb_punt["zeebries_n_uren"]
            _start_h = _zb_punt.get("zeebries_start")
            _stop_h  = _zb_punt.get("zeebries_stop")
            _sst     = _zb_punt.get("sst_middag")
            _dt_max  = max(_zb_punt.get("delta_t_uren", []) or [0.0])
            _sst_is_fallback = (_sst == _NOORDZEE_SST_FALLBACK.get(date.today().month))
            _naam    = _zb_punt.get("naam", "")

            _vlag_kleur = "#cc0000" if _actief else "#00cc00"
            _vlag_html = (
                f"<div style='position:relative;width:26px;height:34px;"
                f"background:transparent;'>"
                f"<div style='position:absolute;left:3px;top:0;width:3px;height:34px;"
                f"background:#222222;'></div>"
                f"<div style='position:absolute;left:6px;top:2px;width:18px;height:14px;"
                f"background:{_vlag_kleur};border:1.5px solid rgba(0,0,0,0.6);'></div>"
                f"</div>"
            )
            if selected_uur is not None:
                _uur_info = (
                    f"⏰ {selected_uur:02d}:00 UTC — "
                    + ("<b style='color:#cc0000;'>🚩 Zeebries</b>" if _actief else "<b style='color:#00aa00;'>✅ Geen zeebries</b>")
                    + "<br>"
                )
            else:
                _uur_info = (
                    (f"⏰ {_start_h:02d}:00–{_stop_h:02d}:00 UTC ({_n_uren}u)<br>"
                     if _actief and _start_h is not None and _stop_h is not None else "")
                )
            _popup_html = (
                f"<div style='font-size:12px;min-width:200px;'>"
                f"<b>{_naam}</b><br>"
                f"{'<b style=\"color:#cc0000;\">🚩 Zeebries verwacht</b>' if _actief else '<b style=\"color:#00aa00;\">✅ Geen zeebries</b>'}<br>"
                f"📍 {_zb_punt['latitude']}°N, {_zb_punt['longitude']}°E<br>"
                + _uur_info
                + (f"🌡️ Max ΔT (land−zee): <b>{_dt_max:.1f} °C</b><br>" if _dt_max > 0 else "")
                + (f"🌊 SST: {_sst:.1f} °C"
                   + (" ⚠️ <i>(klimatol.)</i>" if _sst_is_fallback else "")
                   + "<br>" if _sst is not None else "")
                + "</div>"
            )
            _tooltip = (
                f"🌬️ {'🚩 ZEEBRIES — ' if _actief else '✅ '}{_naam}"
                + (f" | {selected_uur:02d}:00 UTC" if selected_uur is not None else "")
                + (f" | {_start_h:02d}h–{_stop_h:02d}h UTC"
                   if selected_uur is None and _actief and _start_h is not None and _stop_h is not None
                   else "")
            )
            folium.Marker(
                location=[_zb_punt["latitude"], _zb_punt["longitude"]],
                icon=folium.DivIcon(
                    icon_size=(26, 34),
                    icon_anchor=(3, 34),
                    html=_vlag_html,
                    class_name="",
                ),
                popup=folium.Popup(_popup_html, max_width=230),
                tooltip=_tooltip,
            ).add_to(m_dag)

        with col_kaart:
            st_folium(
                m_dag, height=500, returned_objects=[],
                use_container_width=True, key=f"raster_dag_{dag_idx}",
            )

        with col_uren:
            _uur_opties = ["📊 Dag"] + [f"{h:02d}:00" for h in range(24)]
            st.radio(
                "Uur (UTC):",
                _uur_opties,
                key=f"uur_radio_{dag_idx}",
                label_visibility="collapsed",
            )

        st.divider()


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
