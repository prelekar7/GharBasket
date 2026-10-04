"""Spellings people type: Devanagari names written the everyday "Hinglish" way.

indic-transliteration gives scholarly ITRANS ("pAlaka", "bhiMDI", "TamATara"). People type
"palak", "bhindi", "tamatar", so the ITRANS form is simplified the way spoken Hindi and Marathi
drop sounds: the inherent final "a" goes, the nasal mark becomes n (m before p, b, bh, m), and
long and retroflex letters lose their capitals.
"""

import re

from indic_transliteration import sanscript

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LABIALS = ("p", "b", "m")


def has_devanagari(text: str) -> bool:
    return bool(DEVANAGARI.search(text))


def _hinglish_word(itrans: str) -> str:
    word = itrans.replace(".c", "").replace(".N", "M").replace("~N", "n").replace(".", "")
    # The inherent final short "a" is not spoken ("pAlaka" → "pAlak"); a long "A" stays.
    if len(word) > 2 and word.endswith("a") and not word.endswith(("aa", "A")):
        word = word[:-1]
    # Anusvara: "bhiMDI" → "bhinDI", "kothiMbIra" → "kothimbIra".
    out: list[str] = []
    for i, char in enumerate(word):
        if char == "M":
            following = word[i + 1 : i + 2].lower()
            out.append("m" if following.startswith(_LABIALS) else "n")
        else:
            out.append(char)
    return "".join(out).lower()


def hinglish(text: str) -> str:
    """ "तूर डाळ" → "tur dal", "भिंडी" → "bhindi", "कोथिंबीर" → "kothimbir"."""
    itrans = sanscript.transliterate(text, sanscript.DEVANAGARI, sanscript.ITRANS)
    return " ".join(_hinglish_word(word) for word in itrans.split())
