# Festival calendar check log

`generate.py` computes the days from the `kalagana` 0.1.3 offline panchang (Mumbai, north tradition,
Lahiri ayanamsa, sunrise tithi). A date counts as **verified** only when a person has checked it against
a printed panchang (Kalnirnay / Date Panchang) or drikpanchang.com and writes `yes` in the last column;
the generator reads this table and sets `verified` on the matching `(date, code)` rows.

Prathamesh's recollection of the public calendars is in the "expected" column as a hint; it is not a
check. **Rhythm Thakur and Manish Raje: please tick these ten first** (they cover every rule the
generator uses), then the Ekadashi and Sankashti dates in `festivals.json`.

| date | code | kalagana says | expected (recalled, unchecked) | checked against | yes |
|---|---|---|---|---|---|
| 2026-09-14 | ganesh_chaturthi | Ganesh Chaturthi | 14 Sep 2026 | | |
| 2026-10-06 | ekadashi | Indira Ekadashi | 6 Oct 2026 | | |
| 2026-10-11 | navratri | Sharad Navratri day 1 | 11 Oct 2026 | | |
| 2026-10-21 | dussehra | Vijayadashami | 20 or 21 Oct 2026 (tithi) | | |
| 2026-11-08 | diwali | Lakshmi Puja | 8 Nov 2026 | | |
| 2026-11-21 | ekadashi | Devutthana (Prabodhini) Ekadashi | 21 Nov 2026 | | |
| 2027-01-14 | makar_sankranti | Makar Sankranti | 14 Jan 2027 | | |
| 2027-03-06 | maha_shivratri | Maha Shivratri | 6 Mar 2027 | | |
| 2027-03-22 | holi | Holi (Dhulivandan) | 22 Mar 2027 | | |
| 2027-04-07 | gudi_padwa | Gudi Padwa | 7 Apr 2027 | | |

Notes
- Ekadashi follows the Smartha (sunrise) rule; Vaishnava families may observe the next day.
- Navratri is nine days from the first; the generator does not model a skipped or doubled tithi inside it.
- Shravan Mondays are computed by the planner from the month (M3 `planning.fasting_days`), not listed here.
- `sankashti` and `gudi_padwa` carry `region: maharashtra`.
