#!/usr/bin/env python3
"""
GearBlast Leaderboard Updater
Firebase Realtime Database'den leaderboard verilerini çeker,
data/leaderboard.json dosyasına yazar.
"""

import json
import os
import sys
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, db

MODES = ["classic_normal", "classic_timed", "extra_normal", "extra_timed"]
LIMIT = 10000  # Her mod için çekilecek maksimum oyuncu sayısı

def init_firebase():
    key_json = os.environ.get("FIREBASE_KEY")
    if not key_json:
        print("ERROR: FIREBASE_KEY environment variable not set")
        sys.exit(1)

    key_dict = json.loads(key_json)
    db_url   = os.environ.get("FIREBASE_DB_URL")
    if not db_url:
        print("ERROR: FIREBASE_DB_URL environment variable not set")
        sys.exit(1)

    cred = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(cred, {"databaseURL": db_url})
    print(f"[Firebase] Connected: {db_url}")

def fetch_mode(mode):
    ref   = db.reference(f"leaderboards/{mode}")
    # orderByChild("score") ile sıralı çek, en yüksek LIMIT kişi
    query = ref.order_by_child("score").limit_to_last(LIMIT)
    raw   = query.get()

    if not raw:
        print(f"[{mode}] No data")
        return []

    entries = []
    for uid, val in raw.items():
        try:
            # Gelen verinin sözlük (dict) olduğundan emin ol
            if not isinstance(val, dict):
                print(f"[{mode}] Skipping invalid entry for {uid}: not a dict")
                continue
                
            entries.append({
                "uid":           uid,
                "username":      val.get("username", "???"),
                "score":         int(val.get("score", 0)),
                "leagueLevel":   int(val.get("leagueLevel", 0)),
                "playstyleLevel":int(val.get("playstyleLevel", 0)),
                "avatarId":      int(val.get("avatarId", 1)),
            })
        except Exception as e:
            print(f"[{mode}] Error parsing entry {uid}: {e}")
            continue

    print(f"[{mode}] Raw entries count: {len(entries)}")

    # Skora göre büyükten küçüğe sırala
    entries.sort(key=lambda x: x["score"], reverse=True)

    # Rank ve Rozet Kuralı ekle
    for i, e in enumerate(entries):
        rank = i + 1
        e["rank"] = rank
        
    print(f"[{mode}] Fetched and cleaned {len(entries)} entries")
    return entries

def main():
    init_firebase()

    output = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_players": 0,
    }

    total = 0
    for mode in MODES:
        entries = fetch_mode(mode)
        output[mode] = entries
        total = max(total, len(entries))

    output["total_players"] = total

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "leaderboard.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[Done] leaderboard.json updated at {output['updated_at']}")

if __name__ == "__main__":
    main()
