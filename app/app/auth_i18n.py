"""Languages of the sign-in pages (login and two-step verification): it, en, fr, es.

The language comes from the ``nsm-lang`` cookie, set by the selector on the page (``/login/language/{code}``) and by the
application when the user changes language, otherwise from the browser (``Accept-Language``), otherwise Italian.
The new interface reads the same cookie when the user has not chosen a language yet, so the choice made before
signing in carries over.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter()

LANGUAGES = {"it": "Italiano", "en": "English", "fr": "Français", "es": "Español"}
DEFAULT = "it"
COOKIE = "nsm-lang"
COOKIE_MAX_AGE = 365 * 24 * 3600
_RETURN_TO = {"/login", "/login/2fa"}

TEXTS = {
    "login_title": {"it": "Accesso", "en": "Sign in", "fr": "Connexion", "es": "Acceso"},
    "username": {"it": "Username", "en": "Username", "fr": "Nom d'utilisateur", "es": "Usuario"},
    "password": {"it": "Password", "en": "Password", "fr": "Mot de passe", "es": "Contraseña"},
    "submit": {"it": "Accedi", "en": "Sign in", "fr": "Se connecter", "es": "Entrar"},
    "idle": {"it": "Sessione scaduta per inattività: accedi di nuovo.", "en": "Session expired after inactivity: please sign in again.",
             "fr": "Session expirée après inactivité : reconnectez-vous.", "es": "Sesión caducada por inactividad: vuelve a iniciar sesión."},
    "revoked": {"it": "La sessione è stata chiusa (cambio password o disconnessione da un amministratore): accedi di nuovo.",
                "en": "The session was closed (password change or sign-out by an administrator): please sign in again.",
                "fr": "La session a été fermée (changement de mot de passe ou déconnexion par un administrateur) : reconnectez-vous.",
                "es": "La sesión se ha cerrado (cambio de contraseña o cierre por un administrador): vuelve a iniciar sesión."},
    "language": {"it": "Lingua", "en": "Language", "fr": "Langue", "es": "Idioma"},
    "twofa_title": {"it": "Verifica in due passaggi", "en": "Two-step verification", "fr": "Vérification en deux étapes", "es": "Verificación en dos pasos"},
    "twofa_intro": {"it": "Inserisci il codice a 6 cifre dell'app di autenticazione.", "en": "Enter the 6-digit code from your authenticator app.",
                    "fr": "Saisissez le code à 6 chiffres de votre application d'authentification.",
                    "es": "Introduce el código de 6 dígitos de tu aplicación de autenticación."},
    "code": {"it": "Codice", "en": "Code", "fr": "Code", "es": "Código"},
    "verify": {"it": "Verifica", "en": "Verify", "fr": "Vérifier", "es": "Verificar"},
    "twofa_help": {"it": "Hai perso il telefono? Usa uno dei codici di recupero (formato abcde-12345) oppure chiedi a un amministratore di reimpostare la verifica.",
                   "en": "Lost your phone? Use one of your recovery codes (format abcde-12345) or ask an administrator to reset the verification.",
                   "fr": "Téléphone perdu ? Utilisez l'un de vos codes de récupération (format abcde-12345) ou demandez à un administrateur de réinitialiser la vérification.",
                   "es": "¿Has perdido el teléfono? Usa uno de tus códigos de recuperación (formato abcde-12345) o pide a un administrador que restablezca la verificación."},
    "back": {"it": "Torna all'accesso", "en": "Back to sign in", "fr": "Retour à la connexion", "es": "Volver al acceso"},
}

# Messages raised by the sign-in handlers (Italian), translated when shown.
_ERRORS = {
    "Credenziali non valide.": {"en": "Invalid credentials.", "fr": "Identifiants non valides.", "es": "Credenciales no válidas."},
    "Codice non valido o già usato.": {"en": "Invalid or already used code.", "fr": "Code non valide ou déjà utilisé.", "es": "Código no válido o ya usado."},
}
_THROTTLED = re.compile(r"^Troppi tentativi di accesso non riusciti\. Riprova tra (\d+) minuti\.$")
_THROTTLED_TEXT = {"en": "Too many failed sign-in attempts. Try again in {minutes} minutes.",
                   "fr": "Trop de tentatives de connexion échouées. Réessayez dans {minutes} minutes.",
                   "es": "Demasiados intentos de acceso fallidos. Inténtalo de nuevo en {minutes} minutos."}


def language(request: Request) -> str:
    chosen = (request.cookies.get(COOKIE) or "")[:2].lower()
    if chosen in LANGUAGES:
        return chosen
    for part in (request.headers.get("accept-language") or "").split(","):
        code = part.split(";")[0].strip()[:2].lower()
        if code in LANGUAGES:
            return code
    return DEFAULT


def text(request: Request, key: str) -> str:
    entry = TEXTS[key]
    return entry.get(language(request)) or entry[DEFAULT]


def error(request: Request, message: str | None) -> str | None:
    lang = language(request)
    if not message or lang == DEFAULT:
        return message
    if message in _ERRORS:
        return _ERRORS[message].get(lang, message)
    match = _THROTTLED.match(message)
    if match:
        return _THROTTLED_TEXT[lang].format(minutes=match.group(1))
    return message


@router.get("/login/language/{code}", include_in_schema=False)
def choose_language(code: str, next: str = "/login"):
    target = next if next in _RETURN_TO else "/login"
    response = RedirectResponse(target, status_code=303)
    if code in LANGUAGES:
        response.set_cookie(COOKIE, code, max_age=COOKIE_MAX_AGE, samesite="lax", path="/")
    return response


def install_auth_i18n(app, templates) -> None:
    templates.env.globals.update(auth_text=text, auth_error=error, auth_language=language, auth_languages=LANGUAGES)
    app.include_router(router)
