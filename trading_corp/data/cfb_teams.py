"""CFB (US college football) team map: Kalshi/Polymarket team CODE -> canonical school name.

**BUILT FROM REAL TWO-VENUE DATA (2026-09-06), NOT a hand-typed school list (which would look
complete and be wrong exactly where it matters). Sources: 297 live Kalshi KXNCAAFGAME ticker codes
(yes_sub_title) + 240 Polymarket cfb slug codes (title-derived), joined by a school-identity key.

SAFETY (the acceptance test is tests/prediction_markets/test_cfb_match.py):
 - The matcher joins on (date, frozenset{away_name, home_name}) via this map for BOTH venues, so a
   code must resolve to ONE school or be DROPPED (a SAFE MISS, never the wrong school).
 - Every Jack-named collision pair resolves to DISTINCT schools: Miami (MIA) vs Miami (OH) (MIAOH/MOH);
   Ole Miss (MISS) vs Mississippi St (MSST/MSPST) vs Miss Valley (MVSU) vs Southern Miss (USM/SOUMIS);
   Ohio St (OSU) vs Oklahoma St (OKST) vs Oregon St (ORST); Michigan St (MSU) vs Missouri St (MSRST);
   Kansas St (KSU) vs Kansas (KU) vs Kentucky (UK); Washington (WASH) vs Washington St (WSU);
   Colorado (COL) vs Colorado St (CSU); San Diego (SDSU) vs San Jose (SJSU) vs South Dakota (SDKST).
 - **DROPPED (unresolvable, NAMED as safe misses):
     SDST -- a genuine cross-venue collision (Poly `sdst`=San Diego St, Kalshi `SDST`=South Dakota St);
             San Diego St stays reachable via `sdsu`, so only the `sdst` spelling misses.
 - **KALSHI-AMBIGUOUS codes CSU/KSU/WSU (Kalshi reuses them for a non-FBS school too: Central St OH /
     Kentucky St / Winona St) are mapped to the FBS meaning = the Poly meaning. The non-FBS meaning is a
     SAFE MISS: Polymarket never bets those FCS/D2 schools, and the both-team join key cannot wrong-match
     (the mislabeled game's OPPONENT would also have to collide, which is impossible across schools).
 - Poly uses several codes per school (af/afa/airf, ala/bama, ariz/arz) -- all folded to one canonical.
 - Canonical strings keep a few mascots (Poly's dominant text, e.g. 'Maryland Terrapins') -- harmless:
   resolve_side matches the whale's outcome by shared token, and the join is code-based, not name-based.

Regenerate each season from the live codes (build_cfb_map.py). An unmapped/new code is a SAFE MISS.
"""
CFB_TEAMS = {
    'AF': 'Air Force', 'AFA': 'Air Force', 'AIRF': 'Air Force', 'AKR': 'Akron',
    'AKRN': 'Akron', 'AKRON': 'Akron', 'ALA': 'Alabama', 'APP': 'Appalachian State',
    'APPLST': 'Appalachian State', 'ARIZ': 'Arizona', 'ARK': 'Arkansas', 'ARKST': 'Arkansas State',
    'ARMY': 'Army', 'ARST': 'Arkansas State', 'ARZ': 'Arizona', 'ARZST': 'Arizona State',
    'ASU': 'Arizona State', 'AUB': 'Auburn', 'AUBRN': 'Auburn', 'AZST': 'Arizona State',
    'BALL': 'Ball State', 'BALLST': 'Ball State', 'BAMA': 'Alabama', 'BAY': 'Baylor',
    'BAYL': 'Baylor', 'BC': 'Boston College', 'BGSU': 'Bowling', 'BOISE': 'Boise State',
    'BOSCOL': 'Boston College', 'BOWLGR': 'Bowling', 'BSU': 'Boise State', 'BUF': 'Buffalo',
    'BUFF': 'Buffalo', 'BYU': 'BYU', 'CAH': 'California', 'CAL': 'California',
    'CCAR': 'Coastal Carolina', 'CHAR': 'Charlotte', 'CHARLT': 'Charlotte', 'CIN': 'Cincinnati',
    'CIT': 'The Citadel', 'CITA': 'The Citadel', 'CLEM': 'Clemson', 'CLMSN': 'Clemson',
    'CMICH': 'Central Michigan', 'CMU': 'Central Michigan', 'COAST': 'Coastal Carolina', 'COL': 'Colorado',
    'COLO': 'Colorado', 'COLST': 'Colorado State', 'CONN': 'UConn', 'CSU': 'Colorado State',
    'DEL': 'Delaware Blue Hens', 'DUKE': 'Duke', 'ECAR': 'East Carolina', 'ECU': 'East Carolina',
    'EMICH': 'Eastern Michigan', 'EMU': 'Eastern Michigan', 'FAU': 'Florida Atlantic', 'FIU': 'Florida International',
    'FL': 'Florida', 'FLA': 'Florida', 'FLATL': 'Florida Atlantic', 'FLINT': 'Florida International',
    'FLST': 'Florida State', 'FRES': 'Fresno State', 'FREST': 'Fresno State', 'FRSNO': 'Fresno State',
    'FSU': 'Florida State', 'GA': 'Georgia', 'GAS': 'Georgia Southern', 'GASO': 'Georgia Southern',
    'GAST': 'Georgia State', 'GT': 'Georgia Tech', 'GTECH': 'Georgia Tech', 'HAW': 'Hawaii',
    'HAWAII': 'Hawaii', 'HOU': 'Houston', 'IDAHO': 'Idaho', 'IDHO': 'Idaho',
    'IDHST': 'Idaho State', 'IDST': 'Idaho State', 'ILL': 'Illinois', 'IND': 'Indiana',
    'IOWA': 'Iowa', 'IOWAST': 'Iowa State', 'ISU': 'Iowa State', 'JAXST': 'Jacksonville State',
    'JMAD': 'James Madison', 'JMU': 'James Madison', 'JVST': 'Jacksonville State', 'KAN': 'Kansas',
    'KANST': 'Kansas State', 'KENEST': 'Kennesaw State', 'KENN': 'Kennesaw State', 'KENT': 'Kent State Golden Flashes',
    'KENTST': 'Kent State Golden Flashes', 'KSU': 'Kansas State', 'KU': 'Kansas', 'LAF': 'Lafayette Leopards',
    'LAFAY': 'Lafayette Leopards', 'LAM': 'Lamar', 'LAMAR': 'Lamar', 'LAMON': 'UL Monroe',
    'LIB': 'Liberty', 'LIBRTY': 'Liberty', 'LOU': 'Louisville', 'LOULAF': 'Louisiana',
    'LOUTCH': 'Louisiana Tech', 'LSU': 'LSU', 'LT': 'Louisiana Tech', 'MARSH': 'Marshall',
    'MARY': 'Maryland Terrapins', 'MASS': 'UMass', 'MD': 'Maryland Terrapins', 'MEM': 'Memphis',
    'MER': 'Mercer', 'MERC': 'Mercer', 'MIA': 'Miami', 'MIAMI': 'Miami',
    'MIAOH': 'Miami (OH)', 'MICH': 'Michigan', 'MINN': 'Minnesota', 'MINNST': 'Minnesota',
    'MISS': 'Ole Miss', 'MISSR': 'Missouri', 'MIZZ': 'Missouri', 'MOH': 'Miami (OH)',
    'MOSU': 'Missouri State', 'MPHS': 'Memphis', 'MRSH': 'Marshall', 'MSPST': 'Mississippi State',
    'MSRST': 'Missouri State', 'MSST': 'Mississippi State', 'MST': 'Michigan State', 'MSU': 'Michigan State',
    'MSVLST': 'Mississippi Valley State', 'MTNST': 'Middle Tennessee', 'MTSU': 'Middle Tennessee', 'MTU': 'Middle Tennessee',
    'MVSU': 'Mississippi Valley State', 'NAVY': 'Navy', 'NC': 'North Carolina', 'NCAR': 'North Carolina',
    'NCST': 'NC State', 'ND': 'Notre Dame', 'NDAME': 'Notre Dame', 'NDKST': 'North Dakota State Bison',
    'NDSU': 'North Dakota State Bison', 'NEB': 'Nebraska', 'NEBR': 'Nebraska', 'NEV': 'Nevada',
    'NEVADA': 'Nevada', 'NICH': 'Nicholls State', 'NICHLS': 'Nicholls State', 'NILL': 'Northern Illinois',
    'NIU': 'Northern Illinois', 'NMST': 'New Mexico State', 'NMSU': 'New Mexico State', 'NMX': 'New Mexico',
    'NMXST': 'New Mexico State', 'NTX': 'North Texas', 'NW': 'Northwestern', 'ODU': 'Old Dominion',
    'OHIO': 'Ohio', 'OHIOST': 'Ohio State', 'OHST': 'Ohio State', 'OKL': 'Oklahoma',
    'OKLA': 'Oklahoma', 'OKST': 'Oklahoma State', 'OLD': 'Old Dominion', 'ORE': 'Oregon',
    'OREG': 'Oregon', 'OREGST': 'Oregon State', 'ORST': 'Oregon State', 'OSU': 'Ohio State',
    'PENNST': 'Penn State', 'PITT': 'Pittsburgh', 'PSU': 'Penn State', 'PUR': 'Purdue',
    'PURD': 'Purdue', 'RI': 'Rhode Island', 'RICE': 'Rice', 'RUTG': 'Rutgers',
    'RUTGER': 'Rutgers', 'SAC': 'Sacramento State Hornets', 'SACST': 'Sacramento State Hornets', 'SALA': 'South Alabama',
    'SBAMA': 'South Alabama', 'SC': 'South Carolina', 'SCAR': 'South Carolina', 'SDKST': 'South Dakota State',
    'SDSU': 'San Diego State', 'SEMO': 'Southeast Missouri State', 'SEMST': 'Southeast Missouri State', 'SFL': 'South Florida',
    'SHSU': 'Sam Houston', 'SJST': 'San Jose State', 'SJSU': 'San Jose State', 'SMHO': 'Sam Houston',
    'SMU': 'SMU Mustangs', 'SOUMIS': 'Southern Miss', 'STAN': 'Stanford', 'SYR': 'Syracuse',
    'SYRA': 'Syracuse', 'TARL': 'Tarleton State', 'TCU': 'TCU', 'TEM': 'Temple',
    'TEMPL': 'Temple', 'TENN': 'Tennessee', 'TEX': 'Texas', 'TLSA': 'Tulsa',
    'TOL': 'Toledo', 'TOLEDO': 'Toledo', 'TROY': 'Troy', 'TTU': 'Texas Tech',
    'TULANE': 'Tulane', 'TULN': 'Tulane', 'TULSA': 'Tulsa', 'TX': 'Texas',
    'TXAM': 'Texas A&M', 'TXST': 'Texas State', 'TXTECH': 'Texas Tech', 'UAB': 'UAB Blazers',
    'UCF': 'UCF', 'UCLA': 'UCLA', 'UCONN': 'UConn', 'UF': 'Florida',
    'UGA': 'Georgia', 'UK': 'Kentucky', 'ULL': 'Louisiana', 'ULM': 'UL Monroe',
    'UMASS': 'UMass', 'UNC': 'North Carolina', 'UNLV': 'UNLV', 'UNM': 'New Mexico',
    'UNT': 'North Texas', 'URI': 'Rhode Island', 'USA': 'South Alabama', 'USC': 'USC',
    'USF': 'South Florida', 'USM': 'Southern Miss', 'USU': 'Utah State', 'UTAH': 'Utah',
    'UTAHST': 'Utah State', 'UTEP': 'UTEP', 'UTRGV': 'UT Rio Grande Valley', 'UTSA': 'UTSA Roadrunners',
    'UTST': 'Utah State', 'UVA': 'Virginia', 'VAN': 'Vanderbilt', 'VAND': 'Vanderbilt',
    'VIR': 'Virginia', 'VT': 'Virginia Tech', 'VTECH': 'Virginia Tech', 'WAKE': 'Wake Forest',
    'WASH': 'Washington', 'WASHST': 'Washington State', 'WIS': 'Wisconsin', 'WISC': 'Wisconsin',
    'WKENT': 'Western Kentucky', 'WKU': 'Western Kentucky', 'WMICH': 'Western Michigan', 'WMU': 'Western Michigan',
    'WSU': 'Washington State', 'WVIR': 'West Virginia', 'WVU': 'West Virginia', 'WYO': 'Wyoming',
    'WYOM': 'Wyoming',
}
