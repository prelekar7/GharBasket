# Festival calendar check log

`generate.py` computes the days from the `kalagana` 0.1.3 offline panchang (Mumbai, north tradition,
Lahiri ayanamsa, sunrise tithi). A date counts as **verified** only when it has been checked against a
printed panchang (Kalnirnay / Date Panchang) or drikpanchang.com and `yes` is written in the last
column; the generator reads this table and sets `verified` on the matching `(date, code)` rows. A day
the panchangs keep on another date goes in the corrections table below: the generator moves it there
and marks it verified.

**1 Nov 2026:** the ten priority dates and the Ekadashis to December were checked on the web by Claude
against drikpanchang.com (its Marathi calendars for 2026 and 2027 and its Ekadashi list; the pages
default to New Delhi, which keeps the same days as Mumbai for these festivals) and at least one other
published panchang site. **Rhythm Thakur and Manish Raje: please confirm in the PR review** against
Kalnirnay; then the Sankashti dates and the Ekadashis from January.

| date | code | kalagana says | expected (recalled, unchecked) | checked against | yes |
|---|---|---|---|---|---|
| 2026-09-14 | ganesh_chaturthi | Ganesh Chaturthi | 14 Sep 2026 | drikpanchang Marathi 2026; Shubh Panchang | yes |
| 2026-10-06 | ekadashi | Indira Ekadashi | 6 Oct 2026 | drikpanchang Ekadashi list and Marathi 2026; Astrotalk | yes |
| 2026-10-11 | navratri | Sharad Navratri day 1 | 11 Oct 2026 | drikpanchang Marathi 2026 (Ghatasthapana); Dekho Panchang | yes |
| 2026-10-21 | dussehra | Vijayadashami | 20 or 21 Oct 2026 (tithi) | drikpanchang keeps 20 Oct (see corrections) | |
| 2026-11-08 | diwali | Lakshmi Puja | 8 Nov 2026 | drikpanchang Diwali calendar and Marathi 2026; Dekho Panchang | yes |
| 2026-11-21 | ekadashi | Devutthana (Prabodhini) Ekadashi | 21 Nov 2026 | drikpanchang keeps 20 Nov (21 Nov is the Vaishnava day; see corrections) | |
| 2027-01-14 | makar_sankranti | Makar Sankranti | 14 Jan 2027 | drikpanchang Marathi 2027 keeps 15 Jan (see corrections) | |
| 2027-03-06 | maha_shivratri | Maha Shivratri | 6 Mar 2027 | drikpanchang Marathi 2027; Dekho Panchang; Muhuratam | yes |
| 2027-03-22 | holi | Holi (Dhulivandan) | 22 Mar 2027 | drikpanchang Marathi 2027 (Dhulivandan); Shubh Panchang | yes |
| 2027-04-07 | gudi_padwa | Gudi Padwa | 7 Apr 2027 | drikpanchang Marathi 2027 and Gudi Padwa page | yes |
| 2026-09-07 | ekadashi | Aja Ekadashi | | drikpanchang Ekadashi list 2026 | yes |
| 2026-09-22 | ekadashi | Parsva Ekadashi | | drikpanchang Ekadashi list 2026 | yes |
| 2026-10-22 | ekadashi | Papankusha Ekadashi | | drikpanchang Ekadashi list 2026 | yes |
| 2026-11-05 | ekadashi | Rama Ekadashi | | drikpanchang Ekadashi list 2026 | yes |
| 2026-12-04 | ekadashi | Utpanna Ekadashi | | drikpanchang Ekadashi list 2026 | yes |
| 2026-12-20 | ekadashi | Mokshada Ekadashi | | drikpanchang Ekadashi list 2026 | yes |

## Corrections

kalagana's day → the day the published panchangs keep. Navratri still ends on 19 Oct (nine days from
11 Oct); only the feast moves.

| kalagana date | code | correct date | checked against |
|---|---|---|---|
| 2026-10-21 | dussehra | 2026-10-20 | drikpanchang Marathi 2026 and Vijayadashami page; Business Today |
| 2026-11-21 | ekadashi | 2026-11-20 | drikpanchang Ekadashi list 2026 (Smartha day; Vaishnava 21 Nov); Dekho Panchang; Astrotalk |
| 2027-01-14 | makar_sankranti | 2027-01-15 | drikpanchang Marathi 2027 (the Sun enters Makara at night on 14 Jan); Hindutone |

Notes
- Ekadashi follows the Smartha (sunrise) rule; Vaishnava families may observe the next day.
- Navratri is nine days from the first; the generator does not model a skipped or doubled tithi inside it.
- Shravan Mondays are computed by the planner from the month (M3 `planning.fasting_days`), not listed here.
- `sankashti` and `gudi_padwa` carry `region: maharashtra`.
