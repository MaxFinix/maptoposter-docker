"""
Localization helpers for the MaptoPoster web interface.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

SUPPORTED_LANGUAGES = ("de", "en")
DEFAULT_LANGUAGE = "de"
LANGUAGE_COOKIE_NAME = "maptoposter_lang"
CM_PER_INCH = 2.54
COOKIE_MAX_AGE = 60 * 60 * 24 * 365


TEXTS: dict[str, dict[str, Any]] = {
    "de": {
        "meta": {
            "title": "MaptoPoster",
        },
        "language": {
            "label": "Sprache",
            "de": "DE",
            "en": "EN",
            "de_long": "Deutsch",
            "en_long": "English",
        },
        "hero": {
            "copy": (
                "Erstelle druckbare Stadtplakate direkt im Browser, speichere jedes Poster "
                "in deinem Unraid-Appdata-Ordner und lade fertige Dateien herunter, ohne im "
                "Dateisystem zu suchen."
            ),
            "themes_stat": "Verfügbare Themes aus der eingebauten JSON-Bibliothek.",
            "files_stat": "Generierte Dateien, die aktuell im Webinterface verfügbar sind.",
        },
        "form": {
            "title": "Poster erstellen",
            "description": (
                "Nutze Stadt und Land für das normale Geocoding oder überschreibe den Mittelpunkt "
                "mit eigenen Koordinaten."
            ),
            "city": "Stadt",
            "city_placeholder": "Paris",
            "country": "Land",
            "country_placeholder": "Frankreich",
            "theme": "Theme",
            "format": "Format",
            "distance": "Entfernung (Meter)",
            "distance_placeholder": "18000",
            "width": "Breite (cm)",
            "width_placeholder": "30,5",
            "height": "Höhe (cm)",
            "height_placeholder": "40,6",
            "size_note": "Breite und Höhe werden in Zentimetern angegeben, maximal 50,8 cm.",
            "latitude": "Breitengrad überschreiben",
            "latitude_placeholder": "48.8566",
            "longitude": "Längengrad überschreiben",
            "longitude_placeholder": "2.3522",
            "display_city": "Anzeigestadt",
            "display_city_placeholder": "PARIS",
            "display_country": "Anzeigeland",
            "display_country_placeholder": "FRANKREICH",
            "country_label": "Ländertext überschreiben",
            "country_label_placeholder": "Niedersachsen",
            "font_family": "Google-Schriftfamilie",
            "font_family_placeholder": "Noto Sans JP",
            "all_themes": "Alle verfügbaren Themes für diese Stadt erzeugen",
            "generate": "Poster erstellen",
            "storage_note": "Fertige Dateien werden im gemounteten Poster-Ordner gespeichert.",
        },
        "loading": {
            "title": "Poster wird erstellt...",
            "initial_elapsed": "0 s vergangen",
            "elapsed": "{seconds} s vergangen",
            "note": (
                "Bitte lasse diese Seite geöffnet. Große Städte, Netzwerkabfragen und "
                "Alle-Theme-Läufe können etwas länger dauern."
            ),
        },
        "result": {
            "title": "Neuestes Ergebnis",
            "description": (
                "Die neueste erzeugte Datei erscheint hier sofort, damit du sie direkt ansehen "
                "und herunterladen kannst."
            ),
            "ready": "Fertig",
            "preview_unavailable_title": "Für diesen Dateityp ist keine eingebettete Vorschau verfügbar.",
            "preview_unavailable_note": "Nutze Öffnen oder Herunterladen, um {name} anzusehen.",
            "modified": "Geändert",
            "size": "Größe",
            "type": "Typ",
            "empty": (
                "Erstelle ein Poster, um hier eine große Vorschau und direkte "
                "Download-Aktionen zu sehen."
            ),
            "also_created": "Zusätzlich erstellt",
        },
        "downloads": {
            "title": "Downloads",
            "description": (
                "Neueste Dateien stehen oben. Jede Datei kann direkt auf dieser Seite geöffnet "
                "oder heruntergeladen werden."
            ),
            "file": "Datei",
            "modified": "Geändert",
            "size": "Größe",
            "actions": "Aktionen",
            "empty": "Es wurden noch keine Poster erstellt. Erstelle links das erste Poster.",
        },
        "buttons": {
            "open": "Öffnen",
            "download": "Herunterladen",
            "cancel": "Abbrechen",
        },
        "messages": {
            "created_singular": "1 Datei erstellt: {names}",
            "created_plural": "{count} Dateien erstellt: {names}",
            "created_fallback": "Poster erstellt.",
            "job_started": "Poster-Erstellung gestartet. Der Status wird automatisch aktualisiert.",
            "job_running": "Es läuft bereits eine Poster-Erstellung. Der Status wird automatisch aktualisiert.",
            "job_canceling": "Poster-Erstellung wird abgebrochen...",
            "job_canceled": "Poster-Erstellung wurde abgebrochen.",
            "job_cancel_unavailable": "Dieser Erstellungsauftrag kann nicht mehr abgebrochen werden.",
            "job_not_found": (
                "Der Erstellungsauftrag wurde nicht gefunden. Bitte starte die Poster-Erstellung erneut."
            ),
            "job_status_retrying": (
                "Der Status der Poster-Erstellung konnte gerade nicht geladen werden. "
                "Es wird erneut versucht."
            ),
            "worker_exited": "Der Hintergrundprozess wurde unerwartet beendet.",
            "generation_failed_prefix": "Poster-Erstellung fehlgeschlagen: {details}",
            "response_unreadable": "Die Serverantwort konnte nicht gelesen werden.",
            "network_failed": (
                "Die Verbindung zur App wurde während der Poster-Erstellung unterbrochen. "
                "Bitte Seite neu laden und erneut versuchen."
            ),
        },
        "errors": {
            "whole_number": "{field} muss eine ganze Zahl sein.",
            "number": "{field} muss eine Zahl sein.",
        },
        "labels": {
            "distance": "Entfernung",
            "width": "Breite",
            "height": "Höhe",
        },
    },
    "en": {
        "meta": {
            "title": "MaptoPoster",
        },
        "language": {
            "label": "Language",
            "de": "DE",
            "en": "EN",
            "de_long": "Deutsch",
            "en_long": "English",
        },
        "hero": {
            "copy": (
                "Generate printable city map artwork in the browser, keep every poster in your "
                "Unraid appdata folder, and download finished files without digging through the "
                "filesystem."
            ),
            "themes_stat": "Available themes from the bundled JSON library.",
            "files_stat": "Generated files currently available from the web interface.",
        },
        "form": {
            "title": "Create Poster",
            "description": (
                "Use city and country for standard geocoding, or override the center point with "
                "custom coordinates."
            ),
            "city": "City",
            "city_placeholder": "Paris",
            "country": "Country",
            "country_placeholder": "France",
            "theme": "Theme",
            "format": "Format",
            "distance": "Distance (meters)",
            "distance_placeholder": "18000",
            "width": "Width (cm)",
            "width_placeholder": "30.5",
            "height": "Height (cm)",
            "height_placeholder": "40.6",
            "size_note": "Width and height are entered in centimeters, maximum 50.8 cm.",
            "latitude": "Latitude override",
            "latitude_placeholder": "48.8566",
            "longitude": "Longitude override",
            "longitude_placeholder": "2.3522",
            "display_city": "Display city",
            "display_city_placeholder": "PARIS",
            "display_country": "Display country",
            "display_country_placeholder": "FRANCE",
            "country_label": "Country label override",
            "country_label_placeholder": "Lower Saxony",
            "font_family": "Google font family",
            "font_family_placeholder": "Noto Sans JP",
            "all_themes": "Generate every available theme for this city",
            "generate": "Generate poster",
            "storage_note": "Generated files are written into the mounted posters directory.",
        },
        "loading": {
            "title": "Generating poster...",
            "initial_elapsed": "0s elapsed",
            "elapsed": "{seconds}s elapsed",
            "note": (
                "Please keep this page open. Large cities, network fetches, and all-theme runs "
                "can take a little longer."
            ),
        },
        "result": {
            "title": "Latest Result",
            "description": (
                "The newest generated file appears here right away so you can preview it and "
                "download it without browsing the filesystem."
            ),
            "ready": "Ready to use",
            "preview_unavailable_title": "Preview is not embedded for this file type.",
            "preview_unavailable_note": "Use Open or Download to inspect {name}.",
            "modified": "Modified",
            "size": "Size",
            "type": "Type",
            "empty": "Generate a poster to show a large preview and direct download actions here.",
            "also_created": "Also created",
        },
        "downloads": {
            "title": "Downloads",
            "description": (
                "Newest files appear first. Every file can be opened inline or downloaded "
                "directly from this page."
            ),
            "file": "File",
            "modified": "Modified",
            "size": "Size",
            "actions": "Actions",
            "empty": "No generated posters yet. Create the first one from the form on the left.",
        },
        "buttons": {
            "open": "Open",
            "download": "Download",
            "cancel": "Cancel",
        },
        "messages": {
            "created_singular": "Created 1 file: {names}",
            "created_plural": "Created {count} files: {names}",
            "created_fallback": "Poster created.",
            "job_started": "Poster generation started. Status updates will appear automatically.",
            "job_running": "A poster generation job is already running. Status updates will continue automatically.",
            "job_canceling": "Poster generation is being canceled...",
            "job_canceled": "Poster generation was canceled.",
            "job_cancel_unavailable": "This generation job can no longer be canceled.",
            "job_not_found": (
                "The generation job could not be found. Please start the poster generation again."
            ),
            "job_status_retrying": (
                "The poster generation status could not be loaded just now. Retrying automatically."
            ),
            "worker_exited": "The background worker process exited unexpectedly.",
            "generation_failed_prefix": "Poster generation failed: {details}",
            "response_unreadable": "The server returned an unreadable response.",
            "network_failed": (
                "The connection to the app was interrupted while generating the poster. "
                "Please reload the page and try again."
            ),
        },
        "errors": {
            "whole_number": "{field} must be a whole number.",
            "number": "{field} must be a number.",
        },
        "labels": {
            "distance": "Distance",
            "width": "Width",
            "height": "Height",
        },
    },
}

THEME_TRANSLATIONS_DE = {
    "autumn": {
        "display_name": "Herbst",
        "description": "Verbrannte Orangetöne, dunkle Rottöne und goldenes Gelb - herbstliche Wärme",
    },
    "blueprint": {
        "display_name": "Blaupause",
        "description": "Klassische Architektur-Blaupause - technische Zeichenästhetik",
    },
    "contrast_zones": {
        "display_name": "Kontrastzonen",
        "description": "Starker Kontrast für urbane Dichte - dunkler im Zentrum, heller am Rand",
    },
    "copper_patina": {
        "display_name": "Kupferpatina",
        "description": "Oxidiertes Kupfer - türkisgrüne Patina mit Kupferakzenten",
    },
    "emerald": {
        "display_name": "Smaragdstadt",
        "description": "Sattes Dunkelgrün mit mintfarbenen Akzenten",
    },
    "forest": {
        "display_name": "Wald",
        "description": "Tiefe Waldtöne mit ruhigen grünen Nuancen",
    },
    "gradient_roads": {
        "display_name": "Verlaufsstraßen",
        "description": "Straßen mit weichem Farbverlauf auf hellem Grund",
    },
    "japanese_ink": {
        "display_name": "Japanische Tinte",
        "description": "Von Tuschemalerei inspiriert - ruhige Kontraste und klare Linien",
    },
    "midnight_blue": {
        "display_name": "Mitternachtsblau",
        "description": "Elegantes Nachtblau mit heller Straßenzeichnung",
    },
    "monochrome_blue": {
        "display_name": "Monochromes Blau",
        "description": "Kühle Blauabstufungen mit klarer grafischer Wirkung",
    },
    "neon_cyberpunk": {
        "display_name": "Neon-Cyberpunk",
        "description": "Leuchtende Neonfarben auf dunklem Grund - futuristische Nachtstimmung",
    },
    "noir": {
        "display_name": "Noir",
        "description": "Tiefschwarzer Hintergrund mit weißen und grauen Straßen - moderne Galerieästhetik",
    },
    "ocean": {
        "display_name": "Ozean",
        "description": "Kühlblaue Meerestöne mit luftiger, klarer Wirkung",
    },
    "pastel_dream": {
        "display_name": "Pastelltraum",
        "description": "Sanfte Pastelltöne mit leichter, träumerischer Stimmung",
    },
    "sunset": {
        "display_name": "Sonnenuntergang",
        "description": "Warme Abendfarben zwischen Orange, Rosa und Gold",
    },
    "terracotta": {
        "display_name": "Terrakotta",
        "description": "Mediterrane Wärme mit gebrannten Orange- und Tontönen auf Creme",
    },
    "warm_beige": {
        "display_name": "Warmes Beige",
        "description": "Sanfte Beigetöne mit ruhiger, wohnlicher Ausstrahlung",
    },
}

THEME_NOT_FOUND_RE = re.compile(r"^Theme '(.+)' not found\. Available themes: (.+)$")
INVALID_COORDINATE_RE = re.compile(r"^Invalid coordinate value: (.+)$")
COORDINATES_NOT_FOUND_RE = re.compile(r"^Could not find coordinates for (.+), (.+)$")
GEOCODING_FAILED_RE = re.compile(r"^Geocoding failed for (.+), (.+): (.+)$")

EXACT_ERROR_MESSAGES = {
    "de": {
        "City is required.": "Stadt ist erforderlich.",
        "Country is required.": "Land ist erforderlich.",
        "Distance must be greater than 0.": "Die Entfernung muss größer als 0 sein.",
        "Width must be greater than 0.": "Die Breite muss größer als 0 sein.",
        "Height must be greater than 0.": "Die Höhe muss größer als 0 sein.",
        "Format must be one of: png, svg, pdf.": "Das Format muss eines von: png, svg, pdf sein.",
        "Latitude and longitude must be provided together.": (
            "Breiten- und Längengrad müssen zusammen angegeben werden."
        ),
        "No themes found in the themes directory.": "Im Theme-Verzeichnis wurden keine Themes gefunden.",
    },
    "en": {},
}

PROGRESS_STEP_TRANSLATIONS = {
    "de": {
        "Loading fonts": "Schriften werden geladen",
        "Looking up coordinates": "Koordinaten werden gesucht",
        "Downloading street network": "Straßennetz wird geladen",
        "Downloading water features": "Gewässer werden geladen",
        "Downloading parks/green spaces": "Parks und Grünflächen werden geladen",
        "Rendering map": "Karte wird gerendert",
        "Saving poster": "Poster wird gespeichert",
        "Finalizing files": "Dateien werden finalisiert",
        "Completed": "Abgeschlossen",
    },
    "en": {
        "Loading fonts": "Loading fonts",
        "Looking up coordinates": "Looking up coordinates",
        "Downloading street network": "Downloading street network",
        "Downloading water features": "Downloading water features",
        "Downloading parks/green spaces": "Downloading parks/green spaces",
        "Rendering map": "Rendering map",
        "Saving poster": "Saving poster",
        "Finalizing files": "Finalizing files",
        "Completed": "Completed",
    },
}


def normalize_language(value: str | None) -> str:
    if value in SUPPORTED_LANGUAGES:
        return value
    return DEFAULT_LANGUAGE


def get_text_bundle(language: str) -> dict[str, Any]:
    return TEXTS[normalize_language(language)]


def build_js_text(language: str) -> dict[str, str]:
    text = get_text_bundle(language)
    return {
        "open": text["buttons"]["open"],
        "download": text["buttons"]["download"],
        "cancel": text["buttons"]["cancel"],
        "ready": text["result"]["ready"],
        "preview_unavailable_title": text["result"]["preview_unavailable_title"],
        "preview_unavailable_note": text["result"]["preview_unavailable_note"],
        "modified": text["result"]["modified"],
        "size": text["result"]["size"],
        "type": text["result"]["type"],
        "also_created": text["result"]["also_created"],
        "latest_result_empty": text["result"]["empty"],
        "downloads_empty": text["downloads"]["empty"],
        "file": text["downloads"]["file"],
        "actions": text["downloads"]["actions"],
        "elapsed": text["loading"]["elapsed"],
        "response_unreadable": text["messages"]["response_unreadable"],
        "network_failed": text["messages"]["network_failed"],
        "job_started": text["messages"]["job_started"],
        "job_running": text["messages"]["job_running"],
        "job_canceling": text["messages"]["job_canceling"],
        "job_canceled": text["messages"]["job_canceled"],
        "job_cancel_unavailable": text["messages"]["job_cancel_unavailable"],
        "job_not_found": text["messages"]["job_not_found"],
        "job_status_retrying": text["messages"]["job_status_retrying"],
        "worker_exited": text["messages"]["worker_exited"],
        "generation_failed": text["messages"]["generation_failed_prefix"].format(
            details="__details__"
        ),
        "created_fallback": text["messages"]["created_fallback"],
    }


def localize_theme_catalog(themes: list[dict[str, str]], language: str) -> list[dict[str, str]]:
    if normalize_language(language) != "de":
        return themes

    localized = []
    for theme in themes:
        translation = THEME_TRANSLATIONS_DE.get(theme["name"])
        if translation is None:
            localized.append(theme)
            continue

        localized.append(
            {
                **theme,
                "display_name": translation["display_name"],
                "description": translation["description"],
            }
        )
    return localized


def format_modified_label(timestamp: float, language: str) -> str:
    fmt = "%d.%m.%Y %H:%M:%S" if normalize_language(language) == "de" else "%Y-%m-%d %H:%M:%S"
    return datetime.fromtimestamp(timestamp).strftime(fmt)


def format_metric_input(value_cm: float, language: str) -> str:
    formatted = f"{value_cm:.1f}"
    if normalize_language(language) == "de":
        return formatted.replace(".", ",")
    return formatted


def format_created_message(count: int, names: str, language: str) -> str:
    text = get_text_bundle(language)["messages"]
    if count == 1:
        return text["created_singular"].format(names=names)
    return text["created_plural"].format(count=count, names=names)


def build_generation_failure_message(details: str, language: str) -> str:
    return get_text_bundle(language)["messages"]["generation_failed_prefix"].format(
        details=details
    )


def localize_progress_step(step: str | None, language: str) -> str | None:
    if not step:
        return None

    lang = normalize_language(language)
    if step.startswith("Preparing theme: "):
        theme_name = step.removeprefix("Preparing theme: ")
        if lang == "de":
            return f"Theme wird vorbereitet: {theme_name}"
        return f"Preparing theme: {theme_name}"

    return PROGRESS_STEP_TRANSLATIONS.get(lang, {}).get(step, step)


def translate_error_message(message: str, language: str) -> str:
    lang = normalize_language(language)
    if lang == "en":
        return message

    exact = EXACT_ERROR_MESSAGES["de"].get(message)
    if exact:
        return exact

    match = THEME_NOT_FOUND_RE.match(message)
    if match:
        theme_name, available = match.groups()
        return f"Theme '{theme_name}' wurde nicht gefunden. Verfügbare Themes: {available}"

    match = INVALID_COORDINATE_RE.match(message)
    if match:
        return f"Ungültiger Koordinatenwert: {match.group(1)}"

    match = COORDINATES_NOT_FOUND_RE.match(message)
    if match:
        city, country = match.groups()
        return f"Koordinaten für {city}, {country} konnten nicht gefunden werden."

    match = GEOCODING_FAILED_RE.match(message)
    if match:
        city, country, details = match.groups()
        return f"Geocoding für {city}, {country} ist fehlgeschlagen: {details}"

    return message
