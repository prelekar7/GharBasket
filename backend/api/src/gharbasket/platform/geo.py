"""Points on the map, for PostGIS `geography` columns (SRID 4326: plain latitude and longitude)."""

from typing import Any, Literal

from geoalchemy2 import WKTElement
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

# India's bounding box: a pin outside it is a mistake (or the 0,0 of an unset map).
LAT_RANGE = (6.0, 37.0)
LNG_RANGE = (68.0, 98.0)
# A drawn ring: points counted with the repeated closing point.
RING_POINTS = (4, 500)


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


class GeoPolygon(BaseModel):
    """GeoJSON, WGS 84, `[lng, lat]`; one closed ring (first point = last), 4 to 500 points, no
    holes, not self-intersecting."""

    type: Literal["Polygon"]
    coordinates: list[list[list[float]]] = Field(
        examples=[[[[73.02, 19.0], [73.13, 19.0], [73.13, 19.05], [73.02, 19.05], [73.02, 19.0]]]]
    )


class PolygonInvalid(ValueError):
    """A polygon the rules refuse; the message is for the client and the owning module picks the
    problem code."""


async def polygon_element(session: AsyncSession, polygon: GeoPolygon) -> WKTElement:
    """A GeoJSON polygon as a value for a `Geography("POLYGON", srid=4326)` column, or
    `PolygonInvalid`: one ring, 4 to 500 points, each `[lng, lat]` in India, closed, and valid to
    PostGIS (no self-crossing)."""
    if len(polygon.coordinates) != 1:
        raise PolygonInvalid("The area is one ring, without holes.")
    ring = polygon.coordinates[0]
    if not RING_POINTS[0] <= len(ring) <= RING_POINTS[1]:
        raise PolygonInvalid("The ring has 4 to 500 points.")
    for corner in ring:
        # NaN fails every comparison, so it is refused here too.
        if len(corner) != 2 or not (
            LNG_RANGE[0] <= corner[0] <= LNG_RANGE[1] and LAT_RANGE[0] <= corner[1] <= LAT_RANGE[1]
        ):
            raise PolygonInvalid("Each point is [lng, lat] in India.")
    if ring[0] != ring[-1]:
        raise PolygonInvalid("The ring is closed: its last point repeats the first.")
    wkt = f"POLYGON(({', '.join(f'{lng!r} {lat!r}' for lng, lat in ring)}))"
    valid = await session.scalar(select(func.ST_IsValid(func.ST_GeomFromText(wkt, 4326))))
    if not valid:
        raise PolygonInvalid("The area crosses itself or has no inside.")
    return WKTElement(wkt, srid=4326, extended=False)
