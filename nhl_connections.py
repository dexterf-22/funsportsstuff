#!/usr/bin/env python3
"""
NHL Connection Finder
Finds the shortest chain of teammates connecting any two NHL players across history.

A "connection" = two players who appeared on the same team roster in the same season.

Usage:
  python nhl_connections.py fetch              # download all roster data (~30-60 min)
  python nhl_connections.py fetch --start-season 19171918 --end-season 19601961
  python nhl_connections.py search "Gretzky"   # find player IDs by name
  python nhl_connections.py path "Connor McDavid" "Joe Malone"
  python nhl_connections.py history "Connor McDavid"  # min hops to oldest era
"""

import requests
import sqlite3
import unicodedata
import time
import sys
import argparse
from collections import defaultdict, deque
from pathlib import Path

DB_PATH = Path("nhl_connections.db")
NHL_STATS_API = "https://api.nhle.com/stats/rest/en"
RATE_LIMIT = 0.4  # seconds between requests


def ascii_name(name: str) -> str:
    """'Tim Stützle' → 'tim stutzle'  (strips diacritics, lowercases)."""
    nfkd = unicodedata.normalize("NFKD", name)
    return nfkd.encode("ascii", "ignore").decode("ascii").lower()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def open_db(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            name_ascii TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS seasons_fetched (
            season TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS rosters (
            player_id   INTEGER NOT NULL,
            team_abbrev TEXT    NOT NULL,
            season      TEXT    NOT NULL,
            PRIMARY KEY (player_id, team_abbrev, season)
        );
        CREATE INDEX IF NOT EXISTS idx_rosters_player ON rosters(player_id);
        CREATE INDEX IF NOT EXISTS idx_rosters_team_season ON rosters(team_abbrev, season);
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _stats_get(session: requests.Session, endpoint: str, params: dict = None) -> dict:
    url = f"{NHL_STATS_API}/{endpoint}"
    resp = session.get(url, params=params, timeout=20)
    resp.raise_for_status()
    time.sleep(RATE_LIMIT)
    return resp.json()


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_season_players(session: requests.Session, season_id: str) -> list[dict]:
    """
    Fetch all skaters and goalies who played in a given season.
    Returns list of dicts with keys: playerId, playerName, teamAbbrevs.
    Paginates using the 'total' field from the response so the API's
    per-page cap (typically 100) doesn't silently truncate results.
    """
    players = []
    page_size = 100

    for pos in ("skater", "goalie"):
        start = 0
        while True:
            try:
                data = _stats_get(session, f"{pos}/summary", {
                    "limit": page_size,
                    "start": start,
                    "cayenneExp": f"seasonId={season_id}",
                })
            except requests.HTTPError as exc:
                print(f"    Warning: {pos} fetch failed ({exc})")
                break

            page  = data.get("data", [])
            total = data.get("total", 0)
            players.extend(page)

            start += len(page)
            if not page or start >= total:
                break

    return players


def fetch_all(conn: sqlite3.Connection, start_season: str = None, end_season: str = None):
    """
    Download every NHL season's rosters into the local SQLite cache.
    Uses the NHL Stats REST API (api.nhle.com/stats/rest/en).
    Already-fetched seasons are skipped, so this is safe to re-run.
    """
    session = requests.Session()
    session.headers["User-Agent"] = "NHLConnectionFinder/1.0 (educational)"

    print("Fetching season list from NHL API...")
    # Returns {"data": [{"id": 20242025, ...}, ...], "total": N}
    seasons_data = _stats_get(session, "season")
    # Season IDs are integers; store as zero-padded 8-char strings
    seasons = sorted(str(s["id"]) for s in seasons_data["data"])

    if start_season:
        seasons = [s for s in seasons if s >= start_season]
    if end_season:
        seasons = [s for s in seasons if s <= end_season]

    total = len(seasons)
    print(f"Will process {total} season(s).\n")

    for idx, season_id in enumerate(seasons, 1):
        already = conn.execute(
            "SELECT 1 FROM seasons_fetched WHERE season=?", (season_id,)
        ).fetchone()
        if already:
            print(f"[{idx:3}/{total}] {season_id}  (cached)")
            continue

        players = _fetch_season_players(session, season_id)
        player_count = 0

        for p in players:
            pid   = p.get("playerId")
            # skater endpoint uses skaterFullName; goalie endpoint uses goalieFullName
            pname = (p.get("skaterFullName") or p.get("goalieFullName")
                     or p.get("playerName") or "")
            teams_raw = p.get("teamAbbrevs", "") or ""

            if not pid or not pname:
                continue

            conn.execute(
                "INSERT OR IGNORE INTO players (id, name, name_ascii) VALUES (?, ?, ?)",
                (pid, pname, ascii_name(pname)),
            )

            # teamAbbrevs may be "EDM", "TOR/MTL", or "ANA,BUF" (traded)
            for abbrev in teams_raw.replace(",", "/").split("/"):
                abbrev = abbrev.strip()
                if abbrev:
                    conn.execute(
                        "INSERT OR IGNORE INTO rosters (player_id, team_abbrev, season) "
                        "VALUES (?, ?, ?)",
                        (pid, abbrev, season_id),
                    )
                    player_count += 1

        conn.execute("INSERT INTO seasons_fetched VALUES (?)", (season_id,))
        conn.commit()
        print(f"[{idx:3}/{total}] {season_id}  {player_count} player-team entries")

    print("\nFetch complete.")


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph(conn: sqlite3.Connection):
    """
    Returns:
        adjacency  – dict[player_id -> set of neighbour player_ids]
        earliest   – dict[player_id -> earliest season string, e.g. '19171918']
    """
    print("Building connection graph...", flush=True)

    # Earliest season per player
    earliest: dict[int, str] = {}
    for row in conn.execute(
        "SELECT player_id, MIN(season) AS es FROM rosters GROUP BY player_id"
    ):
        earliest[row["player_id"]] = row["es"]

    # Adjacency: players share an edge iff they share a team-season
    adjacency: dict[int, set] = defaultdict(set)
    for row in conn.execute(
        "SELECT team_abbrev, season, GROUP_CONCAT(player_id) AS pids "
        "FROM rosters GROUP BY team_abbrev, season"
    ):
        ids = [int(p) for p in row["pids"].split(",")]
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                adjacency[a].add(b)
                adjacency[b].add(a)

    print(f"Graph ready: {len(adjacency):,} players with connections.\n")
    return adjacency, earliest


# ---------------------------------------------------------------------------
# Algorithms
# ---------------------------------------------------------------------------

def bfs_all_paths(
    adjacency: dict, src: int, dst: int, limit: int = 1
) -> tuple[list[list[int]] | None, int | None, int]:
    """
    Find shortest paths from src to dst.

    Returns (paths, hop_count, total_count) where:
      paths       — up to `limit` shortest paths (each a list of player IDs)
      hop_count   — length of each path in hops
      total_count — total number of distinct shortest paths that exist

    Returns (None, None, 0) if no path exists.
    """
    if src == dst:
        return [[src]], 0, 1
    if src not in adjacency or dst not in adjacency:
        return None, None, 0

    # BFS: record EVERY parent that lies on a shortest path, not just one.
    # A node `p` is a valid parent of `nbr` iff dist[p] + 1 == dist[nbr].
    dist    = {src: 0}
    parents: dict[int, set] = defaultdict(set)
    queue   = deque([src])

    while queue:
        node = queue.popleft()
        for nbr in adjacency[node]:
            if nbr not in dist:
                dist[nbr] = dist[node] + 1
                queue.append(nbr)
            if dist[nbr] == dist[node] + 1:
                parents[nbr].add(node)

    if dst not in dist:
        return None, None, 0

    hop_count = dist[dst]

    # Count ALL shortest paths via DP on the distance-layered DAG.
    # path_count[v] = number of shortest paths from src to v.
    path_count: dict[int, int] = {src: 1}
    for d in range(1, hop_count + 1):
        for node, nd in dist.items():
            if nd == d:
                path_count[node] = sum(path_count.get(p, 0) for p in parents[node])
    total_count = path_count.get(dst, 0)

    # DFS backward through the parents DAG to enumerate up to `limit` paths.
    paths: list[list[int]] = []

    def _dfs(node: int, path: list[int]) -> None:
        if len(paths) >= limit:
            return
        if node == src:
            paths.append(path[::-1])
            return
        for parent in parents[node]:
            path.append(parent)
            _dfs(parent, path)
            path.pop()

    _dfs(dst, [dst])
    return paths, hop_count, total_count


def _bfs_distances(adjacency: dict, src: int) -> dict:
    """BFS from src; returns dist dict (no parent tracking)."""
    dist = {src: 0}
    queue = deque([src])
    while queue:
        node = queue.popleft()
        d = dist[node] + 1
        for nbr in adjacency[node]:
            if nbr not in dist:
                dist[nbr] = d
                queue.append(nbr)
    return dist


def _bfs_path(adjacency: dict, src: int, dst: int) -> list[int]:
    """BFS from src to dst; returns the path as a list of player IDs."""
    if src == dst:
        return [src]
    parent = {src: None}
    queue = deque([src])
    while queue:
        node = queue.popleft()
        for nbr in adjacency[node]:
            if nbr not in parent:
                parent[nbr] = node
                if nbr == dst:
                    path = []
                    cur = dst
                    while cur is not None:
                        path.append(cur)
                        cur = parent[cur]
                    return path[::-1]
                queue.append(nbr)
    return []


def find_diameter(adjacency: dict) -> tuple:
    """
    Exact graph diameter via full BFS from every node in the main connected
    component.  Returns (max_dist, src_id, dst_id).
    Path reconstruction is left to the caller (call _bfs_path separately).
    """
    # Identify connected components
    visited: set[int] = set()
    components: list[set] = []
    for start in adjacency:
        if start not in visited:
            comp: set[int] = set()
            q: deque = deque([start])
            visited.add(start)
            while q:
                n = q.popleft()
                comp.add(n)
                for nb in adjacency[n]:
                    if nb not in visited:
                        visited.add(nb)
                        q.append(nb)
            components.append(comp)

    main_comp = max(components, key=len)
    isolated  = sum(1 for c in components if len(c) == 1)
    print(f"Main connected component: {len(main_comp):,} players  "
          f"({len(components) - 1} other component(s), {isolated} isolated)")

    nodes = list(main_comp)
    total = len(nodes)

    # Double-BFS warm-up: quick lower bound
    seed = nodes[0]
    d1   = _bfs_distances(adjacency, seed)
    u    = max(main_comp, key=lambda x: d1.get(x, 0))
    d2   = _bfs_distances(adjacency, u)
    v    = max(main_comp, key=lambda x: d2.get(x, 0))
    best_dist = d2[v]
    best_src, best_dst = u, v
    print(f"Double-BFS lower bound: {best_dist}  ({best_src} → {best_dst})")
    print(f"Running full scan ({total:,} BFS calls)…", flush=True)

    for i, src in enumerate(nodes):
        if i % 1000 == 0 and i:
            pct = 100 * i // total
            print(f"  {i:,}/{total:,} ({pct}%)  current max={best_dist}", flush=True)
        dist = _bfs_distances(adjacency, src)
        far  = max(main_comp, key=lambda x: dist.get(x, 0))
        d    = dist[far]
        if d > best_dist:
            best_dist = d
            best_src, best_dst = src, far

    return best_dist, best_src, best_dst


def bfs_history(
    adjacency: dict, earliest: dict, start: int, max_hops: int = 15
) -> list[tuple]:
    """
    BFS from `start`. Whenever a player is reached whose earliest season
    is older than any previously seen, record the find.

    Returns list of (hops, player_id, earliest_season, path) sorted by hops.
    """
    if start not in adjacency:
        return []

    best_year = int(earliest.get(start, "99999999")[:4])
    findings  = []

    # Store (player_id, path) in the queue
    parent = {start: None}
    queue  = deque([(start, [start])])

    while queue:
        node, path = queue.popleft()
        hops = len(path) - 1

        if hops > max_hops:
            continue

        season = earliest.get(node, "99999999")
        year   = int(season[:4])

        if year < best_year:
            best_year = year
            findings.append((hops, node, season, path[:]))

        for nbr in adjacency[node]:
            if nbr not in parent:
                parent[nbr] = node
                queue.append((nbr, path + [nbr]))

    return sorted(findings, key=lambda x: x[0])


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _player_name(conn: sqlite3.Connection, pid: int) -> str:
    row = conn.execute("SELECT name FROM players WHERE id=?", (pid,)).fetchone()
    return row["name"] if row else f"#  {pid}"


def _shared_team(conn: sqlite3.Connection, p1: int, p2: int) -> tuple[str, str]:
    """Return (team_abbrev, season_label) for the earliest shared team-season."""
    row = conn.execute(
        """
        SELECT r1.team_abbrev, r1.season
        FROM rosters r1
        JOIN rosters r2 ON r1.team_abbrev = r2.team_abbrev AND r1.season = r2.season
        WHERE r1.player_id = ? AND r2.player_id = ?
        ORDER BY r1.season ASC
        LIMIT 1
        """,
        (p1, p2),
    ).fetchone()
    if row:
        s = row["season"]
        label = f"{s[:4]}-{s[6:]}"  # e.g. 1917-18
        return row["team_abbrev"], label
    return "?", "?"


def print_path(conn: sqlite3.Connection, path: list[int]):
    if not path:
        print("  (empty path)")
        return

    name0 = _player_name(conn, path[0])
    era0  = earliest_label(conn, path[0])
    print(f"  1. {name0}  {era0}")

    for i in range(1, len(path)):
        name = _player_name(conn, path[i])
        era  = earliest_label(conn, path[i])
        team, season = _shared_team(conn, path[i - 1], path[i])
        print(f"     └─ {name}  {era}  (via {team}, {season})")


def earliest_label(conn: sqlite3.Connection, pid: int) -> str:
    row = conn.execute(
        "SELECT MIN(season) AS es, MAX(season) AS ls FROM rosters WHERE player_id=?",
        (pid,),
    ).fetchone()
    if row and row["es"]:
        return f"[{row['es'][:4]}–{row['ls'][:4]}]"
    return ""


# ---------------------------------------------------------------------------
# Player search / disambiguation
# ---------------------------------------------------------------------------

def search_players(conn: sqlite3.Connection, query: str) -> list:
    # Search the original name (handles correct Unicode input)
    # AND the ASCII-normalised column (handles "stutzle" matching "Stützle")
    q_ascii = f"%{ascii_name(query)}%"
    q_raw   = f"%{query}%"
    return conn.execute(
        "SELECT id, name FROM players "
        "WHERE name LIKE ? OR name_ascii LIKE ? "
        "ORDER BY name",
        (q_raw, q_ascii),
    ).fetchall()


def pick_player(conn: sqlite3.Connection, query: str):
    results = search_players(conn, query)
    if not results:
        print(f"No player found matching '{query}'.")
        print("Tip: run  python nhl_connections.py search <name>  to find exact spellings.")
        return None

    if len(results) == 1:
        return results[0]

    print(f"\nMultiple players match '{query}':")
    for i, row in enumerate(results[:10], 1):
        era = earliest_label(conn, row["id"])
        print(f"  {i}. {row['name']}  {era}")
    if len(results) > 10:
        print(f"  … and {len(results) - 10} more. Refine your search.")

    try:
        choice = int(input("Pick a number: ").strip()) - 1
        return results[choice]
    except (ValueError, IndexError):
        print("Invalid choice.")
        return None


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_fetch(args, conn):
    fetch_all(conn, start_season=args.start_season, end_season=args.end_season)


def cmd_search(args, conn):
    results = search_players(conn, args.name)
    if not results:
        print(f"No players found matching '{args.name}'.")
        return
    print(f"{len(results)} result(s) for '{args.name}':")
    for row in results[:25]:
        era = earliest_label(conn, row["id"])
        print(f"  [{row['id']:7}] {row['name']:<30} {era}")
    if len(results) > 25:
        print(f"  … {len(results) - 25} more results omitted.")


def cmd_path(args, conn):
    adjacency, earliest = build_graph(conn)

    p1 = pick_player(conn, args.player1)
    p2 = pick_player(conn, args.player2)
    if not p1 or not p2:
        return

    limit = args.max_paths if args.all else 1
    print(f"\nSearching: {p1['name']} → {p2['name']} …")
    paths, hops, total = bfs_all_paths(adjacency, p1["id"], p2["id"], limit=limit)

    if paths is None:
        print("\nNo connection found. The players may be in disconnected parts of the graph.")
        return

    if args.all:
        showing = len(paths)
        if total > showing:
            print(f"\nShortest chain: {hops} connection(s)  "
                  f"({total:,} total paths — showing first {showing})\n")
        else:
            print(f"\nShortest chain: {hops} connection(s)  ({total:,} path(s) total)\n")
        for i, path in enumerate(paths, 1):
            print(f"── Path {i} ──")
            print_path(conn, path)
            print()
    else:
        print(f"\nShortest chain: {hops} connection(s)  "
              f"({total:,} total path(s) of this length — use --all to see them)\n")
        print_path(conn, paths[0])


def cmd_diameter(args, conn):
    adjacency, _ = build_graph(conn)

    max_dist, src_id, dst_id = find_diameter(adjacency)

    src_name = _player_name(conn, src_id)
    dst_name = _player_name(conn, dst_id)
    print(f"\nGraph diameter: {max_dist} hops")
    print(f"  {src_name}  →  {dst_name}\n")

    path = _bfs_path(adjacency, src_id, dst_id)
    print_path(conn, path)


def cmd_history(args, conn):
    adjacency, earliest = build_graph(conn)

    start = pick_player(conn, args.player)
    if not start:
        return

    print(f"\nSearching from {start['name']} for oldest reachable players (max {args.max_hops} hops)…\n")
    findings = bfs_history(adjacency, earliest, start["id"], max_hops=args.max_hops)

    if not findings:
        print("No results.")
        return

    print(f"{'Hops':<6} {'Player':<30} {'First season'}")
    print("-" * 55)
    for hops, pid, season, path in findings:
        name  = _player_name(conn, pid)
        label = f"{season[:4]}-{season[6:]}"
        print(f"{hops:<6} {name:<30} {label}")

    # Show the full path to the oldest find
    best = min(findings, key=lambda x: x[2])
    print(f"\nFull chain to furthest reach ({_player_name(conn, best[1])}):\n")
    print_path(conn, best[3])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Find the shortest chain of NHL teammates connecting any two players.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # fetch
    pf = sub.add_parser("fetch", help="Download NHL roster data (run once)")
    pf.add_argument("--start-season", metavar="SSSSEEEE", default=None,
                    help="Only fetch from this season onward, e.g. 19171918")
    pf.add_argument("--end-season",   metavar="SSSSEEEE", default=None,
                    help="Only fetch up to this season, e.g. 20232024")

    # search
    ps = sub.add_parser("search", help="Find players by name")
    ps.add_argument("name", help="Partial name to search for")

    # path
    pp = sub.add_parser("path", help="Shortest connection between two named players")
    pp.add_argument("player1", help="Starting player (e.g. 'Connor McDavid')")
    pp.add_argument("player2", help="Target player (e.g. 'Joe Malone')")
    pp.add_argument("--all", action="store_true",
                    help="Show all shortest paths, not just one")
    pp.add_argument("--max-paths", type=int, default=20,
                    help="Cap on paths shown with --all (default: 20)")

    # history
    ph = sub.add_parser("history", help="Minimum hops from a player to the oldest reachable era")
    ph.add_argument("player", help="Starting player name")
    ph.add_argument("--max-hops", type=int, default=12,
                    help="Maximum chain length to explore (default: 12)")

    # diameter
    sub.add_parser("diameter", help="Find the two players with the longest shortest connection path")

    args = parser.parse_args()
    conn = open_db()

    dispatch = {
        "fetch":    cmd_fetch,
        "search":   cmd_search,
        "path":     cmd_path,
        "history":  cmd_history,
        "diameter": cmd_diameter,
    }
    dispatch[args.command](args, conn)
    conn.close()


if __name__ == "__main__":
    main()
