import streamlit as st
from geopy.geocoders import Nominatim, OpenCage
import folium
import requests
from streamlit_folium import st_folium
import pandas as pd
from datetime import date, datetime
from dateutil.parser import parse
import pytz
from io import BytesIO
import time
from geopy.exc import GeocoderUnavailable
import streamlit.components.v1 as components
from soorten_geluiden import iframe_data


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
tabs = st.tabs(["Weergegevens", "Voorspellingen", "CROW project", "Kraanvogel Radar", "🎧 Vluchtroepen","Gebruiksaanwijzing"])


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
        st.stop()  # Stop de app als de vereiste gegevens ontbreken

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
                    kleur = 'background-color: #ffcb53'
                if richting == 'ZO':
                    kleur = 'background-color: #ff4d00'
                elif richting == 'ZZO':
                    kleur = 'background-color: #ffde7f'
                elif richting == 'Z':
                    kleur = 'background-color: #fff97f'
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
    
    # Standaardwaarden instellen als ze niet bestaan
    if 'lat' not in st.session_state or 'lon' not in st.session_state:
        st.session_state.lat = 52.3794  # Amsterdam
        st.session_state.lon = 4.9009

with tabs[2]:
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
with tabs[3]:
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


with tabs[4]:
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


with tabs[5]:
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
