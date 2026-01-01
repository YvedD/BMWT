# Hoe maak je een Fork van een GitHub Repository?

## Handleiding voor Beginners: Fork maken van BirdNET-Pi

Deze handleiding legt stap voor stap uit hoe je een **fork** (kopie) maakt van de `Nachtzuster/BirdNET-Pi` repository naar jouw eigen GitHub account met de naam `YvedD/BirdNET-Pi-MigCount`.

### Wat is een Fork?

Een fork is een kopie van iemand anders' repository die je in je eigen GitHub account plaatst. Je kunt dan wijzigingen maken zonder de originele repository te beïnvloeden.

---

## Stap 1: Navigeer naar de BirdNET-Pi Repository

1. Open je webbrowser
2. Ga naar: **https://github.com/Nachtzuster/BirdNET-Pi**
3. Zorg ervoor dat je **ingelogd** bent op je GitHub account (YvedD)

---

## Stap 2: Klik op de "Fork" Knop

1. Rechtsboven op de repository pagina zie je een knop met **"Fork"**
2. Klik op deze knop
3. Je wordt doorgestuurd naar een nieuwe pagina: "Create a new fork"

---

## Stap 3: Configureer je Fork

Op de "Create a new fork" pagina zie je verschillende opties:

### 3.1 Repository Naam Aanpassen

**BELANGRIJK:** Standaard krijgt je fork dezelfde naam als het origineel (`BirdNET-Pi`), maar jij wilt een andere naam:

1. Zoek het veld **"Repository name"**
2. Verwijder de standaard naam `BirdNET-Pi`
3. Typ de nieuwe naam: **`BirdNET-Pi-MigCount`**

### 3.2 Beschrijving (Optioneel)

1. In het veld **"Description"** kun je een beschrijving toevoegen, bijvoorbeeld:
   - "Fork van BirdNET-Pi voor vogeltrek telling (Migration Counting)"
   - Of laat het leeg, dat mag ook

### 3.3 Kopieer alleen de Main Branch

1. Zorg ervoor dat het vakje **"Copy the main branch only"** is **AANGEVINKT** (standaard staat dit meestal al aan)
2. Dit zorgt ervoor dat je alleen de hoofdbranch kopieert en niet alle ontwikkel-branches

---

## Stap 4: Maak de Fork aan

1. Controleer nog een keer of alles klopt:
   - Owner: **YvedD**
   - Repository name: **BirdNET-Pi-MigCount**
   - "Copy the main branch only" is aangevinkt
2. Klik op de groene knop **"Create fork"** onderaan de pagina
3. GitHub begint nu met het kopiëren van de repository (dit kan even duren)

---

## Stap 5: Klaar! Je hebt nu je eigen Fork

Na enkele seconden word je automatisch doorgestuurd naar jouw nieuwe repository:

**https://github.com/YvedD/BirdNET-Pi-MigCount**

Je ziet nu:
- Bovenaan staat: **"YvedD/BirdNET-Pi-MigCount"**
- Er staat een melding: "forked from Nachtzuster/BirdNET-Pi"
- Alle bestanden van de originele repository zijn gekopieerd

---

## Extra Tips voor Beginners

### Wat kun je nu doen?

1. **Code bekijken**: Blader door de bestanden in je fork
2. **Wijzigingen maken**: Klik op een bestand en dan op het potlood-icoon (✏️) om te bewerken
3. **Klonen naar je computer**: Klik op de groene knop **"Code"** en kopieer de URL om de repository lokaal te klonen

### Sync houden met het origineel

Als de originele `Nachtzuster/BirdNET-Pi` repository wordt bijgewerkt, kun je deze wijzigingen later ook naar jouw fork halen:

1. Ga naar jouw fork: **https://github.com/YvedD/BirdNET-Pi-MigCount**
2. Klik op **"Sync fork"** (verschijnt als er updates zijn)
3. Klik op **"Update branch"**

### Voorkomen van ongewenste commits naar Nachtzuster repository

**BELANGRIJK:** Als je je fork lokaal hebt gekloond op je computer (bijvoorbeeld op je Raspberry Pi 4B), wil je voorkomen dat je per ongeluk wijzigingen pusht naar de originele Nachtzuster repository.

#### CLI Commando's voor je Raspberry Pi 4B

Als je je fork al hebt gekloond op je Raspberry Pi:

```bash
# Ga naar de directory van je gekloonde repository
cd ~/BirdNET-Pi-MigCount  # Pas dit aan naar jouw pad

# Bekijk de huidige remote configuratie
git remote -v

# Verwijder de upstream remote als deze naar Nachtzuster wijst
git remote remove upstream

# Voeg je eigen fork toe als origin (als deze er nog niet is)
git remote set-url origin https://github.com/YvedD/BirdNET-Pi-MigCount.git

# Controleer of het goed is ingesteld
git remote -v
# Je zou moeten zien:
# origin  https://github.com/YvedD/BirdNET-Pi-MigCount.git (fetch)
# origin  https://github.com/YvedD/BirdNET-Pi-MigCount.git (push)
```

#### Je RPi 4B forceren om te synchroniseren met je eigen GitHub fork

Als je wilt dat je lokale Raspberry Pi 4B **exact hetzelfde** is als jouw GitHub fork (YvedD/BirdNET-Pi-MigCount), gebruik dan deze commando's:

**⚠️ WAARSCHUWING:** Dit verwijdert ALLE lokale wijzigingen op je RPi en vervangt deze met wat er op GitHub staat!

```bash
# Stap 1: Ga naar je repository directory op de RPi
cd ~/BirdNET-Pi-MigCount  # Pas aan naar waar je repository staat

# Stap 2: Controleer dat origin naar je eigen fork wijst
git remote -v
# Moet tonen: origin  https://github.com/YvedD/BirdNET-Pi-MigCount.git

# Als origin niet correct is ingesteld, stel deze in:
git remote set-url origin https://github.com/YvedD/BirdNET-Pi-MigCount.git

# Stap 3: Haal de laatste versie op van je GitHub fork
git fetch origin

# Stap 4: Gooi alle lokale wijzigingen weg en forceer sync met GitHub
git reset --hard origin/main

# Stap 5: Zorg dat je werkdirectory schoon is
git clean -fd

# Klaar! Je RPi is nu volledig in sync met je GitHub fork
```

**Verificatie:** Controleer of alles gesynchroniseerd is:
```bash
# Dit commando moet tonen: "Your branch is up to date with 'origin/main'"
git status
```

#### Hard Reset: Je lokale repository synchroniseren

Als je je lokale repository volledig wilt synchroniseren met de Nachtzuster fork (bijvoorbeeld om een schone start te maken):

**⚠️ WAARSCHUWING:** Een hard reset verwijdert ALLE lokale wijzigingen die je hebt gemaakt. Zorg dat je belangrijke wijzigingen eerst hebt opgeslagen!

```bash
# Stap 1: Ga naar je repository directory
cd ~/BirdNET-Pi-MigCount

# Stap 2: Voeg Nachtzuster toe als upstream remote (als deze er nog niet is)
git remote add upstream https://github.com/Nachtzuster/BirdNET-Pi.git

# Stap 3: Haal de laatste wijzigingen op van Nachtzuster
git fetch upstream

# Stap 4: Hard reset naar de main branch van Nachtzuster
git reset --hard upstream/main

# Stap 5: Force push naar je eigen fork 
# ⚠️ LET OP: Dit overschrijft de geschiedenis in je remote fork!
# Alleen doen als je ZEKER weet dat je alle remote wijzigingen wilt verwijderen!
git push origin main --force

# Stap 6: Verwijder de upstream remote weer om ongewenste pushes te voorkomen
git remote remove upstream
```

#### Alternatief: Soft sync (behoudt lokale wijzigingen)

Als je je lokale wijzigingen wilt behouden maar toch synchroniseren:

```bash
# Voeg Nachtzuster toe als upstream
git remote add upstream https://github.com/Nachtzuster/BirdNET-Pi.git

# Haal wijzigingen op
git fetch upstream

# Merge de wijzigingen (dit behoudt je lokale changes)
git merge upstream/main

# Push naar je eigen fork
git push origin main

# Verwijder upstream weer
git remote remove upstream
```

### Het newinstaller.sh script aanpassen

Als je wilt dat gebruikers van jouw fork updates ontvangen vanuit **jouw repository** in plaats van de Nachtzuster repository, moet je het `newinstaller.sh` script aanpassen.

#### Stap-voor-stap aanpassing:

1. **Ga naar je fork op GitHub**: https://github.com/YvedD/BirdNET-Pi-MigCount

2. **Navigeer naar het script**:
   - Klik op de `main` branch
   - Navigeer naar het bestand: `/scripts/newinstaller.sh` (of waar het script zich bevindt)

3. **Bewerk het bestand**:
   - Klik op het potlood-icoon (✏️) rechtsboven

4. **Zoek en vervang repository URL's**:
   
   Zoek naar regels die verwijzen naar:
   ```bash
   https://github.com/Nachtzuster/BirdNET-Pi
   ```
   
   Vervang deze door:
   ```bash
   https://github.com/YvedD/BirdNET-Pi-MigCount
   ```

5. **Specifieke aanpassingen te zoeken**:
   
   Let op deze patronen in het script:
   ```bash
   # Voorbeeld van wat je mogelijk tegenkomt:
   REPO_URL="https://github.com/Nachtzuster/BirdNET-Pi"
   git clone https://github.com/Nachtzuster/BirdNET-Pi.git
   ```
   
   Wijzig deze naar:
   ```bash
   REPO_URL="https://github.com/YvedD/BirdNET-Pi-MigCount"
   git clone https://github.com/YvedD/BirdNET-Pi-MigCount.git
   ```

6. **Commit de wijzigingen**:
   - Scroll naar beneden
   - Voeg een commit message toe: "Update installer to use YvedD fork"
   - Klik op **"Commit changes"**

#### Via de Command Line (op je Raspberry Pi):

Als je het script lokaal wilt aanpassen:

```bash
# Navigeer naar je repository
cd ~/BirdNET-Pi-MigCount

# Open het bestand met nano of vim
nano scripts/newinstaller.sh

# Vervang alle instanties van Nachtzuster door jouw repository
# Gebruik Ctrl+W om te zoeken in nano
# Zoek: Nachtzuster/BirdNET-Pi
# Vervang door: YvedD/BirdNET-Pi-MigCount

# Sla op en sluit (Ctrl+X, Y, Enter in nano)

# Commit de wijziging
git add scripts/newinstaller.sh
git commit -m "Update installer to use YvedD/BirdNET-Pi-MigCount fork"

# Push naar je fork
git push origin main
```

#### Andere bestanden die mogelijk aangepast moeten worden:

Controleer ook deze bestanden op verwijzingen naar de Nachtzuster repository:
- `README.md`
- `update.sh` (als die bestaat)
- Andere installatie of update scripts
- Configuratiebestanden

### Wijzigingen terug sturen naar het origineel

Als je verbeteringen hebt gemaakt die je wilt delen met de originele repository:

1. Klik op **"Contribute"**
2. Klik op **"Open pull request"**
3. Beschrijf je wijzigingen
4. Dien de pull request in

---

## Hulp Nodig?

Als je problemen hebt of vragen, kun je:
- Een **Issue** openen in deze repository
- De GitHub documentatie bekijken: https://docs.github.com/en/get-started/quickstart/fork-a-repo
- Contact opnemen via GitHub

---

## Samenvatting van de Stappen

1. ✅ Ga naar https://github.com/Nachtzuster/BirdNET-Pi
2. ✅ Klik op **"Fork"**
3. ✅ Wijzig de naam naar: **BirdNET-Pi-MigCount**
4. ✅ Vink **"Copy the main branch only"** aan
5. ✅ Klik op **"Create fork"**
6. ✅ Klaar! Je hebt nu **YvedD/BirdNET-Pi-MigCount**

---

*Laatst bijgewerkt: Januari 2026*
