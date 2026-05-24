"""One-off deploy script: add MLB AZ + CWS team-code aliases to prod.

Companion to commit d6d54d3. Surgical str.replace patch; EOL-aware.
Backup tagged .pre-mlb-aliases-20260524.
"""
import hashlib, shutil, sys

TAG = "pre-mlb-aliases-20260524"
PATH = "/home/azureuser/trading_corp/trading_corp/data/sports_team_mapping.py"

OLD = (
    'MLB_TEAMS: dict[str, str] = {\n'
    '    "ARI": "Arizona Diamondbacks",   "ATL": "Atlanta Braves",\n'
    '    "BAL": "Baltimore Orioles",      "BOS": "Boston Red Sox",\n'
    '    "CHC": "Chicago Cubs",           "CHW": "Chicago White Sox",\n'
    '    "CIN": "Cincinnati Reds",        "CLE": "Cleveland Guardians",\n'
)
NEW = (
    'MLB_TEAMS: dict[str, str] = {\n'
    '    "ARI": "Arizona Diamondbacks",   "ATL": "Atlanta Braves",\n'
    '    "AZ":  "Arizona Diamondbacks",\n'
    '    "BAL": "Baltimore Orioles",      "BOS": "Boston Red Sox",\n'
    '    "CHC": "Chicago Cubs",           "CHW": "Chicago White Sox",\n'
    '    "CWS": "Chicago White Sox",\n'
    '    "CIN": "Cincinnati Reds",        "CLE": "Cleveland Guardians",\n'
)


def main():
    with open(PATH, "rb") as f:
        data = f.read()
    is_crlf = b"\r\n" in data[:8192]
    text = data.decode("utf-8")
    md5_before = hashlib.md5(data).hexdigest()
    shutil.copy2(PATH, f"{PATH}.{TAG}")
    old = OLD.replace("\n", "\r\n") if is_crlf else OLD
    new = NEW.replace("\n", "\r\n") if is_crlf else NEW
    n = text.count(old)
    if n != 1:
        print(f"FAIL: old found {n}x (need 1) - abort")
        sys.exit(1)
    text = text.replace(old, new, 1)
    new_data = text.encode("utf-8")
    md5_after = hashlib.md5(new_data).hexdigest()
    with open(PATH, "wb") as f:
        f.write(new_data)
    print(f"OK: {PATH} crlf={is_crlf} {md5_before} -> {md5_after}")


if __name__ == "__main__":
    main()
