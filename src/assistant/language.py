"""Which language to answer in, from the platform's selected locale.

Split out of the tool runtime, which is capped at 1000 lines. Self-contained:
a locale string in, an instruction out, no client state involved.
"""
from __future__ import annotations

# Endonyms, so the instruction names the language in the language itself.
# "Answer in Português" is a stronger cue to a multilingual model than
# "Answer in Portuguese", and it costs nothing.
_LOCALE_NAMES = {
    "bg": "български", "cs": "čeština", "da": "dansk", "de": "Deutsch",
    "el": "ελληνικά", "en": "English", "es": "español", "et": "eesti",
    "fi": "suomi", "fr": "français", "ga": "Gaeilge", "hr": "hrvatski",
    "hu": "magyar", "it": "italiano", "lt": "lietuvių", "lv": "latviešu",
    "mt": "Malti", "nl": "Nederlands", "pl": "polski", "pt": "português",
    "ro": "română", "sk": "slovenčina", "sl": "slovenščina", "sv": "svenska",
}


def _language_directive(locale: str | None) -> str:
    """Tell the model which language to answer in, by name.

    Left to itself the model infers the language from the conversation, and
    that inference breaks precisely when a tool is called: the tool schemas,
    the JSON results and the entity names are all English, so a French
    question answered after a search came back in English. Measured at -100%
    on the language check for the 1.7B and 4B.

    The platform's selected locale is the authority, not the question: a user
    reading the site in Portuguese who types an English place name still
    wants a Portuguese answer.
    """
    code = (locale or "").split("-")[0].lower()
    name = _LOCALE_NAMES.get(code)
    if not name or code == "en":
        return ""
    return (f"\n\n## Language\n\n"
            f"The user is reading this platform in {name}. Write every reply "
            f"in {name}, including when the tools return English data — tool "
            f"output is evidence, not a cue to switch language. Keep entity "
            f"names, tickers and identifiers exactly as the data spells them.")
