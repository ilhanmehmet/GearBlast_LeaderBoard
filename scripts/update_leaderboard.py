#!/usr/bin/env python3
"""
GearBlast Leaderboard Updater
Firebase Realtime Database'den leaderboard verilerini çeker,
data/leaderboard.json dosyasına yazar.

Kimlik bilgisi (öncelik sırasıyla):
  1) FIREBASE_SERVICE_ACCOUNT_PATH — JSON dosya yolu (yerel çalıştırma)
  2) FIREBASE_KEY_BASE64 — service account JSON'un base64 kodu (GitHub Secrets için önerilir)
  3) FIREBASE_KEY — ham JSON string

Veritabanı URL:
  FIREBASE_DB_URL — örn. https://xxx-default-rtdb.europe-west1.firebasedatabase.app

Not: order_by_child("score") kullanılmıyor; RTDB'de .indexOn eksikliğinden kaynaklanan
     sorgu hatalarını (exit code 1) önlemek için tüm düğümler çekilip Python'da sıralanıyor.
"""

import base64
import json
import os
import sys
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, db

MODES = ["classic_normal", "classic_timed", "adventure"]
LIMIT = 2000  # Maksimum kayıt sayısı
MAX_AVATAR_ID = 65

# Gear Blast 2.0 global liste kesiti (2026-06-12 00:00 UTC). Eski Extra/v1 skorlari haric tutulur.
V2_CUTOFF_TS = int(os.environ.get("V2_CUTOFF_TS", "1781222400"))
# versionCode 20 = oyun 2.0.0
MIN_VERSION_CODE = int(os.environ.get("MIN_VERSION_CODE", "20"))


def include_legacy_scores():
    return os.environ.get("INCLUDE_LEGACY_SCORES", "").strip().lower() in ("1", "true", "yes")


def entry_timestamp(val):
    try:
        return int(val.get("timestamp", 0) or 0)
    except (TypeError, ValueError):
        return 0


def entry_version_code(val):
    if "versionCode" not in val:
        return None
    try:
        return int(val.get("versionCode", 0) or 0)
    except (TypeError, ValueError):
        return None


def is_chicken_entry(val):
  if not isinstance(val, dict):
    return False
  ic = val.get("isChicken")
  if ic is True or ic in (1, "1", "true", "True"):
    return True
  try:
    return int(val.get("avatarId", 1) or 1) == 999
  except (TypeError, ValueError):
    return False


def stable_avatar_id(uid, username):
    """Oyunla ayni: uid/isim hash -> 2-20 arasi sabit avatar."""
    if uid and uid not in ("", "anonymous"):
        key = f"uid:{uid}"
    elif username and username != "???":
        key = f"name:{username.lower()}"
    else:
        key = "player"
    h = 0
    for ch in key:
        h = (h * 31 + ord(ch)) % 2147483647
    return 2 + (h % max(1, MAX_AVATAR_ID - 1))


_users_profile_cache = None


def load_users_profile_map():
    global _users_profile_cache
    if _users_profile_cache is not None:
        return _users_profile_cache
    cache = {}
    try:
        raw = db.reference("users").get()
        if isinstance(raw, dict):
            for uid, val in raw.items():
                if not isinstance(val, dict):
                    continue
                ic = val.get("isChicken")
                is_ch = (ic is True) or (ic in (1, "1", "true", "True")) or (val.get("avatarId") == 999)
                
                avatar = None
                for field in ("avatarId", "savedAvatarId"):
                    try:
                        av = int(val.get(field) or 0)
                    except (TypeError, ValueError):
                        av = 0
                    if 2 <= av <= MAX_AVATAR_ID and av != 999:
                        avatar = av
                        break
                cache[uid] = {
                    "isChicken": is_ch,
                    "avatarId": avatar
                }
    except Exception as e:
        print(f"[warn] users profile fetch failed: {e}")
    _users_profile_cache = cache
    print(f"[users] Loaded {len(cache)} profile records")
    return cache


def resolve_avatar_id(val, uid, users_profile):
    if is_chicken_entry(val):
        return 999
    profile = users_profile.get(uid, {})
    if profile.get("isChicken"):
        return 999
    try:
        lb_av = int(val.get("avatarId", 1) or 1)
    except (TypeError, ValueError):
        lb_av = 1
    if lb_av == 999:
        return 999
    profile_av = profile.get("avatarId")
    if profile_av and 2 <= profile_av <= MAX_AVATAR_ID:
        return profile_av
    if 2 <= lb_av <= MAX_AVATAR_ID:
        return lb_av
    return stable_avatar_id(uid, val.get("username", "???"))


def passes_global_list_filter(val):
    """2.0.0+ (versionCode>=20) veya versionCode yoksa 12 Haziran sonrasi skorlar."""
    if include_legacy_scores():
        return True

    vc = entry_version_code(val)
    if vc is not None:
        return vc >= MIN_VERSION_CODE

    return entry_timestamp(val) >= V2_CUTOFF_TS


def load_service_account_dict():
    path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
    if path:
        if not os.path.isfile(path):
            print(f"ERROR: FIREBASE_SERVICE_ACCOUNT_PATH not a file: {path}")
            sys.exit(1)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # GitHub Secret çok satırlı yapıştırılırsa ortada \n kalır; base64'i tek sürekli satır say.
    b64 = "".join(os.environ.get("FIREBASE_KEY_BASE64", "").split())
    if b64:
        try:
            raw = base64.b64decode(b64)
            return json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            print(f"ERROR: FIREBASE_KEY_BASE64 decode/parse failed: {e}")
            sys.exit(1)

    key_json = os.environ.get("FIREBASE_KEY", "").strip()
    if key_json:
        try:
            return json.loads(key_json)
        except json.JSONDecodeError as e:
            print(
                "ERROR: FIREBASE_KEY is not valid JSON. "
                "Use FIREBASE_KEY_BASE64 secret instead (base64 of the JSON file)."
            )
            print(f"Parse error: {e}")
            sys.exit(1)

    print(
        "ERROR: No credentials. Set one of:\n"
        "  FIREBASE_SERVICE_ACCOUNT_PATH\n"
        "  FIREBASE_KEY_BASE64 (recommended for GitHub Actions)\n"
        "  FIREBASE_KEY"
    )
    sys.exit(1)


DEFAULT_DB_URL = (
    "https://gearblast-35ada-default-rtdb.europe-west1.firebasedatabase.app"
)


def init_firebase():
    db_url = os.environ.get("FIREBASE_DB_URL", "").strip() or DEFAULT_DB_URL
    if db_url != DEFAULT_DB_URL and "europe-west1" not in db_url:
        print(f"[warn] FIREBASE_DB_URL region may be wrong (expected europe-west1): {db_url}")

    key_dict = load_service_account_dict()

    try:
        firebase_admin.get_app()
        return
    except ValueError:
        pass

    cred = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(cred, {"databaseURL": db_url})
    print(f"[Firebase] Connected: {db_url}")

def fetch_mode(mode, users_profile):
    ref = db.reference(f"leaderboards/{mode}")
    # Tam çekim: RTDB kurallarında .indexOn eksik olsa bile çalışır.
    # Çok büyük listelerde maliyet artar; LIMIT kadar üst skor burada kesilir.
    raw = ref.get()

    if not raw:
        print(f"[{mode}] No data")
        return []

    if not isinstance(raw, dict):
        print(f"[{mode}] Unexpected payload type: {type(raw).__name__}")
        return []

    entries = []
    skipped_filtered = 0
    for uid, val in raw.items():
        try:
            # Gelen verinin sözlük (dict) olduğundan emin ol
            if not isinstance(val, dict):
                print(f"[{mode}] Skipping invalid entry for {uid}: not a dict")
                continue

            if not passes_global_list_filter(val):
                skipped_filtered += 1
                continue

            profile = users_profile.get(uid, {})
            is_chicken = is_chicken_entry(val) or profile.get("isChicken", False)
            avatar_id = resolve_avatar_id(val, uid, users_profile)
            
            entries.append({
                "uid":           uid,
                "username":      val.get("username", "???"),
                "score":         0 if is_chicken else int(val.get("score", 0)),
                "leagueLevel":   0 if is_chicken else int(val.get("leagueLevel", 0)),
                "playstyleLevel":0 if is_chicken else int(val.get("playstyleLevel", 0)),
                "profileAvatarId": profile.get("avatarId") or 0,
                "avatarId":      avatar_id,
                "timestamp":     entry_timestamp(val),
            })
        except Exception as e:
            print(f"[{mode}] Error parsing entry {uid}: {e}")
            continue

    print(f"[{mode}] Raw entries count: {len(entries)} (skipped filtered: {skipped_filtered})")

    def sort_key(e):
        is_chicken = e.get("avatarId") == 999
        score = 0 if is_chicken else int(e.get("score") or 0)
        ts = e.get("timestamp") or 0
        
        if not is_chicken and score > 0:
            return (0, -score, -ts, e.get("username") or "")
        elif not is_chicken:
            return (1, -ts, e.get("username") or "")
        else:
            return (2, ts, e.get("username") or "")

    entries.sort(key=sort_key)

    if len(entries) > LIMIT:
        entries = entries[:LIMIT]

    # Rank ve Rozet Kuralı ekle
    for i, e in enumerate(entries):
        rank = i + 1
        e["rank"] = rank
        
        # KURAL: Sadece 1. numara şampiyon olabilir.
        # Eğer oyuncu 1. sıradaysa lig seviyesini 5 yap.
        # Eğer 1. sırada değilse ama 5 görünüyorsa onu 4'e (Elmas) çek.
        current_lvl = int(e.get("leagueLevel", 0))
        is_chicken = e.get("avatarId") == 999 or int(e.get("score") or 0) <= 0
        if is_chicken:
            e["leagueLevel"] = 0
            # Tavuk olanlarin rozeti/puanı olmamalı
            e["score"] = 0
        elif rank == 1 and int(e.get("score") or 0) > 0:
            e["leagueLevel"] = 5
        elif current_lvl >= 5:
            e["leagueLevel"] = 4

    print(f"[{mode}] Fetched and cleaned {len(entries)} entries")
    return entries

def main():
    init_firebase()

    output = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_players": 0,
    }

    users_profile = load_users_profile_map()
    total = 0
    for mode in MODES:
        entries = fetch_mode(mode, users_profile)
        output[mode] = entries
        total = max(total, len(entries))

    output["total_players"] = total

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "leaderboard.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[Done] leaderboard.json updated at {output['updated_at']}")

if __name__ == "__main__":
    main()
