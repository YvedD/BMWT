# BMWT
Bird Migration Weather Tool

## Persistente opslag in Streamlit Cloud

De AI-trainingsdata staat in `data/migration/observations.json`.

### Aanbevolen: Firebase + Firestore

Voor online persistente opslag van observaties gebruikt BMWT bij voorkeur Firestore. Voeg in Streamlit Cloud één van deze secrets toe:

- `firebase_service_account`: tabel met de volledige inhoud van je Firebase service-account JSON
- of `firebase_service_account_json`: dezelfde JSON als één string

Optioneel:

- `firestore_collection`: standaard `bmwt_observations`

Voorbeeld in `.streamlit/secrets.toml`:

```toml
[firebase_service_account]
type = "service_account"
project_id = "jouw-project-id"
private_key_id = "..."
private_key = """-----BEGIN PRIVATE KEY-----
...
-----END PRIVATE KEY-----
"""
client_email = "firebase-adminsdk-xxxxx@jouw-project-id.iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk-xxxxx%40jouw-project-id.iam.gserviceaccount.com"
universe_domain = "googleapis.com"
```

### Tijdelijke fallback: lokale JSON + GitHub sync

Lokaal in Streamlit wordt `data/migration/observations.json` ook bijgewerkt als cache/fallback. Wil je die JSON-fallback bovendien online in de GitHub-repository bewaren na een herstart of redeploy van Streamlit Cloud, voeg dan deze secret toe:

- `github_token`: GitHub token met `contents:write`

Optioneel:

- `github_repository`: standaard `YvedD/BMWT`
- `github_branch`: doelbranch; standaard de default branch van de repository

This tool has as purpose to give users a diversity of weather data, migration related tools and a good weather forecast for a given location.
