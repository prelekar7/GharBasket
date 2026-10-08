# Festival and fasting calendar

`generate.py` writes `data/seeds/catalog/festivals.json` for September 2026 to August 2027 from the
`kalagana` offline panchang (MIT; no network): Ekadashi (monthly), Sharad and Chaitra Navratri (nine days
each), Sankashti Chaturthi, Maha Shivratri, Janmashtami and Ram Navami as fasting days, and Ganesh
Chaturthi, Dussehra, Diwali, Makar Sankranti, Holi and Gudi Padwa as feast days (highlights, no fasting
rule). The M1 fast codes a household can pick are `ekadashi`, `navratri`, `shravan_mondays`, `tuesdays`.

```
uv run --isolated --no-project --with "kalagana==0.1.3" python data/external/festivals/generate.py
just seed
```

`verified` comes from `CHECK.md`: a date is verified once someone has checked it against a printed
panchang and written `yes` there. Until then the planner still uses the dates (they are the best we
have) and the console shows them as unverified.
