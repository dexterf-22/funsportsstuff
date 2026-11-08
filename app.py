import io
import os
from pathlib import Path
from flask import Flask, jsonify, render_template, request, send_file, abort
import requests as req

from nhl_connections import (
    open_db,
    build_graph,
    bfs_all_paths,
    _player_name,
    _shared_team,
    earliest_label,
    search_players,
)

DB_PATH    = Path(os.path.dirname(os.path.abspath(__file__))) / "nhl_connections.db"
LOGO_CACHE = Path(os.path.dirname(os.path.abspath(__file__))) / "static" / "logo_cache"
LOGO_CACHE.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

# Build graph once at startup
print("Loading NHL graph…")
_startup_conn = open_db(DB_PATH)
adjacency, _ = build_graph(_startup_conn)
_startup_conn.close()
print("Graph ready.")

# ---------------------------------------------------------------------------
# Era-accurate logo map  (team abbrev → [(start_year, end_year, url), ...])
# ---------------------------------------------------------------------------
_LOGOS = {
  'AFM': [(1972,1979,'https://content.sportslogos.net/logos/1/31/full/atlanta-flames-logo-primary-1973-6835.png')],
  'ANA': [
    (1993,1998,'https://content.sportslogos.net/logos/1/1/full/anaheim_mighty_ducks_logo_primary_1994_sportslogosnet-4557.png'),
    (1999,2005,'https://content.sportslogos.net/logos/1/1/full/anaheim_mighty_ducks_logo_primary_20008281.png'),
    (2006,2009,'https://content.sportslogos.net/logos/1/1736/thumbs/173647352007.gif'),
    (2010,2012,'https://content.sportslogos.net/logos/1/1736/thumbs/fbh4jfr7lwbpuezjx0xbktfmo.gif'),
    (2013,2023,'https://content.sportslogos.net/logos/1/1736/full/1651_anaheim_ducks-primary-20141.png'),
    (2024,9999,'https://content.sportslogos.net/logos/1/1736/thumbs/173614882025.gif'),
  ],
  'ARI': [
    (2003,2020,'https://content.sportslogos.net/logos/1/5263/thumbs/526378072015.gif'),
    (2021,2023,'https://content.sportslogos.net/logos/1/5263/thumbs/526382732022.gif'),
  ],
  'ATL': [(1999,2010,'https://content.sportslogos.net/logos/1/2/thumbs/17.gif')],
  'BOS': [
    (1924,1925,'https://content.sportslogos.net/logos/1/3/thumbs/64hesf7ft39jujwjezsnfoaeg.gif'),
    (1926,1931,'https://content.sportslogos.net/logos/1/3/thumbs/ju2gjoh3fj27ihe1365oyn3ql.gif'),
    (1932,1933,'https://content.sportslogos.net/logos/1/3/thumbs/226.gif'),
    (1934,1948,'https://content.sportslogos.net/logos/1/3/thumbs/227.gif'),
    (1949,1994,'https://content.sportslogos.net/logos/1/3/thumbs/228.gif'),
    (1995,2006,'https://content.sportslogos.net/logos/1/3/thumbs/27.gif'),
    (2007,2007,'https://content.sportslogos.net/logos/1/3/thumbs/320282008.gif'),
    (2008,2022,'https://content.sportslogos.net/logos/1/3/thumbs/venf9fmhgnsawnxxvehf.gif'),
    (2023,2023,'https://content.sportslogos.net/logos/1/3/thumbs/335922024.gif'),
    (2024,2024,'https://content.sportslogos.net/logos/1/3/thumbs/372602025.gif'),
    (2025,9999,'https://content.sportslogos.net/logos/1/3/thumbs/boston-bruins-logo-primary-2026-367442026-thumb.png'),
  ],
  'BUF': [
    (1970,1995,'https://content.sportslogos.net/logos/1/4/full/buffalo_sabres_logo_primary_19714287.png'),
    (1996,1998,'https://content.sportslogos.net/logos/1/4/thumbs/28.gif'),
    (1999,2005,'https://content.sportslogos.net/logos/1/4/full/buffalo_sabres_logo_primary_20007152.png'),
    (2006,2009,'https://content.sportslogos.net/logos/1/4/thumbs/6elec248g0248fybjjx0.gif'),
    (2010,2019,'https://content.sportslogos.net/logos/1/4/full/buffalo_sabres_logo_primary_20116399.png'),
    (2020,9999,'https://content.sportslogos.net/logos/1/4/full/buffalo_sabres_logo_primary_20212495.png'),
  ],
  'CAR': [
    (1997,1998,'https://content.sportslogos.net/logos/1/6/thumbs/hmtieeyhlstetwwn9oknkngfx.gif'),
    (1999,9999,'https://content.sportslogos.net/logos/1/6/thumbs/fotih31tn5r345nufo5xxayh3.gif'),
  ],
  'CBJ': [
    (2000,2006,'https://content.sportslogos.net/logos/1/9/thumbs/47.gif'),
    (2007,9999,'https://content.sportslogos.net/logos/1/9/thumbs/jhepegs329pc7ugyypebl28wg.gif'),
  ],
  'CGS': [
    (1970,1973,'https://content.sportslogos.net/logos/1/38/thumbs/3821431971.gif'),
    (1974,1975,'https://content.sportslogos.net/logos/1/38/thumbs/lsqbnflx2ssqtdz8on09.gif'),
  ],
  'CGY': [
    (1980,1993,'https://content.sportslogos.net/logos/1/5/thumbs/231.gif'),
    (1994,2019,'https://content.sportslogos.net/logos/1/5/thumbs/50.gif'),
    (2020,9999,'https://content.sportslogos.net/logos/1/5/thumbs/548202021.gif'),
  ],
  'CHI': [
    (1926,1934,'https://content.sportslogos.net/logos/1/5462/thumbs/6692.gif'),
    (1935,1936,'https://content.sportslogos.net/logos/1/5462/thumbs/6693.gif'),
    (1937,1940,'https://content.sportslogos.net/logos/1/5462/thumbs/546296431938.gif'),
    (1941,1954,'https://content.sportslogos.net/logos/1/5462/thumbs/236.gif'),
    (1955,1956,'https://content.sportslogos.net/logos/1/5462/thumbs/546276351956.gif'),
    (1957,1958,'https://content.sportslogos.net/logos/1/5462/thumbs/p18vqre4e2mnliddgg5phcvgg.gif'),
    (1959,1985,'https://content.sportslogos.net/logos/1/5462/full/1873_chicago_black_hawks-primary-1966.png'),
    (1986,1988,'https://content.sportslogos.net/logos/1/7/thumbs/734101987.gif'),
    (1989,1995,'https://content.sportslogos.net/logos/1/7/full/4191_chicago_blackhawks-primary-1987.png'),
    (1996,1998,'https://content.sportslogos.net/logos/1/7/thumbs/717441997.gif'),
    (1999,9999,'https://content.sportslogos.net/logos/1/7/full/chicago-blackhawks-logo-primary-2000-1288.png'),
  ],
  'CLE': [(1976,1977,'https://content.sportslogos.net/logos/1/39/thumbs/6076.gif')],
  'CLR': [(1976,1981,'https://content.sportslogos.net/logos/1/36/thumbs/233.gif')],
  'COL': [
    (1995,1998,'https://content.sportslogos.net/logos/1/8/thumbs/colorado-avalanche-logo-primary-1996-6770-thumb.png'),
    (1999,9999,'https://content.sportslogos.net/logos/1/8/thumbs/colorado-avalanche-logo-primary-2000-9420-thumb.png'),
  ],
  'DAL': [
    (1993,1993,'https://content.sportslogos.net/logos/1/10/thumbs/jfp4fwck6nkqi4ygxrp23foz2.gif'),
    (1994,2012,'https://content.sportslogos.net/logos/1/10/thumbs/oxahxcheae4mgu4wopqj9ydhy.gif'),
    (2013,2020,'https://content.sportslogos.net/logos/1/10/full/dallas_stars_logo_primary_20149135.png'),
    (2021,9999,'https://content.sportslogos.net/logos/1/10/full/dallas_stars_logo_primary_2022_sportslogosnet-4332.png'),
  ],
  'DCG': [(1926,1929,'https://content.sportslogos.net/logos/1/994/thumbs/6625.gif')],
  'DET': [
    (1932,1947,'https://content.sportslogos.net/logos/1/11/thumbs/6627.gif'),
    (1948,9999,'https://content.sportslogos.net/logos/1/11/thumbs/yo3wysbjtagzmwj37tb11u0fh.gif'),
  ],
  'DFL': [(1930,1931,'https://content.sportslogos.net/logos/1/995/thumbs/iunfqghm9pjzrslmry1sqxk37.gif')],
  'EDM': [
    (1979,1985,'https://content.sportslogos.net/logos/1/12/thumbs/todebbdindanu63inebdizgff.gif'),
    (1986,1995,'https://content.sportslogos.net/logos/1/12/full/2230_edmonton_oilers-primary-1987.png'),
    (1996,2010,'https://content.sportslogos.net/logos/1/12/thumbs/scxsuiit824f8druiaf83mo3j.gif'),
    (2011,2016,'https://content.sportslogos.net/logos/1/12/thumbs/6cphie5heyvfwn6lbzfowe61h.gif'),
    (2017,2021,'https://content.sportslogos.net/logos/1/12/full/2728_edmonton_oilers-primary-2018.png'),
    (2022,9999,'https://content.sportslogos.net/logos/1/12/thumbs/1229852023.gif'),
  ],
  'FLA': [
    (1993,1998,'https://content.sportslogos.net/logos/1/13/full/florida_panthers_logo_primary_19942293.png'),
    (1999,2015,'https://content.sportslogos.net/logos/1/13/full/florida_panthers_logo_primary_20009805.png'),
    (2016,9999,'https://content.sportslogos.net/logos/1/13/full/florida_panthers_logo_primary_20174924.png'),
  ],
  'HAM': [
    (1920,1920,'https://content.sportslogos.net/logos/1/43/thumbs/xuwdkh8ypy4qe3h3nr6uqnhf0.gif'),
    (1921,1922,'https://content.sportslogos.net/logos/1/43/thumbs/z6k2ifu5yjhbrffneoslajvfv.gif'),
    (1923,1924,'https://content.sportslogos.net/logos/1/43/thumbs/9tltb0pek6mw9h5qbyeqxi7nx.gif'),
  ],
  'HFD': [
    (1979,1991,'https://content.sportslogos.net/logos/1/32/thumbs/9zt534ipbfprlfs2vsqfugw41.gif'),
    (1992,1996,'https://content.sportslogos.net/logos/1/32/thumbs/clzty1tgf8glzas62fq6fyphr.gif'),
  ],
  'KCS': [(1974,1975,'https://content.sportslogos.net/logos/1/35/thumbs/kansas-city-scouts-logo-primary-1975-6971-thumb.png')],
  'LAK': [
    (1967,1974,'https://content.sportslogos.net/logos/1/14/full/los_angeles_kings_logo_primary_19686424.png'),
    (1975,1986,'https://content.sportslogos.net/logos/1/14/full/los_angeles_kings_logo_primary_19764392.png'),
    (1987,1987,'https://content.sportslogos.net/logos/1/14/thumbs/1478521988.gif'),
    (1988,1994,'https://content.sportslogos.net/logos/1/14/full/los_angeles_kings_logo_primary_1989_sportslogosnet-4055.png'),
    (1995,1997,'https://content.sportslogos.net/logos/1/14/thumbs/242.gif'),
    (1998,2001,'https://content.sportslogos.net/logos/1/14/thumbs/106.gif'),
    (2002,2010,'https://content.sportslogos.net/logos/1/14/full/los_angeles_kings_logo_primary_20038964.png'),
    (2011,2012,'https://content.sportslogos.net/logos/1/14/thumbs/1477352012.gif'),
    (2013,2018,'https://content.sportslogos.net/logos/1/14/full/los_angeles_kings_logo_primary_20145268.png'),
    (2019,2023,'https://content.sportslogos.net/logos/1/14/full/los_angeles_kings_logo_primary_20208645.png'),
    (2024,9999,'https://content.sportslogos.net/logos/1/14/thumbs/1478602025.gif'),
  ],
  'MIN': [
    (2000,2012,'https://content.sportslogos.net/logos/1/15/thumbs/0kcehji928suy4ckk1pdo8s7l.gif'),
    (2013,9999,'https://content.sportslogos.net/logos/1/15/thumbs/1588102014.gif'),
  ],
  'MMR': [
    (1924,1924,'https://content.sportslogos.net/logos/1/40/thumbs/4070721925.gif'),
    (1925,1934,'https://content.sportslogos.net/logos/1/40/full/3916_montreal_maroons-primary-1926.png'),
    (1935,1937,'https://content.sportslogos.net/logos/1/40/thumbs/255.gif'),
  ],
  'MNS': [
    (1967,1984,'https://content.sportslogos.net/logos/1/34/full/minnesota-north-stars-logo-primary-1968-8073.png'),
    (1985,1990,'https://content.sportslogos.net/logos/1/34/full/minnesota-north-stars-logo-primary-1986-1120.png'),
    (1991,1992,'https://content.sportslogos.net/logos/1/34/thumbs/minnesota-north-stars-logo-primary-1992-9303-thumb.png'),
  ],
  'MTL': [
    (1917,1918,'https://content.sportslogos.net/logos/1/16/thumbs/6380.gif'),
    (1919,1920,'https://content.sportslogos.net/logos/1/16/thumbs/6381.gif'),
    (1921,1921,'https://content.sportslogos.net/logos/1/16/thumbs/1647491922.gif'),
    (1922,1924,'https://content.sportslogos.net/logos/1/16/thumbs/1674941923.gif'),
    (1925,1931,'https://content.sportslogos.net/logos/1/16/full/montreal_canadiens_logo_primary_19263819.png'),
    (1932,1946,'https://content.sportslogos.net/logos/1/16/full/montreal_canadiens_logo_primary_19332827.png'),
    (1947,1955,'https://content.sportslogos.net/logos/1/16/full/montreal_canadiens_logo_primary_19488591.png'),
    (1956,1998,'https://content.sportslogos.net/logos/1/16/full/montreal_canadiens_logo_primary_19574050.png'),
    (1999,9999,'https://content.sportslogos.net/logos/1/16/full/montreal_canadiens_logo_primary_20001687.png'),
  ],
  'MWN': [(1917,1917,'https://content.sportslogos.net/logos/1/41/thumbs/2czmhnx4hthnb22089ck9kd9d.gif')],
  'NJD': [
    (1982,1985,'https://content.sportslogos.net/logos/1/18/thumbs/4739.gif'),
    (1986,1991,'https://content.sportslogos.net/logos/1/18/thumbs/1834131987.gif'),
    (1992,1998,'https://content.sportslogos.net/logos/1/18/thumbs/127.gif'),
    (1999,9999,'https://content.sportslogos.net/logos/1/18/thumbs/32tfs723a3bes0p0hb4hgcy1u.gif'),
  ],
  'NSH': [
    (1998,2010,'https://content.sportslogos.net/logos/1/17/thumbs/129.gif'),
    (2011,9999,'https://content.sportslogos.net/logos/1/17/thumbs/lvchw3qfsun2e7oc02kh2zxb6.gif'),
  ],
  'NYA': [
    (1925,1932,'https://content.sportslogos.net/logos/1/44/full/8809_new_york_americans-primary-1926.png'),
    (1933,1934,'https://content.sportslogos.net/logos/1/44/thumbs/6999.gif'),
    (1935,1938,'https://content.sportslogos.net/logos/1/44/thumbs/257.gif'),
    (1939,1940,'https://content.sportslogos.net/logos/1/44/thumbs/6836.gif'),
  ],
  'NYI': [
    (1972,1994,'https://content.sportslogos.net/logos/1/19/thumbs/259.gif'),
    (1995,1996,'https://content.sportslogos.net/logos/1/19/thumbs/890.gif'),
    (1997,1997,'https://content.sportslogos.net/logos/1/19/thumbs/1939051998.gif'),
    (1998,2009,'https://content.sportslogos.net/logos/1/19/thumbs/138.gif'),
    (2010,2016,'https://content.sportslogos.net/logos/1/19/thumbs/79520qbne58r9i71zhuggbff0.gif'),
    (2017,9999,'https://content.sportslogos.net/logos/1/19/thumbs/1939112018.gif'),
  ],
  'NYR': [
    (1926,1946,'https://content.sportslogos.net/logos/1/20/full/2711_new_york_rangers-primary-1927.png'),
    (1947,1951,'https://content.sportslogos.net/logos/1/20/full/4599_new_york_rangers-primary-1948.png'),
    (1952,1966,'https://content.sportslogos.net/logos/1/20/thumbs/260.gif'),
    (1967,1970,'https://content.sportslogos.net/logos/1/20/thumbs/6797.gif'),
    (1971,1977,'https://content.sportslogos.net/logos/1/20/full/7488_new_york_rangers-primary-1972.png'),
    (1978,1998,'https://content.sportslogos.net/logos/1/20/full/9612_new_york_rangers-primary-19791.png'),
    (1999,9999,'https://content.sportslogos.net/logos/1/20/full/new_york_rangers-primary_20006777.png'),
  ],
  'OAK': [(1967,1969,'https://content.sportslogos.net/logos/1/1875/thumbs/8883uojgyvl8vmimu8vqwiyuk.gif')],
  'OTT': [
    (1992,1996,'https://content.sportslogos.net/logos/1/21/full/ottawa_senators-primary_19933471.png'),
    (1997,2006,'https://content.sportslogos.net/logos/1/21/full/ottawa_senators-primary_19982022.png'),
    (2007,2019,'https://content.sportslogos.net/logos/1/21/full/ottawa_senators-primary_20086582.png'),
    (2020,9999,'https://content.sportslogos.net/logos/1/21/full/1129_ottawa_senators-primary-2021.png'),
  ],
  'PHI': [
    (1967,1998,'https://content.sportslogos.net/logos/1/22/full/philadelphia_flyers_logo_primary_19684892.png'),
    (1999,2022,'https://content.sportslogos.net/logos/1/22/full/philadelphia_flyers_logo_primary_20008300.png'),
    (2023,9999,'https://content.sportslogos.net/logos/1/22/thumbs/2231452024.gif'),
  ],
  'PHX': [
    (1996,1998,'https://content.sportslogos.net/logos/1/23/thumbs/2321441997.gif'),
    (1999,2002,'https://content.sportslogos.net/logos/1/23/thumbs/86liul9vxxfapth3dt9z3cx6b.gif'),
    (2003,2013,'https://content.sportslogos.net/logos/1/23/thumbs/8lqmtthh0w2wgumr6goswqmki.gif'),
  ],
  'PIR': [
    (1925,1927,'https://content.sportslogos.net/logos/1/47/thumbs/4713001926.gif'),
    (1928,1929,'https://content.sportslogos.net/logos/1/47/thumbs/4744571929.gif'),
  ],
  'PIT': [
    (1967,1967,'https://content.sportslogos.net/logos/1/24/thumbs/269.gif'),
    (1968,1971,'https://content.sportslogos.net/logos/1/24/thumbs/268.gif'),
    (1972,1991,'https://content.sportslogos.net/logos/1/24/full/pittsburgh_penguins_logo_primary_19738696.png'),
    (1992,1998,'https://content.sportslogos.net/logos/1/24/full/pittsburgh_penguins_logo_primary_19935752.png'),
    (1999,2001,'https://content.sportslogos.net/logos/1/24/thumbs/pittsburgh-penguins-logo-primary-2000-1607-thumb.png'),
    (2002,2005,'https://content.sportslogos.net/logos/1/24/thumbs/pittsburgh-penguins-logo-primary-2003-9753-thumb.png'),
    (2006,2015,'https://content.sportslogos.net/logos/1/24/full/pittsburgh-penguins-logo-primary-2007-8889.png'),
    (2016,9999,'https://content.sportslogos.net/logos/1/24/full/pittsburgh-penguins-logo-primary-2017-7099.png'),
  ],
  'QUE': [
    (1979,1984,'https://content.sportslogos.net/logos/1/33/full/quebec-nordiques-logo-primary-1980-8115.png'),
    (1985,1994,'https://content.sportslogos.net/logos/1/33/full/quebec-nordiques-logo-primary-1986-2111.png'),
  ],
  'SEA': [(2021,9999,'https://content.sportslogos.net/logos/1/6740/full/seattle_kraken_logo_primary_20226314.png')],
  'SEN': [
    (1917,1930,'https://content.sportslogos.net/logos/1/45/full/3509_ottawa_senators-primary-1918.png'),
    (1932,1933,'https://content.sportslogos.net/logos/1/45/thumbs/4523001933.gif'),
  ],
  'SJS': [
    (1991,1997,'https://content.sportslogos.net/logos/1/26/full/san_jose_sharks-primary_19921901.png'),
    (1998,2006,'https://content.sportslogos.net/logos/1/26/full/7152_san_jose_sharks-primary-19991.png'),
    (2007,2007,'https://content.sportslogos.net/logos/1/26/thumbs/2663792008.gif'),
    (2008,9999,'https://content.sportslogos.net/logos/1/26/full/san_jose_sharks-primary_20099429.png'),
  ],
  'STL': [
    (1967,1977,'https://content.sportslogos.net/logos/1/25/full/8206_st_louis_blues-primary-1968.png'),
    (1978,1983,'https://content.sportslogos.net/logos/1/25/full/4881_st_louis_blues-primary-1979.png'),
    (1984,1986,'https://content.sportslogos.net/logos/1/25/thumbs/274.gif'),
    (1987,1988,'https://content.sportslogos.net/logos/1/25/thumbs/2577571988.gif'),
    (1989,1997,'https://content.sportslogos.net/logos/1/25/thumbs/glxp9zgg8q7mryq3pdpg07i3g.gif'),
    (1998,1998,'https://content.sportslogos.net/logos/1/25/thumbs/2557871999.gif'),
    (1999,2007,'https://content.sportslogos.net/logos/1/25/full/5991_st_louis_blues-primary-2000.png'),
    (2008,2024,'https://content.sportslogos.net/logos/1/25/thumbs/187.gif'),
    (2025,9999,'https://content.sportslogos.net/logos/1/25/thumbs/st-louis-blues-logo-primary-2026-2533842026-thumb.png'),
  ],
  'TAN': [
    (1917,1917,'https://content.sportslogos.net/logos/1/996/thumbs/lgtkven0lgs74prrf26p6rmes.gif'),
    (1918,1918,'https://content.sportslogos.net/logos/1/6551/thumbs/655116831919.gif'),
  ],
  'TBL': [
    (1992,2000,'https://content.sportslogos.net/logos/1/27/full/tampa_bay_lightning_logo_primary_19939887.png'),
    (2001,2006,'https://content.sportslogos.net/logos/1/27/full/tampa_bay_lightning_logo_primary_20024240.png'),
    (2007,2010,'https://content.sportslogos.net/logos/1/27/thumbs/2uir3bokojm47gzgsigrgrje7.gif'),
    (2011,9999,'https://content.sportslogos.net/logos/1/27/full/tampa_bay_lightning_logo_primary_20124022.png'),
  ],
  'TOR': [
    (1926,1926,'https://content.sportslogos.net/logos/1/28/thumbs/6440.gif'),
    (1927,1937,'https://content.sportslogos.net/logos/1/28/thumbs/278.gif'),
    (1938,1962,'https://content.sportslogos.net/logos/1/28/thumbs/5qxts4g5ygd6rufh0k4ftx8rm.gif'),
    (1963,1966,'https://content.sportslogos.net/logos/1/28/thumbs/201.gif'),
    (1967,1969,'https://content.sportslogos.net/logos/1/28/thumbs/fg1lx64zz806v9yqg32x559ec.gif'),
    (1970,1981,'https://content.sportslogos.net/logos/1/28/thumbs/ryb0um66hojdbf8zs6pz.gif'),
    (1982,1986,'https://content.sportslogos.net/logos/1/28/full/6954_toronto_maple_leafs-primary-1983.png'),
    (1987,2015,'https://content.sportslogos.net/logos/1/28/thumbs/199.gif'),
    (2016,9999,'https://content.sportslogos.net/logos/1/28/full/8761_toronto_maple_leafs-primary-2017.png'),
  ],
  'TSP': [
    (1919,1921,'https://content.sportslogos.net/logos/1/997/thumbs/6438.gif'),
    (1922,1924,'https://content.sportslogos.net/logos/1/997/thumbs/280.gif'),
    (1925,1925,'https://content.sportslogos.net/logos/1/997/thumbs/2c1nvj3dym87s20wa0hx6vuhv.gif'),
    (1926,1926,'https://content.sportslogos.net/logos/1/997/thumbs/279.gif'),
  ],
  'UTA': [
    (2024,2024,'https://content.sportslogos.net/logos/1/6902/thumbs/690280952025.gif'),
    (2025,9999,'https://content.sportslogos.net/logos/1/6944/thumbs/utah-mammoth-logo-primary-2026-694433562026-thumb.png'),
  ],
  'VAN': [
    (1970,1977,'https://content.sportslogos.net/logos/1/29/full/vancouver_canucks_logo_primary_19711115.png'),
    (1978,1991,'https://content.sportslogos.net/logos/1/29/full/vancouver_canucks_logo_primary_19792867.png'),
    (1992,1996,'https://content.sportslogos.net/logos/1/29/full/vancouver_canucks_logo_primary_19933385.png'),
    (1997,2006,'https://content.sportslogos.net/logos/1/29/full/vancouver_canucks_logo_primary_19982644.png'),
    (2007,2018,'https://content.sportslogos.net/logos/1/29/thumbs/tlxz9higxwygaehl0j5h.gif'),
    (2019,9999,'https://content.sportslogos.net/logos/1/29/full/2084_vancouver_canucks-primary-2020.png'),
  ],
  'VGK': [(2017,9999,'https://content.sportslogos.net/logos/1/6114/full/vegas_golden_knights_logo_primary_20185183.png')],
  'WIN': [
    (1979,1989,'https://content.sportslogos.net/logos/1/37/full/winnipeg-jets-logo-primary-1980-8159.png'),
    (1990,1995,'https://content.sportslogos.net/logos/1/37/thumbs/dhhc15gfgtog1qo5b5uwpz8h0.gif'),
  ],
  'WPG': [(2011,9999,'https://content.sportslogos.net/logos/1/3050/thumbs/z9qyy9xqoxfjn0njxgzoy2rwk.gif')],
  'WSH': [
    (1974,1994,'https://content.sportslogos.net/logos/1/30/full/washington_capitals-primary_19755453.png'),
    (1995,1996,'https://content.sportslogos.net/logos/1/30/thumbs/214.gif'),
    (1997,2001,'https://content.sportslogos.net/logos/1/30/thumbs/215.gif'),
    (2002,2006,'https://content.sportslogos.net/logos/1/30/thumbs/paefgo9d0nm54u2nlzts.gif'),
    (2007,9999,'https://content.sportslogos.net/logos/1/30/full/washington-capitals-logo-primary-2008-5033.png'),
  ],
}

_ALLOWED = {"content.sportslogos.net"}

def _logo_url(team: str, season: str) -> str | None:
    year = int(season.split("-")[0])
    for s, e, url in _LOGOS.get(team.upper(), []):
        if s <= year <= e:
            return url
    eras = _LOGOS.get(team.upper())
    return eras[-1][2] if eras else None


def _db():
    return open_db(DB_PATH)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    conn = _db()
    rows = search_players(conn, q)
    out = [
        {"id": row["id"], "name": row["name"], "era": earliest_label(conn, row["id"])}
        for row in rows[:15]
    ]
    conn.close()
    return jsonify(out)


@app.route("/api/path")
def api_path():
    try:
        p1_id = int(request.args["p1"])
        p2_id = int(request.args["p2"])
    except (KeyError, ValueError):
        return jsonify({"error": "Both p1 and p2 player IDs are required."}), 400

    if p1_id == p2_id:
        return jsonify({"error": "Select two different players."}), 400

    paths, hops, total = bfs_all_paths(adjacency, p1_id, p2_id, limit=1)
    if paths is None:
        return jsonify({"error": "No connection found between these players."}), 404

    conn = _db()
    path = paths[0]
    steps = []
    for i, pid in enumerate(path):
        step = {"name": _player_name(conn, pid), "era": earliest_label(conn, pid)}
        if i > 0:
            team, season = _shared_team(conn, path[i - 1], pid)
            step["team"] = team
            step["season"] = season
        steps.append(step)
    conn.close()
    return jsonify({"hops": hops, "total_paths": total, "steps": steps})


@app.route("/logo")
def proxy_logo():
    """Fetch a team logo from sportslogos.net, cache it locally, serve it."""
    team   = request.args.get("team", "").upper()
    season = request.args.get("season", "")
    if not team or not season:
        abort(400)

    url = _logo_url(team, season)
    if not url:
        abort(404)

    # Derive a safe cache filename from the URL path
    fname = url.rsplit("/", 1)[-1]
    cached = LOGO_CACHE / fname

    if not cached.exists():
        try:
            resp = req.get(
                url,
                headers={
                    "Referer": "https://www.sportslogos.net/",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120 Safari/537.36",
                },
                timeout=10,
            )
            resp.raise_for_status()
            cached.write_bytes(resp.content)
        except Exception:
            abort(404)

    mime = "image/png" if fname.endswith(".png") else "image/gif"
    return send_file(cached, mimetype=mime, max_age=86400 * 30)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
