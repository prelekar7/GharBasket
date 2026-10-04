"""Points on the map, for PostGIS `geography` columns (SRID 4326: plain latitude and longitude)."""

from typing import Any

from geoalchemy2 import WKTElement
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, func

# India's bounding box: a pin outside it is a mistake (or the 0,0 of an unset map).
LAT_RANGE = (6.0, 37.0)
LNG_RANGE = (68.0, 98.0)


class LatLng(BaseModel):
    """A pin on the map, as every API shape carries it."""

    lat: float = Field(ge=LAT_RANGE[0], le=LAT_RANGE[1], examples=[19.04])
    lng: float = Field(ge=LNG_RANGE[0], le=LNG_RANGE[1], examples=[73.07])


def point(lat: float, lng: float) -> WKTElement:
    """A value to store in a `Geography("POINT", srid=4326)` column."""
    return WKTElement(f"POINT({lng} {lat})", srid=4326, extended=False)


def geography(lat: float, lng: float) -> ColumnElement[Any]:
    """The same point as a SQL expression, for ST_Distance, ST_Covers and friends."""
    return func.ST_GeogFromText(f"SRID=4326;POINT({lng} {lat})")
