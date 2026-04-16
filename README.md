# BMWT
Bird Migration Weather Tool

## Persistente opslag in Streamlit Cloud

De AI-trainingsdata staat in `data/migration/observations.json`.

Lokaal in Streamlit wordt dit bestand meteen bijgewerkt. Wil je dat nieuwe observaties ook in de GitHub-repository bewaard blijven na een herstart of redeploy van Streamlit Cloud, voeg dan deze secret toe:

- `github_token`: GitHub token met `contents:write`

Optioneel:

- `github_repository`: standaard `YvedD/BMWT`
- `github_branch`: doelbranch; standaard de default branch van de repository

This tool has as purpose to give users a diversity of weather data, migration related tools and a good weather forecast for a given location.
