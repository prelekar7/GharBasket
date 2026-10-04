"""The language an answer is written in (`Accept-Language`), for names stored as {en, hi, mr}.

    @router.get("/products/{id}")
    async def product(..., lang: Lang) -> ProductOut:
        ...name=localised(product.names, lang)

`Lang` also sets `Content-Language` and `Vary: Accept-Language` on the response, so caches (the
PWA's service worker) keep one copy per language.
"""

from typing import Annotated, Literal

from fastapi import Depends, Header, Response

Language = Literal["en", "hi", "mr"]
SUPPORTED: tuple[Language, ...] = ("en", "hi", "mr")
DEFAULT: Language = "en"


def parse_accept_language(value: str | None) -> Language:
    """The first supported language in the header ("hi-IN,hi;q=0.9,en;q=0.8" → "hi")."""
    if not value:
        return DEFAULT
    ranked: list[tuple[float, int, str]] = []
    for position, part in enumerate(value.split(",")):
        tag, _, params = part.strip().partition(";")
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 0.0
        ranked.append((-quality, position, tag.strip().lower().split("-")[0]))
    for _, _, language in sorted(ranked):
        if language in SUPPORTED:
            return language  # pyright: ignore[reportReturnType]
    return DEFAULT


def localised(names: dict[str, str], lang: Language) -> str:
    """The name in `lang`, else English, else any name at all."""
    return names.get(lang) or names.get(DEFAULT) or next(iter(names.values()), "")


async def request_language(
    response: Response,
    accept_language: Annotated[str | None, Header(max_length=200)] = None,
) -> Language:
    lang = parse_accept_language(accept_language)
    response.headers["Content-Language"] = lang
    response.headers["Vary"] = "Accept-Language"
    return lang


Lang = Annotated[Language, Depends(request_language)]
