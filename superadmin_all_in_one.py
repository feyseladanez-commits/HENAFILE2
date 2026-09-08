import asyncio
import base64
import json
import logging
import os
import re
import secrets
from datetime import datetime

try:
    import aiohttp
    from aiohttp import web
except ModuleNotFoundError:
    print("❌ የሚያስፈልገው ላይብረሪ 'aiohttp' በኮምፒውተርዎ ላይ አልተጫነም።")
    print("👉 pip install aiohttp")
    input("\nEnter ይጫኑ...")
    raise SystemExit(1)

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update, BotCommand
    from telegram.error import InvalidToken
    from telegram.ext import (
        Application, CallbackQueryHandler, CommandHandler, ContextTypes,
        MessageHandler, filters,
    )
except ModuleNotFoundError as e:
    print(f"❌ የሚያስፈልግ ላይብረሪ አልተጫነም፦ {e}")
    print("👉 pip install python-telegram-bot")
    input("\nEnter ይጫኑ...")
    raise SystemExit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("superadmin")

# =============================================================================
# ALL-IN-ONE SUPER ADMIN + MULTI-HOST APP
# =============================================================================
# ይህ ብቸኛው .py ፋይል ነው የሚያስፈልገው። ውስጡ የያዘው፦
#   1) Super Admin ቦት (ክሬዲት ማጽደቅ/ውድቅ + ሆስት መቆጣጠሪያ)
#   2) የ jemo_2.py ሙሉ የሎተሪ ሆስት ኮድ (base64 ተቀርፆ በዚህ ፋይል ውስጥ ተቀብሮ)
#
# አዲስ ሆስት ሲመዘገብ (bot_token + admin_id ብቻ በመጠየቅ) ይህ ስክሪፕት የ jemo_2 ኮዱን
# በራስ-ሰር (dynamic exec) በተለየ Python namespace ውስጥ በተመሳሳይ ፕሮሰስ/event loop
# ውስጥ ያስነሳዋል - የተለየ ፋይል መፍጠር ወይም እጅ ማዋቀር ሳያስፈልግ። ብዙ ሆስቶች ቢመዘገቡም ሁሉም
# በዚህ አንድ ፕሮሰስ ውስጥ (አንድ ጊዜ python superadmin_all_in_one.py ብለው ሲያስነሱ) አብረው ይሮጣሉ።
#
# ማስታወሻ፦ ሁሉም ሆስቶች 1 ፕሮሰስ ውስጥ ስለሚጋሩ፣ አንድ ሆስት ላይ ያልተጠበቀ ስህተት ቢፈጠር
# ሌሎቹን እንዳያቆም እያንዳንዱ ሆስት instance በራሱ try/except ውስጥ ተጠቅልሎ ይነሳል።

SUPER_BOT_TOKEN = os.environ.get("SUPERADMIN_BOT_TOKEN", "8764160656:AAH05PJHq0kKZrUghV5nSrGjyAXK8JTKaJ8")
SUPER_ADMIN_ID = int(os.environ.get("SUPER_ADMIN_ID", "5094744004"))

# --- Credit Seller (Super Admin) payment account --------------------------
# This is where EVERY host sends money when they top up credit via
# /addcredit — it is the Super Admin's own account, the same for all hosts.
# It is completely separate from each host's own PAYMENT_METHODS (which is
# that host's account for receiving payment FROM PLAYERS buying tickets,
# customized per host via /editpayment). Set these via environment
# variables, or edit the defaults below directly.
CREDIT_SELLER_PAYMENT_METHODS = {
    "telebirr": {
        "label": "📱 ቴሌ ብር (TeleBirr)",
        "emoji": "📱",
        "account": os.environ.get("SELLER_TELEBIRR_ACCOUNT", "0963530030"),
        "holder": os.environ.get("SELLER_TELEBIRR_HOLDER", "FEYSELADANE"),
    },
    "cbebirr": {
        "label": "💚 ሲቢኢ ብር (CBE Birr)",
        "emoji": "💚",
        "account": os.environ.get("SELLER_CBEBIRR_ACCOUNT", "0963530030"),
        "holder": os.environ.get("SELLER_CBEBIRR_HOLDER", "FEYSELADANE"),
    },
}
# Methods with no account configured are dropped — hosts only see options
# you've actually filled in, instead of an empty/blank account.
CREDIT_SELLER_PAYMENT_METHODS = {
    k: m for k, m in CREDIT_SELLER_PAYMENT_METHODS.items() if m["account"]
}

API_HOST = os.environ.get("SUPERADMIN_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("SUPERADMIN_API_PORT", "8091"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Where persistent data (state files, per-host folders) actually lives ---
# IMPORTANT: on most "cloud app" / PaaS platforms, redeploying (pushing a
# fresh copy of the code) tears down the old container/filesystem and builds
# a new one from your source. Anything written to plain local disk -- which
# is what happens by default here -- gets wiped along with it, UNLESS it
# lives on a *persistent volume/disk* that the platform explicitly keeps
# across deploys and you've attached to your app.
#
# DATA_DIR defaults to BASE_DIR (old behavior, so nothing breaks if you
# don't set it) -- but that means your data sits inside the same folder
# that gets replaced on every deploy, which is exactly what's wiping it.
#
# Fix: look in your hosting platform's dashboard for something called
# "persistent disk", "volume", "storage", or similar, attach one, note the
# mount path it gives you (e.g. "/data"), and set the environment variable
# DATA_DIR to that path. Everything below (HOSTS_DIR, STATE_FILE, and every
# per-host lottery_state.json under hosts/<HOST_ID>/) will then live there
# instead, and will survive future "replace the whole code" redeploys.
#
# If your platform genuinely has no persistent-storage option at all, plain
# files can never survive a full redeploy there no matter what DATA_DIR is
# set to -- the real fix in that case is moving state into an external
# database instead of local JSON files, which is a bigger change (ask if
# you want that built).
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
HOSTS_DIR = os.path.join(DATA_DIR, "hosts")
STATE_FILE = os.path.join(DATA_DIR, "superadmin_state.json")

os.makedirs(HOSTS_DIR, exist_ok=True)
if DATA_DIR == BASE_DIR:
    print(
        f"⚠️  DATA_DIR is not set -- data is stored inside the app's own code "
        f"folder ({BASE_DIR}), which most cloud platforms replace on every "
        f"redeploy. Set the DATA_DIR environment variable to a persistent "
        f"volume/disk path if your platform offers one, or your saved data "
        f"will keep disappearing on redeploy."
    )
else:
    print(f"📁 Using persistent DATA_DIR: {DATA_DIR}")


# =============================================================================
# Durable storage abstraction (local disk fallback <-> S3-compatible object
# storage)
# =============================================================================
# Set S3_BUCKET (plus the usual AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY /
# AWS_REGION -- and S3_ENDPOINT_URL for non-AWS S3-compatible providers,
# e.g. your host's injected Object Storage credentials) to make every
# save_state()/load_state() call in THIS file, and in every dynamically
# launched jemo_2 host instance, write to durable object storage instead of
# local disk. If S3_BUCKET is not set, everything falls back to local JSON
# files under DATA_DIR exactly as before -- fine for local testing, but
# wiped on redeploy on platforms with an ephemeral filesystem.
S3_BUCKET = os.environ.get("S3_BUCKET") or os.environ.get("AWS_S3_BUCKET")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL") or os.environ.get("AWS_ENDPOINT_URL")
S3_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
S3_PREFIX = os.environ.get("S3_PREFIX", "").strip("/")

_boto3_client = None
if S3_BUCKET:
    try:
        import boto3
        _boto3_client = boto3.client(
            "s3",
            region_name=S3_REGION,
            endpoint_url=S3_ENDPOINT_URL or None,
        )
        print(
            f"☁️  Using S3-compatible durable storage: bucket={S3_BUCKET!r}"
            + (f" endpoint={S3_ENDPOINT_URL!r}" if S3_ENDPOINT_URL else "")
        )
    except ModuleNotFoundError:
        print(
            "⚠️  S3_BUCKET is set but 'boto3' is not installed "
            "(pip install boto3) -- falling back to local disk storage, "
            "which will NOT survive a redeploy."
        )
        _boto3_client = None
    except Exception as e:
        print(f"⚠️  Failed to initialize S3 client ({e}) -- falling back to local disk storage.")
        _boto3_client = None


class PersistentStore:
    """Reads/writes JSON blobs either to S3-compatible object storage (when
    configured above) or to local disk under DATA_DIR (fallback). `prefix`
    is prepended to every key -- e.g. "hosts/H001" for one host's own state
    -- so each host's data lives at its own S3 key / local path and nothing
    collides with another host or with the super admin's own state."""

    def __init__(self, prefix: str = ""):
        self.prefix = prefix.strip("/")

    def _s3_key(self, key: str) -> str:
        parts = [p for p in (S3_PREFIX, self.prefix, key) if p]
        return "/".join(parts)

    def _local_path(self, key: str) -> str:
        path = os.path.join(DATA_DIR, self.prefix, key) if self.prefix else os.path.join(DATA_DIR, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def write_json(self, key: str, data) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        if _boto3_client is not None:
            _boto3_client.put_object(
                Bucket=S3_BUCKET, Key=self._s3_key(key),
                Body=payload.encode("utf-8"), ContentType="application/json",
            )
            return
        path = self._local_path(key)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, path)

    def read_json(self, key: str, default=None):
        if _boto3_client is not None:
            try:
                obj = _boto3_client.get_object(Bucket=S3_BUCKET, Key=self._s3_key(key))
                return json.loads(obj["Body"].read().decode("utf-8"))
            except Exception as e:
                # NoSuchKey (missing object) is expected on first run; any
                # other error just falls back to "no saved state" rather
                # than crashing startup.
                if "NoSuchKey" not in str(e) and "404" not in str(e):
                    log.error(f"S3 read_json({key!r}) error: {e}")
                return default
        path = self._local_path(key)
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


STORE = PersistentStore()  # super admin's own state (superadmin_state.json)


# Per-host port ranges so each dynamically-launched jemo_2 instance gets its
# own Host-Control-API port and its own SMS-webhook port without clashing.
_HOST_API_PORT_BASE = 8082
_WEBHOOK_PORT_BASE = 9001

JEMO_SOURCE_B64 = (
    "aW1wb3J0IGxvZ2dpbmcKaW1wb3J0IHJlCmltcG9ydCBjb3B5CmltcG9ydCBhc3luY2lvCmltcG9ydCBqc29uCmltcG9ydCBvcwpm"
    "cm9tIHR5cGVzIGltcG9ydCBTaW1wbGVOYW1lc3BhY2UKZnJvbSBkYXRldGltZSBpbXBvcnQgZGF0ZXRpbWUsIHRpbWVkZWx0YQoK"
    "dHJ5OgogICAgaW1wb3J0IGFpb2h0dHAKICAgIGZyb20gYWlvaHR0cCBpbXBvcnQgd2ViCmV4Y2VwdCBNb2R1bGVOb3RGb3VuZEVy"
    "cm9yOgogICAgcHJpbnQoIuKdjCDhi6jhiJrhi6vhiLXhjYjhiI3hjIjhi40g4YiL4Yut4Yml4Yio4YiqICdhaW9odHRwJyDhiaDh"
    "iq7hiJ3hjZLhi43hibDhiK3hi44g4YiL4YutIOGKoOGIjeGJsOGMq+GKkOGIneGNoiIpCiAgICBwcmludCgi8J+RiSDhiqXhiaPh"
    "iq3hi44g4Yut4YiF4YqVIOGJteGLleGLm+GLnSDhi6vhiILhi7HhjaYgcGlwIGluc3RhbGwgYWlvaHR0cCIpCiAgICBwcmludCgi"
    "ICAgKOGKq+GIjeGIsOGIq+GNpiBwaXAgaW5zdGFsbCBhaW9odHRwIC0tYnJlYWstc3lzdGVtLXBhY2thZ2VzKSIpCiAgICBpbnB1"
    "dCgiXG7hiJjhiLXhiq7hibHhipUg4YiI4YiY4Yud4YyL4Ym1IEVudGVyIOGLreGMq+GKkS4uLiIpCiAgICByYWlzZSBTeXN0ZW1F"
    "eGl0KDEpCgp0cnk6CiAgICBmcm9tIHRlbGVncmFtIGltcG9ydCAoCiAgICAgICAgSW5saW5lS2V5Ym9hcmRCdXR0b24sIElubGlu"
    "ZUtleWJvYXJkTWFya3VwLCBSZXBseUtleWJvYXJkTWFya3VwLCBVcGRhdGUsCiAgICAgICAgQm90Q29tbWFuZCwgQm90Q29tbWFu"
    "ZFNjb3BlQ2hhdCwgQm90Q29tbWFuZFNjb3BlRGVmYXVsdCwKICAgICAgICBJbnB1dE1lZGlhUGhvdG8sIElucHV0TWVkaWFWaWRl"
    "bywKICAgICkKICAgIGZyb20gdGVsZWdyYW0uZXh0IGltcG9ydCAoCiAgICAgICAgQXBwbGljYXRpb24sCiAgICAgICAgQ2FsbGJh"
    "Y2tRdWVyeUhhbmRsZXIsCiAgICAgICAgQ29tbWFuZEhhbmRsZXIsCiAgICAgICAgQ29udGV4dFR5cGVzLAogICAgICAgIE1lc3Nh"
    "Z2VIYW5kbGVyLAogICAgICAgIGZpbHRlcnMsCiAgICApCmV4Y2VwdCBNb2R1bGVOb3RGb3VuZEVycm9yIGFzIGU6CiAgICBwcmlu"
    "dChmIuKdjCDhi6jhiJrhi6vhiLXhjYjhiI3hjI0g4YiL4Yut4Yml4Yio4YiqIOGKoOGIjeGJsOGMq+GKkOGIneGNpiB7ZX0iKQog"
    "ICAgcHJpbnQoIvCfkYkg4Yql4Ymj4Yqt4YuOIOGLreGIheGKlSDhibXhi5Xhi5vhi50g4Yur4YiC4Yux4Y2mIHBpcCBpbnN0YWxs"
    "IHB5dGhvbi10ZWxlZ3JhbS1ib3QiKQogICAgaW5wdXQoIlxu4YiY4Yi14Yqu4Ymx4YqVIOGIiOGImOGLneGMi+GJtSBFbnRlciDh"
    "i63hjKvhipEuLi4iKQogICAgcmFpc2UgU3lzdGVtRXhpdCgxKQoKIyAtLS0g4Yuo4Yuw4Yio4Yiw4YqdIHNjcmVlbnNob3Qg4YiL"
    "4YutIE9DUiAo4Yuo4Yid4Yi14YiNLeGMveGIgeGNjSDhipXhiaPhiaUpIOGIiOGIm+GLteGIqOGMjSDhi6jhiJrhi6vhiLXhjYjh"
    "iI3hjIkg4YiL4Yut4Yml4Yio4Yiq4YuO4Ym9IC0tLQojIOGKpeGKkOGLmuGIhSDhiqjhiIzhiIkg4Ymm4YmxIOGKoOGLreGLmOGM"
    "i+GInSAtIOGJsOGMq+GLi+GJvuGJvSDhiLXhiq3hiKrhipXhiL7hibUg4Yiy4YiN4YqpIOGMjeGKlSDhiKrhjYjhiKjhipXhiLHh"
    "ipUg4Yir4YixIOGIm+GKleGJoOGJpSDhiqDhi63hib3hiI3hiJ0g4Yql4YqTCiMg4Yql4Ymj4Yqt4YuOIOGIquGNiOGIqOGKleGI"
    "tSDhiYHhjKXhiK0v4Yqk4Yi14Yqk4Yid4Yqk4Yi1IOGJoOGMveGIgeGNjSDhiqXhipXhi7LhiI3hiqkg4Yml4Ym7IOGLreGMoOGL"
    "reGJg+GIjeGNogp0cnk6CiAgICBpbXBvcnQgcHl0ZXNzZXJhY3QKICAgIGZyb20gUElMIGltcG9ydCBJbWFnZQogICAgaW1wb3J0"
    "IGlvIGFzIF9pbwogICAgT0NSX0FWQUlMQUJMRSA9IFRydWUKZXhjZXB0IE1vZHVsZU5vdEZvdW5kRXJyb3I6CiAgICBPQ1JfQVZB"
    "SUxBQkxFID0gRmFsc2UKICAgIHByaW50KCLimqDvuI8gJ3B5dGVzc2VyYWN0Jy8nUGlsbG93JyDhiqDhiI3hibDhjKvhipHhiJ0g"
    "LSDhi6jhi7DhiKjhiLDhip0g4Yi14Yqt4Yiq4YqV4Yi+4Ym1IOGIq+GItS3hiLDhiK0g4YqV4Ymj4YmlIChPQ1IpIOGJsOGIsOGK"
    "k+GKreGIj+GIjeGNoiIpCiAgICBwcmludCgiICAg4YiI4Yib4Yml4Yir4Ym14Y2mIHBpcCBpbnN0YWxsIHB5dGVzc2VyYWN0IFBp"
    "bGxvdyAg4Yql4YqTICBUZXNzZXJhY3QtT0NSIOGKouGKleGIteGJtuGIjSDhi6vhi7XhiK3hjInhjaIiKQoKIyAtLS0g4YuL4YqT"
    "IOGImOGIiOGLq+GLjuGJvSAo4Yql4YqQ4Yua4YiF4YqVIOGJoOGKpeGIreGIteGLjiDhiJjhiKjhjIMg4Yut4Ymw4YqpKSAtLS0K"
    "Qk9UX1RPS0VOID0gX19JTkpFQ1RFRF9fWyJib3RfdG9rZW4iXQpBRE1JTl9JRCA9IF9fSU5KRUNURURfX1siYWRtaW5faWQiXQoK"
    "IyAtLS0gU01TIFdlYmhvb2sg4Yib4YuL4YmA4Yiq4YurIChBdXRvLVZlcmlmeSkgLS0tCldFQkhPT0tfU0VDUkVUID0gIll5X1pu"
    "VHVZandidjZfZGRaNWNqblR5S0tYZWUwM1Y1IgpBTExPV0VEX1NNU19TRU5ERVJTID0gW10KCldFQkhPT0tfSE9TVCA9ICIxMjcu"
    "MC4wLjEiICAjIE5naW54IOGJpeGJuyDhiaDhi43hjK0g4Yi14YiI4Yia4Yur4YyI4YiI4YyN4YiN4Y2jIOGJpuGJsSDhiaDhiKvh"
    "iLEgbG9jYWxob3N0IOGJpeGJuyDhi6vhi7PhiJ3hjKPhiI0KV0VCSE9PS19QT1JUID0gX19JTkpFQ1RFRF9fWyJ3ZWJob29rX3Bv"
    "cnQiXQoKIyAtLS0g4YuN4Yyr4YuKIFNNUyBSZWxheSBXZWJob29rIFVSTCAo4Yqg4Yib4Yir4YytKSAtLS0KIyDhiIbhiLXhibEg"
    "KEFETUlOX0lEKSDhiaAvc2V0c21zd2ViaG9vayDhibXhi5Xhi5vhi50g4Yir4YixIOGLqOGImuGLq+GIteGJgOGIneGMoOGLjSBV"
    "UkwgLSDhi4jhi7Dhi5rhiIUg4Ymm4Ym1IOGIiOGKreGNjeGLqyDhiJvhiKjhjIvhjIjhjKsKIyDhi6jhiJrhi7DhiK3hiLEg4Yqk"
    "4Yi14Yqk4Yid4Yqk4Yi14YuO4Ym9ICjhiaDhibThiIzhjI3hiKvhiJ0g4Y2O4Yit4YuL4Yit4Yu1IOGLiOGLreGInSDhiaAvc21z"
    "LXdlYmhvb2sg4Ymg4Yqp4YiNIOGJouGImOGMoeGInSkg4YuI4Yuy4Yur4YuN4YqRIOGMiOGIjeGJpeGMpiAocmVsYXkpCiMg4Yut"
    "4YiN4Yqr4YiNIC0g4Ymm4YmxIOGIq+GIsSDhiJvhiKjhjIvhjIjhjKHhipUvYXV0by12ZXJpZnkg4Yib4Yu14Yio4YyJ4YqVIOGM"
    "jeGKlSDhiqDhi6vhiLXhibDhjJPhjInhiI3hiJ0gKGZpcmUtYW5kLWZvcmdldCnhjaIKIyBOb25lIOGIm+GIiOGJtSDhiJ3hipXh"
    "iJ0g4YuN4Yyr4YuKIHdlYmhvb2sg4Yqg4YiN4Ymw4YuL4YmA4Yio4YidIOGIm+GIiOGJtSDhipDhi40gKOGKkOGJo+GIqinhjaIK"
    "T1VUQk9VTkRfU01TX1dFQkhPT0tfVVJMID0gTm9uZQoKIyAtLS0gTXVsdGktSG9zdCBDb250cm9sbGVyIEFQSSAtLS0KIyBUaGUg"
    "Y2VudHJhbCBNdWx0aS1Ib3N0IGJvdCBjYWxscyB0aGVzZSBlbmRwb2ludHMgdG8gbW9uaXRvci9jb250cm9sIHRoaXMgaG9zdC4K"
    "IyBLZWVwIHRoaXMgc2VjcmV0IGRpZmZlcmVudCBmcm9tIFdFQkhPT0tfU0VDUkVULgpIT1NUX0FQSV9TRUNSRVQgPSBfX0lOSkVD"
    "VEVEX19bImhvc3RfYXBpX3NlY3JldCJdCkhPU1RfQVBJX0hPU1QgPSBvcy5lbnZpcm9uLmdldCgiSE9TVF9BUElfSE9TVCIsIFdF"
    "QkhPT0tfSE9TVCkKSE9TVF9BUElfUE9SVCA9IF9fSU5KRUNURURfX1siaG9zdF9hcGlfcG9ydCJdCkhPU1RfQ09NTUlTU0lPTl9Q"
    "RVJDRU5UID0gZmxvYXQoX19JTkpFQ1RFRF9fLmdldCgiY29tbWlzc2lvbl9wZXJjZW50IiwgNS4wKSkKCiMgLS0tIOGLqOGKoOGI"
    "uOGKk+GNiiDhiabhibPhi47hib0g4Yml4Yub4Ym1IChXaW5uZXIgcHJpemUgcG9zaXRpb25zKSAtLS0KIyDhi63hiIUg4YuZ4Yit"
    "LeGMiOGIiOGIjeGJsOGKmyAo4YuT4YiI4YidIOGKoOGJgOGNjSDhiIjhi5rhiIUg4YiG4Yi14Ym1KSDhiYXhipXhiaXhiK0g4YqQ"
    "4YuNIC0g4Yi14YqV4Ym1IOGLsOGIqOGMgyAoMeGKmy8y4YqbLzPhipsvNOGKmy4uLikg4Yql4YqV4Yuw4Yia4Y2I4YmA4Yu1IOGL"
    "reGLiOGIteGKk+GIjeGNogojIOGIhuGIteGJsSDhiKvhiLEg4YmgIC93aW5uZXJzbG90cyDhibXhi5Xhi5vhi50g4Yib4YqV4Yqb"
    "4YuN4YidIOGMiuGLnCDhiJjhiYDhi6jhiK0g4Yut4Ym94YiL4YiNICjhipDhiaPhiKogMyAtIOGIm+GIiOGJteGInSDwn6WH8J+l"
    "iPCfpYkp4Y2iCldJTk5FUl9TTE9UUyA9IGludChfX0lOSkVDVEVEX18uZ2V0KCJ3aW5uZXJfc2xvdHMiLCAzKSkKCiMgLS0tIENy"
    "ZWRpdCBTZWxsZXIgQm90IChhcHByb3ZlL3JlamVjdCkgLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tCiMgVGhp"
    "cyBob3N0IGJvdCBpcyBhIGZ1bGx5IGluZGVwZW5kZW50IGRlcGxveW1lbnQg4oCUIGl0cyBvd24gb3BlcmF0b3IKIyAoQURNSU5f"
    "SUQgYmVsb3cpIHJ1bnMgZXZlcnl0aGluZyBhYm91dCBpdCAocm91bmRzLCB0aWNrZXRzLCBwYXltZW50cykuCiMgVGhlIE9OTFkg"
    "dGhpbmcgaXQgbmVlZHMgdGhlIGNlbnRyYWwgY3JlZGl0IHNlbGxlciBib3QgLyBTdXBlciBBZG1pbiBmb3IgaXMKIyBidXlpbmcg"
    "Y3JlZGl0OiB0aGUgIi9hZGRjcmVkaXQgPGFtb3VudD4iIGZsb3cgbm8gbG9uZ2VyIGFkZHMgY3JlZGl0CiMgaW5zdGFudGx5IHdp"
    "dGggemVybyB2ZXJpZmljYXRpb24uIEluc3RlYWQgaXQgZmlsZXMgYSByZXF1ZXN0IHdpdGggdGhlCiMgY3JlZGl0IHNlbGxlciBi"
    "b3QsIHdoaWNoIHBpbmdzIGl0cyBTdXBlciBBZG1pbiB3aXRoIEFwcHJvdmUvUmVqZWN0CiMgYnV0dG9ucyDigJQgb25seSBvbiBB"
    "cHByb3ZlIGRvZXMgY3JlZGl0IGFjdHVhbGx5IGxhbmQgaGVyZSAodmlhIHRoZQojIGV4aXN0aW5nIC9hcGkvYWRkY3JlZGl0IGhv"
    "c3QgQVBJIGJlbG93KS4KIwojIE9uZS10aW1lIHNldHVwIGZvciBhIGJyYW5kIG5ldyBob3N0IGRlcGxveWluZyB0aGlzIGZpbGUg"
    "Zm9yIHRoZW1zZWx2ZXM6CiMgICAxLiBNZXNzYWdlIHRoZSBjcmVkaXQgc2VsbGVyIGJvdCBhbmQgcnVuIC9jb25uZWN0IChubyBh"
    "ZG1pbiBuZWVkZWQg4oCUCiMgICAgICBpdCdzIHNlbGYtc2VydmljZSkuIFBpY2sgeW91ciBvd24gaG9zdCBuYW1lLCB5b3VyIG93"
    "biBBUEkgVVJMCiMgICAgICAod2hlcmUgVEhJUyBib3QncyBob3N0LWNvbnRyb2wgQVBJIGJlbG93IGlzIHJlYWNoYWJsZSksIGFu"
    "ZCB5b3VyCiMgICAgICBvd24gQVBJIHNlY3JldCDigJQgdGhhdCBzZWNyZXQgYmVjb21lcyBIT1NUX0FQSV9TRUNSRVQgYWJvdmUu"
    "CiMgICAyLiAvY29ubmVjdCByZXBsaWVzIHdpdGggeW91ciBhdXRvLWdlbmVyYXRlZCBob3N0X2lkIChlLmcuICJIMDAyIikuCiMg"
    "ICAgICBTZXQgQ1JFRElUX1NFTExFUl9IT1NUX0lEIGJlbG93IHRvIGV4YWN0bHkgdGhhdCB2YWx1ZS4KIyAgIDMuIENSRURJVF9T"
    "RUxMRVJfQVBJX1NFQ1JFVCBtdXN0IGVxdWFsIHRoZSBzYW1lIGFwaV9zZWNyZXQgeW91IHR5cGVkCiMgICAgICBpbnRvIC9jb25u"
    "ZWN0IGluIHN0ZXAgMSAoYnkgZGVmYXVsdCB0aGlzIGlzIGp1c3QgSE9TVF9BUElfU0VDUkVULAojICAgICAgc2luY2UgaXQncyB0"
    "aGUgc2FtZSBzZWNyZXQgZWl0aGVyIHdheSkuCkNSRURJVF9TRUxMRVJfQVBJX1VSTCA9IF9fSU5KRUNURURfX1siY3JlZGl0X3Nl"
    "bGxlcl9hcGlfdXJsIl0KQ1JFRElUX1NFTExFUl9BUElfU0VDUkVUID0gb3MuZW52aXJvbi5nZXQoIkNSRURJVF9TRUxMRVJfQVBJ"
    "X1NFQ1JFVCIsIEhPU1RfQVBJX1NFQ1JFVCkKIyDimqDvuI8gRmlsbCB0aGlzIGluOiB0aGUgaG9zdF9pZCB0aGUgY3JlZGl0IHNl"
    "bGxlciBib3QncyAvY29ubmVjdCBnYXZlIFlPVQojIChzZWUgY3JlZGl0X3NlbGxlci5kYiBgaG9zdHNgIHRhYmxlLCBvciB0aGUg"
    "Y29uZmlybWF0aW9uIG1lc3NhZ2UgL2Nvbm5lY3QKIyBzZW50KS4gTGVmdCBibGFuayBvbiBwdXJwb3NlIOKAlCBhbiB1bmNvbmZp"
    "Z3VyZWQgYm90IHNob3VsZCBmYWlsIGxvdWRseQojICgiQ1JFRElUX1NFTExFUl9IT1NUX0lEIGlzIG5vdCBjb25maWd1cmVkIikg"
    "cmF0aGVyIHRoYW4gc2lsZW50bHkgZmlsZQojIGNyZWRpdCByZXF1ZXN0cyB1bmRlciBhIHBsYWNlaG9sZGVyL3dyb25nIGhvc3Qu"
    "CkNSRURJVF9TRUxMRVJfSE9TVF9JRCA9IF9fSU5KRUNURURfX1siaG9zdF9pZCJdCgojIC0tLSDhi6jhiq3hiKzhi7LhibUg4Yi7"
    "4YytIChTdXBlciBBZG1pbikg4Yuo4Yqt4Y2N4YurIOGKoOGKq+GLjeGKleGJtSAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0t"
    "LS0tLS0tLS0tLQojIOKaoO+4jyDhiqjhiIvhi60g4Yqr4YiI4YuNIFBBWU1FTlRfTUVUSE9EUyAo4Yut4YiFIOGIhuGIteGJtSDh"
    "iqjhibDhjKvhi4vhib7hib0g4YyI4YqV4YuY4YmlIOGIiOGImOGJgOGJoOGIjSDhi6jhiJrhjKDhiYDhiJ3hiaDhibXhjaMg4Ymg"
    "L2VkaXRwYXltZW50CiMg4Yuo4Yia4Yi14Ymw4Yqr4Yqo4YiNIOGLqOGIq+GIsSDhiqDhiqvhi43hipXhibUpIOGMi+GIrSDhjYjh"
    "jL3hiJ4g4Yqg4Yut4Yid4Ymz4YmzIC0g4Yql4YqQ4Yua4YiFIOGIgeGIiOGJtSDhi6jhibDhiIjhi6vhi6kg4Yqg4Yqr4YuN4YqV"
    "4Ym24Ym9IOGKk+GJuOGLjeGNogojIOGLreGIheGKm+GLjSDhi7DhjI3hiJ4g4Yut4YiFIOGIhuGIteGJtSDhiq3hiKzhi7LhibUg"
    "4Yiy4YyI4YubICgvYWRkY3JlZGl0KSDhjIjhipXhi5jhiaHhipUg4YiY4YiL4YqtIOGLq+GIiOGJoOGJteGNoyDhi6ggY3JlZGl0"
    "IHNlbGxlcgojIChTdXBlciBBZG1pbikg4Yir4YixIOGKoOGKq+GLjeGKleGJtSDhipDhi40gLSDhiIjhiIHhiInhiJ0g4YiG4Yi1"
    "4Ym24Ym9IOGKoOGKleGLtSDhiqDhi63hipDhibUg4YiG4YqWIOGKqOGIm+GLleGKqOGIi+GLiuGLjQojIHN1cGVyYWRtaW5fYWxs"
    "X2luX29uZS5weSDhiLXhiq3hiKrhjZXhibUg4Yuo4Yia4YiL4YqtIChpbmplY3RlZCnhjaIKQ1JFRElUX1NFTExFUl9QQVlNRU5U"
    "X01FVEhPRFMgPSBfX0lOSkVDVEVEX18uZ2V0KCJjcmVkaXRfc2VsbGVyX3BheW1lbnRfbWV0aG9kcyIpIG9yIHt9CgojIC0tLSDh"
    "i6jhiIbhiLXhibUg4YiY4YyI4YiI4YyrIOGKpeGKkyDhiq3hiKzhi7LhibUgKEhvc3QgUHJvZmlsZSAmIENyZWRpdCkgLS0tCiMg"
    "4Yut4YiFIOGJpuGJtSDhiKvhiLEg4Yqg4YqV4Yu1ICLhiIbhiLXhibUiIOGIsuGIhuGKleGNoyDhiKvhiLHhipUg4Ym94YiOIOGI"
    "meGIiSDhiaDhiJnhiIkg4Ymg4Yir4YixIOGKpuGNleGIrOGJsOGIrSAoQURNSU5fSUQpIOGLqOGImuGIsOGIqyDhipDhi43hjaIK"
    "IyBIT1NUX0NPTU1JU1NJT05fUEVSQ0VOVCDhiaDhibDhiLjhjKAg4YmB4Yyl4YitIOGKqOGImuGKqOGJsOGIiOGLjSDhiq3hiKzh"
    "i7LhibUg4YiS4Yiz4YmlIOGLreGJgOGKkOGIs+GIjeGNogojIOGIkuGIs+GJoSDhi5zhiK4g4YuI4Yut4YidIOGKqOGLmuGLqyDh"
    "iaDhibPhib0g4Yiy4Yuw4Yit4Yi1IOGIveGLq+GMrSDhiKvhiLUt4Yiw4YitIOGLreGJhuGIneGKkyAoU3VwZXIgQWRtaW4pIOGJ"
    "oOGJtOGIjOGMjeGIq+GInSDhi63hi7Dhi4jhiI3hiIjhibPhiI0gKOGImOGIjeGKpeGKreGJtSDhi63hiIvhiq3hiIjhibPhiI0p"
    "4Y2iCkhPU1RfTkFNRSA9IG9zLmVudmlyb24uZ2V0KCJIT1NUX05BTUUiLCAi4Yut4YiFIOGIhuGIteGJtSIpCkhPU1RfU1RBUlRJ"
    "TkdfQ1JFRElUID0gZmxvYXQob3MuZW52aXJvbi5nZXQoIkhPU1RfU1RBUlRJTkdfQ1JFRElUIiwgIjEwMDAiKSkgICMg4p6cIOGI"
    "iOGLtOGIniAxMDAwIOGJpeGIrQojIFNVUEVSX0FETUlOX0lEOiDhi6jhiq3hiKzhi7LhibUg4Yi74YytIOGJpuGJtSDhiJvhi5Xh"
    "iqjhiIvhi4ogU3VwZXIgQWRtaW4g4Yuo4Ym04YiM4YyN4Yir4YidIElE4Y2iIOGLreGIhSDhiIbhiLXhibUg4Yqt4Yis4Yuy4Ym1"
    "IOGIiOGImOGMjeGLm+GJtQojIOGLqOGImuGMoOGJgOGIneGJoOGJtS/hi6jhiJrhi7Dhi4jhiI3hiIjhibUg4Yiw4YuNIOGKkOGL"
    "jSAo4Yqt4Yis4Yuy4Ym1IOGIsuGLq+GIjeGJhS/hiLLhiJ7hiIsg4Yut4Yuw4YuI4YiN4YiI4Ymz4YiNKeGNoyDhiqXhipMg4Yqo"
    "IEFETUlOX0lEIOGMi+GIrSDhiaDhi5rhiIUg4Ymm4Ym1IOGLjeGIteGMpQojIC9ob3N0cHJvZmlsZSwgL2VkaXRwYXltZW50LCAv"
    "YWRkY3JlZGl0IOGJteGLleGLm+GLnuGJveGKleGInSDhiJjhjKDhiYDhiJ0g4Yut4Ym94YiL4YiN4Y2iClNVUEVSX0FETUlOX0lE"
    "ID0gX19JTkpFQ1RFRF9fWyJzdXBlcl9hZG1pbl9pZCJdCgojIC0tLSDhi6jhiq3hjY3hi6sg4Yqg4Yib4Yir4Yyu4Ym9IChQYXlt"
    "ZW50IE1ldGhvZHMpIC0tLQojIGtleTogaW50ZXJuYWwgaWQsIHVzZWQgZXZlcnl3aGVyZSBhcyB0WyJwYXltZW50X21ldGhvZCJd"
    "CiMgbmFtZTogZGlzcGxheSBuYW1lLCBhY2NvdW50OiBudW1iZXIgc2hvd24gdG8gcGxheWVyLCBob2xkZXI6IGFjY291bnQgaG9s"
    "ZGVyIG5hbWUKIyBsb2dvX2ZpbGU6IG9wdGlvbmFsIGxvY2FsIFBORy9KUEcgcGF0aCAocmVsYXRpdmUgdG8gdGhpcyBzY3JpcHQp"
    "IC0gaWYgbWlzc2luZywgZmFsbHMgYmFjayB0byBlbW9qaSBvbmx5CiMgc2VuZGVyX2tleXdvcmRzOiB3b3Jkcy9zZW5kZXJzIHVz"
    "ZWQgdG8gZGV0ZWN0IHdoaWNoIHByb3ZpZGVyIGFuIGluY29taW5nIGZvcndhcmRlZCBTTVMgaXMgZnJvbQojIHJlZl9wYXR0ZXJu"
    "OiByZWdleCBhIHZhbGlkIHJlZmVyZW5jZSBudW1iZXIgZm9yIHRoaXMgcHJvdmlkZXIgbXVzdCBmdWxseSBtYXRjaCAoYWZ0ZXIg"
    "c3RyaXBwaW5nIHNwYWNlcykKIyByZWZfaGludDogZXhhbXBsZSBzaG93biB0byB0aGUgcGxheWVyIHdoZW4gdGhlaXIgcmVmIGZv"
    "cm1hdCBpcyByZWplY3RlZAojCiMg4Yib4Yi14Ymz4YuI4Yi74Y2mIENCRSAo4Yuo4YqV4YyN4Yu1IOGJo+GKleGKrSDhibXhiKvh"
    "ipXhiLXhjYjhiK0pIOGKqOGIsuGIteGJsOGImSDhiJnhiIkg4Ymg4YiZ4YiJIOGJsOGLiOGMjeGLt+GIjSAtIOGLqOGJo+GKleGK"
    "rSDhibXhiKvhipXhiLXhjYjhiK0g4YyN4YiN4Yy9IOGLqCByZWZlcmVuY2UvdHJhbnNhY3Rpb24gSUQKIyDhiLXhiIjhiJvhi63h"
    "iLDhjKUg4Yir4Yi1LeGIsOGIrSDhiJvhiKjhjIvhjIjhjKsgKGF1dG8tdmVyaWZ5KSDhiIrhiLDhiKvhiIjhibUg4Yi14YiI4Yib"
    "4Yut4Ym94YiN4Y2iClBBWU1FTlRfTUVUSE9EUyA9IHsKICAgICJ0ZWxlYmlyciI6IHsKICAgICAgICAibGFiZWwiOiAi8J+TsSDh"
    "ibThiIwg4Yml4YitIChUZWxlQmlycikiLAogICAgICAgICJlbW9qaSI6ICLwn5OxIiwKICAgICAgICAiYWNjb3VudCI6ICIwOTYz"
    "NTMwMDMwIiwKICAgICAgICAiaG9sZGVyIjogIkZleXNlbCBBZGFuZSIsCiAgICAgICAgImxvZ29fZmlsZSI6ICJsb2dvcy90ZWxl"
    "Ymlyci5wbmciLAogICAgICAgICJzZW5kZXJfa2V5d29yZHMiOiAoInRlbGViaXJyIiwgInRlbGUgYmlyciIsICJldGhpbyB0ZWxl"
    "Y29tIiwgIuGJtOGIjOGJpeGIrSIsICLhiqLhibXhi64g4Ym04YiM4Yqu4YidIiksCiAgICAgICAgInJlZl9wYXR0ZXJuIjogcmUu"
    "Y29tcGlsZShyJ15bQS1aXVtBLVowLTldezcsMTN9JCcsIHJlLklHTk9SRUNBU0UpLAogICAgICAgICJyZWZfaGludCI6ICLhiIjh"
    "iJ3hiLPhiIzhjaYgREhNODJPUkRWRyAo4YuowqvhiqXhipXhiYXhiLXhiYPhiLQg4YmB4Yyl4YitwrsgLSDhjYrhi7DhiIvhibUr"
    "4YmB4Yyl4Yiu4Ym9IOGLteGJpeGIjeGJheGNoyDhiaXhi5nhi43hipUg4YyK4YucIDEwIOGJgeGIneGNiikiLAogICAgfSwKICAg"
    "ICJjYmViaXJyIjogewogICAgICAgICJsYWJlbCI6ICLwn5KaIOGIsuGJouGKoiDhiaXhiK0gKENCRSBCaXJyKSIsCiAgICAgICAg"
    "ImVtb2ppIjogIvCfkpoiLAogICAgICAgICJhY2NvdW50IjogIjA5NjM1MzAwMzAiLAogICAgICAgICJob2xkZXIiOiAiRmV5c2Vs"
    "IEFkYW5lIiwKICAgICAgICAibG9nb19maWxlIjogImxvZ29zL2NiZWJpcnIucG5nIiwKICAgICAgICAic2VuZGVyX2tleXdvcmRz"
    "IjogKCJjYmViaXJyIiwgImNiZSBiaXJyIiksCiAgICAgICAgInJlZl9wYXR0ZXJuIjogcmUuY29tcGlsZShyJ15bQS1aXVtBLVow"
    "LTldezcsMTN9JCcsIHJlLklHTk9SRUNBU0UpLAogICAgICAgICJyZWZfaGludCI6ICLhiIjhiJ3hiLPhiIzhjaYgREhOMTFNMlBB"
    "MUYgKOGLqMKrVHhuIElEwrsgLSDhjYrhi7DhiIvhibUr4YmB4Yyl4Yiu4Ym9IOGLteGJpeGIjeGJheGNoyDhiaXhi5nhi43hipUg"
    "4YyK4YucIDExIOGJgeGIneGNiikiLAogICAgfSwKICAgICJtYW51YWxfY2FsbCI6IHsKICAgICAgICAibGFiZWwiOiAi8J+TniDh"
    "i7Dhi4nhiIjhi4kg4Yur4Yiy4YuZIiwKICAgICAgICAiZW1vamkiOiAi8J+TniIsCiAgICAgICAgImFjY291bnQiOiAiMDk2MzUz"
    "MDAzMCIsCiAgICAgICAgImhvbGRlciI6ICJGZXlzZWwgQWRhbmUiLAogICAgICAgICJsb2dvX2ZpbGUiOiAiIiwKICAgICAgICAi"
    "c2VuZGVyX2tleXdvcmRzIjogKCksCiAgICAgICAgInJlZl9wYXR0ZXJuIjogTm9uZSwKICAgICAgICAicmVmX2hpbnQiOiAiIiwK"
    "ICAgIH0sCn0KTUFYX1JFRl9BVFRFTVBUUyA9IDMgICMg4Ymw4Yyr4YuL4Ym9IOGJteGKreGKreGIiOGKmyDhi6vhiI3hiIbhipAv"
    "4Yur4YiN4YyI4Yyj4Yyg4YiYIOGIquGNiOGIqOGKleGItSDhiaLhiI3hiq0g4Yuo4Yia4Y2I4YmA4Yu14YiI4Ym1IOGKqOGNjeGJ"
    "sOGKmyDhiJnhiqjhiKsg4Yml4Yub4Ym1CgojIOGKqOGIi+GLrSDhi6vhiIjhi40gUEFZTUVOVF9NRVRIT0RTIOGMiOGKkyDhiJ3h"
    "ipXhiJ0gL2VkaXRwYXltZW50IOGLiOGLreGInSBsb2FkX3N0YXRlKCkg4Yqo4YiY4YmA4Yuo4YipIOGJoOGNiuGJtSDhi6vhiIjh"
    "i40g4Yqm4Yiq4YyF4YqT4YiNIChmYWN0b3J5CiMgZGVmYXVsdCkg4YmF4YyCIC0gL3Jlc2V0ZmFjdG9yeSDhjKXhiYXhiJ0g4YiL"
    "4YutIOGIsuGLjeGIjSBwYXltZW50IGFjY291bnQvaG9sZGVyIOGLiOGLsOGLmuGIhSDhiqbhiKrhjIXhipPhiI0g4Yut4YiY4YiI"
    "4Yiz4YiN4Y2iCl9GQUNUT1JZX0RFRkFVTFRfUEFZTUVOVF9NRVRIT0RTID0gY29weS5kZWVwY29weShQQVlNRU5UX01FVEhPRFMp"
    "CgojIC0tLSDhibLhiqzhibUg4YmG4Yut4YmzIOGMiuGLnCAoVGlja2V0IGhvbGQgZHVyYXRpb24pIC0tLQojIOGJsOGMq+GLi+GJ"
    "uSDhiYHhjKXhiK0g4Yqo4YiY4Yio4YygIOGJoOGKi+GIiyDhiq3hjY3hi6sg4Y2I4Yy94YieIOGIquGNiOGIqOGKleGIseGKlSDh"
    "iqXhiLXhiqrhiI3hiq0g4Yu14Yio4Yi1IOGLq+GIiOGLjSDhjIrhi5zhjaIKIyDhiq3hjY3hi6vhi40g4Y2I4Yyj4YqVIOGJouGI"
    "huGKleGInSDhiqXhipXhirMg4Ymw4Yyr4YuL4Ym5IOGLqOGJo+GKleGKrSDhiqThiLXhiqThiJ3hiqThiLUg4YiL4YutIOGLq+GI"
    "iOGLjeGKlSDhiKrhjYjhiKjhipXhiLUg4Y2I4YiN4YyOIOGMiOGIjeGJpeGMpiDhiJjhiIvhiq0g4YyK4YucIOGIteGIiOGImuGL"
    "iOGIteGLteGJoOGJtQojIOGLreGIhSDhiqg1IOGLsOGJguGJgyDhi4jhi7AgMTUg4Yuw4YmC4YmDIOGJsOGIq+GLneGIn+GIjeGN"
    "ogpUSUNLRVRfSE9MRF9NSU5VVEVTID0gNQoKIyAtLS0g4Ymg4Yit4Yqr4YmzIOGLmeGIruGJvSDhiaDhiqDhipXhi7Ug4YiL4Yut"
    "IChNdWx0aXBsZSBTaW11bHRhbmVvdXMgUm91bmRzKSAtLS0KIyByb3VuZHM6IHJvdW5kX2lkIChpbnQpIC0+IHsKIyAgICAgIm5h"
    "bWUiOiBzdHIsICJkZXNjcmlwdGlvbiI6IHN0ciwgImltYWdlX2ZpbGVfaWQiOiBzdHJ8Tm9uZSwKIyAgICAgIm51bV90aWNrZXRz"
    "IjogaW50LCAicHJpY2UiOiBmbG9hdCwgInN0YXR1cyI6ICJPUEVOIiB8ICJQQVVTRUQiIHwgIkNMT1NFRCIsCiMgICAgICJ0aWNr"
    "ZXRzIjoge3RpY2tldF9udW06IHsuLi59fQojIH0Kcm91bmRzID0ge30KbmV4dF9yb3VuZF9pZCA9IDEKCiMgR2xvYmFsIGhvc3Qt"
    "bGV2ZWwgcGF1c2UgY29udHJvbGxlZCBieSB0aGUgTXVsdGktSG9zdCBDb250cm9sbGVyLgojIFJvdW5kIHN0YXR1c2VzIGFyZSBw"
    "cmVzZXJ2ZWQgd2hpbGUgdGhpcyBpcyBUcnVlLgpob3N0X3BhdXNlZCA9IEZhbHNlCiMg4YiI4Yid4YqVIGhvc3RfcGF1c2VkPVRy"
    "dWUg4Yql4YqV4Yuw4YiG4YqQIOGLqOGImuGLq+GImOGIiOGKreGJteGNpiAibWFudWFsIiAoTXVsdGktSG9zdCBjb250cm9sbGVy"
    "LyAvcGF1c2Ug4Ymg4Yql4YyFIOGKq+GJhuGImCkg4YuI4Yut4YidCiMgImNyZWRpdCIgKOGKreGIrOGLsuGJtSDhiLXhiIvhiIjh"
    "iYAg4Yir4Yi1LeGIsOGIrSDhiqjhiYbhiJgpIC0g4Yqt4Yis4Yuy4Ym1IOGIsuGInuGIiyAiY3JlZGl0IiDhiqjhiIbhipAg4Yml"
    "4Ym7IOGIq+GItS3hiLDhiK0g4Yql4YqV4Yuw4YyI4YqTIOGLreGMgOGIneGIq+GIjeGNogpob3N0X3BhdXNlZF9yZWFzb24gPSBO"
    "b25lCgojIC0tLSDhi6jhiIbhiLXhibUg4Yqt4Yis4Yuy4Ym1IOGIkuGIs+GJpSAoSG9zdCBDcmVkaXQgQmFsYW5jZSkgLS0tCiMg"
    "YmFsYW5jZTog4Yuo4YmA4YioIOGKreGIrOGLsuGJtSAo4Yml4YitKSAtIOGJoOGKpeGLq+GKleGLs+GKleGLsSDhibLhiqzhibUg"
    "4Yi94Yur4YytIOGJoOGKruGImuGIveGKlSDhiJjhjKDhipUg4Yut4YmA4YqQ4Yiz4YiNCiMgdG90YWxfZGVkdWN0ZWQ6IOGKpeGI"
    "teGKq+GIgeGKlSDhi6jhibDhiYDhipDhiLAg4Yyg4YmF4YiL4YiLIOGKruGImuGIveGKlSAo4YiI4YiY4Yio4YyDL+GJs+GIquGK"
    "rSDhiaXhibspCiMgbG93X2NyZWRpdF9ub3RpZmllZDog4YiS4Yiz4YmhIOGKq+GIiOGJgCDhiaDhiovhiIsgU3VwZXIgQWRtaW4g"
    "4Yuw4YyL4YyN4YieIOGKpeGKleGLs+GLreGLsOGLiOGIjeGJoOGJtSAo4Yqg4YqV4Yu0IOGJpeGJuyDhi63hi7Dhi4jhiIvhiI3h"
    "jaMg4Yqt4Yis4Yuy4Ym1IOGIsuGInuGIiyDhi63hjLjhi7PhiI0pCmhvc3RfY3JlZGl0ID0geyJiYWxhbmNlIjogSE9TVF9TVEFS"
    "VElOR19DUkVESVQsICJ0b3RhbF9kZWR1Y3RlZCI6IDAuMCwgImxvd19jcmVkaXRfbm90aWZpZWQiOiBGYWxzZX0KCiMgdXNlcl9p"
    "ZCAtPiB7InJvdW5kX2lkIjogaW50LCAidGlja2V0cyI6IFt0aWNrZXRfbnVtLCAuLi5dfSAgKOGKoOGKleGLtSDhibDhjKvhi4vh"
    "ib0g4Ymg4Yqg4YqV4Yu1IOGMiuGLnCDhiqjhiqDhipXhi7Ug4Ymg4YiL4YutIOGJgeGMpeGIrSDhiJjhi6vhi50g4Yut4Ym94YiL"
    "4YiN4Y2jCiMg4YqQ4YyI4YitIOGMjeGKlSDhiIHhiInhiJ0g4YmB4Yyl4Yiu4Ym9IOGIiOGKoOGKleGLtSDhi5nhiK0g4Yml4Ym7"
    "IOGIhuGKkOGLjSDhiaDhiqDhipXhi7Ug4YiL4YutIOGLreGKqOGNiOGIi+GIiS/hi63hiKjhjIvhjIjhjKPhiIkpCnVzZXJfc2Vs"
    "ZWN0aW9ucyA9IHt9CgojIOGJsOGMq+GLi+GJuSDhjIjhipMgIuGKreGNjeGLqyDhiYDhjKXhiI0iIChjaGVja291dCkg4Yiz4Yut"
    "4Yyr4YqVIOGKqOGImOGIqOGMo+GJuOGLjSAo4YyI4YqTIOGLq+GIjeGJsOGJhuGIiOGNiSkg4YmB4Yyl4Yiu4Ym9IC0gdXNlcl9p"
    "ZCAtPiB7InJvdW5kX2lkIjogaW50LCAidGlja2V0cyI6IHNldCgpfQp1c2VyX2NhcnRzID0ge30KCiMg4YmA4Yuw4YidIOGJpeGI"
    "iOGLjSDhjKXhiYXhiJ0g4YiL4YutIOGLqOGLi+GIiSDhiKrhjYjhiKjhipXhiLbhib3hipUg4YiI4YiY4Yqo4Ymz4Ymw4YiNICjh"
    "ibDhiJjhiLPhiLPhi60gU01TIOGLsOGMi+GMjeGIniDhjKXhiYXhiJ0g4YiL4YutIOGKpeGKleGLs+GLreGLjeGIjSkKIyBub3Jt"
    "X3JlZiAoc3RyKSAtPiB7InJhd19yZWYiLCAicm91bmRfaWQiLCAidGlja2V0X251bSIsICJ1c2VyX2lkIiwgImJ1eWVyX25hbWUi"
    "LCAidXNlZF9hdCJ9CnVzZWRfc21zX3JlZnMgPSB7fQoKIyAtLS0g4YyI4YqTIOGKq+GIjeGJsOGImOGLmOGMiOGJoCDhibDhjKvh"
    "i4vhib0g4Yiq4Y2I4Yio4YqV4Yi1IOGMi+GIrSDhi6vhiI3hjIjhjKPhjKDhiJggU01T4YuO4Ym94YqVIOGIiOGMiuGLnOGLjSDh"
    "i6jhiJ3hipPhiLXhiYDhiJ3hjKXhiaDhibUgKFJhY2UtY29uZGl0aW9uIOGIm+GIteGJsOGKq+GKqOGLqykgLS0tCnVubWF0Y2hl"
    "ZF9zbXNfbG9nID0gW10gICAgICAgICAgIyBbeyJ0ZXh0Iiwic2VuZGVyIiwic291cmNlIiwicmVmX2NhbmRpZGF0ZXMiLCJhbW91"
    "bnQiLCJyZWNlaXZlZF9hdCJ9LCAuLi5dClVOTUFUQ0hFRF9TTVNfVFRMX01JTlVURVMgPSAxNSAgIyDhiqgxMCDhi7DhiYLhiYMg"
    "4Yuo4Ymy4Yqs4Ym1IOGJhuGLreGJsyDhibXhipXhiL0g4Yio4YuY4YidIOGLq+GIiCDhjIrhi5wgLSDhiJvhiK3hjIMg4YyK4Yuc"
    "IOGIiOGImOGIteGMoOGJtQoKIyAtLS0gL2FkZGNyZWRpdCDhi7DhiKjhjIMt4Ymg4Yuw4Yio4YyDIOGIguGLsOGJtSAoSG9zdCBj"
    "cmVkaXQgdG9wLXVwIHdpemFyZCkgLS0tCiMgYWRtaW5fdWlkIC0+IHsic3RlcCI6ICJhbW91bnQifCJtZXRob2QifCJyZWZlcmVu"
    "Y2UiLCAiYW1vdW50IjogZmxvYXQsICJtZXRob2QiOiBzdHJ8Tm9uZX0KY3JlZGl0X3RvcHVwX3N0YXRlID0ge30KIyDhjIjhipMg"
    "4Yib4Yio4YyL4YyI4YyrICjhiaPhipXhiq0v4Ym04YiM4Yml4YitIFNNUykg4Yuo4Yia4Yyg4Yml4YmBIOGLqOGKreGIrOGLsuGJ"
    "tSDhiJjhiJnhi6sg4Yyl4Yur4YmE4YuO4Ym9IC0g4YiN4YqtIOGKpeGKleGLsCBQRU5ESU5HIOGJsuGKrOGJtSDhjI3hipUg4YiI"
    "4YuZ4YitL+GJgeGMpeGIrSDhi6vhiI3hibDhiYbhiKvhipkKIyBub3JtX3JlZiAoc3RyKSAtPiB7InJhd19yZWYiLCAiYW1vdW50"
    "IiwgIm1ldGhvZCIsICJjcmVhdGVkX2F0In0KcGVuZGluZ19jcmVkaXRfdG9wdXBzID0ge30KCiMgLS0tIOGJsOGMq+GLi+GJvSDh"
    "i6jhiIvhiqjhi40g4Yiq4Y2I4Yio4YqV4Yi1IOGMiOGMo+GMo+GImiBTTVMg4YyI4YqTIOGIteGIi+GIjeGLsOGIqOGIsCBhdXRv"
    "LXJlamVjdCDhi6jhibDhi7DhiKjhjIjhiaPhibjhi40g4Ym14YuV4Yub4Yue4Ym9IC0gL3JlamVjdGVkcmVmcwojIOGJteGLleGL"
    "m+GLnSDhi43hiLXhjKUg4YiI4Yqg4Yu14Yia4YqRIOGJoOGKpeGMhSDhiIjhiJvhjL3hi7DhiYUgKEFwcHJvdmUpIOGJgeGIjeGN"
    "jSDhjIvhiK0g4Ymw4Yur4Yut4YueIOGLqOGImuGJs+GLqeGJoOGJteGNogojIGtleTogZiJ7cm91bmRfaWR9X3t0aWNrZXQxfV97"
    "dGlja2V0Mn0uLi4iIC0+IHsicm91bmRfaWQiLCJ0aWNrZXRzIiwidXNlcl9pZCIsInJlZiIsInJlamVjdGVkX2F0In0KIyDhibXh"
    "i5Xhi5vhi5kg4Yiy4Yy44Yu14YmFL+GIsuGLiOGLteGJhS/hibLhiqzhibEg4Yiy4YiI4YmA4YmFIOGKqOGLmuGIhSDhi43hiLXh"
    "jKUg4Ymg4Yir4Yi1LeGIsOGIrSDhi63hjKDhjYvhiI0gKOGKqOGLmuGIhSDhiaDhibPhib0gX2NsZWFyX3JlamVjdGVkX3JlZl9l"
    "bnRyaWVzKCkg4Yut4YiY4YiN4Yqo4YmxKeGNogpyZWplY3RlZF9yZWZzID0ge30KCiMgLS0tIC9lZGl0cGF5bWVudCDhi7DhiKjh"
    "jIMt4Ymg4Yuw4Yio4YyDIOGIguGLsOGJtSAoUGF5bWVudCBhY2NvdW50IGVkaXQgd2l6YXJkKSAtLS0KIyBhZG1pbl91aWQgLT4g"
    "eyJtZXRob2QiOiAidGVsZWJpcnIifCJjYmViaXJyIiwgInN0ZXAiOiAiYWNjb3VudCJ8ImhvbGRlciIsICJuZXdfYWNjb3VudCI6"
    "IHN0cnxOb25lfQplZGl0X3BheW1lbnRfc3RhdGUgPSB7fQoKIyAtLS0gL3NldHNtc3dlYmhvb2sg4YiC4Yuw4Ym1ICjhi43hjKvh"
    "i4ogU01TIFJlbGF5IFdlYmhvb2sgVVJMIOGIm+GLi+GJgOGIquGLqykgLS0tCnNldF9zbXNfd2ViaG9va19zdGF0ZSA9IHNldCgp"
    "ICAjIGFkbWluX2lkIHNldDogY3VycmVudGx5IGF3YWl0aW5nIGEgVVJMIChvciAib2ZmIi8icmVtb3ZlIikgZm9yIC9zZXRzbXN3"
    "ZWJob29rCgpkZWYgX3JlamVjdGVkX3JlZl9rZXkocm91bmRfaWQsIHRpY2tldHMpOgogICAgcmV0dXJuIGYie3JvdW5kX2lkfV8i"
    "ICsgIl8iLmpvaW4oc3RyKHgpIGZvciB4IGluIHRpY2tldHMpCgpkZWYgX2NsZWFyX3JlamVjdGVkX3JlZl9lbnRyaWVzKHJvdW5k"
    "X2lkLCB0aWNrZXRfbnVtcyk6CiAgICAiIiLhiqjhibDhiLDhjKHhibUg4Ymy4Yqs4Ym1KOGJtuGJvSkg4YyL4YitIOGLqOGJsOGL"
    "q+GLq+GLmSByZWplY3RlZF9yZWZzIGVudHJpZXMg4YiB4YiJIOGLqOGImuGLq+GMoOGNiyBoZWxwZXIgLSDhibLhiqzhibEg4Yiy"
    "4Yy44Yu14YmFL+GLjeGLteGJhSDhiLLhi7DhiKjhjI0v4Yiy4YiI4YmA4YmFIOGLreGMoOGIq+GIjSIiIgogICAgaWYgaXNpbnN0"
    "YW5jZSh0aWNrZXRfbnVtcywgKGxpc3QsIHR1cGxlLCBzZXQpKToKICAgICAgICB0aWNrZXRfc2V0ID0gc2V0KHRpY2tldF9udW1z"
    "KQogICAgZWxzZToKICAgICAgICB0aWNrZXRfc2V0ID0ge3RpY2tldF9udW1zfQogICAgZm9yIGtleSBpbiBbayBmb3IgaywgZSBp"
    "biByZWplY3RlZF9yZWZzLml0ZW1zKCkgaWYgZVsicm91bmRfaWQiXSA9PSByb3VuZF9pZCBhbmQgc2V0KGVbInRpY2tldHMiXSkg"
    "JiB0aWNrZXRfc2V0XToKICAgICAgICByZWplY3RlZF9yZWZzLnBvcChrZXksIE5vbmUpCgpkZWYgX2VtcHR5X3RpY2tldCgpOgog"
    "ICAgcmV0dXJuIHsKICAgICAgICAic3RhdHVzIjogIkFWQUlMQUJMRSIsICJ1c2VyX2lkIjogTm9uZSwgInVzZXJuYW1lIjogTm9u"
    "ZSwgInJlZiI6IE5vbmUsCiAgICAgICAgImV4cGlyZXNfYXQiOiBOb25lLCAiYnV5ZXJfbmFtZSI6IE5vbmUsICJidXllcl9waG9u"
    "ZSI6IE5vbmUsCiAgICAgICAgInBheW1lbnRfbWV0aG9kIjogTm9uZSwgInJlZl9hdHRlbXB0cyI6IDAsICJyZWNlaXB0X2ZpbGVf"
    "aWQiOiBOb25lLCAicmVjZWlwdF9yZWNlaXZlZCI6IEZhbHNlLAogICAgfQoKZGVmIGJ1aWxkX3RpY2tldHMobik6CiAgICAiIiLh"
    "iqgxIOGKpeGIteGKqCBuIOGLq+GIieGJteGKlSDhibLhiqzhibbhib0g4YmgIEFWQUlMQUJMRSDhiIHhipThibMg4Yuo4Yia4Y2I"
    "4Yyl4YitIGhlbHBlciIiIgogICAgcmV0dXJuIHtpOiBfZW1wdHlfdGlja2V0KCkgZm9yIGkgaW4gcmFuZ2UoMSwgbiArIDEpfQoK"
    "IyAtLS0g4YuoL25ld3JvdW5kIOGLjeGLreGLreGJtSDhiIHhipThibMgKOGIteGInS/hibLhiqzhibUv4YuL4YyLL+GImOGMjeGI"
    "iOGMqy/hiL3hiI3hiJvhibbhib0v4Yid4Yi14YiNIOGIiOGImOGMoOGLqOGJhSAtIOGLq+GIiCBhcmdzIOGKqOGJsOGMoOGJgOGI"
    "mSkgLS0tCiMg4Yuw4Yio4YyD4YuO4Ym94Y2mIGF3YWl0aW5nX25hbWUgLT4gYXdhaXRpbmdfY291bnQgLT4gYXdhaXRpbmdfcHJp"
    "Y2UgLT4gYXdhaXRpbmdfZGVzY3JpcHRpb24gLT4gYXdhaXRpbmdfcHJpemVzIC0+IGF3YWl0aW5nX2ltYWdlCiMgTkVXUk9VTkRf"
    "U1RFUFMg4Yuo4Yuw4Yio4YyD4YuO4Ym54YqVIOGJheGLsOGInSDhibDhiqjhibDhiI0g4Yut4Yut4Yub4YiNIC0gwqvirIXvuI8g"
    "4Ymw4YiY4YiI4Yi1wrsgKEJhY2svVW5kbyBvbmUgc3RlcCkg4Yqo4Yqg4YqV4YuxIOGLsOGIqOGMgyDhi4jhi7Ag4YmA4Yuw4YiY"
    "4YuNCiMg4Yuw4Yio4YyDIOGJpeGJuyDhiIjhiJjhiJjhiIjhiLUg4Yut4YiF4YqV4YqRIOGJheGLsOGInSDhibDhiqjhibDhiI0g"
    "4Yut4Yyg4YmA4Yib4YiNICjhiqDhjKDhiYPhiIvhi60gL25ld3JvdW5kIOGIguGLsOGJseGKlSDhjI3hipUg4Yqg4Yur4YmL4Yit"
    "4Yyl4YidIC0g4YurIC9jYW5jZWwg4Yml4Ym7IOGKkOGLjSnhjaIKTkVXUk9VTkRfU1RFUFMgPSBbCiAgICAiYXdhaXRpbmdfbmFt"
    "ZSIsICJhd2FpdGluZ19jb3VudCIsICJhd2FpdGluZ19wcmljZSIsCiAgICAiYXdhaXRpbmdfZGVzY3JpcHRpb24iLCAiYXdhaXRp"
    "bmdfcHJpemVzIiwgImF3YWl0aW5nX2ltYWdlIiwKXQpuZXdyb3VuZF9zdGF0ZSA9IHt9ICAjIGFkbWluX2lkIC0+IGN1cnJlbnQg"
    "c3RlcCBuYW1lCm5ld3JvdW5kX3RlbXAgPSB7fSAgICMgYWRtaW5faWQgLT4geyJuYW1lIjouLi4sICJjb3VudCI6Li4uLCAicHJp"
    "Y2UiOi4uLiwgImRlc2NyaXB0aW9uIjouLi4sICJwcml6ZXMiOlsuLi5dLCAiaW1hZ2VfZmlsZV9pZCI6Li4ufQpwZW5kaW5nX3Jv"
    "dW5kX2NvbmZpcm0gPSB7fSAgIyBhZG1pbl9pZCAtPiB7ImNvdW50IjouLi4sICJwcmljZSI6Li4uLCAibmFtZSI6Li4uLCAiZGVz"
    "Y3JpcHRpb24iOi4uLiwgInByaXplcyI6Wy4uLl0sICJpbWFnZV9maWxlX2lkIjouLi59CgojIC0tLSDhi5nhiK0g4YiI4Yib4Yyl"
    "4Y2L4Ym1IOGIm+GIqOGMi+GMiOGMqyDhi6jhiJrhjKDhiaXhiYEgKERlbGV0ZSBjb25maXJtYXRpb24pIC0tLQpwZW5kaW5nX2Rl"
    "bGV0ZV9jb25maXJtID0ge30gICMgYWRtaW5faWQgLT4gcm91bmRfaWQKCiMgLS0tIOGLqC9tYW51YWxzZWxsIOGLjeGLreGLreGJ"
    "tSDhiIHhipThibMgKOGLmeGIrSvhiYHhjKXhiK0o4Ym24Ym9KSDhiaDhiYHhiI3hjY0g4Yqo4Ymw4YiY4Yio4YygIOGJoOGKi+GI"
    "iyDhiLXhiJ0v4Yi14YiN4YqtIOGJoOGMveGIgeGNjSDhiIjhiJjhjKDhi6jhiYUpIC0tLQptYW51YWxzZWxsX3N0YXRlID0ge30g"
    "ICMgYWRtaW5faWQgLT4geyJyb3VuZF9pZCI6IGludCwgInRpY2tldHMiOiBbaW50LCAuLi5dLCAic3RlcCI6IHN0cn0KCiMgLS0t"
    "IC9tYW51YWxzZWxsIOGMjeGIquGLtSDhiIvhi60g4Yqg4Yu14Yia4YqRIOGKpeGLqOGImOGIqOGMoCDhi6vhiIjhi40gKOGMiOGK"
    "kyAi4YmA4Yyl4YiNIiDhi6vhiI3hibDhjKvhipApIOGJpeGLmSDhiYHhjKXhiK7hib0g4Yid4Yit4YyrIC0tLQptYW51YWxzZWxs"
    "X2NhcnQgPSB7fSAgIyBhZG1pbl9pZCAtPiB7InJvdW5kX2lkIjogaW50LCAidGlja2V0cyI6IHNldCgpfQoKIyAtLS0g4YuoL3Nl"
    "dHdpbm5lciDhi7DhiKjhjIMt4Ymg4Yuw4Yio4YyDIOGLjeGLreGLreGJtSAo4YuZ4YitK+GJgeGMpeGIrSDhiqjhibDhiJjhiKjh"
    "jKAg4Ymg4YqL4YiLIOGIveGIjeGIm+GJtSDhjL3hiIHhjY0g4YiI4YiY4Yyg4Yuo4YmFKSAtLS0Kc2V0d2lubmVyX3N0YXRlID0g"
    "e30gICMgYWRtaW5faWQgLT4gImF3YWl0aW5nX3ByaXplIgpzZXR3aW5uZXJfdGVtcCA9IHt9ICAgIyBhZG1pbl9pZCAtPiB7InJv"
    "dW5kX2lkIjogaW50LCAidGlja2V0X251bSI6IGludH0KIyDhi6jhiJjhjKjhiKjhiLsg4Yib4Yio4YyL4YyI4YyrICjinIUv4p2M"
    "KSDhiaDhiJjhjKDhiaPhiaDhiYUg4YiL4YutIOGLq+GIiCDhiqDhiLjhipPhjYog4Yid4Yud4YyI4YmjIC0gYWRtaW5faWQgLT4g"
    "eyJyb3VuZF9pZCIsInRpY2tldF9udW0iLCJwcml6ZSJ9CnBlbmRpbmdfd2lubmVyX2NvbmZpcm0gPSB7fQojIC0tLSAvd2lubmVy"
    "c2xvdHMg4Yib4Yio4YyL4YyI4YyrIOGJoOGImOGMoOGJo+GJoOGJhSDhiIvhi60gKGFkbWluX2lkIC0+IOGKoOGLsuGIsSDhiYHh"
    "jKXhiK0pIC0tLQpwZW5kaW5nX3dpbm5lcnNsb3RzX2NvbmZpcm0gPSB7fQp3aW5uZXJzbG90c19zdGF0ZSA9IHNldCgpICAjIGFk"
    "bWluX2lkIHNldDogY3VycmVudGx5IGF3YWl0aW5nIGEgbnVtYmVyIGZvciAvd2lubmVyc2xvdHMKCmRlZiBfcGVuZGluZ19hZG1p"
    "bl90YXNrX25hbWUodWlkKToKICAgICIiIuGKoOGLteGImuGKkSDhjIjhipMg4Yur4YiN4Yyo4Yio4Yiw4YuNIOGIguGLsOGJtSAo"
    "d2l6YXJkKSDhiqvhiIgg4Yi14YiZ4YqVIOGLqOGImuGImOGIjeGItSBoZWxwZXLhjaMg4Yqo4YiM4YiIIE5vbmUg4Yut4YiY4YiN"
    "4Yiz4YiN4Y2iIiIiCiAgICBpZiB1aWQgaW4gbmV3cm91bmRfc3RhdGU6CiAgICAgICAgcmV0dXJuICLinpUg4Yqg4Yuy4Yi1IOGL"
    "meGIrSDhiJjhiq3hjYjhibUiCiAgICBpZiB1aWQgaW4gbWFudWFsc2VsbF9zdGF0ZSBvciB1aWQgaW4gbWFudWFsc2VsbF9jYXJ0"
    "OgogICAgICAgIHJldHVybiAi8J+StSDhiaDhiqXhjIUg4Yi94Yur4YytIgogICAgaWYgdWlkIGluIGNyZWRpdF90b3B1cF9zdGF0"
    "ZToKICAgICAgICByZXR1cm4gIuKelSDhiq3hiKzhi7LhibUg4Yyl4Yur4YmEIgogICAgaWYgdWlkIGluIGJyb2FkY2FzdF9zdGF0"
    "ZToKICAgICAgICByZXR1cm4gIvCfk6Ig4Yib4Yi14Ymz4YuI4YmC4YurIgogICAgaWYgdWlkIGluIGVkaXRfcGF5bWVudF9zdGF0"
    "ZToKICAgICAgICByZXR1cm4gIvCfkrMg4Yuo4Yqt4Y2N4YurIOGKoOGKq+GLjeGKleGJtSDhiJvhiLXhibDhiqvhiqjhi6siCiAg"
    "ICBpZiB1aWQgaW4gc2V0d2lubmVyX3N0YXRlOgogICAgICAgIHJldHVybiAi8J+PhiDhiqDhiLjhipPhjYog4YiY4YiY4Yud4YyI"
    "4YmlIgogICAgaWYgdWlkIGluIHdpbm5lcnNsb3RzX3N0YXRlOgogICAgICAgIHJldHVybiAi8J+PhSDhi6jhiqDhiLjhipPhjYog"
    "4Ymm4Ymz4YuO4Ym9IOGJpeGLm+GJtSIKICAgIGlmIHVpZCBpbiBzZXRfc21zX3dlYmhvb2tfc3RhdGU6CiAgICAgICAgcmV0dXJu"
    "ICLwn5SXIOGLqFNNUyBXZWJob29rIFVSTCDhiJvhi4vhiYDhiKrhi6siCiAgICByZXR1cm4gTm9uZQoKZGVmIF9jYW5jZWxfb3Ro"
    "ZXJfYWRtaW5fdGFza3ModWlkLCBrZWVwPU5vbmUpOgogICAgIiIi4Yqg4Yu14Yia4YqRIOGKoOGLsuGItSDhiILhi7DhibUgKG5l"
    "d3JvdW5kL21hbnVhbHNlbGwvYWRkY3JlZGl0L2Fubm91bmNlKSDhiLLhjIDhiJ3hiK3hjaMg4YyI4YqTIOGLq+GIjeGJsOGMoOGK"
    "k+GJgOGJgCDhi6jhibDhiIjhi6gKICAgIOGIguGLsOGJtSDhiqvhiIgg4Yqg4YmL4Yit4YymIOGLqOGImuGLq+GMuOGLsyBoZWxw"
    "ZXIgLSDhiLXhiIjhi5rhiIUg4YiB4YiI4Ym1IOGIguGLsOGJtuGJvSDhjI3hiKsg4Ymw4YyL4Yml4Ymw4YuNIOGLqOGMveGIgeGN"
    "jSDhjI3hiaXhi5PhibUg4Yql4YqV4Yuz4Yut4YiL4YmA4YmBIOGLreGKqOGIi+GKqOGIi+GIjeGNogogICAga2VlcCDhi6jhibDh"
    "iaPhiIjhi43hipUg4YiC4Yuw4Ym1IOGIq+GIsSDhiqDhi63hipDhiqvhi43hiJ0gKOGKoOGLsuGItSDhi6jhiJrhjIDhiJjhiKjh"
    "i40g4Yir4YixIOGIteGIiOGIhuGKkCnhjaIg4Yuo4Ymw4YmL4Yio4YyhIOGIguGLsOGJtSDhiLXhiJ7hib3hipUg4Yud4Yit4Yud"
    "4YitIOGLreGImOGIjeGIs+GIjeGNoiIiIgogICAgY2xlYXJlZCA9IFtdCiAgICBpZiBrZWVwICE9ICJuZXdyb3VuZCIgYW5kIHVp"
    "ZCBpbiBuZXdyb3VuZF9zdGF0ZToKICAgICAgICBuZXdyb3VuZF9zdGF0ZS5wb3AodWlkLCBOb25lKQogICAgICAgIG5ld3JvdW5k"
    "X3RlbXAucG9wKHVpZCwgTm9uZSkKICAgICAgICBjbGVhcmVkLmFwcGVuZCgi4p6VIOGKoOGLsuGItSDhi5nhiK0g4YiY4Yqt4Y2I"
    "4Ym1IikKICAgIGlmIGtlZXAgIT0gIm1hbnVhbHNlbGwiIGFuZCAodWlkIGluIG1hbnVhbHNlbGxfc3RhdGUgb3IgdWlkIGluIG1h"
    "bnVhbHNlbGxfY2FydCk6CiAgICAgICAgbWFudWFsc2VsbF9zdGF0ZS5wb3AodWlkLCBOb25lKQogICAgICAgIG1hbnVhbHNlbGxf"
    "Y2FydC5wb3AodWlkLCBOb25lKQogICAgICAgIGNsZWFyZWQuYXBwZW5kKCLwn5K1IOGJoOGKpeGMhSDhiL3hi6vhjK0iKQogICAg"
    "aWYga2VlcCAhPSAiYWRkY3JlZGl0IiBhbmQgdWlkIGluIGNyZWRpdF90b3B1cF9zdGF0ZToKICAgICAgICBjcmVkaXRfdG9wdXBf"
    "c3RhdGUucG9wKHVpZCwgTm9uZSkKICAgICAgICBjbGVhcmVkLmFwcGVuZCgi4p6VIOGKreGIrOGLsuGJtSDhjKXhi6vhiYQiKQog"
    "ICAgaWYga2VlcCAhPSAiYW5ub3VuY2UiIGFuZCB1aWQgaW4gYnJvYWRjYXN0X3N0YXRlOgogICAgICAgIGJyb2FkY2FzdF9zdGF0"
    "ZS5wb3AodWlkLCBOb25lKQogICAgICAgIGNsZWFyZWQuYXBwZW5kKCLwn5OiIOGIm+GIteGJs+GLiOGJguGLqyIpCiAgICBpZiBr"
    "ZWVwICE9ICJlZGl0cGF5bWVudCIgYW5kIHVpZCBpbiBlZGl0X3BheW1lbnRfc3RhdGU6CiAgICAgICAgZWRpdF9wYXltZW50X3N0"
    "YXRlLnBvcCh1aWQsIE5vbmUpCiAgICAgICAgY2xlYXJlZC5hcHBlbmQoIvCfkrMg4Yuo4Yqt4Y2N4YurIOGKoOGKq+GLjeGKleGJ"
    "tSDhiJvhiLXhibDhiqvhiqjhi6siKQogICAgaWYga2VlcCAhPSAic2V0d2lubmVyIiBhbmQgdWlkIGluIHNldHdpbm5lcl9zdGF0"
    "ZToKICAgICAgICBzZXR3aW5uZXJfc3RhdGUucG9wKHVpZCwgTm9uZSkKICAgICAgICBzZXR3aW5uZXJfdGVtcC5wb3AodWlkLCBO"
    "b25lKQogICAgICAgIGNsZWFyZWQuYXBwZW5kKCLwn4+GIOGKoOGIuOGKk+GNiiDhiJjhiJjhi53hjIjhiaUiKQogICAgaWYga2Vl"
    "cCAhPSAid2lubmVyc2xvdHMiIGFuZCB1aWQgaW4gd2lubmVyc2xvdHNfc3RhdGU6CiAgICAgICAgd2lubmVyc2xvdHNfc3RhdGUu"
    "ZGlzY2FyZCh1aWQpCiAgICAgICAgY2xlYXJlZC5hcHBlbmQoIvCfj4Ug4Yuo4Yqg4Yi44YqT4Y2KIOGJpuGJs+GLjuGJvSDhiaXh"
    "i5vhibUiKQogICAgaWYga2VlcCAhPSAic2V0c21zd2ViaG9vayIgYW5kIHVpZCBpbiBzZXRfc21zX3dlYmhvb2tfc3RhdGU6CiAg"
    "ICAgICAgc2V0X3Ntc193ZWJob29rX3N0YXRlLmRpc2NhcmQodWlkKQogICAgICAgIGNsZWFyZWQuYXBwZW5kKCLwn5SXIOGLqFNN"
    "UyBXZWJob29rIFVSTCDhiJvhi4vhiYDhiKrhi6siKQogICAgcmV0dXJuIGNsZWFyZWQKCmFzeW5jIGRlZiBfbm90aWZ5X2NhbmNl"
    "bGxlZF90YXNrcyhib3QsIHVpZCwgY2xlYXJlZCk6CiAgICAiIiLhiqjhiIvhi60g4YmgX2NhbmNlbF9vdGhlcl9hZG1pbl90YXNr"
    "cyDhi6jhibDhiYvhiKjhjKEg4YiC4Yuw4Ym24Ym9IOGKq+GIiSDhiIjhiqDhi7XhiJrhipEg4Yuo4Yia4Yur4Yiz4YuN4YmFIGhl"
    "bHBlcuGNoiIiIgogICAgaWYgbm90IGNsZWFyZWQ6CiAgICAgICAgcmV0dXJuCiAgICBuYW1lcyA9ICLhjaMgIi5qb2luKGNsZWFy"
    "ZWQpCiAgICB0cnk6CiAgICAgICAgYXdhaXQgYm90LnNlbmRfbWVzc2FnZSgKICAgICAgICAgICAgY2hhdF9pZD11aWQsCiAgICAg"
    "ICAgICAgIHRleHQ9ZiLimqDvuI8g4Yur4YiN4Yyo4Yio4Yix4Ym1IMKre25hbWVzfcK7IOGIguGLsOGJtSDhibDhiYvhiK3hjKfh"
    "iI3hjaMg4Yqg4Yuy4YixIOGJteGLleGLm+GLnSDhiaDhiJjhiYDhjKDhiI0g4YiL4YutIOGKkOGLjeGNoiIKICAgICAgICApCiAg"
    "ICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKCiMgLS0tIOGLqOGJsOGMq+GLi+GJvuGJvSDhiJ3hi53hjIjhiaMgKFBs"
    "YXllciBSZWdpc3RyYXRpb24pIC0tLQpwbGF5ZXJzID0ge30gICMgdXNlcl9pZCAtPiB7Im5hbWUiOiBzdHIsICJwaG9uZSI6IHN0"
    "ciwgInVzZXJuYW1lIjogc3RyfQpyZWdpc3RyYXRpb25fc3RhdGUgPSB7fSAgIyB1c2VyX2lkIC0+ICJhd2FpdGluZ19sYW5ndWFn"
    "ZSIgfCAiYXdhaXRpbmdfbmFtZSIgfCAiYXdhaXRpbmdfcGhvbmUiCnJlZ2lzdHJhdGlvbl90ZW1wID0ge30gICMgdXNlcl9pZCAt"
    "PiB7Im5hbWUiOiBzdHJ9CgojIC0tLSDhi6jhiYvhipXhiYsg4Yid4Yit4YyrIChMYW5ndWFnZSBzZWxlY3Rpb24pIC0tLQpMQU5H"
    "UyA9ICgiYW0iLCAiZW4iLCAib20iKQpERUZBVUxUX0xBTkcgPSAiYW0iCnBsYXllcl9sYW5nID0ge30gICMgdXNlcl9pZCAtPiAi"
    "YW0iIHwgImVuIiB8ICJvbSIKCmRlZiBnZXRfbGFuZyh1c2VyX2lkKToKICAgIHJldHVybiBwbGF5ZXJfbGFuZy5nZXQodXNlcl9p"
    "ZCwgREVGQVVMVF9MQU5HKQoKTEFOR19OQU1FUyA9IHsiYW0iOiAi4Yqg4Yib4Yit4YqbIiwgImVuIjogIkVuZ2xpc2giLCAib20i"
    "OiAiQWZhYW4gT3JvbW9vIn0KCmRlZiBsYW5ndWFnZV9waWNrZXJfa2IocHJlZml4PSJzZXRsYW5nIik6CiAgICByZXR1cm4gSW5s"
    "aW5lS2V5Ym9hcmRNYXJrdXAoW1sKICAgICAgICBJbmxpbmVLZXlib2FyZEJ1dHRvbigiRW5nbGlzaCIsIGNhbGxiYWNrX2RhdGE9"
    "ZiJ7cHJlZml4fV9lbiIpLAogICAgICAgIElubGluZUtleWJvYXJkQnV0dG9uKCLhiqDhiJvhiK3hipsiLCBjYWxsYmFja19kYXRh"
    "PWYie3ByZWZpeH1fYW0iKSwKICAgICAgICBJbmxpbmVLZXlib2FyZEJ1dHRvbigiQWZhYW4gT3JvbW9vIiwgY2FsbGJhY2tfZGF0"
    "YT1mIntwcmVmaXh9X29tIiksCiAgICBdXSkKCiMg4YuL4YqTIOGLqOGJsOGMoOGJg+GImi3hjIjhjL3hibMg4Yy94YiB4Y2O4Ym9"
    "IOGJoOGItuGIteGJtSDhiYvhipXhiYsgKGFtPeGKoOGIm+GIreGKmywgZW49RW5nbGlzaCwgb209QWZhYW4gT3JvbW9vKQpUWFQg"
    "PSB7CiAgICAiY2hvb3NlX2xhbmd1YWdlIjogewogICAgICAgICJhbSI6ICLwn4yQIOGKpeGJo+GKreGLjiDhiYvhipXhiYsg4Yut"
    "4Yid4Yio4Yyh4Y2mIiwKICAgICAgICAiZW4iOiAi8J+MkCBQbGVhc2UgY2hvb3NlIHlvdXIgbGFuZ3VhZ2U6IiwKICAgICAgICAi"
    "b20iOiAi8J+MkCBNYWFsb28gYWZhYW4gZmlsYWRoYWE6IiwKICAgIH0sCiAgICAibGFuZ3VhZ2Vfc2F2ZWQiOiB7CiAgICAgICAg"
    "ImFtIjogIuKchSDhiYvhipXhiYvhi44g4YuI4YuwIOGKoOGIm+GIreGKmyDhibDhiYDhi63hiK/hiI3hjaIiLAogICAgICAgICJl"
    "biI6ICLinIUgWW91ciBsYW5ndWFnZSBoYXMgYmVlbiBzZXQgdG8gRW5nbGlzaC4iLAogICAgICAgICJvbSI6ICLinIUgQWZhYW4g"
    "a2Vlc3NhbiBnYXJhIEFmYWFuIE9yb21vb3R0aSBqaWpqaWlyYW1lZXJhLiIsCiAgICB9LAogICAgImFza19uYW1lIjogewogICAg"
    "ICAgICJhbSI6ICLwn5GLIOGKpeGKleGKs+GKlSDhi4jhi7Ag4Yqg4YuN4Ym24Yib4Ymy4YqtIOGLqOGIjuGJsOGIqiDhjKjhi4vh"
    "ibMg4Ymm4Ym1IOGJoOGLsOGIheGKkyDhiJjhjKEhXG5cbvCfp74g4YiI4YiY4Yyr4YuI4Ym1IOGKpeGJo+GKreGLjiDhiqDhipXh"
    "i7Ug4YyK4YucIOGLreGImOGLneGMiOGJoeGNolxuXG7wn5GkIOGKpeGJo+GKreGLjiDhiJnhiIkg4Yi14Yid4YuO4YqVIOGLreGI"
    "i+GKqeGNpiIsCiAgICAgICAgImVuIjogIvCfkYsgV2VsY29tZSB0byB0aGUgYXV0b21hdGljIGxvdHRlcnkgYm90IVxuXG7wn6e+"
    "IFBsZWFzZSByZWdpc3RlciBvbmNlIGJlZm9yZSB5b3UgY2FuIHBsYXkuXG5cbvCfkaQgUGxlYXNlIHNlbmQgeW91ciBmdWxsIG5h"
    "bWU6IiwKICAgICAgICAib20iOiAi8J+RiyBCYWdhIGdhcmEgYm90aSBsb290YXJpaSBvdG9tYWF0aWthYXR0aSBuYWdhYW4gZGh1"
    "ZnRhbiFcblxu8J+nviBUYXBoYWNodXVmIGR1cmFhbiBkdXJzYWEgeWVyb28gdG9ra28gZ2FsbWFhJ2FhLlxuXG7wn5GkIE1hYWxv"
    "byBtYXFhYSBrZWVzc2FuIGd1dXR1dSBlcmdhYToiLAogICAgfSwKICAgICJhc2tfcGhvbmUiOiB7CiAgICAgICAgImFtIjogIvCf"
    "k7Eg4Yqg4YiY4Yiw4YyN4YqT4YiI4YiBISDhiqDhiIHhipUg4Yuo4Yi14YiN4YqtIOGJgeGMpeGIreGLjuGKlSDhi63hiIvhiqkg"
    "KOGIiOGIneGIs+GIjOGNpiAwOTEyMzQ1Njc4KeGNpiIsCiAgICAgICAgImVuIjogIvCfk7EgVGhhbmsgeW91ISBOb3cgcGxlYXNl"
    "IHNlbmQgeW91ciBwaG9uZSBudW1iZXIgKGUuZy4gMDkxMjM0NTY3OCk6IiwKICAgICAgICAib20iOiAi8J+TsSBHYWxhdG9vbWFh"
    "ISBBbW1hIGxha2tvb2ZzYSBiaWxiaWxhIGtlZXNzYW4gZXJnYWEgKGZrbjogMDkxMjM0NTY3OCk6IiwKICAgIH0sCiAgICAiaW52"
    "YWxpZF9uYW1lIjogewogICAgICAgICJhbSI6ICLimqDvuI8g4Yql4Ymj4Yqt4YuOIOGJteGKreGKreGIiOGKmyDhiJnhiIkg4Yi1"
    "4YidIOGLq+GIteGMiOGJoeGNpiIsCiAgICAgICAgImVuIjogIuKaoO+4jyBQbGVhc2UgZW50ZXIgYSB2YWxpZCBmdWxsIG5hbWU6"
    "IiwKICAgICAgICAib20iOiAi4pqg77iPIE1hYWxvbyBtYXFhYSBndXV0dXUgc2lycmlpIGdhbGNoYWE6IiwKICAgIH0sCiAgICAi"
    "aW52YWxpZF9waG9uZSI6IHsKICAgICAgICAiYW0iOiAi4pqg77iPIOGLqOGIteGIjeGKrSDhiYHhjKXhiKkg4Ym14Yqt4Yqt4YiN"
    "IOGKoOGLreGImOGIteGIjeGIneGNoiDhiqXhiaPhiq3hi44g4Ymg4Ym14Yqt4Yqt4YiI4YqbIOGNjuGIreGIm+GJtSDhi63hiIvh"
    "iqkgKOGIiOGIneGIs+GIjOGNpiAwOTEyMzQ1Njc4KeGNpiIsCiAgICAgICAgImVuIjogIuKaoO+4jyBUaGF0IHBob25lIG51bWJl"
    "ciBkb2Vzbid0IGxvb2sgdmFsaWQuIFBsZWFzZSBzZW5kIGl0IGluIHRoZSBjb3JyZWN0IGZvcm1hdCAoZS5nLiAwOTEyMzQ1Njc4"
    "KToiLAogICAgICAgICJvbSI6ICLimqDvuI8gTGFra29vZnNpIGJpbGJpbGFhIGt1biBzaXJyaWkgaGluIGZha2thYXR1LiBNYWFs"
    "b28gYmlmYSBzaXJyaWkgdGEnZWVuIGVyZ2FhIChma246IDA5MTIzNDU2NzgpOiIsCiAgICB9LAogICAgInBob25lX2FscmVhZHlf"
    "cmVnaXN0ZXJlZCI6IHsKICAgICAgICAiYW0iOiAi4pqg77iPIDxiPuGLreGIhSDhiLXhiI3hiq0g4YmB4Yyl4YitIOGKoOGIteGJ"
    "gOGLteGIniDhibDhiJjhi53hjI3hiafhiI0hPC9iPlxuXG7wn5GkIOGKoOGKleGLtSDhibDhjKvhi4vhib0g4Yqg4YqV4Yu1IOGI"
    "teGIjeGKrSDhiYHhjKXhiK0g4Yml4Ym7IOGImOGMoOGJgOGInSDhi63hib3hiIvhiI3hjaJcbvCfk7Eg4Yql4Ymj4Yqt4YuOIOGL"
    "qOGIq+GIteGLjuGKlSDhi6vhiI3hibDhiJjhi5jhjIjhiaAg4Yi14YiN4YqtIOGJgeGMpeGIrSDhi63hiIvhiqnhjaIiLAogICAg"
    "ICAgICJlbiI6ICLimqDvuI8gPGI+VGhpcyBwaG9uZSBudW1iZXIgaXMgYWxyZWFkeSByZWdpc3RlcmVkITwvYj5cblxu8J+RpCBF"
    "YWNoIHBsYXllciBtYXkgb25seSB1c2Ugb25lIHBob25lIG51bWJlci5cbvCfk7EgUGxlYXNlIHNlbmQgeW91ciBvd24sIHVucmVn"
    "aXN0ZXJlZCBwaG9uZSBudW1iZXIuIiwKICAgICAgICAib20iOiAi4pqg77iPIDxiPkxha2tvb2ZzaSBiaWxiaWxhYSBrdW4gZHVy"
    "YWFuIGdhbG1hYSdlZXJhITwvYj5cblxu8J+RpCBUYXBoYXRhYW4gdG9ra28gbGFra29vZnNhIGJpbGJpbGFhIHRva2tvIHFvZmEg"
    "ZmF5eWFkYW11dSBkYW5kYSdhLlxu8J+TsSBNYWFsb28gbGFra29vZnNhIGJpbGJpbGFhIGthbiBoaW4gZ2FsbW9vZm5lIGthbiBr"
    "ZWVzc2FuIGVyZ2FhLiIsCiAgICB9LAogICAgInJlZ2lzdHJhdGlvbl9jb21wbGV0ZSI6IHsKICAgICAgICAiYW0iOiAi4pyFIOGI"
    "neGLneGMiOGJo+GLjiDhibDhjKDhipPhiYvhiI3hjaMge25hbWV9IVxuXG7wn46wIOGKqOGJs+GJvSDhi6vhiIjhi43hipUg4Yic"
    "4YqRIOGLreGMoOGJgOGImeGNoiIsCiAgICAgICAgImVuIjogIuKchSBSZWdpc3RyYXRpb24gY29tcGxldGUsIHtuYW1lfSFcblxu"
    "8J+OsCBVc2UgdGhlIG1lbnUgYmVsb3cgdG8gY29udGludWUuIiwKICAgICAgICAib20iOiAi4pyFIEdhbG1lZSBrZWVzc2FuIHh1"
    "bXVyYW1lZXJhLCB7bmFtZX0hXG5cbvCfjrAgTWVudSBnYWRpaSBmYXl5YWRhbWFhLiIsCiAgICB9LAogICAgIndlbGNvbWVfYmFj"
    "ayI6IHsKICAgICAgICAiYW0iOiAi8J+RiyDhiqXhipXhirPhipUg4Yuw4YiF4YqTIOGImOGMoeGNoyB7bmFtZX0hXG5cbuGKqOGJ"
    "s+GJvSDhi6vhiIjhi43hipUg4Yic4YqRIOGJoOGImOGMoOGJgOGInSDhi63hiYDhjKXhiInhjaIiLAogICAgICAgICJlbiI6ICLw"
    "n5GLIFdlbGNvbWUgYmFjaywge25hbWV9IVxuXG5Vc2UgdGhlIG1lbnUgYmVsb3cgdG8gY29udGludWUuIiwKICAgICAgICAib20i"
    "OiAi8J+RiyBCYWdhIG5hZ2FhbiBkZWViaXRhbiwge25hbWV9IVxuXG5NZW51IGdhZGlpIGZheXlhZGFtdXVkaGFhbiBpdHRpIGZ1"
    "ZmFhLiIsCiAgICB9LAogICAgIm11c3RfcmVnaXN0ZXJfZmlyc3QiOiB7CiAgICAgICAgImFtIjogIvCfp74g4YiY4Yyr4YuI4Ym1"
    "IOGKqOGImOGMgOGImOGIreGLjiDhiaDhjYrhibUg4Yql4Ymj4Yqt4YuOIOGLreGImOGLneGMiOGJoeGNolxuXG7wn5GkIOGKpeGJ"
    "o+GKreGLjiDhiJnhiIkg4Yi14Yid4YuO4YqVIOGLreGIi+GKqeGNpiIsCiAgICAgICAgImVuIjogIvCfp74gUGxlYXNlIHJlZ2lz"
    "dGVyIGJlZm9yZSB5b3Ugc3RhcnQgcGxheWluZy5cblxu8J+RpCBQbGVhc2Ugc2VuZCB5b3VyIGZ1bGwgbmFtZToiLAogICAgICAg"
    "ICJvbSI6ICLwn6e+IFRhcGhhY2h1dSB1dHV1IGhpbiBqYWxxYWJpbiBkdXJhIGdhbG1hYSdhYS5cblxu8J+RpCBNYWFsb28gbWFx"
    "YWEga2Vlc3NhbiBndXV0dXUgZXJnYWE6IiwKICAgIH0sCiAgICAiYnRuX3BsYXkiOiB7ImFtIjogIvCfjq4g4YiY4Yyr4YuI4Ym1"
    "IiwgImVuIjogIvCfjq4gUGxheSIsICJvbSI6ICLwn46uIFRhcGhhY2h1dSJ9LAogICAgImJ0bl9yb3VuZHMiOiB7ImFtIjogIvCf"
    "jrIg4YqV4YmBIOGLmeGIruGJvSIsICJlbiI6ICLwn46yIEFjdGl2ZSBSb3VuZHMiLCAib20iOiAi8J+OsiBNYXJzYWEgQW1tZWUi"
    "fSwKICAgICJidG5fbXlpbmZvIjogeyJhbSI6ICLwn5GkIOGLqOGKpeGKlCDhiJjhiKjhjIMiLCAiZW4iOiAi8J+RpCBNeSBJbmZv"
    "IiwgIm9tIjogIvCfkaQgT2RlZWZmYW5ub28gS29vIn0sCiAgICAiYnRuX2hlbHAiOiB7ImFtIjogIuKEue+4jyDhiJjhiJjhiKrh"
    "i6siLCAiZW4iOiAi4oS577iPIEhlbHAiLCAib20iOiAi4oS577iPIFFhamVlbGZhbWEifSwKICAgICJidG5fY29udGFjdCI6IHsi"
    "YW0iOiAi8J+TniDhiqXhiK3hi7PhibMiLCAiZW4iOiAi8J+TniBTdXBwb3J0IiwgIm9tIjogIvCfk54gRGVlZ2dhcnNhIn0sCiAg"
    "ICAiYnRuX2xhbmd1YWdlIjogeyJhbSI6ICLwn4yQIOGJi+GKleGJiyIsICJlbiI6ICLwn4yQIExhbmd1YWdlIiwgIm9tIjogIvCf"
    "jJAgQWZhYW4ifSwKICAgICJidG5fYmVjb21lX2hvc3QiOiB7ImFtIjogIuGKoOGMq+GLi+GJvSDhiJjhiIbhipUg4Yut4Y2I4YiN"
    "4YyL4YiJPyIsICJlbiI6ICLwn5al77iPIEJlY29tZSBhIEhvc3QiLCAib20iOiAi8J+Wpe+4jyBIb3N0IFRhJ3V1IEJhcmJhYWRh"
    "In0sCiAgICAiYnRuX3dpbm5lcnMiOiB7ImFtIjogIvCfj4Yg4Yqg4Yi44YqT4Y2K4YuO4Ym9IiwgImVuIjogIvCfj4YgV2lubmVy"
    "cyIsICJvbSI6ICLwn4+GIEluamlmYXR0b290YSJ9LAogICAgIyAtLS0g4Yuo4YiG4Yi14Ym1ICjhiqDhi7XhiJrhipUpIOGJpeGJ"
    "uyDhiJzhipEg4YmB4YiN4Y2O4Ym9IC0g4YiN4YqtIOGKpeGKleGLsCDhibDhjKvhi4vhib7hibkg4YiG4Yi14Ymx4YidIOGIq+GI"
    "sSDhiYvhipXhiYsg4YiY4YmA4Yuo4YitIOGLreGJveGIi+GIjSAtLS0KICAgICJhZG1pbl9idG5fbmV3X3JvdW5kIjogeyJhbSI6"
    "ICLinpUg4Yqg4Yuy4Yi1IOGLmeGIrSIsICJlbiI6ICLinpUgTmV3IFJvdW5kIiwgIm9tIjogIuKelSBNYXJzYWEgSGFhcmFhIn0s"
    "CiAgICAiYWRtaW5fYnRuX3JvdW5kc19saXN0IjogeyJhbSI6ICLwn5OLIOGLmeGIruGJvSIsICJlbiI6ICLwn5OLIFJvdW5kcyIs"
    "ICJvbSI6ICLwn5OLIE1hcnNhYWxlZSJ9LAogICAgImFkbWluX2J0bl9tYW51YWxfc2FsZSI6IHsiYW0iOiAi8J+StSDhiaDhiqXh"
    "jIUg4Yi94Yur4YytIiwgImVuIjogIvCfkrUgTWFudWFsIFNhbGUiLCAib20iOiAi8J+StSBHdXJndXJ0YWEgSGFya2FhbiJ9LAog"
    "ICAgImFkbWluX2J0bl9zb2xkIjogeyJhbSI6ICLwn46f77iPIOGLqOGJsOGIuOGMoSDhibLhiqzhibbhib0iLCAiZW4iOiAi8J+O"
    "n++4jyBTb2xkIFRpY2tldHMiLCAib20iOiAi8J+On++4jyBUaWtlZXRhIEd1cmd1cmFtZSJ9LAogICAgImFkbWluX2J0bl9hbGxf"
    "dGlja2V0cyI6IHsiYW0iOiAi8J+Xgu+4jyDhiIHhiInhiJ0g4Ymy4Yqs4Ym24Ym9IiwgImVuIjogIvCfl4LvuI8gQWxsIFRpY2tl"
    "dHMiLCAib20iOiAi8J+Xgu+4jyBUaWtlZXRhIEh1bmRhIn0sCiAgICAiYWRtaW5fYnRuX3Vuc29sZCI6IHsiYW0iOiAi8J+foiDh"
    "i6vhiI3hibDhiLjhjKEg4Ymy4Yqs4Ym24Ym9IiwgImVuIjogIvCfn6IgVW5zb2xkIFRpY2tldHMiLCAib20iOiAi8J+foiBUaWtl"
    "ZXRhIEhpbiBHdXJndXJhbWluIn0sCiAgICAiYWRtaW5fYnRuX3NldF93aW5uZXIiOiB7ImFtIjogIvCfjq8g4Yqg4Yi44YqT4Y2K"
    "IOGImOGLneGMjeGJpSIsICJlbiI6ICLwn46vIFNldCBXaW5uZXIiLCAib20iOiAi8J+OryBJbmppZmF0YWEgR2FsbWVlc3NpIn0s"
    "CiAgICAiYWRtaW5fYnRuX3BheW1lbnRzIjogeyJhbSI6ICLwn5KwIOGKreGNjeGLq+GLjuGJvSIsICJlbiI6ICLwn5KwIFBheW1l"
    "bnRzIiwgIm9tIjogIvCfkrAgS2FmZmFsdGlpd3dhbiJ9LAogICAgImFkbWluX2J0bl9zdGF0cyI6IHsiYW0iOiAi8J+TiiDhiLXh"
    "ibPhibLhiLXhibLhiq3hiLUiLCAiZW4iOiAi8J+TiiBTdGF0aXN0aWNzIiwgIm9tIjogIvCfk4ogSXN0YWF0aXN0aWtzaWkifSwK"
    "ICAgICJhZG1pbl9idG5fcGF1c2UiOiB7ImFtIjogIuKPuO+4jyDhiL3hi6vhjK0g4Yqg4YmB4YidIiwgImVuIjogIuKPuO+4jyBQ"
    "YXVzZSBTYWxlcyIsICJvbSI6ICLij7jvuI8gR3VyZ3VydGFhIERoYWFiaSJ9LAogICAgImFkbWluX2J0bl9yZXN1bWUiOiB7ImFt"
    "IjogIuKWtu+4jyDhiL3hi6vhjK0g4YmA4Yyl4YiNIiwgImVuIjogIuKWtu+4jyBSZXN1bWUgU2FsZXMiLCAib20iOiAi4pa277iP"
    "IEd1cmd1cnRhYSBJdHRpIEZ1ZmkifSwKICAgICJhZG1pbl9idG5fY2xvc2Vfcm91bmQiOiB7ImFtIjogIvCflJIg4YuZ4YitIOGL"
    "neGMiyIsICJlbiI6ICLwn5SSIENsb3NlIFJvdW5kIiwgIm9tIjogIvCflJIgTWFyc2FhIEN1ZmkifSwKICAgICJhZG1pbl9idG5f"
    "cmVzdGFydF9yb3VuZCI6IHsiYW0iOiAi8J+UhCDhi5nhiK0g4Yql4YqV4Yuw4YyI4YqTIOGMgOGIneGIrSIsICJlbiI6ICLwn5SE"
    "IFJlc3RhcnQgUm91bmQiLCAib20iOiAi8J+UhCBNYXJzYWEgSXJyYSBEZWViaSdpaSBKYWxxYWJpIn0sCiAgICAiYWRtaW5fYnRu"
    "X2RlbGV0ZV9yb3VuZCI6IHsiYW0iOiAi8J+Xke+4jyDhi5nhiK0g4Yiw4Yit4YudIiwgImVuIjogIvCfl5HvuI8gRGVsZXRlIFJv"
    "dW5kIiwgIm9tIjogIvCfl5HvuI8gTWFyc2FhIEhhcWkifSwKICAgICJhZG1pbl9idG5fcGxheWVycyI6IHsiYW0iOiAi8J+RpSDh"
    "ibDhjKvhi4vhib7hib0iLCAiZW4iOiAi8J+RpSBQbGF5ZXJzIiwgIm9tIjogIvCfkaUgVGFwaGF0dG9vdGEifSwKICAgICJhZG1p"
    "bl9idG5fcmVsZWFzZV90aWNrZXQiOiB7ImFtIjogIvCflJMg4Ymy4Yqs4Ym1IOGIjeGJgOGJhSIsICJlbiI6ICLwn5STIFJlbGVh"
    "c2UgVGlja2V0IiwgIm9tIjogIvCflJMgVGlrZWV0YSBIaWlraSJ9LAogICAgImFkbWluX2J0bl9ob3N0X3Byb2ZpbGUiOiB7ImFt"
    "IjogIvCfj6Ag4Yuo4YiG4Yi14Ym1IOGImOGMiOGIiOGMqyIsICJlbiI6ICLwn4+gIEhvc3QgUHJvZmlsZSIsICJvbSI6ICLwn4+g"
    "IFBpcm9vZmFheWlsaWkgSG9zdCJ9LAogICAgImFkbWluX2J0bl9hZGRfY3JlZGl0IjogeyJhbSI6ICLinpUg4Yqt4Yis4Yuy4Ym1"
    "IOGMqOGIneGIrSIsICJlbiI6ICLinpUgQWRkIENyZWRpdCIsICJvbSI6ICLinpUgS2lyZWRpaSBEYWJhbGkifSwKICAgICJhZG1p"
    "bl9idG5fcGF5bWVudF9hY2NvdW50IjogeyJhbSI6ICLwn5KzIOGKreGNjeGLqyDhiqDhiqvhi43hipXhibUiLCAiZW4iOiAi8J+S"
    "syBQYXltZW50IEFjY291bnQiLCAib20iOiAi8J+SsyBIZXJyZWdhIEthZmZhbHRpaSJ9LAogICAgImFkbWluX2J0bl9hbm5vdW5j"
    "ZSI6IHsiYW0iOiAi8J+ToiDhiJvhiLXhibPhi4jhiYLhi6siLCAiZW4iOiAi8J+ToiBBbm5vdW5jZW1lbnQiLCAib20iOiAi8J+T"
    "oiBCZWVrc2lzYSJ9LAogICAgImFkbWluX2J0bl9oZWxwIjogeyJhbSI6ICLwn4aYIOGKpeGIreGLs+GJsyIsICJlbiI6ICLwn4aY"
    "IEhlbHAiLCAib20iOiAi8J+GmCBHYXJnYWFyc2EifSwKICAgICJiZWNvbWVfaG9zdF9yZXF1ZXN0X3NlbnQiOiB7CiAgICAgICAg"
    "ImFtIjogIuKchSDhjKXhi6vhiYThi44g4YiI4Yqg4Yu14Yia4YqRIOGJsOGIjeGKs+GIjSEg4Yi14YiIIOGIhuGIteGJtSDhiJjh"
    "iIbhipUg4Yud4Yit4Yud4YitIOGImOGIqOGMgyDhiaDhiYXhiK3hiaEg4Yur4YyI4YqZ4YuO4Ymz4YiN4Y2iIiwKICAgICAgICAi"
    "ZW4iOiAi4pyFIFlvdXIgcmVxdWVzdCBoYXMgYmVlbiBzZW50IHRvIHRoZSBhZG1pbiEgWW91J2xsIGhlYXIgYmFjayB3aXRoIGRl"
    "dGFpbHMgYWJvdXQgYmVjb21pbmcgYSBob3N0IHNvb24uIiwKICAgICAgICAib20iOiAi4pyFIEdhYWZmaWluIGtlZXNzYW4gYWRt"
    "aW4tdHRpIGVyZ2FtZWVyYSEgT2RlZWZmYW5ub28gaG9zdCB0YSd1dSBkaGloZWVueWEgYXJnYXR0dS4iLAogICAgfSwKICAgICJt"
    "eV9pbmZvIjogewogICAgICAgICJhbSI6ICLwn5GkIDxiPntuYW1lfTwvYj5cbvCfk7Ege3Bob25lfVxu8J+UlyBAe3VzZXJuYW1l"
    "fSIsCiAgICAgICAgImVuIjogIvCfkaQgPGI+e25hbWV9PC9iPlxu8J+TsSB7cGhvbmV9XG7wn5SXIEB7dXNlcm5hbWV9IiwKICAg"
    "ICAgICAib20iOiAi8J+RpCA8Yj57bmFtZX08L2I+XG7wn5OxIHtwaG9uZX1cbvCflJcgQHt1c2VybmFtZX0iLAogICAgfSwKICAg"
    "ICJjb250YWN0X2FkbWluIjogewogICAgICAgICJhbSI6ICLwn5OeIOGKpeGJo+GKreGLjiDhiqjhiqDhi7XhiJrhipEg4YyL4Yit"
    "IOGLreGMiOGKk+GKmeGNpiA8Y29kZT4wOTYzNTMwMDMwPC9jb2RlPlxuXG7wn5GHIOGKqOGJs+GJvSDhi6vhiIjhi43hipUg4YmB"
    "4YiN4Y2NIOGJoOGImOGMq+GKlSDhiaDhiYDhjKXhibMg4Yut4Yuw4YuN4YiJ4Y2iIiwKICAgICAgICAiZW4iOiAi8J+TniBQbGVh"
    "c2UgY29udGFjdCB0aGUgYWRtaW46IDxjb2RlPjA5NjM1MzAwMzA8L2NvZGU+XG5cbvCfkYcgVGFwIHRoZSBidXR0b24gYmVsb3cg"
    "dG8gY2FsbCBkaXJlY3RseS4iLAogICAgICAgICJvbSI6ICLwn5OeIE1hYWxvbyBhZG1pbiB3YWxpaW4gcXV1bm5hbWFhOiA8Y29k"
    "ZT4wOTYzNTMwMDMwPC9jb2RlPlxuXG7wn5GHIEJpbGJpbHV1ZiBxYWJkdXUgYXJtYWFuIGdhZGlpIHR1cWFhLiIsCiAgICB9LAog"
    "ICAgInJvdW5kc19ub25lX2FjdGl2ZSI6IHsKICAgICAgICAiYW0iOiAi4oS577iPIOGJoOGKoOGIgeGKkSDhiLDhi5PhibUg4Yid"
    "4YqV4YidIOGKleGJgSDhi5nhiK0g4Yuo4YiI4Yid4Y2iIOGKoOGLteGImuGKkSDhiqDhi7LhiLUg4YuZ4YitIOGKpeGIteGKquGK"
    "qOGNjeGJtSDhi63hjKDhiaXhiYHhjaIiLAogICAgICAgICJlbiI6ICLihLnvuI8gVGhlcmUgYXJlIG5vIGFjdGl2ZSByb3VuZHMg"
    "cmlnaHQgbm93LiBQbGVhc2Ugd2FpdCBmb3IgdGhlIGFkbWluIHRvIG9wZW4gYSBuZXcgcm91bmQuIiwKICAgICAgICAib20iOiAi"
    "4oS577iPIFllcm9vIGFtbWFhIG1hcnNhYW4gaG9qaWlycmEgamlydSBoaW4gamlydS4gQWRtaW4gbWFyc2FhIGhhYXJhYSBoYW5n"
    "YSBiYW51dXR0aSBlZWdhYS4iLAogICAgfSwKICAgICJyb3VuZHNfaGVhZGVyIjogewogICAgICAgICJhbSI6ICLwn46yIDxiPuGK"
    "leGJgSDhi5nhiK7hib08L2I+XG4iLAogICAgICAgICJlbiI6ICLwn46yIDxiPkFjdGl2ZSBSb3VuZHM8L2I+XG4iLAogICAgICAg"
    "ICJvbSI6ICLwn46yIDxiPk1hcnNhYXd3YW4gSG9qaWlycmEgSmlyYW48L2I+XG4iLAogICAgfSwKICAgICJyb3VuZHNfdXNhZ2Vf"
    "aGludCI6IHsKICAgICAgICAiYW0iOiAiXG7hiqDhjKDhiYPhiYDhiJ3hjaYgL3BsYXkgPOGLmeGIrSDhiYHhjKXhiK0+ICjhiIjh"
    "iJ3hiLPhiIzhjaYgL3BsYXkgMSkiLAogICAgICAgICJlbiI6ICJcblVzYWdlOiAvcGxheSA8cm91bmQgbnVtYmVyPiAoZS5nLiAv"
    "cGxheSAxKSIsCiAgICAgICAgIm9tIjogIlxuSXR0aSBmYXl5YWRhbWE6IC9wbGF5IDxsYWtrLiBtYXJzYWE+IChma246IC9wbGF5"
    "IDEpIiwKICAgIH0sCiAgICAicm91bmRzX2xpbmVfbGVmdCI6IHsKICAgICAgICAiYW0iOiAi4Yur4YiN4Ymw4Yur4YuZIOGJgOGI"
    "reGJsOGLi+GIjSIsCiAgICAgICAgImVuIjogImxlZnQgdW5jbGFpbWVkIiwKICAgICAgICAib20iOiAia2FuIGhhZmFuIGhpbiBx"
    "YWJhbWluIiwKICAgIH0sCiAgICAidGlja2V0X3ByaWNlX3VuaXQiOiB7CiAgICAgICAgImFtIjogIuGJpeGIrS/hibLhiqzhibUi"
    "LAogICAgICAgICJlbiI6ICJiaXJyL3RpY2tldCIsCiAgICAgICAgIm9tIjogImJpcnJpaS90aWtlZXRpaSIsCiAgICB9LAogICAg"
    "InJlbGVhc2VkX25vdGlmaWNhdGlvbiI6IHsKICAgICAgICAiYW0iOiAi8J+UlCDhiJvhiLPhi4jhiYLhi6shXG5cbvCfjp/vuI8g"
    "4YmB4Yyl4YitIHt0bn0gKHtybGFiZWx9KSDhiqDhiIHhipUg4Yur4YiN4Ymw4Yur4YuYIOGIhuGKl+GIjeGNolxu8J+RhyDhiqjh"
    "jYjhiIjhjIkg4Yqg4YiB4YqR4YqRIOGLreGIneGIqOGMoeGNoiIsCiAgICAgICAgImVuIjogIvCflJQgTm90aWNlIVxuXG7wn46f"
    "77iPIFRpY2tldCAje3RufSAoe3JsYWJlbH0pIGlzIG5vdyBhdmFpbGFibGUgYWdhaW4uXG7wn5GHIFRhcCBiZWxvdyB0byBncmFi"
    "IGl0IGlmIHlvdSdkIGxpa2UuIiwKICAgICAgICAib20iOiAi8J+UlCBCZWVrc2lzYSFcblxu8J+On++4jyBMYWtrb29mc2kgI3t0"
    "bn0gKHtybGFiZWx9KSBhbW1hIGJhbmFhIHRhJ2VlcmEuXG7wn5GHIFlvbyBiYXJiYWFkZGFuIGFtbWEgZmlsYWRoYWEuIiwKICAg"
    "IH0sCiAgICAid2F0Y2hfY29uZmlybWVkIjogewogICAgICAgICJhbSI6ICLwn5SUIOGJgeGMpeGIrSB7dG59ICh7cmxhYmVsfSkg"
    "4Yql4YqV4Yuw4Ymw4YiI4YmA4YmAIOGLiOGLsuGLq+GLjeGKkSDhiqXhipPhiLPhi43hiYXhi47hibPhiIjhipXhjaIiLAogICAg"
    "ICAgICJlbiI6ICLwn5SUIFdlJ2xsIG5vdGlmeSB5b3UgYXMgc29vbiBhcyB0aWNrZXQgI3t0bn0gKHtybGFiZWx9KSBpcyByZWxl"
    "YXNlZC4iLAogICAgICAgICJvbSI6ICLwn5SUIExha2tvb2ZzaSAje3RufSAoe3JsYWJlbH0pIHllcm9vIGdhZGkgbGFra2lmYW11"
    "IGJhdHRhbHVtYXR0aSBpc2luIGJlZWtzaWZuYS4iLAogICAgfSwKICAgICJob2xkX2V4cGlyZWQiOiB7CiAgICAgICAgImFtIjog"
    "IuKdjCDhi6h7bWluc30g4Yuw4YmC4YmDIOGLqOGKreGNjeGLqyDhjIrhi5zhi44g4Yqg4YiN4Y2P4YiN4Y2iXG7wn46f77iPIOGJ"
    "geGMpeGIqSDhiqXhipXhi7DhjIjhipMg4Yur4YiN4Ymw4Yur4YuYIOGIhuGKl+GIjeGNoiDhiqXhiaPhiq3hi44g4Yqo4Ymz4Ym9"
    "IOGJoOGImOGMq+GKlSDhiqXhipXhi7DhjIjhipMg4Yut4Yid4Yio4Yyh4Y2iIiwKICAgICAgICAiZW4iOiAi4p2MIFlvdXIge21p"
    "bnN9LW1pbnV0ZSBwYXltZW50IHdpbmRvdyBoYXMgZXhwaXJlZC5cbvCfjp/vuI8gVGhlIHRpY2tldCBoYXMgYmVlbiByZWxlYXNl"
    "ZCBhZ2Fpbi4gUGxlYXNlIHRhcCBiZWxvdyB0byBwaWNrIGFnYWluLiIsCiAgICAgICAgIm9tIjogIuKdjCBZZXJvb24ga2FmZmFs"
    "dGlpIGRhcWlpcWFhIHttaW5zfSBrZWVzc2FuIGRhYnJlZXJhLlxu8J+On++4jyBUaWtlZXRpaW4gc3VuIGFtbWFzIGhpaWthbWVl"
    "cmEuIE1hYWxvbyBnYWRpaSB0dXFhYXRpaSBhbW1hcyBmaWxhZGhhYS4iLAogICAgfSwKICAgICJyZWplY3RlZF9vdXRfb2ZfYXR0"
    "ZW1wdHMiOiB7CiAgICAgICAgImFtIjogIvCfmqsgPGI+4YuN4Yu14YmFIOGJsOGLsOGIreGMk+GIjSAoUmVqZWN0ZWQpPC9iPlxu"
    "XG7hiqXhiaPhiq3hi44g4Yql4YqV4Yuw4YyI4YqTIC9wbGF5IOGJpeGIiOGLjSDhi63hjIDhiJ3hiKkg4YuI4Yut4YidIOGKoOGL"
    "teGImuGKkeGKlSDhi6vhipDhjIvhjI3hiKnhjaIiLAogICAgICAgICJlbiI6ICLwn5qrIDxiPlJlamVjdGVkPC9iPlxuXG5QbGVh"
    "c2Ugc3RhcnQgYWdhaW4gd2l0aCAvcGxheSBvciBjb250YWN0IHRoZSBhZG1pbi4iLAogICAgICAgICJvbSI6ICLwn5qrIDxiPkRp"
    "ZGRlZXJhIChSZWplY3RlZCk8L2I+XG5cbk1hYWxvbyAvcGxheSBqZWRoYWEgYW1tYXMgamFscWFiYWEgeW9va2FhbiBhZG1pbiBx"
    "dXVubmFtYWEuIiwKICAgIH0sCiAgICAicmVqZWN0ZWRfcmV0cnkiOiB7CiAgICAgICAgImFtIjogIvCfmqsgPGI+4YuN4Yu14YmF"
    "IOGJsOGLsOGIreGMk+GIjSAoUmVqZWN0ZWQpPC9iPlxuXG7hiqXhiaPhiq3hi44g4Yuo4Yqt4Y2N4YurIOGKpOGIteGKpOGIneGK"
    "pOGIteGLjiDhiJjhi7XhiKjhiLHhipUg4Yqg4Yio4YyL4YyN4Yyg4YuNIOGJteGKreGKreGIiOGKm+GLjeGKlSBSZWZlcmVuY2Ug"
    "SUQg4Ymg4Yu14YyL4YiaIOGLreGIi+GKqVxu8J+UgSDhi6jhiYDhiKkg4YiZ4Yqo4Yir4YuO4Ym94Y2mIHtyZW1haW5pbmd9IiwK"
    "ICAgICAgICAiZW4iOiAi8J+aqyA8Yj5SZWplY3RlZDwvYj5cblxuUGxlYXNlIG1ha2Ugc3VyZSB5b3VyIHBheW1lbnQgU01TIGhh"
    "cyBhcnJpdmVkLCB0aGVuIHJlc2VuZCB0aGUgY29ycmVjdCBSZWZlcmVuY2UgSUQuXG7wn5SBIEF0dGVtcHRzIGxlZnQ6IHtyZW1h"
    "aW5pbmd9IiwKICAgICAgICAib20iOiAi8J+aqyA8Yj5EaWRkZWVyYSAoUmVqZWN0ZWQpPC9iPlxuXG5NYWFsb28gU01TIGthZmZh"
    "bHRpaSBrZWVzc2FuIGFra2EgZ2HKvGUgbWlya2FuZWVmZmFkaGFhdGlpIFJlZmVyZW5jZSBJRCBzaXJyaWkgZGVlYmlzYWEgZXJn"
    "YWFcbvCflIEgWWFhbGlpIGhhZmU6IHtyZW1haW5pbmd9IiwKICAgIH0sCiAgICAibm9fcHVyY2hhc2VfaW5fcHJvZ3Jlc3MiOiB7"
    "CiAgICAgICAgImFtIjogIuKEue+4jyDhiJjhjIDhiJjhiKrhi6sg4Ymy4Yqs4Ym1IOGLreGIneGIqOGMoeGKkyDhiq3hjY3hi6vh"
    "i43hipUg4Y2I4Yy94YiY4YuNIFJlZmVyZW5jZSBJRCDhi63hiIvhiqnhjaIiLAogICAgICAgICJlbiI6ICLihLnvuI8gUGxlYXNl"
    "IHBpY2sgYSB0aWNrZXQgZmlyc3QsIGNvbXBsZXRlIHBheW1lbnQsIHRoZW4gc2VuZCB0aGUgUmVmZXJlbmNlIElELiIsCiAgICAg"
    "ICAgIm9tIjogIuKEue+4jyBEdXJhYW4gZHVyc2FhIHRpa2VldGlpIGZpbGFkaGFhLCBrYWZmYWx0aWkgeHVtdXJhYSwgZXJnYXNp"
    "aSBSZWZlcmVuY2UgSUQgZXJnYWEuIiwKICAgIH0sCiAgICAicmVjZWlwdF9yZWNlaXZlZCI6IHsKICAgICAgICAiYW0iOiAi4pyF"
    "IOGLqOGKreGNjeGLqyDhi7DhiKjhiLDhip3hi44gc2NyZWVuc2hvdCDhibDhiYDhiaXhiIjhipPhiI3hjaJcbvCflIQg4Yqt4Y2N"
    "4Yur4YuNIOGJoFNNUyBhdXRvLXZlcmlmeSDhiLLhiKjhjIvhjIjhjKUg4YuI4Yut4YidIOGJoOGKoOGLteGImuGKlSDhiLLhjLjh"
    "i7XhiYUg4Yql4YqT4Yiz4YuN4YmF4YuO4Ymz4YiI4YqV4Y2iIiwKICAgICAgICAiZW4iOiAi4pyFIFdlJ3ZlIHJlY2VpdmVkIHlv"
    "dXIgcGF5bWVudCByZWNlaXB0IHNjcmVlbnNob3QuXG7wn5SEIFdlJ2xsIG5vdGlmeSB5b3Ugb25jZSBwYXltZW50IGlzIHZlcmlm"
    "aWVkIGJ5IFNNUyBhdXRvLXZlcmlmeSBvciBhcHByb3ZlZCBieSB0aGUgYWRtaW4uIiwKICAgICAgICAib20iOiAi4pyFIEZha2tp"
    "aSByYWdhYSBrYWZmYWx0aWkga2Vlc3NhbmlpIGZ1ZGhhbm5lZXJyYS5cbvCflIQgWWVyb28ga2FmZmFsdGlpbiBTTVMtdGlpbiBt"
    "aXJrYW5hYSd1IHlrbiBhZG1pbmlpbiByYWdnYWFzaWZhbXUgaXNpbiBiZWVrc2lmbmEuIiwKICAgIH0sCiAgICAicGVuZGluZ19u"
    "b3RpY2UiOiB7CiAgICAgICAgImFtIjogIuKPsyA8Yj7hi6vhiI3hibDhjKDhipPhiYDhiYAg4YyN4YuiIOGKoOGIiOGLjuGJtTwv"
    "Yj5cblxu8J+UoiDhiYHhjKXhiK0o4YuO4Ym9KSB7dG5zfSAoe3JsYWJlbH0pIOGImOGIreGMoOGLjSDhjIjhipMg4Yqt4Y2N4Yur"
    "4YuN4YqVIOGKoOGIi+GMoOGKk+GJgOGJgeGIneGNolxuXG7wn5OMIOGKpeGJo+GKreGLjiDhiaDhiJjhjIDhiJjhiKrhi6sg4Yqo"
    "4Yia4Yqo4Ymw4YiJ4Ym1IOGKoOGKleGLseGKlSDhi6vhi7XhiK3hjInhjaZcbuKAoiDhiq3hjY3hi6vhi43hipUg4Y2I4Yy94YiY"
    "4YuNIFJlZmVyZW5jZSBJRCDhi63hiIvhiqkgKOGMjeGLouGLjeGKlSDhiIjhiJvhjKDhipPhiYDhiYUp4Y2jIOGLiOGLreGInVxu"
    "4oCiIOGKqOGJs+GJvSDhi6vhiIjhi43hipUg4Ymw4Yyt4YqQ4YuNIOGLreGIheGKlS/hip7hibnhipUg4YmB4Yyl4YitKOGLjuGJ"
    "vSkg4Yut4Yiw4Yit4YuZ4Y2jIOGKqOGLmuGLqyDhiIzhiIsg4Ym14YuV4Yub4YudIOGLreGMoOGJgOGImeGNoiIsCiAgICAgICAg"
    "ImVuIjogIuKPsyA8Yj5Zb3UgaGF2ZSBhbiB1bmZpbmlzaGVkIHB1cmNoYXNlPC9iPlxuXG7wn5SiIFlvdSBwaWNrZWQgdGlja2V0"
    "KHMpICN7dG5zfSAoe3JsYWJlbH0pIGJ1dCBoYXZlbid0IGNvbXBsZXRlZCBwYXltZW50IHlldC5cblxu8J+TjCBQbGVhc2UgZG8g"
    "b25lIG9mIHRoZSBmb2xsb3dpbmcgZmlyc3Q6XG7igKIgQ29tcGxldGUgcGF5bWVudCBhbmQgc2VuZCB0aGUgUmVmZXJlbmNlIElE"
    "LCBvclxu4oCiIFRhcCBiZWxvdyB0byBjYW5jZWwgdGhlc2UgdGlja2V0KHMpLCB0aGVuIHVzZSBhbm90aGVyIGNvbW1hbmQuIiwK"
    "ICAgICAgICAib20iOiAi4o+zIDxiPkJpdHRhYSBoaW4geHVtdXJhbWluIHFhYmR1PC9iPlxuXG7wn5SiIExha2tvb2ZzYSh3d2Fu"
    "KSAje3Ruc30gKHtybGFiZWx9KSBmaWxhdHRhbmlpIGthZmZhbHRpaSBoaW4geHVtdXJyZS5cblxu8J+TjCBNYWFsb28gdG9ra28g"
    "a2Vlc3NhIGlzYWFuIGFybWFhbiBnYWRpaSBkdXJzYWEgcmFhd3dhZGhhYTpcbuKAoiBLYWZmYWx0aWkgeHVtdXJhYXRpaSBSZWZl"
    "cmVuY2UgSUQgZXJnYWEsIHlvb2thYW5cbuKAoiBHYWRpaSB0dXFhYXRpaSBsYWtrb29mc2Eod3dhbikga2FuYSBoYXFhYSwgZXJn"
    "YXNpaSBhamFqYSBiaXJhYSBmYXl5YWRhbWFhLiIsCiAgICB9LAogICAgInBlbmRpbmdfbm90aWNlX2V4cGlyeSI6IHsKICAgICAg"
    "ICAiYW0iOiAiXG5cbuKPse+4jyDhi6jhiYDhiKgg4YyK4Yuc4Y2mIH57bWluc30g4Yuw4YmC4YmDIiwKICAgICAgICAiZW4iOiAi"
    "XG5cbuKPse+4jyBUaW1lIGxlZnQ6IH57bWluc30gbWluIiwKICAgICAgICAib20iOiAiXG5cbuKPse+4jyBZZXJvbyBoYWZlOiB+"
    "e21pbnN9IGRhcWlpcWFhIiwKICAgIH0sCiAgICAiYnRuX3JldHVybl9wdXJjaGFzZSI6IHsiYW0iOiAi8J+UmSDhjI3hi6Lhi43h"
    "ipUg4YmA4Yyl4YiNIiwgImVuIjogIvCflJkgQ29udGludWUgUHVyY2hhc2UiLCAib20iOiAi8J+UmSBCaXR0YWEgSXR0aSBGdWZp"
    "In0sCiAgICAiYnRuX2NhbmNlbF90aWNrZXQiOiB7ImFtIjogIuKdjCDhi63hiIXhipUg4YmB4Yyl4YitIOGIsOGIreGLnSIsICJl"
    "biI6ICLinYwgQ2FuY2VsIFRoaXMgVGlja2V0IiwgIm9tIjogIuKdjCBMYWtrb29mc2EgS2FuYSBIYXFpIn0sCiAgICAiY2FuY2Vs"
    "X2NvbmZpcm1lZCI6IHsKICAgICAgICAiYW0iOiAi8J+aqyDhiYHhjKXhiK0o4YuO4Ym9KSB7dG5zfSAoe3JsYWJlbH0pIOGIneGI"
    "reGMq+GLjiDhibDhiLDhiK3hi5/hiI3hjaMg4YmB4Yyl4Yiu4Ym5IOGIiOGIjOGIjuGJvSDhibDhiIjhiYDhiYHhjaIiLAogICAg"
    "ICAgICJlbiI6ICLwn5qrIFlvdXIgc2VsZWN0aW9uIG9mIHRpY2tldChzKSAje3Ruc30gKHtybGFiZWx9KSBoYXMgYmVlbiBjYW5j"
    "ZWxsZWQ7IHRoZXkncmUgbm93IGF2YWlsYWJsZSB0byBvdGhlcnMuIiwKICAgICAgICAib20iOiAi8J+aqyBGaWxhbm5vb24ga2Vl"
    "c3NhbiBsYWtrb29mc2Eod3dhbikgI3t0bnN9ICh7cmxhYmVsfSkgaGFxYW1lZXJhOyBhbW1hcyB3YXJyYSBrYWFuaWlmIGJhbmFh"
    "ZGhhLiIsCiAgICB9LAogICAgImNhbmNlbF9waWNrX2FnYWluIjogewogICAgICAgICJhbSI6ICLwn5GHIOGKqOGNiOGIiOGMiSDh"
    "iqjhibPhib0g4Ymj4YiI4YuNIOGInOGKkSDhi4jhi63hiJ0gL3BsYXkg4Yml4YiI4YuNIOGKpeGKleGLsOGMiOGKkyDhiYHhjKXh"
    "iK0g4Yut4Yid4Yio4Yyh4Y2iIiwKICAgICAgICAiZW4iOiAi8J+RhyBJZiB5b3UnZCBsaWtlLCB1c2UgdGhlIG1lbnUgYmVsb3cg"
    "b3IgL3BsYXkgdG8gcGljayBhIG51bWJlciBhZ2Fpbi4iLAogICAgICAgICJvbSI6ICLwn5GHIFlvbyBiYXJiYWFkZGFuIG1lbnUg"
    "Z2FkaWkgeWtuIC9wbGF5IGZheXlhZGFtYWF0aWkgYW1tYXMgbGFra29vZnNhIGZpbGFkaGFhLiIsCiAgICB9LAogICAgImFscmVh"
    "ZHlfY2FuY2VsbGVkX29yX2V4cGlyZWQiOiB7CiAgICAgICAgImFtIjogIuKEue+4jyDhi63hiIUg4Yid4Yit4YyrIOGKqOGLmuGI"
    "hSDhiaDhjYrhibUg4Ymw4Yiw4Yit4Yuf4YiNIOGLiOGLreGInSDhjIrhi5zhi40g4Yqg4YiN4Y2O4Ymg4Ymz4YiN4Y2iIiwKICAg"
    "ICAgICAiZW4iOiAi4oS577iPIFRoaXMgc2VsZWN0aW9uIHdhcyBhbHJlYWR5IGNhbmNlbGxlZCBvciBoYXMgZXhwaXJlZC4iLAog"
    "ICAgICAgICJvbSI6ICLihLnvuI8gRmlsYW5ub29uIGt1biBkdXJhYW4gaGFxYW1lZXJhIHlvb2thYW4geWVyb29uIGlzYWEgZGFi"
    "cmVlcmEuIiwKICAgIH0sCiAgICAidmVyaWZpZWRfc3VjY2VzcyI6IHsKICAgICAgICAiYW0iOiAi8J+OiSDhiJjhiI3hiqvhiJ0g"
    "4Yuc4YqTISDhi6jhiqjhjYjhiInhiaDhibUg4Yqt4Y2N4YurIOGJsOGIqOGMi+GMjeGMpiDhiYHhjKXhiK0o4YuO4Ym9KSB7dG5z"
    "fSAo4YuZ4YitIHtyaWR9KSDhiaDhiYvhiJrhipDhibUg4YiI4Yql4YqT4YqV4YmwIOGJsOGImOGLneGMjeGJoOGLi+GIjeGNoiDh"
    "iJjhiI3hiqvhiJ0g4Yql4Yu14YiNISIsCiAgICAgICAgImVuIjogIvCfjokgR29vZCBuZXdzISBZb3VyIHBheW1lbnQgaGFzIGJl"
    "ZW4gdmVyaWZpZWQgYW5kIHRpY2tldChzKSAje3Ruc30gKHJvdW5kIHtyaWR9KSBhcmUgbm93IHBlcm1hbmVudGx5IHlvdXJzLiBH"
    "b29kIGx1Y2shIiwKICAgICAgICAib20iOiAi8J+OiSBPZHV1IGdhYXJpaSEgS2FmZmFsdGlpbiBrZWVzc2FuIG1pcmthbmFhJ2Vl"
    "IGxha2tvb2ZzYSh3d2FuKSAje3Ruc30gKG1hcnNhYSB7cmlkfSkgeWVyb28gaHVuZGFhZiBpc2luaWlmIGdhbG1hYSdlZXJhLiBN"
    "aWxrYWEnaW5hISIsCiAgICB9LAogICAgInJvdW5kX2Nsb3NlZCI6IHsKICAgICAgICAiYW0iOiAi4p2MIOGLreGIhSDhi5nhiK0g"
    "4Ymw4YuY4YyN4Ym34YiN4Y2iIC9yb3VuZHMg4Yml4YiI4YuNIOGKleGJgSDhi5nhiK7hib3hipUg4Yut4YiY4YiN4Yqo4Ymx4Y2i"
    "IiwKICAgICAgICAiZW4iOiAi4p2MIFRoaXMgcm91bmQgaXMgY2xvc2VkLiBUeXBlIC9yb3VuZHMgdG8gc2VlIGFjdGl2ZSByb3Vu"
    "ZHMuIiwKICAgICAgICAib20iOiAi4p2MIE1hcnNhYW4ga3VuIGN1ZmFtZWVyYS4gTWFyc2Fhd3dhbiBob2ppaXJyYSBqaXJhbiBp"
    "bGFhbHV1ZiAvcm91bmRzIGplZGhhYSBiYXJyZWVzc2FhLiIsCiAgICB9LAogICAgInRpY2tldF9hbHJlYWR5X3NvbGQiOiB7CiAg"
    "ICAgICAgImFtIjogIuKdjCDhi63hiYXhiK3hibMg4YmB4Yyl4YitIHt0bn0gKOGLmeGIrSB7cmlkfSkg4Yqg4Yi14YmA4Yu14Yie"
    "IOGJsOGIveGMp+GIjeGNoiDhiqXhiaPhiq3hi44gL3BsYXkge3JpZH0g4Yml4YiI4YuNIOGIjOGIiyDhi63hiJ3hiKjhjKHhjaIi"
    "LAogICAgICAgICJlbiI6ICLinYwgU29ycnksIHRpY2tldCAje3RufSAocm91bmQge3JpZH0pIGhhcyBhbHJlYWR5IGJlZW4gc29s"
    "ZC4gUGxlYXNlIHRyeSAvcGxheSB7cmlkfSB0byBwaWNrIGFub3RoZXIuIiwKICAgICAgICAib20iOiAi4p2MIERoaWlmYW1hLCBs"
    "YWtrb29mc2kgI3t0bn0gKG1hcnNhYSB7cmlkfSkgZHVyYWFuIGd1cmd1cmFtZWVyYS4gTWFhbG9vIC9wbGF5IHtyaWR9IGplZGhh"
    "YSBrYW4gYmlyYWEgZmlsYWRoYWEuIiwKICAgIH0sCiAgICAidGlja2V0X3Rha2VuX2J5X290aGVyIjogewogICAgICAgICJhbSI6"
    "ICLij7Mg4YmB4Yyl4YitIHt0bn0g4Yqg4YiB4YqVIOGJoOGIjOGIiyDhibDhjKvhi4vhib0g4Ymw4Yut4Yuf4YiN4Y2iXG5cbvCf"
    "lJQg4Yqo4Y2I4YiI4YyJIOGLreGIheGKlSDhiYHhjKXhiK0g4Yut4Yyg4Yml4YmB4Y2kIOGKqOGJsOGIiOGJgOGJgCDhi4jhi7Lh"
    "i6vhi43hipEg4Yql4YqT4Yiz4YuN4YmF4YuO4Ymz4YiI4YqV4Y2iIiwKICAgICAgICAiZW4iOiAi4o+zIFRpY2tldCAje3RufSBp"
    "cyBjdXJyZW50bHkgaGVsZCBieSBhbm90aGVyIHBsYXllci5cblxu8J+UlCBJZiB5b3UnZCBsaWtlLCB5b3UgY2FuIHdhaXQgZm9y"
    "IGl0OyB3ZSdsbCBub3RpZnkgeW91IGFzIHNvb24gYXMgaXQncyByZWxlYXNlZC4iLAogICAgICAgICJvbSI6ICLij7MgTGFra29v"
    "ZnNpICN7dG59IHllcm9vIGFtbWFhIHRhcGhhdGFhIGJpcmFhdGlpbiBxYWJhbWVlcmEuXG5cbvCflJQgWW9vIGJhcmJhYWRkYW4g"
    "ZWVnYWE7IHllcm9vIGdhZGkgbGFra2lmYW11IGJhdHRhbHVtYXR0aSBpc2luIGJlZWtzaWZuYS4iLAogICAgfSwKICAgICJ3YXRj"
    "aF9idXR0b24iOiB7ImFtIjogIvCflJQg4YmB4Yyl4Yip4YqVIOGKpeGMoOGJpeGJg+GIiOGIgSIsICJlbiI6ICLwn5SUIE5vdGlm"
    "eSBtZSB3aGVuIGZyZWUiLCAib20iOiAi8J+UlCBZZXJvbyBiYW5hYSB0YSd1IG5hYWYgaGltYWEifSwKICAgICJ0aWNrZXRfaGVs"
    "ZCI6IHsKICAgICAgICAiYW0iOiAi4pyFIDxiPuGJgeGMpeGIrSB7dG59PC9iPiAo4YuZ4YitIHtyaWR9KSDhiIgge21pbnN9IOGL"
    "sOGJguGJgyDhibDhi63hi57hiI3hi47hibPhiI3hjaJcblxu8J+StSA8Yj7hi6jhibXhiqzhibUg4YuL4YyL4Y2mPC9iPiB7cHJp"
    "Y2U6LjBmfSDhiaXhiK1cblxu8J+SsyDhiqXhiaPhiq3hi44g4Yuo4Yia4Yqo4Y2N4YiJ4Ymg4Ym14YqVIOGLqOGKreGNjeGLqyDh"
    "i5jhi7Qg4Yut4Yid4Yio4Yyh4Y2mIiwKICAgICAgICAiZW4iOiAi4pyFIDxiPlRpY2tldCAje3RufTwvYj4gKHJvdW5kIHtyaWR9"
    "KSBpcyBoZWxkIGZvciB5b3UgZm9yIHttaW5zfSBtaW51dGVzLlxuXG7wn5K1IDxiPlRpY2tldCBwcmljZTo8L2I+IHtwcmljZTou"
    "MGZ9IGJpcnJcblxu8J+SsyBQbGVhc2UgY2hvb3NlIGhvdyB5b3UnZCBsaWtlIHRvIHBheToiLAogICAgICAgICJvbSI6ICLinIUg"
    "PGI+TGFra29vZnNpICN7dG59PC9iPiAobWFyc2FhIHtyaWR9KSBkYXFpaXFhYSB7bWluc30gaXNpbmlpZiBxYWJhbWVlcmEuXG5c"
    "bvCfkrUgPGI+R2F0aWkgdGlrZWV0aWk6PC9iPiB7cHJpY2U6LjBmfSBiaXJyaWlcblxu8J+SsyBNYWFsb28gbWFsYSBrYWZmYWx0"
    "aWkgZmlsYWRoYWE6IiwKICAgIH0sCiAgICAicGF5bWVudF9tZXRob2RfcHJvbXB0IjogewogICAgICAgICJhbSI6ICLinIUgPGI+"
    "4YmB4Yyl4YitIHt0bn08L2I+ICjhi5nhiK0ge3JpZH0pXG5cbvCfkrUgPGI+4Yuo4Ym14Yqs4Ym1IOGLi+GMi+GNpjwvYj4ge3By"
    "aWNlOi4wZn0g4Yml4YitXG5cbvCfkrMg4Yql4Ymj4Yqt4YuOIOGLqOGImuGKqOGNjeGIieGJoOGJteGKlSDhi6jhiq3hjY3hi6sg"
    "4YuY4Yu0IOGLreGIneGIqOGMoeGNpiIsCiAgICAgICAgImVuIjogIuKchSA8Yj5UaWNrZXQgI3t0bn08L2I+IChyb3VuZCB7cmlk"
    "fSlcblxu8J+StSA8Yj5UaWNrZXQgcHJpY2U6PC9iPiB7cHJpY2U6LjBmfSBiaXJyXG5cbvCfkrMgUGxlYXNlIGNob29zZSBob3cg"
    "eW91J2QgbGlrZSB0byBwYXk6IiwKICAgICAgICAib20iOiAi4pyFIDxiPkxha2tvb2ZzaSAje3RufTwvYj4gKG1hcnNhYSB7cmlk"
    "fSlcblxu8J+StSA8Yj5HYXRpaSB0aWtlZXRpaTo8L2I+IHtwcmljZTouMGZ9IGJpcnJpaVxuXG7wn5KzIE1hYWxvbyBtYWxhIGth"
    "ZmZhbHRpaSBmaWxhZGhhYToiLAogICAgfSwKICAgICJtYW51YWxfY2FsbF9jYXB0aW9uIjogewogICAgICAgICJhbSI6ICLwn5Oe"
    "IDxiPuGLsOGLieGIiOGLiSDhi6vhiLLhi5k8L2I+XG5cbvCfk7Eg4Yuo4Yqg4Yu14Yia4YqVIOGIteGIjeGKrTogPGNvZGU+e2Fj"
    "Y291bnR9PC9jb2RlPlxuXG7ij7HvuI8g4YmB4Yyl4YitKOGLjuGJvSkge251bXN9ICh7cmxhYmVsfSkg4Ymw4Yut4YuY4YuN4YiN"
    "4YuO4Ymz4YiN4Y2jIOGKpeGJo+GKreGLjiDhi63hi7Dhi43hiInhjaIiLAogICAgICAgICJlbiI6ICLwn5OeIDxiPkNhbGwgdG8g"
    "cmVzZXJ2ZTwvYj5cblxu8J+TsSBBZG1pbiBwaG9uZTogPGNvZGU+e2FjY291bnR9PC9jb2RlPlxuXG7ij7HvuI8gVGlja2V0KHMp"
    "ICN7bnVtc30gKHtybGFiZWx9KSBhcmUgaGVsZCBmb3IgeW91IOKAlCBwbGVhc2UgY2FsbC4iLAogICAgICAgICJvbSI6ICLwn5Oe"
    "IDxiPkJpbGJpbHV1biBxYWJhZGhhYTwvYj5cblxu8J+TsSBCaWxiaWxhIGFkbWluOiA8Y29kZT57YWNjb3VudH08L2NvZGU+XG5c"
    "buKPse+4jyBMYWtrb29mc2Eod3dhbikgI3tudW1zfSAoe3JsYWJlbH0pIGlzaW5paWYgcWFiYW1hbmlpcnUg4oCUIG1hYWxvbyBi"
    "aWxiaWxhYS4iLAogICAgfSwKICAgICJwYXltZW50X2RldGFpbHNfY2FwdGlvbiI6IHsKICAgICAgICAiYW0iOiAie2Vtb2ppfSA8"
    "Yj57bGFiZWx9PC9iPlxuXG7ij7HvuI8gPGI+4Yuo4YmA4YioIOGMiuGLnOGNpjwvYj4gfnttaW5zfSDhi7DhiYLhiYNcblxu8J+S"
    "tSA8Yj7hi6jhiJrhiqjhjY3hiInhibUg4Yyg4YmF4YiL4YiLIOGImOGMoOGKleGNpjwvYj4ge3ByaWNlOi4wZn0g4Yml4YitXG7w"
    "n5GkIDxiPuGLqOGKoOGKq+GLjeGKleGJtSDhiLXhiJ3hjaY8L2I+IHtob2xkZXJ9XG7wn5SiIDxiPuGLqOGKoOGKq+GLjeGKleGJ"
    "tS/hiLXhiI3hiq0g4YmB4Yyl4Yit4Y2mPC9iPiA8Y29kZT57YWNjb3VudH08L2NvZGU+XG5cbvCfk4wg4Yqt4Y2N4YurIOGKqOGN"
    "iOGMuOGImSDhiaDhiovhiIsgUmVmZXJlbmNlIElEIOGLreGIi+GKqeGNolxu8J+TuCBSZWZlcmVuY2UgSUQg4Yqo4YiL4YqpIOGJ"
    "oOGKi+GIiyDhi6jhi7DhiKjhiLDhip0gc2NyZWVuc2hvdCDhiqXhipXhi7LhiIEg4Yut4YiL4Yqp4Y2iIiwKICAgICAgICAiZW4i"
    "OiAie2Vtb2ppfSA8Yj57bGFiZWx9PC9iPlxuXG7ij7HvuI8gPGI+VGltZSBsZWZ0OjwvYj4gfnttaW5zfSBtaW5cblxu8J+StSA8"
    "Yj5Ub3RhbCBhbW91bnQgdG8gcGF5OjwvYj4ge3ByaWNlOi4wZn0gYmlyclxu8J+RpCA8Yj5BY2NvdW50IG5hbWU6PC9iPiB7aG9s"
    "ZGVyfVxu8J+UoiA8Yj5BY2NvdW50L3Bob25lIG51bWJlcjo8L2I+IDxjb2RlPnthY2NvdW50fTwvY29kZT5cblxu8J+TjCBBZnRl"
    "ciBwYXlpbmcsIHBsZWFzZSBzZW5kIHRoZSBSZWZlcmVuY2UgSUQuXG7wn5O4IEFmdGVyIHNlbmRpbmcgdGhlIFJlZmVyZW5jZSBJ"
    "RCwgYWxzbyBzZW5kIGEgc2NyZWVuc2hvdCBvZiB0aGUgcmVjZWlwdC4iLAogICAgICAgICJvbSI6ICJ7ZW1vaml9IDxiPntsYWJl"
    "bH08L2I+XG5cbuKPse+4jyA8Yj5ZZXJvbyBoYWZlOjwvYj4gfnttaW5zfSBkYXFpaXFhYVxuXG7wn5K1IDxiPldhbGlpZ2FsYSBr"
    "YWZmYWx0aWk6PC9iPiB7cHJpY2U6LjBmfSBiaXJyaWlcbvCfkaQgPGI+TWFxYWEgaGVycmVnYTo8L2I+IHtob2xkZXJ9XG7wn5Si"
    "IDxiPkxha2tvb2ZzYSBoZXJyZWdhL2JpbGJpbGFhOjwvYj4gPGNvZGU+e2FjY291bnR9PC9jb2RlPlxuXG7wn5OMIEthZmZhbHRp"
    "aSBib29kYSwgUmVmZXJlbmNlIElEIGVyZ2FhLlxu8J+TuCBSZWZlcmVuY2UgSUQgZXJnYSBlcmdpdGFuaWkgYm9vZGEsIGZha2tp"
    "aSByYWdhYSBrYWZmYWx0aWkgaWxsZWUgZXJnYWEuIiwKICAgIH0sCiAgICAicGxheV9waWNrX2hpbnQiOiB7CiAgICAgICAgImFt"
    "IjogIuGKpeGJo+GKreGLjiDhiqjhibPhib0g4Yqr4YiJ4Ym1IOGJgeGMpeGIruGJvSDhiqDhipXhi7Ug4YuI4Yut4YidIOGKqOGL"
    "muGLqyDhiaDhiIvhi60g4Yur4YiN4Ymw4Yur4YuZIOGJgeGMpeGIruGJvSDhi63hiJ3hiKjhjKEgKOGJpeGLmSDhiYHhjKXhiK0g"
    "4YiY4Yid4Yio4YylIOGLreGJveGIi+GIiSnhjaYiLAogICAgICAgICJlbiI6ICJQbGVhc2UgdGFwIG9uZSBvciBtb3JlIGF2YWls"
    "YWJsZSBudW1iZXJzIGJlbG93ICh5b3UgY2FuIHBpY2sgbW9yZSB0aGFuIG9uZSk6IiwKICAgICAgICAib20iOiAiTWFhbG9vIGxh"
    "a2tvb2ZzYSBiYW5hYSBnYWRpaSBrZWVzc2FhIHRva2tvIHlrbiBpc2FhIG9sIGZpbGFkaGFhIChsYWtrb29mc2EgdG9ra28gb2wg"
    "ZmlsYWNodXUgbmkgZGFuZGVlc3N1KToiLAogICAgfSwKICAgICJjYXJ0X3N1bW1hcnkiOiB7CiAgICAgICAgImFtIjogIlxuXG7w"
    "n5uSIDxiPntjb3VudH0g4YmB4Yyl4YitKOGLjuGJvSkg4Ymw4YiY4Yit4Yyg4YuL4YiN4Y2mPC9iPiB7bnVtc31cbvCfkrUgPGI+"
    "4Yyg4YmF4YiL4YiLIOGLteGIneGIreGNpjwvYj4ge3RvdGFsOi4wZn0g4Yml4YitIiwKICAgICAgICAiZW4iOiAiXG5cbvCfm5Ig"
    "PGI+e2NvdW50fSBudW1iZXIocykgc2VsZWN0ZWQ6PC9iPiB7bnVtc31cbvCfkrUgPGI+VG90YWw6PC9iPiB7dG90YWw6LjBmfSBi"
    "aXJyIiwKICAgICAgICAib20iOiAiXG5cbvCfm5IgPGI+TGFra29vZnNhIHtjb3VudH0gZmlsYXRhbWFuaWlydTo8L2I+IHtudW1z"
    "fVxu8J+StSA8Yj5XYWxpaWdhbGE6PC9iPiB7dG90YWw6LjBmfSBiaXJyaWkiLAogICAgfSwKICAgICJidG5fY2hlY2tvdXQiOiB7"
    "CiAgICAgICAgImFtIjogIuKchSDhiq3hjY3hi6sg4YmA4Yyl4YiNICh7Y291bnR9IOGJsuGKrOGJtSDigKIge3RvdGFsOi4wZn0g"
    "4Yml4YitKSIsCiAgICAgICAgImVuIjogIuKchSBDaGVja291dCAoe2NvdW50fSB0aWNrZXRzIOKAoiB7dG90YWw6LjBmfSBiaXJy"
    "KSIsCiAgICAgICAgIm9tIjogIuKchSBLYWZmYWx0aWl0dGkgY2XKvGkgKHtjb3VudH0gdGlrZWV0aWkg4oCiIHt0b3RhbDouMGZ9"
    "IGJpcnJpaSkiLAogICAgfSwKICAgICJidG5fY2xlYXJfY2FydCI6IHsiYW0iOiAi8J+XkSDhiJ3hiK3hjKsg4Yqg4Yy94YuzIiwg"
    "ImVuIjogIvCfl5EgQ2xlYXIgc2VsZWN0aW9uIiwgIm9tIjogIvCfl5EgRmlsYW5ub28gaGFxaSJ9LAogICAgImJ0bl9jb25maXJt"
    "X3NlbGVjdGlvbiI6IHsiYW0iOiAi4pyFIOGIneGIreGMqyDhiqDhiKjhjIvhjI3hjKUiLCAiZW4iOiAi4pyFIENvbmZpcm0gc2Vs"
    "ZWN0aW9uIiwgIm9tIjogIuKchSBGaWxhbm5vbyBtaXJrYW5lZXNzaSJ9LAogICAgImJ0bl9yZWplY3Rfc2VsZWN0aW9uIjogeyJh"
    "bSI6ICLinYwg4Yid4Yit4YyrIOGLjeGLteGJhSDhiqDhi7XhiK3hjI0iLCAiZW4iOiAi4p2MIFJlamVjdCBzZWxlY3Rpb24iLCAi"
    "b20iOiAi4p2MIEZpbGFubm9vIHR1ZmZhZGh1In0sCiAgICAiY29uZmlybV9zZWxlY3Rpb25fcHJvbXB0IjogewogICAgICAgICJh"
    "bSI6ICLwn5uSIDxiPuGJgeGMpeGIrSjhi47hib0pIHtudW1zfTwvYj4gKOGLmeGIrSB7cmlkfSkg4YiY4Yit4Yyg4YuL4YiN4Y2i"
    "XG5cbvCfjp/vuI8gPGI+4Yml4Yub4Ym14Y2mPC9iPiB7Y291bnR9XG7wn5K1IDxiPuGMoOGJheGIi+GIiyDhi4vhjIvhjaY8L2I+"
    "IHt0b3RhbDouMGZ9IOGJpeGIrVxuXG7hi63hiIXhipUg4Yid4Yit4YyrIOGIm+GIqOGMi+GMiOGMpSDhi63hjYjhiI3hjIvhiIk/"
    "IiwKICAgICAgICAiZW4iOiAi8J+bkiBZb3Ugc2VsZWN0ZWQgPGI+dGlja2V0KHMpIHtudW1zfTwvYj4gKHJvdW5kIHtyaWR9KS5c"
    "blxu8J+On++4jyA8Yj5Db3VudDo8L2I+IHtjb3VudH1cbvCfkrUgPGI+VG90YWwgcHJpY2U6PC9iPiB7dG90YWw6LjBmfSBiaXJy"
    "XG5cbkRvIHlvdSB3YW50IHRvIGNvbmZpcm0gdGhpcyBzZWxlY3Rpb24/IiwKICAgICAgICAib20iOiAi8J+bkiA8Yj5MYWtrb29m"
    "c2Eod3dhbikge251bXN9PC9iPiAobWFyc2FhIHtyaWR9KSBmaWxhdHRhbmlpcnUuXG5cbvCfjp/vuI8gPGI+QmFheSdpbmE6PC9i"
    "PiB7Y291bnR9XG7wn5K1IDxiPkdhdGlpIHdhbGlpZ2FsYWE6PC9iPiB7dG90YWw6LjBmfSBiaXJyaWlcblxuRmlsYW5ub28ga2Fu"
    "YSBtaXJrYW5lZXNzdXUgYmFyYmFhZGR1dT8iLAogICAgfSwKICAgICJzZWxlY3Rpb25fcmVqZWN0ZWRfYWxlcnQiOiB7CiAgICAg"
    "ICAgImFtIjogIuKdjCDhiJ3hiK3hjKvhi40g4YuN4Yu14YmFIOGJsOGLsOGIreGMk+GIjeGNoiIsCiAgICAgICAgImVuIjogIuKd"
    "jCBTZWxlY3Rpb24gcmVqZWN0ZWQuIiwKICAgICAgICAib20iOiAi4p2MIEZpbGFubm9vbiB0dWZmYXRhbWVlcmEuIiwKICAgIH0s"
    "CiAgICAiY2FydF9lbXB0eV9hbGVydCI6IHsKICAgICAgICAiYW0iOiAi4pqg77iPIOGKpeGJo+GKreGLjiDhiJjhjIDhiJjhiKrh"
    "i6sg4Ymi4Yur4YqV4Yi1IOGKoOGKleGLtSDhiYHhjKXhiK0g4Yut4Yid4Yio4Yyh4Y2iIiwKICAgICAgICAiZW4iOiAi4pqg77iP"
    "IFBsZWFzZSBzZWxlY3QgYXQgbGVhc3Qgb25lIG51bWJlciBmaXJzdC4iLAogICAgICAgICJvbSI6ICLimqDvuI8gTWFhbG9vIGR1"
    "cmFhbiBkdXJzYWEgbGFra29vZnNhIHRva2tvIGZpbGFkaGFhLiIsCiAgICB9LAogICAgImNhcnRfYWxsX3Rha2VuIjogewogICAg"
    "ICAgICJhbSI6ICLinYwg4Yut4YmF4Yit4YmzIOGLqOGImOGIqOGMoeGJtSDhiYHhjKXhiK0o4YuO4Ym9KSDhiI3hiq0g4Yqg4YiB"
    "4YqVIOGJoOGIjOGIiyDhibDhjKvhi4vhib0g4Ymw4Yut4YuY4YuL4YiNL+GJsOGIuOGMoOGLi+GIjeGNoiDhiqXhiaPhiq3hi44g"
    "L3BsYXkg4Yml4YiI4YuNIOGKpeGKleGLsOGMiOGKkyDhi63hiJ7hiq3hiKnhjaIiLAogICAgICAgICJlbiI6ICLinYwgU29ycnks"
    "IHRoZSBudW1iZXIocykgeW91IHBpY2tlZCB3ZXJlIGp1c3QgdGFrZW4vc29sZCB0byBzb21lb25lIGVsc2UuIFBsZWFzZSB0cnkg"
    "L3BsYXkgYWdhaW4uIiwKICAgICAgICAib20iOiAi4p2MIERoaWlmYW1hLCBsYWtrb29mc2kgZmlsYXR0YW4geWVyb28gYW1tYWEg"
    "dGFwaGF0YWEgYmlyYWF0aWluIHFhYmFtZS9ndXJndXJhbWUuIE1hYWxvbyAvcGxheSBhbW1hcyB5YWFsYWEuIiwKICAgIH0sCiAg"
    "ICAic29tZV91bmF2YWlsYWJsZV9ub3RlIjogewogICAgICAgICJhbSI6ICJcblxu4pqg77iPIOGIm+GIteGJs+GLiOGIu+GNpiDh"
    "iqjhiJjhiKjhjKHhibUg4YuN4Yi14YylIOGJgeGMpeGIrSjhi47hib0pIHtudW1zfSDhiI3hiq0g4Yqg4YiB4YqVIOGJoOGIjOGI"
    "iyDhibDhjKvhi4vhib0g4Yi14YiI4Ymw4Yur4YuZIOGKoOGIjeGJsOGKq+GJsOGJseGIneGNoiIsCiAgICAgICAgImVuIjogIlxu"
    "XG7imqDvuI8gTm90ZTogbnVtYmVyKHMpIHtudW1zfSBmcm9tIHlvdXIgcGljayB3ZXJlIGp1c3QgdGFrZW4gYnkgc29tZW9uZSBl"
    "bHNlIGFuZCBhcmUgbm90IGluY2x1ZGVkLiIsCiAgICAgICAgIm9tIjogIlxuXG7imqDvuI8gSHViYWNoaWlzYTogbGFra29vZnNh"
    "IHtudW1zfSBmaWxhdGFuIGtlZXNzYWEgdGFwaGF0YWEgYmlyYWF0aWluIHFhYmFtYW5paSBoaW4gZGFiYWxhbW5lLiIsCiAgICB9"
    "LAogICAgInRpY2tldHNfaGVsZCI6IHsKICAgICAgICAiYW0iOiAi4pyFIDxiPuGJgeGMpeGIrSjhi47hib0pIHtudW1zfTwvYj4g"
    "KOGLmeGIrSB7cmlkfSkg4YiIIHttaW5zfSDhi7DhiYLhiYMg4Ymw4Yut4YuY4YuN4YiN4YuO4Ymz4YiN4Y2iXG5cbvCfjp/vuI8g"
    "PGI+4Yml4Yub4Ym14Y2mPC9iPiB7Y291bnR9XG7wn5K1IDxiPuGMoOGJheGIi+GIiyDhi4vhjIvhjaY8L2I+IHt0b3RhbDouMGZ9"
    "IOGJpeGIrVxuXG7wn5KzIOGKpeGJo+GKreGLjiDhi6jhiJrhiqjhjY3hiInhiaDhibXhipUg4Yuo4Yqt4Y2N4YurIOGLmOGLtCDh"
    "i63hiJ3hiKjhjKHhjaYiLAogICAgICAgICJlbiI6ICLinIUgPGI+VGlja2V0KHMpIHtudW1zfTwvYj4gKHJvdW5kIHtyaWR9KSBh"
    "cmUgaGVsZCBmb3IgeW91IGZvciB7bWluc30gbWludXRlcy5cblxu8J+On++4jyA8Yj5Db3VudDo8L2I+IHtjb3VudH1cbvCfkrUg"
    "PGI+VG90YWwgcHJpY2U6PC9iPiB7dG90YWw6LjBmfSBiaXJyXG5cbvCfkrMgUGxlYXNlIGNob29zZSBob3cgeW91J2QgbGlrZSB0"
    "byBwYXk6IiwKICAgICAgICAib20iOiAi4pyFIDxiPkxha2tvb2ZzYSh3d2FuKSB7bnVtc308L2I+IChtYXJzYWEge3JpZH0pIGRh"
    "cWlpcWFhIHttaW5zfSBpc2luaWlmIHFhYmFtYW5paXJ1LlxuXG7wn46f77iPIDxiPkJhYXknaW5hOjwvYj4ge2NvdW50fVxu8J+S"
    "tSA8Yj5HYXRpaSB3YWxpaWdhbGFhOjwvYj4ge3RvdGFsOi4wZn0gYmlycmlpXG5cbvCfkrMgTWFhbG9vIG1hbGEga2FmZmFsdGlp"
    "IGZpbGFkaGFhOiIsCiAgICB9LAogICAgIm11bHRpX3BheW1lbnRfbWV0aG9kX3Byb21wdCI6IHsKICAgICAgICAiYW0iOiAi4pyF"
    "IDxiPuGJgeGMpeGIrSjhi47hib0pIHtudW1zfTwvYj4gKOGLmeGIrSB7cmlkfSlcblxu8J+On++4jyA8Yj7hiaXhi5vhibXhjaY8"
    "L2I+IHtjb3VudH1cbvCfkrUgPGI+4Yyg4YmF4YiL4YiLIOGLi+GMi+GNpjwvYj4ge3RvdGFsOi4wZn0g4Yml4YitXG5cbvCfkrMg"
    "4Yql4Ymj4Yqt4YuOIOGLqOGImuGKqOGNjeGIieGJoOGJteGKlSDhi6jhiq3hjY3hi6sg4YuY4Yu0IOGLreGIneGIqOGMoeGNpiIs"
    "CiAgICAgICAgImVuIjogIuKchSA8Yj5UaWNrZXQocykge251bXN9PC9iPiAocm91bmQge3JpZH0pXG5cbvCfjp/vuI8gPGI+Q291"
    "bnQ6PC9iPiB7Y291bnR9XG7wn5K1IDxiPlRvdGFsIHByaWNlOjwvYj4ge3RvdGFsOi4wZn0gYmlyclxuXG7wn5KzIFBsZWFzZSBj"
    "aG9vc2UgaG93IHlvdSdkIGxpa2UgdG8gcGF5OiIsCiAgICAgICAgIm9tIjogIuKchSA8Yj5MYWtrb29mc2Eod3dhbikge251bXN9"
    "PC9iPiAobWFyc2FhIHtyaWR9KVxuXG7wn46f77iPIDxiPkJhYXknaW5hOjwvYj4ge2NvdW50fVxu8J+StSA8Yj5HYXRpaSB3YWxp"
    "aWdhbGFhOjwvYj4ge3RvdGFsOi4wZn0gYmlycmlpXG5cbvCfkrMgTWFhbG9vIG1hbGEga2FmZmFsdGlpIGZpbGFkaGFhOiIsCiAg"
    "ICB9LAogICAgInBtX3RlbGViaXJyIjogeyJhbSI6ICLwn5OxIOGJtOGIjCDhiaXhiK0gKFRlbGVCaXJyKSIsICJlbiI6ICLwn5Ox"
    "IFRlbGVCaXJyIiwgIm9tIjogIvCfk7EgVGVsZUJpcnIifSwKICAgICJwbV9jYmViaXJyIjogeyJhbSI6ICLwn5KaIOGIsuGJouGK"
    "oiDhiaXhiK0gKENCRSBCaXJyKSIsICJlbiI6ICLwn5KaIENCRSBCaXJyIiwgIm9tIjogIvCfkpogQ0JFIEJpcnIifSwKICAgICJw"
    "bV9tYW51YWxfY2FsbCI6IHsiYW0iOiAi8J+TniDhi7Dhi4nhiIjhi4kg4Yur4Yiy4YuZIiwgImVuIjogIvCfk54gQ2FsbCB0byBy"
    "ZXNlcnZlIiwgIm9tIjogIvCfk54gQmlsYmlsdXVuIHFhYmFkaGFhIn0sCiAgICAid2F0Y2hfY29uZmlybWVkX2FsZXJ0IjogeyJh"
    "bSI6ICLwn5SUIOGKpeGKk+GIs+GLjeGJheGLjuGJs+GIiOGKleGNoiIsICJlbiI6ICLwn5SUIFdlJ2xsIG5vdGlmeSB5b3UuIiwg"
    "Im9tIjogIvCflJQgSXNpbiBiZWVrc2lmbmEuIn0sCiAgICAid2F0Y2hfY29uZmlybWVkX3RleHQiOiB7CiAgICAgICAgImFtIjog"
    "IvCflJQg4YmB4Yyl4YitIHt0bn0gKHtybGFiZWx9KSDhiqXhipXhi7DhibDhiIjhiYDhiYAg4YuI4Yuy4Yur4YuN4YqRIOGKpeGK"
    "k+GIs+GLjeGJheGLjuGJs+GIiOGKleGNoiIsCiAgICAgICAgImVuIjogIvCflJQgV2UnbGwgbm90aWZ5IHlvdSBhcyBzb29uIGFz"
    "IHRpY2tldCAje3RufSAoe3JsYWJlbH0pIGlzIHJlbGVhc2VkLiIsCiAgICAgICAgIm9tIjogIvCflJQgTGFra29vZnNpICN7dG59"
    "ICh7cmxhYmVsfSkgeWVyb28gZ2FkaSBsYWtraWZhbXUgYmF0dGFsdW1hdHRpIGlzaW4gYmVla3NpZm5hLiIsCiAgICB9LAogICAg"
    "InBheW1lbnRfa2VwdF9hbGVydCI6IHsiYW0iOiAi4Yql4YqV4Yuw4Ymw4YiY4Yio4Yyg4YuNIOGJsOGJgOGIneGMp+GIjeGNoiIs"
    "ICJlbiI6ICJLZXB0IGFzIHNlbGVjdGVkLiIsICJvbSI6ICJBa2t1bWEgZmlsYXRhbWV0dGkgZWVnYW1lZXJhLiJ9LAogICAgInBh"
    "eW1lbnRfa2VwdF90ZXh0IjogewogICAgICAgICJhbSI6ICLinIUg4Yuo4Yqt4Y2N4YurIOGLmOGLtOGLjiB7bGFiZWx9IOGKpeGK"
    "leGLsOGJsOGImOGIqOGMoCDhibDhiYDhiJ3hjKfhiI3hjaJcblxu8J+TjCDhiqXhiaPhiq3hi44g4Yqt4Y2N4Yur4YuN4YqVIOGN"
    "iOGMveGImOGLjSBSZWZlcmVuY2UgSUQg4Yut4YiL4Yqp4Y2iIiwKICAgICAgICAiZW4iOiAi4pyFIFlvdXIgcGF5bWVudCBtZXRo"
    "b2QgKHtsYWJlbH0pIGhhcyBiZWVuIGtlcHQgYXMgc2VsZWN0ZWQuXG5cbvCfk4wgUGxlYXNlIGNvbXBsZXRlIHBheW1lbnQgYW5k"
    "IHNlbmQgdGhlIFJlZmVyZW5jZSBJRC4iLAogICAgICAgICJvbSI6ICLinIUgTWFsbGkga2FmZmFsdGlpIGtlZXNzYW4gKHtsYWJl"
    "bH0pIGFra3VtYSBmaWxhdGFtZXR0aSBlZWdhbWVlcmEuXG5cbvCfk4wgTWFhbG9vIGthZmZhbHRpaSB4dW11cmFhdGlpIFJlZmVy"
    "ZW5jZSBJRCBlcmdhYS4iLAogICAgfSwKICAgICJwYXltZW50X2NoYW5nZWRfYWxlcnQiOiB7ImFtIjogIuGLqOGKreGNjeGLqyDh"
    "i5jhi7Qg4Ymw4YmA4Yut4Yiv4YiN4Y2iIiwgImVuIjogIlBheW1lbnQgbWV0aG9kIGNoYW5nZWQuIiwgIm9tIjogIk1hbGxpIGth"
    "ZmZhbHRpaSBqaWpqaWlyYW1lZXJhLiJ9LAogICAgInRpY2tldF9ub3RfeW91cnMiOiB7CiAgICAgICAgImFtIjogIuKdjCDhi63h"
    "iIUg4Ymy4Yqs4Ym1IOGKqOGKpeGIreGIteGLjiDhjIvhiK0g4Yqg4Yut4YyI4YqT4Yqd4Yid4Y2iIiwKICAgICAgICAiZW4iOiAi"
    "4p2MIFRoaXMgdGlja2V0IGRvZXNuJ3QgYmVsb25nIHRvIHlvdS4iLAogICAgICAgICJvbSI6ICLinYwgVGlrZWV0aWluIGt1biBr"
    "YW4ga2Vlc3NhbiBtaXRpLiIsCiAgICB9LAogICAgInVua25vd25fcGF5bWVudF9tZXRob2QiOiB7CiAgICAgICAgImFtIjogIuKd"
    "jCDhi6vhiI3hibPhi4jhiYAg4Yuo4Yqt4Y2N4YurIOGLmOGLtOGNoiIsCiAgICAgICAgImVuIjogIuKdjCBVbmtub3duIHBheW1l"
    "bnQgbWV0aG9kLiIsCiAgICAgICAgIm9tIjogIuKdjCBNYWxsaSBrYWZmYWx0aWkgaGluIGJlZWthbW5lLiIsCiAgICB9LAogICAg"
    "InRpY2tldF9ub3RfZm91bmRfcmV0cnkiOiB7CiAgICAgICAgImFtIjogIuKdjCDhi63hiIUg4Ymy4Yqs4Ym1IOGKoOGIjeGJsOGM"
    "iOGKmOGIneGNoiDhiqXhiaPhiq3hi44g4Yql4YqV4Yuw4YyI4YqTIC9wbGF5IOGLreGInuGKreGIqeGNoiIsCiAgICAgICAgImVu"
    "IjogIuKdjCBUaGlzIHRpY2tldCB3YXNuJ3QgZm91bmQuIFBsZWFzZSB0cnkgL3BsYXkgYWdhaW4uIiwKICAgICAgICAib20iOiAi"
    "4p2MIFRpa2VldGlpbiBrdW4gaGluIGFyZ2FtbmUuIE1hYWxvbyAvcGxheSBhbW1hcyB5YWFsYWEuIiwKICAgIH0sCiAgICAidGlj"
    "a2V0X25vdF9oZWxkX29yX2V4cGlyZWQiOiB7CiAgICAgICAgImFtIjogIuKdjCDhi63hiIUg4Ymy4Yqs4Ym1IOGIiOGKpeGIreGI"
    "teGLjiDhi6jhibDhi6vhi5gg4Yqg4Yut4Yuw4YiI4YidIOGLiOGLreGInSDhjIrhi5zhi40g4Yqg4YiN4Y2P4YiN4Y2iIOGKpeGJ"
    "o+GKreGLjiDhiqXhipXhi7DhjIjhipMgL3BsYXkg4Yut4Yie4Yqt4Yip4Y2iIiwKICAgICAgICAiZW4iOiAi4p2MIFRoaXMgdGlj"
    "a2V0IGlzbid0IGhlbGQgZm9yIHlvdSwgb3IgaXQgaGFzIGV4cGlyZWQuIFBsZWFzZSB0cnkgL3BsYXkgYWdhaW4uIiwKICAgICAg"
    "ICAib20iOiAi4p2MIFRpa2VldGlpbiBrdW4gaXNpbmlpZiBoaW4gcWFiYW1uZSwgeW9va2FhbiB5ZXJvb24gaXNhYSBkYWJyZWVy"
    "YS4gTWFhbG9vIC9wbGF5IGFtbWFzIHlhYWxhYS4iLAogICAgfSwKICAgICJjb25maXJtX3N3aXRjaF9tYW51YWxfY2FsbCI6IHsK"
    "ICAgICAgICAiYW0iOiAi4pqg77iPIOGLqOGKreGNjeGLqyDhi5jhi7Qg4Yqg4Yi14YmA4Yu14YieIOGImOGIreGMoOGLi+GIjeGN"
    "piB7Y3VycmVudH1cblxu4YuI4YuwIHtuZXd9IOGImOGJgOGLqOGIrSDhi63hjYjhiI3hjIvhiIk/IiwKICAgICAgICAiZW4iOiAi"
    "4pqg77iPIFlvdSd2ZSBhbHJlYWR5IGNob3NlbiBhIHBheW1lbnQgbWV0aG9kOiB7Y3VycmVudH1cblxuV291bGQgeW91IGxpa2Ug"
    "dG8gc3dpdGNoIHRvIHtuZXd9PyIsCiAgICAgICAgIm9tIjogIuKaoO+4jyBEdXJhYW4gbWFsYSBrYWZmYWx0aWkgZmlsYXR0YW5p"
    "aXR0dToge2N1cnJlbnR9XG5cbkdhcmEge25ld30gamlqamlpcnV1IGJhcmJhYWRkdT8iLAogICAgfSwKICAgICJidG5fc3dpdGNo"
    "X3llcyI6IHsiYW0iOiAi8J+UhCDhiqDhi47hjaMg4Yuo4Yqt4Y2N4YurIOGLmOGLtOGKlSDhiYDhi63hiK0iLCAiZW4iOiAi8J+U"
    "hCBZZXMsIHN3aXRjaCBtZXRob2QiLCAib20iOiAi8J+UhCBFZXl5ZWUsIG1hbGEgamlqamlpcmkifSwKICAgICJidG5fc3dpdGNo"
    "X25vIjogeyJhbSI6ICLinYwg4Yqg4Yut4Y2jIOGKpeGKleGLsOGLmuGIgSDhi63hiYbhi60iLCAiZW4iOiAi4p2MIE5vLCBrZWVw"
    "IGFzIGlzIiwgIm9tIjogIuKdjCBMYWtraSwgYWtrYXN1bWF0dGkgaGFhIGhhZnUifSwKICAgICJhbHJlYWR5X3NlbGVjdGVkX21l"
    "dGhvZCI6IHsKICAgICAgICAiYW0iOiAi4pyFIOGLreGIheGKlSDhi6jhiq3hjY3hi6sg4YuY4Yu0IOGKoOGIteGJgOGLteGImOGL"
    "jSDhiJjhiK3hjKDhi4vhiI3hjaIiLAogICAgICAgICJlbiI6ICLinIUgWW91J3ZlIGFscmVhZHkgc2VsZWN0ZWQgdGhpcyBwYXlt"
    "ZW50IG1ldGhvZC4iLAogICAgICAgICJvbSI6ICLinIUgTWFsYSBrYWZmYWx0aWkga2FuYSBkdXJhYW4gZmlsYXR0YW5paXJ0dS4i"
    "LAogICAgfSwKICAgICJtYW51YWxfY2FsbF9hZG1pbl9ub3RpZnkiOiB7CiAgICAgICAgImFtIjogIvCfk54gPGI+4Ymg4Yi14YiN"
    "4YqtIOGKqOGKoOGLteGImuGKlSDhjIvhiK0g4YiI4YiY4YyN4Yub4Ym1IOGMpeGLq+GJhDwvYj5cblxu8J+On++4jyDhi5nhiK06"
    "IHtyaWR9XG7wn5SiIOGJgeGMpeGIrSjhi47hib0pOiB7bnVtc31cbvCfkaQg4Yi14YidOiB7bmFtZX1cbvCfk7Eg4Yi14YiN4Yqt"
    "OiB7cGhvbmV9XG7wn5K1IOGMoOGJheGIi+GIiyDhi4vhjIs6IHt0b3RhbDouMGZ9IOGJpeGIrSIsCiAgICAgICAgImVuIjogIvCf"
    "k54gPGI+UmVxdWVzdCB0byBidXkgYnkgcGhvbmUgY2FsbDwvYj5cblxu8J+On++4jyBSb3VuZDoge3JpZH1cbvCflKIgVGlja2V0"
    "KHMpOiB7bnVtc31cbvCfkaQgTmFtZToge25hbWV9XG7wn5OxIFBob25lOiB7cGhvbmV9XG7wn5K1IFRvdGFsIHByaWNlOiB7dG90"
    "YWw6LjBmfSBiaXJyIiwKICAgICAgICAib20iOiAi8J+TniA8Yj5HYWFmZmlpIGJpbGJpbGFhbiBiaXR1dTwvYj5cblxu8J+On++4"
    "jyBNYXJzYWE6IHtyaWR9XG7wn5SiIExha2tvb2ZzYSh3d2FuKToge251bXN9XG7wn5GkIE1hcWFhOiB7bmFtZX1cbvCfk7EgQmls"
    "YmlsYToge3Bob25lfVxu8J+StSBHYXRpaSB3YWxpaWdhbGFhOiB7dG90YWw6LjBmfSBiaXJyaWkiLAogICAgfSwKICAgICJiZWNv"
    "bWVfaG9zdF9hZG1pbl9ub3RpZnkiOiB7CiAgICAgICAgImFtIjogIvCflqXvuI8gPGI+4Yqg4Yuy4Yi1IOGLqOGIhuGIteGJtSDh"
    "iJjhiIbhipUg4Yyl4Yur4YmEITwvYj5cblxu8J+RpCDhiLXhiJ06IHtuYW1lfVxu8J+TsSDhiLXhiI3hiq06IHtwaG9uZX1cbvCf"
    "lJcg4Yup4YuY4Yit4YqU4YidOiBAe3VzZXJuYW1lfVxu8J+GlCBVc2VyIElEOiB7dWlkfSIsCiAgICAgICAgImVuIjogIvCflqXv"
    "uI8gPGI+TmV3IHJlcXVlc3QgdG8gYmVjb21lIGEgaG9zdCE8L2I+XG5cbvCfkaQgTmFtZToge25hbWV9XG7wn5OxIFBob25lOiB7"
    "cGhvbmV9XG7wn5SXIFVzZXJuYW1lOiBAe3VzZXJuYW1lfVxu8J+GlCBVc2VyIElEOiB7dWlkfSIsCiAgICAgICAgIm9tIjogIvCf"
    "lqXvuI8gPGI+R2FhZmZpaSBoYWFyYWEgaG9zdCB0YSd1dSE8L2I+XG5cbvCfkaQgTWFxYWE6IHtuYW1lfVxu8J+TsSBCaWxiaWxh"
    "OiB7cGhvbmV9XG7wn5SXIFVzZXJuYW1lOiBAe3VzZXJuYW1lfVxu8J+GlCBVc2VyIElEOiB7dWlkfSIsCiAgICB9LAogICAgIm1h"
    "bnVhbF9jYWxsX2Z1bGwiOiB7CiAgICAgICAgImFtIjogIvCfk54gPGI+4Yuw4YuJ4YiI4YuJIOGLq+GIsuGLmTwvYj5cblxu8J+T"
    "sSDhi6jhiqDhi7XhiJrhipUg4Yi14YiN4YqtOiA8Y29kZT57YWNjb3VudH08L2NvZGU+XG5cbvCfkYcg4Yqo4Ymz4Ym9IOGLq+GI"
    "iOGLjeGKlSDhiYHhiI3hjY0g4Ymg4YiY4Yyr4YqVIOGJoOGJgOGMpeGJsyDhi63hi7Dhi43hiInhjaJcbuKPse+4jyDhiYHhjKXh"
    "iK0o4YuO4Ym9KSB7bnVtc30g4YiIIHttaW5zfSDhi7DhiYLhiYMg4Ymw4Yut4YuY4YuN4YiN4YuO4Ymz4YiN4Y2iIiwKICAgICAg"
    "ICAiZW4iOiAi8J+TniA8Yj5DYWxsIHRvIHJlc2VydmU8L2I+XG5cbvCfk7EgQWRtaW4gcGhvbmU6IDxjb2RlPnthY2NvdW50fTwv"
    "Y29kZT5cblxu8J+RhyBUYXAgdGhlIGJ1dHRvbiBiZWxvdyB0byBjYWxsIGRpcmVjdGx5Llxu4o+x77iPIFRpY2tldChzKSAje251"
    "bXN9IGFyZSBoZWxkIGZvciB5b3UgZm9yIHttaW5zfSBtaW51dGVzLiIsCiAgICAgICAgIm9tIjogIvCfk54gPGI+QmlsYmlsdXVu"
    "IHFhYmFkaGFhPC9iPlxuXG7wn5OxIEJpbGJpbGEgYWRtaW46IDxjb2RlPnthY2NvdW50fTwvY29kZT5cblxu8J+RhyBRYWJvbyBn"
    "YWRpaSB0dXFhYXRpaSBrYWxsYXR0aWluIGJpbGJpbGFhLlxu4o+x77iPIExha2tvb2ZzYSh3d2FuKSAje251bXN9IGRhcWlpcWFh"
    "IHttaW5zfSBpc2luaWlmIHFhYmFtYW5paXJ1LiIsCiAgICB9LAogICAgImNhbGxfYnV0dG9uIjogeyJhbSI6ICLwn5OeIHthY2Nv"
    "dW50fSDhi63hi7Dhi43hiIkiLCAiZW4iOiAi8J+TniBDYWxsIHthY2NvdW50fSIsICJvbSI6ICLwn5OeIHthY2NvdW50fSBiaWxi"
    "aWxhYSJ9LAogICAgImFkbWluX3JlamVjdGVkX3RpY2tldCI6IHsKICAgICAgICAiYW0iOiAi4p2MIOGLreGJheGIreGJsyDhi6jh"
    "iIvhiqnhibUg4Yuo4Yiq4Y2I4Yio4YqV4Yi1IOGJgeGMpeGIrSDhiqjhiaPhipXhiq0g4YiY4Yio4YyDIOGMi+GIrSDhiIrhjIjh"
    "jKPhjKDhiJ0g4Yqg4YiN4Ym74YiI4Yid4Y2iIOGKreGNjeGLq+GLjiAo4YuZ4YitIHtyaWR94Y2jIOGJgeGMpeGIrSB7dG59KSDh"
    "i43hi7XhiYUg4Ymw4Yuw4Yit4YyT4YiN4Y2iIiwKICAgICAgICAiZW4iOiAi4p2MIFNvcnJ5LCB0aGUgcmVmZXJlbmNlIG51bWJl"
    "ciB5b3Ugc2VudCBjb3VsZG4ndCBiZSBtYXRjaGVkIHdpdGggYmFuayByZWNvcmRzLiBZb3VyIHBheW1lbnQgKHJvdW5kIHtyaWR9"
    "LCB0aWNrZXQgI3t0bn0pIGhhcyBiZWVuIHJlamVjdGVkLiIsCiAgICAgICAgIm9tIjogIuKdjCBEaGlpZmFtYSwgbGFra29vZnNp"
    "IFJlZmVyZW5jZSBlcmdpdGFuIGdhbG1lZSBiYWFua2lpIHdhamppbiB3YWwgZmFra2FhY2h1dSBoaW4gZGFuZGVlbnllLiBLYWZm"
    "YWx0aWluIGtlZXNzYW4gKG1hcnNhYSB7cmlkfSwgbGFra29vZnNhICN7dG59KSBkaWRkZWVyYS4iLAogICAgfSwKICAgICJhZG1p"
    "bl9yZWplY3RlZF90aWNrZXRfZ3JvdXAiOiB7CiAgICAgICAgImFtIjogIuKdjCDhi63hiYXhiK3hibMg4Yuo4YiL4Yqp4Ym1IOGL"
    "qOGIquGNiOGIqOGKleGItSDhiYHhjKXhiK0g4Yqo4Ymj4YqV4YqtIOGImOGIqOGMgyDhjIvhiK0g4YiK4YyI4Yyj4Yyg4YidIOGK"
    "oOGIjeGJu+GIiOGIneGNoiDhiq3hjY3hi6vhi44gKOGLmeGIrSB7cmlkfeGNoyDhiYHhjKXhiK7hib0ge3Ruc30pIOGLjeGLteGJ"
    "hSDhibDhi7DhiK3hjJPhiI3hjaIiLAogICAgICAgICJlbiI6ICLinYwgU29ycnksIHRoZSByZWZlcmVuY2UgbnVtYmVyIHlvdSBz"
    "ZW50IGNvdWxkbid0IGJlIG1hdGNoZWQgd2l0aCBiYW5rIHJlY29yZHMuIFlvdXIgcGF5bWVudCAocm91bmQge3JpZH0sIHRpY2tl"
    "dChzKSAje3Ruc30pIGhhcyBiZWVuIHJlamVjdGVkLiIsCiAgICAgICAgIm9tIjogIuKdjCBEaGlpZmFtYSwgbGFra29vZnNpIFJl"
    "ZmVyZW5jZSBlcmdpdGFuIGdhbG1lZSBiYWFua2lpIHdhamppbiB3YWwgZmFra2FhY2h1dSBoaW4gZGFuZGVlbnllLiBLYWZmYWx0"
    "aWluIGtlZXNzYW4gKG1hcnNhYSB7cmlkfSwgbGFra29vZnNhKHd3YW4pICN7dG5zfSkgZGlkZGVlcmEuIiwKICAgIH0sCiAgICAi"
    "YWRtaW5fcmVqZWN0ZWRfcmVhc29uX2xpbmUiOiB7CiAgICAgICAgImFtIjogIlxu8J+TnSDhiJ3hiq3hipXhi6vhibXhjaYge3Jl"
    "YXNvbn0iLAogICAgICAgICJlbiI6ICJcbvCfk50gUmVhc29uOiB7cmVhc29ufSIsCiAgICAgICAgIm9tIjogIlxu8J+TnSBTYWJh"
    "YmE6IHtyZWFzb259IiwKICAgIH0sCiAgICAiYmFja2dyb3VuZF9ob2xkX2V4cGlyZWQiOiB7CiAgICAgICAgImFtIjogIuKPsCDh"
    "i6h7bWluc30g4Yuw4YmC4YmDIOGLqOGKreGNjeGLqyDhjIrhi5zhi44g4Yqg4YiN4YmL4YiN4Y2iXG7wn46f77iPIHtybGFiZWx9"
    "IOGJgeGMpeGIrSjhi47hib0pIHt0bnN9IOGJsOGIiOGJheGJgOGLi+GIjeGNolxu4Yqo4Y2I4YiI4YyJIOGKpeGKleGLsOGMiOGK"
    "kyDhiYHhjKXhiK0g4Yut4Yid4Yio4Yyh4Y2iIiwKICAgICAgICAiZW4iOiAi4o+wIFlvdXIge21pbnN9LW1pbnV0ZSBwYXltZW50"
    "IHdpbmRvdyBoYXMgZW5kZWQuXG7wn46f77iPIHtybGFiZWx9IHRpY2tldChzKSAje3Ruc30gaGF2ZSBiZWVuIHJlbGVhc2VkLlxu"
    "UGljayBhIG51bWJlciBhZ2FpbiBpZiB5b3UnZCBsaWtlLiIsCiAgICAgICAgIm9tIjogIuKPsCBZZXJvb24ga2FmZmFsdGlpIGRh"
    "cWlpcWFhIHttaW5zfSBrZWVzc2FuIHh1bXVyYW1lZXJhLlxu8J+On++4jyB7cmxhYmVsfSBsYWtrb29mc2Eod3dhbikgI3t0bnN9"
    "IGdhZGkgbGFra2lmYW1hbmlpcnUuXG5Zb28gYmFyYmFhZGRhbiBhbW1hcyBsYWtrb29mc2EgZmlsYWRoYWEuIiwKICAgIH0sCiAg"
    "ICAiYnRuX25leHRfcGFnZSI6IHsiYW0iOiAi4p6h77iPIOGJgOGMo+GLrSIsICJlbiI6ICLinqHvuI8gTmV4dCIsICJvbSI6ICLi"
    "nqHvuI8gSXR0aSBhYW51In0sCiAgICAiYnRuX3ByZXZfcGFnZSI6IHsiYW0iOiAi4qyF77iPIOGJgOGLs+GImiIsICJlbiI6ICLi"
    "rIXvuI8gUHJldmlvdXMiLCAib20iOiAi4qyF77iPIER1cmFhbmlpIn0sCiAgICAicGFnZV9pbmRpY2F0b3IiOiB7ImFtIjogIvCf"
    "k4Qge3BhZ2V9L3t0b3RhbH0iLCAiZW4iOiAi8J+ThCB7cGFnZX0ve3RvdGFsfSIsICJvbSI6ICLwn5OEIHtwYWdlfS97dG90YWx9"
    "In0sCiAgICAibXlfaW5mb19ub190aWNrZXRzIjogewogICAgICAgICJhbSI6ICJcblxu8J+On++4jyDhiqXhiLXhiqvhiIHhipUg"
    "4Yid4YqV4YidIOGJsuGKrOGJtSDhiqDhiI3hjIjhi5nhiJ3hjaIiLAogICAgICAgICJlbiI6ICJcblxu8J+On++4jyBZb3UgaGF2"
    "ZW4ndCBwdXJjaGFzZWQgYW55IHRpY2tldHMgeWV0LiIsCiAgICAgICAgIm9tIjogIlxuXG7wn46f77iPIEhhbmdhIGFtbWFhdHRp"
    "IHRpa2VldGlpIHRva2tvIGlsbGVlIGhpbiBiaXRhbm5lLiIsCiAgICB9LAogICAgIm15X2luZm9fdGlja2V0c19oZWFkZXIiOiB7"
    "CiAgICAgICAgImFtIjogIlxuXG7wn46f77iPIDxiPuGLqOGMiOGLmeGJtSDhibLhiqzhibbhib3hjaY8L2I+IiwKICAgICAgICAi"
    "ZW4iOiAiXG5cbvCfjp/vuI8gPGI+WW91ciB0aWNrZXRzOjwvYj4iLAogICAgICAgICJvbSI6ICJcblxu8J+On++4jyA8Yj5UaWtl"
    "ZXRvdGEgYml0dGFuOjwvYj4iLAogICAgfSwKICAgICJ0aWNrZXRfbGluZV9vbGQiOiB7CiAgICAgICAgImFtIjogIvCfl4TvuI8g"
    "I3t0bn0g4oCUIHtybGFiZWx9IOKAlCA8aT7hi6jhiYbhi6ggKOGLmeGIqSDhibDhi5jhjI3hibfhiI0pPC9pPiIsCiAgICAgICAg"
    "ImVuIjogIvCfl4TvuI8gI3t0bn0g4oCUIHtybGFiZWx9IOKAlCA8aT5PbGQgKHJvdW5kIGNsb3NlZCk8L2k+IiwKICAgICAgICAi"
    "b20iOiAi8J+XhO+4jyAje3RufSDigJQge3JsYWJlbH0g4oCUIDxpPkthbiBtb29mYWEgKG1hcnNhYW4gY3VmYW1lZXJhKTwvaT4i"
    "LAogICAgfSwKICAgICJ0aWNrZXRfbGluZV93YWl0aW5nIjogewogICAgICAgICJhbSI6ICLij7MgI3t0bn0g4oCUIHtybGFiZWx9"
    "IOKAlCA8aT7hiaDhiJjhjKDhiaPhiaDhiYUg4YiL4YutICjhiq3hjY3hi6sg4Ymg4YiC4Yuw4Ym1KTwvaT4iLAogICAgICAgICJl"
    "biI6ICLij7MgI3t0bn0g4oCUIHtybGFiZWx9IOKAlCA8aT5XYWl0aW5nIChwYXltZW50IGluIHByb2dyZXNzKTwvaT4iLAogICAg"
    "ICAgICJvbSI6ICLij7MgI3t0bn0g4oCUIHtybGFiZWx9IOKAlCA8aT5FZWdhYSBqaXJhIChrYWZmYWx0aWluIGFkZWVtc2EgaXJy"
    "YSk8L2k+IiwKICAgIH0sCiAgICAidGlja2V0X2xpbmVfY29uZmlybWVkIjogewogICAgICAgICJhbSI6ICLinIUgI3t0bn0g4oCU"
    "IHtybGFiZWx9IOKAlCA8aT7hi6jhibDhiKjhjIvhjIjhjKA8L2k+IiwKICAgICAgICAiZW4iOiAi4pyFICN7dG59IOKAlCB7cmxh"
    "YmVsfSDigJQgPGk+Q29uZmlybWVkPC9pPiIsCiAgICAgICAgIm9tIjogIuKchSAje3RufSDigJQge3JsYWJlbH0g4oCUIDxpPk1p"
    "cmthbmFhJ2U8L2k+IiwKICAgIH0sCiAgICAibm9fc2VsZWN0aW9uX3lldCI6IHsKICAgICAgICAiYW0iOiAi4p2MIOGJoOGImOGM"
    "gOGImOGIquGLqyAvcGxheSDhiaXhiIjhi40g4Yuo4Yia4YyI4YuZ4Ym14YqVIOGJgeGMpeGIrSDhi63hiJ3hiKjhjKHhjaIiLAog"
    "ICAgICAgICJlbiI6ICLinYwgUGxlYXNlIHBpY2sgYSBudW1iZXIgZmlyc3Qgd2l0aCAvcGxheS4iLAogICAgICAgICJvbSI6ICLi"
    "nYwgRHVyYWFuIGR1cnNhYSAvcGxheSBqZWRoYWEgbGFra29vZnNhIGJpdHV1IGJhcmJhYWRkYW4gZmlsYWRoYWEuIiwKICAgIH0s"
    "CiAgICAibm9fcGF5bWVudF9tZXRob2RfeWV0IjogewogICAgICAgICJhbSI6ICLinYwg4Ymg4YiY4YyA4YiY4Yiq4YurIOGKqOGI"
    "i+GLrSDhiqvhiInhibUg4YuN4Yi14YylIOGLqOGKreGNjeGLqyDhi5jhi7QgKFRlbGVCaXJyL0NCRSBCaXJyKSDhi63hiJ3hiKjh"
    "jKHhjaIiLAogICAgICAgICJlbiI6ICLinYwgUGxlYXNlIGZpcnN0IGNob29zZSBhIHBheW1lbnQgbWV0aG9kIChUZWxlQmlyci9D"
    "QkUgQmlycikgZnJvbSBhYm92ZS4iLAogICAgICAgICJvbSI6ICLinYwgRHVyYWFuIGR1cnNhYSBtYWxhIGthZmZhbHRpaSAoVGVs"
    "ZUJpcnIvQ0JFIEJpcnIpIGFybWFhbiBvbGlpIGtlZXNzYWEgZmlsYWRoYWEuIiwKICAgIH0sCiAgICAibWFudWFsX2NhbGxfbm9f"
    "cmVmX25lZWRlZCI6IHsKICAgICAgICAiYW0iOiAi8J+TniDhi7Dhi4nhiIjhi4kg4Yur4Yiy4YuZIOGLqOGImuGIiOGLjeGKlSDh"
    "iJjhiK3hjKDhi4vhiI3hjaIgUmVmZXJlbmNlIElEIOGImOGIi+GKrSDhiqDhi6vhiLXhjYjhiI3hjI3hiJ3hjaIg4Yql4Ymj4Yqt"
    "4YuOIDA5NjM1MzAwMzAg4Yut4Yuw4YuN4YiJ4Y2iIiwKICAgICAgICAiZW4iOiAi8J+TniBZb3UgY2hvc2UgXCJDYWxsIHRvIHJl"
    "c2VydmVcIi4gWW91IGRvbid0IG5lZWQgdG8gc2VuZCBhIFJlZmVyZW5jZSBJRC4gUGxlYXNlIGNhbGwgMDk2MzUzMDAzMC4iLAog"
    "ICAgICAgICJvbSI6ICLwn5OeIFwiQmlsYmlsdXVuIHFhYmFkaGFhXCIgZmlsYXR0YW5paXR0dS4gUmVmZXJlbmNlIElEIGVyZ3Ug"
    "aGluIGJhcmJhYWNoaXN1LiBNYWFsb28gMDk2MzUzMDAzMCBiaWxiaWxhYS4iLAogICAgfSwKICAgICJpbnZhbGlkX2Zvcm1hdF9v"
    "dXRfb2ZfYXR0ZW1wdHMiOiB7CiAgICAgICAgImFtIjogIuKdjCA8Yj7hiI3hiq0g4Yur4YiN4YiG4YqQIOGJheGIreGMuOGJtSAo"
    "SW52YWxpZCBmb3JtYXQpPC9iPiDigJQg4Yuo4YiL4Yqp4Ym1ICh7cmVmfSkg4Ym14Yqt4Yqt4YiI4YqbIOGLq+GIjeGIhuGKkCDh"
    "i6h7bGFiZWx9IOGIquGNiOGIqOGKleGItSDhiYXhiK3hjLjhibUg4YqQ4YuN4Y2iXG7wn5qrIOGLqHttYXh9IOGImeGKqOGIq+GL"
    "juGKlSDhjKjhiK3hiLDhi4vhiI3hjaMg4Ymy4Yqs4YmxIOGJsOGIiOGJheGJi+GIjeGNoiDhiqXhiaPhiq3hi44g4Yql4YqV4Yuw"
    "4YyI4YqTIC9wbGF5IOGJpeGIiOGLjSDhi63hjIDhiJ3hiKkg4YuI4Yut4YidIOGKoOGLteGImuGKkeGKlSDhi6vhipDhjIvhjI3h"
    "iKnhjaIiLAogICAgICAgICJlbiI6ICLinYwgPGI+SW52YWxpZCBmb3JtYXQ8L2I+IOKAlCAoe3JlZn0pIGlzbid0IGEgdmFsaWQg"
    "e2xhYmVsfSByZWZlcmVuY2UgZm9ybWF0Llxu8J+aqyBZb3UndmUgdXNlZCBhbGwge21heH0gYXR0ZW1wdHMsIHRoZSB0aWNrZXQg"
    "aGFzIGJlZW4gcmVsZWFzZWQuIFBsZWFzZSBzdGFydCBhZ2FpbiB3aXRoIC9wbGF5IG9yIGNvbnRhY3QgdGhlIGFkbWluLiIsCiAg"
    "ICAgICAgIm9tIjogIuKdjCA8Yj5CaWZhIGRvZ29uZ29yYWEgKEludmFsaWQgZm9ybWF0KTwvYj4g4oCUICh7cmVmfSkgYmlmYSBS"
    "ZWZlcmVuY2Uge2xhYmVsfSBzaXJyaWkgbWl0aS5cbvCfmqsgWWFhbGlpIHttYXh9IGtlZXNzYW4gZml4eGFuaWl0dHUsIHRpa2Vl"
    "dGlpbiBnYWRpIGxha2tpZmFtZWVyYS4gTWFhbG9vIC9wbGF5IGplZGhhYSBhbW1hcyBqYWxxYWJhYSB5b29rYWFuIGFkbWluIHF1"
    "dW5uYW1hYS4iLAogICAgfSwKICAgICJpbnZhbGlkX2Zvcm1hdF9yZXRyeSI6IHsKICAgICAgICAiYW0iOiAi4p2MIDxiPuGIjeGK"
    "rSDhi6vhiI3hiIbhipAg4YmF4Yit4Yy44Ym1IChJbnZhbGlkIGZvcm1hdCk8L2I+XG5cbuGLqOGIi+GKqeGJtSAoe3JlZn0pIOGK"
    "qHtsYWJlbH0g4Yiq4Y2I4Yio4YqV4Yi1IOGJheGIreGMuOGJtSDhjIvhiK0g4Yqg4Yut4YiY4Yiz4Yiw4YiN4Yid4Y2iXG57aGlu"
    "dH1cblxu8J+UgSDhiqXhiaPhiq3hi44g4Ym14Yqt4Yqt4YiI4Yqb4YuN4YqVIOGIquGNiOGIqOGKleGItSDhiYHhjKXhiK0g4YuI"
    "4Yut4YidIOGImeGIiSDhi6jhiqThiLXhiqThiJ3hiqThiLUg4YiY4YiN4YuV4Yqt4Ym1IOGJoOGLteGMi+GImiDhi63hiIvhiqkg"
    "KFRyeSBhZ2FpbinhjaIg4Yuo4YmA4YipIOGImeGKqOGIq+GLjuGJveGNpiB7cmVtYWluaW5nfSIsCiAgICAgICAgImVuIjogIuKd"
    "jCA8Yj5JbnZhbGlkIGZvcm1hdDwvYj5cblxuKHtyZWZ9KSBkb2Vzbid0IG1hdGNoIHRoZSB7bGFiZWx9IHJlZmVyZW5jZSBmb3Jt"
    "YXQuXG57aGludH1cblxu8J+UgSBQbGVhc2UgcmVzZW5kIHRoZSBjb3JyZWN0IHJlZmVyZW5jZSBudW1iZXIgb3IgdGhlIGZ1bGwg"
    "U01TIG1lc3NhZ2UgKFRyeSBhZ2FpbikuIEF0dGVtcHRzIGxlZnQ6IHtyZW1haW5pbmd9IiwKICAgICAgICAib20iOiAi4p2MIDxi"
    "PkJpZmEgZG9nb25nb3JhYSAoSW52YWxpZCBmb3JtYXQpPC9iPlxuXG4oe3JlZn0pIGJpZmEgUmVmZXJlbmNlIHtsYWJlbH0gd2Fq"
    "amluIHdhbCBoaW4gZmFra2FhdHUuXG57aGludH1cblxu8J+UgSBNYWFsb28gbGFra29vZnNhIFJlZmVyZW5jZSBzaXJyaWkgeW9v"
    "a2FhbiBlcmdhYSBTTVMgZ3V1dHV1IGRlZWJpc2FhIGVyZ2FhIChUcnkgYWdhaW4pLiBZYWFsaWkgaGFmZToge3JlbWFpbmluZ30i"
    "LAogICAgfSwKICAgICJzbXNfcmVmX2V4dHJhY3RlZCI6IHsKICAgICAgICAiYW0iOiAi8J+UjiDhiqjhiIvhiqnhibUg4Yqk4Yi1"
    "4Yqk4Yid4Yqk4Yi1IOGLjeGIteGMpSDhi63hiIXhipUg4Yiq4Y2I4Yio4YqV4Yi1IOGJgeGMpeGIrSDhiqDhjI3hip3hibDhipPh"
    "iI3hjaYgPGNvZGU+e3JlZn08L2NvZGU+XG7ij7Mg4Ymg4Yib4Yio4YyL4YyI4YylIOGIi+GLrS4uLiIsCiAgICAgICAgImVuIjog"
    "IvCflI4gV2UgZm91bmQgdGhpcyByZWZlcmVuY2UgbnVtYmVyIGluIHlvdXIgU01TOiA8Y29kZT57cmVmfTwvY29kZT5cbuKPsyBW"
    "ZXJpZnlpbmcuLi4iLAogICAgICAgICJvbSI6ICLwn5SOIEVyZ2FhIFNNUyBrZWVzc2FuIGtlZXNzYWEgbGFra29vZnNhIFJlZmVy"
    "ZW5jZSBrYW5hIGFyZ2luZWVycmE6IDxjb2RlPntyZWZ9PC9jb2RlPlxu4o+zIE1pcmthbmVlc3NhYSBqaXJyYS4uLiIsCiAgICB9"
    "LAogICAgInNjcmVlbnNob3RfcmVmX2V4dHJhY3RlZCI6IHsKICAgICAgICAiYW0iOiAi8J+UjiDhiqjhiIvhiqnhibUg4Yi14Yqt"
    "4Yiq4YqV4Yi+4Ym1IOGLjeGIteGMpSDhi63hiIXhipUg4Yiq4Y2I4Yio4YqV4Yi1IOGJgeGMpeGIrSDhiqDhjI3hip3hibDhipPh"
    "iI3hjaYgPGNvZGU+e3JlZn08L2NvZGU+XG7ij7Mg4Ymg4Yib4Yio4YyL4YyI4YylIOGIi+GLrS4uLiIsCiAgICAgICAgImVuIjog"
    "IvCflI4gV2UgZm91bmQgdGhpcyByZWZlcmVuY2UgbnVtYmVyIGluIHlvdXIgc2NyZWVuc2hvdDogPGNvZGU+e3JlZn08L2NvZGU+"
    "XG7ij7MgVmVyaWZ5aW5nLi4uIiwKICAgICAgICAib20iOiAi8J+UjiBTdXVyYWEga2Vlc3NhbiBrZWVzc2FhIGxha2tvb2ZzYSBS"
    "ZWZlcmVuY2Uga2FuYSBhcmdpbmVlcnJhOiA8Y29kZT57cmVmfTwvY29kZT5cbuKPsyBNaXJrYW5lZXNzYWEgamlycmEuLi4iLAog"
    "ICAgfSwKICAgICJzY3JlZW5zaG90X3JlZl9ub3RfZm91bmQiOiB7CiAgICAgICAgImFtIjogIvCfp74g4Yi14Yqt4Yiq4YqV4Yi+"
    "4Ymx4YqVIOGJsOGJgOGJpeGIiOGKk+GIjeGNoyDhipDhjIjhiK0g4YyN4YqVIOGIquGNiOGIqOGKleGItSDhiYHhjKXhiKnhipUg"
    "4Ymg4YyN4YiN4Yy9IOGIm+GKleGJoOGJpSDhiqDhiI3hibvhiI3hipXhiJ3hjaJcbuKcje+4jyDhiqXhiaPhiq3hi44g4Yuo4Yiq"
    "4Y2I4Yio4YqV4Yi1IOGJgeGMpeGIqeGKlSDhi4jhi63hiJ0g4YiZ4YiJ4YuN4YqVIOGLqOGKpOGIteGKpOGIneGKpOGItSDhiJjh"
    "iI3hi5Xhiq3hibUg4Ymg4Yy94YiB4Y2NIOGLreGIi+GKqeGIjeGKleGNoiIsCiAgICAgICAgImVuIjogIvCfp74gV2UgcmVjZWl2"
    "ZWQgeW91ciBzY3JlZW5zaG90LCBidXQgY291bGRuJ3QgY2xlYXJseSByZWFkIHRoZSByZWZlcmVuY2UgbnVtYmVyIGZyb20gaXQu"
    "XG7inI3vuI8gUGxlYXNlIHNlbmQgdGhlIHJlZmVyZW5jZSBudW1iZXIsIG9yIHRoZSBmdWxsIFNNUyBtZXNzYWdlLCBhcyB0ZXh0"
    "IGluc3RlYWQuIiwKICAgICAgICAib20iOiAi8J+nviBTdXVyYWEga2Vlc3NhbiBzaW1hbm5lZXJyYSwgZ2FydXUgbGFra29vZnNh"
    "IFJlZmVyZW5jZSBpc2FhIGlmYXR0aSBkdWJiaXN1dSBoaW4gZGFuZGVlbnllLlxu4pyN77iPIE1hYWxvbyBsYWtrb29mc2EgUmVm"
    "ZXJlbmNlIHlvb2thYW4gZXJnYWEgU01TIGd1dXR1dSBiYXJyZWVmZmFtYWFuIGVyZ2FhLiIsCiAgICB9LAogICAgImR1cF9yZWZf"
    "b3V0X29mX2F0dGVtcHRzIjogewogICAgICAgICJhbSI6ICLwn5qrIDxiPuGIq+GItS3hiLDhiK0g4YuN4Yu14YmFIOGJsOGLsOGI"
    "reGMk+GIjSAoQXV0by1yZWplY3RlZCk8L2I+IOKAlCDhi63hiIUg4Yuo4Yiq4Y2I4Yio4YqV4Yi1IOGJgeGMpeGIrSDhiqDhiLXh"
    "iYDhi7XhiJ4g4YiI4YiM4YiLIOGMjeGLoiDhjKXhiYXhiJ0g4YiL4YutIOGLjeGIj+GIjeGNolxu4Yuoe21heH0g4YiZ4Yqo4Yir"
    "4YuO4YqVIOGMqOGIreGIsOGLi+GIjeGNoyDhibLhiqzhibEg4Ymw4YiI4YmF4YmL4YiN4Y2iIOGKpeGJo+GKreGLjiDhiqXhipXh"
    "i7DhjIjhipMgL3BsYXkg4Yml4YiI4YuNIOGLreGMgOGIneGIqSDhi4jhi63hiJ0g4Yqg4Yu14Yia4YqR4YqVIOGLq+GKkOGMi+GM"
    "jeGIqeGNoiIsCiAgICAgICAgImVuIjogIvCfmqsgPGI+QXV0by1yZWplY3RlZDwvYj4g4oCUIHRoaXMgcmVmZXJlbmNlIG51bWJl"
    "ciBoYXMgYWxyZWFkeSBiZWVuIHVzZWQgZm9yIGFub3RoZXIgcHVyY2hhc2UuXG5Zb3UndmUgdXNlZCBhbGwge21heH0gYXR0ZW1w"
    "dHMsIHRoZSB0aWNrZXQgaGFzIGJlZW4gcmVsZWFzZWQuIFBsZWFzZSBzdGFydCBhZ2FpbiB3aXRoIC9wbGF5IG9yIGNvbnRhY3Qg"
    "dGhlIGFkbWluLiIsCiAgICAgICAgIm9tIjogIvCfmqsgPGI+T2ZpaW4gZGlkZGVlcmEgKEF1dG8tcmVqZWN0ZWQpPC9iPiDigJQg"
    "bGFra29vZnNpIFJlZmVyZW5jZSBrdW4gZHVyYWFuIGJpdHRhYSBiaXJhYSBpcnJhdHRpIGZheXlhZGFtZWVyYS5cbllhYWxpaSB7"
    "bWF4fSBrZWVzc2FuIGZpeHhhbmlpdHR1LCB0aWtlZXRpaW4gZ2FkaSBsYWtraWZhbWVlcmEuIE1hYWxvbyAvcGxheSBqZWRoYWEg"
    "YW1tYXMgamFscWFiYWEgeW9va2FhbiBhZG1pbiBxdXVubmFtYWEuIiwKICAgIH0sCiAgICAiZHVwX3JlZl9yZXRyeSI6IHsKICAg"
    "ICAgICAiYW0iOiAi8J+aqyA8Yj7hiKvhiLUt4Yiw4YitIOGLjeGLteGJhSDhibDhi7DhiK3hjJPhiI0gKEF1dG8tcmVqZWN0ZWQp"
    "PC9iPiDigJQg4Yut4YiFIOGLqOGIquGNiOGIqOGKleGItSDhiYHhjKXhiK0g4Yqg4Yi14YmA4Yu14YieIOGIiOGIjOGIiyDhjI3h"
    "i6Ig4Yyl4YmF4YidIOGIi+GLrSDhi43hiI/hiI3hjaMg4Yi14YiI4Yua4YiFIOGIiOGLmuGIhSDhjI3hi6Ig4YiK4Yur4YyI4YiI"
    "4YyN4YiNIOGKoOGLreGJveGIjeGIneGNolxu4Yql4Ymj4Yqt4YuOIOGJteGKreGKreGIiOGKm+GLjeGKlSDhi6jhiq3hjY3hi6vh"
    "i47hipUg4Yiq4Y2I4Yio4YqV4Yi1IOGJgeGMpeGIrSDhiqXhipXhi7DhjIjhipMg4Yur4Yio4YyL4YyN4Yyh4YqTIOGLreGIi+GK"
    "qSAoVHJ5IGFnYWluKeGNoyDhi4jhi63hiJ0g4Yqg4Yu14Yia4YqR4YqVIOGLq+GKkOGMi+GMjeGIqeGNolxu8J+UgSDhi6jhiYDh"
    "iKkg4YiZ4Yqo4Yir4YuO4Ym94Y2mIHtyZW1haW5pbmd9IiwKICAgICAgICAiZW4iOiAi8J+aqyA8Yj5BdXRvLXJlamVjdGVkPC9i"
    "PiDigJQgdGhpcyByZWZlcmVuY2UgbnVtYmVyIGhhcyBhbHJlYWR5IGJlZW4gdXNlZCBmb3IgYW5vdGhlciBwdXJjaGFzZSwgc28g"
    "aXQgY2FuJ3QgYmUgdXNlZCBmb3IgdGhpcyBvbmUuXG5QbGVhc2UgZG91YmxlLWNoZWNrIGFuZCByZXNlbmQgeW91ciBjb3JyZWN0"
    "IHBheW1lbnQgcmVmZXJlbmNlIG51bWJlciAoVHJ5IGFnYWluKSwgb3IgY29udGFjdCB0aGUgYWRtaW4uXG7wn5SBIEF0dGVtcHRz"
    "IGxlZnQ6IHtyZW1haW5pbmd9IiwKICAgICAgICAib20iOiAi8J+aqyA8Yj5PZmlpbiBkaWRkZWVyYSAoQXV0by1yZWplY3RlZCk8"
    "L2I+IOKAlCBsYWtrb29mc2kgUmVmZXJlbmNlIGt1biBkdXJhYW4gYml0dGFhIGJpcmFhIGlycmF0dGkgZmF5eWFkYW1lZXJhLCBr"
    "YW5hYWZ1dSBrYW5hYWYgaGluIGZheXlhZHUuXG5NYWFsb28gbGFra29vZnNhIFJlZmVyZW5jZSBrYWZmYWx0aWkga2Vlc3NhbiBz"
    "aXJyaWkgdGEnZSBtaXJrYW5lZWZmYWRoYWF0aWkgZXJnYWEgKFRyeSBhZ2FpbiksIHlvb2thYW4gYWRtaW4gcXV1bm5hbWFhLlxu"
    "8J+UgSBZYWFsaWkgaGFmZToge3JlbWFpbmluZ30iLAogICAgfSwKICAgICJwYXltZW50X2NhcHRpb25fZnVsbCI6IHsKICAgICAg"
    "ICAiYW0iOiAoIntlbW9qaX0gPGI+e2xhYmVsfTwvYj5cblxuIgogICAgICAgICAgICAgICAi4o+x77iPIDxiPuGLqOGJgOGIqCDh"
    "jIrhi5zhjaY8L2I+IH57bWluc30g4Yuw4YmC4YmDICjhiYHhjKXhiK0o4YuO4Ym5KSDhiqXhiLXhiqjhi5rhi6vhi40g4Yml4Ym7"
    "IOGLreGLq+GLneGIjeGLjuGJs+GIjSlcblxuIgogICAgICAgICAgICAgICAi8J+StSA8Yj7hi6jhiJrhiqjhjY3hiInhibUg4Yyg"
    "4YmF4YiL4YiLIOGImOGMoOGKleGNpjwvYj4ge3ByaWNlOi4wZn0g4Yml4YitXG4iCiAgICAgICAgICAgICAgICLwn5GkIDxiPuGL"
    "qOGKoOGKq+GLjeGKleGJtSDhiLXhiJ3hjaY8L2I+IHtob2xkZXJ9XG4iCiAgICAgICAgICAgICAgICLwn5SiIDxiPuGLqOGKoOGK"
    "q+GLjeGKleGJtS/hiLXhiI3hiq0g4YmB4Yyl4Yit4Y2mPC9iPiA8Y29kZT57YWNjb3VudH08L2NvZGU+XG5cbiIKICAgICAgICAg"
    "ICAgICAgIuKEue+4jyDhiq3hjY3hi6sg4Yqo4Y2I4Yy44YiZIOGJoOGKi+GIiyDhi6jhi7DhiKjhiLDhi47hibXhipUg4Yuo4Yiq"
    "4Y2I4Yio4YqV4Yi1IOGJgeGMpeGIreGNoyDhiqh7bGFiZWx9IOGLqOGLsOGIqOGIsOGLjuGJteGKlSDhiJnhiIkg4Yuo4Yqk4Yi1"
    "4Yqk4Yid4Yqk4Yi1IOGImOGIjeGLleGKreGJteGNoyDhi4jhi63hiJ0g4Yuo4Yqt4Y2N4YurIOGIm+GIqOGMi+GMiOGMqyDhiLXh"
    "iq3hiKrhipXhiL7hibUg4Yql4Yua4YiFIOGJpuGJtSDhiIvhi60g4Yut4YiL4Yqp4Ym14Y2iXG4iCiAgICAgICAgICAgICAgICLi"
    "mqDvuI8g4Yuoe2xhYmVsfSDhiKrhjYjhiKjhipXhiLUg4YmB4Yyl4YitIOGIjeGKrSDhiqvhiI3hiIbhipAg4YmF4Yit4Yy44Ym1"
    "IOGKpeGKleGLsOGIm+GLreGIhuGKlSDhiI3hiaUg4Yut4Ymg4YiJIC0ge3JlZl9oaW50fVxuXG4iCiAgICAgICAgICAgICAgICLw"
    "n5qrIDxiPuGIm+GIteGJs+GLiOGIu+GNpjwvYj4g4Yqo4YiM4YiLIOGJo+GKleGKrSDhibXhiKvhipXhiLXhjYjhiK0gKHRyYW5z"
    "ZmVyIGZyb20gb3RoZXIgYmFuaykg4Yuo4Ymw4YiL4YqoIOGKreGNjeGLqyDhibDhiYDhiaPhi63hipDhibUg4Yuo4YiI4YuN4Yid"
    "4Y2iICIKICAgICAgICAgICAgICAgIuGKpeGJo+GKreGLjiDhiaDhiYDhjKXhibMg4Yqoe2xhYmVsfSDhiaXhibsg4Yut4Yqt4Y2I"
    "4YiJ4Y2iIiksCiAgICAgICAgImVuIjogKCJ7ZW1vaml9IDxiPntsYWJlbH08L2I+XG5cbiIKICAgICAgICAgICAgICAgIuKPse+4"
    "jyA8Yj5UaW1lIGxlZnQ6PC9iPiB+e21pbnN9IG1pbiAodGhlIHRpY2tldChzKSBhcmUgaGVsZCBmb3IgeW91IG9ubHkgdW50aWwg"
    "dGhlbilcblxuIgogICAgICAgICAgICAgICAi8J+StSA8Yj5Ub3RhbCBhbW91bnQgdG8gcGF5OjwvYj4ge3ByaWNlOi4wZn0gYmly"
    "clxuIgogICAgICAgICAgICAgICAi8J+RpCA8Yj5BY2NvdW50IG5hbWU6PC9iPiB7aG9sZGVyfVxuIgogICAgICAgICAgICAgICAi"
    "8J+UoiA8Yj5BY2NvdW50L3Bob25lIG51bWJlcjo8L2I+IDxjb2RlPnthY2NvdW50fTwvY29kZT5cblxuIgogICAgICAgICAgICAg"
    "ICAi4oS577iPIEFmdGVyIHBheWluZywgcGxlYXNlIHNlbmQgdGhlIHJlZmVyZW5jZSBudW1iZXIsIHRoZSBmdWxsIFNNUyBtZXNz"
    "YWdlIHlvdSByZWNlaXZlZCBmcm9tIHtsYWJlbH0sIG9yIGEgc2NyZWVuc2hvdCBvZiB0aGUgcGF5bWVudCBjb25maXJtYXRpb24s"
    "IGhlcmUgaW4gdGhlIGNoYXQuXG4iCiAgICAgICAgICAgICAgICLimqDvuI8gTm90ZSB0aGUge2xhYmVsfSByZWZlcmVuY2UgbnVt"
    "YmVyIHdvbid0IGJlIGFjY2VwdGVkIGluIHRoZSB3cm9uZyBmb3JtYXQg4oCUIHtyZWZfaGludH1cblxuIgogICAgICAgICAgICAg"
    "ICAi8J+aqyA8Yj5Ob3RlOjwvYj4gUGF5bWVudHMgc2VudCBhcyBhIHRyYW5zZmVyIGZyb20gYW5vdGhlciBiYW5rIGFyZSBub3Qg"
    "YWNjZXB0ZWQuICIKICAgICAgICAgICAgICAgIlBsZWFzZSBwYXkgZGlyZWN0bHkgZnJvbSB7bGFiZWx9IG9ubHkuIiksCiAgICAg"
    "ICAgIm9tIjogKCJ7ZW1vaml9IDxiPntsYWJlbH08L2I+XG5cbiIKICAgICAgICAgICAgICAgIuKPse+4jyA8Yj5ZZXJvbyBoYWZl"
    "OjwvYj4gfnttaW5zfSBkYXFpaXFhYSAobGFra29vZnNpKHd3YW4pIGhhbmdhIHNhbmFhIHFvZmEgaXNpbmlpZiBxYWJhbWEpXG5c"
    "biIKICAgICAgICAgICAgICAgIvCfkrUgPGI+V2FsaWlnYWxhIGthZmZhbHRpaTo8L2I+IHtwcmljZTouMGZ9IGJpcnJpaVxuIgog"
    "ICAgICAgICAgICAgICAi8J+RpCA8Yj5NYXFhYSBoZXJyZWdhOjwvYj4ge2hvbGRlcn1cbiIKICAgICAgICAgICAgICAgIvCflKIg"
    "PGI+TGFra29vZnNhIGhlcnJlZ2EvYmlsYmlsYWE6PC9iPiA8Y29kZT57YWNjb3VudH08L2NvZGU+XG5cbiIKICAgICAgICAgICAg"
    "ICAgIuKEue+4jyBLYWZmYWx0aWkgYm9vZGEsIGxha2tvb2ZzYSBSZWZlcmVuY2UsIGVyZ2FhIFNNUyBndXV0dXUge2xhYmVsfSBp"
    "cnJhYSBhcmdhdHRhbiwgeW9va2FhbiBzdXVyYWEgKHNjcmVlbnNob3QpIHJhZ2FhIGthZmZhbHRpaSBhc2l0dGkgZXJnYWEuXG4i"
    "CiAgICAgICAgICAgICAgICLimqDvuI8gTGFra29vZnNpIFJlZmVyZW5jZSBJRCB7bGFiZWx9IGJpZmEgZG9nb25nb3JhYSB0YSdl"
    "IGFra2EgaGluIGZ1ZGhhdGFtbmUgaHViYWRoYWEgLSB7cmVmX2hpbnR9XG5cbiIKICAgICAgICAgICAgICAgIvCfmqsgPGI+SHVi"
    "YWNoaWlzYTo8L2I+IEthZmZhbHRpaW4gYmFhbmtpaSBiaXJhYXRpaW4gKHRyYW5zZmVyKSBlcmdhbWUgaGluIGZ1ZGhhdGFtdS4g"
    "IgogICAgICAgICAgICAgICAiTWFhbG9vIGthbGxhdHRpaW4ge2xhYmVsfSBxb2ZhIGlycmFhIGthZmZhbGFhLiIpLAogICAgfSwK"
    "ICAgICJoZWxwX3RleHQiOiB7CiAgICAgICAgImFtIjogKCLihLnvuI8gPGI+4YiY4YiY4Yiq4YurPC9iPlxuXG4iCiAgICAgICAg"
    "ICAgICAgICIx77iP4oOjIPCfjq4gwqvhiJjhjKvhi4jhibXCuyDhi63hjKvhipEg4Yql4YqTIOGKleGJgSDhi5nhiK0g4Yut4Yid"
    "4Yio4Yyh4Y2iXG4iCiAgICAgICAgICAgICAgICIy77iP4oOjIOGKq+GIjeGJsOGLq+GLmCDhiYHhjKXhiK0g4Yut4Yid4Yio4Yyh"
    "ICjhiIh7bWluc30g4Yuw4YmC4YmDIOGLreGLq+GLneGIjeGLjuGJs+GIjSnhjaJcbiIKICAgICAgICAgICAgICAgIjPvuI/ig6Mg"
    "4Yuo4Yqt4Y2N4YurIOGLmOGLtCDhi63hiJ3hiKjhjKHhipMg4Yqt4Y2N4Yur4YuN4YqVIOGLreGNiOGMveGImeGNolxuIgogICAg"
    "ICAgICAgICAgICAiNO+4j+KDoyBSZWZlcmVuY2UgSUQg4Yql4YqTIOGLqOGLsOGIqOGIsOGKnSBzY3JlZW5zaG90IOGLreGIi+GK"
    "qeGNolxuIgogICAgICAgICAgICAgICAiNe+4j+KDoyDhiq3hjY3hi6vhi40g4Yiy4Yio4YyL4YyI4YylIOGJgeGMpeGIqSDhiaDh"
    "iYvhiJrhipDhibUg4Yuo4Yql4Yit4Yi14YuOIOGLreGIhuGKk+GIjeGNolxuXG4iCiAgICAgICAgICAgICAgICLwn5OeIOGJsOGM"
    "qOGIm+GIqiDhiqXhiK3hi7PhibMg4Yqr4Yi14Y2I4YiI4YyI4YuO4Ym1IMKr4Yql4Yit4Yuz4Ymzwrsg4Yut4Yyr4YqR4Y2iIiks"
    "CiAgICAgICAgImVuIjogKCLihLnvuI8gPGI+SGVscDwvYj5cblxuIgogICAgICAgICAgICAgICAiMe+4j+KDoyBUYXAg8J+OriBc"
    "IlBsYXlcIiBhbmQgY2hvb3NlIGFuIGFjdGl2ZSByb3VuZC5cbiIKICAgICAgICAgICAgICAgIjLvuI/ig6MgUGljayBhbiBhdmFp"
    "bGFibGUgbnVtYmVyIChpdCdzIGhlbGQgZm9yIHlvdSBmb3Ige21pbnN9IG1pbnV0ZXMpLlxuIgogICAgICAgICAgICAgICAiM++4"
    "j+KDoyBDaG9vc2UgYSBwYXltZW50IG1ldGhvZCBhbmQgY29tcGxldGUgdGhlIHBheW1lbnQuXG4iCiAgICAgICAgICAgICAgICI0"
    "77iP4oOjIFNlbmQgdGhlIFJlZmVyZW5jZSBJRCBhbmQgYSBzY3JlZW5zaG90IG9mIHRoZSByZWNlaXB0LlxuIgogICAgICAgICAg"
    "ICAgICAiNe+4j+KDoyBPbmNlIHBheW1lbnQgaXMgdmVyaWZpZWQsIHRoZSB0aWNrZXQgaXMgcGVybWFuZW50bHkgeW91cnMuXG5c"
    "biIKICAgICAgICAgICAgICAgIvCfk54gTmVlZCBtb3JlIGhlbHA/IFRhcCBcIlN1cHBvcnRcIi4iKSwKICAgICAgICAib20iOiAo"
    "IuKEue+4jyA8Yj5RYWplZWxmYW1hPC9iPlxuXG4iCiAgICAgICAgICAgICAgICIx77iP4oOjIPCfjq4gXCJUYXBoYWNodXVcIiB0"
    "dXFhYXRpaSBtYXJzYWEgaG9qaWlycmEgamlydSBmaWxhZGhhYS5cbiIKICAgICAgICAgICAgICAgIjLvuI/ig6MgTGFra29vZnNh"
    "IGJhbmFhIGZpbGFkaGFhIChkYXFpaXFhYSB7bWluc30gaXNpbmlpZiBxYWJhbWEpLlxuIgogICAgICAgICAgICAgICAiM++4j+KD"
    "oyBNYWxhIGthZmZhbHRpaSBmaWxhZGhhYXRpaSBrYWZmYWx0aWkgeHVtdXJhYS5cbiIKICAgICAgICAgICAgICAgIjTvuI/ig6Mg"
    "UmVmZXJlbmNlIElEIGZpIGZha2tpaSByYWdhYSBrYWZmYWx0aWkgZXJnYWEuXG4iCiAgICAgICAgICAgICAgICI177iP4oOjIEth"
    "ZmZhbHRpaW4gZXJnYSBtaXJrYW5hYSdlZSBib29kYSwgbGFra29vZnNpIHN1biB5ZXJvbyBodW5kYWFmIGtlZXNzYW4gdGEnYS5c"
    "blxuIgogICAgICAgICAgICAgICAi8J+TniBEZWVnZ2Fyc2EgZGFiYWxhdGFhIHlvbyBiYXJiYWFkZGFuIFwiRGVlZ2dhcnNhXCIg"
    "dHVxYWEuIiksCiAgICB9LAp9CgpkZWYgTCh1c2VyX2lkLCBrZXksICoqa3dhcmdzKToKICAgICIiIuGJsOGMq+GLi+GJuSDhiaPh"
    "iLXhiYDhiJjhjKDhi40g4YmL4YqV4YmLIChhbS9lbi9vbSkg4Yuo4Ymw4Ymw4Yio4YyO4YiYIOGMveGIgeGNjSDhi6jhiJrhiJjh"
    "iI3hiLUgaGVscGVyIiIiCiAgICBsYW5nID0gZ2V0X2xhbmcodXNlcl9pZCkKICAgIHRlbXBsYXRlID0gVFhULmdldChrZXksIHt9"
    "KS5nZXQobGFuZykgb3IgVFhULmdldChrZXksIHt9KS5nZXQoREVGQVVMVF9MQU5HLCAiIikKICAgIHJldHVybiB0ZW1wbGF0ZS5m"
    "b3JtYXQoKiprd2FyZ3MpIGlmIGt3YXJncyBlbHNlIHRlbXBsYXRlCgpQQVlNRU5UX0xBQkVMX0tFWVMgPSB7InRlbGViaXJyIjog"
    "InBtX3RlbGViaXJyIiwgImNiZWJpcnIiOiAicG1fY2JlYmlyciIsICJtYW51YWxfY2FsbCI6ICJwbV9tYW51YWxfY2FsbCJ9Cgpk"
    "ZWYgcGF5bWVudF9sYWJlbChtZXRob2Rfa2V5LCB1c2VyX2lkKToKICAgICIiIuGLqOGKreGNjeGLqyDhi5jhi7QgKHRlbGViaXJy"
    "L2NiZWJpcnIvbWFudWFsX2NhbGwpIOGIteGInSDhiaDhibDhjKvhi4vhibkg4YmL4YqV4YmLIOGLqOGImuGImOGIjeGItSBoZWxw"
    "ZXIiIiIKICAgIHR4dF9rZXkgPSBQQVlNRU5UX0xBQkVMX0tFWVMuZ2V0KG1ldGhvZF9rZXkpCiAgICBpZiB0eHRfa2V5OgogICAg"
    "ICAgIHJldHVybiBMKHVzZXJfaWQsIHR4dF9rZXkpCiAgICByZXR1cm4gUEFZTUVOVF9NRVRIT0RTLmdldChtZXRob2Rfa2V5LCB7"
    "fSkuZ2V0KCJsYWJlbCIsIG1ldGhvZF9rZXkpCgoKIyDhibDhjKvhi4vhib7hib0g4Ymg4YmB4Yyl4YitIOGKpeGLqOGMoOGJoOGJ"
    "gSDhiqjhiIbhipAg4YmB4Yyl4YipIOGIsuGIiOGJgOGJhSDhiIjhiJvhiLPhi4jhiYUKIyAocm91bmRfaWQsIHRpY2tldF9udW0p"
    "IC0+IHNldCh1c2VyX2lkKQp0aWNrZXRfd2F0Y2hlcnMgPSB7fQoKIyDhiKrhjYjhiKjhipXhiLUg4Yqo4Ymw4YiL4YqoIOGJoOGK"
    "i+GIiyDhi7DhiKjhiLDhip0gc2NyZWVuc2hvdCDhi6jhiJrhjKDhiaDhiYXhiaDhibUKIyB1c2VyX2lkIC0+IHsicm91bmRfaWQi"
    "OiBpbnQsICJ0aWNrZXRfbnVtIjogaW50LCAicmVmIjogc3RyfQpyZWNlaXB0X2NvbnRleHRzID0ge30KClBIT05FX1BBVFRFUk4g"
    "PSByZS5jb21waWxlKHInXig/OlwrPzI1MXwwKT9bNzldXGR7OH0kJykKCiMgLS0tIEVuZ2xpc2gvQW1oYXJpYyBjb250cm9sLXdv"
    "cmQgaW5wdXQgKOGKoOGLteGImuGKlSDhiaDhiqXhipXhjI3hiIrhi53hipsg4YuI4Yut4YidIOGJoOGKoOGIm+GIreGKmyDhjL3h"
    "iIHhjY0g4Ym14YuV4Yub4YuZ4YqVIOGImOGIteGIqOGLnS/hiJjhi53hiIjhiI0g4Yql4YqV4Yuy4Ym94YiNKSAtLS0KX0NBTkNF"
    "TF9XT1JEUyA9IHsiL2NhbmNlbCIsICJjYW5jZWwiLCAi4Yiw4Yit4YudIiwgIuGJsOGLiOGLjSIsICLhiqDhiYvhiK3hjKUiLCAi"
    "4Yqg4YmB4YidIn0KX1NLSVBfV09SRFMgPSB7Ii9za2lwIiwgInNraXAiLCAi4Yud4YiI4YiNIiwgIuGLqOGIiOGInSIsICLhibXh"
    "i53hiIjhiI0ifQpfT0ZGX1dPUkRTID0geyJvZmYiLCAiL29mZiIsICJyZW1vdmUiLCAiL3JlbW92ZSIsICJjbGVhciIsICIvY2xl"
    "YXIiLCAi4Yyl4Y2LIiwgIuGKoOGMpeGNiyIsICLhiqDhiLXhi4jhjI3hi7UifQojIMKr4YuI4YuwIOGKi+GIiyDhibDhiJjhiIjh"
    "iLXCuyAoQmFjay9VbmRvIG9uZSBzdGVwKSAtIOGLiuGLm+GIreGLtSDhi7DhiKjhjIMgKHN0ZXApIOGLjeGIteGMpSDhiaXhibsg"
    "4YuI4YuwIOGJgOGLsOGImOGLjSDhjKXhi6vhiYQg4YiI4YiY4YiY4YiI4Yi1IOGKpeGKleGMggojIOGKoOGMoOGJg+GIi+GLrSDh"
    "ibXhi5Xhi5vhi5nhipUgKOGIiOGIneGIs+GIjCAvbmV3cm91bmQg4Yir4Yix4YqVKSDhiIjhiJjhiLDhiKjhi50g4Yqg4Yut4Yuw"
    "4YiI4YidIC0g4YurIHN0aWxsIC9jYW5jZWwg4Yml4Ym7IOGKkOGLjeGNogpfQkFDS19XT1JEUyA9IHsiL2JhY2siLCAiYmFjayIs"
    "ICLhibDhiJjhiIjhiLUiLCAi4YuI4Yuw4YqL4YiLIiwgIuGLiOGLsCDhiovhiIsiLCAi4qyF77iPIOGJsOGImOGIiOGItSAoYmFj"
    "aykiLCAi4qyF77iPIn0KCmRlZiBfaXNfY2FuY2VsX3RleHQodGV4dDogc3RyKSAtPiBib29sOgogICAgIiIi4Ymw4Yyg4YmD4Yia"
    "4YuNICjhiqDhi7XhiJrhipUv4YiG4Yi14Ym1KSDhiaDhiqXhipXhjI3hiIrhi53hipsgKCdjYW5jZWwnLycvY2FuY2VsJykg4YuI"
    "4Yut4YidIOGJoOGKoOGIm+GIreGKmyAoJ+GIsOGIreGLnSfhjaMgJ+GJsOGLiOGLjSfhjaMgJ+GKoOGJi+GIreGMpSfhjaMgJ+GK"
    "oOGJgeGInScpCiAgICDhi6jhiIvhiqjhi43hipUg4Yuo4YiY4Yiw4Yio4YudIOGJteGLleGLm+GLnSDhiIjhi63hibYg4Yuo4Yia"
    "4Yur4YuN4YmFIC0g4Ymg4YqV4YmBIOGLsOGIqOGMgyAod2l6YXJkIHN0ZXApIOGLjeGIteGMpSDhiqvhiIgg4YuN4Yut4Yut4Ym1"
    "IOGIiOGImOGLjeGMo+GJtSDhi63hjKDhiYXhiJvhiI3hjaIiIiIKICAgIHJldHVybiAodGV4dCBvciAiIikuc3RyaXAoKS5sb3dl"
    "cigpIGluIF9DQU5DRUxfV09SRFMKCmRlZiBfaXNfc2tpcF90ZXh0KHRleHQ6IHN0cikgLT4gYm9vbDoKICAgICIiIuGJsOGMoOGJ"
    "g+GImuGLjSAo4Yqg4Yu14Yia4YqVL+GIhuGIteGJtSkg4Ymg4Yql4YqV4YyN4YiK4Yud4YqbICgnc2tpcCcvJy9za2lwJykg4YuI"
    "4Yut4YidIOGJoOGKoOGIm+GIreGKmyAoJ+GLneGIiOGIjSfhjaMgJ+GLqOGIiOGInScpIOGLqOGIi+GKqOGLjeGKlQogICAg4Yuo"
    "4YiY4Yud4YiI4YurICjhiqDhiJvhiKvhjK0g4Yuw4Yio4YyDIOGKoOGIiOGIm+GLteGIqOGMjSkg4Ym14YuV4Yub4YudIOGIiOGL"
    "reGJtiDhi6jhiJrhi6vhi43hiYXhjaIiIiIKICAgIHJldHVybiAodGV4dCBvciAiIikuc3RyaXAoKS5sb3dlcigpIGluIF9TS0lQ"
    "X1dPUkRTCgpkZWYgX2lzX29mZl90ZXh0KHRleHQ6IHN0cikgLT4gYm9vbDoKICAgICIiIuGJsOGMoOGJg+GImuGLjSAo4Yqg4Yu1"
    "4Yia4YqVL+GIhuGIteGJtSkg4Ymg4Yql4YqV4YyN4YiK4Yud4YqbICgnb2ZmJy8ncmVtb3ZlJy8nY2xlYXInKSDhi4jhi63hiJ0g"
    "4Ymg4Yqg4Yib4Yit4YqbICgn4Yyl4Y2LJ+GNoyAn4Yqg4Yyl4Y2LJ+GNoyAn4Yqg4Yi14YuI4YyN4Yu1JykKICAgIOGLqOGIi+GK"
    "qOGLjeGKlSDhi6jhiJvhjKXhjYvhibUv4Yuo4Yib4Yi14YuI4YyI4YyDIOGJteGLleGLm+GLnSDhiIjhi63hibYg4Yuo4Yia4Yur"
    "4YuN4YmFICjhiIjhiJ3hiLPhiIwgU01TIHdlYmhvb2sg4Yib4Yyl4Y2L4Ym1KeGNoiIiIgogICAgcmV0dXJuICh0ZXh0IG9yICIi"
    "KS5zdHJpcCgpLmxvd2VyKCkgaW4gX09GRl9XT1JEUwoKZGVmIF9pc19iYWNrX3RleHQodGV4dDogc3RyKSAtPiBib29sOgogICAg"
    "IiIi4Ymw4Yyg4YmD4Yia4YuNICjhiqDhi7XhiJrhipUv4YiG4Yi14Ym1KSDhiaDhiqXhipXhjI3hiIrhi53hipsgKCcvYmFjaycv"
    "J2JhY2snKSDhi4jhi63hiJ0g4Ymg4Yqg4Yib4Yit4YqbICgn4Ymw4YiY4YiI4Yi1Jy8n4YuI4Yuw4YqL4YiLJykg4Yuo4YiL4Yqo"
    "4YuN4YqVCiAgICDCq+GLiOGLsCDhiYDhi7DhiJjhi40g4Yuw4Yio4YyDIOGJsOGImOGIiOGItcK7IOGMpeGLq+GJhCDhiIjhi63h"
    "ibYg4Yuo4Yia4Yur4YuN4YmFIC0g4YuK4Yub4Yit4Yu1IOGLjeGIteGMpSDhiqvhiIjhipXhiaDhibUg4Yuw4Yio4YyDIOGKoOGK"
    "leGLtSDhi7DhiKjhjIMg4Yml4Ym7IOGLiOGLsCDhiovhiIsg4YiI4YiY4YiY4YiI4Yi1CiAgICDhiqXhipXhjIIgKOGKpeGKleGL"
    "sCAvY2FuY2VsIOGIs+GLreGIhuGKlSkg4Yqg4Yyg4YmD4YiL4YutIOGIguGLsOGJseGKlSDhiIjhiJvhiYvhiKjhjKUg4Yqg4Yut"
    "4Yuw4YiI4Yid4Y2iIiIiCiAgICByZXR1cm4gKHRleHQgb3IgIiIpLnN0cmlwKCkubG93ZXIoKSBpbiBfQkFDS19XT1JEUwoKIyAt"
    "LS0g4Yuo4Yib4Yi14Ymz4YuI4YmC4YurIOGIgeGKkOGJsyAoQnJvYWRjYXN0L0Fubm91bmNlIFN0YXRlKSAtLS0KYnJvYWRjYXN0"
    "X3N0YXRlID0ge30KCmxvZ2dpbmcuYmFzaWNDb25maWcoZm9ybWF0PSIlKGFzY3RpbWUpcyAtICUobmFtZSlzIC0gJShsZXZlbG5h"
    "bWUpcyAtICUobWVzc2FnZSlzIiwgbGV2ZWw9bG9nZ2luZy5JTkZPKQpsb2dnZXIgPSBsb2dnaW5nLmdldExvZ2dlcihfX25hbWVf"
    "XykKCiMgLS0tIOGLiOGLsCDhi7LhiLXhiq0g4Yib4Yi14YmA4YiY4YylL+GImOGMq+GKlSAoUGVyc2lzdGVuY2UpIC0tLQojIOGI"
    "m+GIteGJs+GLiOGIu+GNpiDhi63hiIUg4Y2L4Yut4YiNIChsb3R0ZXJ5X3N0YXRlLmpzb24pIOGJoOGIteGKreGIquGNleGJsSDh"
    "iqDhjKDhjIjhiaUg4Ymj4YiI4YuNIOGIm+GIheGLsOGIrSDhi43hiLXhjKUg4Yut4YmA4YiY4Yyj4YiN4Y2jIOGKpeGKk+GInQoj"
    "IOGJpuGJsSDhiaPhi63hiLDhiKvhiJ0gKOGJgOGKk+GJtSDhiaLhi5jhjIsg4Yql4YqV4YqzKSDhiaDhi5rhi6vhi40g4Ymm4Ymz"
    "IOGJsOGJgOGIneGMpiDhi63hiYbhi6vhiI0gLSDhiI3hiq0g4Yql4YqV4YuwIOGIm+GKleGKm+GLjeGInSDhibDhiKsg4Y2L4Yut"
    "4YiN4Y2iCiMg4Yi14YiI4Yua4YiF4Y2mCiMgICDigKIg4YuZ4Yiu4Ym94Y2jIOGJteGKrOGJtuGJveGNoyDhibDhjKvhi4vhib7h"
    "ib3hjaMg4Yuo4Ymw4Yyg4YmA4YiZIFNNUyDhiKrhjYjhiKjhipXhiLbhib3hjaMg4YuI4YuY4YmwIC0g4YiB4YiJ4YidIOGImOGI"
    "qOGMgyDhiabhibEg4Ymg4Yia4Yiw4Yir4Ymg4Ym1IOGMiuGLnCDhiIHhiIkKIyAgICAgKHNhdmVfc3RhdGUoKSDhiaDhibDhjKDh"
    "iKsg4YmB4Yyl4YitIC0g4Yqo4Yua4YiFIOGJoOGJs+GJvSDhiaDhiq7hi7Eg4YuN4Yi14YylIOGJoOGLqOGJteGKm+GLjeGInSDh"
    "iIjhi43hjKUg4Ymg4YqL4YiLKSDhi4jhi7Dhi5rhiIUg4Y2L4Yut4YiNIOGLreGMu+GNi+GIjeGNogojICAg4oCiIOGJpuGJsSDh"
    "iLLhi5jhjIsv4Yqu4Yid4Y2S4Yup4Ymw4YipIOGIsuGMoOGNiy/hiqLhipXhibDhiK3hipThibUg4Ymi4YmL4Yio4YylIC0g4Y2L"
    "4Yut4YiJIOGKpeGKleGLsOGJsOGJgOGImOGMoCDhi63hiYbhi6vhiI3hjaMg4Yid4YqV4YidIOGKoOGLreGMoOGNi+GIneGNogoj"
    "ICAg4oCiIOGJpuGJsSDhiqXhipXhi7DhjIjhipMg4Yiy4YqQ4YizICjhiqjhi7DhiYLhiYPhi47hib0g4YuI4Yut4YidIOGKqOGJ"
    "gOGKk+GJtSDhiaDhiovhiIsg4Ymi4YiG4YqV4YidKSBsb2FkX3N0YXRlKCkg4Yir4YixIOGJoOGIq+GItS3hiLDhiK0g4Yut4YiF"
    "4YqVIOGNi+GLreGIjQojICAgICDhiaDhiJvhipXhiaDhiaUg4YiB4YiJ4YqV4YidIOGImOGIqOGMgyDhi4jhi7Ag4Yib4YiF4Yuw"
    "4YioIOGJteGLjeGIteGJsyAobWVtb3J5KSDhi63hiJjhiI3hiLPhiI0gLSDhiI3hiq0g4Ymm4YmxIOGNiOGMveGIniDhiqXhipXh"
    "i7PhiI3hjKDhjYsg4Yur4YiF4YiN4Y2iCiMgICDigKIg4Yy94YiB4Y2JIOGLiOGLsCDhjYvhi63hiI0g4Yiy4Yy74Y2NIOGJoOGI"
    "mOGMgOGImOGIquGLqyDhi4jhi7AgLnRtcCDhjYvhi63hiI0g4Ymw4Yy94Y2OIOGJoOGKi+GIiyBvcy5yZXBsYWNlKCkg4Yi14YiI"
    "4Yia4Yuw4Yio4YyNIChhdG9taWMgd3JpdGUp4Y2jCiMgICAgIOGJoOGMveGIgeGNjSDhiIvhi60g4Yiz4YiIIOGJpuGJsSDhiaLh"
    "iYvhiKjhjKUv4Ymi4Yyg4Y2LIOGKpeGKleGKsyDhipDhiaPhiKkgbG90dGVyeV9zdGF0ZS5qc29uIOGKoOGLreGJoOGIi+GIveGI"
    "nS/hiqDhi63hjKDhjYvhiJ3hjaIKIyAgIOKAoiDhiaXhibjhipvhi40g4Yib4Yu14Yio4YyNIOGLqOGIjOGIiOGJpeGLjuGJtSDh"
    "ipDhjIjhiK3hjaYgbG90dGVyeV9zdGF0ZS5qc29uIOGNi+GLreGIieGKlSDhiqDhi63hiLDhiK3hi5kv4Yqg4Yur4YqV4YmA4Yiz"
    "4YmF4YixICjhiLXhiq3hiKrhjZXhibEg4Yqr4YiI4Ymg4Ym1CiMgICAgIOGIm+GIheGLsOGIrSDhi43hjK0p4Y2iIOGNi+GLreGI"
    "iSDhiqXhiLXhiqvhiIgg4Yu14Yio4Yi1IOGImOGIqOGMg+GLjSDhiIjhi5jhiIvhiIjhiJ0gKOGJpuGJsSDhiLPhi63hiLDhiKsg"
    "4Yml4YuZIOGJgOGKk+GJtSDhiaLhiYbhi63hiJ0pIOGLreGJhuGLq+GIjeGNogpTVEFURV9GSUxFID0gb3MucGF0aC5qb2luKG9z"
    "LnBhdGguZGlybmFtZShvcy5wYXRoLmFic3BhdGgoX19maWxlX18pKSwgImxvdHRlcnlfc3RhdGUuanNvbiIpCgojIC0tLSBEdXJh"
    "YmxlIHN0b3JhZ2UgLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tCiMgc3VwZXJhZG1p"
    "bl9hbGxfaW5fb25lLnB5IGluamVjdHMgYSBsaXZlIFBlcnNpc3RlbnRTdG9yZSAoUzMtY29tcGF0aWJsZQojIG9iamVjdCBzdG9y"
    "YWdlLCBvciBsb2NhbCBkaXNrIHVuZGVyIHRoaXMgaG9zdCdzIG93biBEQVRBX0RJUi9ob3N0cy88aWQ+LwojIGZvbGRlciwgZGVw"
    "ZW5kaW5nIG9uIGhvdyB0aGUgc3VwZXIgYWRtaW4gcHJvY2VzcyBpcyBjb25maWd1cmVkKS4gSWYgaXQncwojIGV2ZXIgbWlzc2lu"
    "ZyAoc2hvdWxkbid0IGhhcHBlbiAtLSB0aGlzIGZpbGUgaXMgb25seSBldmVyIGxhdW5jaGVkIHZpYQojIGluamVjdGlvbiwgbmV2"
    "ZXIgc3RhbmRhbG9uZSksIGZhbGwgYmFjayB0byB0aGUgcGxhaW4gbG9jYWwgU1RBVEVfRklMRQojIG5leHQgdG8gdGhpcyBzY3Jp"
    "cHQuCl9TVE9SRSA9IF9fSU5KRUNURURfXy5nZXQoInN0b3JhZ2UiKQoKZGVmIF9kdF90b19zdHIoZHQpOgogICAgcmV0dXJuIGR0"
    "Lmlzb2Zvcm1hdCgpIGlmIGR0IGVsc2UgTm9uZQoKZGVmIF9zdHJfdG9fZHQocyk6CiAgICByZXR1cm4gZGF0ZXRpbWUuZnJvbWlz"
    "b2Zvcm1hdChzKSBpZiBzIGVsc2UgTm9uZQoKZGVmIHNhdmVfc3RhdGUoKToKICAgIGdsb2JhbCBob3N0X3BhdXNlZAogICAgIiIi"
    "4Yuo4Yqg4YiB4YqR4YqVIOGMqOGLi+GJsyDhiIHhipThibMgKOGIgeGIieGKleGInSDhi5nhiK7hib0v4Ymw4Yyr4YuL4Ym+4Ym9"
    "KSDhi4jhi7Ag4Yuy4Yi14YqtIOGLqOGImuGLq+GIteGJgOGIneGMpSBoZWxwZXIiIiIKICAgIHRyeToKICAgICAgICBkYXRhID0g"
    "ewogICAgICAgICAgICAibmV4dF9yb3VuZF9pZCI6IG5leHRfcm91bmRfaWQsCiAgICAgICAgICAgICJob3N0X3BhdXNlZCI6IGhv"
    "c3RfcGF1c2VkLAogICAgICAgICAgICAiaG9zdF9wYXVzZWRfcmVhc29uIjogaG9zdF9wYXVzZWRfcmVhc29uLAogICAgICAgICAg"
    "ICAiaG9zdF9jcmVkaXQiOiBob3N0X2NyZWRpdCwKICAgICAgICAgICAgIndpbm5lcl9zbG90cyI6IFdJTk5FUl9TTE9UUywKICAg"
    "ICAgICAgICAgInJvdW5kcyI6IHsKICAgICAgICAgICAgICAgIHN0cihyaWQpOiB7CiAgICAgICAgICAgICAgICAgICAgIm5hbWUi"
    "OiByLmdldCgibmFtZSIpLAogICAgICAgICAgICAgICAgICAgICJkZXNjcmlwdGlvbiI6IHIuZ2V0KCJkZXNjcmlwdGlvbiIpLAog"
    "ICAgICAgICAgICAgICAgICAgICJpbWFnZV9maWxlX2lkIjogci5nZXQoImltYWdlX2ZpbGVfaWQiKSwKICAgICAgICAgICAgICAg"
    "ICAgICAibnVtX3RpY2tldHMiOiByWyJudW1fdGlja2V0cyJdLAogICAgICAgICAgICAgICAgICAgICJwcmljZSI6IHJbInByaWNl"
    "Il0sCiAgICAgICAgICAgICAgICAgICAgInN0YXR1cyI6IHJbInN0YXR1cyJdLAogICAgICAgICAgICAgICAgICAgICJ0aWNrZXRz"
    "IjogewogICAgICAgICAgICAgICAgICAgICAgICBzdHIoaSk6IHsqKnQsICJleHBpcmVzX2F0IjogX2R0X3RvX3N0cih0WyJleHBp"
    "cmVzX2F0Il0pfQogICAgICAgICAgICAgICAgICAgICAgICBmb3IgaSwgdCBpbiByWyJ0aWNrZXRzIl0uaXRlbXMoKQogICAgICAg"
    "ICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgICAgICAgIndpbm5lcnMiOiBbCiAgICAgICAgICAgICAgICAgICAgICAgIHsq"
    "KncsICJzZXRfYXQiOiBfZHRfdG9fc3RyKHcuZ2V0KCJzZXRfYXQiKSl9CiAgICAgICAgICAgICAgICAgICAgICAgIGZvciB3IGlu"
    "IHIuZ2V0KCJ3aW5uZXJzIiwgW10pCiAgICAgICAgICAgICAgICAgICAgXSwKICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAg"
    "ICAgIGZvciByaWQsIHIgaW4gcm91bmRzLml0ZW1zKCkKICAgICAgICAgICAgfSwKICAgICAgICAgICAgInBsYXllcnMiOiB7c3Ry"
    "KHVpZCk6IGluZm8gZm9yIHVpZCwgaW5mbyBpbiBwbGF5ZXJzLml0ZW1zKCl9LAogICAgICAgICAgICAicGxheWVyX2xhbmciOiB7"
    "c3RyKHVpZCk6IGxhbmcgZm9yIHVpZCwgbGFuZyBpbiBwbGF5ZXJfbGFuZy5pdGVtcygpfSwKICAgICAgICAgICAgInVzZXJfc2Vs"
    "ZWN0aW9ucyI6IHtzdHIodWlkKTogc2VsIGZvciB1aWQsIHNlbCBpbiB1c2VyX3NlbGVjdGlvbnMuaXRlbXMoKX0sCiAgICAgICAg"
    "ICAgICJ0aWNrZXRfd2F0Y2hlcnMiOiB7ZiJ7cmlkfTp7dG59IjogbGlzdCh1aWRzKSBmb3IgKHJpZCwgdG4pLCB1aWRzIGluIHRp"
    "Y2tldF93YXRjaGVycy5pdGVtcygpfSwKICAgICAgICAgICAgInJlY2VpcHRfY29udGV4dHMiOiB7c3RyKHVpZCk6IGluZm8gZm9y"
    "IHVpZCwgaW5mbyBpbiByZWNlaXB0X2NvbnRleHRzLml0ZW1zKCl9LAogICAgICAgICAgICAidXNlZF9zbXNfcmVmcyI6IHsKICAg"
    "ICAgICAgICAgICAgIG5vcm1fcmVmOiB7KippbmZvLCAidXNlZF9hdCI6IF9kdF90b19zdHIoaW5mb1sidXNlZF9hdCJdKX0KICAg"
    "ICAgICAgICAgICAgIGZvciBub3JtX3JlZiwgaW5mbyBpbiB1c2VkX3Ntc19yZWZzLml0ZW1zKCkKICAgICAgICAgICAgfSwKICAg"
    "ICAgICAgICAgInVubWF0Y2hlZF9zbXNfbG9nIjogWwogICAgICAgICAgICAgICAgeyoqZSwgInJlY2VpdmVkX2F0IjogX2R0X3Rv"
    "X3N0cihlWyJyZWNlaXZlZF9hdCJdKX0KICAgICAgICAgICAgICAgIGZvciBlIGluIHVubWF0Y2hlZF9zbXNfbG9nCiAgICAgICAg"
    "ICAgIF0sCiAgICAgICAgICAgICJwZW5kaW5nX2NyZWRpdF90b3B1cHMiOiB7CiAgICAgICAgICAgICAgICBub3JtX3JlZjogeyoq"
    "ZSwgImNyZWF0ZWRfYXQiOiBfZHRfdG9fc3RyKGVbImNyZWF0ZWRfYXQiXSl9CiAgICAgICAgICAgICAgICBmb3Igbm9ybV9yZWYs"
    "IGUgaW4gcGVuZGluZ19jcmVkaXRfdG9wdXBzLml0ZW1zKCkKICAgICAgICAgICAgfSwKICAgICAgICAgICAgInJlamVjdGVkX3Jl"
    "ZnMiOiB7CiAgICAgICAgICAgICAgICBrZXk6IHsqKmUsICJyZWplY3RlZF9hdCI6IF9kdF90b19zdHIoZVsicmVqZWN0ZWRfYXQi"
    "XSl9CiAgICAgICAgICAgICAgICBmb3Iga2V5LCBlIGluIHJlamVjdGVkX3JlZnMuaXRlbXMoKQogICAgICAgICAgICB9LAogICAg"
    "ICAgICAgICAicGF5bWVudF9tZXRob2RzIjogewogICAgICAgICAgICAgICAga2V5OiB7ImFjY291bnQiOiBtLmdldCgiYWNjb3Vu"
    "dCIpLCAiaG9sZGVyIjogbS5nZXQoImhvbGRlciIpfQogICAgICAgICAgICAgICAgZm9yIGtleSwgbSBpbiBQQVlNRU5UX01FVEhP"
    "RFMuaXRlbXMoKQogICAgICAgICAgICB9LAogICAgICAgICAgICAib3V0Ym91bmRfc21zX3dlYmhvb2tfdXJsIjogT1VUQk9VTkRf"
    "U01TX1dFQkhPT0tfVVJMLAogICAgICAgIH0KICAgICAgICBpZiBfU1RPUkUgaXMgbm90IE5vbmU6CiAgICAgICAgICAgIF9TVE9S"
    "RS53cml0ZV9qc29uKCJsb3R0ZXJ5X3N0YXRlLmpzb24iLCBkYXRhKQogICAgICAgIGVsc2U6CiAgICAgICAgICAgIHRtcF9wYXRo"
    "ID0gU1RBVEVfRklMRSArICIudG1wIgogICAgICAgICAgICB3aXRoIG9wZW4odG1wX3BhdGgsICJ3IiwgZW5jb2Rpbmc9InV0Zi04"
    "IikgYXMgZjoKICAgICAgICAgICAgICAgIGpzb24uZHVtcChkYXRhLCBmLCBlbnN1cmVfYXNjaWk9RmFsc2UsIGluZGVudD0yKQog"
    "ICAgICAgICAgICBvcy5yZXBsYWNlKHRtcF9wYXRoLCBTVEFURV9GSUxFKQogICAgZXhjZXB0IEV4Y2VwdGlvbiBhcyBlOgogICAg"
    "ICAgIGxvZ2dpbmcuZXJyb3IoZiLimqDvuI8g4YiB4YqU4Ymz4YqVIOGLiOGLsCDhi7LhiLXhiq0g4Yib4Yi14YmA4YiY4YylIOGK"
    "oOGIjeGJsOGIs+GKq+GIneGNpiB7ZX0iKQoKZGVmIGxvYWRfc3RhdGUoKToKICAgIGdsb2JhbCBuZXh0X3JvdW5kX2lkLCBob3N0"
    "X3BhdXNlZCwgaG9zdF9wYXVzZWRfcmVhc29uLCBob3N0X2NyZWRpdCwgV0lOTkVSX1NMT1RTLCBPVVRCT1VORF9TTVNfV0VCSE9P"
    "S19VUkwKICAgICIiIuGJpuGJsSDhiLLhjIDhiJ3hiK0g4YmA4Yuw4YidIOGJpeGIjiDhi6jhibDhiYDhiJjhjKAg4YiB4YqU4Ymz"
    "IOGKq+GIiCDhiqjhi7LhiLXhiq0g4Yuo4Yia4Yyt4YqVIGhlbHBlciIiIgogICAgdHJ5OgogICAgICAgIGlmIF9TVE9SRSBpcyBu"
    "b3QgTm9uZToKICAgICAgICAgICAgZGF0YSA9IF9TVE9SRS5yZWFkX2pzb24oImxvdHRlcnlfc3RhdGUuanNvbiIpCiAgICAgICAg"
    "ICAgIGlmIGRhdGEgaXMgTm9uZToKICAgICAgICAgICAgICAgIHJldHVybgogICAgICAgIGVsc2U6CiAgICAgICAgICAgIGlmIG5v"
    "dCBvcy5wYXRoLmV4aXN0cyhTVEFURV9GSUxFKToKICAgICAgICAgICAgICAgIHJldHVybgogICAgICAgICAgICB3aXRoIG9wZW4o"
    "U1RBVEVfRklMRSwgInIiLCBlbmNvZGluZz0idXRmLTgiKSBhcyBmOgogICAgICAgICAgICAgICAgZGF0YSA9IGpzb24ubG9hZChm"
    "KQoKICAgICAgICBuZXh0X3JvdW5kX2lkID0gZGF0YS5nZXQoIm5leHRfcm91bmRfaWQiLCBuZXh0X3JvdW5kX2lkKQogICAgICAg"
    "IGhvc3RfcGF1c2VkID0gYm9vbChkYXRhLmdldCgiaG9zdF9wYXVzZWQiLCBGYWxzZSkpCiAgICAgICAgaG9zdF9wYXVzZWRfcmVh"
    "c29uID0gZGF0YS5nZXQoImhvc3RfcGF1c2VkX3JlYXNvbiIpCiAgICAgICAgV0lOTkVSX1NMT1RTID0gaW50KGRhdGEuZ2V0KCJ3"
    "aW5uZXJfc2xvdHMiLCBXSU5ORVJfU0xPVFMpKQogICAgICAgIHNhdmVkX2NyZWRpdCA9IGRhdGEuZ2V0KCJob3N0X2NyZWRpdCIp"
    "CiAgICAgICAgaWYgaXNpbnN0YW5jZShzYXZlZF9jcmVkaXQsIGRpY3QpOgogICAgICAgICAgICBob3N0X2NyZWRpdFsiYmFsYW5j"
    "ZSJdID0gZmxvYXQoc2F2ZWRfY3JlZGl0LmdldCgiYmFsYW5jZSIsIEhPU1RfU1RBUlRJTkdfQ1JFRElUKSkKICAgICAgICAgICAg"
    "aG9zdF9jcmVkaXRbInRvdGFsX2RlZHVjdGVkIl0gPSBmbG9hdChzYXZlZF9jcmVkaXQuZ2V0KCJ0b3RhbF9kZWR1Y3RlZCIsIDAu"
    "MCkpCiAgICAgICAgICAgIGhvc3RfY3JlZGl0WyJsb3dfY3JlZGl0X25vdGlmaWVkIl0gPSBib29sKHNhdmVkX2NyZWRpdC5nZXQo"
    "Imxvd19jcmVkaXRfbm90aWZpZWQiLCBGYWxzZSkpCgogICAgICAgIHJvdW5kcy5jbGVhcigpCiAgICAgICAgZm9yIHJpZCwgciBp"
    "biBkYXRhLmdldCgicm91bmRzIiwge30pLml0ZW1zKCk6CiAgICAgICAgICAgIHJfdGlja2V0cyA9IHt9CiAgICAgICAgICAgIGZv"
    "ciBpLCB0IGluIHIuZ2V0KCJ0aWNrZXRzIiwge30pLml0ZW1zKCk6CiAgICAgICAgICAgICAgICB0ID0gZGljdCh0KQogICAgICAg"
    "ICAgICAgICAgdFsiZXhwaXJlc19hdCJdID0gX3N0cl90b19kdCh0LmdldCgiZXhwaXJlc19hdCIpKQogICAgICAgICAgICAgICAg"
    "IyDhiqjhi5rhiIUg4YmA4Yuw4YidIChwYXltZW50X21ldGhvZC9yZWZfYXR0ZW1wdHMg4Yqo4YiY4Yyo4YiY4Yir4Ym44YuNIOGJ"
    "oOGNiuGJtSkg4Yuo4Ymw4YmA4YiY4YyhIOGJsuGKrOGJtuGJveGKlSDhibDhirPhiovhip0g4YiI4Yib4Yu14Yio4YyNCiAgICAg"
    "ICAgICAgICAgICB0LnNldGRlZmF1bHQoInBheW1lbnRfbWV0aG9kIiwgTm9uZSkKICAgICAgICAgICAgICAgIHQuc2V0ZGVmYXVs"
    "dCgicmVmX2F0dGVtcHRzIiwgMCkKICAgICAgICAgICAgICAgIHQuc2V0ZGVmYXVsdCgicmVjZWlwdF9maWxlX2lkIiwgTm9uZSkK"
    "ICAgICAgICAgICAgICAgIHQuc2V0ZGVmYXVsdCgicmVjZWlwdF9yZWNlaXZlZCIsIEZhbHNlKQogICAgICAgICAgICAgICAgcl90"
    "aWNrZXRzW2ludChpKV0gPSB0CiAgICAgICAgICAgIHJfd2lubmVycyA9IFtdCiAgICAgICAgICAgIGZvciB3IGluIHIuZ2V0KCJ3"
    "aW5uZXJzIiwgW10pOgogICAgICAgICAgICAgICAgdyA9IGRpY3QodykKICAgICAgICAgICAgICAgIHdbInNldF9hdCJdID0gX3N0"
    "cl90b19kdCh3LmdldCgic2V0X2F0IikpCiAgICAgICAgICAgICAgICByX3dpbm5lcnMuYXBwZW5kKHcpCiAgICAgICAgICAgIHJv"
    "dW5kc1tpbnQocmlkKV0gPSB7CiAgICAgICAgICAgICAgICAibmFtZSI6IHIuZ2V0KCJuYW1lIiksCiAgICAgICAgICAgICAgICAi"
    "ZGVzY3JpcHRpb24iOiByLmdldCgiZGVzY3JpcHRpb24iKSwKICAgICAgICAgICAgICAgICJpbWFnZV9maWxlX2lkIjogci5nZXQo"
    "ImltYWdlX2ZpbGVfaWQiKSwKICAgICAgICAgICAgICAgICJudW1fdGlja2V0cyI6IHJbIm51bV90aWNrZXRzIl0sCiAgICAgICAg"
    "ICAgICAgICAicHJpY2UiOiByWyJwcmljZSJdLAogICAgICAgICAgICAgICAgInN0YXR1cyI6IHJbInN0YXR1cyJdLAogICAgICAg"
    "ICAgICAgICAgInRpY2tldHMiOiByX3RpY2tldHMsCiAgICAgICAgICAgICAgICAid2lubmVycyI6IHJfd2lubmVycywgICMg4Yqo"
    "4Yua4YiFIOGJgOGLsOGInSAo4Yut4YiFIOGMiOGMveGJsyDhiqjhiJjhjKjhiJjhiKkg4Ymg4Y2K4Ym1KSDhi6jhibDhiYDhiJjh"
    "jKEg4YuZ4Yiu4Ym9IOGIi+GLrSDhiaPhi7Yg4Yud4Yit4Yud4YitIOGLreGIhuGKk+GIjSAo4Ymw4Yqz4YqL4Yqd4YqQ4Ym1KQog"
    "ICAgICAgICAgICB9CgogICAgICAgIHBsYXllcnMuY2xlYXIoKQogICAgICAgIGZvciB1aWQsIGluZm8gaW4gZGF0YS5nZXQoInBs"
    "YXllcnMiLCB7fSkuaXRlbXMoKToKICAgICAgICAgICAgcGxheWVyc1tpbnQodWlkKV0gPSBpbmZvCgogICAgICAgIHBsYXllcl9s"
    "YW5nLmNsZWFyKCkKICAgICAgICBmb3IgdWlkLCBsYW5nIGluIGRhdGEuZ2V0KCJwbGF5ZXJfbGFuZyIsIHt9KS5pdGVtcygpOgog"
    "ICAgICAgICAgICBpZiBsYW5nIGluIExBTkdTOgogICAgICAgICAgICAgICAgcGxheWVyX2xhbmdbaW50KHVpZCldID0gbGFuZwoK"
    "ICAgICAgICB1c2VyX3NlbGVjdGlvbnMuY2xlYXIoKQogICAgICAgIGZvciB1aWQsIHNlbCBpbiBkYXRhLmdldCgidXNlcl9zZWxl"
    "Y3Rpb25zIiwge30pLml0ZW1zKCk6CiAgICAgICAgICAgIHNlbCA9IGRpY3Qoc2VsKQogICAgICAgICAgICAjIOGKqOGLmuGIhSDh"
    "iYDhi7DhiJ0gKOGJpeGLmSDhiYHhjKXhiK0gY2hlY2tvdXQg4Yqo4YiY4Yyo4YiY4YipIOGJoOGNiuGJtSkg4Yuo4Ymw4YmA4YiY"
    "4YyhIOGIneGIreGMq+GLjuGJveGKlSDhi4jhi7Ag4Yqg4Yuy4YixIOGLqOGLneGIreGLneGIrSAobGlzdCkg4YmF4Yit4Yy9IOGI"
    "mOGJgOGLqOGIrQogICAgICAgICAgICBpZiAidGlja2V0cyIgbm90IGluIHNlbCBhbmQgInRpY2tldF9udW0iIGluIHNlbDoKICAg"
    "ICAgICAgICAgICAgIHNlbFsidGlja2V0cyJdID0gW3NlbC5wb3AoInRpY2tldF9udW0iKV0KICAgICAgICAgICAgdXNlcl9zZWxl"
    "Y3Rpb25zW2ludCh1aWQpXSA9IHNlbAoKICAgICAgICB0aWNrZXRfd2F0Y2hlcnMuY2xlYXIoKQogICAgICAgIGZvciBrZXksIGlk"
    "cyBpbiBkYXRhLmdldCgidGlja2V0X3dhdGNoZXJzIiwge30pLml0ZW1zKCk6CiAgICAgICAgICAgIHRyeToKICAgICAgICAgICAg"
    "ICAgIHJpZCwgdG4gPSBbaW50KHgpIGZvciB4IGluIGtleS5zcGxpdCgiOiIsIDEpXQogICAgICAgICAgICAgICAgdGlja2V0X3dh"
    "dGNoZXJzWyhyaWQsIHRuKV0gPSBzZXQoaWRzKQogICAgICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICAgICAg"
    "cGFzcwoKICAgICAgICByZWNlaXB0X2NvbnRleHRzLmNsZWFyKCkKICAgICAgICBmb3IgdWlkLCBpbmZvIGluIGRhdGEuZ2V0KCJy"
    "ZWNlaXB0X2NvbnRleHRzIiwge30pLml0ZW1zKCk6CiAgICAgICAgICAgIHRyeToKICAgICAgICAgICAgICAgIHJlY2VpcHRfY29u"
    "dGV4dHNbaW50KHVpZCldID0gaW5mbwogICAgICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICAgICAgcGFzcwoK"
    "ICAgICAgICB1c2VkX3Ntc19yZWZzLmNsZWFyKCkKICAgICAgICByYXdfdXNlZF9yZWZzID0gZGF0YS5nZXQoInVzZWRfc21zX3Jl"
    "ZnMiLCB7fSkKICAgICAgICBpZiBpc2luc3RhbmNlKHJhd191c2VkX3JlZnMsIGxpc3QpOgogICAgICAgICAgICAjIOGLqOGJgOGL"
    "sOGImCDhiLXhiKrhibUg4YuN4YiC4YmlICjhi53hiK3hi53hiK7hib0g4Yur4YiN4YqQ4Ymg4Yip4Ymg4Ym1KSAtIOGIiOGJsOGK"
    "s+GKi+GKneGKkOGJtSDhiaXhibsg4Ymj4Yu2IOGLneGIreGLneGIrSDhi6vhiIjhi40g4YiY4Yud4YyI4YmlIOGKpeGKleGNiOGM"
    "peGIq+GIiOGKlQogICAgICAgICAgICBmb3Igbm9ybV9yZWYgaW4gcmF3X3VzZWRfcmVmczoKICAgICAgICAgICAgICAgIHVzZWRf"
    "c21zX3JlZnNbbm9ybV9yZWZdID0gewogICAgICAgICAgICAgICAgICAgICJyYXdfcmVmIjogbm9ybV9yZWYsICJyb3VuZF9pZCI6"
    "IE5vbmUsICJ0aWNrZXRfbnVtIjogTm9uZSwKICAgICAgICAgICAgICAgICAgICAidXNlcl9pZCI6IE5vbmUsICJidXllcl9uYW1l"
    "IjogTm9uZSwgInVzZWRfYXQiOiBOb25lLAogICAgICAgICAgICAgICAgfQogICAgICAgIGVsc2U6CiAgICAgICAgICAgIGZvciBu"
    "b3JtX3JlZiwgaW5mbyBpbiByYXdfdXNlZF9yZWZzLml0ZW1zKCk6CiAgICAgICAgICAgICAgICBpbmZvID0gZGljdChpbmZvKQog"
    "ICAgICAgICAgICAgICAgaW5mb1sidXNlZF9hdCJdID0gX3N0cl90b19kdChpbmZvLmdldCgidXNlZF9hdCIpKQogICAgICAgICAg"
    "ICAgICAgdXNlZF9zbXNfcmVmc1tub3JtX3JlZl0gPSBpbmZvCgogICAgICAgIHVubWF0Y2hlZF9zbXNfbG9nLmNsZWFyKCkKICAg"
    "ICAgICBmb3IgZSBpbiBkYXRhLmdldCgidW5tYXRjaGVkX3Ntc19sb2ciLCBbXSk6CiAgICAgICAgICAgIGUgPSBkaWN0KGUpCiAg"
    "ICAgICAgICAgIGVbInJlY2VpdmVkX2F0Il0gPSBfc3RyX3RvX2R0KGUuZ2V0KCJyZWNlaXZlZF9hdCIpKQogICAgICAgICAgICB1"
    "bm1hdGNoZWRfc21zX2xvZy5hcHBlbmQoZSkKCiAgICAgICAgcGVuZGluZ19jcmVkaXRfdG9wdXBzLmNsZWFyKCkKICAgICAgICBm"
    "b3Igbm9ybV9yZWYsIGUgaW4gZGF0YS5nZXQoInBlbmRpbmdfY3JlZGl0X3RvcHVwcyIsIHt9KS5pdGVtcygpOgogICAgICAgICAg"
    "ICBlID0gZGljdChlKQogICAgICAgICAgICBlWyJjcmVhdGVkX2F0Il0gPSBfc3RyX3RvX2R0KGUuZ2V0KCJjcmVhdGVkX2F0Iikp"
    "CiAgICAgICAgICAgIHBlbmRpbmdfY3JlZGl0X3RvcHVwc1tub3JtX3JlZl0gPSBlCgogICAgICAgIHJlamVjdGVkX3JlZnMuY2xl"
    "YXIoKQogICAgICAgIGZvciBrZXksIGUgaW4gZGF0YS5nZXQoInJlamVjdGVkX3JlZnMiLCB7fSkuaXRlbXMoKToKICAgICAgICAg"
    "ICAgZSA9IGRpY3QoZSkKICAgICAgICAgICAgZVsicmVqZWN0ZWRfYXQiXSA9IF9zdHJfdG9fZHQoZS5nZXQoInJlamVjdGVkX2F0"
    "IikpCiAgICAgICAgICAgIHJlamVjdGVkX3JlZnNba2V5XSA9IGUKCiAgICAgICAgIyDhi6jhiq3hjY3hi6sg4Yqg4Yqr4YuN4YqV"
    "4Ym1L+GJo+GIiOGJpOGJtSDhiLXhiJ0g4Yqo4Yua4YiFIOGJgOGLsOGInSDhiaAvZWRpdHBheW1lbnQg4Ymw4Yi14Ymw4Yqr4Yqt"
    "4YiOIOGKqOGKkOGJoOGIqCDhi4jhi7AgUEFZTUVOVF9NRVRIT0RTIOGImOGIjeGIsOGKlSDhiqXhipXhjK3hipPhiIjhipUKICAg"
    "ICAgICBmb3Iga2V5LCBzYXZlZCBpbiBkYXRhLmdldCgicGF5bWVudF9tZXRob2RzIiwge30pLml0ZW1zKCk6CiAgICAgICAgICAg"
    "IGlmIGtleSBpbiBQQVlNRU5UX01FVEhPRFMgYW5kIGlzaW5zdGFuY2Uoc2F2ZWQsIGRpY3QpOgogICAgICAgICAgICAgICAgaWYg"
    "c2F2ZWQuZ2V0KCJhY2NvdW50Iik6CiAgICAgICAgICAgICAgICAgICAgUEFZTUVOVF9NRVRIT0RTW2tleV1bImFjY291bnQiXSA9"
    "IHNhdmVkWyJhY2NvdW50Il0KICAgICAgICAgICAgICAgIGlmIHNhdmVkLmdldCgiaG9sZGVyIik6CiAgICAgICAgICAgICAgICAg"
    "ICAgUEFZTUVOVF9NRVRIT0RTW2tleV1bImhvbGRlciJdID0gc2F2ZWRbImhvbGRlciJdCgogICAgICAgIE9VVEJPVU5EX1NNU19X"
    "RUJIT09LX1VSTCA9IGRhdGEuZ2V0KCJvdXRib3VuZF9zbXNfd2ViaG9va191cmwiKSBvciBOb25lCgogICAgICAgIG9wZW5fY291"
    "bnQgPSBzdW0oMSBmb3IgciBpbiByb3VuZHMudmFsdWVzKCkgaWYgclsic3RhdHVzIl0gPT0gIk9QRU4iKQogICAgICAgIHByaW50"
    "KGYi8J+SviDhiYDhi7DhiJ0g4Yml4YiOIOGLqOGJsOGJgOGImOGMoCDhiIHhipThibMg4Ymw4Yyt4YqX4YiN4Y2mIHtsZW4ocm91"
    "bmRzKX0g4YuZ4Yiu4Ym9ICh7b3Blbl9jb3VudH0g4YqV4YmBKeGNoyB7bGVuKHBsYXllcnMpfSDhibDhjKvhi4vhib7hib0iKQog"
    "ICAgZXhjZXB0IEV4Y2VwdGlvbiBhcyBlOgogICAgICAgIGxvZ2dpbmcuZXJyb3IoZiLimqDvuI8g4YiB4YqU4Ymz4YqVIOGKqOGL"
    "suGIteGKrSDhiJjhjKvhipUg4Yqg4YiN4Ymw4Yiz4Yqr4Yid4Y2mIHtlfSIpCgpjbGFzcyBSZWdpc3RyYXRpb25GaWx0ZXIoZmls"
    "dGVycy5NZXNzYWdlRmlsdGVyKToKICAgICIiIuGJsOGMoOGJg+GImuGLjSDhiaDhiJ3hi53hjIjhiaMg4YiC4Yuw4Ym1IOGIi+GL"
    "rSDhiqXhi6vhiIgg4Yml4Ym7IOGKpeGLjeGKkOGJtSDhi6jhiJrhiJjhiI3hiLUg4Yib4Yyj4Yiq4YurIiIiCiAgICBkZWYgZmls"
    "dGVyKHNlbGYsIG1lc3NhZ2UpOgogICAgICAgIHJldHVybiBtZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRCBhbmQgbWVz"
    "c2FnZS5mcm9tX3VzZXIuaWQgaW4gcmVnaXN0cmF0aW9uX3N0YXRlCgpyZWdpc3RyYXRpb25fZmlsdGVyID0gUmVnaXN0cmF0aW9u"
    "RmlsdGVyKCkKCmRlZiBtYXNrX3Bob25lKHBob25lKToKICAgICIiIuGIteGIjeGKrSDhiYHhjKXhiK3hipUg4Ymg4Yqo4Y2K4YiN"
    "IOGLsOGJpeGJhiDhi6jhiJrhiJjhiI3hiLUgaGVscGVyICjhiIjhiJ3hiLPhiIzhjaYgMDkjIyMjNTY3OCkiIiIKICAgIGRpZ2l0"
    "cyA9IChwaG9uZSBvciAiIikucmVwbGFjZSgiICIsICIiKQogICAgaWYgbGVuKGRpZ2l0cykgPj0gNjoKICAgICAgICByZXR1cm4g"
    "ZiJ7ZGlnaXRzWzoyXX0jIyMje2RpZ2l0c1stNDpdfSIKICAgIHJldHVybiBkaWdpdHMgb3IgIk4vQSIKCmRlZiBtYXNrX3Bob25l"
    "X2xhc3QzKHBob25lKToKICAgICIiIuGIteGIjeGKrSDhiYHhjKXhiK3hipUg4Yuo4YiY4Yyo4Yio4Yi74YuO4Ym5IDMg4Yqg4YiD"
    "4Yue4Ym9IOGJpeGJuyDhi7DhiaXhiYYg4Yuo4Yia4YiY4YiN4Yi1IGhlbHBlciAo4YiI4Yid4Yiz4YiM4Y2mIDA5MTIzNDUqKiog"
    "KSIiIgogICAgZGlnaXRzID0gKHBob25lIG9yICIiKS5yZXBsYWNlKCIgIiwgIiIpCiAgICBpZiBub3QgZGlnaXRzOgogICAgICAg"
    "IHJldHVybiAiTi9BIgogICAgaWYgbGVuKGRpZ2l0cykgPD0gMzoKICAgICAgICByZXR1cm4gIioqKiIKICAgIHJldHVybiBmIntk"
    "aWdpdHNbOi0zXX0qKioiCgpkZWYgbm9ybWFsaXplX3Bob25lKHBob25lKToKICAgICIiIuGLqOGKouGJteGLruGMteGLqyDhiLXh"
    "iI3hiq0g4YmB4Yyl4Yit4YqVIOGLiOGLsCAyNTE5WFhYWFhYWFgg4Yur4Yuw4Yir4YyD4YiN4Y2iIiIiCiAgICBkaWdpdHMgPSBy"
    "ZS5zdWIociJcRCIsICIiLCBwaG9uZSBvciAiIikKICAgIGlmIGRpZ2l0cy5zdGFydHN3aXRoKCIwMCIpOgogICAgICAgIGRpZ2l0"
    "cyA9IGRpZ2l0c1syOl0KICAgIGlmIGRpZ2l0cy5zdGFydHN3aXRoKCIrMjUxIik6CiAgICAgICAgZGlnaXRzID0gZGlnaXRzWzE6"
    "XQogICAgaWYgZGlnaXRzLnN0YXJ0c3dpdGgoIjAiKSBhbmQgbGVuKGRpZ2l0cykgPT0gMTA6CiAgICAgICAgZGlnaXRzID0gIjI1"
    "MSIgKyBkaWdpdHNbMTpdCiAgICBlbGlmIGRpZ2l0cy5zdGFydHN3aXRoKCI5IikgYW5kIGxlbihkaWdpdHMpID09IDk6CiAgICAg"
    "ICAgZGlnaXRzID0gIjI1MSIgKyBkaWdpdHMKICAgIHJldHVybiBkaWdpdHMKCmRlZiBwaG9uZV9hbHJlYWR5X3JlZ2lzdGVyZWQo"
    "cGhvbmUsIGV4Y2x1ZGVfdXNlcl9pZD1Ob25lKToKICAgIG5vcm1hbGl6ZWQgPSBub3JtYWxpemVfcGhvbmUocGhvbmUpCiAgICBm"
    "b3IgdWlkLCBpbmZvIGluIHBsYXllcnMuaXRlbXMoKToKICAgICAgICBpZiBleGNsdWRlX3VzZXJfaWQgaXMgbm90IE5vbmUgYW5k"
    "IHVpZCA9PSBleGNsdWRlX3VzZXJfaWQ6CiAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgaWYgbm9ybWFsaXplX3Bob25lKGlu"
    "Zm8uZ2V0KCJwaG9uZSIpKSA9PSBub3JtYWxpemVkOgogICAgICAgICAgICByZXR1cm4gdWlkCiAgICByZXR1cm4gTm9uZQoKZGVm"
    "IHBsYXllcl9rZXlib2FyZChsYW5nPURFRkFVTFRfTEFORyk6CiAgICBkZWYgdChrZXkpOgogICAgICAgIHJldHVybiBUWFRba2V5"
    "XS5nZXQobGFuZykgb3IgVFhUW2tleV1bREVGQVVMVF9MQU5HXQogICAgcmV0dXJuIFJlcGx5S2V5Ym9hcmRNYXJrdXAoCiAgICAg"
    "ICAgWwogICAgICAgICAgICBbdCgiYnRuX3BsYXkiKSwgdCgiYnRuX215aW5mbyIpXSwKICAgICAgICAgICAgW3QoImJ0bl93aW5u"
    "ZXJzIiksIHQoImJ0bl9oZWxwIildLAogICAgICAgICAgICBbdCgiYnRuX2NvbnRhY3QiKSwgdCgiYnRuX2xhbmd1YWdlIildLAog"
    "ICAgICAgICAgICBbdCgiYnRuX2JlY29tZV9ob3N0IildLAogICAgICAgIF0sIHJlc2l6ZV9rZXlib2FyZD1UcnVlLCBpc19wZXJz"
    "aXN0ZW50PVRydWUKICAgICkKCmRlZiBhZG1pbl9rZXlib2FyZChsYW5nPU5vbmUpOgogICAgbGFuZyA9IGxhbmcgb3IgZ2V0X2xh"
    "bmcoQURNSU5fSUQpCiAgICBiID0gbGFtYmRhIGs6IFRYVFtrXVtsYW5nXQogICAgcmV0dXJuIFJlcGx5S2V5Ym9hcmRNYXJrdXAo"
    "CiAgICAgICAgWwogICAgICAgICAgICBbYigiYWRtaW5fYnRuX25ld19yb3VuZCIpLCBiKCJhZG1pbl9idG5fcm91bmRzX2xpc3Qi"
    "KV0sCiAgICAgICAgICAgIFtiKCJhZG1pbl9idG5fbWFudWFsX3NhbGUiKSwgYigiYWRtaW5fYnRuX3NvbGQiKV0sCiAgICAgICAg"
    "ICAgIFtiKCJhZG1pbl9idG5fYWxsX3RpY2tldHMiKSwgYigiYWRtaW5fYnRuX3Vuc29sZCIpXSwKICAgICAgICAgICAgW2IoImJ0"
    "bl93aW5uZXJzIiksIGIoImFkbWluX2J0bl9zZXRfd2lubmVyIildLAogICAgICAgICAgICBbYigiYWRtaW5fYnRuX3BheW1lbnRz"
    "IiksIGIoImFkbWluX2J0bl9zdGF0cyIpXSwKICAgICAgICAgICAgW2IoImFkbWluX2J0bl9wYXVzZSIpLCBiKCJhZG1pbl9idG5f"
    "cmVzdW1lIildLAogICAgICAgICAgICBbYigiYWRtaW5fYnRuX2Nsb3NlX3JvdW5kIiksIGIoImFkbWluX2J0bl9yZXN0YXJ0X3Jv"
    "dW5kIildLAogICAgICAgICAgICBbYigiYWRtaW5fYnRuX2RlbGV0ZV9yb3VuZCIpLCBiKCJhZG1pbl9idG5fcGxheWVycyIpXSwK"
    "ICAgICAgICAgICAgW2IoImFkbWluX2J0bl9yZWxlYXNlX3RpY2tldCIpLCBiKCJhZG1pbl9idG5faG9zdF9wcm9maWxlIildLAog"
    "ICAgICAgICAgICBbYigiYWRtaW5fYnRuX2FkZF9jcmVkaXQiKSwgYigiYWRtaW5fYnRuX3BheW1lbnRfYWNjb3VudCIpXSwKICAg"
    "ICAgICAgICAgW2IoImFkbWluX2J0bl9hbm5vdW5jZSIpLCBiKCJhZG1pbl9idG5faGVscCIpXSwKICAgICAgICAgICAgW2IoImJ0"
    "bl9sYW5ndWFnZSIpXSwKICAgICAgICBdLCByZXNpemVfa2V5Ym9hcmQ9VHJ1ZSwgaXNfcGVyc2lzdGVudD1UcnVlCiAgICApCgph"
    "c3luYyBkZWYgbm90aWZ5X3RpY2tldF93YXRjaGVycyhyb3VuZF9pZCwgdGlja2V0X251bSwgYm90LCByZWFzb249IiIpOgogICAg"
    "d2F0Y2hlcnMgPSBsaXN0KHRpY2tldF93YXRjaGVycy5wb3AoKHJvdW5kX2lkLCB0aWNrZXRfbnVtKSwgc2V0KCkpKQogICAgaWYg"
    "bm90IHdhdGNoZXJzOgogICAgICAgIHJldHVybgogICAgc2F2ZV9zdGF0ZSgpCiAgICBmb3IgdWlkIGluIHdhdGNoZXJzOgogICAg"
    "ICAgIHRyeToKICAgICAgICAgICAgYXdhaXQgYm90LnNlbmRfbWVzc2FnZSgKICAgICAgICAgICAgICAgIGNoYXRfaWQ9dWlkLAog"
    "ICAgICAgICAgICAgICAgdGV4dD1MKHVpZCwgInJlbGVhc2VkX25vdGlmaWNhdGlvbiIsIHRuPXRpY2tldF9udW0sIHJsYWJlbD1y"
    "b3VuZF9sYWJlbChyb3VuZF9pZCkpLAogICAgICAgICAgICAgICAgcmVwbHlfbWFya3VwPXBsYXllcl9rZXlib2FyZChnZXRfbGFu"
    "Zyh1aWQpKSwKICAgICAgICAgICAgKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgIHBhc3MKCmFzeW5jIGRl"
    "ZiByZWxlYXNlX3RpY2tldChyb3VuZF9pZCwgdGlja2V0X251bSwgYm90PU5vbmUsIG5vdGlmeT1UcnVlKToKICAgIGlmIHJvdW5k"
    "X2lkIG5vdCBpbiByb3VuZHMgb3IgdGlja2V0X251bSBub3QgaW4gcm91bmRzW3JvdW5kX2lkXVsidGlja2V0cyJdOgogICAgICAg"
    "IHJldHVybgogICAgcm91bmRzW3JvdW5kX2lkXVsidGlja2V0cyJdW3RpY2tldF9udW1dID0gX2VtcHR5X3RpY2tldCgpCiAgICBf"
    "Y2xlYXJfcmVqZWN0ZWRfcmVmX2VudHJpZXMocm91bmRfaWQsIFt0aWNrZXRfbnVtXSkKICAgIHNhdmVfc3RhdGUoKQogICAgaWYg"
    "bm90aWZ5IGFuZCBib3Q6CiAgICAgICAgYXdhaXQgbm90aWZ5X3RpY2tldF93YXRjaGVycyhyb3VuZF9pZCwgdGlja2V0X251bSwg"
    "Ym90KQoKZGVmIGdldF9vcGVuX3JvdW5kcygpOgogICAgIyBBIGhvc3QtbGV2ZWwgcGF1c2UgaGlkZXMgYWxsIE9QRU4gcm91bmRz"
    "IGZyb20gcGxheWVycy4KICAgICMgVGhlIHVuZGVybHlpbmcgcm91bmQgc3RhdHVzIGlzIHByZXNlcnZlZCwgc28gcmVzdW1lIHJl"
    "c3RvcmVzIHNlbGxpbmcuCiAgICBpZiBob3N0X3BhdXNlZDoKICAgICAgICByZXR1cm4ge30KICAgIHJldHVybiB7cmlkOiByIGZv"
    "ciByaWQsIHIgaW4gcm91bmRzLml0ZW1zKCkgaWYgclsic3RhdHVzIl0gPT0gIk9QRU4ifQoKZGVmIHJvdW5kX2xhYmVsKHJvdW5k"
    "X2lkKToKICAgICIiIuGLmeGIrSBJRCArICjhiqvhiIjhi40pIOGIteGInSDhiqDhjKPhiJ3hiK4g4Yuo4Yia4Yur4Yiz4YutIGhl"
    "bHBlciAo4YiI4Yid4Yiz4YiM4Y2mICfhi5nhiK0gMyAo4Yuo4YyI4YqTIOGIjuGJsOGIqiknKSIiIgogICAgciA9IHJvdW5kcy5n"
    "ZXQocm91bmRfaWQpCiAgICBpZiByIGFuZCByLmdldCgibmFtZSIpOgogICAgICAgIHJldHVybiBmIuGLmeGIrSB7cm91bmRfaWR9"
    "ICh7clsnbmFtZSddfSkiCiAgICByZXR1cm4gZiLhi5nhiK0ge3JvdW5kX2lkfSIKCmRlZiBnZXRfcGxheWVyX3RpY2tldHModWlk"
    "KToKICAgICIiIuGLqOGJsOGMq+GLi+GJueGKlSDhiIHhiInhipXhiJ0g4Ymy4Yqs4Ym24Ym9ICjhiaDhiIHhiInhiJ0g4YuZ4Yit"
    "KSDhi6jhiJrhiJjhiI3hiLUgLSDhiaDhiabhibUg4Yuo4YyI4YuZ4Ym14YqVICh1c2VyX2lkIOGMjeGMpeGImuGLqykg4Yql4YqT"
    "CiAgICDhiaDhiLXhiI3hiq0g4Ymg4Yql4YyFIChtYW51YWwgc2VsbCkg4Yuo4Ymw4Yi44Yyh4YiI4Ym14YqVICjhiLXhiI3hiq0g"
    "4YmB4Yyl4YitIOGMjeGMpeGImuGLqykg4Yyo4Yid4Yiu4Y2iIChyb3VuZF9pZCwgdGlja2V0X251bSwgdGlja2V0KSDhi53hiK3h"
    "i53hiK0g4Yut4YiY4YiN4Yiz4YiN4Y2jCiAgICDhiaDhi5nhiK0g4Yql4YqTIOGJoOGJgeGMpeGIrSDhibDhi7DhiKvhjIXhibbh"
    "jaIiIiIKICAgIHBsYXllciA9IHBsYXllcnMuZ2V0KHVpZCwge30pCiAgICBwaG9uZV9ub3JtID0gcGxheWVyLmdldCgicGhvbmVf"
    "bm9ybWFsaXplZCIpIG9yIG5vcm1hbGl6ZV9waG9uZShwbGF5ZXIuZ2V0KCJwaG9uZSIsICIiKSkKICAgIHJlc3VsdHMgPSBbXQog"
    "ICAgZm9yIHJpZCwgciBpbiByb3VuZHMuaXRlbXMoKToKICAgICAgICBmb3IgdG4sIHQgaW4gclsidGlja2V0cyJdLml0ZW1zKCk6"
    "CiAgICAgICAgICAgIGlmIHQuZ2V0KCJzdGF0dXMiKSBub3QgaW4gKCJQRU5ESU5HIiwgIlNPTEQiKToKICAgICAgICAgICAgICAg"
    "IGNvbnRpbnVlCiAgICAgICAgICAgIG1hdGNoID0gdC5nZXQoInVzZXJfaWQiKSA9PSB1aWQKICAgICAgICAgICAgaWYgbm90IG1h"
    "dGNoIGFuZCB0LmdldCgic3RhdHVzIikgPT0gIlNPTEQiIGFuZCB0LmdldCgidXNlcl9pZCIpIGlzIE5vbmUgYW5kIHBob25lX25v"
    "cm06CiAgICAgICAgICAgICAgICBpZiBub3JtYWxpemVfcGhvbmUodC5nZXQoImJ1eWVyX3Bob25lIiwgIiIpKSA9PSBwaG9uZV9u"
    "b3JtOgogICAgICAgICAgICAgICAgICAgIG1hdGNoID0gVHJ1ZQogICAgICAgICAgICBpZiBtYXRjaDoKICAgICAgICAgICAgICAg"
    "IHJlc3VsdHMuYXBwZW5kKChyaWQsIHRuLCB0KSkKICAgIHJlc3VsdHMuc29ydChrZXk9bGFtYmRhIHg6ICh4WzBdLCB4WzFdKSkK"
    "ICAgIHJldHVybiByZXN1bHRzCgpUSUNLRVRTX1BFUl9QQUdFID0gNTAKCmRlZiBnZXRfa2V5Ym9hcmQocm91bmRfaWQsIHBhZ2U9"
    "MCwgdXNlcl9pZD1Ob25lLCBjYXJ0PU5vbmUpOgogICAgIiIi4YiI4Ymw4YuI4Yiw4YqQIOGLmeGIrSDhiYHhjKXhiK7hib3hipUg"
    "4Ymg4YyI4Yy9IChwYWdlKSDhiqjhjY3hiI4g4Yuo4Yia4Yur4Yiz4YutIOGJgeGIjeGNjSAo4Ymg4YyI4Yy9IFRJQ0tFVFNfUEVS"
    "X1BBR0Ug4YmB4Yyl4Yiu4Ym9KeGNowogICAg4YqoMTAwIOGJgeGIjeGNjSDhiaDhiIvhi60g4Yur4YiI4YuNIGlubGluZSBrZXli"
    "b2FyZCDhiaDhibThiIzhjI3hiKvhiJ0g4Yi14YiI4Yib4Yut4Yuw4YyI4Y2NIOGJpeGLmSDhibLhiqzhibUg4YiL4YiL4Ym44YuN"
    "IOGLmeGIruGJvSDhjIjhjL0t4Ymg4YyI4Yy9IOGIm+GIs+GLqOGJtSDhi6vhiLXhjYjhiI3hjIvhiI3hjaIKICAgIGNhcnQgKHNl"
    "dCkg4Yqr4YiI4Y2jIOGJsOGMq+GLi+GJuSDhiqDhiLXhiYDhi7XhiJ4g4YiIwqvhjI3hi6Ig4YmF4Yit4Yyr4Ym1wrsg4Yuo4YiY"
    "4Yio4Yyj4Ym44YuNICjhjIjhipMg4Yur4YiN4Ymw4YmG4YiI4Y2JKSDhiYHhjKXhiK7hib0g4pyFIOGJsOGJpeGIiOGLjSDhi63h"
    "ibPhi6vhiInhjaIiIiIKICAgIGNhcnQgPSBjYXJ0IG9yIHNldCgpCiAgICByID0gcm91bmRzW3JvdW5kX2lkXQogICAgdG90YWwg"
    "PSByWyJudW1fdGlja2V0cyJdCiAgICB0b3RhbF9wYWdlcyA9IG1heCgxLCAodG90YWwgKyBUSUNLRVRTX1BFUl9QQUdFIC0gMSkg"
    "Ly8gVElDS0VUU19QRVJfUEFHRSkKICAgIHBhZ2UgPSBtYXgoMCwgbWluKHBhZ2UsIHRvdGFsX3BhZ2VzIC0gMSkpCiAgICBzdGFy"
    "dCA9IHBhZ2UgKiBUSUNLRVRTX1BFUl9QQUdFICsgMQogICAgZW5kID0gbWluKHN0YXJ0ICsgVElDS0VUU19QRVJfUEFHRSAtIDEs"
    "IHRvdGFsKQoKICAgIGtleWJvYXJkID0gW10KICAgIHJvdyA9IFtdCiAgICBmb3IgaSBpbiByYW5nZShzdGFydCwgZW5kICsgMSk6"
    "CiAgICAgICAgdCA9IHJbInRpY2tldHMiXVtpXQogICAgICAgIHN0YXR1cyA9IHRbInN0YXR1cyJdCiAgICAgICAgaWYgaSBpbiBj"
    "YXJ0OgogICAgICAgICAgICB0ZXh0ID0gZiLinIUge2l9IgogICAgICAgIGVsaWYgc3RhdHVzID09ICJBVkFJTEFCTEUiOgogICAg"
    "ICAgICAgICB0ZXh0ID0gZiLwn5+iIHtpfSIKICAgICAgICBlbGlmIHN0YXR1cyA9PSAiUEVORElORyI6CiAgICAgICAgICAgIHRl"
    "eHQgPSBmIvCfn6Ege2l9IgogICAgICAgIGVsaWYgdC5nZXQoInNoYXJlZCIpOgogICAgICAgICAgICB0ZXh0ID0gZiLwn5+jIHtp"
    "fSIKICAgICAgICBlbHNlOgogICAgICAgICAgICBtYXNrZWQgPSBtYXNrX3Bob25lKHQuZ2V0KCJidXllcl9waG9uZSIpKQogICAg"
    "ICAgICAgICB0ZXh0ID0gZiLwn5S0IHtpfVxue21hc2tlZH0iIGlmIG1hc2tlZCAhPSAiTi9BIiBlbHNlIGYi8J+UtCB7aX0iCgog"
    "ICAgICAgIHJvdy5hcHBlbmQoSW5saW5lS2V5Ym9hcmRCdXR0b24odGV4dCwgY2FsbGJhY2tfZGF0YT1mInRpY2tldF97cm91bmRf"
    "aWR9X3tpfV97cGFnZX0iKSkKICAgICAgICBpZiBsZW4ocm93KSA9PSA1OiAgIyDhiaDhi6jhiJjhiLXhiJjhiKkgNSDhiYHhiI3h"
    "jY7hib0g4Yql4YqV4Yuy4YiG4YqRCiAgICAgICAgICAgIGtleWJvYXJkLmFwcGVuZChyb3cpCiAgICAgICAgICAgIHJvdyA9IFtd"
    "CiAgICBpZiByb3c6ICAjIOGLqOGJgOGIqOGLjeGKlSDhi6vhiI3hibDhiJ/hiIsg4YiY4Yi14YiY4YitIOGImOGMqOGImOGIrSAo"
    "4Yqr4YiN4Ymw4Yyo4YiY4YioIOGLqOGImOGMqOGIqOGIu+GLjuGJuSDhiYHhjKXhiK7hib0g4Yut4Yyg4Y2JIOGKkOGJoOGIrSkK"
    "ICAgICAgICBrZXlib2FyZC5hcHBlbmQocm93KQoKICAgIGlmIHRvdGFsX3BhZ2VzID4gMToKICAgICAgICBuYXZfcm93ID0gW10K"
    "ICAgICAgICBpZiBwYWdlID4gMDoKICAgICAgICAgICAgbmF2X3Jvdy5hcHBlbmQoSW5saW5lS2V5Ym9hcmRCdXR0b24oTCh1c2Vy"
    "X2lkLCAiYnRuX3ByZXZfcGFnZSIpLCBjYWxsYmFja19kYXRhPWYidGlja2V0cGFnZV97cm91bmRfaWR9X3twYWdlLTF9IikpCiAg"
    "ICAgICAgbmF2X3Jvdy5hcHBlbmQoSW5saW5lS2V5Ym9hcmRCdXR0b24oTCh1c2VyX2lkLCAicGFnZV9pbmRpY2F0b3IiLCBwYWdl"
    "PXBhZ2UrMSwgdG90YWw9dG90YWxfcGFnZXMpLCBjYWxsYmFja19kYXRhPSJub29wIikpCiAgICAgICAgaWYgcGFnZSA8IHRvdGFs"
    "X3BhZ2VzIC0gMToKICAgICAgICAgICAgbmF2X3Jvdy5hcHBlbmQoSW5saW5lS2V5Ym9hcmRCdXR0b24oTCh1c2VyX2lkLCAiYnRu"
    "X25leHRfcGFnZSIpLCBjYWxsYmFja19kYXRhPWYidGlja2V0cGFnZV97cm91bmRfaWR9X3twYWdlKzF9IikpCiAgICAgICAga2V5"
    "Ym9hcmQuYXBwZW5kKG5hdl9yb3cpCgogICAgaWYgY2FydDoKICAgICAgICBwcmljZSA9IHJbInByaWNlIl0KICAgICAgICBjYXJ0"
    "X3RvdGFsID0gcHJpY2UgKiBsZW4oY2FydCkKICAgICAgICBrZXlib2FyZC5hcHBlbmQoW0lubGluZUtleWJvYXJkQnV0dG9uKAog"
    "ICAgICAgICAgICBMKHVzZXJfaWQsICJidG5fY2hlY2tvdXQiLCBjb3VudD1sZW4oY2FydCksIHRvdGFsPWNhcnRfdG90YWwpLAog"
    "ICAgICAgICAgICBjYWxsYmFja19kYXRhPWYiY2FydGNoZWNrb3V0X3tyb3VuZF9pZH1fe3BhZ2V9IgogICAgICAgICldKQogICAg"
    "ICAgIGtleWJvYXJkLmFwcGVuZChbSW5saW5lS2V5Ym9hcmRCdXR0b24oCiAgICAgICAgICAgIEwodXNlcl9pZCwgImJ0bl9jbGVh"
    "cl9jYXJ0IiksCiAgICAgICAgICAgIGNhbGxiYWNrX2RhdGE9ZiJjYXJ0Y2xlYXJfe3JvdW5kX2lkfV97cGFnZX0iCiAgICAgICAg"
    "KV0pCgogICAgcmV0dXJuIElubGluZUtleWJvYXJkTWFya3VwKGtleWJvYXJkKQoKYXN5bmMgZGVmIGhhbmRsZV9ub29wX2NhbGxi"
    "YWNrKHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIuGLqOGMiOGMvSDh"
    "iYHhjKXhiK0g4Yqg4YiY4YiN4Yqr4Ym9IChwYWdlIGluZGljYXRvcikg4YiL4YutIOGIsuGMq+GKkSDhiJ3hipXhiJ0g4Yuo4Yib"
    "4Yut4Yiw4YirIC0g4Yi14Y2S4YqQ4YipIOGKpeGKleGLs+GLreGJhuGLrSDhiaXhibsg4Yid4YiL4Yi9IOGLreGIsOGMo+GIjSIi"
    "IgogICAgYXdhaXQgdXBkYXRlLmNhbGxiYWNrX3F1ZXJ5LmFuc3dlcigpCgphc3luYyBkZWYgaGFuZGxlX3RpY2tldF9wYWdlKHVw"
    "ZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIuGJsOGMq+GLi+GJuSAn4YmA"
    "4Yyj4YutJy8n4YmA4Yuz4YiaJyDhibDhjK3hipYg4YuI4YuwIOGIjOGIiyDhi6jhibLhiqzhibUg4YyI4Yy9IOGIsuGLmOGLi+GL"
    "iOGIrSDhi6jhiJrhiLDhiKsiIiIKICAgIHF1ZXJ5ID0gdXBkYXRlLmNhbGxiYWNrX3F1ZXJ5CiAgICBhd2FpdCBxdWVyeS5hbnN3"
    "ZXIoKQogICAgXywgcm91bmRfaWRfc3RyLCBwYWdlX3N0ciA9IHF1ZXJ5LmRhdGEuc3BsaXQoIl8iKQogICAgcm91bmRfaWQsIHBh"
    "Z2UgPSBpbnQocm91bmRfaWRfc3RyKSwgaW50KHBhZ2Vfc3RyKQogICAgdWlkID0gcXVlcnkuZnJvbV91c2VyLmlkCiAgICBpZiBy"
    "b3VuZF9pZCBub3QgaW4gcm91bmRzOgogICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KEwodWlkLCAicm91bmRf"
    "Y2xvc2VkIikpCiAgICAgICAgcmV0dXJuCiAgICBjYXJ0ID0gX2dldF9jYXJ0KHVpZCwgcm91bmRfaWQpCiAgICB0cnk6CiAgICAg"
    "ICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3JlcGx5X21hcmt1cChyZXBseV9tYXJrdXA9Z2V0X2tleWJvYXJkKHJvdW5kX2lk"
    "LCBwYWdlPXBhZ2UsIHVzZXJfaWQ9dWlkLCBjYXJ0PWNhcnQpKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICBwYXNzCgpk"
    "ZWYgX2dldF9jYXJ0KHVzZXJfaWQsIHJvdW5kX2lkKToKICAgICIiIuGJsOGMq+GLi+GJuSDhjIjhipMg4YiL4YiN4YmG4YiI4Y2L"
    "4Ym44YuNICjhjIjhipMgY2hlY2tvdXQg4YiL4YiN4Ymw4Yuw4Yio4YyI4YiL4Ym44YuNKSDhiYHhjKXhiK7hib0g4Yur4YiI4YuN"
    "4YqVIOGJheGIreGMq+GJtSAoc2V0KSDhi63hiJjhiI3hiLPhiI3hjaIiIiIKICAgIGMgPSB1c2VyX2NhcnRzLmdldCh1c2VyX2lk"
    "KQogICAgaWYgbm90IGMgb3IgYy5nZXQoInJvdW5kX2lkIikgIT0gcm91bmRfaWQ6CiAgICAgICAgcmV0dXJuIHNldCgpCiAgICBy"
    "ZXR1cm4gY1sidGlja2V0cyJdCgpkZWYgX2NhcnRfc3VtbWFyeV9saW5lKHVzZXJfaWQsIHJvdW5kX2lkLCBjYXJ0KToKICAgIGlm"
    "IG5vdCBjYXJ0OgogICAgICAgIHJldHVybiAiIgogICAgcHJpY2UgPSByb3VuZHNbcm91bmRfaWRdWyJwcmljZSJdCiAgICB0b3Rh"
    "bCA9IHByaWNlICogbGVuKGNhcnQpCiAgICBudW1zID0gIiwgIi5qb2luKHN0cihuKSBmb3IgbiBpbiBzb3J0ZWQoY2FydCkpCiAg"
    "ICByZXR1cm4gTCh1c2VyX2lkLCAiY2FydF9zdW1tYXJ5IiwgY291bnQ9bGVuKGNhcnQpLCBudW1zPW51bXMsIHRvdGFsPXRvdGFs"
    "KQoKZGVmIF9yZW5kZXJfcGlja19tZXNzYWdlKHJvdW5kX2lkLCB1c2VyX2lkLCBjYXJ0KToKICAgICIiIuGLqOGJgeGMpeGIrS3h"
    "iJjhiJ3hiKjhjKsg4YyI4Yy5IOGIi+GLrSDhi6jhiJrhibPhi6jhi43hipUg4Yy94YiB4Y2NICjhi6jhi5nhiK0g4YiY4Yio4YyD"
    "ICsg4Yid4Yit4YyrIOGNjeGKleGMrSArIOGLqOGJheGIreGMq+GJtSDhiJvhjKDhiYPhiIjhi6spIOGLqOGImuGMiOGKkOGJoyBo"
    "ZWxwZXLhjaIiIiIKICAgIHIgPSByb3VuZHNbcm91bmRfaWRdCiAgICB0ZXh0ID0gZiLwn46yIDxiPntyb3VuZF9sYWJlbChyb3Vu"
    "ZF9pZCl9PC9iPiDigJQge3JbJ3ByaWNlJ106LjBmfSB7TCh1c2VyX2lkLCAndGlja2V0X3ByaWNlX3VuaXQnKX1cbiIKICAgIGlm"
    "IHIuZ2V0KCJkZXNjcmlwdGlvbiIpOgogICAgICAgIHRleHQgKz0gZiJcbvCfk50ge3JbJ2Rlc2NyaXB0aW9uJ119XG4iCiAgICB0"
    "ZXh0ICs9ICJcbvCfjq8gIiArIEwodXNlcl9pZCwgInBsYXlfcGlja19oaW50IikKICAgIHRleHQgKz0gX2NhcnRfc3VtbWFyeV9s"
    "aW5lKHVzZXJfaWQsIHJvdW5kX2lkLCBjYXJ0KQogICAgcmV0dXJuIHRleHQKCmRlZiBfc2VsZWN0aW9uX3RpY2tldHMoc2VsKToK"
    "ICAgICIiInVzZXJfc2VsZWN0aW9ucyDhi43hiLXhjKUg4Yqr4YiIIOGKoOGKleGLtSBlbnRyeSAobGVnYWN5IHNpbmd1bGFyIOGL"
    "iOGLreGInSDhiqDhi7LhiLEg4Yud4Yit4Yud4YitIOGJheGIreGMvSkg4YuN4Yi14YylIOGLqOGJsuGKrOGJtSDhiYHhjKXhiK7h"
    "ib0g4Yud4Yit4Yud4YitIOGLqOGImuGLq+GLiOGMoyBoZWxwZXLhjaIiIiIKICAgIGlmIG5vdCBzZWw6CiAgICAgICAgcmV0dXJu"
    "IFtdCiAgICB0aWNrZXRzID0gc2VsLmdldCgidGlja2V0cyIpCiAgICBpZiB0aWNrZXRzIGlzIG5vdCBOb25lOgogICAgICAgIHJl"
    "dHVybiBsaXN0KHRpY2tldHMpCiAgICBpZiAidGlja2V0X251bSIgaW4gc2VsOgogICAgICAgIHJldHVybiBbc2VsWyJ0aWNrZXRf"
    "bnVtIl1dCiAgICByZXR1cm4gW10KCmRlZiBfcmVtb3ZlX3RpY2tldF9mcm9tX3NlbGVjdGlvbih1c2VyX2lkLCByb3VuZF9pZCwg"
    "dGlja2V0X251bSk6CiAgICAiIiLhiqDhipXhi7Ug4Yuo4Ymw4YuI4Yiw4YqQIOGJgeGMpeGIrSAo4YiI4Yid4Yiz4YiMIOGMiuGL"
    "nOGLjSDhiLXhiIvhiIjhjYgpIOGKq+GKleGLtSDhibDhjKvhi4vhib0g4Yur4YiN4Ymw4Yyg4YqT4YmA4YmAIOGJteGLleGLm+GL"
    "nSDhi53hiK3hi53hiK0g4YuN4Yi14YylIOGLqOGImuGLq+GIteGLiOGMjeGLtSBoZWxwZXLhjaIiIiIKICAgIGlmIHVzZXJfaWQg"
    "aXMgTm9uZToKICAgICAgICByZXR1cm4KICAgIHNlbCA9IHVzZXJfc2VsZWN0aW9ucy5nZXQodXNlcl9pZCkKICAgIGlmIG5vdCBz"
    "ZWwgb3Igc2VsLmdldCgicm91bmRfaWQiKSAhPSByb3VuZF9pZDoKICAgICAgICByZXR1cm4KICAgIHRpY2tldHMgPSBbdG4gZm9y"
    "IHRuIGluIF9zZWxlY3Rpb25fdGlja2V0cyhzZWwpIGlmIHRuICE9IHRpY2tldF9udW1dCiAgICBpZiB0aWNrZXRzOgogICAgICAg"
    "IHNlbFsidGlja2V0cyJdID0gdGlja2V0cwogICAgICAgIHNlbC5wb3AoInRpY2tldF9udW0iLCBOb25lKQogICAgZWxzZToKICAg"
    "ICAgICB1c2VyX3NlbGVjdGlvbnMucG9wKHVzZXJfaWQsIE5vbmUpCgpkZWYgX2dldF9wZW5kaW5nX3NlbGVjdGlvbih1c2VyX2lk"
    "KToKICAgICIiIuGJsOGMq+GLi+GJuSDhjIjhipMg4Yur4YiN4Yyo4Yio4Yiw4YuNICjhiq3hjY3hi6sg4Yur4YiN4Ymw4Yyg4YqT"
    "4YmA4YmAL1BFTkRJTkcpIOGLqOGJgeGMpeGIrSjhi47hib0pIOGIneGIreGMqyDhiqvhiIjhi40gKHJvdW5kX2lkLCBbdGlja2V0"
    "X251bXNdLCByZXByZXNlbnRhdGl2ZV90aWNrZXQpCiAgICDhi63hiJjhiI3hiLPhiI3hjaMg4Yqo4YiM4YiI4YuNIE5vbmUg4Yut"
    "4YiY4YiN4Yiz4YiN4Y2iIOGMiuGLnOGLjSDhi6vhiIjhjYjhiaPhibjhi40g4YuI4Yut4YidIOGKqOGJsuGKrOGJsSDhjIvhiK0g"
    "4Yuo4Yib4Yut4YyI4YqT4YqZIOGKoOGIruGMjCDhiYHhjKXhiK7hib0g4Yqr4YiJIOGJoOGIq+GIsSDhi6vhjLjhi7PhiI3hjaIi"
    "IiIKICAgIHNlbCA9IHVzZXJfc2VsZWN0aW9ucy5nZXQodXNlcl9pZCkKICAgIGlmIG5vdCBzZWw6CiAgICAgICAgcmV0dXJuIE5v"
    "bmUKICAgIHJvdW5kX2lkID0gc2VsLmdldCgicm91bmRfaWQiKQogICAgdGlja2V0cyA9IF9zZWxlY3Rpb25fdGlja2V0cyhzZWwp"
    "CiAgICByID0gcm91bmRzLmdldChyb3VuZF9pZCkKICAgIGlmIG5vdCByIG9yIG5vdCB0aWNrZXRzOgogICAgICAgIHVzZXJfc2Vs"
    "ZWN0aW9ucy5wb3AodXNlcl9pZCwgTm9uZSkKICAgICAgICByZXR1cm4gTm9uZQoKICAgIHZhbGlkID0gW10KICAgIGNoYW5nZWQg"
    "PSBGYWxzZQogICAgZm9yIHRuIGluIHRpY2tldHM6CiAgICAgICAgdCA9IHJbInRpY2tldHMiXS5nZXQodG4pCiAgICAgICAgaWYg"
    "bm90IHQgb3IgdC5nZXQoInN0YXR1cyIpICE9ICJQRU5ESU5HIiBvciB0LmdldCgidXNlcl9pZCIpICE9IHVzZXJfaWQ6CiAgICAg"
    "ICAgICAgIGNoYW5nZWQgPSBUcnVlCiAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgaWYgdC5nZXQoImV4cGlyZXNfYXQiKSBh"
    "bmQgdFsiZXhwaXJlc19hdCJdIDwgZGF0ZXRpbWUubm93KCk6CiAgICAgICAgICAgIHJbInRpY2tldHMiXVt0bl0gPSBfZW1wdHlf"
    "dGlja2V0KCkKICAgICAgICAgICAgY2hhbmdlZCA9IFRydWUKICAgICAgICAgICAgY29udGludWUKICAgICAgICB2YWxpZC5hcHBl"
    "bmQodG4pCgogICAgaWYgbm90IHZhbGlkOgogICAgICAgIHVzZXJfc2VsZWN0aW9ucy5wb3AodXNlcl9pZCwgTm9uZSkKICAgICAg"
    "ICBpZiBjaGFuZ2VkOgogICAgICAgICAgICBzYXZlX3N0YXRlKCkKICAgICAgICByZXR1cm4gTm9uZQoKICAgIGlmIGNoYW5nZWQ6"
    "CiAgICAgICAgc2VsWyJ0aWNrZXRzIl0gPSB2YWxpZAogICAgICAgIHNlbC5wb3AoInRpY2tldF9udW0iLCBOb25lKQogICAgICAg"
    "IHNhdmVfc3RhdGUoKQoKICAgIHJldHVybiByb3VuZF9pZCwgdmFsaWQsIHJbInRpY2tldHMiXVt2YWxpZFswXV0KCmFzeW5jIGRl"
    "ZiBfYmxvY2tfd2l0aF9wZW5kaW5nX3NlbGVjdGlvbl9ub3RpY2UodXBkYXRlLCB1c2VyX2lkKToKICAgICIiIuGJsOGMq+GLi+GJ"
    "uSDhi6vhiI3hjKjhiKjhiLDhi40g4Yid4Yit4YyrIOGKq+GIiOGLjeGNoyDhiIzhiIsg4Ym14YuV4Yub4YudL+GJgeGIjeGNjSDh"
    "iqjhiJjhiLXhiKvhibUg4Yut4YiN4YmFIOGImOGMgOGImOGIquGLqyDhjI3hi6Lhi43hipUg4Yql4YqV4Yuy4Yyo4Yit4YixIOGL"
    "iOGLreGInQogICAg4Yid4Yit4Yyr4Ym44YuN4YqVIOGKpeGKleGLsuGIsOGIreGLmSDhi6jhiJrhjKDhi63hiYUg4YiY4YiN4YuV"
    "4Yqt4Ym1IOGIjeGKriBUcnVlIOGLreGImOGIjeGIs+GIjSAo4Yi14YiI4Yua4YiFIOGMoOGIquGLjSDhiJvhiLXhiqzhi7HhipUg"
    "4Yur4YmB4YidKeGNogogICAg4Yur4YiN4Ymw4Yyg4YqT4YmA4YmAIOGIneGIreGMqyDhiqjhiIzhiIggRmFsc2Ug4Yut4YiY4YiN"
    "4Yiz4YiNICjhiLXhiIjhi5rhiIUg4Yyg4Yiq4YuNIOGJoOGImOGLsOGJoOGKm+GLjSDhi63hiYDhjKXhiI0p4Y2iIiIiCiAgICBw"
    "ZW5kaW5nID0gX2dldF9wZW5kaW5nX3NlbGVjdGlvbih1c2VyX2lkKQogICAgaWYgbm90IHBlbmRpbmc6CiAgICAgICAgcmV0dXJu"
    "IEZhbHNlCiAgICByb3VuZF9pZCwgdGlja2V0cywgdCA9IHBlbmRpbmcKICAgIGtiID0gSW5saW5lS2V5Ym9hcmRNYXJrdXAoW1sK"
    "ICAgICAgICBJbmxpbmVLZXlib2FyZEJ1dHRvbihMKHVzZXJfaWQsICJidG5fcmV0dXJuX3B1cmNoYXNlIiksIGNhbGxiYWNrX2Rh"
    "dGE9ZiJyZXR1cm5wZW5kaW5nX3tyb3VuZF9pZH0iKSwKICAgICAgICBJbmxpbmVLZXlib2FyZEJ1dHRvbihMKHVzZXJfaWQsICJi"
    "dG5fY2FuY2VsX3RpY2tldCIpLCBjYWxsYmFja19kYXRhPWYiY2FuY2VscGVuZGluZ197cm91bmRfaWR9IikKICAgIF1dKQogICAg"
    "cmVtYWluaW5nX21pbiA9IE5vbmUKICAgIGlmIHQuZ2V0KCJleHBpcmVzX2F0Iik6CiAgICAgICAgcmVtYWluaW5nX21pbiA9IG1h"
    "eCgwLCBpbnQoKHRbImV4cGlyZXNfYXQiXSAtIGRhdGV0aW1lLm5vdygpKS50b3RhbF9zZWNvbmRzKCkgLy8gNjApKQogICAgbnVt"
    "cyA9ICIsICIuam9pbihzdHIobikgZm9yIG4gaW4gdGlja2V0cykKICAgIHRleHQgPSBMKHVzZXJfaWQsICJwZW5kaW5nX25vdGlj"
    "ZSIsIHRucz1udW1zLCBybGFiZWw9cm91bmRfbGFiZWwocm91bmRfaWQpKQogICAgaWYgcmVtYWluaW5nX21pbiBpcyBub3QgTm9u"
    "ZToKICAgICAgICB0ZXh0ICs9IEwodXNlcl9pZCwgInBlbmRpbmdfbm90aWNlX2V4cGlyeSIsIG1pbnM9cmVtYWluaW5nX21pbikK"
    "ICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQodGV4dCwgcGFyc2VfbW9kZT0iSFRNTCIsIHJlcGx5X21hcmt1cD1r"
    "YikKICAgIHJldHVybiBUcnVlCgphc3luYyBkZWYgaGFuZGxlX3NldF9sYW5ndWFnZSh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDog"
    "Q29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhibDhjKvhi4vhibkg4Yqo4YmL4YqV4YmLIOGImOGIreGMqyDhiYHh"
    "iI3hjY7hib0gKEVuZ2xpc2gv4Yqg4Yib4Yit4YqbL0FmYWFuIE9yb21vbykg4Yqg4YqV4Yux4YqVIOGIsuGMq+GKlSDhi6jhiJrh"
    "iLDhiKsiIiIKICAgIHF1ZXJ5ID0gdXBkYXRlLmNhbGxiYWNrX3F1ZXJ5CiAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgcHJl"
    "Zml4LCBsYW5nID0gcXVlcnkuZGF0YS5yc3BsaXQoIl8iLCAxKQogICAgaWYgbGFuZyBub3QgaW4gTEFOR1M6CiAgICAgICAgbGFu"
    "ZyA9IERFRkFVTFRfTEFORwogICAgdXNlcl9pZCA9IHF1ZXJ5LmZyb21fdXNlci5pZAogICAgcGxheWVyX2xhbmdbdXNlcl9pZF0g"
    "PSBsYW5nCiAgICBzYXZlX3N0YXRlKCkKCiAgICBpZiByZWdpc3RyYXRpb25fc3RhdGUuZ2V0KHVzZXJfaWQpID09ICJhd2FpdGlu"
    "Z19sYW5ndWFnZSI6CiAgICAgICAgaWYgdXNlcl9pZCBpbiBwbGF5ZXJzOgogICAgICAgICAgICAjIOGKkOGJo+GIrSAo4YmA4Yu1"
    "4YieIOGLqOGJsOGImOGLmOGMiOGJoCkg4Ymw4Yyr4YuL4Ym9IC0g4YmgL3N0YXJ0IOGLqOGJi+GKleGJiyDhiJ3hiK3hjKsg4Yml"
    "4Ym7IOGKoOGLteGIreGMjiDhi4jhi7Ag4Yql4YqV4Yqz4YqVIOGLsOGIheGKkyDhiJjhjKEg4Yut4YiC4Yu1CiAgICAgICAgICAg"
    "IHJlZ2lzdHJhdGlvbl9zdGF0ZS5wb3AodXNlcl9pZCwgTm9uZSkKICAgICAgICAgICAgcmVnaXN0cmF0aW9uX3RlbXAucG9wKHVz"
    "ZXJfaWQsIE5vbmUpCiAgICAgICAgICAgIHRyeToKICAgICAgICAgICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV9yZXBs"
    "eV9tYXJrdXAocmVwbHlfbWFya3VwPU5vbmUpCiAgICAgICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgICAgICBw"
    "YXNzCiAgICAgICAgICAgIGF3YWl0IGNvbnRleHQuYm90LnNlbmRfbWVzc2FnZSgKICAgICAgICAgICAgICAgIGNoYXRfaWQ9dXNl"
    "cl9pZCwKICAgICAgICAgICAgICAgIHRleHQ9TCh1c2VyX2lkLCAid2VsY29tZV9iYWNrIiwgbmFtZT1wbGF5ZXJzW3VzZXJfaWRd"
    "WyduYW1lJ10pLAogICAgICAgICAgICAgICAgcmVwbHlfbWFya3VwPXBsYXllcl9rZXlib2FyZChsYW5nKQogICAgICAgICAgICAp"
    "CiAgICAgICAgICAgIHJldHVybgogICAgICAgICMg4Yuo4YiY4YyA4YiY4Yiq4YurIOGIneGLneGMiOGJoyDhjY3hiLDhibUgLSDh"
    "iYDhjKXhiI4g4Yi14YidIOGImOGMoOGLqOGJhQogICAgICAgIHJlZ2lzdHJhdGlvbl9zdGF0ZVt1c2VyX2lkXSA9ICJhd2FpdGlu"
    "Z19uYW1lIgogICAgICAgIHJlZ2lzdHJhdGlvbl90ZW1wW3VzZXJfaWRdID0ge30KICAgICAgICBhd2FpdCBxdWVyeS5lZGl0X21l"
    "c3NhZ2VfdGV4dChMKHVzZXJfaWQsICJhc2tfbmFtZSIpKQogICAgICAgIHJldHVybgoKICAgICMg4YqQ4Ymj4YitIOGJsOGMq+GL"
    "i+GJvSAo4YuI4Yut4YidIOGIhuGIteGJsSDhiKvhiLEpIOGJi+GKleGJiyDhiqXhi6jhiYDhi6jhiKgg4YqQ4YuNCiAgICB0cnk6"
    "CiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3JlcGx5X21hcmt1cChyZXBseV9tYXJrdXA9Tm9uZSkKICAgIGV4Y2Vw"
    "dCBFeGNlcHRpb246CiAgICAgICAgcGFzcwogICAgdHJ5OgogICAgICAgIGF3YWl0IGNvbnRleHQuYm90LnNlbmRfbWVzc2FnZSgK"
    "ICAgICAgICAgICAgY2hhdF9pZD11c2VyX2lkLAogICAgICAgICAgICB0ZXh0PUwodXNlcl9pZCwgImxhbmd1YWdlX3NhdmVkIiks"
    "CiAgICAgICAgICAgIHJlcGx5X21hcmt1cD0oYWRtaW5fa2V5Ym9hcmQobGFuZykgaWYgdXNlcl9pZCA9PSBBRE1JTl9JRCBlbHNl"
    "IHBsYXllcl9rZXlib2FyZChsYW5nKSkKICAgICAgICApCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKCmFzeW5j"
    "IGRlZiBoYW5kbGVfcmV0dXJuX3RvX3BlbmRpbmcodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxU"
    "X1RZUEUpOgogICAgIiIi4Ymw4Yyr4YuL4Ym5ICfhjI3hi6Lhi43hipUg4YmA4Yyl4YiNJyDhiYHhiI3hjY3hipUg4Ymw4Yyt4YqW"
    "IOGLiOGLsCDhiYDhi7XhiJ4g4Yur4YiN4Yyo4Yio4Yiw4YuNIOGMjeGLoiDhi7DhiKjhjIMg4Yiy4YiY4YiI4Yi1IOGLqOGImuGI"
    "sOGIqyIiIgogICAgcXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICBwYXJ0"
    "cyA9IHF1ZXJ5LmRhdGEuc3BsaXQoIl8iKQogICAgcm91bmRfaWQgPSBpbnQocGFydHNbMV0pCiAgICB1c2VyX2lkID0gcXVlcnku"
    "ZnJvbV91c2VyLmlkCgogICAgcGVuZGluZyA9IF9nZXRfcGVuZGluZ19zZWxlY3Rpb24odXNlcl9pZCkKICAgIGlmIG5vdCBwZW5k"
    "aW5nIG9yIHBlbmRpbmdbMF0gIT0gcm91bmRfaWQ6CiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoTCh1c2Vy"
    "X2lkLCAiYWxyZWFkeV9jYW5jZWxsZWRfb3JfZXhwaXJlZCIpKQogICAgICAgIHJldHVybgogICAgXywgdGlja2V0cywgdCA9IHBl"
    "bmRpbmcKCiAgICBwcmljZSA9IHJvdW5kc1tyb3VuZF9pZF1bInByaWNlIl0KICAgIHRvdGFsID0gcHJpY2UgKiBsZW4odGlja2V0"
    "cykKICAgIG51bXMgPSAiLCAiLmpvaW4oc3RyKG4pIGZvciBuIGluIHRpY2tldHMpCgogICAgbWV0aG9kX2tleSA9IHQuZ2V0KCJw"
    "YXltZW50X21ldGhvZCIpCiAgICBpZiBub3QgbWV0aG9kX2tleToKICAgICAgICAjIOGMiOGKkyDhi6jhiq3hjY3hi6sg4YuY4Yu0"
    "IOGKoOGIjeGImOGIqOGMoeGInSAtIOGIneGIreGMq+GLjeGKlSDhiqXhipXhi7DhjIjhipMg4Yib4Yiz4Yuo4Ym1CiAgICAgICAg"
    "a2IgPSBbCiAgICAgICAgICAgIFtJbmxpbmVLZXlib2FyZEJ1dHRvbihwYXltZW50X2xhYmVsKGtleSwgdXNlcl9pZCksIGNhbGxi"
    "YWNrX2RhdGE9ZiJwYXltZXRob2Rfe3JvdW5kX2lkfV97a2V5fSIpXQogICAgICAgICAgICBmb3Iga2V5IGluIFBBWU1FTlRfTUVU"
    "SE9EUy5rZXlzKCkKICAgICAgICBdCiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoCiAgICAgICAgICAgIHRl"
    "eHQ9TCh1c2VyX2lkLCAibXVsdGlfcGF5bWVudF9tZXRob2RfcHJvbXB0IiwgbnVtcz1udW1zLCBjb3VudD1sZW4odGlja2V0cyks"
    "IHJpZD1yb3VuZF9pZCwgdG90YWw9dG90YWwpLAogICAgICAgICAgICBwYXJzZV9tb2RlPSJIVE1MIiwKICAgICAgICAgICAgcmVw"
    "bHlfbWFya3VwPUlubGluZUtleWJvYXJkTWFya3VwKGtiKSwKICAgICAgICApCiAgICAgICAgcmV0dXJuCgogICAgbWV0aG9kID0g"
    "UEFZTUVOVF9NRVRIT0RTLmdldChtZXRob2Rfa2V5KQogICAgaWYgbWV0aG9kX2tleSA9PSAibWFudWFsX2NhbGwiOgogICAgICAg"
    "IGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KAogICAgICAgICAgICBMKHVzZXJfaWQsICJtYW51YWxfY2FsbF9jYXB0aW9u"
    "IiwgYWNjb3VudD1tZXRob2RbJ2FjY291bnQnXSwgbnVtcz1udW1zLCBybGFiZWw9cm91bmRfbGFiZWwocm91bmRfaWQpKSwKICAg"
    "ICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIsCiAgICAgICAgKQogICAgICAgIHJldHVybgoKICAgIHJlbWFpbmluZ19taW4gPSBU"
    "SUNLRVRfSE9MRF9NSU5VVEVTCiAgICBpZiB0LmdldCgiZXhwaXJlc19hdCIpOgogICAgICAgIHJlbWFpbmluZ19taW4gPSBtYXgo"
    "MCwgaW50KCh0WyJleHBpcmVzX2F0Il0gLSBkYXRldGltZS5ub3coKSkudG90YWxfc2Vjb25kcygpIC8vIDYwKSkKICAgIGNhcHRp"
    "b24gPSBMKAogICAgICAgIHVzZXJfaWQsICJwYXltZW50X2RldGFpbHNfY2FwdGlvbiIsCiAgICAgICAgZW1vamk9bWV0aG9kWydl"
    "bW9qaSddLCBsYWJlbD1wYXltZW50X2xhYmVsKG1ldGhvZF9rZXksIHVzZXJfaWQpLAogICAgICAgIHByaWNlPXRvdGFsLCBob2xk"
    "ZXI9bWV0aG9kWydob2xkZXInXSwgYWNjb3VudD1tZXRob2RbJ2FjY291bnQnXSwgbWlucz1yZW1haW5pbmdfbWluCiAgICApCiAg"
    "ICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfdGV4dChjYXB0aW9uLCBwYXJzZV9tb2RlPSJIVE1MIikKCmFzeW5jIGRlZiBoYW5k"
    "bGVfY2FuY2VsX3BlbmRpbmdfc2VsZWN0aW9uKHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9U"
    "WVBFKToKICAgICIiIuGJsOGMq+GLi+GJuSDhi6vhiI3hjKjhiKjhiLDhi43hipUg4Yid4Yit4YyrICfhi63hiIXhipUv4Yqe4Ym5"
    "4YqVIOGJgeGMpeGIrSjhi47hib0pIOGIsOGIreGLnScg4YmB4YiN4Y2N4YqVIOGJsOGMreGKliDhiLLhiLDhiK3hi50g4Yuo4Yia"
    "4Yiw4YirIiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgIHBh"
    "cnRzID0gcXVlcnkuZGF0YS5zcGxpdCgiXyIpCiAgICByb3VuZF9pZCA9IGludChwYXJ0c1sxXSkKICAgIHVzZXJfaWQgPSBxdWVy"
    "eS5mcm9tX3VzZXIuaWQKCiAgICBzZWwgPSB1c2VyX3NlbGVjdGlvbnMuZ2V0KHVzZXJfaWQpCiAgICB0aWNrZXRzID0gX3NlbGVj"
    "dGlvbl90aWNrZXRzKHNlbCkgaWYgc2VsIGFuZCBzZWwuZ2V0KCJyb3VuZF9pZCIpID09IHJvdW5kX2lkIGVsc2UgW10KICAgIGlm"
    "IG5vdCB0aWNrZXRzOgogICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KEwodXNlcl9pZCwgImFscmVhZHlfY2Fu"
    "Y2VsbGVkX29yX2V4cGlyZWQiKSkKICAgICAgICByZXR1cm4KCiAgICBjYW5jZWxsZWQgPSBbXQogICAgZm9yIHRuIGluIHRpY2tl"
    "dHM6CiAgICAgICAgdCA9IHJvdW5kcy5nZXQocm91bmRfaWQsIHt9KS5nZXQoInRpY2tldHMiLCB7fSkuZ2V0KHRuKQogICAgICAg"
    "IGlmIHQgYW5kIHQuZ2V0KCJ1c2VyX2lkIikgPT0gdXNlcl9pZCBhbmQgdC5nZXQoInN0YXR1cyIpID09ICJQRU5ESU5HIjoKICAg"
    "ICAgICAgICAgcm91bmRzW3JvdW5kX2lkXVsidGlja2V0cyJdW3RuXSA9IF9lbXB0eV90aWNrZXQoKQogICAgICAgICAgICBjYW5j"
    "ZWxsZWQuYXBwZW5kKHRuKQoKICAgIHVzZXJfc2VsZWN0aW9ucy5wb3AodXNlcl9pZCwgTm9uZSkKICAgIHJlY2VpcHRfY29udGV4"
    "dHMucG9wKHVzZXJfaWQsIE5vbmUpCiAgICBzYXZlX3N0YXRlKCkKCiAgICBudW1zID0gIiwgIi5qb2luKHN0cihuKSBmb3IgbiBp"
    "biBjYW5jZWxsZWQpIGlmIGNhbmNlbGxlZCBlbHNlICItIgogICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoTCh1c2Vy"
    "X2lkLCAiY2FuY2VsX2NvbmZpcm1lZCIsIHRucz1udW1zLCBybGFiZWw9cm91bmRfbGFiZWwocm91bmRfaWQpKSkKICAgIHRyeToK"
    "ICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoCiAgICAgICAgICAgIGNoYXRfaWQ9dXNlcl9pZCwKICAgICAg"
    "ICAgICAgdGV4dD1MKHVzZXJfaWQsICJjYW5jZWxfcGlja19hZ2FpbiIpLAogICAgICAgICAgICByZXBseV9tYXJrdXA9cGxheWVy"
    "X2tleWJvYXJkKGdldF9sYW5nKHVzZXJfaWQpKQogICAgICAgICkKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcGFzcwog"
    "ICAgZm9yIHRuIGluIGNhbmNlbGxlZDoKICAgICAgICBhd2FpdCBub3RpZnlfdGlja2V0X3dhdGNoZXJzKHJvdW5kX2lkLCB0biwg"
    "Y29udGV4dC5ib3QpCgpjbGFzcyBfRmFrZU1zZzoKICAgICIiIuGKqCBjYWxsYmFja19xdWVyeS5tZXNzYWdlIOGMi+GIrSDhibDh"
    "iJjhiLPhiLPhi60g4YqQ4YyI4YitIOGMjeGKlSDhibXhiq3hiq3hiIjhipvhi43hipUg4Ymw4Yyg4YmD4YiaIChmcm9tX3VzZXIp"
    "IOGLqOGLq+GLmCAn4YyN4YuiJyBNZXNzYWdl4Y2iCiAgICDhi63hiIUg4Yuo4Yia4Yur4YyI4YiI4YyN4YiI4YuNIOGKoOGLteGI"
    "muGKlSDhiYHhiI3hjY0gKGJ1dHRvbikg4Ymw4Yyt4YqWIOGLqMKrL2NvbW1hbmQgPOGLmeGIrT7CuyDhibXhi5Xhi5vhi57hib3h"
    "ipUg4Ymg4Yu14YyL4YiaIOGLq+GIiCDhi7XhjIvhiJot4Yqg4Yy74Yy74Y2NIOGIiOGIm+GIteGKrOGLtSDhipDhi43hjaIiIiIK"
    "ICAgIGRlZiBfX2luaXRfXyhzZWxmLCByZWFsX21lc3NhZ2UsIGZyb21fdXNlcik6CiAgICAgICAgc2VsZi5fcmVhbCA9IHJlYWxf"
    "bWVzc2FnZQogICAgICAgIHNlbGYuZnJvbV91c2VyID0gZnJvbV91c2VyCiAgICBkZWYgX19nZXRhdHRyX18oc2VsZiwgbmFtZSk6"
    "CiAgICAgICAgcmV0dXJuIGdldGF0dHIoc2VsZi5fcmVhbCwgbmFtZSkKCmNsYXNzIF9GYWtlVXBkYXRlOgogICAgZGVmIF9faW5p"
    "dF9fKHNlbGYsIG1lc3NhZ2UpOgogICAgICAgIHNlbGYubWVzc2FnZSA9IG1lc3NhZ2UKCmFzeW5jIGRlZiBfc2VuZF9yb3VuZF9w"
    "aWNrZXIodXBkYXRlLCBjb250ZXh0LCBhY3Rpb25fa2V5LCBlbGlnaWJsZV9yb3VuZF9pZHMsIHRpdGxlLCBlbXB0eV90ZXh0KToK"
    "ICAgICIiIuGKreGIreGKreGIrSAoYXJndW1lbnQpIOGIs+GLreGIsOGMpSDhibXhi5Xhi5vhi50g4Yiy4YiL4Yqt4Y2jIOGKqOGI"
    "mOGJsOGLqOGJpSDhi63hiI3hiYUg4YuZ4YitIOGJoOGJgeGIjeGNjSAoYnV0dG9uKSDhiIjhiJjhiJ3hiKjhjKUg4Yuo4Yia4Yur"
    "4Yiz4YutIGhlbHBlcuGNogogICAgYWN0aW9uX2tleSDhi6jhibXhipvhi40g4Ym14YuV4Yub4YudIOGJsOGMoOGIreGJtiDhiqXh"
    "ipXhi7DhipDhiaDhiK0g4Yi14YiI4Yia4YyI4YiN4Yy9IGNhbGxiYWNrX2RhdGEg4YiL4YutICgiYWRtaW5waWNrXzxhY3Rpb25f"
    "a2V5Pl88cm91bmRfaWQ+Iikg4Yut4YiY4YuY4YyI4Ymj4YiN4Y2iIiIiCiAgICBpZHMgPSBzb3J0ZWQoZWxpZ2libGVfcm91bmRf"
    "aWRzKQogICAgaWYgbm90IGlkczoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KGVtcHR5X3RleHQpCiAg"
    "ICAgICAgcmV0dXJuCgogICAgbGluZXMgPSBbXQogICAgZm9yIHJpZCBpbiBpZHM6CiAgICAgICAgciA9IHJvdW5kc1tyaWRdCiAg"
    "ICAgICAgbGVmdCA9IHN1bSgxIGZvciB0IGluIHJbInRpY2tldHMiXS52YWx1ZXMoKSBpZiB0WyJzdGF0dXMiXSA9PSAiQVZBSUxB"
    "QkxFIikKICAgICAgICBsaW5lcy5hcHBlbmQoCiAgICAgICAgICAgIGYi4paq77iPIDxiPntyb3VuZF9sYWJlbChyaWQpfTwvYj4g"
    "4oCUIHtyWydzdGF0dXMnXX1cbiIKICAgICAgICAgICAgZiIgICDwn5SiIHtyWydudW1fdGlja2V0cyddfSDhibLhiqzhibUgKHts"
    "ZWZ0fSDhi6vhiI3hibDhi6vhi5kpIOKAlCDwn5K1IHtyWydwcmljZSddOi4wZn0g4Yml4YitL+GJsuGKrOGJtSIKICAgICAgICAg"
    "ICAgKyAoZiJcbiAgIPCfk50ge3JbJ2Rlc2NyaXB0aW9uJ119IiBpZiByLmdldCgiZGVzY3JpcHRpb24iKSBlbHNlICIiKQogICAg"
    "ICAgICkKICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIlxuIi5qb2luKGxpbmVzKSwgcGFyc2VfbW9kZT0iSFRN"
    "TCIpCgogICAga2IgPSBbCiAgICAgICAgW0lubGluZUtleWJvYXJkQnV0dG9uKAogICAgICAgICAgICBmIntyb3VuZF9sYWJlbChy"
    "aWQpfSDigJQge3JvdW5kc1tyaWRdWydudW1fdGlja2V0cyddfSDhibLhiqzhibUg4oCUIHtyb3VuZHNbcmlkXVsncHJpY2UnXTou"
    "MGZ9IOGJpeGIrSDigJQge3JvdW5kc1tyaWRdWydzdGF0dXMnXX0iLAogICAgICAgICAgICBjYWxsYmFja19kYXRhPWYiYWRtaW5w"
    "aWNrX3thY3Rpb25fa2V5fV97cmlkfSIKICAgICAgICApXQogICAgICAgIGZvciByaWQgaW4gaWRzCiAgICBdCiAgICBhd2FpdCB1"
    "cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KHRpdGxlLCByZXBseV9tYXJrdXA9SW5saW5lS2V5Ym9hcmRNYXJrdXAoa2IpKQoKYXN5"
    "bmMgZGVmIF9yZXNvbHZlX3JvdW5kX2Zvcl9wbGF5ZXIodXBkYXRlLCBhcmdzLCBhY3Rpb25fa2V5PSJwbGF5IiwgcmVxdWlyZV9v"
    "cGVuPVRydWUpOgogICAgIiIi4Ymw4Yyr4YuL4Ym9IOGJteGLleGLm+GLnSDhiLLhiI3hiq0g4Yuo4Ym14Yqb4YuN4YqVIOGLmeGI"
    "rSDhiqXhipXhi7DhiJrhi6vhiJjhiIjhiq3hibUg4Yuo4Yia4YuI4Yi14YqVIGhlbHBlcuGNogogICAgcm91bmRfaWQg4Ymw4Yyg"
    "4YmF4Yi2IOGKqOGIhuGKkCDhi6vhipXhipUg4Yut4Yyg4YmA4Yib4YiN4Y2kIOGKq+GIjeGJsOGMoOGJgOGIsCDhjI3hipUg4YqV"
    "4YmBIOGLmeGIrSjhi47hib0p4YqVIOGIgeGIjOGInSDhiJjhjIDhiJjhiKrhi6sg4Y2O4Ym2ICjhiqvhiIjhi40pICsg4YmB4YiN"
    "4Y2NIChidXR0b24pCiAgICDhiqDhiLPhi63hibYg4Ymw4Yyr4YuL4Ym5IOGJoOGMo+GJtSDhiqXhipXhi7LhiJjhiK3hjKUg4Yur"
    "4Yuw4Yit4YyL4YiNICjhi5nhiK0g4Yqg4YqV4Yu1IOGJpeGJuyDhiaLhiIbhipXhiJ0g4Yql4YqV4YqzIOGJoOGIq+GItS3hiLDh"
    "iK0g4Yqg4Yut4YuY4YiN4YidKeGNoiIiIgogICAgaWYgaG9zdF9wYXVzZWQ6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2Uu"
    "cmVwbHlfdGV4dCgi4o+477iPIOGIveGLq+GMrSDhiaDhiqDhiLXhibDhi7Phi7PhiKog4YiI4YyK4Yuc4YuNIOGJhuGIn+GIjeGN"
    "oiDhiqXhiaPhiq3hi44g4YmG4Yut4Ymw4YuNIOGKpeGKleGLsOGMiOGKkyDhi63hiJ7hiq3hiKnhjaIiKQogICAgICAgIHJldHVy"
    "biBOb25lCgogICAgaWYgYXJnczoKICAgICAgICB0cnk6CiAgICAgICAgICAgIHJpZCA9IGludChhcmdzWzBdKQogICAgICAgIGV4"
    "Y2VwdCBWYWx1ZUVycm9yOgogICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Yql4Ymj"
    "4Yqt4YuOIOGJteGKreGKreGIiOGKmyDhi6jhi5nhiK0g4YmB4Yyl4YitIChyb3VuZCBJRCkg4Yur4Yi14YyI4Ymh4Y2iIOGLneGI"
    "reGLneGIqeGKlSDhiIjhiJvhi6jhibUgL3JvdW5kcyDhi63hjKvhipHhjaIiKQogICAgICAgICAgICByZXR1cm4gTm9uZQogICAg"
    "ICAgIGlmIHJpZCBub3QgaW4gcm91bmRzIG9yIChyZXF1aXJlX29wZW4gYW5kIHJvdW5kc1tyaWRdWyJzdGF0dXMiXSAhPSAiT1BF"
    "TiIpOgogICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLinYwg4Yql4YqV4Yuw4Yua4YiFIOGLq+GI"
    "iCDhipXhiYEg4YuZ4YitIOGKoOGIjeGJsOGMiOGKmOGIneGNoiDhi53hiK3hi53hiKnhipUg4YiI4Yib4Yuo4Ym1IC9yb3VuZHMg"
    "4Yut4Yyr4YqR4Y2iIikKICAgICAgICAgICAgcmV0dXJuIE5vbmUKICAgICAgICByZXR1cm4gcmlkCgogICAgb3Blbl9yb3VuZHMg"
    "PSBnZXRfb3Blbl9yb3VuZHMoKQogICAgaWYgbGVuKG9wZW5fcm91bmRzKSA9PSAwOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNz"
    "YWdlLnJlcGx5X3RleHQoIuKEue+4jyDhiaDhiqDhiIHhipEg4Yiw4YuT4Ym1IOGIneGKleGInSDhipXhiYEg4YuZ4YitIOGLqOGI"
    "iOGIneGNoiIpCiAgICAgICAgcmV0dXJuIE5vbmUKCiAgICBsaW5lcyA9IFsi8J+OsiA8Yj7hipXhiYEg4YuZ4Yiu4Ym9PC9iPlxu"
    "Il0KICAgIGZvciByaWQsIHIgaW4gc29ydGVkKG9wZW5fcm91bmRzLml0ZW1zKCkpOgogICAgICAgIGxlZnQgPSBzdW0oMSBmb3Ig"
    "dCBpbiByWyJ0aWNrZXRzIl0udmFsdWVzKCkgaWYgdFsic3RhdHVzIl0gPT0gIkFWQUlMQUJMRSIpCiAgICAgICAgbGluZXMuYXBw"
    "ZW5kKAogICAgICAgICAgICBmIuKWqu+4jyA8Yj57cm91bmRfbGFiZWwocmlkKX08L2I+XG4iCiAgICAgICAgICAgIGYiICAg8J+U"
    "oiDhjKDhiYXhiIvhiIsg4Ymy4Yqs4Ym14Y2mIHtyWydudW1fdGlja2V0cyddfSAoe2xlZnR9IOGLq+GIjeGJsOGLq+GLmSDhiYDh"
    "iK3hibDhi4vhiI0pXG4iCiAgICAgICAgICAgIGYiICAg8J+StSDhi4vhjIvhjaYge3JbJ3ByaWNlJ106LjBmfSDhiaXhiK0v4Ymy"
    "4Yqs4Ym1IgogICAgICAgICkKICAgICAgICBpZiByLmdldCgiZGVzY3JpcHRpb24iKToKICAgICAgICAgICAgbGluZXMuYXBwZW5k"
    "KGYiICAg8J+TnSB7clsnZGVzY3JpcHRpb24nXX0iKQogICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgiXG4iLmpv"
    "aW4obGluZXMpLCBwYXJzZV9tb2RlPSJIVE1MIikKCiAgICBmb3IgcmlkLCByIGluIHNvcnRlZChvcGVuX3JvdW5kcy5pdGVtcygp"
    "KToKICAgICAgICBpZiByLmdldCgiaW1hZ2VfZmlsZV9pZCIpOgogICAgICAgICAgICBsZWZ0ID0gc3VtKDEgZm9yIHQgaW4gclsi"
    "dGlja2V0cyJdLnZhbHVlcygpIGlmIHRbInN0YXR1cyJdID09ICJBVkFJTEFCTEUiKQogICAgICAgICAgICBjYXB0aW9uID0gKAog"
    "ICAgICAgICAgICAgICAgZiJ7cm91bmRfbGFiZWwocmlkKX1cbiIKICAgICAgICAgICAgICAgIGYi8J+UoiB7clsnbnVtX3RpY2tl"
    "dHMnXX0g4Ymy4Yqs4Ym1ICh7bGVmdH0g4Yur4YiN4Ymw4Yur4YuZKSDigJQg8J+StSB7clsncHJpY2UnXTouMGZ9IOGJpeGIrS/h"
    "ibLhiqzhibUiCiAgICAgICAgICAgICkKICAgICAgICAgICAgaWYgci5nZXQoImRlc2NyaXB0aW9uIik6CiAgICAgICAgICAgICAg"
    "ICBjYXB0aW9uICs9IGYiXG7wn5OdIHtyWydkZXNjcmlwdGlvbiddfSIKICAgICAgICAgICAgdHJ5OgogICAgICAgICAgICAgICAg"
    "YXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfcGhvdG8ocGhvdG89clsiaW1hZ2VfZmlsZV9pZCJdLCBjYXB0aW9uPWNhcHRpb24p"
    "CiAgICAgICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgICAgICBwYXNzCgogICAga2IgPSBbCiAgICAgICAgW0lu"
    "bGluZUtleWJvYXJkQnV0dG9uKAogICAgICAgICAgICBmIntyb3VuZF9sYWJlbChyaWQpfSDigJQge3JbJ251bV90aWNrZXRzJ119"
    "IOGJsuGKrOGJtSDigJQge3JbJ3ByaWNlJ106LjBmfSDhiaXhiK0iLAogICAgICAgICAgICBjYWxsYmFja19kYXRhPWYicGxheWVy"
    "cGlja197YWN0aW9uX2tleX1fe3JpZH0iCiAgICAgICAgKV0KICAgICAgICBmb3IgcmlkLCByIGluIHNvcnRlZChvcGVuX3JvdW5k"
    "cy5pdGVtcygpKQogICAgXQogICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAi8J+RhyDhi6jhibXh"
    "ipvhi43hipUg4YiY4Yyr4YuI4Ym1IOGLreGNiOGIjeGMi+GIiT8iIGlmIGxlbihvcGVuX3JvdW5kcykgPiAxIGVsc2UgIvCfkYcg"
    "4YiI4YiY4Yyr4YuI4Ym1IOGLreGIneGIqOGMoeGNpiIsCiAgICAgICAgcmVwbHlfbWFya3VwPUlubGluZUtleWJvYXJkTWFya3Vw"
    "KGtiKQogICAgKQogICAgcmV0dXJuIE5vbmUKCmFzeW5jIGRlZiBzdGFydCh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4"
    "dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhibDhjKvhi4vhib0v4Yqg4Yu14Yia4YqVIOGLqOGImOGMgOGImOGIquGLqyDh"
    "iJvhi6sg4YyI4Yy94Y2iIOGKoOGLteGImuGKlSDhiJjhiJjhi53hjIjhiaUg4YuI4Yut4YidIOGImOGMq+GLiOGJtSDhiqDhi63h"
    "ib3hiI3hiJ3hjaIiIiIKICAgIHVzZXIgPSB1cGRhdGUubWVzc2FnZS5mcm9tX3VzZXIKCiAgICBpZiB1c2VyLmlkID09IEFETUlO"
    "X0lEOgogICAgICAgIHJlZ2lzdHJhdGlvbl9zdGF0ZS5wb3AodXNlci5pZCwgTm9uZSkKICAgICAgICByZWdpc3RyYXRpb25fdGVt"
    "cC5wb3AodXNlci5pZCwgTm9uZSkKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICAi"
    "8J+boCA8Yj7hi6jhiqDhiLXhibDhi7Phi7PhiKog4Yib4YuV4Yqo4YiNPC9iPlxuXG4iCiAgICAgICAgICAgICLhi63hiIUg4Yib"
    "4YurIOGMiOGMvSDhiIjhiqDhiLXhibDhi7Phi7DhiK0g4Yml4Ym7IOGKkOGLjeGNolxuIgogICAgICAgICAgICAi8J+aqyDhiJjh"
    "iJjhi53hjIjhiaUv4YiY4Yyr4YuI4Ym1L+GJsuGKrOGJtSDhiJjhjI3hi5vhibUg4Yqg4Yut4Ym94YiJ4Yid4Y2iIiwKICAgICAg"
    "ICAgICAgcGFyc2VfbW9kZT0iSFRNTCIsIHJlcGx5X21hcmt1cD1hZG1pbl9rZXlib2FyZCgpCiAgICAgICAgKQogICAgICAgIHJl"
    "dHVybgoKICAgIGlmIGF3YWl0IF9ibG9ja193aXRoX3BlbmRpbmdfc2VsZWN0aW9uX25vdGljZSh1cGRhdGUsIHVzZXIuaWQpOgog"
    "ICAgICAgIHJldHVybgoKICAgIHJlZ2lzdHJhdGlvbl9zdGF0ZVt1c2VyLmlkXSA9ICJhd2FpdGluZ19sYW5ndWFnZSIKICAgIHJl"
    "Z2lzdHJhdGlvbl90ZW1wLnNldGRlZmF1bHQodXNlci5pZCwge30pCiAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0"
    "KAogICAgICAgIFRYVFsiY2hvb3NlX2xhbmd1YWdlIl1bImFtIl0gKyAiIC8gIiArIFRYVFsiY2hvb3NlX2xhbmd1YWdlIl1bImVu"
    "Il0gKyAiIC8gIiArIFRYVFsiY2hvb3NlX2xhbmd1YWdlIl1bIm9tIl0sCiAgICAgICAgcmVwbHlfbWFya3VwPWxhbmd1YWdlX3Bp"
    "Y2tlcl9rYigic2V0bGFuZyIpCiAgICApCgphc3luYyBkZWYgcm91bmRzX2NvbW1hbmQodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6"
    "IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4Yib4YqV4Yqb4YuN4YidIOGJsOGMq+GLi+GJvSDhi6jhiqDhiIHh"
    "ipHhipUg4YqV4YmBIOGLmeGIruGJvSDhi53hiK3hi53hiK0g4Yuo4Yia4Yur4Yut4Ymg4Ym1IOGJteGLleGLm+GLnSIiIgogICAg"
    "dWlkID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkCiAgICBpZiB1aWQgPT0gQURNSU5fSUQ6CiAgICAgICAgYXdhaXQgdXBk"
    "YXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi8J+boCDhi6jhiqDhiLXhibDhi7Phi7PhiKog4Yic4YqR4YqVIOGLreGMoOGJgOGImeGN"
    "oiIsIHJlcGx5X21hcmt1cD1hZG1pbl9rZXlib2FyZCgpKQogICAgICAgIHJldHVybgogICAgaWYgYXdhaXQgX2Jsb2NrX3dpdGhf"
    "cGVuZGluZ19zZWxlY3Rpb25fbm90aWNlKHVwZGF0ZSwgdWlkKToKICAgICAgICByZXR1cm4KICAgIG9wZW5fcm91bmRzID0gZ2V0"
    "X29wZW5fcm91bmRzKCkKICAgIGlmIG5vdCBvcGVuX3JvdW5kczoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90"
    "ZXh0KEwodWlkLCAicm91bmRzX25vbmVfYWN0aXZlIiksIHJlcGx5X21hcmt1cD1wbGF5ZXJfa2V5Ym9hcmQoZ2V0X2xhbmcodWlk"
    "KSkpCiAgICAgICAgcmV0dXJuCgogICAgdW5pdCA9IEwodWlkLCAidGlja2V0X3ByaWNlX3VuaXQiKQogICAgbGVmdF93b3JkID0g"
    "TCh1aWQsICJyb3VuZHNfbGluZV9sZWZ0IikKICAgIGxpbmVzID0gW0wodWlkLCAicm91bmRzX2hlYWRlciIpXQogICAgZm9yIHJp"
    "ZCwgciBpbiBzb3J0ZWQob3Blbl9yb3VuZHMuaXRlbXMoKSk6CiAgICAgICAgbGVmdCA9IHN1bSgxIGZvciB0IGluIHJbInRpY2tl"
    "dHMiXS52YWx1ZXMoKSBpZiB0WyJzdGF0dXMiXSA9PSAiQVZBSUxBQkxFIikKICAgICAgICB0aXRsZSA9IHJvdW5kX2xhYmVsKHJp"
    "ZCkKICAgICAgICBsaW5lcy5hcHBlbmQoZiLilqrvuI8ge3RpdGxlfSDigJQge3JbJ3ByaWNlJ106LjBmfSB7dW5pdH0g4oCUIHts"
    "ZWZ0fS97clsnbnVtX3RpY2tldHMnXX0ge2xlZnRfd29yZH0iKQogICAgICAgIGlmIHIuZ2V0KCJkZXNjcmlwdGlvbiIpOgogICAg"
    "ICAgICAgICBsaW5lcy5hcHBlbmQoZiIgICDwn5OdIHtyWydkZXNjcmlwdGlvbiddfSIpCiAgICBsaW5lcy5hcHBlbmQoTCh1aWQs"
    "ICJyb3VuZHNfdXNhZ2VfaGludCIpKQoKICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIlxuIi5qb2luKGxpbmVz"
    "KSwgcGFyc2VfbW9kZT0iSFRNTCIpCgogICAgZm9yIHJpZCwgciBpbiBzb3J0ZWQob3Blbl9yb3VuZHMuaXRlbXMoKSk6CiAgICAg"
    "ICAgaWYgci5nZXQoImltYWdlX2ZpbGVfaWQiKToKICAgICAgICAgICAgdHJ5OgogICAgICAgICAgICAgICAgYXdhaXQgdXBkYXRl"
    "Lm1lc3NhZ2UucmVwbHlfcGhvdG8ocGhvdG89clsiaW1hZ2VfZmlsZV9pZCJdLCBjYXB0aW9uPXJvdW5kX2xhYmVsKHJpZCkpCiAg"
    "ICAgICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgICAgICBwYXNzCgphc3luYyBkZWYgcGxheSh1cGRhdGU6IFVw"
    "ZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhi6jhiYHhjKXhiK7hib0g4Yib4Ym14Yiq"
    "4Yqt4Yi1IOGIm+GIs+GLqyIiIgogICAgdXNlciA9IHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlcgoKICAgIGlmIHVzZXIuaWQgPT0g"
    "QURNSU5fSUQ6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi8J+aqyDhiqDhi7XhiJrhipUg4YiY4Yyr"
    "4YuI4Ym1IOGKoOGLreGJveGIjeGIneGNoiDhi6jhiqDhiLXhibDhi7Phi7PhiKog4Yic4YqR4YqVIOGLreGMoOGJgOGImeGNoiIs"
    "IHJlcGx5X21hcmt1cD1hZG1pbl9rZXlib2FyZCgpKQogICAgICAgIHJldHVybgoKICAgIGlmIHVzZXIuaWQgbm90IGluIHBsYXll"
    "cnM6CiAgICAgICAgaWYgdXNlci5pZCBub3QgaW4gcGxheWVyX2xhbmc6CiAgICAgICAgICAgIHJlZ2lzdHJhdGlvbl9zdGF0ZVt1"
    "c2VyLmlkXSA9ICJhd2FpdGluZ19sYW5ndWFnZSIKICAgICAgICAgICAgcmVnaXN0cmF0aW9uX3RlbXBbdXNlci5pZF0gPSB7fQog"
    "ICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICAgICAgVFhUWyJjaG9vc2VfbGFu"
    "Z3VhZ2UiXVsiYW0iXSArICIgLyAiICsgVFhUWyJjaG9vc2VfbGFuZ3VhZ2UiXVsiZW4iXSArICIgLyAiICsgVFhUWyJjaG9vc2Vf"
    "bGFuZ3VhZ2UiXVsib20iXSwKICAgICAgICAgICAgICAgIHJlcGx5X21hcmt1cD1sYW5ndWFnZV9waWNrZXJfa2IoInNldGxhbmci"
    "KQogICAgICAgICAgICApCiAgICAgICAgICAgIHJldHVybgogICAgICAgIHJlZ2lzdHJhdGlvbl9zdGF0ZVt1c2VyLmlkXSA9ICJh"
    "d2FpdGluZ19uYW1lIgogICAgICAgIHJlZ2lzdHJhdGlvbl90ZW1wW3VzZXIuaWRdID0ge30KICAgICAgICBhd2FpdCB1cGRhdGUu"
    "bWVzc2FnZS5yZXBseV90ZXh0KEwodXNlci5pZCwgIm11c3RfcmVnaXN0ZXJfZmlyc3QiKSkKICAgICAgICByZXR1cm4KCiAgICBp"
    "ZiBhd2FpdCBfYmxvY2tfd2l0aF9wZW5kaW5nX3NlbGVjdGlvbl9ub3RpY2UodXBkYXRlLCB1c2VyLmlkKToKICAgICAgICByZXR1"
    "cm4KCiAgICByb3VuZF9pZCA9IGF3YWl0IF9yZXNvbHZlX3JvdW5kX2Zvcl9wbGF5ZXIodXBkYXRlLCBjb250ZXh0LmFyZ3MsIGFj"
    "dGlvbl9rZXk9InBsYXkiKQogICAgaWYgcm91bmRfaWQgaXMgTm9uZToKICAgICAgICByZXR1cm4KCiAgICAjIOGMiuGLnOGLq+GJ"
    "uOGLjSDhi6vhiIjhjYjhiaPhibjhi43hipUg4Ym84YqtIOGKoOGLteGIreGMjeGKkyDhiq3hjY3hibUg4Yqg4Yu14Yit4YyNCiAg"
    "ICBub3cgPSBkYXRldGltZS5ub3coKQogICAgcl90aWNrZXRzID0gcm91bmRzW3JvdW5kX2lkXVsidGlja2V0cyJdCiAgICBmb3Ig"
    "aSwgdCBpbiByX3RpY2tldHMuaXRlbXMoKToKICAgICAgICBpZiB0WyJzdGF0dXMiXSA9PSAiUEVORElORyIgYW5kIHRbImV4cGly"
    "ZXNfYXQiXSBhbmQgdFsiZXhwaXJlc19hdCJdIDwgbm93OgogICAgICAgICAgICByX3RpY2tldHNbaV0gPSBfZW1wdHlfdGlja2V0"
    "KCkKCiAgICAjIOGJgOGLteGIniDhi6vhiI3hibDhjKDhipPhiYDhiYAg4YmF4Yit4Yyr4Ym1IChjYXJ0KSDhiqvhiIgg4YiI4Yua"
    "4YiFIOGLmeGIrSDhiqvhiI3hiIbhipAg4Yib4Yy94Yuz4Ym1ICjhibDhjKvhi4vhibkg4YuI4YuwIOGIjOGIiyDhi5nhiK0g4Yiy"
    "4YuY4YuL4YuI4YitKQogICAgZXhpc3RpbmdfY2FydCA9IHVzZXJfY2FydHMuZ2V0KHVzZXIuaWQpCiAgICBpZiBleGlzdGluZ19j"
    "YXJ0IGFuZCBleGlzdGluZ19jYXJ0LmdldCgicm91bmRfaWQiKSAhPSByb3VuZF9pZDoKICAgICAgICB1c2VyX2NhcnRzLnBvcCh1"
    "c2VyLmlkLCBOb25lKQoKICAgIHIgPSByb3VuZHNbcm91bmRfaWRdCiAgICBjYXB0aW9uID0gZiLwn46yIDxiPntyb3VuZF9sYWJl"
    "bChyb3VuZF9pZCl9PC9iPiDigJQge3JbJ3ByaWNlJ106LjBmfSB7TCh1c2VyLmlkLCAndGlja2V0X3ByaWNlX3VuaXQnKX1cbiIK"
    "ICAgIGlmIHIuZ2V0KCJkZXNjcmlwdGlvbiIpOgogICAgICAgIGNhcHRpb24gKz0gZiJcbvCfk50ge3JbJ2Rlc2NyaXB0aW9uJ119"
    "XG4iCiAgICBjYXB0aW9uICs9ICJcbvCfjq8gIiArIEwodXNlci5pZCwgInBsYXlfcGlja19oaW50IikKCiAgICBwaWNrX3Byb21w"
    "dCA9IHsiYW0iOiAi8J+RhyDhiYHhjKXhiK0o4YuO4Ym9KSDhi63hiJ3hiKjhjKEgKOGKqOGKoOGKleGLtSDhiaDhiIvhi60g4YiY"
    "4Yid4Yio4YylIOGLreGJveGIi+GIiSnhjaYiLAogICAgICAgICAgICAgICAgICAgImVuIjogIvCfkYcgUGljayBhIG51bWJlciAo"
    "eW91IGNhbiBwaWNrIG1vcmUgdGhhbiBvbmUpOiIsCiAgICAgICAgICAgICAgICAgICAib20iOiAi8J+RhyBMYWtrb29mc2EgZmls"
    "YWRoYWEgKGxha2tvb2ZzYSB0b2trbyBvbCBmaWxhY2h1dSBuaSBkYW5kZWVzc3UpOiJ9W2dldF9sYW5nKHVzZXIuaWQpXQogICAg"
    "aWYgci5nZXQoImltYWdlX2ZpbGVfaWQiKToKICAgICAgICB0cnk6CiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJl"
    "cGx5X3Bob3RvKHBob3RvPXJbImltYWdlX2ZpbGVfaWQiXSwgY2FwdGlvbj1jYXB0aW9uLCBwYXJzZV9tb2RlPSJIVE1MIikKICAg"
    "ICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dChwaWNrX3Byb21wdCwgcmVwbHlfbWFya3VwPWdldF9rZXli"
    "b2FyZChyb3VuZF9pZCwgcGFnZT0wLCB1c2VyX2lkPXVzZXIuaWQsIGNhcnQ9c2V0KCkpKQogICAgICAgICAgICByZXR1cm4KICAg"
    "ICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICBwYXNzCgogICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4"
    "dCgKICAgICAgICBjYXB0aW9uLAogICAgICAgIHJlcGx5X21hcmt1cD1nZXRfa2V5Ym9hcmQocm91bmRfaWQsIHBhZ2U9MCwgdXNl"
    "cl9pZD11c2VyLmlkLCBjYXJ0PXNldCgpKSwKICAgICAgICBwYXJzZV9tb2RlPSJIVE1MIgogICAgKQoKYXN5bmMgZGVmIGhhbmRs"
    "ZV9jYXJ0X2NoZWNrb3V0KHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIi"
    "IuGJsOGMq+GLi+GJuSAi4Yqt4Y2N4YurIOGJgOGMpeGIjSIgKENoZWNrb3V0KSDhibDhjK3hipYg4YmB4Yyl4Yiu4Ym54YqVIOGK"
    "qOGImOGJhuGIiOGNiSDhiaDhjYrhibUgwqvinIUg4Yqg4Yio4YyL4YyN4YylIC8g4p2MIOGLjeGLteGJhSDhiqDhi7XhiK3hjI3C"
    "uyDhi6jhiJrhiI0g4Yib4Yio4YyL4YyI4YyrIOGLqOGImuGLq+GIs+GLrSAtCiAgICDhibXhiq3hiq3hiIjhipvhi40g4YiY4YmG"
    "4YiI4Y2NIOGLqOGImuGNiOGMuOGImOGLjSDhibDhjKvhi4vhibkg4Yqr4Yio4YyL4YyI4YygIOGJoOGKi+GIiyAoaGFuZGxlX2Nh"
    "cnRfY29uZmlybSkg4Yml4Ym7IOGKkOGLjeGNoiIiIgogICAgcXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIHBhcnRz"
    "ID0gcXVlcnkuZGF0YS5zcGxpdCgiXyIpCiAgICByb3VuZF9pZCA9IGludChwYXJ0c1sxXSkKICAgIHVpZCA9IHF1ZXJ5LmZyb21f"
    "dXNlci5pZAoKICAgIGNhcnQgPSB1c2VyX2NhcnRzLmdldCh1aWQpCiAgICBpZiBub3QgY2FydCBvciBjYXJ0LmdldCgicm91bmRf"
    "aWQiKSAhPSByb3VuZF9pZCBvciBub3QgY2FydC5nZXQoInRpY2tldHMiKToKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoTCh1"
    "aWQsICJjYXJ0X2VtcHR5X2FsZXJ0IiksIHNob3dfYWxlcnQ9VHJ1ZSkKICAgICAgICByZXR1cm4KCiAgICBpZiBob3N0X3BhdXNl"
    "ZDoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoIuKPuO+4jyDhiL3hi6vhjK0g4Ymg4Yqg4Yi14Ymw4Yuz4Yuz4YiqIOGIiOGM"
    "iuGLnOGLjSDhiYbhiJ/hiI3hjaIiLCBzaG93X2FsZXJ0PVRydWUpCiAgICAgICAgcmV0dXJuCgogICAgaWYgcm91bmRfaWQgbm90"
    "IGluIHJvdW5kcyBvciByb3VuZHNbcm91bmRfaWRdWyJzdGF0dXMiXSAhPSAiT1BFTiI6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5z"
    "d2VyKCkKICAgICAgICB1c2VyX2NhcnRzLnBvcCh1aWQsIE5vbmUpCiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3Rl"
    "eHQoTCh1aWQsICJyb3VuZF9jbG9zZWQiKSkKICAgICAgICByZXR1cm4KCiAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQoKICAgIHBy"
    "aWNlID0gcm91bmRzW3JvdW5kX2lkXVsicHJpY2UiXQogICAgcGlja2VkID0gc29ydGVkKGNhcnRbInRpY2tldHMiXSkKICAgIHRv"
    "dGFsID0gcHJpY2UgKiBsZW4ocGlja2VkKQogICAgbnVtcyA9ICIsICIuam9pbihzdHIobikgZm9yIG4gaW4gcGlja2VkKQoKICAg"
    "IGtiID0gSW5saW5lS2V5Ym9hcmRNYXJrdXAoW1sKICAgICAgICBJbmxpbmVLZXlib2FyZEJ1dHRvbihMKHVpZCwgImJ0bl9jb25m"
    "aXJtX3NlbGVjdGlvbiIpLCBjYWxsYmFja19kYXRhPWYiY2FydGNvbmZpcm1fe3JvdW5kX2lkfSIpLAogICAgICAgIElubGluZUtl"
    "eWJvYXJkQnV0dG9uKEwodWlkLCAiYnRuX3JlamVjdF9zZWxlY3Rpb24iKSwgY2FsbGJhY2tfZGF0YT1mImNhcnRyZWplY3Rfe3Jv"
    "dW5kX2lkfSIpLAogICAgXV0pCiAgICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfdGV4dCgKICAgICAgICBMKHVpZCwgImNvbmZp"
    "cm1fc2VsZWN0aW9uX3Byb21wdCIsIG51bXM9bnVtcywgY291bnQ9bGVuKHBpY2tlZCksIHJpZD1yb3VuZF9pZCwgdG90YWw9dG90"
    "YWwpLAogICAgICAgIHBhcnNlX21vZGU9IkhUTUwiLAogICAgICAgIHJlcGx5X21hcmt1cD1rYiwKICAgICkKCmFzeW5jIGRlZiBo"
    "YW5kbGVfY2FydF9jb25maXJtKHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAg"
    "ICIiIuGJsOGMq+GLi+GJuSDhiJ3hiK3hjKvhi43hipUg4Yqr4Yio4YyL4YyI4YygIOGJoOGKi+GIiyDhiaDhiYXhiK3hjKvhibEg"
    "4YuN4Yi14YylIOGLq+GIieGJteGKlSDhiYHhjKXhiK7hib0g4Ymg4YiZ4YiJIOGJoOGKoOGKleGLtSDhjIrhi5wg4Yuo4Yia4YmG"
    "4YiN4Y2NL+GLqOGImuGLreGLnSIiIgogICAgcXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIHBhcnRzID0gcXVlcnku"
    "ZGF0YS5zcGxpdCgiXyIpCiAgICByb3VuZF9pZCA9IGludChwYXJ0c1sxXSkKICAgIHVzZXIgPSBxdWVyeS5mcm9tX3VzZXIKICAg"
    "IHVpZCA9IHVzZXIuaWQKCiAgICBjYXJ0ID0gdXNlcl9jYXJ0cy5nZXQodWlkKQogICAgaWYgbm90IGNhcnQgb3IgY2FydC5nZXQo"
    "InJvdW5kX2lkIikgIT0gcm91bmRfaWQgb3Igbm90IGNhcnQuZ2V0KCJ0aWNrZXRzIik6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5z"
    "d2VyKEwodWlkLCAiY2FydF9lbXB0eV9hbGVydCIpLCBzaG93X2FsZXJ0PVRydWUpCiAgICAgICAgcmV0dXJuCgogICAgaWYgaG9z"
    "dF9wYXVzZWQ6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKCLij7jvuI8g4Yi94Yur4YytIOGJoOGKoOGIteGJsOGLs+GLs+GI"
    "qiDhiIjhjIrhi5zhi40g4YmG4Yif4YiN4Y2iIiwgc2hvd19hbGVydD1UcnVlKQogICAgICAgIHJldHVybgoKICAgIGlmIHJvdW5k"
    "X2lkIG5vdCBpbiByb3VuZHMgb3Igcm91bmRzW3JvdW5kX2lkXVsic3RhdHVzIl0gIT0gIk9QRU4iOgogICAgICAgIGF3YWl0IHF1"
    "ZXJ5LmFuc3dlcigpCiAgICAgICAgdXNlcl9jYXJ0cy5wb3AodWlkLCBOb25lKQogICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVz"
    "c2FnZV90ZXh0KEwodWlkLCAicm91bmRfY2xvc2VkIikpCiAgICAgICAgcmV0dXJuCgogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkK"
    "CiAgICBub3cgPSBkYXRldGltZS5ub3coKQogICAgcl90aWNrZXRzID0gcm91bmRzW3JvdW5kX2lkXVsidGlja2V0cyJdCiAgICB3"
    "YW50ZWQgPSBzb3J0ZWQoY2FydFsidGlja2V0cyJdKQogICAgbG9ja2VkLCB1bmF2YWlsYWJsZSA9IFtdLCBbXQogICAgZm9yIHRu"
    "IGluIHdhbnRlZDoKICAgICAgICB0ID0gcl90aWNrZXRzLmdldCh0bikKICAgICAgICBpZiBub3QgdDoKICAgICAgICAgICAgdW5h"
    "dmFpbGFibGUuYXBwZW5kKHRuKQogICAgICAgICAgICBjb250aW51ZQogICAgICAgIGlmIHRbInN0YXR1cyJdID09ICJQRU5ESU5H"
    "IiBhbmQgdC5nZXQoImV4cGlyZXNfYXQiKSBhbmQgdFsiZXhwaXJlc19hdCJdIDwgbm93OgogICAgICAgICAgICBfcmVtb3ZlX3Rp"
    "Y2tldF9mcm9tX3NlbGVjdGlvbih0LmdldCgidXNlcl9pZCIpLCByb3VuZF9pZCwgdG4pCiAgICAgICAgICAgIHJfdGlja2V0c1t0"
    "bl0gPSBfZW1wdHlfdGlja2V0KCkKICAgICAgICAgICAgdCA9IHJfdGlja2V0c1t0bl0KICAgICAgICBpZiB0WyJzdGF0dXMiXSAh"
    "PSAiQVZBSUxBQkxFIjoKICAgICAgICAgICAgdW5hdmFpbGFibGUuYXBwZW5kKHRuKQogICAgICAgICAgICBjb250aW51ZQogICAg"
    "ICAgIGxvY2tlZC5hcHBlbmQodG4pCgogICAgdXNlcl9jYXJ0cy5wb3AodWlkLCBOb25lKQoKICAgIGlmIG5vdCBsb2NrZWQ6CiAg"
    "ICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoTCh1aWQsICJjYXJ0X2FsbF90YWtlbiIpKQogICAgICAgIHJldHVy"
    "bgoKICAgIGV4cGlyZXNfYXQgPSBkYXRldGltZS5ub3coKSArIHRpbWVkZWx0YShtaW51dGVzPVRJQ0tFVF9IT0xEX01JTlVURVMp"
    "CiAgICBmb3IgdG4gaW4gbG9ja2VkOgogICAgICAgIHQgPSByX3RpY2tldHNbdG5dCiAgICAgICAgdFsic3RhdHVzIl0gPSAiUEVO"
    "RElORyIKICAgICAgICB0WyJ1c2VyX2lkIl0gPSB1aWQKICAgICAgICB0WyJ1c2VybmFtZSJdID0gdXNlci51c2VybmFtZSBvciB1"
    "c2VyLmZpcnN0X25hbWUKICAgICAgICB0WyJleHBpcmVzX2F0Il0gPSBleHBpcmVzX2F0CiAgICAgICAgdFsiYnV5ZXJfbmFtZSJd"
    "ID0gcGxheWVyc1t1aWRdWyJuYW1lIl0KICAgICAgICB0WyJidXllcl9waG9uZSJdID0gcGxheWVyc1t1aWRdWyJwaG9uZSJdCiAg"
    "ICAgICAgdFsicGF5bWVudF9tZXRob2QiXSA9IE5vbmUKICAgICAgICB0WyJyZWZfYXR0ZW1wdHMiXSA9IDAKICAgICAgICB0WyJy"
    "ZWYiXSA9IE5vbmUKCiAgICB1c2VyX3NlbGVjdGlvbnNbdWlkXSA9IHsicm91bmRfaWQiOiByb3VuZF9pZCwgInRpY2tldHMiOiBs"
    "b2NrZWR9CiAgICBzYXZlX3N0YXRlKCkKCiAgICBwcmljZSA9IHJvdW5kc1tyb3VuZF9pZF1bInByaWNlIl0KICAgIHRvdGFsID0g"
    "cHJpY2UgKiBsZW4obG9ja2VkKQogICAgbnVtcyA9ICIsICIuam9pbihzdHIobikgZm9yIG4gaW4gbG9ja2VkKQoKICAgIG5vdGUg"
    "PSAiIgogICAgaWYgdW5hdmFpbGFibGU6CiAgICAgICAgbm90ZSA9IEwodWlkLCAic29tZV91bmF2YWlsYWJsZV9ub3RlIiwgbnVt"
    "cz0iLCAiLmpvaW4oc3RyKG4pIGZvciBuIGluIHVuYXZhaWxhYmxlKSkKCiAgICBrYiA9IFsKICAgICAgICBbSW5saW5lS2V5Ym9h"
    "cmRCdXR0b24ocGF5bWVudF9sYWJlbChrZXksIHVpZCksIGNhbGxiYWNrX2RhdGE9ZiJwYXltZXRob2Rfe3JvdW5kX2lkfV97a2V5"
    "fSIpXQogICAgICAgIGZvciBrZXkgaW4gUEFZTUVOVF9NRVRIT0RTLmtleXMoKQogICAgXQogICAgYXdhaXQgcXVlcnkuZWRpdF9t"
    "ZXNzYWdlX3RleHQoCiAgICAgICAgdGV4dD1MKHVpZCwgInRpY2tldHNfaGVsZCIsIG51bXM9bnVtcywgY291bnQ9bGVuKGxvY2tl"
    "ZCksIHJpZD1yb3VuZF9pZCwgbWlucz1USUNLRVRfSE9MRF9NSU5VVEVTLCB0b3RhbD10b3RhbCkgKyBub3RlLAogICAgICAgIHBh"
    "cnNlX21vZGU9IkhUTUwiLAogICAgICAgIHJlcGx5X21hcmt1cD1JbmxpbmVLZXlib2FyZE1hcmt1cChrYiksCiAgICApCgphc3lu"
    "YyBkZWYgaGFuZGxlX2NhcnRfcmVqZWN0KHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBF"
    "KToKICAgICIiIuGJsOGMq+GLi+GJuSDhiqvhiKjhjIvhjIjhjKAg4Ymg4Y2K4Ym1IOGIneGIreGMq+GLjeGKlSDhi43hi7XhiYUg"
    "4Yqr4Yuw4Yio4YyIIOGJheGIreGMq+GJseGKlSDhiqDhjL3hi7XhibYg4YuI4YuwIOGJgeGMpeGIrSDhjY3hiK3hjI3hiK3hjI0g"
    "4Yuo4Yia4YiY4YiN4Yi1IiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgdWlkID0gcXVlcnkuZnJvbV91"
    "c2VyLmlkCiAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoTCh1aWQsICJzZWxlY3Rpb25fcmVqZWN0ZWRfYWxlcnQiKSkKICAgIHBhcnRz"
    "ID0gcXVlcnkuZGF0YS5zcGxpdCgiXyIpCiAgICByb3VuZF9pZCA9IGludChwYXJ0c1sxXSkKICAgIHVzZXJfY2FydHMucG9wKHVp"
    "ZCwgTm9uZSkKCiAgICBpZiByb3VuZF9pZCBub3QgaW4gcm91bmRzOgogICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90"
    "ZXh0KEwodWlkLCAicm91bmRfY2xvc2VkIikpCiAgICAgICAgcmV0dXJuCgogICAgdGV4dCA9IF9yZW5kZXJfcGlja19tZXNzYWdl"
    "KHJvdW5kX2lkLCB1aWQsIHNldCgpKQogICAga2IgPSBnZXRfa2V5Ym9hcmQocm91bmRfaWQsIHBhZ2U9MCwgdXNlcl9pZD11aWQs"
    "IGNhcnQ9c2V0KCkpCiAgICB0cnk6CiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQodGV4dCwgcGFyc2VfbW9k"
    "ZT0iSFRNTCIsIHJlcGx5X21hcmt1cD1rYikKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcGFzcwoKYXN5bmMgZGVmIGhh"
    "bmRsZV9jYXJ0X2NsZWFyKHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIi"
    "IuGJsOGMq+GLi+GJuSAi4Yid4Yit4YyrIOGKoOGMveGLsyIg4Ymw4Yyt4YqWIOGMiOGKkyDhi6vhiI3hiYbhiIjhjYvhibjhi43h"
    "ipUg4YmF4Yit4Yyr4Ym1IOGLjeGIteGMpSDhi6vhiIkg4YmB4Yyl4Yiu4Ym9IOGLqOGImuGLq+GMuOGLsyIiIgogICAgcXVlcnkg"
    "PSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICBwYXJ0cyA9IHF1ZXJ5LmRhdGEuc3Bs"
    "aXQoIl8iKQogICAgcm91bmRfaWQgPSBpbnQocGFydHNbMV0pCiAgICBwYWdlID0gaW50KHBhcnRzWzJdKSBpZiBsZW4ocGFydHMp"
    "ID4gMiBlbHNlIDAKICAgIHVpZCA9IHF1ZXJ5LmZyb21fdXNlci5pZAogICAgdXNlcl9jYXJ0cy5wb3AodWlkLCBOb25lKQoKICAg"
    "IHRleHQgPSBfcmVuZGVyX3BpY2tfbWVzc2FnZShyb3VuZF9pZCwgdWlkLCBzZXQoKSkKICAgIGtiID0gZ2V0X2tleWJvYXJkKHJv"
    "dW5kX2lkLCBwYWdlPXBhZ2UsIHVzZXJfaWQ9dWlkLCBjYXJ0PXNldCgpKQogICAgdHJ5OgogICAgICAgIGF3YWl0IHF1ZXJ5LmVk"
    "aXRfbWVzc2FnZV90ZXh0KHRleHQsIHBhcnNlX21vZGU9IkhUTUwiLCByZXBseV9tYXJrdXA9a2IpCiAgICBleGNlcHQgRXhjZXB0"
    "aW9uOgogICAgICAgIHBhc3MKCmFzeW5jIGRlZiBhZG1pbl9yb3VuZHNfb3ZlcnZpZXcodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6"
    "IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgaWYgdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkICE9IEFETUlOX0lE"
    "OgogICAgICAgIHJldHVybgogICAgaWYgbm90IHJvdW5kczoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0"
    "KCLihLnvuI8g4Yid4YqV4YidIOGLmeGIrSDhi6jhiIjhiJ3hjaIiLCByZXBseV9tYXJrdXA9YWRtaW5fa2V5Ym9hcmQoKSkKICAg"
    "ICAgICByZXR1cm4KICAgIGxpbmVzPVsi8J+TiyA8Yj7hi6jhiIHhiInhiJ0g4YuZ4Yiu4Ym9IOGIgeGKlOGJszwvYj5cbiJdCiAg"
    "ICBmb3IgcmlkLHIgaW4gc29ydGVkKHJvdW5kcy5pdGVtcygpKToKICAgICAgICBhdmFpbGFibGU9c3VtKDEgZm9yIHQgaW4gclsn"
    "dGlja2V0cyddLnZhbHVlcygpIGlmIHRbJ3N0YXR1cyddPT0nQVZBSUxBQkxFJykKICAgICAgICBwZW5kaW5nPXN1bSgxIGZvciB0"
    "IGluIHJbJ3RpY2tldHMnXS52YWx1ZXMoKSBpZiB0WydzdGF0dXMnXT09J1BFTkRJTkcnKQogICAgICAgIHNvbGQ9c3VtKDEgZm9y"
    "IHQgaW4gclsndGlja2V0cyddLnZhbHVlcygpIGlmIHRbJ3N0YXR1cyddPT0nU09MRCcpCiAgICAgICAgaWNvbj17J09QRU4nOifw"
    "n5+iJywnUEFVU0VEJzon4o+477iPJywnQ0xPU0VEJzon8J+Ukid9LmdldChyWydzdGF0dXMnXSwn8J+UkicpCiAgICAgICAgbGlu"
    "ZXMuYXBwZW5kKGYie2ljb259IDxiPntyb3VuZF9sYWJlbChyaWQpfTwvYj4g4oCUIHtyWydzdGF0dXMnXX1cbiAgIPCfn6Ig4Yur"
    "4YiN4Ymw4Yur4YuZOiB7YXZhaWxhYmxlfSB8IPCfn6Eg4Ymw4Yut4YuY4YuL4YiNOiB7cGVuZGluZ30gfCDwn5S0IOGJsOGIuOGM"
    "oOGLi+GIjToge3NvbGR9L3tyWydudW1fdGlja2V0cyddfVxuICAg8J+StSB7clsncHJpY2UnXTouMGZ9IOGJpeGIrS/hibLhiqzh"
    "ibUiKQogICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgiXG4iLmpvaW4obGluZXMpLCBwYXJzZV9tb2RlPSdIVE1M"
    "JywgcmVwbHlfbWFya3VwPWFkbWluX2tleWJvYXJkKCkpCgphc3luYyBkZWYgX3NlbmRfaG9zdF9zaWdudXBfcmVxdWVzdF90b19z"
    "dXBlcl9hZG1pbih1aWQsIHBsYXllciwgYm90KToKICAgICIiIuGLqMKr4YiG4Yi14Ym1IOGImOGIhuGKlSDhiqXhjYjhiI3hjIvh"
    "iIjhiIHCuyDhjKXhi6vhiYThipUg4YuI4YuwIFN1cGVyIEFkbWluIOGIq+GIsSDhiabhibUgKOGJoOGLjeGIteGMo+GLiiBIb3N0"
    "IEFQSSDhiaDhiqnhiI0pIOGJoOGKoOGIteGJsOGIm+GIm+GKnSDhiIHhipThibMKICAgIOGLqOGImuGLq+GLsOGIreGItSBoZWxw"
    "ZXIgLSDhiI3hiq0g4Yql4YqV4YuwIF9zZW5kX2NyZWRpdF9yZXF1ZXN0X3RvX3NlbGxlciDhibDhiJjhiLPhiLPhi60g4Yi14YiN"
    "4Ym1IOGJoOGImOGMoOGJgOGIneGNoiDhi63hiIUg4Yqr4YiN4Ymw4Yiz4YqrCiAgICAo4YiI4Yid4Yiz4YiMIOGIm+GLleGKqOGI"
    "i+GLiuGLjSDhiILhi7DhibUg4YiL4YutIOGJveGMjeGIrSDhiqvhiIgpIOGJpeGJuyDhi4jhi7Ag4YmA4Yyl4Ymw4YqbIOGLqOGL"
    "muGIhSDhiIbhiLXhibUg4Ymm4Ym1IOGImOGIjeGKpeGKreGJtSAo4Yu14YiuIOGLqOGKkOGJoOGIqOGLjSDhiJjhipXhjIjhi7Up"
    "IOGLreGImOGIiOGIs+GIjSAtCiAgICDhi6sg4YyN4YqVIFN1cGVyIEFkbWluIOGLreGIheGKlSDhiI3hi6kg4YiG4Yi14Ym1IOGJ"
    "puGJtSDhiqDhiLXhiYDhi7XhiJ4gL3N0YXJ0IOGKq+GIi+GLsOGIqOGMiCDhiqDhi63hi7DhiK3hiLXhiJ0g4Yib4YiI4YmxIOGI"
    "jeGJpSDhi63hiaPhiI3hjaIKICAgIFJldHVybnMgVHJ1ZSBpZiBkZWxpdmVyZWQgdmlhIGVpdGhlciBwYXRoLiIiIgogICAgaWYg"
    "Q1JFRElUX1NFTExFUl9IT1NUX0lEIGFuZCBDUkVESVRfU0VMTEVSX0FQSV9VUkw6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBh"
    "c3luYyB3aXRoIGFpb2h0dHAuQ2xpZW50U2Vzc2lvbigpIGFzIHNlc3Npb246CiAgICAgICAgICAgICAgICBhc3luYyB3aXRoIHNl"
    "c3Npb24ucG9zdCgKICAgICAgICAgICAgICAgICAgICBDUkVESVRfU0VMTEVSX0FQSV9VUkwucnN0cmlwKCIvIikgKyAiL2FwaS9y"
    "ZXF1ZXN0X2hvc3Rfc2lnbnVwIiwKICAgICAgICAgICAgICAgICAgICBoZWFkZXJzPXsKICAgICAgICAgICAgICAgICAgICAgICAg"
    "IlgtUmVxdWVzdC1BUEktS2V5IjogQ1JFRElUX1NFTExFUl9BUElfU0VDUkVULAogICAgICAgICAgICAgICAgICAgICAgICAiQ29u"
    "dGVudC1UeXBlIjogImFwcGxpY2F0aW9uL2pzb24iLAogICAgICAgICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgICAgICAg"
    "anNvbj17CiAgICAgICAgICAgICAgICAgICAgICAgICJob3N0X2lkIjogQ1JFRElUX1NFTExFUl9IT1NUX0lELAogICAgICAgICAg"
    "ICAgICAgICAgICAgICAiaG9zdF9uYW1lIjogSE9TVF9OQU1FLAogICAgICAgICAgICAgICAgICAgICAgICAidGVsZWdyYW1fdXNl"
    "cl9pZCI6IHVpZCwKICAgICAgICAgICAgICAgICAgICAgICAgIm5hbWUiOiBwbGF5ZXIuZ2V0KCJuYW1lIiwgIk4vQSIpLAogICAg"
    "ICAgICAgICAgICAgICAgICAgICAicGhvbmUiOiBwbGF5ZXIuZ2V0KCJwaG9uZSIsICJOL0EiKSwKICAgICAgICAgICAgICAgICAg"
    "ICAgICAgInVzZXJuYW1lIjogcGxheWVyLmdldCgidXNlcm5hbWUiKSBvciAiIiwKICAgICAgICAgICAgICAgICAgICB9LAogICAg"
    "ICAgICAgICAgICAgICAgIHRpbWVvdXQ9YWlvaHR0cC5DbGllbnRUaW1lb3V0KHRvdGFsPTE1KSwKICAgICAgICAgICAgICAgICkg"
    "YXMgcmVzcDoKICAgICAgICAgICAgICAgICAgICB0cnk6CiAgICAgICAgICAgICAgICAgICAgICAgIGRhdGEgPSBhd2FpdCByZXNw"
    "Lmpzb24oY29udGVudF90eXBlPU5vbmUpCiAgICAgICAgICAgICAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAg"
    "ICAgICAgICAgICAgZGF0YSA9IHt9CiAgICAgICAgICAgIGlmIHJlc3Auc3RhdHVzID09IDIwMCBhbmQgZGF0YS5nZXQoIm9rIik6"
    "CiAgICAgICAgICAgICAgICByZXR1cm4gVHJ1ZQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgICAgICAgICAgcHJp"
    "bnQoZiLimqDvuI8g4YuI4YuwIFN1cGVyIEFkbWluIEFQSSDhi6hob3N0LXNpZ251cCDhjKXhi6vhiYQg4YiY4YiL4YqtIOGKoOGI"
    "jeGJsOGIs+GKq+GIneGNpiB7ZX0iKQoKICAgICMgRmFsbGJhY2s6IOGLqOGJgOGLteGInuGLjSDhiYDhjKXhibDhipsg4YiY4YqV"
    "4YyI4Yu1IChTdXBlciBBZG1pbiDhi63hiIXhipUg4Ymm4Ym1IOGKoOGIteGJgOGLteGIniAvc3RhcnQg4Yqr4Yuw4Yio4YyI4YuN"
    "IOGJpeGJuyDhi63hi7DhiK3hiLPhiI0pCiAgICBub3RpZnlfaWQgPSBTVVBFUl9BRE1JTl9JRCBvciBBRE1JTl9JRAogICAgdHJ5"
    "OgogICAgICAgIGF3YWl0IGJvdC5zZW5kX21lc3NhZ2UoCiAgICAgICAgICAgIGNoYXRfaWQ9bm90aWZ5X2lkLAogICAgICAgICAg"
    "ICB0ZXh0PUwoCiAgICAgICAgICAgICAgICB1aWQsICJiZWNvbWVfaG9zdF9hZG1pbl9ub3RpZnkiLAogICAgICAgICAgICAgICAg"
    "bmFtZT1wbGF5ZXIuZ2V0KCJuYW1lIiwgIk4vQSIpLAogICAgICAgICAgICAgICAgcGhvbmU9cGxheWVyLmdldCgicGhvbmUiLCAi"
    "Ti9BIiksCiAgICAgICAgICAgICAgICB1c2VybmFtZT1wbGF5ZXIuZ2V0KCJ1c2VybmFtZSIpIG9yICLigJQiLAogICAgICAgICAg"
    "ICAgICAgdWlkPXVpZCwKICAgICAgICAgICAgKSwKICAgICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIsCiAgICAgICAgKQogICAg"
    "ICAgIHJldHVybiBUcnVlCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHJldHVybiBGYWxzZQoKYXN5bmMgZGVmIGhhbmRs"
    "ZV9tZW51X2J1dHRvbih1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICB0ZXh0"
    "PSh1cGRhdGUubWVzc2FnZS50ZXh0IG9yICcnKS5zdHJpcCgpCiAgICB1aWQ9dXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkCiAg"
    "ICAjIEFkbWluIChob3N0KSBtZW51IC0g4YiN4YqtIOGKpeGKleGLsCDhibDhjKvhi4vhib7hibkg4YiG4Yi14Ymx4YidIOGLqOGI"
    "q+GIsSDhiYvhipXhiYsg4Yid4Yit4YyrIOGKoOGIiOGLjSAoZ2V0X2xhbmcvcGxheWVyX2xhbmcg4Ymw4YiY4Yiz4Yiz4YutIOGL"
    "mOGLtCDhi63hjKDhiYDhiJvhiI0pCiAgICBpZiB1aWQgPT0gQURNSU5fSUQ6CiAgICAgICAgYWRtaW5fbGFuZyA9IGdldF9sYW5n"
    "KHVpZCkKICAgICAgICBkZWYgX2FkbWluX2lzKGtleSk6CiAgICAgICAgICAgIHJldHVybiB0ZXh0IGluIChUWFRba2V5XVsiYW0i"
    "XSwgVFhUW2tleV1bImVuIl0sIFRYVFtrZXldWyJvbSJdKQoKICAgICAgICBpZiBfYWRtaW5faXMoImJ0bl9sYW5ndWFnZSIpOgog"
    "ICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KEwodWlkLCAiY2hvb3NlX2xhbmd1YWdlIiksIHJlcGx5"
    "X21hcmt1cD1sYW5ndWFnZV9waWNrZXJfa2IoInN3aXRjaGxhbmciKSkKICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgaWYgX2Fk"
    "bWluX2lzKCJhZG1pbl9idG5fbmV3X3JvdW5kIik6CiAgICAgICAgICAgICMgQWx3YXlzIHJlc2V0IGEgc3RhbGUgd2l6YXJkIGFu"
    "ZCBzdGFydCB0aGUgbmV3LXJvdW5kIGZsb3cgZGlyZWN0bHkuCiAgICAgICAgICAgIG5ld3JvdW5kX3N0YXRlLnBvcCh1aWQsIE5v"
    "bmUpCiAgICAgICAgICAgIG5ld3JvdW5kX3RlbXAucG9wKHVpZCwgTm9uZSkKICAgICAgICAgICAgY29udGV4dC5hcmdzID0gW10K"
    "ICAgICAgICAgICAgdHJ5OgogICAgICAgICAgICAgICAgYXdhaXQgbmV3X3JvdW5kKHVwZGF0ZSwgY29udGV4dCkKICAgICAgICAg"
    "ICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgICAgIGxvZ2dlci5leGNlcHRpb24oIk5ldy1yb3VuZCBtZW51IGZhaWxl"
    "ZCIpCiAgICAgICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICAgICAgICAgICLi"
    "nYwg4Yqg4Yuy4Yi1IOGLmeGIrSDhiJjhiq3hjYjhibUg4Yqg4YiN4Ymw4Yiz4Yqr4Yid4Y2iXG4iCiAgICAgICAgICAgICAgICAg"
    "ICAgZiLhiqXhiaPhiq3hi44gwqt7VFhUWydhZG1pbl9idG5fbmV3X3JvdW5kJ11bYWRtaW5fbGFuZ119wrsg4Yql4YqV4Yuw4YyI"
    "4YqTIOGLreGMq+GKkeGNoiIsCiAgICAgICAgICAgICAgICAgICAgcmVwbHlfbWFya3VwPWFkbWluX2tleWJvYXJkKGFkbWluX2xh"
    "bmcpCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgIHJldHVybgogICAgICAgIGlmIF9hZG1pbl9pcygiYWRtaW5fYnRuX3Jv"
    "dW5kc19saXN0Iik6CiAgICAgICAgICAgIGF3YWl0IGFkbWluX3JvdW5kc19vdmVydmlldyh1cGRhdGUsIGNvbnRleHQpOyByZXR1"
    "cm4KICAgICAgICBpZiBfYWRtaW5faXMoImFkbWluX2J0bl9wbGF5ZXJzIik6CiAgICAgICAgICAgIGF3YWl0IHBsYXllcnNfY29t"
    "bWFuZCh1cGRhdGUsIGNvbnRleHQpOyByZXR1cm4KICAgICAgICBpZiBfYWRtaW5faXMoImFkbWluX2J0bl9zb2xkIik6CiAgICAg"
    "ICAgICAgIGF3YWl0IF9zZW5kX3JvdW5kX3BpY2tlcih1cGRhdGUsIGNvbnRleHQsICJzb2xkIiwgcm91bmRzLmtleXMoKSwgIvCf"
    "kYcg4YuZ4YitIOGLreGIneGIqOGMoeGNpiIsICLihLnvuI8g4Yid4YqV4YidIOGLmeGIrSDhi6jhiIjhiJ3hjaIiKTsgcmV0dXJu"
    "CiAgICAgICAgaWYgX2FkbWluX2lzKCJhZG1pbl9idG5fYWxsX3RpY2tldHMiKToKICAgICAgICAgICAgYXdhaXQgX3NlbmRfcm91"
    "bmRfcGlja2VyKHVwZGF0ZSwgY29udGV4dCwgImFsbHRpY2tldHMiLCByb3VuZHMua2V5cygpLCAi8J+RhyDhi5nhiK0g4Yut4Yid"
    "4Yio4Yyh4Y2mIiwgIuKEue+4jyDhiJ3hipXhiJ0g4YuZ4YitIOGLqOGIiOGIneGNoiIpOyByZXR1cm4KICAgICAgICBpZiBfYWRt"
    "aW5faXMoImFkbWluX2J0bl91bnNvbGQiKToKICAgICAgICAgICAgdW5zb2xkX3JvdW5kX2lkcyA9IFtyaWQgZm9yIHJpZCwgciBp"
    "biByb3VuZHMuaXRlbXMoKSBpZiBhbnkodFsic3RhdHVzIl0gPT0gIkFWQUlMQUJMRSIgZm9yIHQgaW4gclsidGlja2V0cyJdLnZh"
    "bHVlcygpKV0KICAgICAgICAgICAgYXdhaXQgX3NlbmRfcm91bmRfcGlja2VyKHVwZGF0ZSwgY29udGV4dCwgInVuc29sZCIsIHVu"
    "c29sZF9yb3VuZF9pZHMgaWYgdW5zb2xkX3JvdW5kX2lkcyBlbHNlIGxpc3Qocm91bmRzLmtleXMoKSksICLwn5GHIOGLmeGIrSDh"
    "i63hiJ3hiKjhjKHhjaYiLCAi4oS577iPIOGIneGKleGInSDhi5nhiK0g4Yuo4YiI4Yid4Y2iIik7IHJldHVybgogICAgICAgIGlm"
    "IF9hZG1pbl9pcygiYWRtaW5fYnRuX2hvc3RfcHJvZmlsZSIpOgogICAgICAgICAgICBhd2FpdCBob3N0X3Byb2ZpbGUodXBkYXRl"
    "LCBjb250ZXh0KTsgcmV0dXJuCiAgICAgICAgaWYgX2FkbWluX2lzKCJhZG1pbl9idG5fYWRkX2NyZWRpdCIpOgogICAgICAgICAg"
    "ICBjb250ZXh0LmFyZ3MgPSBbXQogICAgICAgICAgICBhd2FpdCBhZGRfY3JlZGl0KHVwZGF0ZSwgY29udGV4dCk7IHJldHVybgog"
    "ICAgICAgIGlmIF9hZG1pbl9pcygiYWRtaW5fYnRuX3BheW1lbnRfYWNjb3VudCIpOgogICAgICAgICAgICBhd2FpdCBlZGl0X3Bh"
    "eW1lbnRfYWNjb3VudCh1cGRhdGUsIGNvbnRleHQpOyByZXR1cm4KICAgICAgICBpZiBfYWRtaW5faXMoImFkbWluX2J0bl9wYXlt"
    "ZW50cyIpOgogICAgICAgICAgICBhd2FpdCB1c2VkX3JlZnNfY29tbWFuZCh1cGRhdGUsIGNvbnRleHQpOyByZXR1cm4KICAgICAg"
    "ICBpZiBfYWRtaW5faXMoImFkbWluX2J0bl9zdGF0cyIpOgogICAgICAgICAgICBhd2FpdCBnYW1lX3N0YXRzKHVwZGF0ZSwgY29u"
    "dGV4dCk7IHJldHVybgogICAgICAgIGlmIF9hZG1pbl9pcygiYWRtaW5fYnRuX2Fubm91bmNlIik6CiAgICAgICAgICAgIGNvbnRl"
    "eHQuYXJncyA9IFtdCiAgICAgICAgICAgIGF3YWl0IGFubm91bmNlKHVwZGF0ZSwgY29udGV4dCk7IHJldHVybgogICAgICAgIGlm"
    "IF9hZG1pbl9pcygiYWRtaW5fYnRuX2hlbHAiKToKICAgICAgICAgICAgYXdhaXQgaGVscF9jb21tYW5kKHVwZGF0ZSwgY29udGV4"
    "dCk7IHJldHVybgogICAgICAgIGlmIF9hZG1pbl9pcygiYWRtaW5fYnRuX3BhdXNlIikgb3IgX2FkbWluX2lzKCJhZG1pbl9idG5f"
    "cmVzdW1lIik6CiAgICAgICAgICAgIGlzX3BhdXNlID0gX2FkbWluX2lzKCJhZG1pbl9idG5fcGF1c2UiKQogICAgICAgICAgICBl"
    "bGlnaWJsZT1bcmlkIGZvciByaWQsciBpbiByb3VuZHMuaXRlbXMoKSBpZiAoclsnc3RhdHVzJ109PSdPUEVOJyBpZiBpc19wYXVz"
    "ZSBlbHNlIHJbJ3N0YXR1cyddPT0nUEFVU0VEJyldCiAgICAgICAgICAgIGFjdGlvbj0ncGF1c2UnIGlmIGlzX3BhdXNlIGVsc2Ug"
    "J3Jlc3VtZScKICAgICAgICAgICAgYXdhaXQgX3NlbmRfcm91bmRfcGlja2VyKHVwZGF0ZSwgY29udGV4dCwgYWN0aW9uLCBlbGln"
    "aWJsZSwgIvCfkYcg4YuZ4YitIOGLreGIneGIqOGMoeGNpiIsICLihLnvuI8g4YiI4Yua4YiFIOGKpeGIreGIneGMgyDhibDhiLXh"
    "iJvhiJog4YuZ4YitIOGLqOGIiOGIneGNoiIpOyByZXR1cm4KICAgICAgICBpZiBfYWRtaW5faXMoImFkbWluX2J0bl9jbG9zZV9y"
    "b3VuZCIpOgogICAgICAgICAgICBhd2FpdCBfc2VuZF9yb3VuZF9waWNrZXIodXBkYXRlLCBjb250ZXh0LCAiY2xvc2UiLCByb3Vu"
    "ZHMua2V5cygpLCAi8J+RhyDhi6jhiJrhi5jhjIvhi43hipUg4YuZ4YitIOGLreGIneGIqOGMoeGNpiIsICLihLnvuI8g4Yid4YqV"
    "4YidIOGLmeGIrSDhi6jhiIjhiJ3hjaIiKTsgcmV0dXJuCiAgICAgICAgaWYgX2FkbWluX2lzKCJhZG1pbl9idG5fcmVzdGFydF9y"
    "b3VuZCIpOgogICAgICAgICAgICBhd2FpdCBfc2VuZF9yb3VuZF9waWNrZXIodXBkYXRlLCBjb250ZXh0LCAicmVzdGFydCIsIHJv"
    "dW5kcy5rZXlzKCksICLwn5GHIOGLqOGImuGMgOGImOGIqOGLjeGKlSDhi5nhiK0g4Yut4Yid4Yio4Yyh4Y2mIiwgIuKEue+4jyDh"
    "iJ3hipXhiJ0g4YuZ4YitIOGLqOGIiOGIneGNoiIpOyByZXR1cm4KICAgICAgICBpZiBfYWRtaW5faXMoImFkbWluX2J0bl9kZWxl"
    "dGVfcm91bmQiKToKICAgICAgICAgICAgYXdhaXQgX3NlbmRfcm91bmRfcGlja2VyKHVwZGF0ZSwgY29udGV4dCwgImRlbGV0ZSIs"
    "IHJvdW5kcy5rZXlzKCksICLwn5GHIOGLqOGImuGIsOGIqOGLmOGLjeGKlSDhi5nhiK0g4Yut4Yid4Yio4Yyh4Y2mIiwgIuKEue+4"
    "jyDhiJ3hipXhiJ0g4YuZ4YitIOGLqOGIiOGIneGNoiIpOyByZXR1cm4KICAgICAgICBpZiBfYWRtaW5faXMoImFkbWluX2J0bl9t"
    "YW51YWxfc2FsZSIpOgogICAgICAgICAgICBjbGVhcmVkID0gX2NhbmNlbF9vdGhlcl9hZG1pbl90YXNrcyh1aWQsIGtlZXA9Im1h"
    "bnVhbHNlbGwiKQogICAgICAgICAgICBhd2FpdCBfbm90aWZ5X2NhbmNlbGxlZF90YXNrcyhjb250ZXh0LmJvdCwgdWlkLCBjbGVh"
    "cmVkKQogICAgICAgICAgICBhd2FpdCBfc2VuZF9yb3VuZF9waWNrZXIodXBkYXRlLCBjb250ZXh0LCAibWFudWFsc2VsbHJvdW5k"
    "IiwgW3JpZCBmb3IgcmlkLHIgaW4gcm91bmRzLml0ZW1zKCkgaWYgclsnc3RhdHVzJ109PSdPUEVOJ10sICLwn5GHIOGLmeGIrSDh"
    "i63hiJ3hiKjhjKHhjaYiLCAi4oS577iPIOGKreGNjeGJtSDhi5nhiK0g4Yuo4YiI4Yid4Y2iIik7IHJldHVybgogICAgICAgIGlm"
    "IF9hZG1pbl9pcygiYWRtaW5fYnRuX3JlbGVhc2VfdGlja2V0Iik6CiAgICAgICAgICAgIGF3YWl0IF9zZW5kX3JvdW5kX3BpY2tl"
    "cih1cGRhdGUsIGNvbnRleHQsICJjYW5jZWxyb3VuZCIsIHJvdW5kcy5rZXlzKCksICLwn5GHIOGLmeGIrSDhi63hiJ3hiKjhjKHh"
    "jaYiLCAi4oS577iPIOGIneGKleGInSDhi5nhiK0g4Yuo4YiI4Yid4Y2iIik7IHJldHVybgogICAgICAgIGlmIF9hZG1pbl9pcygi"
    "YnRuX3dpbm5lcnMiKToKICAgICAgICAgICAgY29udGV4dC5hcmdzID0gW10KICAgICAgICAgICAgYXdhaXQgd2lubmVyc19jb21t"
    "YW5kKHVwZGF0ZSwgY29udGV4dCk7IHJldHVybgogICAgICAgIGlmIF9hZG1pbl9pcygiYWRtaW5fYnRuX3NldF93aW5uZXIiKToK"
    "ICAgICAgICAgICAgY29udGV4dC5hcmdzID0gW10KICAgICAgICAgICAgYXdhaXQgc2V0X3dpbm5lcl9jb21tYW5kKHVwZGF0ZSwg"
    "Y29udGV4dCk7IHJldHVybgogICAgICAgIHJldHVybgogICAgIyBQbGF5ZXIgbWVudQogICAgbGFuZyA9IGdldF9sYW5nKHVpZCkK"
    "ICAgIGlmIHRleHQgaW4gKFRYVFsiYnRuX2xhbmd1YWdlIl1bImFtIl0sIFRYVFsiYnRuX2xhbmd1YWdlIl1bImVuIl0sIFRYVFsi"
    "YnRuX2xhbmd1YWdlIl1bIm9tIl0pOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoTCh1aWQsICJjaG9v"
    "c2VfbGFuZ3VhZ2UiKSwgcmVwbHlfbWFya3VwPWxhbmd1YWdlX3BpY2tlcl9rYigic3dpdGNobGFuZyIpKQogICAgICAgIHJldHVy"
    "bgogICAgaWYgYXdhaXQgX2Jsb2NrX3dpdGhfcGVuZGluZ19zZWxlY3Rpb25fbm90aWNlKHVwZGF0ZSwgdWlkKToKICAgICAgICBy"
    "ZXR1cm4KICAgIGlmIHRleHQgaW4gKFRYVFsiYnRuX3BsYXkiXVsiYW0iXSwgVFhUWyJidG5fcGxheSJdWyJlbiJdLCBUWFRbImJ0"
    "bl9wbGF5Il1bIm9tIl0pOgogICAgICAgIGF3YWl0IHBsYXkodXBkYXRlLCBjb250ZXh0KTsgcmV0dXJuCiAgICBpZiB0ZXh0IGlu"
    "IChUWFRbImJ0bl9yb3VuZHMiXVsiYW0iXSwgVFhUWyJidG5fcm91bmRzIl1bImVuIl0sIFRYVFsiYnRuX3JvdW5kcyJdWyJvbSJd"
    "KToKICAgICAgICBhd2FpdCByb3VuZHNfY29tbWFuZCh1cGRhdGUsIGNvbnRleHQpOyByZXR1cm4KICAgIGlmIHRleHQgaW4gKFRY"
    "VFsiYnRuX3dpbm5lcnMiXVsiYW0iXSwgVFhUWyJidG5fd2lubmVycyJdWyJlbiJdLCBUWFRbImJ0bl93aW5uZXJzIl1bIm9tIl0p"
    "OgogICAgICAgIGNvbnRleHQuYXJncyA9IFtdCiAgICAgICAgYXdhaXQgd2lubmVyc19jb21tYW5kKHVwZGF0ZSwgY29udGV4dCk7"
    "IHJldHVybgogICAgaWYgdGV4dCBpbiAoVFhUWyJidG5fbXlpbmZvIl1bImFtIl0sIFRYVFsiYnRuX215aW5mbyJdWyJlbiJdLCBU"
    "WFRbImJ0bl9teWluZm8iXVsib20iXSk6CiAgICAgICAgaWYgdWlkIGluIHBsYXllcnM6CiAgICAgICAgICAgIHAgPSBwbGF5ZXJz"
    "W3VpZF0KICAgICAgICAgICAgaW5mb190ZXh0ID0gTCh1aWQsICJteV9pbmZvIiwgbmFtZT1wWyduYW1lJ10sIHBob25lPShwLmdl"
    "dCgncGhvbmUnKSBvciAnTi9BJyksIHVzZXJuYW1lPXAuZ2V0KCd1c2VybmFtZScpIG9yICfigJQnKQogICAgICAgICAgICBteV90"
    "aWNrZXRzID0gZ2V0X3BsYXllcl90aWNrZXRzKHVpZCkKICAgICAgICAgICAgaWYgbm90IG15X3RpY2tldHM6CiAgICAgICAgICAg"
    "ICAgICBpbmZvX3RleHQgKz0gTCh1aWQsICJteV9pbmZvX25vX3RpY2tldHMiKQogICAgICAgICAgICBlbHNlOgogICAgICAgICAg"
    "ICAgICAgaW5mb190ZXh0ICs9IEwodWlkLCAibXlfaW5mb190aWNrZXRzX2hlYWRlciIpCiAgICAgICAgICAgICAgICBmb3Igcmlk"
    "LCB0biwgdCBpbiBteV90aWNrZXRzOgogICAgICAgICAgICAgICAgICAgIHJsYWJlbCA9IHJvdW5kX2xhYmVsKHJpZCkKICAgICAg"
    "ICAgICAgICAgICAgICByb3VuZF9zdGF0dXMgPSByb3VuZHMuZ2V0KHJpZCwge30pLmdldCgic3RhdHVzIikKICAgICAgICAgICAg"
    "ICAgICAgICBpZiByb3VuZF9zdGF0dXMgPT0gIkNMT1NFRCI6CiAgICAgICAgICAgICAgICAgICAgICAgIGluZm9fdGV4dCArPSAi"
    "XG4iICsgTCh1aWQsICJ0aWNrZXRfbGluZV9vbGQiLCB0bj10biwgcmxhYmVsPXJsYWJlbCkKICAgICAgICAgICAgICAgICAgICBl"
    "bGlmIHQuZ2V0KCJzdGF0dXMiKSA9PSAiUEVORElORyI6CiAgICAgICAgICAgICAgICAgICAgICAgIGluZm9fdGV4dCArPSAiXG4i"
    "ICsgTCh1aWQsICJ0aWNrZXRfbGluZV93YWl0aW5nIiwgdG49dG4sIHJsYWJlbD1ybGFiZWwpCiAgICAgICAgICAgICAgICAgICAg"
    "ZWxzZToKICAgICAgICAgICAgICAgICAgICAgICAgaW5mb190ZXh0ICs9ICJcbiIgKyBMKHVpZCwgInRpY2tldF9saW5lX2NvbmZp"
    "cm1lZCIsIHRuPXRuLCBybGFiZWw9cmxhYmVsKQogICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KGlu"
    "Zm9fdGV4dCwgcGFyc2VfbW9kZT0iSFRNTCIsIHJlcGx5X21hcmt1cD1wbGF5ZXJfa2V5Ym9hcmQobGFuZykpCiAgICAgICAgZWxz"
    "ZToKICAgICAgICAgICAgYXdhaXQgc3RhcnQodXBkYXRlLCBjb250ZXh0KQogICAgICAgIHJldHVybgogICAgaWYgdGV4dCBpbiAo"
    "VFhUWyJidG5faGVscCJdWyJhbSJdLCBUWFRbImJ0bl9oZWxwIl1bImVuIl0sIFRYVFsiYnRuX2hlbHAiXVsib20iXSk6CiAgICAg"
    "ICAgYXdhaXQgaGVscF9jb21tYW5kKHVwZGF0ZSwgY29udGV4dCk7IHJldHVybgogICAgaWYgdGV4dCBpbiAoVFhUWyJidG5fY29u"
    "dGFjdCJdWyJhbSJdLCBUWFRbImJ0bl9jb250YWN0Il1bImVuIl0sIFRYVFsiYnRuX2NvbnRhY3QiXVsib20iXSk6CiAgICAgICAg"
    "YXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgTCh1aWQsICJjb250YWN0X2FkbWluIiksCiAgICAg"
    "ICAgICAgIHBhcnNlX21vZGU9IkhUTUwiLAogICAgICAgICAgICByZXBseV9tYXJrdXA9SW5saW5lS2V5Ym9hcmRNYXJrdXAoW1tJ"
    "bmxpbmVLZXlib2FyZEJ1dHRvbihMKHVpZCwgImNhbGxfYnV0dG9uIiwgYWNjb3VudD0iMDk2MzUzMDAzMCIpLCB1cmw9InRlbDow"
    "OTYzNTMwMDMwIildXSkKICAgICAgICApCiAgICAgICAgcmV0dXJuCiAgICBpZiB0ZXh0IGluIChUWFRbImJ0bl9iZWNvbWVfaG9z"
    "dCJdWyJhbSJdLCBUWFRbImJ0bl9iZWNvbWVfaG9zdCJdWyJlbiJdLCBUWFRbImJ0bl9iZWNvbWVfaG9zdCJdWyJvbSJdKToKICAg"
    "ICAgICBwbGF5ZXIgPSBwbGF5ZXJzLmdldCh1aWQsIHt9KQogICAgICAgIGF3YWl0IF9zZW5kX2hvc3Rfc2lnbnVwX3JlcXVlc3Rf"
    "dG9fc3VwZXJfYWRtaW4odWlkLCBwbGF5ZXIsIGNvbnRleHQuYm90KQogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5"
    "X3RleHQoTCh1aWQsICJiZWNvbWVfaG9zdF9yZXF1ZXN0X3NlbnQiKSwgcmVwbHlfbWFya3VwPXBsYXllcl9rZXlib2FyZChsYW5n"
    "KSkKICAgICAgICByZXR1cm4KCmFzeW5jIGRlZiBoYW5kbGVfcmVnaXN0cmF0aW9uKHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBD"
    "b250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIuGKoOGLsuGItSDhibDhjKvhi4vhib0g4Yi14YidIOGKpeGKkyDhiLXh"
    "iI3hiq0g4YmB4Yyl4YitIOGKpeGKleGLsuGLq+GIteGMiOGJoyDhi6jhiJrhi6vhi7DhiK3hjI0g4Yuw4Yio4YyDIiIiCiAgICB1"
    "c2VyID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyCiAgICBzdGF0ZSA9IHJlZ2lzdHJhdGlvbl9zdGF0ZS5nZXQodXNlci5pZCkK"
    "ICAgIHRleHQgPSB1cGRhdGUubWVzc2FnZS50ZXh0LnN0cmlwKCkKCiAgICBpZiBzdGF0ZSA9PSAiYXdhaXRpbmdfbGFuZ3VhZ2Ui"
    "OgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAgICAgIFRYVFsiY2hvb3NlX2xhbmd1YWdl"
    "Il1bImFtIl0gKyAiIC8gIiArIFRYVFsiY2hvb3NlX2xhbmd1YWdlIl1bImVuIl0gKyAiIC8gIiArIFRYVFsiY2hvb3NlX2xhbmd1"
    "YWdlIl1bIm9tIl0sCiAgICAgICAgICAgIHJlcGx5X21hcmt1cD1sYW5ndWFnZV9waWNrZXJfa2IoInNldGxhbmciKQogICAgICAg"
    "ICkKICAgICAgICByZXR1cm4KCiAgICBpZiBzdGF0ZSA9PSAiYXdhaXRpbmdfbmFtZSI6CiAgICAgICAgaWYgbGVuKHRleHQpIDwg"
    "MjoKICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dChMKHVzZXIuaWQsICJpbnZhbGlkX25hbWUiKSkK"
    "ICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgcmVnaXN0cmF0aW9uX3RlbXBbdXNlci5pZF1bIm5hbWUiXSA9IHRleHQKICAgICAg"
    "ICByZWdpc3RyYXRpb25fc3RhdGVbdXNlci5pZF0gPSAiYXdhaXRpbmdfcGhvbmUiCiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3Nh"
    "Z2UucmVwbHlfdGV4dChMKHVzZXIuaWQsICJhc2tfcGhvbmUiKSkKICAgICAgICByZXR1cm4KCiAgICBpZiBzdGF0ZSA9PSAiYXdh"
    "aXRpbmdfcGhvbmUiOgogICAgICAgIGlmIG5vdCBQSE9ORV9QQVRURVJOLm1hdGNoKHRleHQucmVwbGFjZSgiICIsICIiKSk6CiAg"
    "ICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoTCh1c2VyLmlkLCAiaW52YWxpZF9waG9uZSIpKQogICAg"
    "ICAgICAgICByZXR1cm4KCiAgICAgICAgbmFtZSA9IHJlZ2lzdHJhdGlvbl90ZW1wW3VzZXIuaWRdWyJuYW1lIl0KICAgICAgICBl"
    "eGlzdGluZ191aWQgPSBwaG9uZV9hbHJlYWR5X3JlZ2lzdGVyZWQodGV4dCwgZXhjbHVkZV91c2VyX2lkPXVzZXIuaWQpCiAgICAg"
    "ICAgaWYgZXhpc3RpbmdfdWlkIGlzIG5vdCBOb25lOgogICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0"
    "KAogICAgICAgICAgICAgICAgTCh1c2VyLmlkLCAicGhvbmVfYWxyZWFkeV9yZWdpc3RlcmVkIiksCiAgICAgICAgICAgICAgICBw"
    "YXJzZV9tb2RlPSJIVE1MIiwgcmVwbHlfbWFya3VwPXBsYXllcl9rZXlib2FyZChnZXRfbGFuZyh1c2VyLmlkKSkKICAgICAgICAg"
    "ICAgKQogICAgICAgICAgICByZXR1cm4KCiAgICAgICAgcGxheWVyc1t1c2VyLmlkXSA9IHsKICAgICAgICAgICAgIm5hbWUiOiBu"
    "YW1lLAogICAgICAgICAgICAicGhvbmUiOiB0ZXh0LAogICAgICAgICAgICAicGhvbmVfbm9ybWFsaXplZCI6IG5vcm1hbGl6ZV9w"
    "aG9uZSh0ZXh0KSwKICAgICAgICAgICAgInVzZXJuYW1lIjogdXNlci51c2VybmFtZSBvciB1c2VyLmZpcnN0X25hbWUsCiAgICAg"
    "ICAgfQogICAgICAgIGRlbCByZWdpc3RyYXRpb25fc3RhdGVbdXNlci5pZF0KICAgICAgICBkZWwgcmVnaXN0cmF0aW9uX3RlbXBb"
    "dXNlci5pZF0KICAgICAgICBzYXZlX3N0YXRlKCkKCiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAg"
    "ICAgICAgICAgTCh1c2VyLmlkLCAicmVnaXN0cmF0aW9uX2NvbXBsZXRlIiwgbmFtZT1uYW1lKSwKICAgICAgICAgICAgcmVwbHlf"
    "bWFya3VwPXBsYXllcl9rZXlib2FyZChnZXRfbGFuZyh1c2VyLmlkKSkKICAgICAgICApCgogICAgICAgIHRyeToKICAgICAgICAg"
    "ICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9tZXNzYWdlKAogICAgICAgICAgICAgICAgY2hhdF9pZD1BRE1JTl9JRCwKICAgICAg"
    "ICAgICAgICAgIHRleHQ9KAogICAgICAgICAgICAgICAgICAgIGYi8J+GlSA8Yj7hiqDhi7LhiLUg4Ymw4Yyr4YuL4Ym9IOGJsOGI"
    "mOGLneGMjeGJp+GIjSE8L2I+XG5cbiIKICAgICAgICAgICAgICAgICAgICBmIvCfkaQg4Yi14YidOiB7bmFtZX1cbiIKICAgICAg"
    "ICAgICAgICAgICAgICBmIvCfk7Eg4Yi14YiN4YqtOiB7dGV4dH1cbiIKICAgICAgICAgICAgICAgICAgICBmIvCflJcg4Yup4YuY"
    "4Yit4YqU4YidOiBAe3BsYXllcnNbdXNlci5pZF1bJ3VzZXJuYW1lJ119XG4iCiAgICAgICAgICAgICAgICAgICAgZiLwn4aUIFVz"
    "ZXIgSUQ6IHt1c2VyLmlkfSIKICAgICAgICAgICAgICAgICksCiAgICAgICAgICAgICAgICBwYXJzZV9tb2RlPSJIVE1MIgogICAg"
    "ICAgICAgICApCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgcGFzcwogICAgICAgIHJldHVybgoKYXN5bmMg"
    "ZGVmIGhhbmRsZV90aWNrZXQodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAg"
    "IiIi4Yqg4YqV4Yu1IOGJgeGMpeGIrSDhiLLhipDhiqsg4YuI4YuwIMKr4YyN4YuiIOGJheGIreGMq+GJtcK7IChjYXJ0KSDhi6jh"
    "iJrhjKjhiJ3hiK0v4Yuo4Yia4Yur4Yi14YuI4YyN4Yu1IC0g4YyI4YqTIOGKoOGLreGJhuGIjeGNjeGInSAoY2hlY2tvdXQg4Yiy"
    "4Yuw4Yio4YyNIOGJpeGJuyDhi63hiYbhiIjhjYvhiI0p4Y2iIiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQog"
    "ICAgcGFydHMgPSBxdWVyeS5kYXRhLnNwbGl0KCJfIikKICAgIHJvdW5kX2lkID0gaW50KHBhcnRzWzFdKQogICAgdGlja2V0X251"
    "bSA9IGludChwYXJ0c1syXSkKICAgIHBhZ2UgPSBpbnQocGFydHNbM10pIGlmIGxlbihwYXJ0cykgPiAzIGVsc2UgMAogICAgdXNl"
    "ciA9IHF1ZXJ5LmZyb21fdXNlcgoKICAgIGlmIHVzZXIuaWQgPT0gQURNSU5fSUQ6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2Vy"
    "KCLwn5qrIOGKoOGLteGImuGKlSDhibLhiqzhibUg4YiY4Yid4Yio4YylIOGKoOGLreGJveGIjeGIneGNoiIsIHNob3dfYWxlcnQ9"
    "VHJ1ZSkKICAgICAgICByZXR1cm4KCiAgICBpZiB1c2VyLmlkIG5vdCBpbiBwbGF5ZXJzOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFu"
    "c3dlcigi4Yql4Ymj4Yqt4YuOIOGJoOGImOGMgOGImOGIquGLqyAvc3RhcnQg4Yml4YiI4YuNIOGLreGImOGLneGMiOGJoeGNoiIs"
    "IHNob3dfYWxlcnQ9VHJ1ZSkKICAgICAgICByZXR1cm4KCiAgICBpZiBob3N0X3BhdXNlZDoKICAgICAgICBhd2FpdCBxdWVyeS5h"
    "bnN3ZXIoIuKPuO+4jyDhiL3hi6vhjK0g4Ymg4Yqg4Yi14Ymw4Yuz4Yuz4YiqIOGIiOGMiuGLnOGLjSDhiYbhiJ/hiI3hjaIiLCBz"
    "aG93X2FsZXJ0PVRydWUpCiAgICAgICAgcmV0dXJuCgogICAgaWYgcm91bmRfaWQgbm90IGluIHJvdW5kcyBvciByb3VuZHNbcm91"
    "bmRfaWRdWyJzdGF0dXMiXSAhPSAiT1BFTiI6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgICAgICBhd2FpdCBxdWVy"
    "eS5lZGl0X21lc3NhZ2VfdGV4dChMKHVzZXIuaWQsICJyb3VuZF9jbG9zZWQiKSkKICAgICAgICByZXR1cm4KCiAgICBleGlzdGlu"
    "Z19wZW5kaW5nID0gX2dldF9wZW5kaW5nX3NlbGVjdGlvbih1c2VyLmlkKQogICAgaWYgZXhpc3RpbmdfcGVuZGluZyBhbmQgZXhp"
    "c3RpbmdfcGVuZGluZ1swXSAhPSByb3VuZF9pZDoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIGV4X3JvdW5k"
    "X2lkLCBleF90aWNrZXRzLCBfID0gZXhpc3RpbmdfcGVuZGluZwogICAgICAgIGtiID0gSW5saW5lS2V5Ym9hcmRNYXJrdXAoW1sK"
    "ICAgICAgICAgICAgSW5saW5lS2V5Ym9hcmRCdXR0b24oTCh1c2VyLmlkLCAiYnRuX3JldHVybl9wdXJjaGFzZSIpLCBjYWxsYmFj"
    "a19kYXRhPWYicmV0dXJucGVuZGluZ197ZXhfcm91bmRfaWR9IiksCiAgICAgICAgICAgIElubGluZUtleWJvYXJkQnV0dG9uKEwo"
    "dXNlci5pZCwgImJ0bl9jYW5jZWxfdGlja2V0IiksIGNhbGxiYWNrX2RhdGE9ZiJjYW5jZWxwZW5kaW5nX3tleF9yb3VuZF9pZH0i"
    "KQogICAgICAgIF1dKQogICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KAogICAgICAgICAgICBMKHVzZXIuaWQs"
    "ICJwZW5kaW5nX25vdGljZSIsIHRucz0iLCAiLmpvaW4oc3RyKG4pIGZvciBuIGluIGV4X3RpY2tldHMpLCBybGFiZWw9cm91bmRf"
    "bGFiZWwoZXhfcm91bmRfaWQpKSwKICAgICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIsCiAgICAgICAgICAgIHJlcGx5X21hcmt1"
    "cD1rYgogICAgICAgICkKICAgICAgICByZXR1cm4KCiAgICByX3RpY2tldHMgPSByb3VuZHNbcm91bmRfaWRdWyJ0aWNrZXRzIl0K"
    "ICAgIG5vdyA9IGRhdGV0aW1lLm5vdygpCiAgICBpZiByX3RpY2tldHNbdGlja2V0X251bV1bInN0YXR1cyJdID09ICJQRU5ESU5H"
    "IiBhbmQgcl90aWNrZXRzW3RpY2tldF9udW1dWyJleHBpcmVzX2F0Il0gYW5kIHJfdGlja2V0c1t0aWNrZXRfbnVtXVsiZXhwaXJl"
    "c19hdCJdIDwgbm93OgogICAgICAgIG9sZF91aWQgPSByX3RpY2tldHNbdGlja2V0X251bV0uZ2V0KCJ1c2VyX2lkIikKICAgICAg"
    "ICByX3RpY2tldHNbdGlja2V0X251bV0gPSBfZW1wdHlfdGlja2V0KCkKICAgICAgICBfcmVtb3ZlX3RpY2tldF9mcm9tX3NlbGVj"
    "dGlvbihvbGRfdWlkLCByb3VuZF9pZCwgdGlja2V0X251bSkKICAgICAgICBzYXZlX3N0YXRlKCkKICAgICAgICBhd2FpdCBub3Rp"
    "ZnlfdGlja2V0X3dhdGNoZXJzKHJvdW5kX2lkLCB0aWNrZXRfbnVtLCBjb250ZXh0LmJvdCkKCiAgICBpZiByX3RpY2tldHNbdGlj"
    "a2V0X251bV1bInN0YXR1cyJdID09ICJTT0xEIjoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoTCh1c2VyLmlkLCAidGlja2V0"
    "X2FscmVhZHlfc29sZCIsIHRuPXRpY2tldF9udW0sIHJpZD1yb3VuZF9pZCksIHNob3dfYWxlcnQ9VHJ1ZSkKICAgICAgICByZXR1"
    "cm4KCiAgICBpZiByX3RpY2tldHNbdGlja2V0X251bV1bInN0YXR1cyJdID09ICJQRU5ESU5HIiBhbmQgcl90aWNrZXRzW3RpY2tl"
    "dF9udW1dWyJ1c2VyX2lkIl0gIT0gdXNlci5pZDoKICAgICAgICB0aWNrZXRfd2F0Y2hlcnMuc2V0ZGVmYXVsdCgocm91bmRfaWQs"
    "IHRpY2tldF9udW0pLCBzZXQoKSkuYWRkKHVzZXIuaWQpCiAgICAgICAgc2F2ZV9zdGF0ZSgpCiAgICAgICAgYXdhaXQgcXVlcnku"
    "YW5zd2VyKEwodXNlci5pZCwgInRpY2tldF90YWtlbl9ieV9vdGhlciIsIHRuPXRpY2tldF9udW0pLCBzaG93X2FsZXJ0PVRydWUp"
    "CiAgICAgICAgcmV0dXJuCgogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKCiAgICAjIOGMiOGKkyDhiIvhiI3hibDhiYbhiIjhjYgg"
    "4YmF4Yit4Yyr4Ym1IChjYXJ0KSDhiJjhjKjhiJjhiK0v4Yib4Yi14YuI4YyI4Yu1IC0g4YmB4Yyl4YipIOGLqOGImuGJhuGIiOGN"
    "iOGLjSDhibDhjKvhi4vhibkgIuGKreGNjeGLqyDhiYDhjKXhiI0iIOGIsuGMq+GKlSDhiaXhibsg4YqQ4YuNCiAgICBjYXJ0ID0g"
    "dXNlcl9jYXJ0cy5zZXRkZWZhdWx0KHVzZXIuaWQsIHsicm91bmRfaWQiOiByb3VuZF9pZCwgInRpY2tldHMiOiBzZXQoKX0pCiAg"
    "ICBpZiBjYXJ0WyJyb3VuZF9pZCJdICE9IHJvdW5kX2lkOgogICAgICAgIGNhcnRbInJvdW5kX2lkIl0gPSByb3VuZF9pZAogICAg"
    "ICAgIGNhcnRbInRpY2tldHMiXSA9IHNldCgpCiAgICBpZiB0aWNrZXRfbnVtIGluIGNhcnRbInRpY2tldHMiXToKICAgICAgICBj"
    "YXJ0WyJ0aWNrZXRzIl0uZGlzY2FyZCh0aWNrZXRfbnVtKQogICAgZWxzZToKICAgICAgICBjYXJ0WyJ0aWNrZXRzIl0uYWRkKHRp"
    "Y2tldF9udW0pCgogICAgdGV4dCA9IF9yZW5kZXJfcGlja19tZXNzYWdlKHJvdW5kX2lkLCB1c2VyLmlkLCBjYXJ0WyJ0aWNrZXRz"
    "Il0pCiAgICBrYiA9IGdldF9rZXlib2FyZChyb3VuZF9pZCwgcGFnZT1wYWdlLCB1c2VyX2lkPXVzZXIuaWQsIGNhcnQ9Y2FydFsi"
    "dGlja2V0cyJdKQogICAgdHJ5OgogICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KHRleHQsIHBhcnNlX21vZGU9"
    "IkhUTUwiLCByZXBseV9tYXJrdXA9a2IpCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHRyeToKICAgICAgICAgICAgYXdh"
    "aXQgcXVlcnkuZWRpdF9tZXNzYWdlX3JlcGx5X21hcmt1cChyZXBseV9tYXJrdXA9a2IpCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlv"
    "bjoKICAgICAgICAgICAgcGFzcwoKYXN5bmMgZGVmIGhhbmRsZV93YXRjaF90aWNrZXQodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6"
    "IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgcXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIHVpZCA9IHF1"
    "ZXJ5LmZyb21fdXNlci5pZAogICAgYXdhaXQgcXVlcnkuYW5zd2VyKEwodWlkLCAid2F0Y2hfY29uZmlybWVkX2FsZXJ0IikpCiAg"
    "ICBfLCByaWQsIHRuID0gcXVlcnkuZGF0YS5zcGxpdCgiXyIpCiAgICByaWQsIHRuID0gaW50KHJpZCksIGludCh0bikKICAgIGlm"
    "IHVpZCA9PSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KICAgIHRpY2tldF93YXRjaGVycy5zZXRkZWZhdWx0KChyaWQsIHRuKSwg"
    "c2V0KCkpLmFkZCh1aWQpCiAgICBzYXZlX3N0YXRlKCkKICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KAogICAgICAg"
    "IEwodWlkLCAid2F0Y2hfY29uZmlybWVkX3RleHQiLCB0bj10biwgcmxhYmVsPXJvdW5kX2xhYmVsKHJpZCkpLAogICAgICAgIHJl"
    "cGx5X21hcmt1cD1wbGF5ZXJfa2V5Ym9hcmQoZ2V0X2xhbmcodWlkKSkKICAgICkKCmFzeW5jIGRlZiBoYW5kbGVfa2VlcF9wYXlt"
    "ZW50KHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgIHF1ZXJ5ID0gdXBkYXRl"
    "LmNhbGxiYWNrX3F1ZXJ5CiAgICB1aWQgPSBxdWVyeS5mcm9tX3VzZXIuaWQKICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcihMKHVpZCwg"
    "InBheW1lbnRfa2VwdF9hbGVydCIpKQogICAgcGFydHMgPSBxdWVyeS5kYXRhLnNwbGl0KCJfIikKICAgIHJvdW5kX2lkID0gaW50"
    "KHBhcnRzWzFdKQogICAgcGVuZGluZyA9IF9nZXRfcGVuZGluZ19zZWxlY3Rpb24odWlkKQogICAgaWYgbm90IHBlbmRpbmcgb3Ig"
    "cGVuZGluZ1swXSAhPSByb3VuZF9pZDoKICAgICAgICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfdGV4dChMKHVpZCwgImFscmVh"
    "ZHlfY2FuY2VsbGVkX29yX2V4cGlyZWQiKSkKICAgICAgICByZXR1cm4KICAgIF8sIF90aWNrZXRzLCB0ID0gcGVuZGluZwogICAg"
    "bWV0aG9kX2tleSA9IHQuZ2V0KCJwYXltZW50X21ldGhvZCIpCiAgICBsYWJlbCA9IHBheW1lbnRfbGFiZWwobWV0aG9kX2tleSwg"
    "dWlkKSBpZiBtZXRob2Rfa2V5IGVsc2UgIuKAlCIKICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KEwodWlkLCAicGF5"
    "bWVudF9rZXB0X3RleHQiLCBsYWJlbD1sYWJlbCkpCgphc3luYyBkZWYgaGFuZGxlX2NoYW5nZV9wYXltZW50KHVwZGF0ZTogVXBk"
    "YXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgIHF1ZXJ5ID0gdXBkYXRlLmNhbGxiYWNrX3F1ZXJ5"
    "CiAgICB1aWQgPSBxdWVyeS5mcm9tX3VzZXIuaWQKICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcihMKHVpZCwgInBheW1lbnRfY2hhbmdl"
    "ZF9hbGVydCIpKQogICAgcGFydHMgPSBxdWVyeS5kYXRhLnNwbGl0KCJfIikKICAgIHJvdW5kX2lkID0gaW50KHBhcnRzWzFdKQog"
    "ICAgbWV0aG9kX2tleSA9IHBhcnRzWzJdCgogICAgcGVuZGluZyA9IF9nZXRfcGVuZGluZ19zZWxlY3Rpb24odWlkKQogICAgaWYg"
    "bm90IHBlbmRpbmcgb3IgcGVuZGluZ1swXSAhPSByb3VuZF9pZDoKICAgICAgICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfdGV4"
    "dChMKHVpZCwgInRpY2tldF9ub3RfeW91cnMiKSkKICAgICAgICByZXR1cm4KICAgIF8sIHRpY2tldHMsIF8gPSBwZW5kaW5nCiAg"
    "ICBpZiBtZXRob2Rfa2V5IG5vdCBpbiBQQVlNRU5UX01FVEhPRFM6CiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3Rl"
    "eHQoTCh1aWQsICJ1bmtub3duX3BheW1lbnRfbWV0aG9kIikpCiAgICAgICAgcmV0dXJuCgogICAgZm9yIHRuIGluIHRpY2tldHM6"
    "CiAgICAgICAgdCA9IHJvdW5kc1tyb3VuZF9pZF1bInRpY2tldHMiXVt0bl0KICAgICAgICB0WyJwYXltZW50X21ldGhvZCJdID0g"
    "bWV0aG9kX2tleQogICAgICAgIHRbInJlZiJdID0gTm9uZQogICAgICAgIHRbInJlZl9hdHRlbXB0cyJdID0gMAogICAgc2F2ZV9z"
    "dGF0ZSgpCgogICAgIyDhi6jhiq3hjY3hi6sg4YiY4Yio4YyD4YqVIOGKpeGKleGLsOGMiOGKkyDhiqDhiLPhi60KICAgIG1ldGhv"
    "ZCA9IFBBWU1FTlRfTUVUSE9EU1ttZXRob2Rfa2V5XQogICAgdG90YWwgPSByb3VuZHNbcm91bmRfaWRdWyJwcmljZSJdICogbGVu"
    "KHRpY2tldHMpCiAgICByZW1haW5pbmdfbWluID0gVElDS0VUX0hPTERfTUlOVVRFUwogICAgZmlyc3RfdG4gPSB0aWNrZXRzWzBd"
    "IGlmIHRpY2tldHMgZWxzZSBOb25lCiAgICBpZiBmaXJzdF90biBpcyBub3QgTm9uZToKICAgICAgICB0ayA9IHJvdW5kc1tyb3Vu"
    "ZF9pZF1bInRpY2tldHMiXS5nZXQoZmlyc3RfdG4pCiAgICAgICAgaWYgdGsgYW5kIHRrLmdldCgiZXhwaXJlc19hdCIpOgogICAg"
    "ICAgICAgICByZW1haW5pbmdfbWluID0gbWF4KDAsIGludCgodGtbImV4cGlyZXNfYXQiXSAtIGRhdGV0aW1lLm5vdygpKS50b3Rh"
    "bF9zZWNvbmRzKCkgLy8gNjApKQogICAgY2FwdGlvbiA9IEwoCiAgICAgICAgdWlkLCAicGF5bWVudF9jYXB0aW9uX2Z1bGwiLAog"
    "ICAgICAgIGVtb2ppPW1ldGhvZFsnZW1vamknXSwgbGFiZWw9cGF5bWVudF9sYWJlbChtZXRob2Rfa2V5LCB1aWQpLAogICAgICAg"
    "IHByaWNlPXRvdGFsLCBob2xkZXI9bWV0aG9kWydob2xkZXInXSwgYWNjb3VudD1tZXRob2RbJ2FjY291bnQnXSwgcmVmX2hpbnQ9"
    "bWV0aG9kWydyZWZfaGludCddLAogICAgICAgIG1pbnM9cmVtYWluaW5nX21pbgogICAgKQogICAgbG9nb19wYXRoID0gb3MucGF0"
    "aC5qb2luKG9zLnBhdGguZGlybmFtZShvcy5wYXRoLmFic3BhdGgoX19maWxlX18pKSwgbWV0aG9kWyJsb2dvX2ZpbGUiXSkKICAg"
    "IGlmIG9zLnBhdGguaXNmaWxlKGxvZ29fcGF0aCk6CiAgICAgICAgdHJ5OgogICAgICAgICAgICB3aXRoIG9wZW4obG9nb19wYXRo"
    "LCAicmIiKSBhcyBmOgogICAgICAgICAgICAgICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9waG90byh1aWQsIHBob3RvPWYsIGNh"
    "cHRpb249Y2FwdGlvbiwgcGFyc2VfbW9kZT0iSFRNTCIpCiAgICAgICAgICAgIHJldHVybgogICAgICAgIGV4Y2VwdCBFeGNlcHRp"
    "b246CiAgICAgICAgICAgIHBhc3MKICAgIGF3YWl0IGNvbnRleHQuYm90LnNlbmRfbWVzc2FnZSh1aWQsIGNhcHRpb24sIHBhcnNl"
    "X21vZGU9IkhUTUwiKQoKYXN5bmMgZGVmIGhhbmRsZV9wYXltZW50X21ldGhvZCh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29u"
    "dGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhibDhjKvhi4vhibkg4Yuo4Yqt4Y2N4YurIOGLmOGLtCAoVGVsZUJpcnIv"
    "Q0JFQmlyci9DQkUpIOGIsuGImOGIreGMpSDhi6jhiJrhiLDhiKsgLSDhibXhi5Xhi5vhi5kg4YuN4Yi14YylIOGIi+GIieGJtSDh"
    "iYHhjKXhiK7hib0g4YiB4YiJIOGJoOGKoOGKleGLtSDhiIvhi60g4Yut4YiG4YqT4YiNIiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5j"
    "YWxsYmFja19xdWVyeQogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgIHBhcnRzID0gcXVlcnkuZGF0YS5zcGxpdCgiXyIpCiAg"
    "ICByb3VuZF9pZCA9IGludChwYXJ0c1sxXSkKICAgIG1ldGhvZF9rZXkgPSBwYXJ0c1syXQogICAgdXNlciA9IHF1ZXJ5LmZyb21f"
    "dXNlcgoKICAgIGlmIHJvdW5kX2lkIG5vdCBpbiByb3VuZHM6CiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQo"
    "TCh1c2VyLmlkLCAidGlja2V0X25vdF9mb3VuZF9yZXRyeSIpKQogICAgICAgIHJldHVybgoKICAgIHBlbmRpbmcgPSBfZ2V0X3Bl"
    "bmRpbmdfc2VsZWN0aW9uKHVzZXIuaWQpCiAgICBpZiBub3QgcGVuZGluZyBvciBwZW5kaW5nWzBdICE9IHJvdW5kX2lkOgogICAg"
    "ICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KEwodXNlci5pZCwgInRpY2tldF9ub3RfaGVsZF9vcl9leHBpcmVkIikp"
    "CiAgICAgICAgcmV0dXJuCiAgICBfLCB0aWNrZXRzLCB0ID0gcGVuZGluZwoKICAgIG1ldGhvZCA9IFBBWU1FTlRfTUVUSE9EUy5n"
    "ZXQobWV0aG9kX2tleSkKICAgIGlmIG5vdCBtZXRob2Q6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKEwodXNlci5pZCwgInVu"
    "a25vd25fcGF5bWVudF9tZXRob2QiKSwgc2hvd19hbGVydD1UcnVlKQogICAgICAgIHJldHVybgoKICAgIHByaWNlID0gcm91bmRz"
    "W3JvdW5kX2lkXVsicHJpY2UiXQogICAgdG90YWwgPSBwcmljZSAqIGxlbih0aWNrZXRzKQogICAgbnVtcyA9ICIsICIuam9pbihz"
    "dHIobikgZm9yIG4gaW4gdGlja2V0cykKICAgIGN1cnJlbnRfbWV0aG9kID0gdC5nZXQoInBheW1lbnRfbWV0aG9kIikKCiAgICAj"
    "IPCfk54g4Yuw4YuJ4YiI4YuJIOGLq+GIsuGLmSDigJQg4Ymg4YmA4Yyl4YmzIOGLqOGIteGIjeGKrSDhiJjhi7Dhi4jhi6vhi43h"
    "ipUg4Yut4Yqo4Y2N4Ymz4YiN4Y2iCiAgICBpZiBtZXRob2Rfa2V5ID09ICJtYW51YWxfY2FsbCI6CiAgICAgICAgaWYgY3VycmVu"
    "dF9tZXRob2QgYW5kIGN1cnJlbnRfbWV0aG9kICE9IG1ldGhvZF9rZXk6CiAgICAgICAgICAgIGtiID0gW1sKICAgICAgICAgICAg"
    "ICAgIElubGluZUtleWJvYXJkQnV0dG9uKEwodXNlci5pZCwgImJ0bl9zd2l0Y2hfeWVzIiksIGNhbGxiYWNrX2RhdGE9ZiJjaGFu"
    "Z2VwYXlfe3JvdW5kX2lkfV97bWV0aG9kX2tleX0iKSwKICAgICAgICAgICAgICAgIElubGluZUtleWJvYXJkQnV0dG9uKEwodXNl"
    "ci5pZCwgImJ0bl9zd2l0Y2hfbm8iKSwgY2FsbGJhY2tfZGF0YT1mImtlZXBwYXlfe3JvdW5kX2lkfSIpCiAgICAgICAgICAgIF1d"
    "CiAgICAgICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KAogICAgICAgICAgICAgICAgTCh1c2VyLmlkLCAiY29u"
    "ZmlybV9zd2l0Y2hfbWFudWFsX2NhbGwiLCBjdXJyZW50PXBheW1lbnRfbGFiZWwoY3VycmVudF9tZXRob2QsIHVzZXIuaWQpLCBu"
    "ZXc9cGF5bWVudF9sYWJlbChtZXRob2Rfa2V5LCB1c2VyLmlkKSksCiAgICAgICAgICAgICAgICByZXBseV9tYXJrdXA9SW5saW5l"
    "S2V5Ym9hcmRNYXJrdXAoa2IpCiAgICAgICAgICAgICkKICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgaWYgY3VycmVudF9tZXRo"
    "b2QgPT0gbWV0aG9kX2tleToKICAgICAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKEwodXNlci5pZCwgImFscmVhZHlfc2VsZWN0"
    "ZWRfbWV0aG9kIiksIHNob3dfYWxlcnQ9VHJ1ZSkKICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgZm9yIHRuIGluIHRpY2tldHM6"
    "CiAgICAgICAgICAgIHJvdW5kc1tyb3VuZF9pZF1bInRpY2tldHMiXVt0bl1bInBheW1lbnRfbWV0aG9kIl0gPSBtZXRob2Rfa2V5"
    "CiAgICAgICAgICAgIHJvdW5kc1tyb3VuZF9pZF1bInRpY2tldHMiXVt0bl1bInJlZl9hdHRlbXB0cyJdID0gMAogICAgICAgIHNh"
    "dmVfc3RhdGUoKQogICAgICAgIGF3YWl0IGNvbnRleHQuYm90LnNlbmRfbWVzc2FnZSgKICAgICAgICAgICAgY2hhdF9pZD1BRE1J"
    "Tl9JRCwKICAgICAgICAgICAgdGV4dD1MKHVzZXIuaWQsICJtYW51YWxfY2FsbF9hZG1pbl9ub3RpZnkiLCByaWQ9cm91bmRfaWQs"
    "IG51bXM9bnVtcywKICAgICAgICAgICAgICAgICAgIG5hbWU9dC5nZXQoJ2J1eWVyX25hbWUnLCcnKSwgcGhvbmU9dC5nZXQoJ2J1"
    "eWVyX3Bob25lJywnJyksIHRvdGFsPXRvdGFsKSwKICAgICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIKICAgICAgICApCiAgICAg"
    "ICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoCiAgICAgICAgICAgIEwodXNlci5pZCwgIm1hbnVhbF9jYWxsX2Z1bGwi"
    "LCBhY2NvdW50PSIwOTYzNTMwMDMwIiwgbnVtcz1udW1zLCBtaW5zPVRJQ0tFVF9IT0xEX01JTlVURVMpLAogICAgICAgICAgICBw"
    "YXJzZV9tb2RlPSJIVE1MIiwKICAgICAgICAgICAgcmVwbHlfbWFya3VwPUlubGluZUtleWJvYXJkTWFya3VwKFtbSW5saW5lS2V5"
    "Ym9hcmRCdXR0b24oTCh1c2VyLmlkLCAiY2FsbF9idXR0b24iLCBhY2NvdW50PSIwOTYzNTMwMDMwIiksIHVybD0idGVsOjA5NjM1"
    "MzAwMzAiKV1dKQogICAgICAgICkKICAgICAgICByZXR1cm4KCiAgICAjIOGLqOGKreGNjeGLqyDhi5jhi7Qg4Yqg4YqV4Yu0IOGK"
    "qOGJsOGImOGIqOGMoCDhi63hiYbhiIjhjYvhiI3hjaIg4YiI4YiY4YmA4Yuo4YitIOGJsOGMq+GLi+GJuSDhiaDhjI3hiI3hjL0g"
    "4Y2I4YmD4Yu1IOGImOGIteGMoOGJtSDhiqDhiIjhiaDhibXhjaIKICAgIGlmIGN1cnJlbnRfbWV0aG9kIGFuZCBjdXJyZW50X21l"
    "dGhvZCAhPSBtZXRob2Rfa2V5OgogICAgICAgIGtiID0gW1sKICAgICAgICAgICAgSW5saW5lS2V5Ym9hcmRCdXR0b24oTCh1c2Vy"
    "LmlkLCAiYnRuX3N3aXRjaF95ZXMiKSwgY2FsbGJhY2tfZGF0YT1mImNoYW5nZXBheV97cm91bmRfaWR9X3ttZXRob2Rfa2V5fSIp"
    "LAogICAgICAgICAgICBJbmxpbmVLZXlib2FyZEJ1dHRvbihMKHVzZXIuaWQsICJidG5fc3dpdGNoX25vIiksIGNhbGxiYWNrX2Rh"
    "dGE9ZiJrZWVwcGF5X3tyb3VuZF9pZH0iKQogICAgICAgIF1dCiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQo"
    "CiAgICAgICAgICAgIEwodXNlci5pZCwgImNvbmZpcm1fc3dpdGNoX21hbnVhbF9jYWxsIiwgY3VycmVudD1wYXltZW50X2xhYmVs"
    "KGN1cnJlbnRfbWV0aG9kLCB1c2VyLmlkKSwgbmV3PXBheW1lbnRfbGFiZWwobWV0aG9kX2tleSwgdXNlci5pZCkpLAogICAgICAg"
    "ICAgICByZXBseV9tYXJrdXA9SW5saW5lS2V5Ym9hcmRNYXJrdXAoa2IpCiAgICAgICAgKQogICAgICAgIHJldHVybgoKICAgIGlm"
    "IGN1cnJlbnRfbWV0aG9kID09IG1ldGhvZF9rZXk6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKEwodXNlci5pZCwgImFscmVh"
    "ZHlfc2VsZWN0ZWRfbWV0aG9kIiksIHNob3dfYWxlcnQ9VHJ1ZSkKICAgICAgICByZXR1cm4KCiAgICBmb3IgdG4gaW4gdGlja2V0"
    "czoKICAgICAgICByb3VuZHNbcm91bmRfaWRdWyJ0aWNrZXRzIl1bdG5dWyJwYXltZW50X21ldGhvZCJdID0gbWV0aG9kX2tleQog"
    "ICAgICAgIHJvdW5kc1tyb3VuZF9pZF1bInRpY2tldHMiXVt0bl1bInJlZl9hdHRlbXB0cyJdID0gMAogICAgc2F2ZV9zdGF0ZSgp"
    "CgogICAgcmVtYWluaW5nX21pbiA9IFRJQ0tFVF9IT0xEX01JTlVURVMKICAgIGlmIHQuZ2V0KCJleHBpcmVzX2F0Iik6CiAgICAg"
    "ICAgcmVtYWluaW5nX21pbiA9IG1heCgwLCBpbnQoKHRbImV4cGlyZXNfYXQiXSAtIGRhdGV0aW1lLm5vdygpKS50b3RhbF9zZWNv"
    "bmRzKCkgLy8gNjApKQogICAgY2FwdGlvbiA9IEwoCiAgICAgICAgdXNlci5pZCwgInBheW1lbnRfY2FwdGlvbl9mdWxsIiwKICAg"
    "ICAgICBlbW9qaT1tZXRob2RbJ2Vtb2ppJ10sIGxhYmVsPXBheW1lbnRfbGFiZWwobWV0aG9kX2tleSwgdXNlci5pZCksCiAgICAg"
    "ICAgcHJpY2U9dG90YWwsIGhvbGRlcj1tZXRob2RbJ2hvbGRlciddLCBhY2NvdW50PW1ldGhvZFsnYWNjb3VudCddLCByZWZfaGlu"
    "dD1tZXRob2RbJ3JlZl9oaW50J10sCiAgICAgICAgbWlucz1yZW1haW5pbmdfbWluCiAgICApCgogICAgbG9nb19wYXRoID0gb3Mu"
    "cGF0aC5qb2luKG9zLnBhdGguZGlybmFtZShvcy5wYXRoLmFic3BhdGgoX19maWxlX18pKSwgbWV0aG9kWyJsb2dvX2ZpbGUiXSkK"
    "CiAgICBpZiBvcy5wYXRoLmlzZmlsZShsb2dvX3BhdGgpOgogICAgICAgIHRyeToKICAgICAgICAgICAgd2l0aCBvcGVuKGxvZ29f"
    "cGF0aCwgInJiIikgYXMgcGhvdG9fZjoKICAgICAgICAgICAgICAgIGF3YWl0IGNvbnRleHQuYm90LnNlbmRfcGhvdG8oCiAgICAg"
    "ICAgICAgICAgICAgICAgY2hhdF9pZD11c2VyLmlkLCBwaG90bz1waG90b19mLCBjYXB0aW9uPWNhcHRpb24sIHBhcnNlX21vZGU9"
    "IkhUTUwiCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgIHJldHVybgogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAg"
    "ICAgICAgIHBhc3MKCiAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoY2hhdF9pZD11c2VyLmlkLCB0ZXh0PWNhcHRp"
    "b24sIHBhcnNlX21vZGU9IkhUTUwiKQoKYXN5bmMgZGVmIGhhbmRsZV91c2VyX3JlZih1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDog"
    "Q29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhibDhjKvhi4vhibkg4Yuo4Yiq4Y2I4Yio4YqV4Yi1IOGJgeGMpeGI"
    "rSDhiLLhiI3hiq0gLSDhibXhi5Xhi5vhi5kg4YuN4Yi14YylIOGLq+GIieGJtSDhiYHhjKXhiK7hib0g4YiB4YiJIOGJoOGKoOGK"
    "leGLtSDhiKrhjYjhiKjhipXhiLUv4Yyg4YmF4YiL4YiLIOGImOGMoOGKlSDhiIvhi60g4Ymw4YiY4Yi14Yit4Ymw4YuNIOGLreGI"
    "qOGMi+GMiOGMo+GIiSIiIgogICAgdXNlcl9pZCA9IHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZAogICAgcmVmX2lucHV0ID0g"
    "dXBkYXRlLm1lc3NhZ2UudGV4dC5zdHJpcCgpCiAgICBoYW5kbGVkID0gYXdhaXQgX2hhbmRsZV9pbmNvbWluZ19yZWYodXBkYXRl"
    "LCBjb250ZXh0LCByZWZfaW5wdXQpCiAgICBpZiBub3QgaGFuZGxlZDoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBs"
    "eV90ZXh0KEwodXNlcl9pZCwgIm5vX3NlbGVjdGlvbl95ZXQiKSkKCmFzeW5jIGRlZiBfaGFuZGxlX2luY29taW5nX3JlZih1cGRh"
    "dGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSwgcmVmX2lucHV0OiBzdHIpOgogICAgIiIi4Yib"
    "4YqV4Yqb4YuN4YidIOGIneGKleGMrSAo4Ymg4Yy94YiB4Y2NIOGLqOGJsOGIi+GKqCDhiKrhjYjhiKjhipXhiLXhjaMg4Yqo4YiZ"
    "4YiJIOGKpOGIteGKpOGIneGKpOGItSDhi6jhibDhi4jhiLDhi7DhjaMg4YuI4Yut4YidIOGKqOGIteGKreGIquGKleGIvuGJtSBP"
    "Q1Ig4Yuo4Ymw4YyI4YqYKSDhi6jhiIvhiqjhi43hipUKICAgIOGIquGNiOGIqOGKleGItSDhiYHhjKXhiK0g4Ymw4YmA4Yml4YiO"
    "IOGLqOGImuGLq+GIqOGMi+GMjeGMpSDhi6jhjIvhiKsgKHNoYXJlZCkg4Ymw4YyN4Ymj4Yit4Y2iIHBlbmRpbmcgc2VsZWN0aW9u"
    "IOGKqOGIjOGIiCBGYWxzZSDhi63hiJjhiI3hiLPhiI3hjaMKICAgIOGLreGIheGInSDhjKDhiKrhi40gKGNhbGxlcikg4Yqg4Yib"
    "4Yir4YytIOGLqOGImOGIjeGItSDhiqDhiqvhiIThi7UgKGZhbGxiYWNrKSDhiqXhipXhi7Lhi4jhiLXhi7Ug4Yur4Yi14Ym94YiI"
    "4YuL4YiN4Y2iIiIiCiAgICB1c2VyX2lkID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkCgogICAgcGVuZGluZyA9IF9nZXRf"
    "cGVuZGluZ19zZWxlY3Rpb24odXNlcl9pZCkKICAgIGlmIG5vdCBwZW5kaW5nOgogICAgICAgIHJldHVybiBGYWxzZQogICAgcm91"
    "bmRfaWQsIHRpY2tldHMsIHQgPSBwZW5kaW5nCgogICAgaWYgbm90IHQuZ2V0KCJwYXltZW50X21ldGhvZCIpOgogICAgICAgIGF3"
    "YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoTCh1c2VyX2lkLCAibm9fcGF5bWVudF9tZXRob2RfeWV0IikpCiAgICAgICAg"
    "cmV0dXJuIFRydWUKCiAgICBtZXRob2QgPSBQQVlNRU5UX01FVEhPRFMuZ2V0KHRbInBheW1lbnRfbWV0aG9kIl0pCiAgICBpZiB0"
    "LmdldCgicGF5bWVudF9tZXRob2QiKSA9PSAibWFudWFsX2NhbGwiOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5"
    "X3RleHQoTCh1c2VyX2lkLCAibWFudWFsX2NhbGxfbm9fcmVmX25lZWRlZCIpKQogICAgICAgIHJldHVybiBUcnVlCgogICAgIyAt"
    "LS0g4YiZ4Yqo4YirIOGJhuGMo+GIqiAoYXR0ZW1wdCBjb3VudGVyKSAtIOGJteGKreGKreGIjSDhi6vhiI3hiIbhipAg4YmF4Yit"
    "4Yy44Ym1IOGLiOGLreGInSDhi6vhiI3hjIjhjKPhjKDhiJgg4Yiq4Y2I4Yio4YqV4Yi1IOGIgeGIieGInSDhi63hiYbhjKDhiKvh"
    "iIkgKOGIiOGIgeGIieGInSDhiYHhjKXhiK7hib0g4Ymg4YyL4YirKSAtLS0KICAgIGF0dGVtcHRzX3VzZWQgPSB0LmdldCgicmVm"
    "X2F0dGVtcHRzIiwgMCkgKyAxCiAgICBmb3IgdG4gaW4gdGlja2V0czoKICAgICAgICByb3VuZHNbcm91bmRfaWRdWyJ0aWNrZXRz"
    "Il1bdG5dWyJyZWZfYXR0ZW1wdHMiXSA9IGF0dGVtcHRzX3VzZWQKICAgIHJlbWFpbmluZyA9IE1BWF9SRUZfQVRURU1QVFMgLSBh"
    "dHRlbXB0c191c2VkCgogICAgZGVmIF9yZWxlYXNlX29yZGVyX291dF9vZl9hdHRlbXB0cygpOgogICAgICAgIGZvciB0biBpbiB0"
    "aWNrZXRzOgogICAgICAgICAgICByb3VuZHNbcm91bmRfaWRdWyJ0aWNrZXRzIl1bdG5dID0gX2VtcHR5X3RpY2tldCgpCiAgICAg"
    "ICAgdXNlcl9zZWxlY3Rpb25zLnBvcCh1c2VyX2lkLCBOb25lKQogICAgICAgIHNhdmVfc3RhdGUoKQoKICAgICMgLS0tIOGLqOGJ"
    "heGIreGMuOGJtSDhiJvhiKjhjIvhjIjhjKsgKGZvcm1hdCB2YWxpZGF0aW9uKSAtLS0KICAgIGNsZWFuZWRfcmVmID0gcmVmX2lu"
    "cHV0LnJlcGxhY2UoIiAiLCAiIikudXBwZXIoKQoKICAgICMgLS0tIOGJsOGMq+GLi+GJuSDhipDhjKDhiIsg4Yiq4Y2I4Yio4YqV"
    "4Yi1IOGJpeGJuyDhiLPhi63hiIbhipUg4YiZ4YiJIOGLqOGJo+GKleGKrSDhiqThiLXhiqThiJ3hiqThiLUg4Yy94YiB4Y2NIOGJ"
    "ouGIiOGMpeGNjSAtIOGKqOGLjeGIteGMoSDhibXhiq3hiq3hiIjhipvhi43hipUKICAgICMg4Yiq4Y2I4Yio4YqV4Yi1IOGJgeGM"
    "peGIrSDhiaDhiKvhiLUt4Yiw4YitIOGIiOGIm+GLjeGMo+GJtSDhiqXhipXhiJ7hiq3hiKvhiIjhipUgKOGIjeGKrSDhiqThiLXh"
    "iqThiJ3hiqThiLUg4YuI4YuwIOGKoOGLteGImuGKkSDhiLLhjY7hiK3hi4vhiK3hi7Ug4Yql4YqV4Yuw4Yia4Yuw4Yio4YyI4YuN"
    "KeGNogogICAgIyDhi63hiIUg4Yuo4Yia4Yie4Yqo4Yio4YuNIOGMjeGJpeGLk+GJsSDhiKvhiLEg4Yql4YqV4YuwIOGKleGMueGI"
    "hSDhiKrhjYjhiKjhipXhiLUg4Yqr4YiN4YyI4Yyj4Yyg4YiYIOGKpeGKkyDhi6jhiaPhipXhiq0g4Yqk4Yi14Yqk4Yid4Yqk4Yi1"
    "IOGLqOGImuGImOGIteGIjSDhiqjhiIbhipAg4Yml4Ym7IOGKkOGLjeGNogogICAgaWYgbWV0aG9kIGFuZCBtZXRob2QuZ2V0KCJy"
    "ZWZfcGF0dGVybiIpIGFuZCBub3QgbWV0aG9kWyJyZWZfcGF0dGVybiJdLm1hdGNoKGNsZWFuZWRfcmVmKSBhbmQgX2xvb2tzX2xp"
    "a2VfYmFua19zbXMocmVmX2lucHV0KToKICAgICAgICBleHRyYWN0ZWRfcmVmID0gX2V4dHJhY3RfcmVmX2Zyb21fdGV4dChyZWZf"
    "aW5wdXQsIG1ldGhvZCkKICAgICAgICBpZiBleHRyYWN0ZWRfcmVmOgogICAgICAgICAgICByZWZfaW5wdXQgPSBleHRyYWN0ZWRf"
    "cmVmCiAgICAgICAgICAgIGNsZWFuZWRfcmVmID0gcmVmX2lucHV0LnJlcGxhY2UoIiAiLCAiIikudXBwZXIoKQoKICAgIGlmIG1l"
    "dGhvZCBhbmQgbm90IG1ldGhvZFsicmVmX3BhdHRlcm4iXS5tYXRjaChjbGVhbmVkX3JlZik6CiAgICAgICAgaWYgcmVtYWluaW5n"
    "IDw9IDA6CiAgICAgICAgICAgIF9yZWxlYXNlX29yZGVyX291dF9vZl9hdHRlbXB0cygpCiAgICAgICAgICAgIGF3YWl0IHVwZGF0"
    "ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAgICAgICAgICBMKHVzZXJfaWQsICJpbnZhbGlkX2Zvcm1hdF9vdXRfb2ZfYXR0"
    "ZW1wdHMiLCByZWY9cmVmX2lucHV0LCBsYWJlbD1wYXltZW50X2xhYmVsKHRbInBheW1lbnRfbWV0aG9kIl0sIHVzZXJfaWQpLCBt"
    "YXg9TUFYX1JFRl9BVFRFTVBUUyksCiAgICAgICAgICAgICAgICBwYXJzZV9tb2RlPSJIVE1MIgogICAgICAgICAgICApCiAgICAg"
    "ICAgICAgIHJldHVybiBUcnVlCiAgICAgICAgc2F2ZV9zdGF0ZSgpCiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlf"
    "dGV4dCgKICAgICAgICAgICAgTCh1c2VyX2lkLCAiaW52YWxpZF9mb3JtYXRfcmV0cnkiLCByZWY9cmVmX2lucHV0LCBsYWJlbD1w"
    "YXltZW50X2xhYmVsKHRbInBheW1lbnRfbWV0aG9kIl0sIHVzZXJfaWQpLCBoaW50PW1ldGhvZFsncmVmX2hpbnQnXSwgcmVtYWlu"
    "aW5nPXJlbWFpbmluZyksCiAgICAgICAgICAgIHBhcnNlX21vZGU9IkhUTUwiCiAgICAgICAgKQogICAgICAgIHJldHVybiBUcnVl"
    "CgogICAgbm9ybV9yZWYgPSBfbm9ybWFsaXplX3JlZihyZWZfaW5wdXQpCiAgICBpZiBub3JtX3JlZiBhbmQgbm9ybV9yZWYgaW4g"
    "dXNlZF9zbXNfcmVmczoKICAgICAgICBpZiByZW1haW5pbmcgPD0gMDoKICAgICAgICAgICAgX3JlbGVhc2Vfb3JkZXJfb3V0X29m"
    "X2F0dGVtcHRzKCkKICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgICAgIEwo"
    "dXNlcl9pZCwgImR1cF9yZWZfb3V0X29mX2F0dGVtcHRzIiwgbWF4PU1BWF9SRUZfQVRURU1QVFMpLAogICAgICAgICAgICAgICAg"
    "cGFyc2VfbW9kZT0iSFRNTCIKICAgICAgICAgICAgKQogICAgICAgICAgICByZXR1cm4gVHJ1ZQogICAgICAgIHNhdmVfc3RhdGUo"
    "KQogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAgICAgIEwodXNlcl9pZCwgImR1cF9yZWZf"
    "cmV0cnkiLCByZW1haW5pbmc9cmVtYWluaW5nKSwKICAgICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIKICAgICAgICApCiAgICAg"
    "ICAgYXdhaXQgX25vdGlmeV9kdXBsaWNhdGVfcmVmX2F0dGVtcHQoCiAgICAgICAgICAgIGNvbnRleHQuYm90LCBub3JtX3JlZiwg"
    "cmVmX2lucHV0LCBzb3VyY2U9InBsYXllci10eXBlZCIsCiAgICAgICAgICAgIGV4dHJhX3RleHQ9ZiLhi6jhiIvhiqjhi40g4Ymw"
    "4Yyr4YuL4Ym94Y2mIHtwbGF5ZXJzLmdldCh1c2VyX2lkLCB7fSkuZ2V0KCduYW1lJywgJ04vQScpfSAo4YiI4YuZ4YitIHtyb3Vu"
    "ZF9pZH0g4YmB4Yyl4Yiu4Ym9IHsnLCAnLmpvaW4oc3RyKHgpIGZvciB4IGluIHRpY2tldHMpfSkiCiAgICAgICAgKQogICAgICAg"
    "IHJldHVybiBUcnVlCgogICAgIyAtLS0g4Yqo4Yua4YiFIOGLjeGMqiDhi6vhiIjhi40g4Yiq4Y2I4Yio4YqV4Yi1IOGJteGKreGK"
    "reGIiOGKmyDhiYXhiK3hjLjhibUg4Yur4YiI4YuNIOGKpeGKkyDhi6vhiI3hibDhi7DhjIvhjIjhiJgg4Yi14YiI4YiG4YqQIC0g"
    "4YiIIHJlY2VpcHQgYXJjaGl2ZSDhiqXhipXhi7LhiKjhi7Mg4Yqg4YiB4YqR4YqRIOGKpeGKk+GIteGJgOGIneGMoOGLi+GIiOGK"
    "lQogICAgcmVjZWlwdF9jb250ZXh0c1t1c2VyX2lkXSA9IHsicm91bmRfaWQiOiByb3VuZF9pZCwgInRpY2tldHMiOiB0aWNrZXRz"
    "LCAicmVmIjogcmVmX2lucHV0fQogICAgc2F2ZV9zdGF0ZSgpCgogICAgIyAtLS0g4Ymw4Yyr4YuL4Ym5IOGLqOGIi+GKqOGLjeGK"
    "lSDhiqbhiKrhjIXhipPhiI0g4YiY4YiN4YuV4Yqt4Ym1ICjhiKrhjYjhiKjhipXhiLUv4Yqk4Yi14Yqk4Yid4Yqk4Yi1IOGMveGI"
    "geGNjSkg4YuI4Yuy4Yur4YuN4YqRIOGKpeGKk+GMoOGNi+GLi+GIiOGKlSAtIOGIquGNiOGIqOGKleGItSDhiYHhjKXhiKkKICAg"
    "ICMg4Yir4YixIOGKqOGIi+GLrSDhi4jhi7AgcmVjZWlwdF9jb250ZXh0cy90aWNrZXRbInJlZiJdIOGIteGIiOGJsOGJgOGImOGM"
    "oCDhiJ3hipXhiJ0g4YiY4Yio4YyDIOGKoOGLreGMoOGNi+GIneGNoyDhi43hjKThibEgKFZFUklGSUVEL1JFSkVDVEVEKQogICAg"
    "IyDhiJ3hipXhiJ0g4Yut4YiB4YqVIOGIneGKlSDhi63hiIUg4Yut4Yiw4Yio4Yub4YiNIC0g4Yi14YixIOGLqOGKreGNjeGLqyDh"
    "jL3hiIHhjY0g4Ymg4Ym74YmxIOGLjeGIteGMpSDhiqXhipXhi7Phi63hiYDhiJjhjKUg4YiI4Yib4Yu14Yio4YyN4Y2iCiAgICB0"
    "cnk6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UuZGVsZXRlKCkKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcGFz"
    "cwoKICAgICMgLS0tIOGIm+GIqOGMi+GMiOGMq+GNpiBWRVJJRklFRCDhi4jhi63hiJ0gUkVKRUNURUQg4Yml4Ym7ICjhi4jhi7Dh"
    "jYrhibUg4Yqk4Yi14Yqk4Yid4Yqk4Yi1IOGKpeGIteGKquGImOGMoyDhi7XhiKjhiLUg4YiY4Yyg4Ymg4YmFIOGJsOGJi+GIreGM"
    "p+GIjSkgLS0tCiAgICAjIOGJsOGMq+GLi+GJuSDhi6jhiIvhiqjhi40g4Yiq4Y2I4Yio4YqV4Yi1IOGJgeGMpeGIrSDhiYDhi7Xh"
    "iJ4g4YuI4YuwIOGJpuGJsSDhiqjhi7DhiKjhiLAg4Yur4YiN4Ymw4YyI4Yyj4Yyg4YiYICh1bm1hdGNoZWQpIOGLqOGJo+GKleGK"
    "rSDhiqThiLXhiqThiJ3hiqThiLUg4YyL4YitIOGLiOGLsuGLq+GLjeGKkSDhiqXhipPhiJjhiLPhiq3hiKvhiIjhipUKICAgICMg"
    "KOGLqOGJsOGIi+GKqOGLjSBTTVMg4YiY4Yyg4YqVIOGKqOGJteGLleGLm+GLmSDhjKDhiYXhiIvhiIsg4Yu14Yid4YitIOGMi+GI"
    "rSDhiJjhjIjhjKPhjKDhiJ0g4Yqg4YiI4Ymg4Ym1KeGNogogICAgIyDhibXhiq3hiq3hiIjhipsg4YyN4Yyl4Yia4YurIOGKq+GI"
    "iCA9IFZFUklGSUVEICjhibXhi5Xhi5vhi5kg4YuN4Yi14YylIOGLq+GIieGJtSDhiYHhjKXhiK7hib0g4YiB4YiJIOGJoOGKoOGK"
    "leGLtSDhiIvhi60g4YuI4Yuy4Yur4YuN4YqRIOGLreGMuOGLteGJg+GIiSnhjaIKICAgICMg4YyN4Yyl4Yia4YurIOGKqOGIjOGI"
    "iCAo4Yib4YiI4Ym14YidIOGLqyDhiKrhjYjhiKjhipXhiLUg4Yur4YiI4YuNIOGKpOGIteGKpOGIneGKpOGItSDhjIjhipMg4YuI"
    "4YuwIOGJpuGJsSDhiqvhiI3hi7DhiKjhiLApID0gUkVKRUNURUQgLSDhi4jhi7DhjYrhibUg4Yqk4Yi14Yqk4Yid4Yqk4Yi1CiAg"
    "ICAjIOGKpeGIteGKquGImOGMoyDhi7XhiKjhiLUg4Yyo4Yit4Yi2IOGKoOGKleGMoOGJpeGJheGIneGNpCDhi63hiIUg4YiZ4Yqo"
    "4YirIOGKpeGKleGLsCDhi4jhi7DhiYAg4Ymw4YmG4Yyl4YiuIOGLqOGJgOGIqOGLjeGKlSDhiJnhiqjhiKsg4Ymw4Yyg4YmF4Yie"
    "IOGJsOGMq+GLi+GJuSDhiaDhi7XhjIvhiJog4YiY4YiL4YqtIOGLreGJveGIi+GIjeGNogogICAgYWxyZWFkeV9hcHByb3ZlZCA9"
    "IGF3YWl0IGNoZWNrX3VubWF0Y2hlZF9zbXNfZm9yX29yZGVyKHJvdW5kX2lkLCB0aWNrZXRzLCByZWZfaW5wdXQsIGNvbnRleHQu"
    "Ym90KQogICAgaWYgYWxyZWFkeV9hcHByb3ZlZDoKICAgICAgICByZXR1cm4gVHJ1ZQoKICAgICMgLS0tIFZFUklGSUVEIOGLq+GI"
    "jeGJsOGJo+GIiCAobWF0Y2hpbmcgU01TIOGMiOGKkyDhiLXhiIvhiI3hi7DhiKjhiLApID0+IFJFSkVDVEVEIC0tLQogICAgZm9y"
    "IHRuIGluIHRpY2tldHM6CiAgICAgICAgcm91bmRzW3JvdW5kX2lkXVsidGlja2V0cyJdW3RuXVsicmVmIl0gPSBOb25lICAjIOGL"
    "qOGLiOGLsOGJgCDhiJnhiqjhiKsg4YuI4Yuw4Y2K4Ym1IOGJoOGIq+GItS3hiLDhiK0g4Yql4YqV4Yuz4Yut4YyI4Yyj4Yyg4Yid"
    "IOGIm+GMveGLs+GJtQoKICAgIGlmIHRbImV4cGlyZXNfYXQiXSBhbmQgdFsiZXhwaXJlc19hdCJdIDwgZGF0ZXRpbWUubm93KCk6"
    "CiAgICAgICAgYXdhaXQgcmVsZWFzZV90aWNrZXRfZ3JvdXAocm91bmRfaWQsIHRpY2tldHMsIGNvbnRleHQuYm90LCBub3RpZnk9"
    "VHJ1ZSkKICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoCiAgICAgICAgICAgIGNoYXRfaWQ9dXNlcl9pZCwK"
    "ICAgICAgICAgICAgdGV4dD1MKHVzZXJfaWQsICJob2xkX2V4cGlyZWQiLCBtaW5zPVRJQ0tFVF9IT0xEX01JTlVURVMpLAogICAg"
    "ICAgICAgICByZXBseV9tYXJrdXA9cGxheWVyX2tleWJvYXJkKGdldF9sYW5nKHVzZXJfaWQpKQogICAgICAgICkKICAgICAgICBy"
    "ZXR1cm4gVHJ1ZQoKICAgIGlmIHJlbWFpbmluZyA8PSAwOgogICAgICAgIGF3YWl0IHJlbGVhc2VfdGlja2V0X2dyb3VwKHJvdW5k"
    "X2lkLCB0aWNrZXRzLCBjb250ZXh0LmJvdCwgbm90aWZ5PVRydWUpCiAgICAgICAgcmVjZWlwdF9jb250ZXh0cy5wb3AodXNlcl9p"
    "ZCwgTm9uZSkKICAgICAgICBzYXZlX3N0YXRlKCkKICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoCiAgICAg"
    "ICAgICAgIGNoYXRfaWQ9dXNlcl9pZCwKICAgICAgICAgICAgdGV4dD1MKHVzZXJfaWQsICJyZWplY3RlZF9vdXRfb2ZfYXR0ZW1w"
    "dHMiKSwKICAgICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIsCiAgICAgICAgICAgIHJlcGx5X21hcmt1cD1wbGF5ZXJfa2V5Ym9h"
    "cmQoZ2V0X2xhbmcodXNlcl9pZCkpCiAgICAgICAgKQogICAgICAgIHJldHVybiBUcnVlCgogICAgcmVjZWlwdF9jb250ZXh0cy5w"
    "b3AodXNlcl9pZCwgTm9uZSkKICAgIGtleSA9IF9yZWplY3RlZF9yZWZfa2V5KHJvdW5kX2lkLCB0aWNrZXRzKQogICAgcmVqZWN0"
    "ZWRfcmVmc1trZXldID0gewogICAgICAgICJyb3VuZF9pZCI6IHJvdW5kX2lkLAogICAgICAgICJ0aWNrZXRzIjogbGlzdCh0aWNr"
    "ZXRzKSwKICAgICAgICAidXNlcl9pZCI6IHVzZXJfaWQsCiAgICAgICAgInJlZiI6IHJlZl9pbnB1dCwKICAgICAgICAicmVqZWN0"
    "ZWRfYXQiOiBkYXRldGltZS5ub3coKSwKICAgIH0KICAgIHNhdmVfc3RhdGUoKQogICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9t"
    "ZXNzYWdlKAogICAgICAgIGNoYXRfaWQ9dXNlcl9pZCwKICAgICAgICB0ZXh0PUwodXNlcl9pZCwgInJlamVjdGVkX3JldHJ5Iiwg"
    "cmVtYWluaW5nPXJlbWFpbmluZyksCiAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIsCiAgICAgICAgcmVwbHlfbWFya3VwPXBsYXll"
    "cl9rZXlib2FyZChnZXRfbGFuZyh1c2VyX2lkKSkKICAgICkKICAgICMg4YuI4Yuy4Yur4YuN4YqRIOGIiCBBRE1JTl9JRCAo4YiG"
    "4Yi14YmxKSDhiJvhiLPhi4jhiYLhi6sg4Yql4YqV4YiN4Yqr4YiI4YqVIC0gwqvinIUg4Yqg4Yy94Yu14YmFwrsvwqvinYwg4YuN"
    "4Yu14YmFwrsg4YmB4YiN4Y2O4Ym9IOGMi+GIrSAtIOGIteGIiOGLmuGIhQogICAgIyDhiIbhiLXhibEgL3JlamVjdGVkcmVmcyDh"
    "iaXhiIjhi40g4YqV4YmBIOGIhuGKkOGLjSDhiJjhjYjhiIjhjI0g4Yiz4Yur4Yi14Y2I4YiN4YyL4Ym44YuNIOGLiOGLsuGLq+GL"
    "jeGKkSDhi43hiLPhipQg4YiY4Yi14Yyg4Ym1IOGLreGJveGIi+GIieGNogogICAgIyAo4Yut4YiFIGVudHJ5IOGKoOGIgeGKleGI"
    "nSByZWplY3RlZF9yZWZzLyAvcmVqZWN0ZWRyZWZzIOGLjeGIteGMpSDhi63hiYDhiKvhiI0gLSDhiJvhiLPhi4jhiYLhi6vhi40g"
    "4Ymw4Yyo4Yib4YiqIOGKpeGKleGMgiDhiJ3hibXhiq0g4Yqg4Yut4Yuw4YiI4Yid4Y2iKQogICAgYXdhaXQgX25vdGlmeV9hZG1p"
    "bl9yZWplY3RlZF9yZWYoY29udGV4dC5ib3QsIGtleSwgcmVqZWN0ZWRfcmVmc1trZXldKQogICAgcmV0dXJuIFRydWUKCmFzeW5j"
    "IGRlZiBoYW5kbGVfcmVjZWlwdF9waG90byh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQ"
    "RSk6CiAgICAiIiLhibDhjKvhi4vhibkg4Yi14Yqt4Yiq4YqV4Yi+4Ym1ICjhi6jhi7DhiKjhiLDhip0v4Yqt4Y2N4YurIOGIm+GI"
    "qOGMi+GMiOGMqyDhiJ3hiLXhiI0pIOGIsuGIjeGKrSDhi63hiIUg4Yut4Yiw4Yir4YiN4Y2iCiAgICDhjIjhipMg4Yur4YiN4Yy4"
    "4Yuw4YmAL+GLq+GIjeGJsOGIs+GKqyDhibXhi5Xhi5vhi50g4Yqr4YiI4YuNICjhiJvhiIjhibXhiJ0g4YyI4YqTIOGIquGNiOGI"
    "qOGKleGItSDhiLLhjKDhiaDhiYUpIC0g4Ymm4YmxIOGIq+GIsSDhiqjhiJ3hiLXhiIkg4YiL4YutIE9DUiDhibDhjKDhiYXhiJ4K"
    "ICAgIOGIquGNiOGIqOGKleGItSDhiYHhjKXhiKnhipUg4YiI4Yib4YqV4Ymg4YmlIOGLreGInuGKreGIq+GIjSDhiqXhipMg4Yql"
    "4YqV4YuwIOGJsOGIiOGImOGLsOGLjSAo4Ymg4Yy94YiB4Y2NIOGKpeGKleGLsOGJsOGIi+GKqCDhiKrhjYjhiKjhipXhiLUpIOGJ"
    "oOGIq+GItS3hiLDhiK0g4Yur4Yio4YyL4YyN4Yyj4YiNL+GLreGKreGLs+GIjeGNogogICAg4Yqr4YiN4Ymw4YyI4YqYIOGLiOGL"
    "reGInSDhibXhi5Xhi5vhi5kg4YmA4Yuw4YidIOGJpeGIjiDhi6jhibDhjKDhipPhiYDhiYAg4Yqo4YiG4YqQIC0g4Yuo4YmG4Yuo"
    "4YuN4YqVIOGLqOGLsOGIqOGIsOGKnS3hiJjhi53hjIjhiaUgKGFyY2hpdmUpIOGKoOGKq+GIhOGLtSDhi63hiqjhibDhiIvhiI3h"
    "jaIiIiIKICAgIHVzZXJfaWQgPSB1cGRhdGUubWVzc2FnZS5mcm9tX3VzZXIuaWQKICAgIGlmIHVzZXJfaWQgPT0gQURNSU5fSUQ6"
    "CiAgICAgICAgcmV0dXJuCgogICAgcGVuZGluZyA9IF9nZXRfcGVuZGluZ19zZWxlY3Rpb24odXNlcl9pZCkKICAgIGlmIHBlbmRp"
    "bmc6CiAgICAgICAgcm91bmRfaWQsIHRpY2tldHMsIHQgPSBwZW5kaW5nCiAgICAgICAgbWV0aG9kX2tleSA9IHQuZ2V0KCJwYXlt"
    "ZW50X21ldGhvZCIpCiAgICAgICAgaWYgbWV0aG9kX2tleSBpbiAoInRlbGViaXJyIiwgImNiZWJpcnIiKToKICAgICAgICAgICAg"
    "bWV0aG9kID0gUEFZTUVOVF9NRVRIT0RTLmdldChtZXRob2Rfa2V5KQogICAgICAgICAgICBwaG90byA9IHVwZGF0ZS5tZXNzYWdl"
    "LnBob3RvWy0xXQoKICAgICAgICAgICAgb2NyX3RleHQgPSAiIgogICAgICAgICAgICB0cnk6CiAgICAgICAgICAgICAgICB0Z19m"
    "aWxlID0gYXdhaXQgY29udGV4dC5ib3QuZ2V0X2ZpbGUocGhvdG8uZmlsZV9pZCkKICAgICAgICAgICAgICAgIGltYWdlX2J5dGVz"
    "ID0gYnl0ZXMoYXdhaXQgdGdfZmlsZS5kb3dubG9hZF9hc19ieXRlYXJyYXkoKSkKICAgICAgICAgICAgICAgIG9jcl90ZXh0ID0g"
    "YXdhaXQgX29jcl9leHRyYWN0X3RleHRfYXN5bmMoaW1hZ2VfYnl0ZXMpCiAgICAgICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAg"
    "ICAgICAgICAgICAgICBvY3JfdGV4dCA9ICIiCgogICAgICAgICAgICBleHRyYWN0ZWRfcmVmID0gX2V4dHJhY3RfcmVmX2Zyb21f"
    "dGV4dChvY3JfdGV4dCwgbWV0aG9kKQoKICAgICAgICAgICAgIyDhiLXhiq3hiKrhipXhiL7hibHhipUg4YuN4Yyk4YmxIOGIneGK"
    "leGInSDhi63hiIHhipUg4Yid4YqVIOGIiOGKoOGLteGImuGKkSAo4Yib4YiF4Yuw4YitL+GIm+GIqOGMi+GMiOGMqyDhiqXhipXh"
    "i7LhipbhiKjhi40pIOGLiOGLsCDhiovhiIsg4Yql4YqT4Yi14Ymw4YiL4YiN4Y2L4YiI4YqVCiAgICAgICAgICAgIG51bXMgPSAi"
    "LCAiLmpvaW4oc3RyKHgpIGZvciB4IGluIHRpY2tldHMpCiAgICAgICAgICAgIHBsYXllciA9IHBsYXllcnMuZ2V0KHVzZXJfaWQs"
    "IHt9KQogICAgICAgICAgICBjYXB0aW9uID0gKAogICAgICAgICAgICAgICAgZiLwn6e+IDxiPuGLqOGKreGNjeGLqyDhiJvhiKjh"
    "jIvhjIjhjKsgU2NyZWVuc2hvdDwvYj5cblxuIgogICAgICAgICAgICAgICAgZiLwn5GkIHtwbGF5ZXIuZ2V0KCduYW1lJywnTi9B"
    "Jyl9XG4iCiAgICAgICAgICAgICAgICBmIvCfk7Ege3BsYXllci5nZXQoJ3Bob25lJywnTi9BJyl9XG4iCiAgICAgICAgICAgICAg"
    "ICBmIvCfjrIge3JvdW5kX2xhYmVsKHJvdW5kX2lkKX1cbiIKICAgICAgICAgICAgICAgIGYi8J+UoiDhiYHhjKXhiK0o4YuO4Ym9"
    "KSB7bnVtc31cbiIKICAgICAgICAgICAgICAgIGYi8J+SsyB7bWV0aG9kLmdldCgnbGFiZWwnLCdOL0EnKSBpZiBtZXRob2QgZWxz"
    "ZSAnTi9BJ31cbiIKICAgICAgICAgICAgICAgIGYi8J+TnSBPQ1Ig4Yur4YyI4YqY4YuNIOGIquGNiOGIqOGKleGIteGNpiA8Y29k"
    "ZT57ZXh0cmFjdGVkX3JlZiBvciAn4Yqg4YiN4Ymw4YyI4YqY4YidJ308L2NvZGU+XG4iCiAgICAgICAgICAgICAgICBmIvCfhpQg"
    "VXNlciBJRDoge3VzZXJfaWR9XG5cbiIKICAgICAgICAgICAgICAgIGYi4pyFIOGIiOGIm+GMveGLsOGJhSAo4YiB4YiJ4YqV4Yid"
    "IOGJgeGMpeGIruGJvSDhiaDhiqDhipXhi7Ug4YyK4YucKeGNplxuPGNvZGU+L2FwcHJvdmVfIiArICJfIi5qb2luKFtzdHIocm91"
    "bmRfaWQpXSArIFtzdHIoeCkgZm9yIHggaW4gdGlja2V0c10pICsgIjwvY29kZT5cbiIKICAgICAgICAgICAgICAgIGYi4p2MIOGL"
    "jeGLteGJhSDhiIjhiJvhi7XhiKjhjI0gKOGIneGKreGKleGLq+GJtSDhiJvhiqjhiI0g4Yut4Ym74YiL4YiNKeGNplxuPGNvZGU+"
    "L3JlamVjdF8iICsgIl8iLmpvaW4oW3N0cihyb3VuZF9pZCldICsgW3N0cih4KSBmb3IgeCBpbiB0aWNrZXRzXSkgKyAiIDzhiJ3h"
    "iq3hipXhi6vhibU+PC9jb2RlPiIKICAgICAgICAgICAgKQogICAgICAgICAgICB0cnk6CiAgICAgICAgICAgICAgICBhd2FpdCBj"
    "b250ZXh0LmJvdC5zZW5kX3Bob3RvKGNoYXRfaWQ9QURNSU5fSUQsIHBob3RvPXBob3RvLmZpbGVfaWQsIGNhcHRpb249Y2FwdGlv"
    "biwgcGFyc2VfbW9kZT0iSFRNTCIpCiAgICAgICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgICAgICBwYXNzCiAg"
    "ICAgICAgICAgIGZvciB0biBpbiB0aWNrZXRzOgogICAgICAgICAgICAgICAgdGsgPSByb3VuZHMuZ2V0KHJvdW5kX2lkLCB7fSku"
    "Z2V0KCJ0aWNrZXRzIiwge30pLmdldCh0bikKICAgICAgICAgICAgICAgIGlmIHRrIGlzIG5vdCBOb25lOgogICAgICAgICAgICAg"
    "ICAgICAgIHRrWyJyZWNlaXB0X2ZpbGVfaWQiXSA9IHBob3RvLmZpbGVfaWQKICAgICAgICAgICAgICAgICAgICB0a1sicmVjZWlw"
    "dF9yZWNlaXZlZCJdID0gVHJ1ZQogICAgICAgICAgICBzYXZlX3N0YXRlKCkKCiAgICAgICAgICAgIGlmIGV4dHJhY3RlZF9yZWY6"
    "CiAgICAgICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICAgICAgICAgIEwodXNl"
    "cl9pZCwgInNjcmVlbnNob3RfcmVmX2V4dHJhY3RlZCIsIHJlZj1leHRyYWN0ZWRfcmVmKSwgcGFyc2VfbW9kZT0iSFRNTCIKICAg"
    "ICAgICAgICAgICAgICkKICAgICAgICAgICAgICAgIGF3YWl0IF9oYW5kbGVfaW5jb21pbmdfcmVmKHVwZGF0ZSwgY29udGV4dCwg"
    "ZXh0cmFjdGVkX3JlZikKICAgICAgICAgICAgZWxzZToKICAgICAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5"
    "X3RleHQoCiAgICAgICAgICAgICAgICAgICAgTCh1c2VyX2lkLCAic2NyZWVuc2hvdF9yZWZfbm90X2ZvdW5kIiksIHBhcnNlX21v"
    "ZGU9IkhUTUwiLAogICAgICAgICAgICAgICAgICAgIHJlcGx5X21hcmt1cD1wbGF5ZXJfa2V5Ym9hcmQoZ2V0X2xhbmcodXNlcl9p"
    "ZCkpCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgIHJldHVybgoKICAgICMgLS0tIOGNjuGIjeGJo+GKreGNpiDhibXhi5Xh"
    "i5vhi5kg4YmA4Yuw4YidIOGJpeGIjiDhiLXhiIjhibDhiLXhibDhipPhjIjhi7Ag4YuI4Yut4YidIG1hbnVhbF9jYWxsIOGIteGI"
    "iOGIhuGKkCAtIOGLqOGJhuGLqOGLjQogICAgIyDhi6jhi7DhiKjhiLDhip0t4YiY4Yud4YyI4YmlIChhcmNoaXZlKSDhiqDhiqvh"
    "iIThi7Ug4Yut4Yqo4Ymw4YiL4YiNICjhiqvhiIggcmVjZWlwdF9jb250ZXh0cyDhiaXhibspIC0tLQogICAgYXdhaXQgX2FyY2hp"
    "dmVfcmVjZWlwdF9waG90byh1cGRhdGUsIGNvbnRleHQpCgphc3luYyBkZWYgX2FyY2hpdmVfcmVjZWlwdF9waG90byh1cGRhdGU6"
    "IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiJSZWZlcmVuY2UgSUQg4Yqo4Ymw4YiL"
    "4YqoIOGJoOGKi+GIiyDhi4jhi63hiJ0g4Ym14YuV4Yub4YuZIOGKqOGJsOGMoOGKk+GJgOGJgCDhiaDhiovhiIsg4YiI4YiY4Yud"
    "4YyI4YmlIOGJpeGJuyDhi6jhiJrhiIvhiq0g4Yuo4Yuw4Yio4Yiw4YqdIHNjcmVlbnNob3Qg4Yut4YmA4Ymg4YiL4YiN4Y2iIiIi"
    "CiAgICB1c2VyX2lkID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkCiAgICBjdHggPSByZWNlaXB0X2NvbnRleHRzLmdldCh1"
    "c2VyX2lkKQogICAgaWYgbm90IGN0eDoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KEwodXNlcl9pZCwg"
    "Im5vX3B1cmNoYXNlX2luX3Byb2dyZXNzIiksIHJlcGx5X21hcmt1cD1wbGF5ZXJfa2V5Ym9hcmQoZ2V0X2xhbmcodXNlcl9pZCkp"
    "KQogICAgICAgIHJldHVybgogICAgcmlkID0gY3R4WyJyb3VuZF9pZCJdCiAgICBjdHhfdGlja2V0cyA9IGN0eC5nZXQoInRpY2tl"
    "dHMiKSBvciAoW2N0eFsidGlja2V0X251bSJdXSBpZiAidGlja2V0X251bSIgaW4gY3R4IGVsc2UgW10pCiAgICByZWYgPSBjdHgu"
    "Z2V0KCJyZWYiLCAiTi9BIikKICAgIHBob3RvID0gdXBkYXRlLm1lc3NhZ2UucGhvdG9bLTFdCiAgICBudW1zID0gIiwgIi5qb2lu"
    "KHN0cih4KSBmb3IgeCBpbiBjdHhfdGlja2V0cykgaWYgY3R4X3RpY2tldHMgZWxzZSAiTi9BIgogICAgZmlyc3RfdCA9IHJvdW5k"
    "cy5nZXQocmlkLCB7fSkuZ2V0KCJ0aWNrZXRzIiwge30pLmdldChjdHhfdGlja2V0c1swXSkgaWYgY3R4X3RpY2tldHMgZWxzZSBO"
    "b25lCiAgICBwbGF5ZXIgPSBwbGF5ZXJzLmdldCh1c2VyX2lkLCB7fSkKICAgIGNhcHRpb24gPSAoCiAgICAgICAgZiLwn6e+IDxi"
    "PuGLqOGKreGNjeGLqyDhi7DhiKjhiLDhip0gU2NyZWVuc2hvdDwvYj5cblxuIgogICAgICAgIGYi8J+RpCB7cGxheWVyLmdldCgn"
    "bmFtZScsJ04vQScpfVxuIgogICAgICAgIGYi8J+TsSB7cGxheWVyLmdldCgncGhvbmUnLCdOL0EnKX1cbiIKICAgICAgICBmIvCf"
    "jrIge3JvdW5kX2xhYmVsKHJpZCl9XG4iCiAgICAgICAgZiLwn5SiIOGJgeGMpeGIrSjhi47hib0pIHtudW1zfVxuIgogICAgICAg"
    "IGYi8J+SsyB7UEFZTUVOVF9NRVRIT0RTLmdldCgoZmlyc3RfdCBvciB7fSkuZ2V0KCdwYXltZW50X21ldGhvZCcpLHt9KS5nZXQo"
    "J2xhYmVsJywnTi9BJyl9XG4iCiAgICAgICAgZiLwn5OdIFJlZmVyZW5jZTogPGNvZGU+e3JlZn08L2NvZGU+XG4iCiAgICAgICAg"
    "ZiLwn4aUIFVzZXIgSUQ6IHt1c2VyX2lkfVxuXG4iCiAgICAgICAgKyAoCiAgICAgICAgICAgIGYi4pyFIOGIiOGIm+GMveGLsOGJ"
    "hSAo4YiB4YiJ4YqV4YidIOGJgeGMpeGIruGJvSDhiaDhiqDhipXhi7Ug4YyK4YucKeGNplxuPGNvZGU+L2FwcHJvdmVfIiArICJf"
    "Ii5qb2luKFtzdHIocmlkKV0gKyBbc3RyKHgpIGZvciB4IGluIGN0eF90aWNrZXRzXSkgKyAiPC9jb2RlPlxuIgogICAgICAgICAg"
    "ICBmIuKdjCDhi43hi7XhiYUg4YiI4Yib4Yu14Yio4YyNICjhiJ3hiq3hipXhi6vhibUg4Yib4Yqo4YiNIOGLreGJu+GIi+GIjSnh"
    "jaZcbjxjb2RlPi9yZWplY3RfIiArICJfIi5qb2luKFtzdHIocmlkKV0gKyBbc3RyKHgpIGZvciB4IGluIGN0eF90aWNrZXRzXSkg"
    "KyAiIDzhiJ3hiq3hipXhi6vhibU+PC9jb2RlPiIKICAgICAgICAgICAgaWYgY3R4X3RpY2tldHMgZWxzZSAiIgogICAgICAgICkK"
    "ICAgICkKICAgIHRyeToKICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX3Bob3RvKGNoYXRfaWQ9QURNSU5fSUQsIHBob3Rv"
    "PXBob3RvLmZpbGVfaWQsIGNhcHRpb249Y2FwdGlvbiwgcGFyc2VfbW9kZT0iSFRNTCIpCiAgICBleGNlcHQgRXhjZXB0aW9uOgog"
    "ICAgICAgIHBhc3MKICAgIGZvciB0biBpbiBjdHhfdGlja2V0czoKICAgICAgICB0ID0gcm91bmRzLmdldChyaWQsIHt9KS5nZXQo"
    "InRpY2tldHMiLCB7fSkuZ2V0KHRuKQogICAgICAgIGlmIHQgaXMgbm90IE5vbmU6CiAgICAgICAgICAgIHRbInJlY2VpcHRfZmls"
    "ZV9pZCJdID0gcGhvdG8uZmlsZV9pZAogICAgICAgICAgICB0WyJyZWNlaXB0X3JlY2VpdmVkIl0gPSBUcnVlCiAgICBpZiBjdHhf"
    "dGlja2V0czoKICAgICAgICBzYXZlX3N0YXRlKCkKICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAg"
    "TCh1c2VyX2lkLCAicmVjZWlwdF9yZWNlaXZlZCIpLAogICAgICAgIHJlcGx5X21hcmt1cD1wbGF5ZXJfa2V5Ym9hcmQoZ2V0X2xh"
    "bmcodXNlcl9pZCkpCiAgICApCgphc3luYyBkZWYgaGFuZGxlX2FkbWluX3Ntc19vcl9jb21tYW5kcyh1cGRhdGU6IFVwZGF0ZSwg"
    "Y29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhiqjhiqDhi7XhiJrhipEg4Yuo4Yia4YiY4YyhIOGL"
    "qOGJo+GKleGKrSDhiqThiLXhiqThiJ3hiqThiLbhib3hipUg4Yql4YqTIOGLqOGIm+GMveGLsOGJguGLqyDhibXhi5Xhi5vhi57h"
    "ib3hipUg4Ymg4Yir4Yi1LeGIsOGIrSDhiJvhjKPhiKrhi6siIiIKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAh"
    "PSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAgICB0ZXh0ID0gdXBkYXRlLm1lc3NhZ2UudGV4dAoKICAgICMgLS0tIOGJheGL"
    "teGImuGLqyDhiJvhjKPhiKrhi6vhjaYg4Yut4YiFIOGLqOGJsOGIi+GKqOGLjSDhjL3hiIHhjY0g4Yuo4Ymj4YqV4YqtIOGKpOGI"
    "teGKpOGIneGKpOGItSAoZm9yd2FyZGVkIFNNUykg4Yqo4YiG4YqQ4Y2jIOGJoOGIm+GKleGKm+GLjeGInSDhipXhiYEKICAgICMg"
    "4YuK4Yub4Yit4Yu1IOGIguGLsOGJtSDhi43hiLXhjKUg4Ymi4YiG4YqR4YidIChuZXdyb3VuZC9tYW51YWxzZWxsL2Jyb2FkY2Fz"
    "dCkg4YuI4Yuy4Yur4YuN4YqRIOGLiOGLsCBhdXRvLXZlcmlmeSDhiaXhibsKICAgICMg4YiL4YqtIOGKpeGKkyDhibDhiJjhiIjh"
    "iLUgLSDhi63hiIUg4Yuo4Ymj4YqV4YqtIOGKpOGIteGKpOGIneGKpOGItSDhi6jhi6vhi5nhibXhipUg4Ym14YuV4Yub4YudL+GL"
    "iuGLm+GIreGLtSDhiqXhipXhi7Phi6vhiYvhiK3hjKUg4Yut4Yqo4YiL4Yqo4YiL4YiN4Y2jIOGIneGKreGKleGLq+GJseGInQog"
    "ICAgIyDhjY7hiK3hi4vhiK3hi7Ug4Yuo4Yia4Yuw4Yio4YyI4YuNIOGKpOGIteGKpOGIneGKpOGItSDhiqjhiqXhiK3hiLXhi47h"
    "i40g4Ymw4YiY4Yiz4Yiz4YutIOGKoOGKq+GLjeGKleGJtSDhiLXhiIjhiJrhiJjhjKMg4YmgIHNlbmRlciBJRCDhiaXhibsg4YiY"
    "4YiI4Yuo4Ym1IOGKoOGLreGJu+GIjeGIneGNogogICAgaWYgdGV4dCBhbmQgbm90IHRleHQuc3RhcnRzd2l0aCgiLyIpIGFuZCBf"
    "bG9va3NfbGlrZV9iYW5rX3Ntcyh0ZXh0KToKICAgICAgICAjIOKelSAvYWRkY3JlZGl0IOGLiuGLm+GIreGLtSDhiaDCq3JlZmVy"
    "ZW5jZcK7IOGLsOGIqOGMgyDhiIvhi60g4Yqo4YiG4YqQIOGKpeGKkyDhiqDhi7XhiJrhipEg4Yir4Yix4YqVIOGKpOGIteGKpOGI"
    "neGKpOGIseGKlSDhjY7hiK3hi4vhiK3hi7Ug4Yqr4Yuw4Yio4YyICiAgICAgICAgIyAo4Yiq4Y2I4Yio4YqV4Yix4YqVIOGIiOGJ"
    "peGJu+GLjSDhiLPhi63hibDhi63hiaUpIC0g4Yut4YiFIOGKpOGIteGKpOGIneGKpOGItSDhiKvhiLEg4Yib4Yio4YyL4YyI4Yyr"
    "IOGIteGIiOGIhuGKkCDhiaDhi5rhiIUg4YuN4Yi14YylIOGLq+GIieGJteGKlSDhiIHhiInhipXhiJ0KICAgICAgICAjIOGKpeGM"
    "qSDhiKrhjYjhiKjhipXhiLbhib0g4Yqo4YuK4Yub4Yit4YuxIOGImOGMoOGKlS/hi5jhi7Qg4YyL4YitIOGKoOGIteGJgOGLteGI"
    "mOGKlSDhiaAgcGVuZGluZ19jcmVkaXRfdG9wdXBzIOGImOGLneGMjeGJoOGKlSDhi4jhi7Lhi6vhi43hipEKICAgICAgICAjIOGK"
    "peGKleGLsuGMiOGMo+GMoOGInSDhiqXhipPhi7DhiK3hjIvhiIjhipUgLSDhiqDhiIjhiaDhiIjhi5rhi6sg4Yib4YqV4YidIOGK"
    "q+GIjeGMoOGJoOGJgOGLjSBwZW5kaW5nIGVudHJ5IOGMi+GIrSDhiLXhiIjhiJvhi63hjIjhjKPhjKXhiJ0g4Yut4YqV4Yi44Yir"
    "4Ymw4Ym1IOGKkOGJoOGIreGNogogICAgICAgIHVpZCA9IHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZAogICAgICAgIGlmIHVp"
    "ZCBpbiBjcmVkaXRfdG9wdXBfc3RhdGUgYW5kIGNyZWRpdF90b3B1cF9zdGF0ZVt1aWRdLmdldCgic3RlcCIpID09ICJyZWZlcmVu"
    "Y2UiOgogICAgICAgICAgICBzdGF0ZSA9IGNyZWRpdF90b3B1cF9zdGF0ZVt1aWRdCiAgICAgICAgICAgIGNhbmRfcmVmcywgXyA9"
    "IF9wYXJzZV9zbXModGV4dCkKICAgICAgICAgICAgZm9yIHJhd19yZWYgaW4gY2FuZF9yZWZzOgogICAgICAgICAgICAgICAgbnIg"
    "PSBfbm9ybWFsaXplX3JlZihyYXdfcmVmKQogICAgICAgICAgICAgICAgaWYgbnIgYW5kIG5yIG5vdCBpbiB1c2VkX3Ntc19yZWZz"
    "IGFuZCBuciBub3QgaW4gcGVuZGluZ19jcmVkaXRfdG9wdXBzOgogICAgICAgICAgICAgICAgICAgIHBlbmRpbmdfY3JlZGl0X3Rv"
    "cHVwc1tucl0gPSB7CiAgICAgICAgICAgICAgICAgICAgICAgICJyYXdfcmVmIjogcmF3X3JlZiwgImFtb3VudCI6IHN0YXRlWyJh"
    "bW91bnQiXSwgIm1ldGhvZCI6IHN0YXRlLmdldCgibWV0aG9kIiksCiAgICAgICAgICAgICAgICAgICAgICAgICJjcmVhdGVkX2F0"
    "IjogZGF0ZXRpbWUubm93KCksCiAgICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICBjcmVkaXRfdG9wdXBfc3RhdGUucG9w"
    "KHVpZCwgTm9uZSkKICAgICAgICBhd2FpdCBfcHJvY2Vzc19mb3J3YXJkZWRfc21zKHVwZGF0ZSwgY29udGV4dCwgdGV4dCkKICAg"
    "ICAgICByZXR1cm4KCiAgICBpZiB1cGRhdGUubWVzc2FnZS5mcm9tX3VzZXIuaWQgaW4gd2lubmVyc2xvdHNfc3RhdGU6CiAgICAg"
    "ICAgdWlkID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkCiAgICAgICAgdGV4dF9jbGVhbiA9ICh0ZXh0IG9yICIiKS5zdHJp"
    "cCgpCiAgICAgICAgaWYgX2lzX2NhbmNlbF90ZXh0KHRleHRfY2xlYW4pOgogICAgICAgICAgICB3aW5uZXJzbG90c19zdGF0ZS5k"
    "aXNjYXJkKHVpZCkKICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4p2MIOGJsOGIsOGIreGLn+GI"
    "jeGNoiIpCiAgICAgICAgICAgIHJldHVybgogICAgICAgIHRyeToKICAgICAgICAgICAgbiA9IGludCh0ZXh0X2NsZWFuKQogICAg"
    "ICAgIGV4Y2VwdCBWYWx1ZUVycm9yOgogICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g"
    "4Ym14Yqt4Yqt4YiI4YqbIOGJgeGMpeGIrSDhiaXhibsg4Yur4Yi14YyI4YmhICjhiqgxIOGKpeGIteGKqCAxMCnhjaYiKQogICAg"
    "ICAgICAgICByZXR1cm4KICAgICAgICBpZiBuIDwgMSBvciBuID4gMTA6CiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdl"
    "LnJlcGx5X3RleHQoIuKaoO+4jyDhiqgxIOGKpeGIteGKqCAxMCDhiJjhiqvhiqjhiI0g4YmB4Yyl4YitIOGLq+GIteGMiOGJoeGN"
    "piIpCiAgICAgICAgICAgIHJldHVybgogICAgICAgIHdpbm5lcnNsb3RzX3N0YXRlLmRpc2NhcmQodWlkKQogICAgICAgIGlmIG4g"
    "PT0gV0lOTkVSX1NMT1RTOgogICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KGYi4oS577iPIOGLqOGK"
    "oOGIuOGKk+GNiiDhiabhibPhi47hib0g4YmB4Yyl4YitIOGKoOGIteGJgOGLteGIniB7bn0g4YqQ4YuN4Y2iIikKICAgICAgICAg"
    "ICAgcmV0dXJuCiAgICAgICAgcGVuZGluZ193aW5uZXJzbG90c19jb25maXJtW3VpZF0gPSBuCiAgICAgICAga2IgPSBJbmxpbmVL"
    "ZXlib2FyZE1hcmt1cChbWwogICAgICAgICAgICBJbmxpbmVLZXlib2FyZEJ1dHRvbigi4pyFIOGKoOGLjuGNoyDhiYDhi63hiK0i"
    "LCBjYWxsYmFja19kYXRhPSJ3aW5uZXJzbG90c2NvbmZpcm0iKSwKICAgICAgICAgICAgSW5saW5lS2V5Ym9hcmRCdXR0b24oIuKd"
    "jCDhiqDhi63hjaMg4Ymw4YuI4YuNIiwgY2FsbGJhY2tfZGF0YT0id2lubmVyc2xvdHNjYW5jZWwiKSwKICAgICAgICBdXSkKICAg"
    "ICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICBmIuKaoO+4jyDhi6jhiqDhiLjhipPhjYog"
    "4Ymm4Ymz4YuO4Ym9IOGJpeGLm+GJtSDhiqh7V0lOTkVSX1NMT1RTfSDhi4jhi7Age259IOGImOGJgOGLqOGIrSDhiqXhiK3hjI3h"
    "jKDhipsg4YqQ4YuO4Ym1P1xuIgogICAgICAgICAgICAiKOGKkOGJo+GIrSDhi6jhibDhiJjhi5jhjIjhiaEg4Yqg4Yi44YqT4Y2K"
    "4YuO4Ym9IOGKoOGLreGKkOGKqeGInSAtIOGLiOGLsOGNiuGJtSDhiIjhiJrhiJjhi5jhjIjhiaHhibUg4Yml4Ym7IOGJsOGNheGK"
    "peGKliDhi63hipbhiKjhi4vhiI0pIiwKICAgICAgICAgICAgcmVwbHlfbWFya3VwPWtiCiAgICAgICAgKQogICAgICAgIHJldHVy"
    "bgoKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCBpbiBuZXdyb3VuZF9zdGF0ZToKICAgICAgICBhd2FpdCBoYW5k"
    "bGVfbmV3cm91bmRfZmxvdyh1cGRhdGUsIGNvbnRleHQpCiAgICAgICAgcmV0dXJuCgogICAgaWYgdXBkYXRlLm1lc3NhZ2UuZnJv"
    "bV91c2VyLmlkIGluIG1hbnVhbHNlbGxfc3RhdGU6CiAgICAgICAgdWlkID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkCiAg"
    "ICAgICAgcGVuZGluZyA9IG1hbnVhbHNlbGxfc3RhdGVbdWlkXQogICAgICAgIHRleHRfY2xlYW4gPSB0ZXh0LnN0cmlwKCkKCiAg"
    "ICAgICAgIyDhiaDhiJvhipXhi4vhiI0g4Yi94Yur4YytIOGLjeGLreGLreGJtSDhi43hiLXhjKUg4YiI4YiY4Yiw4Yio4YudIC9j"
    "YW5jZWwg4Yut4Yyg4YmA4YiZCiAgICAgICAgaWYgX2lzX2NhbmNlbF90ZXh0KHRleHRfY2xlYW4pOgogICAgICAgICAgICBtYW51"
    "YWxzZWxsX3N0YXRlLnBvcCh1aWQsIE5vbmUpCiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKd"
    "jCDhi6jhiqXhjIUg4Yi94Yur4YytIOGIguGLsOGJsSDhibDhiLDhiK3hi5/hiI3hjaIiKQogICAgICAgICAgICByZXR1cm4KCiAg"
    "ICAgICAgc3RlcCA9IHBlbmRpbmcuZ2V0KCJzdGVwIiwgIm5hbWUiKQogICAgICAgIGlzX3NwbGl0ID0gcGVuZGluZy5nZXQoIm1v"
    "ZGUiKSA9PSAic3BsaXQiCgogICAgICAgIGlmIHN0ZXAgPT0gImNob29zZV9tb2RlIjoKICAgICAgICAgICAgYXdhaXQgdXBkYXRl"
    "Lm1lc3NhZ2UucmVwbHlfdGV4dCgi4pqg77iPIOGKpeGJo+GKreGLjiDhiqjhiIvhi60g4Yqr4YiJ4Ym1IOGIgeGIiOGJtSDhiYHh"
    "iI3hjY7hib0g4Yqg4YqV4Yux4YqVIOGLreGMq+GKkSAo4YiZ4YiJIOGLi+GMiyDhi4jhi63hiJ0g4YyN4Yib4Yi9IOGLi+GMiynh"
    "jaIiKQogICAgICAgICAgICByZXR1cm4KCiAgICAgICAgaWYgc3RlcCA9PSAibmFtZSI6CiAgICAgICAgICAgIGlmIGxlbih0ZXh0"
    "X2NsZWFuKSA8IDI6CiAgICAgICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Yql4Ymj"
    "4Yqt4YuOIOGLqOGMiOGLouGLjeGKlSDhiJnhiIkg4Yi14YidIOGLq+GIteGMiOGJoeGNpiIpCiAgICAgICAgICAgICAgICByZXR1"
    "cm4KICAgICAgICAgICAgcGVuZGluZ1sibmFtZSJdID0gdGV4dF9jbGVhbgogICAgICAgICAgICBwZW5kaW5nWyJzdGVwIl0gPSAi"
    "cGhvbmUiCiAgICAgICAgICAgIGxhYmVsID0gIjHhipsg4YyI4YuiIiBpZiBpc19zcGxpdCBlbHNlICLhjIjhi6Lhi40iCiAgICAg"
    "ICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAgICAgICAgICBmIvCfk7Eg4Yqg4YiY4Yiw4YyN"
    "4YqT4YiI4YiB4Y2iIOGKoOGIgeGKlSDhi6h7bGFiZWx9IOGIteGIjeGKrSDhiYHhjKXhiK0g4Yur4Yi14YyI4Ymh4Y2mXG4iCiAg"
    "ICAgICAgICAgICAgICAi4YiI4Yid4Yiz4YiM4Y2mIDA5MTIzNDU2NzgiCiAgICAgICAgICAgICkKICAgICAgICAgICAgcmV0dXJu"
    "CgogICAgICAgIGlmIHN0ZXAgPT0gInBob25lIjoKICAgICAgICAgICAgcGhvbmUgPSB0ZXh0X2NsZWFuLnJlcGxhY2UoIiAiLCAi"
    "IikKICAgICAgICAgICAgaWYgbm90IFBIT05FX1BBVFRFUk4ubWF0Y2gocGhvbmUpOgogICAgICAgICAgICAgICAgYXdhaXQgdXBk"
    "YXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgICAgICAgICAi4pqg77iPIOGLqOGIteGIjeGKrSDhiYHhjKXhiKkg"
    "4Ym14Yqt4Yqt4YiNIOGKoOGLreGImOGIteGIjeGIneGNolxuIgogICAgICAgICAgICAgICAgICAgICLhiIjhiJ3hiLPhiIzhjaYg"
    "MDkxMjM0NTY3OFxuXG4iCiAgICAgICAgICAgICAgICAgICAgIuGKpeGJo+GKreGLjiDhiqXhipXhi7DhjIjhipMg4Yur4Yi14YyI"
    "4Ymh4Y2iIgogICAgICAgICAgICAgICAgKQogICAgICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgICAgIHBlbmRpbmdbInBob25l"
    "Il0gPSBwaG9uZQogICAgICAgICAgICBpZiBpc19zcGxpdDoKICAgICAgICAgICAgICAgIHBlbmRpbmdbInN0ZXAiXSA9ICJuYW1l"
    "MiIKICAgICAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAgICAgICAgICAgICAgIvCf"
    "kaQgPGI+MuGKmyDhjIjhi6I8L2I+IOGIteGInSDhi6vhiLXhjIjhiaHhjaZcbuGIiOGIneGIs+GIjOGNpiA8Y29kZT7hiaLhiYLh"
    "iIsg4YyI4YiY4YuzPC9jb2RlPiIsCiAgICAgICAgICAgICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIsCiAgICAgICAgICAgICAg"
    "ICApCiAgICAgICAgICAgIGVsc2U6CiAgICAgICAgICAgICAgICBwZW5kaW5nWyJzdGVwIl0gPSAicmVmZXJlbmNlIgogICAgICAg"
    "ICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgICAgICAgICAi8J+UoiDhi6jhiq3h"
    "jY3hi6sgUmVmZXJlbmNlIElEIOGKq+GIiOGLjuGJtSDhi6vhiLXhjIjhiaHhjaJcbiIKICAgICAgICAgICAgICAgICAgICAi4Yqo"
    "4YiM4YiIIC9za2lwIOGLiOGLreGInSDCq+GLneGIiOGIjcK7IOGJpeGIiOGLjSDhi63hiIvhiqnhjaJcblxuIgogICAgICAgICAg"
    "ICAgICAgICAgICLihLnvuI8gUmVmZXJlbmNlIElEIOGImOGIteGMoOGJtSDhiqDhiJvhiKvhjK0g4YqQ4YuN4Y2iIgogICAgICAg"
    "ICAgICAgICAgKQogICAgICAgICAgICByZXR1cm4KCiAgICAgICAgaWYgc3RlcCA9PSAibmFtZTIiOgogICAgICAgICAgICBpZiBs"
    "ZW4odGV4dF9jbGVhbikgPCAyOgogICAgICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4pqg77iP"
    "IOGKpeGJo+GKreGLjiDhi6gy4YqbIOGMiOGLouGLjeGKlSDhiJnhiIkg4Yi14YidIOGLq+GIteGMiOGJoeGNpiIpCiAgICAgICAg"
    "ICAgICAgICByZXR1cm4KICAgICAgICAgICAgcGVuZGluZ1sibmFtZTIiXSA9IHRleHRfY2xlYW4KICAgICAgICAgICAgcGVuZGlu"
    "Z1sic3RlcCJdID0gInBob25lMiIKICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAg"
    "ICAgICAgICLwn5OxIOGKoOGIgeGKlSDhi6gy4YqbIOGMiOGLoiDhiLXhiI3hiq0g4YmB4Yyl4YitIOGLq+GIteGMiOGJoeGNplxu"
    "4YiI4Yid4Yiz4YiM4Y2mIDA5MTIzNDU2NzgiCiAgICAgICAgICAgICkKICAgICAgICAgICAgcmV0dXJuCgogICAgICAgIGlmIHN0"
    "ZXAgPT0gInBob25lMiI6CiAgICAgICAgICAgIHBob25lMiA9IHRleHRfY2xlYW4ucmVwbGFjZSgiICIsICIiKQogICAgICAgICAg"
    "ICBpZiBub3QgUEhPTkVfUEFUVEVSTi5tYXRjaChwaG9uZTIpOgogICAgICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2Uu"
    "cmVwbHlfdGV4dCgKICAgICAgICAgICAgICAgICAgICAi4pqg77iPIOGLqOGIteGIjeGKrSDhiYHhjKXhiKkg4Ym14Yqt4Yqt4YiN"
    "IOGKoOGLreGImOGIteGIjeGIneGNolxuIgogICAgICAgICAgICAgICAgICAgICLhiIjhiJ3hiLPhiIzhjaYgMDkxMjM0NTY3OFxu"
    "XG4iCiAgICAgICAgICAgICAgICAgICAgIuGKpeGJo+GKreGLjiDhiqXhipXhi7DhjIjhipMg4Yur4Yi14YyI4Ymh4Y2iIgogICAg"
    "ICAgICAgICAgICAgKQogICAgICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgICAgIHBlbmRpbmdbInBob25lMiJdID0gcGhvbmUy"
    "CiAgICAgICAgICAgIHBlbmRpbmdbInN0ZXAiXSA9ICJyZWZlcmVuY2UiCiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdl"
    "LnJlcGx5X3RleHQoCiAgICAgICAgICAgICAgICAi8J+UoiDhi6jhiq3hjY3hi6sgUmVmZXJlbmNlIElEIOGKq+GIiOGLjuGJtSDh"
    "i6vhiLXhjIjhiaEgKOGIiOGIgeGIiOGJseGInSDhjIjhi6Lhi47hib0g4Yuo4YyL4YirKeGNolxuIgogICAgICAgICAgICAgICAg"
    "IuGKqOGIjOGIiCAvc2tpcCDhi4jhi63hiJ0gwqvhi53hiIjhiI3CuyDhiaXhiIjhi40g4Yut4YiL4Yqp4Y2iXG5cbiIKICAgICAg"
    "ICAgICAgICAgICLihLnvuI8gUmVmZXJlbmNlIElEIOGImOGIteGMoOGJtSDhiqDhiJvhiKvhjK0g4YqQ4YuN4Y2iIgogICAgICAg"
    "ICAgICApCiAgICAgICAgICAgIHJldHVybgoKICAgICAgICBpZiBzdGVwID09ICJyZWZlcmVuY2UiOgogICAgICAgICAgICByZWYg"
    "PSAiIiBpZiBfaXNfc2tpcF90ZXh0KHRleHRfY2xlYW4pIGVsc2UgdGV4dF9jbGVhbgogICAgICAgICAgICByb3VuZF9pZCA9IHBl"
    "bmRpbmdbInJvdW5kX2lkIl0KICAgICAgICAgICAgdGlja2V0X251bXMgPSBwZW5kaW5nLmdldCgidGlja2V0cyIpIG9yIChbcGVu"
    "ZGluZ1sidGlja2V0X251bSJdXSBpZiAidGlja2V0X251bSIgaW4gcGVuZGluZyBlbHNlIFtdKQogICAgICAgICAgICBtYW51YWxz"
    "ZWxsX3N0YXRlLnBvcCh1aWQsIE5vbmUpCiAgICAgICAgICAgIGlmIGlzX3NwbGl0OgogICAgICAgICAgICAgICAgb2ssIG1zZywg"
    "c2hvdWxkX25vdGlmeSA9IF9yZWdpc3Rlcl9tYW51YWxfc2FsZSgKICAgICAgICAgICAgICAgICAgICByb3VuZF9pZCwgdGlja2V0"
    "X251bXMsIHBlbmRpbmdbIm5hbWUiXSwgcGVuZGluZ1sicGhvbmUiXSwgcmVmLAogICAgICAgICAgICAgICAgICAgIHNoYXJlZF9i"
    "dXllcjI9eyJuYW1lIjogcGVuZGluZ1sibmFtZTIiXSwgInBob25lIjogcGVuZGluZ1sicGhvbmUyIl19LAogICAgICAgICAgICAg"
    "ICAgKQogICAgICAgICAgICBlbHNlOgogICAgICAgICAgICAgICAgb2ssIG1zZywgc2hvdWxkX25vdGlmeSA9IF9yZWdpc3Rlcl9t"
    "YW51YWxfc2FsZShyb3VuZF9pZCwgdGlja2V0X251bXMsIHBlbmRpbmdbIm5hbWUiXSwgcGVuZGluZ1sicGhvbmUiXSwgcmVmKQog"
    "ICAgICAgICAgICB1bmRvX2tiID0gTm9uZQogICAgICAgICAgICBpZiBvayBhbmQgdGlja2V0X251bXMgYW5kIGxlbih0aWNrZXRf"
    "bnVtcykgPD0gMjA6CiAgICAgICAgICAgICAgICB1bmRvX2tiID0gSW5saW5lS2V5Ym9hcmRNYXJrdXAoW1sKICAgICAgICAgICAg"
    "ICAgICAgICBJbmxpbmVLZXlib2FyZEJ1dHRvbigKICAgICAgICAgICAgICAgICAgICAgICAgIvCflJkgVW5kbyAo4Yut4YiF4YqV"
    "IOGIveGLq+GMrSDhiLXhiKjhi50pIiwKICAgICAgICAgICAgICAgICAgICAgICAgY2FsbGJhY2tfZGF0YT1mInVuZG9tYW51YWxz"
    "YWxlX3tyb3VuZF9pZH1feyctJy5qb2luKHN0cih0KSBmb3IgdCBpbiB0aWNrZXRfbnVtcyl9IiwKICAgICAgICAgICAgICAgICAg"
    "ICApLAogICAgICAgICAgICAgICAgXV0pCiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQobXNnLCBw"
    "YXJzZV9tb2RlPSJIVE1MIiwgcmVwbHlfbWFya3VwPXVuZG9fa2IpCiAgICAgICAgICAgIGlmIHNob3VsZF9ub3RpZnk6CiAgICAg"
    "ICAgICAgICAgICBhd2FpdCBfbm90aWZ5X3N1cGVyX2FkbWluX2xvd19jcmVkaXQoY29udGV4dC5ib3QpCiAgICAgICAgICAgIHJl"
    "dHVybgoKICAgICAgICBtYW51YWxzZWxsX3N0YXRlLnBvcCh1aWQsIE5vbmUpCiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2Uu"
    "cmVwbHlfdGV4dCgi4pqg77iPIOGLqOGIveGLq+GMrSDhiILhi7DhibEg4Yqg4YiN4Ymw4Yio4YyL4YyI4Yyg4Yid4Y2iIC9tYW51"
    "YWxzZWxsIOGJpeGIiOGLjSDhiqXhipXhi7DhjIjhipMg4Yut4YyA4Yid4Yip4Y2iIikKICAgICAgICByZXR1cm4KCiAgICBpZiB1"
    "cGRhdGUubWVzc2FnZS5mcm9tX3VzZXIuaWQgaW4gc2V0d2lubmVyX3N0YXRlOgogICAgICAgIHVpZCA9IHVwZGF0ZS5tZXNzYWdl"
    "LmZyb21fdXNlci5pZAogICAgICAgIHRleHRfY2xlYW4gPSAodGV4dCBvciAiIikuc3RyaXAoKQoKICAgICAgICBpZiBfaXNfY2Fu"
    "Y2VsX3RleHQodGV4dF9jbGVhbik6CiAgICAgICAgICAgIHNldHdpbm5lcl9zdGF0ZS5wb3AodWlkLCBOb25lKQogICAgICAgICAg"
    "ICBzZXR3aW5uZXJfdGVtcC5wb3AodWlkLCBOb25lKQogICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0"
    "KCLinYwg4Yqg4Yi44YqT4Y2KIOGImOGImOGLneGMiOGJpSDhibDhiLDhiK3hi5/hiI3hjaIiKQogICAgICAgICAgICByZXR1cm4K"
    "CiAgICAgICAgdGVtcCA9IHNldHdpbm5lcl90ZW1wLnBvcCh1aWQsIE5vbmUpCiAgICAgICAgc2V0d2lubmVyX3N0YXRlLnBvcCh1"
    "aWQsIE5vbmUpCiAgICAgICAgaWYgbm90IHRlbXA6CiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQo"
    "IuKaoO+4jyDhiILhi7DhibEg4YyK4Yuc4YuNIOGKoOGIjeGNjuGJoOGJs+GIjeGNoyAvc2V0d2lubmVyIOGLsOGMjeGImOGLjSDh"
    "i63hjIDhiJ3hiKnhjaIiKQogICAgICAgICAgICByZXR1cm4KCiAgICAgICAgcHJpemUgPSBOb25lIGlmIF9pc19za2lwX3RleHQo"
    "dGV4dF9jbGVhbikgZWxzZSB0ZXh0X2NsZWFuCiAgICAgICAgYXdhaXQgX3Nob3dfc2V0d2lubmVyX2NvbmZpcm0oCiAgICAgICAg"
    "ICAgIHVwZGF0ZSwgY29udGV4dCwgdGVtcFsicm91bmRfaWQiXSwgdGVtcFsidGlja2V0X251bSJdLCBwcml6ZSwgY2hhdF9pZD11"
    "cGRhdGUubWVzc2FnZS5jaGF0X2lkCiAgICAgICAgKQogICAgICAgIHJldHVybgoKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21f"
    "dXNlci5pZCBpbiBicm9hZGNhc3Rfc3RhdGU6CiAgICAgICAgYXdhaXQgaGFuZGxlX2Fubm91bmNlX2Zsb3codXBkYXRlLCBjb250"
    "ZXh0KQogICAgICAgIHJldHVybgoKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCBpbiBjcmVkaXRfdG9wdXBfc3Rh"
    "dGU6CiAgICAgICAgdWlkID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkCiAgICAgICAgc3RhdGUgPSBjcmVkaXRfdG9wdXBf"
    "c3RhdGVbdWlkXQogICAgICAgIHRleHRfY2xlYW4gPSB0ZXh0LnN0cmlwKCkKCiAgICAgICAgaWYgX2lzX2NhbmNlbF90ZXh0KHRl"
    "eHRfY2xlYW4pOgogICAgICAgICAgICBjcmVkaXRfdG9wdXBfc3RhdGUucG9wKHVpZCwgTm9uZSkKICAgICAgICAgICAgYXdhaXQg"
    "dXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4p2MIOGLqOGKreGIrOGLsuGJtSDhiJjhiJnhi6sg4YiC4Yuw4YmxIOGJsOGIsOGI"
    "reGLn+GIjeGNoiIpCiAgICAgICAgICAgIHJldHVybgoKICAgICAgICBzdGVwID0gc3RhdGUuZ2V0KCJzdGVwIikKCiAgICAgICAg"
    "aWYgc3RlcCA9PSAiYW1vdW50IjoKICAgICAgICAgICAgcmF3ID0gdGV4dF9jbGVhbi5yZXBsYWNlKCIsIiwgIiIpCiAgICAgICAg"
    "ICAgIHRyeToKICAgICAgICAgICAgICAgIGFtb3VudCA9IGZsb2F0KHJhdykKICAgICAgICAgICAgZXhjZXB0IFZhbHVlRXJyb3I6"
    "CiAgICAgICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Ym14Yqt4Yqt4YiI4YqbIOGL"
    "qOGMiOGKleGLmOGJpSDhiJjhjKDhipUg4Yur4Yi14YyI4Ymh4Y2jIOGIiOGIneGIs+GIjOGNpiA1MDAwIikKICAgICAgICAgICAg"
    "ICAgIHJldHVybgogICAgICAgICAgICBpZiBhbW91bnQgPD0gMDoKICAgICAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdl"
    "LnJlcGx5X3RleHQoIuKaoO+4jyDhi6jhiJrhiJ7hiInhibUg4Yuo4YyI4YqV4YuY4YmlIOGImOGMoOGKlSDhiqjhi5zhiK4g4Ymg"
    "4YiL4YutIOGImOGIhuGKlSDhiqDhiIjhiaDhibXhjaIiKQogICAgICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgICAgIGlmIG5v"
    "dCBDUkVESVRfU0VMTEVSX1BBWU1FTlRfTUVUSE9EUzoKICAgICAgICAgICAgICAgIGNyZWRpdF90b3B1cF9zdGF0ZS5wb3AodWlk"
    "LCBOb25lKQogICAgICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgICAgICAg"
    "ICAi4pqg77iPIOGLqOGKreGIrOGLsuGJtSDhiLvhjK0gKFN1cGVyIEFkbWluKSDhi6jhiq3hjY3hi6sg4Yqg4Yqr4YuN4YqV4Ym1"
    "IOGMiOGKkyDhiqDhiI3hibDhi4vhiYDhiKjhiJ3hjaIgIgogICAgICAgICAgICAgICAgICAgICJTdXBlciBBZG1pbiBzdXBlcmFk"
    "bWluX2FsbF9pbl9vbmUucHkg4YuN4Yi14YylIFNFTExFUl9URUxFQklSUl9BQ0NPVU5ULyIKICAgICAgICAgICAgICAgICAgICAi"
    "U0VMTEVSX0NCRUJJUlJfQUNDT1VOVCDhiJvhiLXhiYDhiJjhjKUg4Yqg4YiI4Ymg4Ym14Y2iIgogICAgICAgICAgICAgICAgKQog"
    "ICAgICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgICAgIHN0YXRlWyJhbW91bnQiXSA9IGFtb3VudAogICAgICAgICAgICBzdGF0"
    "ZVsic3RlcCJdID0gIm1ldGhvZCIKICAgICAgICAgICAga2IgPSBbCiAgICAgICAgICAgICAgICBbSW5saW5lS2V5Ym9hcmRCdXR0"
    "b24obVsibGFiZWwiXSwgY2FsbGJhY2tfZGF0YT1mImFkZGNyZWRpdG1ldGhvZF97a2V5fSIpXQogICAgICAgICAgICAgICAgZm9y"
    "IGtleSwgbSBpbiBDUkVESVRfU0VMTEVSX1BBWU1FTlRfTUVUSE9EUy5pdGVtcygpCiAgICAgICAgICAgIF0KICAgICAgICAgICAg"
    "YXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgICAgIGYi8J+SsCDhiJjhjKDhipXhjaYgPGI+e2Ft"
    "b3VudDouMmZ9IOGJpeGIrTwvYj5cblxuIgogICAgICAgICAgICAgICAgIvCfkrMg4Yuo4Yqt4Y2N4YurIOGLmOGLtCDhi63hiJ3h"
    "iKjhjKHhjaYiLAogICAgICAgICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIsCiAgICAgICAgICAgICAgICByZXBseV9tYXJrdXA9"
    "SW5saW5lS2V5Ym9hcmRNYXJrdXAoa2IpLAogICAgICAgICAgICApCiAgICAgICAgICAgIHJldHVybgoKICAgICAgICBpZiBzdGVw"
    "ID09ICJtZXRob2QiOgogICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLwn5GGIOGKpeGJo+GKreGL"
    "jiDhiqjhiIvhi60g4Yqr4YiJ4Ym1IOGJgeGIjeGNjuGJvSDhi6jhiq3hjY3hi6sg4YuY4Yu0IOGLreGIneGIqOGMoeGNoiIpCiAg"
    "ICAgICAgICAgIHJldHVybgoKICAgICAgICBpZiBzdGVwID09ICJyZWZlcmVuY2UiOgogICAgICAgICAgICByZWZfaW5wdXQgPSB0"
    "ZXh0X2NsZWFuCiAgICAgICAgICAgIGlmIGxlbihyZWZfaW5wdXQpIDwgMzoKICAgICAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5t"
    "ZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAgICAgICAgICAgICAgIuKaoO+4jyDhi6jhiKrhjYjhiKjhipXhiLUg4YmB4Yyl4Yit"
    "IOGJoOGMo+GInSDhiqDhjK3hiK0g4Yut4YiY4Yi14YiL4YiN4Y2jIOGKpeGKleGLsOGMiOGKkyDhi63hiIvhiqkg4YuI4Yut4Yid"
    "IOGJteGKreGKreGIiOGKm+GLjeGKlSDhi6jhiaPhipXhiq0v4Ym04YiM4Yml4YitIOGKpOGIteGKpOGIneGKpOGItSDhiKvhiLHh"
    "ipUg4Y2O4Yit4YuL4Yit4Yu1IOGLq+GLteGIreGMieGNoiIKICAgICAgICAgICAgICAgICkKICAgICAgICAgICAgICAgIHJldHVy"
    "bgogICAgICAgICAgICBub3JtX3JlZiA9IF9ub3JtYWxpemVfcmVmKHJlZl9pbnB1dCkKICAgICAgICAgICAgaWYgbm90IG5vcm1f"
    "cmVmOgogICAgICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4pqg77iPIOGJteGKreGKreGIiOGK"
    "myDhiKrhjYjhiKjhipXhiLUg4Yqg4YiN4Ymw4YyI4YqY4Yid4Y2jIOGKpeGKleGLsOGMiOGKkyDhi63hiJ7hiq3hiKnhjaIiKQog"
    "ICAgICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgICAgIGlmIG5vcm1fcmVmIGluIHVzZWRfc21zX3JlZnM6CiAgICAgICAgICAg"
    "ICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Yut4YiFIOGIquGNiOGIqOGKleGItSDhiYDhi7Xh"
    "iJ4g4Yyl4YmF4YidIOGIi+GLrSDhi43hiI/hiI3hjaIiKQogICAgICAgICAgICAgICAgcmV0dXJuCgogICAgICAgICAgICBhbW91"
    "bnQgPSBzdGF0ZVsiYW1vdW50Il0KICAgICAgICAgICAgbWV0aG9kID0gc3RhdGUuZ2V0KCJtZXRob2QiKQogICAgICAgICAgICBj"
    "cmVkaXRfdG9wdXBfc3RhdGUucG9wKHVpZCwgTm9uZSkKCiAgICAgICAgICAgIG1hdGNoZWQgPSBhd2FpdCBjaGVja191bm1hdGNo"
    "ZWRfc21zX2Zvcl9jcmVkaXQobm9ybV9yZWYsIGFtb3VudCwgbWV0aG9kLCBjb250ZXh0LmJvdCkKICAgICAgICAgICAgaWYgbWF0"
    "Y2hlZDoKICAgICAgICAgICAgICAgIHJldHVybgoKICAgICAgICAgICAgcGVuZGluZ19jcmVkaXRfdG9wdXBzW25vcm1fcmVmXSA9"
    "IHsKICAgICAgICAgICAgICAgICJyYXdfcmVmIjogcmVmX2lucHV0LCAiYW1vdW50IjogYW1vdW50LCAibWV0aG9kIjogbWV0aG9k"
    "LCAiY3JlYXRlZF9hdCI6IGRhdGV0aW1lLm5vdygpLAogICAgICAgICAgICB9CiAgICAgICAgICAgIHNhdmVfc3RhdGUoKQoKICAg"
    "ICAgICAgICAgIyDwn5SUIOGMiOGKkyDhiaDhiKvhiLUt4Yiw4YitIChhdXRvLXZlcmlmeSkg4Yi14YiL4YiN4YyI4Yyj4Yyg4YiY"
    "IC0gU3VwZXIgQWRtaW4g4Ymg4Yqt4Yis4Yuy4Ym1IOGIu+GMrSDhiabhibUg4YiL4YutIOGJoOGKpeGMhQogICAgICAgICAgICAj"
    "IOGKpeGKleGLsuGLq+GMuOGLteGJhS/hi43hi7XhiYUg4Yql4YqV4Yuy4Yur4Yuw4Yit4YyNIOGKpeGKleGLsCDhiJjhjKDhiaPh"
    "iaDhiYLhi6sgKG1hbnVhbCBiYWNrdXApIOGMpeGLq+GJhOGLjeGKlSDhiqXhipXhiI3hiqvhiIjhipXhjaIKICAgICAgICAgICAg"
    "bWV0aG9kX2xhYmVsID0gQ1JFRElUX1NFTExFUl9QQVlNRU5UX01FVEhPRFMuZ2V0KG1ldGhvZCwge30pLmdldCgibGFiZWwiLCBt"
    "ZXRob2Qgb3IgIiIpCiAgICAgICAgICAgIG9rLCBpbmZvID0gYXdhaXQgX3NlbmRfY3JlZGl0X3JlcXVlc3RfdG9fc2VsbGVyKAog"
    "ICAgICAgICAgICAgICAgYW1vdW50LCBtZXRob2QsIHJlZl9pbnB1dCwKICAgICAgICAgICAgICAgIGYie0hPU1RfTkFNRX06IHth"
    "bW91bnQ6LjJmfSDhiaXhiK0gKHttZXRob2RfbGFiZWx9KSDhiKrhjYjhiKjhipXhiLUge3JlZl9pbnB1dH0iLAogICAgICAgICAg"
    "ICApCgogICAgICAgICAgICBpZiBvazoKICAgICAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAg"
    "ICAgICAgICAgICAgICAgICAgZiLij7Mg4Yiq4Y2I4Yio4YqV4Yi1ICh7cmVmX2lucHV0fSkg4Ymw4YiY4Yud4YyN4Ymn4YiN4Y2i"
    "IOGLqOGJo+GKleGKrS/hibThiIzhiaXhiK0g4Yqk4Yi14Yqk4Yid4Yqk4YixIOGIsuGLsOGIreGItSDhiaDhiKvhiLUt4Yiw4Yit"
    "IOGLreGIqOGMi+GMiOGMo+GIjeGNo1xuIgogICAgICAgICAgICAgICAgICAgIGYi8J+UlCDhiqXhipXhi7LhiIHhiJ0g4YiIU3Vw"
    "ZXIgQWRtaW4g4Ymg4Yqt4Yis4Yuy4Ym1IOGIu+GMrSDhiabhibUg4Ymg4Yqp4YiNIOGIm+GIqOGMi+GMiOGMqyDhjKXhi6vhiYQg"
    "4Ymw4YiN4Yqz4YiNIChiYWNrdXAp4Y2iIgogICAgICAgICAgICAgICAgKQogICAgICAgICAgICBlbHNlOgogICAgICAgICAgICAg"
    "ICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgICAgICAgICBmIuKPsyDhiKrhjYjhiKjhipXh"
    "iLUgKHtyZWZfaW5wdXR9KSDhibDhiJjhi53hjI3hiafhiI3hjaIg4Yuo4Ymj4YqV4YqtL+GJtOGIjOGJpeGIrSDhiqThiLXhiqTh"
    "iJ3hiqThiLEg4Yiy4Yuw4Yit4Yi1IOGJoOGIq+GItS3hiLDhiK0g4Yut4Yio4YyL4YyI4Yyj4YiN4Y2iXG4iCiAgICAgICAgICAg"
    "ICAgICAgICAgZiLimqDvuI8g4Yib4Yiz4Yiw4Ymi4Yur4Y2mIOGIiOGKreGIrOGLsuGJtSDhiLvhjK0g4Ymm4Ym1IOGLqCBiYWNr"
    "dXAg4Yib4Yiz4YuI4YmC4YurIOGImOGIi+GKrSDhiqDhiI3hibDhiLPhiqvhiJ0gKHtpbmZvfSnhjaIiCiAgICAgICAgICAgICAg"
    "ICApCiAgICAgICAgICAgIHJldHVybgogICAgICAgIHJldHVybgoKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCBp"
    "biBzZXRfc21zX3dlYmhvb2tfc3RhdGU6CiAgICAgICAgZ2xvYmFsIE9VVEJPVU5EX1NNU19XRUJIT09LX1VSTAogICAgICAgIHVp"
    "ZCA9IHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZAogICAgICAgIHRleHRfY2xlYW4gPSB0ZXh0LnN0cmlwKCkKCiAgICAgICAg"
    "aWYgX2lzX2NhbmNlbF90ZXh0KHRleHRfY2xlYW4pOgogICAgICAgICAgICBzZXRfc21zX3dlYmhvb2tfc3RhdGUuZGlzY2FyZCh1"
    "aWQpCiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKdjCDhi6hTTVMgV2ViaG9vayBVUkwg4Yib"
    "4YuL4YmA4Yiq4YurIOGIguGLsOGJsSDhibDhiLDhiK3hi5/hiI3hjaIiKQogICAgICAgICAgICByZXR1cm4KCiAgICAgICAgaWYg"
    "X2lzX29mZl90ZXh0KHRleHRfY2xlYW4pOgogICAgICAgICAgICBzZXRfc21zX3dlYmhvb2tfc3RhdGUuZGlzY2FyZCh1aWQpCiAg"
    "ICAgICAgICAgIE9VVEJPVU5EX1NNU19XRUJIT09LX1VSTCA9IE5vbmUKICAgICAgICAgICAgc2F2ZV9zdGF0ZSgpCiAgICAgICAg"
    "ICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKchSDhi43hjKvhi4ogU01TIFdlYmhvb2sg4Yyg4Y2N4Ym34YiN"
    "4Y2iIOGKqOGKoOGIgeGKlSDhjIDhiJ3hiK4g4Yqk4Yi14Yqk4Yid4Yqk4Yi24Ym9IOGLiOGLsCDhi43hjK0g4Yqg4Yut4YiL4Yqp"
    "4Yid4Y2iIikKICAgICAgICAgICAgcmV0dXJuCgogICAgICAgIGlmIG5vdCAodGV4dF9jbGVhbi5zdGFydHN3aXRoKCJodHRwOi8v"
    "Iikgb3IgdGV4dF9jbGVhbi5zdGFydHN3aXRoKCJodHRwczovLyIpKToKICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2Uu"
    "cmVwbHlfdGV4dCgKICAgICAgICAgICAgICAgICLimqDvuI8g4Ym14Yqt4Yqt4YiI4YqbIFVSTCDhiqDhi63hiJjhiLXhiI3hiJ0g"
    "KGh0dHA6Ly8g4YuI4Yut4YidIGh0dHBzOi8vIOGMi+GIrSDhiJjhjIDhiJjhiK0g4Yqg4YiI4Ymg4Ym1KeGNolxuIgogICAgICAg"
    "ICAgICAgICAgIuGKpeGKleGLsOGMiOGKkyDhi6vhiLXhjIjhiaHhjaMg4YuI4Yut4YidICdvZmYnL8Kr4Yqg4Yyl4Y2Lwrsg4Yml"
    "4YiI4YuNIOGLq+GMpeGNieGJteGNoyDhi4jhi63hiJ0gL2NhbmNlbCDhi4jhi63hiJ0gwqvhiLDhiK3hi53CuyDhiaXhiIjhi40g"
    "4Yur4YmL4Yit4Yyh4Y2iIgogICAgICAgICAgICApCiAgICAgICAgICAgIHJldHVybgoKICAgICAgICBzZXRfc21zX3dlYmhvb2tf"
    "c3RhdGUuZGlzY2FyZCh1aWQpCiAgICAgICAgT1VUQk9VTkRfU01TX1dFQkhPT0tfVVJMID0gdGV4dF9jbGVhbgogICAgICAgIHNh"
    "dmVfc3RhdGUoKQogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAgICAgIGYi4pyFIOGLqFNN"
    "UyBXZWJob29rIFVSTCDhibDhiJjhi53hjI3hiafhiI3hjaZcbjxjb2RlPnt0ZXh0X2NsZWFufTwvY29kZT5cblxuIgogICAgICAg"
    "ICAgICAi4Yqo4Yqg4YiB4YqVIOGMgOGIneGIriDhi4jhi7Dhi5rhiIUg4Ymm4Ym1IOGIiOGKreGNjeGLqyDhiJvhiKjhjIvhjIjh"
    "jKsg4Yuo4Yia4Yuw4Yit4Yi1IOGKpeGLq+GKleGLs+GKleGLsSDhiqThiLXhiqThiJ3hiqThiLUg4YuI4Yuw4Yua4YiFIFVSTCDh"
    "jIjhiI3hiaXhjKYg4Yut4YiL4Yqr4YiN4Y2iXG4iCiAgICAgICAgICAgICLhiIjhiJvhjKXhjYvhibUg4YuI4Yut4YidIOGIiOGI"
    "mOGJgOGLqOGIrSAvc2V0c21zd2ViaG9vayDhi7DhjI3hiJjhi40g4Yut4Yyg4YmA4YiZ4Y2iIiwKICAgICAgICAgICAgcGFyc2Vf"
    "bW9kZT0iSFRNTCIsCiAgICAgICAgKQogICAgICAgIHJldHVybgoKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCBp"
    "biBlZGl0X3BheW1lbnRfc3RhdGU6CiAgICAgICAgdWlkID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkCiAgICAgICAgc3Rh"
    "dGUgPSBlZGl0X3BheW1lbnRfc3RhdGVbdWlkXQogICAgICAgIHRleHRfY2xlYW4gPSB0ZXh0LnN0cmlwKCkKCiAgICAgICAgaWYg"
    "X2lzX2NhbmNlbF90ZXh0KHRleHRfY2xlYW4pOgogICAgICAgICAgICBlZGl0X3BheW1lbnRfc3RhdGUucG9wKHVpZCwgTm9uZSkK"
    "ICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4p2MIOGLqOGKreGNjeGLqyDhiqDhiqvhi43hipXh"
    "ibUg4Yib4Yi14Ymw4Yqr4Yqo4YurIOGIguGLsOGJsSDhibDhiLDhiK3hi5/hiI3hjaIiKQogICAgICAgICAgICByZXR1cm4KCiAg"
    "ICAgICAgc3RlcCA9IHN0YXRlLmdldCgic3RlcCIpCiAgICAgICAgbWV0aG9kX2tleSA9IHN0YXRlWyJtZXRob2QiXQogICAgICAg"
    "IG1ldGhvZCA9IFBBWU1FTlRfTUVUSE9EU1ttZXRob2Rfa2V5XQoKICAgICAgICBpZiBzdGVwID09ICJhY2NvdW50IjoKICAgICAg"
    "ICAgICAgbmV3X2FjY291bnQgPSB0ZXh0X2NsZWFuLnJlcGxhY2UoIiAiLCAiIikKICAgICAgICAgICAgaWYgbm90IFBIT05FX1BB"
    "VFRFUk4ubWF0Y2gobmV3X2FjY291bnQpOgogICAgICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgK"
    "ICAgICAgICAgICAgICAgICAgICAi4pqg77iPIOGLqOGIteGIjeGKrSDhiYHhjKXhiKkg4Ym14Yqt4Yqt4YiNIOGKoOGLreGImOGI"
    "teGIjeGIneGNolxuIgogICAgICAgICAgICAgICAgICAgICLhiIjhiJ3hiLPhiIzhjaYgMDkxMjM0NTY3OFxuXG4iCiAgICAgICAg"
    "ICAgICAgICAgICAgIuGKpeGJo+GKreGLjiDhiqXhipXhi7DhjIjhipMg4Yur4Yi14YyI4Ymh4Y2jIOGLiOGLreGInSAvY2FuY2Vs"
    "IOGLiOGLreGInSDCq+GIsOGIreGLncK7IOGJpeGIiOGLjSDhi6vhiYvhiK3hjKHhjaIiCiAgICAgICAgICAgICAgICApCiAgICAg"
    "ICAgICAgICAgICByZXR1cm4KICAgICAgICAgICAgc3RhdGVbIm5ld19hY2NvdW50Il0gPSBuZXdfYWNjb3VudAogICAgICAgICAg"
    "ICBzdGF0ZVsic3RlcCJdID0gImhvbGRlciIKICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAg"
    "ICAgICAgICAgICAgIGYi4pyFIOGKoOGLsuGIsSDhiLXhiI3hiq0g4YmB4Yyl4Yit4Y2mIDxjb2RlPntuZXdfYWNjb3VudH08L2Nv"
    "ZGU+XG5cbiIKICAgICAgICAgICAgICAgIGYi8J+RpCDhi6jhiqDhiIHhipEg4Ymj4YiI4Ymk4Ym1IOGIteGIneGNpiB7bWV0aG9k"
    "Wydob2xkZXInXX1cblxuIgogICAgICAgICAgICAgICAgIuGKoOGLsuGIseGKlSDhi6jhiaPhiIjhiaThibUg4YiZ4YiJIOGIteGI"
    "nSDhi6vhiLXhjIjhiaHhjaZcbiIKICAgICAgICAgICAgICAgICIo4Yqr4YiN4Ymw4YmA4Yuo4YioIOGJsOGImOGIs+GIs+GLqeGK"
    "lSDhiLXhiJ0g4YiY4YiN4Yiw4YuNIOGImOGIi+GKrSDhi63hib3hiIvhiIkpXG5cbiIKICAgICAgICAgICAgICAgICLinYwg4YiI"
    "4Yib4YmL4Yio4YylIC9jYW5jZWwg4Yut4YiL4Yqp4Y2iIiwKICAgICAgICAgICAgICAgIHBhcnNlX21vZGU9IkhUTUwiLAogICAg"
    "ICAgICAgICApCiAgICAgICAgICAgIHJldHVybgoKICAgICAgICBpZiBzdGVwID09ICJob2xkZXIiOgogICAgICAgICAgICBuZXdf"
    "aG9sZGVyID0gdGV4dF9jbGVhbgogICAgICAgICAgICBpZiBsZW4obmV3X2hvbGRlcikgPCAyOgogICAgICAgICAgICAgICAgYXdh"
    "aXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4pqg77iPIOGKpeGJo+GKreGLjiDhibXhiq3hiq3hiIjhipsg4YiZ4YiJIOGI"
    "teGInSDhi6vhiLXhjIjhiaHhjaIiKQogICAgICAgICAgICAgICAgcmV0dXJuCgogICAgICAgICAgICBuZXdfYWNjb3VudCA9IHN0"
    "YXRlWyJuZXdfYWNjb3VudCJdCiAgICAgICAgICAgIG9sZF9hY2NvdW50ID0gbWV0aG9kWyJhY2NvdW50Il0KICAgICAgICAgICAg"
    "b2xkX2hvbGRlciA9IG1ldGhvZFsiaG9sZGVyIl0KCiAgICAgICAgICAgIFBBWU1FTlRfTUVUSE9EU1ttZXRob2Rfa2V5XVsiYWNj"
    "b3VudCJdID0gbmV3X2FjY291bnQKICAgICAgICAgICAgUEFZTUVOVF9NRVRIT0RTW21ldGhvZF9rZXldWyJob2xkZXIiXSA9IG5l"
    "d19ob2xkZXIKICAgICAgICAgICAgZWRpdF9wYXltZW50X3N0YXRlLnBvcCh1aWQsIE5vbmUpCiAgICAgICAgICAgIHNhdmVfc3Rh"
    "dGUoKQoKICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgICAgIGYi4pyFIDxi"
    "PnttZXRob2RbJ2xhYmVsJ119IOGKoOGKq+GLjeGKleGJtSDhibDhiLXhibDhiqvhiq3hiI/hiI0hPC9iPlxuXG4iCiAgICAgICAg"
    "ICAgICAgICBmIvCfk7Eg4Yi14YiN4YqtIOGJgeGMpeGIreGNpiA8cz57b2xkX2FjY291bnR9PC9zPiDihpIgPGNvZGU+e25ld19h"
    "Y2NvdW50fTwvY29kZT5cbiIKICAgICAgICAgICAgICAgIGYi8J+RpCDhiaPhiIjhiaThibXhjaYgPHM+e29sZF9ob2xkZXJ9PC9z"
    "PiDihpIge25ld19ob2xkZXJ9XG5cbiIKICAgICAgICAgICAgICAgICLhiqjhiqDhiIHhipUg4YyA4Yid4YiuIOGJsOGMq+GLi+GJ"
    "vuGJvSDhi4jhi7Dhi5rhiIUg4Yqg4Yuy4Yi1IOGKoOGKq+GLjeGKleGJtSDhjIjhipXhi5jhiaUg4Yql4YqV4Yuy4YiN4YqpIOGL"
    "reGJs+GLq+GJuOGLi+GIjeGNoiIsCiAgICAgICAgICAgICAgICBwYXJzZV9tb2RlPSJIVE1MIiwKICAgICAgICAgICAgKQogICAg"
    "ICAgICAgICByZXR1cm4KICAgICAgICByZXR1cm4KCiAgICAjIOGIm+GKkeGLi+GIjSDhiJvhjL3hi7DhiYLhi6sgKOGIiOGIneGI"
    "s+GIjCAvYXBwcm92ZV8yXzQ1IOGLiOGLreGInSDhiaXhi5kg4YmB4Yyl4Yiu4Ym94YqVIOGJoOGKoOGKleGLtSDhjIrhi5wgL2Fw"
    "cHJvdmVfMl80NV80Nl80NyAtIOGLmeGIrSAyIOGJgeGMpeGIrSA0NSw0Niw0NykKICAgIGlmIHRleHQuc3RhcnRzd2l0aCgiL2Fw"
    "cHJvdmVfIik6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBjbWRfdG9rZW4gPSB0ZXh0LnN0cmlwKCkuc3BsaXQoKVswXSAgIyDh"
    "ibXhi5Xhi5vhi50g4YmD4YiJIOGJpeGJuyAo4Yqo4Yy94YiB4Y2NIOGJoOGKi+GIiyDhi6vhiIgg4Yib4YqV4Yqb4YuN4YidIOGK"
    "kOGMiOGIrSDhib3hiIsg4Yut4Ymj4YiL4YiNKQogICAgICAgICAgICBwYXJ0cyA9IGNtZF90b2tlbi5zcGxpdCgiXyIpCiAgICAg"
    "ICAgICAgIHJpZCA9IGludChwYXJ0c1sxXSkKICAgICAgICAgICAgdF9udW1zID0gW2ludChwKSBmb3IgcCBpbiBwYXJ0c1syOl1d"
    "CiAgICAgICAgICAgIGlmIG5vdCB0X251bXM6CiAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCJubyB0aWNrZXQgbnVt"
    "YmVycyIpCiAgICAgICAgICAgIGlmIGxlbih0X251bXMpID09IDE6CiAgICAgICAgICAgICAgICBhd2FpdCBhcHByb3ZlX3RpY2tl"
    "dChyaWQsIHRfbnVtc1swXSwgY29udGV4dC5ib3QpCiAgICAgICAgICAgIGVsc2U6CiAgICAgICAgICAgICAgICBhd2FpdCBhcHBy"
    "b3ZlX3RpY2tldF9ncm91cChyaWQsIHRfbnVtcywgY29udGV4dC5ib3QpCiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdl"
    "LnJlcGx5X3RleHQoCiAgICAgICAgICAgICAgICBmIuKchSDhiYHhjKXhiK0o4YuO4Ym9KSB7JywgJy5qb2luKHN0cih4KSBmb3Ig"
    "eCBpbiB0X251bXMpfSAo4YuZ4YitIHtyaWR9KSDhiIHhiInhiJ0g4Ymg4Yqg4YqV4Yu1IOGMiuGLnCDhjLjhi7XhiYDhi4vhiI0h"
    "IgogICAgICAgICAgICApCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2Uu"
    "cmVwbHlfdGV4dCgKICAgICAgICAgICAgICAgICLhi6jhibDhiLPhiLPhibAg4Yqu4Yib4YqV4Yu14Y2iIOGKoOGMoOGJg+GJgOGI"
    "neGNpiAvYXBwcm92ZV884YuZ4YitPl884YmB4Yyl4YitMT5fPOGJgeGMpeGIrTI+Xy4uLiAo4Yml4YuZIOGJgeGMpeGIruGJveGK"
    "lSDhiaDhiqDhipXhi7Ug4YyK4YucIOGIm+GMveGLsOGJhSDhi63hibvhiIvhiI0pIgogICAgICAgICAgICApCiAgICAgICAgcmV0"
    "dXJuCgogICAgaWYgdGV4dC5zdGFydHN3aXRoKCIvcmVqZWN0XyIpOgogICAgICAgIHRyeToKICAgICAgICAgICAgcmF3ID0gdGV4"
    "dC5zdHJpcCgpCiAgICAgICAgICAgIGNtZF90b2tlbiwgXywgcmVhc29uID0gcmF3LnBhcnRpdGlvbigiICIpCiAgICAgICAgICAg"
    "IHJlYXNvbiA9IHJlYXNvbi5zdHJpcCgpIG9yIE5vbmUKICAgICAgICAgICAgcGFydHMgPSBjbWRfdG9rZW4uc3BsaXQoIl8iKQog"
    "ICAgICAgICAgICByaWQgPSBpbnQocGFydHNbMV0pCiAgICAgICAgICAgIHRfbnVtcyA9IFtpbnQocCkgZm9yIHAgaW4gcGFydHNb"
    "MjpdXQogICAgICAgICAgICBpZiBub3QgdF9udW1zOgogICAgICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcigibm8gdGlja2V0"
    "IG51bWJlcnMiKQogICAgICAgICAgICBpZiBsZW4odF9udW1zKSA9PSAxOgogICAgICAgICAgICAgICAgYXdhaXQgcmVqZWN0X3Rp"
    "Y2tldChyaWQsIHRfbnVtc1swXSwgY29udGV4dC5ib3QsIHJlYXNvbj1yZWFzb24pCiAgICAgICAgICAgIGVsc2U6CiAgICAgICAg"
    "ICAgICAgICBhd2FpdCByZWplY3RfdGlja2V0X2dyb3VwKHJpZCwgdF9udW1zLCBjb250ZXh0LmJvdCwgcmVhc29uPXJlYXNvbikK"
    "ICAgICAgICAgICAgIyDhiJvhiLXhibPhi4jhiLvhjaYg4YuN4Yu14YmFIOGIm+GLteGIqOGMjSDhiLXhiIjhibDhiLPhiqsg4YiI"
    "4Yqg4Yu14Yia4YqRIOGIneGKleGInSDhiJvhiKjhjIvhjIjhjKsg4Yqg4YqV4YiN4Yqt4YidICjhi43hi7XhiYUt4YqQ4YqtIOGI"
    "m+GIs+GLiOGJguGLq+GLjuGJvSDhiIHhiIkg4YiI4Yqg4Yu14Yia4YqRIOGJsOGIsOGLjeGIqOGLi+GIjSnhjaIKICAgICAgICBl"
    "eGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICAg"
    "ICAgIuGLqOGJsOGIs+GIs+GJsCDhiq7hiJvhipXhi7XhjaIg4Yqg4Yyg4YmD4YmA4Yid4Y2mIC9yZWplY3RfPOGLmeGIrT5fPOGJ"
    "geGMpeGIrTE+XzzhiYHhjKXhiK0yPl8uLi4gW+GKoOGIm+GIq+GMrSDhiJ3hiq3hipXhi6vhibVdXG4iCiAgICAgICAgICAgICAg"
    "ICAi4YiI4Yid4Yiz4YiM4Y2mIC9yZWplY3RfMl80NV80NiByZWNlaXB0IOGLsOGJpeGLm+GLmyDhiLXhiIjhiIbhipAg4Yqg4YiN"
    "4Ymz4Yuo4YidIgogICAgICAgICAgICApCiAgICAgICAgcmV0dXJuCgogICAgIyAtLS0g4Yqg4YuN4Ym24Yib4Ymy4YqtIOGIm+GI"
    "mOGIs+GKqOGIquGLqyAoQXV0by1WZXJpZnkpIC0tLQogICAgIyAo4Yib4Yi14Ymz4YuI4Yi74Y2mIHR5cGljYWwgYmFuayBTTVMg"
    "4Yy94YiB4Y2O4Ym9IOGKqOGIi+GLrSDhiaPhiIjhi40g4YmF4Yu14Yia4YurLeGIm+GMo+GIquGLqyDhiYDhi7XhiJ7hi43hipEg"
    "4Ymw4Yut4YuY4YuNIOGLreGImOGIiOGIs+GIieGNpAogICAgIyDhi63hiIUg4Yml4YiO4YqtIOGLqOGJsOGIqOGNiC/hi6vhiI3h"
    "ibPhi4jhiYAg4Yy94YiB4Y2NIOGLsOGIheGKleGKkOGJtSDhiJvhiKjhjIvhjIjhjKsgKGZhbGxiYWNrKSDhiaXhibsg4YqQ4YuN"
    "4Y2iKQogICAgYXdhaXQgX3Byb2Nlc3NfZm9yd2FyZGVkX3Ntcyh1cGRhdGUsIGNvbnRleHQsIHRleHQpCgphc3luYyBkZWYgX3By"
    "b2Nlc3NfZm9yd2FyZGVkX3Ntcyh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSwgdGV4"
    "dDogc3RyKToKICAgICIiIuGNjuGIreGLi+GIreGLtSDhi6jhibDhi7DhiKjhjIgg4Yuo4Ymj4YqV4YqtIOGKpOGIteGKpOGIneGK"
    "pOGItSDhjL3hiIHhjY3hipUg4YqoIGF1dG8tdmVyaWZ5IOGMi+GIrSDhi6jhiJrhi6vhjIjhipPhip0gaGVscGVy4Y2iCiAgICDh"
    "ipXhiYEg4YuK4Yub4Yit4Yu1IChuZXdyb3VuZC9tYW51YWxzZWxsL2Jyb2FkY2FzdCkg4YuN4Yi14YylIOGJouGIhuGKkeGInSDh"
    "i63hiIUg4Ymw4YyN4Ymj4YitIOGIq+GIseGKlSDhib3hiI4g4Yut4Yiw4Yir4YiN4Y2jCiAgICDhiLXhiIjhi5rhiIUg4Y2O4Yit"
    "4YuL4Yit4Yu1IOGLqOGImuGLsOGIqOGMjSDhiqThiLXhiqThiJ3hiqThiLUg4Yuo4Yur4YuZ4Ym14YqVIOGIguGLsOGJtSDhiqDh"
    "i6vhiYvhiK3hjKXhiJ3hjaIKICAgIOGIm+GIteGJs+GLiOGIu+GNpiDhiqThiLXhiqThiJ3hiqThiLEg4Ymw4YyI4Yyj4Yyl4Yie"
    "IOGIsuGMuOGLteGJhSDhiaXhibsg4YiI4Yqg4Yu14Yia4YqRIOGIm+GIqOGMi+GMiOGMqyDhi63hiIvhiqvhiI3hjaIg4Yqr4YiN"
    "4YyI4Yyj4Yyg4YiYICjhjIjhipMgUEVORElORyDhibXhi5Xhi5vhi50g4Yi14YiI4YiM4YiIKeGNowogICAg4Yur4YiIIOGIneGK"
    "leGInSDhiJvhiLPhi4jhiYLhi6sg4Ymg4Yy44Yyl4YmzIOGLiOGLsCB1bm1hdGNoZWRfc21zX2xvZyDhibDhiYDhiJ3hjKYg4Yut"
    "4Yyg4Yml4YmD4YiNIC0g4YuI4Yuw4Y2K4Ym1IOGJsOGMq+GLi+GJvSDhibDhiJjhiLPhiLPhi60g4Yiq4Y2I4Yio4YqV4Yi1IOGI"
    "suGIjeGKrQogICAg4Ymg4Yir4Yi1LeGIsOGIrSDhi63hjIjhjKPhjKDhiJvhiI0gKOGKqOGIi+GLrSBjaGVja191bm1hdGNoZWRf"
    "c21zX2Zvcl9vcmRlci9jaGVja191bm1hdGNoZWRfc21zX2Zvcl90aWNrZXQg4Yut4YiY4YiN4Yqo4YmxKeGNoiIiIgogICAgcmVz"
    "dWx0ID0gYXdhaXQgdHJ5X2F1dG9fbWF0Y2hfYW5kX2FwcHJvdmUodGV4dCwgY29udGV4dC5ib3QsIHNlbmRlcj1Ob25lLCBzb3Vy"
    "Y2U9InRlbGVncmFtLWZvcndhcmQiKQogICAgaWYgcmVzdWx0LmdldCgibWF0Y2hlZCIpIGFuZCBub3QgcmVzdWx0LmdldCgiY3Jl"
    "ZGl0X3RvcHVwIik6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgZiLwn46JIOGI"
    "m+GImOGIs+GKqOGIquGLq+GLjSDhibDhiLPhiq3hibfhiI0hIOGJgeGMpeGIrSB7cmVzdWx0Wyd0aWNrZXQnXX0gKOGLmeGIrSB7"
    "cmVzdWx0Wydyb3VuZF9pZCddfSkg4Ymg4Yir4Yi1LeGIsOGIrSDhiaDhiaPhipXhiq0gU01TICh7cmVzdWx0WydyZWYnXX0pIOGM"
    "uOGLteGJi+GIjeGNoiIKICAgICAgICApCgogICAgIyAtLS0g4Yuo4Ymw4YiL4Yqo4YuN4YqVIOGKpuGIquGMheGKk+GIjSDhi6jh"
    "iaPhipXhiq0g4Yqk4Yi14Yqk4Yid4Yqk4Yi1IOGMveGIgeGNjSAo4Yi14YixIOGImOGIqOGMgyAtIOGKoOGKq+GLjeGKleGJtS/h"
    "iIvhiqov4YiY4Yyg4YqVIOGLqOGLq+GLmCkg4YuI4Yuy4Yur4YuN4YqRIOGKpeGKk+GMoOGNi+GLi+GIiOGKleGNogogICAgIyDh"
    "ibDhjIjhip3hibYg4Yuo4YqQ4Ymg4Yio4YuNIOGIquGNiOGIqOGKleGItSDhiYHhjKXhiK0g4YmA4Yu14Yie4YuN4YqRICjhiqjh"
    "iIvhi60gdHJ5X2F1dG9fbWF0Y2hfYW5kX2FwcHJvdmUoKSDhi43hiLXhjKUpIOGLiOGLsCB1c2VkX3Ntc19yZWZzCiAgICAjIOGL"
    "iOGLreGInSB1bm1hdGNoZWRfc21zX2xvZyDhiLXhiIjhibDhiYDhiJjhjKAg4Yid4YqV4YidIOGImOGIqOGMgyDhiqDhi63hjKDh"
    "jYvhiJ0gLSDhjI3hjKXhiJrhi6vhi40g4Ymi4Yiz4Yqr4YidIOGJo+GLreGIs+GKq+GInSDhi63hiIUg4Yy94YiB4Y2NIOGLreGI"
    "sOGIqOGLm+GIjeGNogogICAgdHJ5OgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLmRlbGV0ZSgpCiAgICBleGNlcHQgRXhj"
    "ZXB0aW9uOgogICAgICAgIHBhc3MKCmRlZiBfZGVkdWN0X2hvc3RfY29tbWlzc2lvbihyb3VuZF9pZCwgdGlja2V0X251bXMpOgog"
    "ICAgIiIi4YiI4Ymw4Yi44YyhIChTT0xEIOGLqOGJsOGLsOGIqOGMiSkg4Ymy4Yqs4Ym24Ym9IOGKruGImuGIveGKlSDhiqjhiIbh"
    "iLXhibEg4Yqt4Yis4Yuy4Ym1IOGIkuGIs+GJpSDhi6jhiJrhiYDhipXhiLUgaGVscGVyICjhiaDhiqXhi6vhipXhi7PhipXhi7Eg"
    "4Ymy4Yqs4Ym1IOGJsOGIiOGLreGJtiDhi63hiLDhiIvhiI0p4Y2iCiAgICBIT1NUX0NPTU1JU1NJT05fUEVSQ0VOVCDhi5zhiK4g"
    "4Yqo4YiG4YqQIOGIneGKleGInSDhiqDhi63hiYDhipXhiLXhiJ3hjaIg4YiS4Yiz4YmhIOGKqOGLmuGIhSDhiYXhipPhiL0g4Ymg"
    "4YqL4YiLIOGLnOGIriDhi4jhi63hiJ0g4Yqo4Yua4YurIOGJoOGJs+GJvSDhiqjhi7DhiKjhiLAg4Yql4YqTCiAgICDhjIjhipMg"
    "4Yi14YiL4YiN4Ymw4Yuw4Yio4YyIIOGJpeGJu+GNpiDhiL3hi6vhjK0g4Ymg4Yir4Yi1LeGIsOGIrSDhi6vhiYbhiJ3hipMgU3Vw"
    "ZXIgQWRtaW4g4Yib4Yiz4YuI4YmFIOGKpeGKleGLs+GIiOGJoOGJtSBUcnVlIOGLreGImOGIjeGIs+GIjSAo4Yqr4YiN4YiG4YqQ"
    "IEZhbHNlKeGNoiIiIgogICAgZ2xvYmFsIGhvc3RfcGF1c2VkLCBob3N0X3BhdXNlZF9yZWFzb24KICAgIGlmIEhPU1RfQ09NTUlT"
    "U0lPTl9QRVJDRU5UIDw9IDAgb3Igbm90IHRpY2tldF9udW1zOgogICAgICAgIHJldHVybiBGYWxzZQogICAgcHJpY2UgPSBmbG9h"
    "dChyb3VuZHMuZ2V0KHJvdW5kX2lkLCB7fSkuZ2V0KCJwcmljZSIsIDApIG9yIDApCiAgICBjb21taXNzaW9uID0gcHJpY2UgKiBs"
    "ZW4odGlja2V0X251bXMpICogKEhPU1RfQ09NTUlTU0lPTl9QRVJDRU5UIC8gMTAwLjApCiAgICBpZiBjb21taXNzaW9uIDw9IDA6"
    "CiAgICAgICAgcmV0dXJuIEZhbHNlCiAgICBob3N0X2NyZWRpdFsiYmFsYW5jZSJdID0gcm91bmQoaG9zdF9jcmVkaXRbImJhbGFu"
    "Y2UiXSAtIGNvbW1pc3Npb24sIDIpCiAgICBob3N0X2NyZWRpdFsidG90YWxfZGVkdWN0ZWQiXSA9IHJvdW5kKGhvc3RfY3JlZGl0"
    "LmdldCgidG90YWxfZGVkdWN0ZWQiLCAwLjApICsgY29tbWlzc2lvbiwgMikKCiAgICBzaG91bGRfbm90aWZ5ID0gRmFsc2UKICAg"
    "IGlmIGhvc3RfY3JlZGl0WyJiYWxhbmNlIl0gPD0gMDoKICAgICAgICBpZiBub3QgaG9zdF9wYXVzZWQ6CiAgICAgICAgICAgIGhv"
    "c3RfcGF1c2VkID0gVHJ1ZQogICAgICAgICAgICBob3N0X3BhdXNlZF9yZWFzb24gPSAiY3JlZGl0IgogICAgICAgIGlmIG5vdCBo"
    "b3N0X2NyZWRpdC5nZXQoImxvd19jcmVkaXRfbm90aWZpZWQiKToKICAgICAgICAgICAgaG9zdF9jcmVkaXRbImxvd19jcmVkaXRf"
    "bm90aWZpZWQiXSA9IFRydWUKICAgICAgICAgICAgc2hvdWxkX25vdGlmeSA9IFRydWUKICAgIHNhdmVfc3RhdGUoKQogICAgcmV0"
    "dXJuIHNob3VsZF9ub3RpZnkKCmFzeW5jIGRlZiBfbm90aWZ5X3N1cGVyX2FkbWluX2xvd19jcmVkaXQoYm90KToKICAgICIiIuGL"
    "qOGIhuGIteGJsSDhiq3hiKzhi7LhibUg4Yiy4Yur4YiN4YmFIFN1cGVyIEFkbWluIOGLqOGJtOGIjOGMjeGIq+GInSDhiJjhiI3h"
    "iqXhiq3hibUgKOGMpeGIqikg4Yuo4Yia4Yuw4Yio4YyN4YiI4Ym1IGhlbHBlcuGNoiIiIgogICAgaWYgbm90IFNVUEVSX0FETUlO"
    "X0lEIG9yIGJvdCBpcyBOb25lOgogICAgICAgIHJldHVybgogICAgdHJ5OgogICAgICAgIGF3YWl0IGJvdC5zZW5kX21lc3NhZ2Uo"
    "CiAgICAgICAgICAgIGNoYXRfaWQ9U1VQRVJfQURNSU5fSUQsCiAgICAgICAgICAgIHRleHQ9KAogICAgICAgICAgICAgICAgZiLw"
    "n5qoIDxiPntIT1NUX05BTUV9IOKAlCDhiq3hiKzhi7LhibUg4Yqg4YiN4YmL4YiNITwvYj5cblxuIgogICAgICAgICAgICAgICAg"
    "ZiLwn5KzIOGLqOGJgOGIqCDhiJLhiLPhiaXhjaYge2hvc3RfY3JlZGl0WydiYWxhbmNlJ106LjJmfSDhiaXhiK1cbiIKICAgICAg"
    "ICAgICAgICAgIGYi8J+TiiDhi6jhiq7hiJrhiL3hipUg4YiY4Yyg4YqV4Y2mIHtIT1NUX0NPTU1JU1NJT05fUEVSQ0VOVDouMWZ9"
    "JVxuIgogICAgICAgICAgICAgICAgZiLij7jvuI8g4Yi94Yur4YytIOGJoOGIq+GItS3hiLDhiK0g4YmG4Yif4YiN4Y2jIOGJsOGM"
    "q+GLi+GJvuGJvSDhibLhiqzhibUg4YiY4YyN4Yub4Ym1IOGKoOGLreGJveGIieGIneGNolxuXG4iCiAgICAgICAgICAgICAgICBm"
    "IuKelSDhiJLhiLPhiaUg4YiI4YiY4YiZ4YiL4Ym14Y2mIDxjb2RlPi9hZGRjcmVkaXQgJmx0O+GImOGMoOGKlSZndDs8L2NvZGU+"
    "IOGLreGIi+GKqeGNoiIKICAgICAgICAgICAgKSwKICAgICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIKICAgICAgICApCiAgICBl"
    "eGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKCmFzeW5jIGRlZiBhcHByb3ZlX3RpY2tldChyb3VuZF9pZCwgdF9udW0sIGJv"
    "dCk6CiAgICB0ID0gcm91bmRzW3JvdW5kX2lkXVsidGlja2V0cyJdW3RfbnVtXQogICAgdFsic3RhdHVzIl0gPSAiU09MRCIKICAg"
    "IHVfaWQgPSB0WyJ1c2VyX2lkIl0KICAgIGlmIHVfaWQgaW4gdXNlcl9zZWxlY3Rpb25zOgogICAgICAgIGRlbCB1c2VyX3NlbGVj"
    "dGlvbnNbdV9pZF0KICAgIF9jbGVhcl9yZWplY3RlZF9yZWZfZW50cmllcyhyb3VuZF9pZCwgW3RfbnVtXSkKICAgIHNob3VsZF9u"
    "b3RpZnkgPSBfZGVkdWN0X2hvc3RfY29tbWlzc2lvbihyb3VuZF9pZCwgW3RfbnVtXSkKICAgIHNhdmVfc3RhdGUoKQogICAgdHJ5"
    "OgogICAgICAgIGF3YWl0IGJvdC5zZW5kX21lc3NhZ2UoY2hhdF9pZD11X2lkLCB0ZXh0PUwodV9pZCwgInZlcmlmaWVkX3N1Y2Nl"
    "c3MiLCB0bnM9c3RyKHRfbnVtKSwgcmlkPXJvdW5kX2lkKSkKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcGFzcwogICAg"
    "aWYgc2hvdWxkX25vdGlmeToKICAgICAgICBhd2FpdCBfbm90aWZ5X3N1cGVyX2FkbWluX2xvd19jcmVkaXQoYm90KQoKYXN5bmMg"
    "ZGVmIGFwcHJvdmVfdGlja2V0X2dyb3VwKHJvdW5kX2lkLCB0aWNrZXRzLCBib3QpOgogICAgIiIi4YiI4Yqg4YqV4Yu1IOGJteGL"
    "leGLm+GLnSAoY2hlY2tvdXQpIOGLjeGIteGMpSDhi6vhiInhibXhipUg4YmB4Yyl4Yiu4Ym9IOGJoOGImeGIiSDhiaDhiqDhipXh"
    "i7Ug4YyK4YucIFNPTEQg4Yqg4Yu14Yit4YyOIOGLqOGImuGLq+GMuOGLteGJhSBoZWxwZXIiIiIKICAgIHVpZCA9IE5vbmUKICAg"
    "IGZvciB0biBpbiB0aWNrZXRzOgogICAgICAgIHQgPSByb3VuZHNbcm91bmRfaWRdWyJ0aWNrZXRzIl1bdG5dCiAgICAgICAgdFsi"
    "c3RhdHVzIl0gPSAiU09MRCIKICAgICAgICBpZiB0LmdldCgidXNlcl9pZCIpIGlzIG5vdCBOb25lOgogICAgICAgICAgICB1aWQg"
    "PSB0WyJ1c2VyX2lkIl0KICAgIGlmIHVpZCBpbiB1c2VyX3NlbGVjdGlvbnM6CiAgICAgICAgZGVsIHVzZXJfc2VsZWN0aW9uc1t1"
    "aWRdCiAgICBfY2xlYXJfcmVqZWN0ZWRfcmVmX2VudHJpZXMocm91bmRfaWQsIHRpY2tldHMpCiAgICBzaG91bGRfbm90aWZ5ID0g"
    "X2RlZHVjdF9ob3N0X2NvbW1pc3Npb24ocm91bmRfaWQsIHRpY2tldHMpCiAgICBzYXZlX3N0YXRlKCkKICAgIGlmIHVpZCBpcyBu"
    "b3QgTm9uZToKICAgICAgICB0cnk6CiAgICAgICAgICAgIGF3YWl0IGJvdC5zZW5kX21lc3NhZ2UoY2hhdF9pZD11aWQsIHRleHQ9"
    "TCh1aWQsICJ2ZXJpZmllZF9zdWNjZXNzIiwgdG5zPSIsICIuam9pbihzdHIoeCkgZm9yIHggaW4gdGlja2V0cyksIHJpZD1yb3Vu"
    "ZF9pZCkpCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgcGFzcwogICAgaWYgc2hvdWxkX25vdGlmeToKICAg"
    "ICAgICBhd2FpdCBfbm90aWZ5X3N1cGVyX2FkbWluX2xvd19jcmVkaXQoYm90KQoKYXN5bmMgZGVmIHJlbGVhc2VfdGlja2V0X2dy"
    "b3VwKHJvdW5kX2lkLCB0aWNrZXRzLCBib3Q9Tm9uZSwgbm90aWZ5PVRydWUpOgogICAgIiIi4YiI4Yqg4YqV4Yu1IOGJteGLleGL"
    "m+GLnSDhi43hiLXhjKUg4Yur4YiJ4Ym14YqVIOGJgeGMpeGIruGJvSDhiaDhiJnhiIkg4Yuo4Yia4YiI4YmFICjhiIjhiJ3hiLPh"
    "iIwg4YyK4YucIOGIteGIi+GIiOGNiCDhi4jhi63hiJ0g4YiZ4Yqo4YirIOGIteGIi+GIiOGJgCkgaGVscGVyIiIiCiAgICB1aWQg"
    "PSBOb25lCiAgICBmb3IgdG4gaW4gdGlja2V0czoKICAgICAgICB0ayA9IHJvdW5kcy5nZXQocm91bmRfaWQsIHt9KS5nZXQoInRp"
    "Y2tldHMiLCB7fSkuZ2V0KHRuKQogICAgICAgIGlmIHRrIGFuZCB0ay5nZXQoInVzZXJfaWQiKSBpcyBub3QgTm9uZToKICAgICAg"
    "ICAgICAgdWlkID0gdGsuZ2V0KCJ1c2VyX2lkIikKICAgICAgICBhd2FpdCByZWxlYXNlX3RpY2tldChyb3VuZF9pZCwgdG4sIGJv"
    "dD1ib3QsIG5vdGlmeT1ub3RpZnkpCiAgICBpZiB1aWQgaXMgbm90IE5vbmUgYW5kIHVpZCBpbiB1c2VyX3NlbGVjdGlvbnM6CiAg"
    "ICAgICAgZGVsIHVzZXJfc2VsZWN0aW9uc1t1aWRdCiAgICAgICAgc2F2ZV9zdGF0ZSgpCgphc3luYyBkZWYgcmVqZWN0X3RpY2tl"
    "dChyb3VuZF9pZCwgdF9udW0sIGJvdCwgcmVhc29uPU5vbmUpOgogICAgdCA9IHJvdW5kc1tyb3VuZF9pZF1bInRpY2tldHMiXVt0"
    "X251bV0KICAgIHVfaWQgPSB0WyJ1c2VyX2lkIl0KICAgIHJvdW5kc1tyb3VuZF9pZF1bInRpY2tldHMiXVt0X251bV0gPSBfZW1w"
    "dHlfdGlja2V0KCkKICAgIGlmIHVfaWQgaW4gdXNlcl9zZWxlY3Rpb25zOgogICAgICAgIGRlbCB1c2VyX3NlbGVjdGlvbnNbdV9p"
    "ZF0KICAgIF9jbGVhcl9yZWplY3RlZF9yZWZfZW50cmllcyhyb3VuZF9pZCwgW3RfbnVtXSkKICAgIHNhdmVfc3RhdGUoKQogICAg"
    "dHJ5OgogICAgICAgIG1zZyA9IEwodV9pZCwgImFkbWluX3JlamVjdGVkX3RpY2tldCIsIHJpZD1yb3VuZF9pZCwgdG49dF9udW0p"
    "CiAgICAgICAgaWYgcmVhc29uOgogICAgICAgICAgICBtc2cgKz0gTCh1X2lkLCAiYWRtaW5fcmVqZWN0ZWRfcmVhc29uX2xpbmUi"
    "LCByZWFzb249cmVhc29uKQogICAgICAgIGF3YWl0IGJvdC5zZW5kX21lc3NhZ2UoY2hhdF9pZD11X2lkLCB0ZXh0PW1zZykKICAg"
    "IGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcGFzcwoKYXN5bmMgZGVmIHJlamVjdF90aWNrZXRfZ3JvdXAocm91bmRfaWQsIHRp"
    "Y2tldHMsIGJvdCwgcmVhc29uPU5vbmUpOgogICAgIiIi4YiI4Yqg4YqV4Yu1IOGJteGLleGLm+GLnSAoY2hlY2tvdXQpIOGLjeGI"
    "teGMpSDhi6vhiInhibXhipUg4YmB4Yyl4Yiu4Ym9IOGJoOGImeGIiSDhiaDhiqDhipXhi7Ug4YyK4YucICjhiqDhipXhi7Ug4YiL"
    "4YutKSDhi43hi7XhiYUg4Yuo4Yia4Yur4Yuw4Yit4YyNIGhlbHBlcuGNowogICAg4Yqg4Yib4Yir4YytIOGIneGKreGKleGLq+GJ"
    "tSAocmVhc29uKSDhiqvhiIgg4YiI4Ymw4Yyr4YuL4Ym5IOGKoOGJpeGIriDhi63hiIvhiqvhiI3hjaIiIiIKICAgIHVpZCA9IE5v"
    "bmUKICAgIGZvciB0biBpbiB0aWNrZXRzOgogICAgICAgIHRrID0gcm91bmRzLmdldChyb3VuZF9pZCwge30pLmdldCgidGlja2V0"
    "cyIsIHt9KS5nZXQodG4pCiAgICAgICAgaWYgdGsgYW5kIHRrLmdldCgidXNlcl9pZCIpIGlzIG5vdCBOb25lOgogICAgICAgICAg"
    "ICB1aWQgPSB0ay5nZXQoInVzZXJfaWQiKQogICAgICAgIHJvdW5kc1tyb3VuZF9pZF1bInRpY2tldHMiXVt0bl0gPSBfZW1wdHlf"
    "dGlja2V0KCkKICAgIGlmIHVpZCBpbiB1c2VyX3NlbGVjdGlvbnM6CiAgICAgICAgZGVsIHVzZXJfc2VsZWN0aW9uc1t1aWRdCiAg"
    "ICBfY2xlYXJfcmVqZWN0ZWRfcmVmX2VudHJpZXMocm91bmRfaWQsIHRpY2tldHMpCiAgICBzYXZlX3N0YXRlKCkKICAgIGlmIHVp"
    "ZCBpcyBub3QgTm9uZToKICAgICAgICB0cnk6CiAgICAgICAgICAgIGlmIGxlbih0aWNrZXRzKSA9PSAxOgogICAgICAgICAgICAg"
    "ICAgbXNnID0gTCh1aWQsICJhZG1pbl9yZWplY3RlZF90aWNrZXQiLCByaWQ9cm91bmRfaWQsIHRuPXRpY2tldHNbMF0pCiAgICAg"
    "ICAgICAgIGVsc2U6CiAgICAgICAgICAgICAgICBtc2cgPSBMKHVpZCwgImFkbWluX3JlamVjdGVkX3RpY2tldF9ncm91cCIsIHJp"
    "ZD1yb3VuZF9pZCwgdG5zPSIsICIuam9pbihzdHIoeCkgZm9yIHggaW4gdGlja2V0cykpCiAgICAgICAgICAgIGlmIHJlYXNvbjoK"
    "ICAgICAgICAgICAgICAgIG1zZyArPSBMKHVpZCwgImFkbWluX3JlamVjdGVkX3JlYXNvbl9saW5lIiwgcmVhc29uPXJlYXNvbikK"
    "ICAgICAgICAgICAgYXdhaXQgYm90LnNlbmRfbWVzc2FnZShjaGF0X2lkPXVpZCwgdGV4dD1tc2cpCiAgICAgICAgZXhjZXB0IEV4"
    "Y2VwdGlvbjoKICAgICAgICAgICAgcGFzcwoKZGVmIF9ub3JtYWxpemVfcmVmKHMpOgogICAgcmV0dXJuIHJlLnN1YihyJ1teQS1a"
    "YS16MC05XScsICcnLCBzIG9yICcnKS5sb3dlcigpCgpkZWYgX21hcmtfcmVmX3VzZWQobm9ybV9yZWYsIHJhd19yZWYsIHJvdW5k"
    "X2lkLCB0X251bSwgYm90PU5vbmUpOgogICAgIiIi4Yqg4YqV4Yu1IOGIquGNiOGIqOGKleGItSDhiYHhjKXhiK0g4Yyl4YmF4Yid"
    "IOGIi+GLrSDhiLLhi43hiI0g4Yud4Yit4Yud4Yip4YqVIOGLqOGImuGLq+GIteGJgOGIneGMpSBoZWxwZXIgKOGIm+GKleGNoyDh"
    "i6jhibXhipvhi40g4YuZ4YitL+GJgeGMpeGIreGNoyDhi6jhiq3hjY3hi6sg4YuY4Yu04Y2jIOGImOGJvCkiIiIKICAgIHQgPSBy"
    "b3VuZHNbcm91bmRfaWRdWyJ0aWNrZXRzIl1bdF9udW1dCiAgICB1c2VkX3Ntc19yZWZzW25vcm1fcmVmXSA9IHsKICAgICAgICAi"
    "cmF3X3JlZiI6IHJhd19yZWYsCiAgICAgICAgInJvdW5kX2lkIjogcm91bmRfaWQsCiAgICAgICAgInRpY2tldF9udW0iOiB0X251"
    "bSwKICAgICAgICAidGlja2V0X251bXMiOiBbdF9udW1dLAogICAgICAgICJ1c2VyX2lkIjogdC5nZXQoInVzZXJfaWQiKSwKICAg"
    "ICAgICAiYnV5ZXJfbmFtZSI6IHQuZ2V0KCJidXllcl9uYW1lIiksCiAgICAgICAgInBheW1lbnRfbWV0aG9kIjogdC5nZXQoInBh"
    "eW1lbnRfbWV0aG9kIiksCiAgICAgICAgInVzZWRfYXQiOiBkYXRldGltZS5ub3coKSwKICAgIH0KICAgIHNhdmVfc3RhdGUoKQoK"
    "ZGVmIF9tYXJrX3JlZl91c2VkX2dyb3VwKG5vcm1fcmVmLCByYXdfcmVmLCByb3VuZF9pZCwgdGlja2V0cyk6CiAgICAiIiLhiqDh"
    "ipXhi7Ug4Yiq4Y2I4Yio4YqV4Yi1IOGJgeGMpeGIrSDhiIjhiaXhi5kg4YmB4Yyl4YitIOGJteGLleGLm+GLnSAobXVsdGktdGlj"
    "a2V0IG9yZGVyKSDhjKXhiYXhiJ0g4YiL4YutIOGIsuGLjeGIjSDhi53hiK3hi53hiKnhipUg4Yuo4Yia4Yur4Yi14YmA4Yid4Yyl"
    "IGhlbHBlciIiIgogICAgdCA9IHJvdW5kc1tyb3VuZF9pZF1bInRpY2tldHMiXVt0aWNrZXRzWzBdXQogICAgdXNlZF9zbXNfcmVm"
    "c1tub3JtX3JlZl0gPSB7CiAgICAgICAgInJhd19yZWYiOiByYXdfcmVmLAogICAgICAgICJyb3VuZF9pZCI6IHJvdW5kX2lkLAog"
    "ICAgICAgICJ0aWNrZXRfbnVtIjogdGlja2V0c1swXSwKICAgICAgICAidGlja2V0X251bXMiOiBsaXN0KHRpY2tldHMpLAogICAg"
    "ICAgICJ1c2VyX2lkIjogdC5nZXQoInVzZXJfaWQiKSwKICAgICAgICAiYnV5ZXJfbmFtZSI6IHQuZ2V0KCJidXllcl9uYW1lIiks"
    "CiAgICAgICAgInBheW1lbnRfbWV0aG9kIjogdC5nZXQoInBheW1lbnRfbWV0aG9kIiksCiAgICAgICAgInVzZWRfYXQiOiBkYXRl"
    "dGltZS5ub3coKSwKICAgIH0KICAgIHNhdmVfc3RhdGUoKQoKZGVmIF9tYXJrX2NyZWRpdF9yZWZfdXNlZChub3JtX3JlZiwgcmF3"
    "X3JlZiwgYW1vdW50LCBtZXRob2QpOgogICAgIiIi4Yuo4YiG4Yi14Ym1IOGKreGIrOGLsuGJtSDhiJjhiJnhi6sg4Yiq4Y2I4Yio"
    "4YqV4Yi1IOGMpeGJheGInSDhiIvhi60g4Yiy4YuN4YiNIOGLneGIreGLneGIqeGKlSDhi6jhiJrhi6vhiLXhiYDhiJ3hjKUgaGVs"
    "cGVyICjhiqjhibLhiqzhibUg4Yiq4Y2I4Yio4YqV4Yi1IOGJsOGIiOGLreGJtiAtIOGLmeGIrS/hiYHhjKXhiK0g4Yi14YiI4YiM"
    "4YiI4YuNKSIiIgogICAgdXNlZF9zbXNfcmVmc1tub3JtX3JlZl0gPSB7CiAgICAgICAgInJhd19yZWYiOiByYXdfcmVmLAogICAg"
    "ICAgICJyb3VuZF9pZCI6IE5vbmUsCiAgICAgICAgInRpY2tldF9udW0iOiBOb25lLAogICAgICAgICJ0aWNrZXRfbnVtcyI6IFtd"
    "LAogICAgICAgICJjcmVkaXRfdG9wdXAiOiBUcnVlLAogICAgICAgICJhbW91bnQiOiBhbW91bnQsCiAgICAgICAgInBheW1lbnRf"
    "bWV0aG9kIjogbWV0aG9kLAogICAgICAgICJ1c2VkX2F0IjogZGF0ZXRpbWUubm93KCksCiAgICB9CiAgICBzYXZlX3N0YXRlKCkK"
    "CmFzeW5jIGRlZiBfYXBwbHlfdmVyaWZpZWRfY3JlZGl0X3RvcHVwKGFtb3VudCwgbWV0aG9kLCByYXdfcmVmLCBib3QsIHNvdXJj"
    "ZT0idGVsZWdyYW0iKToKICAgICIiIuGLqOGJsOGIqOGMi+GMiOGMoCDhi6jhiq3hiKzhi7LhibUg4YiY4YiZ4YurIOGImOGMoOGK"
    "lSDhi4jhi7AgaG9zdF9jcmVkaXQg4Ymg4Ym14Yqt4Yqt4YiNIOGLqOGImuGMqOGIneGIrSBoZWxwZXIgLSDhiKvhiLUt4Yiw4Yit"
    "IChhdXRvLXZlcmlmeSkg4Yql4YqTCiAgICDhiaDhiYDhjKXhibMg4Yqg4Yu14Yia4YqVIOGIm+GIs+GLiOGJguGLqyAobWFudWFs"
    "IG92ZXJyaWRlKSDhiIHhiIjhibHhiJ0g4Yuo4Yia4Yyg4YmA4YiZ4Ymg4Ym1IOGLqOGMi+GIqyDhibDhjI3hiaPhiK3hjaIiIiIK"
    "ICAgIGdsb2JhbCBob3N0X3BhdXNlZCwgaG9zdF9wYXVzZWRfcmVhc29uCiAgICBob3N0X2NyZWRpdFsiYmFsYW5jZSJdID0gcm91"
    "bmQoaG9zdF9jcmVkaXRbImJhbGFuY2UiXSArIGFtb3VudCwgMikKICAgIHJlc3VtZWQgPSBGYWxzZQogICAgaWYgaG9zdF9jcmVk"
    "aXRbImJhbGFuY2UiXSA+IDAgYW5kIGhvc3RfcGF1c2VkX3JlYXNvbiA9PSAiY3JlZGl0IjoKICAgICAgICBob3N0X3BhdXNlZCA9"
    "IEZhbHNlCiAgICAgICAgaG9zdF9wYXVzZWRfcmVhc29uID0gTm9uZQogICAgICAgIGhvc3RfY3JlZGl0WyJsb3dfY3JlZGl0X25v"
    "dGlmaWVkIl0gPSBGYWxzZQogICAgICAgIHJlc3VtZWQgPSBUcnVlCiAgICBzYXZlX3N0YXRlKCkKCiAgICBpZiBzb3VyY2UgPT0g"
    "Im1hbnVhbCI6CiAgICAgICAgaGVhZCA9IGYi4pyFIHthbW91bnQ6LjJmfSDhiaXhiK0g4YuI4YuwIOGLqOGIhuGIteGJtSDhiq3h"
    "iKzhi7LhibUg4Ymw4Yyo4Yid4Yiv4YiN4Y2iICjhiaDhiqDhi7XhiJrhipUg4Ymg4YmA4Yyl4YmzIOGLqOGJsOGImOGLmOGMiOGJ"
    "oCAtIOGLq+GIjeGJsOGIqOGMi+GMiOGMoCkiCiAgICBlbHNlOgogICAgICAgIGhlYWQgPSBmIuKchSBbe3NvdXJjZX1dIHthbW91"
    "bnQ6LjJmfSDhiaXhiK0g4YuI4YuwIOGLqOGIhuGIteGJtSDhiq3hiKzhi7LhibUg4Ymg4Yir4Yi1LeGIsOGIrSDhiaDhiqThiLXh"
    "iqThiJ3hiqThiLUg4Yiq4Y2I4Yio4YqV4Yi1ICh7cmF3X3JlZn0pIOGJsOGMqOGIneGIr+GIjeGNoiIKICAgIG1zZyA9IGhlYWQg"
    "KyBmIlxu8J+SsyDhiqDhi7LhiLEg4YiS4Yiz4Yml4Y2mIHtob3N0X2NyZWRpdFsnYmFsYW5jZSddOi4yZn0g4Yml4YitIgogICAg"
    "aWYgcmVzdW1lZDoKICAgICAgICBtc2cgKz0gIlxuXG7ilrbvuI8g4Yi94Yur4YytIOGIq+GItS3hiLDhiK0g4Yql4YqV4Yuw4YyI"
    "4YqTIOGJsOGMgOGIneGIr+GIjeGNoiIKICAgIHRyeToKICAgICAgICBhd2FpdCBib3Quc2VuZF9tZXNzYWdlKGNoYXRfaWQ9QURN"
    "SU5fSUQsIHRleHQ9bXNnKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICBwYXNzCiAgICBpZiByZXN1bWVkIGFuZCBTVVBF"
    "Ul9BRE1JTl9JRCBhbmQgU1VQRVJfQURNSU5fSUQgIT0gQURNSU5fSUQ6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBhd2FpdCBi"
    "b3Quc2VuZF9tZXNzYWdlKAogICAgICAgICAgICAgICAgY2hhdF9pZD1TVVBFUl9BRE1JTl9JRCwKICAgICAgICAgICAgICAgIHRl"
    "eHQ9ZiLinIUge0hPU1RfTkFNRX0g4Yqt4Yis4Yuy4Ym1IOGJsOGInuGIjeGJtiDhiL3hi6vhjK0g4Yql4YqV4Yuw4YyI4YqTIOGM"
    "gOGIneGIr+GIjeGNoiAo4YiS4Yiz4Yml4Y2mIHtob3N0X2NyZWRpdFsnYmFsYW5jZSddOi4yZn0g4Yml4YitKSIKICAgICAgICAg"
    "ICAgKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgIHBhc3MKICAgIHJldHVybiBtc2cKCmFzeW5jIGRlZiBj"
    "aGVja191bm1hdGNoZWRfc21zX2Zvcl9jcmVkaXQobm9ybV9yZWYsIGFtb3VudCwgbWV0aG9kLCBib3QpOgogICAgIiIi4Yqg4Yu1"
    "4Yia4YqRIOGLqOGKreGIrOGLsuGJtSDhiJjhiJnhi6sg4Yiq4Y2I4Yio4YqV4Yi1IOGMiOGKkyDhiLLhiI3hiq0g4YmA4Yu14Yie"
    "IOGKq+GIjeGMiOGMo+GMoOGImCBTTVMg4Yib4Yi14Ymz4YuI4Yi7IOGLjeGIteGMpSDhibDhiJjhiLPhiLPhi60g4Yqr4YiIIOGJ"
    "oOGIq+GItS3hiLDhiK0g4Yib4Yy94Yuw4YmFIiIiCiAgICBfcHJ1bmVfdW5tYXRjaGVkX3Ntc19sb2coKQogICAgaWYgbm90IG5v"
    "cm1fcmVmIG9yIG5vcm1fcmVmIGluIHVzZWRfc21zX3JlZnM6CiAgICAgICAgcmV0dXJuIEZhbHNlCiAgICBmb3IgZW50cnkgaW4g"
    "bGlzdCh1bm1hdGNoZWRfc21zX2xvZyk6CiAgICAgICAgZm9yIHJhd19yZWYgaW4gZW50cnlbInJlZl9jYW5kaWRhdGVzIl06CiAg"
    "ICAgICAgICAgIGlmIF9ub3JtYWxpemVfcmVmKHJhd19yZWYpICE9IG5vcm1fcmVmOgogICAgICAgICAgICAgICAgY29udGludWUK"
    "ICAgICAgICAgICAgaWYgZW50cnlbImFtb3VudCJdIGlzIE5vbmUgb3IgYWJzKGVudHJ5WyJhbW91bnQiXSAtIGFtb3VudCkgPiAw"
    "LjAxOgogICAgICAgICAgICAgICAgY29udGludWUKICAgICAgICAgICAgZW50cnlfcHJvdmlkZXIgPSBfZGV0ZWN0X3Ntc19wcm92"
    "aWRlcihlbnRyeS5nZXQoInRleHQiKSwgZW50cnkuZ2V0KCJzZW5kZXIiKSkKICAgICAgICAgICAgaWYgZW50cnlfcHJvdmlkZXIg"
    "YW5kIG1ldGhvZCBhbmQgZW50cnlfcHJvdmlkZXIgIT0gbWV0aG9kOgogICAgICAgICAgICAgICAgY29udGludWUKICAgICAgICAg"
    "ICAgX21hcmtfY3JlZGl0X3JlZl91c2VkKG5vcm1fcmVmLCByYXdfcmVmLCBhbW91bnQsIG1ldGhvZCkKICAgICAgICAgICAgdW5t"
    "YXRjaGVkX3Ntc19sb2cucmVtb3ZlKGVudHJ5KQogICAgICAgICAgICBhd2FpdCBfYXBwbHlfdmVyaWZpZWRfY3JlZGl0X3RvcHVw"
    "KGFtb3VudCwgbWV0aG9kLCByYXdfcmVmLCBib3QsIHNvdXJjZT0idGVsZWdyYW0tZm9yd2FyZCIpCiAgICAgICAgICAgIHJldHVy"
    "biBUcnVlCiAgICByZXR1cm4gRmFsc2UKCmFzeW5jIGRlZiBfbm90aWZ5X2R1cGxpY2F0ZV9yZWZfYXR0ZW1wdChib3QsIG5vcm1f"
    "cmVmLCByYXdfcmVmLCBzb3VyY2UsIGV4dHJhX3RleHQ9IiIpOgogICAgIiIi4YmA4Yuw4YidIOGJpeGIjiDhjKXhiYXhiJ0g4YiL"
    "4YutIOGLqOGLi+GIiCDhiKrhjYjhiKjhipXhiLUg4Yql4YqV4Yuw4YyI4YqTIOGIsuGIi+GKrS/hiLLhi7DhiKjhiLUgLSDhiYDh"
    "i7DhiJ0g4Yiy4YiNIOGKoOGLteGImuGKkeGKlSDhi6vhiLPhi43hiYUg4YqQ4Ymg4Yit4Y2iCiAgICDhiqDhi7XhiJrhipEg4YuN"
    "4Yu14YmFLeGKkOGKrSDhiJvhiLPhi4jhiYLhi6vhi47hib0g4YiB4YiJIOGKpeGKleGLsuGIsOGLiOGIqeGIiOGJtSDhiLXhiIjh"
    "jKDhi6jhiYAg4Yut4YiFIOGJsOGMjeGJo+GIrSDhiqDhiIHhipUg4Yid4YqV4YidIOGImOGIjeGLleGKreGJtSDhiqDhi63hiI3h"
    "iq3hiJ0gKG5vLW9wKeGNogogICAgKHVzZWRfc21zX3JlZnMg4YuN4Yi14YylIOGLq+GIiOGLjSDhiJjhiKjhjIMg4YyN4YqVIOGI"
    "s+GLreGKkOGKqyDhi63hiYDhiKvhiI0gLSDhjY3hiIvhjI7hibUg4Yqr4YiIIOGJoOGKi+GIiyAvdXNlZHJlZnMg4Ym14YuV4Yub"
    "4YudIOGLjeGIteGMpSDhiJvhi6jhibUg4Yut4Ym74YiL4YiN4Y2iKSIiIgogICAgcmV0dXJuCgojIOGLqOGJo+GKleGKrSDhiqTh"
    "iLXhiqThiJ3hiqThiLUg4YqT4Ym44YuNIOGJpeGIiOGKlSDhi6jhiJ3hipXhiYbhjKXhiK3hiaPhibjhi40g4YmB4YiN4Y2NIOGJ"
    "g+GIi+GJteGNpiDhi63hiIUg4Yqo4YiM4YiIICjhiIjhiJ3hiLPhiIwg4Yqg4Yu14Yia4YqRIOGJsOGIqyDhjL3hiIHhjY0g4Ymi"
    "4Yy94Y2NKQojIGF1dG8tdmVyaWZ5IOGIi+GLrSDhiLXhiIXhibDhibUg4Yql4YqV4Yuz4Yut4Y2I4Yyg4YitIOGIiOGImOGKqOGI"
    "i+GKqOGIjSDhjL3hiIHhjYkg4Yyo4Yit4Yi2IOGKoOGLreGJs+GLreGInQpTTVNfTE9PS1NfTElLRV9CQU5LX0tFWVdPUkRTID0g"
    "KCJjcmVkaXRlZCIsICJkZWJpdGVkIiwgInRyYW5zYWN0aW9uIiwgImJhbGFuY2UiLCAiYmlyciIsICJldGIiLCAiIGJyIiwgImJy"
    "LiIsICLhiaXhiK0iKQoKZGVmIF9vY3JfZXh0cmFjdF90ZXh0KGltYWdlX2J5dGVzKToKICAgICIiIuGKqOGIneGIteGIjSAoc2Ny"
    "ZWVuc2hvdCkg4YuN4Yi14YylIOGMveGIgeGNjSDhiIjhiJvhipXhiaDhiaUg4Yuo4Yia4Yie4Yqt4YitIGhlbHBlciAtIHB5dGVz"
    "c2VyYWN0IOGKqOGIjOGIiCDhi4jhi63hiJ0g4YqV4Ymj4YmhIOGJouGLiOGLteGJhSDhiaPhi7Ygc3RyaW5nIOGLreGImOGIjeGI"
    "s+GIjeGNogogICAg4Yib4Yi14Ymz4YuI4Yi74Y2mIOGLreGIhSBmdW5jdGlvbiDhi6vhiIjhiJ3hipXhiJ0gYXN5bmMgYmxvY2tp"
    "bmcgKHN5bmNocm9ub3VzL0NQVS1oZWF2eSkg4Yi14YiI4YiG4YqQIOGJoOGJgOGMpeGJsyDhiqggYXN5bmMgaGFuZGxlciDhi43h"
    "iLXhjKUKICAgIOGKoOGLreGMoOGIq+GInSAtIOGLreGIjeGJgeGKleGInSDhiqjhibPhib0g4Yur4YiI4YuN4YqVIF9vY3JfZXh0"
    "cmFjdF90ZXh0X2FzeW5jKCkg4YmgIGF3YWl0IOGLreGMoOGJgOGImSAoYXN5bmNpby50b190aHJlYWQg4Ymw4Yyg4YmF4YieCiAg"
    "ICDhiaDhibDhiIjhi6ggdGhyZWFkIOGLjeGIteGMpSDhiLXhiIjhiJrhi6vhiLXhiqzhi7Dhi40gZXZlbnQgbG9vcC3hipUg4Yqg"
    "4Yur4YmG4Yid4YidL+GKoOGLq+GLsOGKk+GJheGNjeGInSnhjaIiIiIKICAgIGlmIG5vdCBPQ1JfQVZBSUxBQkxFOgogICAgICAg"
    "IHJldHVybiAiIgogICAgdHJ5OgogICAgICAgIGltZyA9IEltYWdlLm9wZW4oX2lvLkJ5dGVzSU8oaW1hZ2VfYnl0ZXMpKQogICAg"
    "ICAgIHJldHVybiBweXRlc3NlcmFjdC5pbWFnZV90b19zdHJpbmcoaW1nKSBvciAiIgogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAg"
    "ICAgICByZXR1cm4gIiIKCmFzeW5jIGRlZiBfb2NyX2V4dHJhY3RfdGV4dF9hc3luYyhpbWFnZV9ieXRlcyk6CiAgICAiIiJfb2Ny"
    "X2V4dHJhY3RfdGV4dCgpLeGKlSDhiaDhibDhiIjhi6ggKHdvcmtlcikgdGhyZWFkIOGLjeGIteGMpSDhi6jhiJrhi6vhiLXhiqzh"
    "i7UgYXN5bmMgd3JhcHBlcuGNogogICAgcHl0ZXNzZXJhY3QuaW1hZ2VfdG9fc3RyaW5nKCkg4Yml4YiO4YqtIOGLqOGImuGLq+GL"
    "sOGIreGMjSAoYmxvY2tpbmcvQ1BVLWJvdW5kKSDhjKXhiKog4Yi14YiI4YiG4YqQIOGJoOGJgOGMpeGJsyDhiqggYXN5bmMgaGFu"
    "ZGxlcgogICAg4YuN4Yi14YylIOGJouGMoOGIqyDhipbhiK4g4YiY4YiL4YuN4YqVIOGLqOGJpuGJseGKlSBldmVudCBsb29wIOGI"
    "teGIiOGImuGLq+GJhuGInSAtIOGLqyDhjIrhi5wg4YuN4Yi14YylIOGIm+GKleGInSDhiIzhiIsg4Ym14YuV4Yub4YudICjhi6jh"
    "iqDhi7XhiJrhipUg4Ym14YuV4Yub4Yue4Ym94YqVIOGMqOGIneGIrikKICAgIOGIneGIi+GIvSDhiqDhi6vhjIjhip3hiJ0g4YqQ"
    "4Ymg4Yit4Y2iIGFzeW5jaW8udG9fdGhyZWFkKCkg4Ymw4Yyg4YmF4YiY4YqVIOGLreGIheGKlSDhi4jhi7Ag4Ymw4YiI4YuoIHRo"
    "cmVhZCDhiaDhiJjhiIvhiq0gZXZlbnQgbG9vcC3hipUg4YqQ4Yy7IOGKpeGKk+GLsOGIreGMiOGLi+GIiOGKleGNoiIiIgogICAg"
    "cmV0dXJuIGF3YWl0IGFzeW5jaW8udG9fdGhyZWFkKF9vY3JfZXh0cmFjdF90ZXh0LCBpbWFnZV9ieXRlcykKCmRlZiBfZXh0cmFj"
    "dF9yZWZfZnJvbV90ZXh0KHJhd190ZXh0LCBtZXRob2QpOgogICAgIiIi4Yqo4Ymw4Yiw4YygIOGMveGIgeGNjSAo4Yqo4Yqk4Yi1"
    "4Yqk4Yid4Yqk4Yi1IOGLiOGLreGInSDhiqhPQ1Ig4Yuo4YiY4YyjKSDhi43hiLXhjKUg4YiI4Ymw4YiY4Yio4Yyg4YuNIOGLqOGK"
    "reGNjeGLqyDhi5jhi7QgKG1ldGhvZCkg4Ym14Yqt4Yqt4YiI4YqbIOGJheGIreGMuOGJtSDhi6vhiIjhi40KICAgIOGLqOGImOGM"
    "gOGImOGIquGLq+GLjeGKlSDhiqXhjKkg4Yiq4Y2I4Yio4YqV4Yi1IOGJgeGMpeGIrSDhi6jhiJrhiJjhiI3hiLUgaGVscGVy4Y2i"
    "IOGKq+GIjeGJsOGMiOGKmCBOb25lIOGLreGImOGIjeGIs+GIjeGNoiIiIgogICAgaWYgbm90IHJhd190ZXh0IG9yIG5vdCBtZXRo"
    "b2Qgb3Igbm90IG1ldGhvZC5nZXQoInJlZl9wYXR0ZXJuIik6CiAgICAgICAgcmV0dXJuIE5vbmUKICAgIGNhbmRpZGF0ZXMsIF9h"
    "bW91bnQgPSBfcGFyc2Vfc21zKHJhd190ZXh0KQogICAgdmFsaWQgPSBbYyBmb3IgYyBpbiBjYW5kaWRhdGVzIGlmIG1ldGhvZFsi"
    "cmVmX3BhdHRlcm4iXS5tYXRjaChjLnJlcGxhY2UoIiAiLCAiIikudXBwZXIoKSldCiAgICByZXR1cm4gdmFsaWRbMF0gaWYgdmFs"
    "aWQgZWxzZSBOb25lCgpkZWYgX2xvb2tzX2xpa2VfYmFua19zbXModGV4dCk6CiAgICAiIiLhi63hiIUg4Yy94YiB4Y2NIOGJoOGK"
    "peGLjeGKkOGJtSDhjY7hiK3hi4vhiK3hi7Ug4Yuo4Ymw4Yuw4Yio4YyIIOGLqOGJo+GKleGKrSDhiqThiLXhiqThiJ3hiqThiLUg"
    "4Yut4YiY4Yi14YiNIOGKpeGKleGLsOGIhuGKkCDhi63hjYjhibXhiLvhiI3hjaIKICAgIOGIm+GIteGJs+GLiOGIu+GNpiDhiYDh"
    "i7DhiJ0g4Yiy4YiNIOGLreGIhSDhibDhjI3hiaPhiK0g4YmB4YiN4Y2NIOGJg+GIjSAo4YiI4Yid4Yiz4YiMIMKr4Yml4Yitwrsp"
    "ICsg4YmB4Yyl4YitICsg4Yit4Yud4YiY4Ym1IOGJpeGJuyDhjYjhiI3hjI4g4Yqr4YyI4YqYIHRydWUg4Yut4YiY4YiN4Yi1IOGK"
    "kOGJoOGIrSAtIOGLreGIhSDhjI3hipUKICAgIOGJsOGIqyDhi6jhi5nhiK0g4Yi14YidL+GImOGMjeGIiOGMqy/hiL3hiI3hiJvh"
    "ibUg4Yy94YiB4Y2NICgiMeGKmyAxMjAwMCDhiaXhiK0uLi4iKSDhiaXhiK0g4Yuo4Yia4YiNIOGJg+GIjSDhiqXhipMg4YmB4Yyl"
    "4YitIOGIteGIiOGLq+GLmCDhiaXhibsg4Ymg4Yi14YiF4Ymw4Ym1IOGKpeGKleGLsAogICAg4Ymj4YqV4YqtIOGKpOGIteGKpOGI"
    "neGKpOGItSDhibDhiYbhjKXhiK4gKG1hbnVhbHNlbGwvbmV3cm91bmQvc2V0d2lubmVyIOGLjeGLreGLreGJtSDhi43hiLXhjKUp"
    "IOGMuOGMpSDhiaXhiI4g4Yut4Yiw4Yit4YuY4YuNIOGKkOGJoOGIrSAtIOGIneGKleGInSDhiJ3hiIvhiL0KICAgIOGIs+GLreGI"
    "sOGMpeGNoiDhiqXhi43hipDhibDhipsg4Yuo4Ymj4YqV4YqtIOGKpOGIteGKpOGIneGKpOGItSDhiIHhiI3hjIrhi5wg4Yuo4YyN"
    "4Yml4Yut4Ym1IOGIquGNiOGIqOGKleGItSDhiq7hi7UgKOGIiOGIneGIs+GIjCBGVDI1MzZBQkNYWVopIOGIteGIiOGLq+GLmOGN"
    "oyDhi6sg4Yqu4Yu1CiAgICDhiqvhiI3hibDhjIjhipggKOGIm+GIiOGJteGInSDhiJvhipXhipvhi43hiJ0g4YiY4YiN4YqpIOGJ"
    "ouGIhuGKlSDhjYjhjL3hiJ4g4YiK4YyI4Yyj4Yyg4YidIOGIteGIiOGIm+GLreGJveGIjSkg4Ymj4YqV4YqtIOGKpOGIteGKpOGI"
    "neGKpOGItSDhipDhi40g4Yml4YiI4YqVIOGKoOGKleGJhuGMpeGIreGIneGNoiIiIgogICAgdGV4dCA9IHRleHQgb3IgIiIKICAg"
    "IGlmIGxlbih0ZXh0LnN0cmlwKCkpIDwgMjA6CiAgICAgICAgcmV0dXJuIEZhbHNlCiAgICBpZiBub3QgYW55KGNoLmlzZGlnaXQo"
    "KSBmb3IgY2ggaW4gdGV4dCk6CiAgICAgICAgcmV0dXJuIEZhbHNlCiAgICBsb3cgPSB0ZXh0Lmxvd2VyKCkKICAgIGlmIG5vdCBh"
    "bnkoayBpbiBsb3cgZm9yIGsgaW4gU01TX0xPT0tTX0xJS0VfQkFOS19LRVlXT1JEUyk6CiAgICAgICAgcmV0dXJuIEZhbHNlCiAg"
    "ICByZWZfY2FuZGlkYXRlcywgXyA9IF9wYXJzZV9zbXModGV4dCkKICAgIHJldHVybiBib29sKHJlZl9jYW5kaWRhdGVzKQoKZGVm"
    "IF9wYXJzZV9zbXModGV4dCk6CiAgICAjIOGJheGLteGImuGLqyDhi6jhiJ3hipXhiLDhjKDhi40g4YyN4YiN4Yy9IOGLqOGMjeGJ"
    "peGLreGJtS3hiKrhjYjhiKjhipXhiLUg4YmF4Yu14YiYLeGJheGMpeGLqyAoRlQvVFhOL1R4biBJRC9DUi9SZWYvSUQpIOGIi+GI"
    "i+GJuOGLjSDhiaXhibsg4YqQ4YuN4Y2kCiAgICAjIOGLreGIhSDhiqjhiIzhiIgg4Yml4Ym7IOGLiOGLsCDhi7DhjYjhjKMgKGdl"
    "bmVyaWMpIDgtMTUg4Yit4Yud4YiY4Ym1IOGLq+GIiOGLjSDhjY3hiIjhjIsg4Yql4YqV4YiY4YiI4Yiz4YiI4YqVIC0g4Yut4YiF"
    "4YidIOGJoOGKpOGIteGKpOGIneGKpOGItSDhi43hiLXhjKUg4Yqr4YiJCiAgICAjIOGJsOGIqyDhiYHhjKXhiK7hib0gKOGIteGI"
    "jeGKrSDhiYHhjKXhiK3hjaMg4YmA4YqV4Y2jIOGIguGIs+GJpSDhiYHhjKXhiK0g4YiY4Yyo4Yio4Yi7KSDhjIvhiK0g4Ymg4Yqg"
    "4YyL4Yyj4YiaIOGLqOGImOGMiOGMo+GMoOGInSDhiqXhi7XhiI3hipUg4Yut4YmA4YqV4Yiz4YiN4Y2iCiAgICAjIOGIm+GIteGJ"
    "s+GLiOGIu+GNpiDhiq7hi7HhipUg4Yml4Ym7IChjYXB0dXJpbmcgZ3JvdXApIOGKpeGKleGLreGLm+GIiOGKleGNoyDhiYXhi7Xh"
    "iJgt4YmF4Yyl4Yur4YuN4YqVICgiVHhuIElEIuGNoyAiSUQiLi4uKSDhiKvhiLHhipUg4Yqg4YqV4Yut4Yud4YidIC0KICAgICMg"
    "4Yqr4YiN4YiG4YqQIOGJsOGMq+GLi+GJuSDhiqjhiIvhiqjhi40g4YqV4Yy54YiFIOGKruGLtSAoIkRITjExTTJQQTFGIikg4YyL"
    "4YitIOGKoOGLreGImOGIs+GIsOGIjeGInSDhipDhiaDhiK3hjaIKICAgIHByZWZpeGVkID0gWwogICAgICAgIG0uZ3JvdXAoMSkK"
    "ICAgICAgICBmb3IgbSBpbiByZS5maW5kaXRlcihyJ1xiKD86Q1J8RlR8VFhOfFJlZnxJRClcYlstOlxzXSooW0EtWjAtOV17Niwx"
    "NX0pXGInLCB0ZXh0LCByZS5JR05PUkVDQVNFKQogICAgXQogICAgaWYgcHJlZml4ZWQ6CiAgICAgICAgcmVmX2NhbmRpZGF0ZXMg"
    "PSBwcmVmaXhlZAogICAgZWxzZToKICAgICAgICByZWZfY2FuZGlkYXRlcyA9IHJlLmZpbmRhbGwocidcYltBLVowLTldezgsMTV9"
    "XGInLCB0ZXh0KQogICAgICAgICMg4Yi14YiN4YqtIOGJgeGMpeGIrSAoMDkuLi4g4YuI4Yut4YidIDI1MTkuLi4pIOGLqOGImuGI"
    "mOGIteGIiSDhiYHhjKXhiK7hib3hipUg4Yqo4Yql4YypIOGIquGNiOGIqOGKleGItuGJvSDhi43hiLXhjKUg4Yib4Yi14YuI4Yyj"
    "4Ym1CiAgICAgICAgcmVmX2NhbmRpZGF0ZXMgPSBbCiAgICAgICAgICAgIGMgZm9yIGMgaW4gcmVmX2NhbmRpZGF0ZXMKICAgICAg"
    "ICAgICAgaWYgbm90IHJlLm1hdGNoKHInXigwfDI1MTkpXGR7Nyw5fSQnLCBjKQogICAgICAgIF0KCiAgICBzbXNfYW1vdW50ID0g"
    "Tm9uZQogICAgYW1vdW50X21hdGNoID0gKAogICAgICAgIHJlLnNlYXJjaChyJ2NyZWRpdGVkXHMrd2l0aFxzKyhbXGQsXStcLj9c"
    "ZCopXHMqKD86QnJ8RVRCfEJpcnIpJywgdGV4dCwgcmUuSUdOT1JFQ0FTRSkKICAgICAgICBvciByZS5zZWFyY2gocicoPzpFVEJ8"
    "QmlycnxBbW91bnQpWzpcc10qKFtcZCxdK1wuP1xkKiknLCB0ZXh0LCByZS5JR05PUkVDQVNFKQogICAgICAgIG9yIHJlLnNlYXJj"
    "aChyJyhbXGQsXStcLj9cZCopXHMqQnJcYicsIHRleHQsIHJlLklHTk9SRUNBU0UpCiAgICAgICAgb3IgcmUuc2VhcmNoKHInKFtc"
    "ZCxdK1wuP1xkKilccyrhiaXhiK0nLCB0ZXh0KSAgIyDhiIjhiJ3hiLPhiIzhjaYgIjQ4MC4wMCDhiaXhiK0iICjhi6jhibThiIzh"
    "iaXhiK0gQW1oYXJpYyBTTVMpCiAgICApCiAgICBpZiBhbW91bnRfbWF0Y2g6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBzbXNf"
    "YW1vdW50ID0gZmxvYXQoYW1vdW50X21hdGNoLmdyb3VwKDEpLnJlcGxhY2UoIiwiLCAiIikpCiAgICAgICAgZXhjZXB0IFZhbHVl"
    "RXJyb3I6CiAgICAgICAgICAgIHNtc19hbW91bnQgPSBOb25lCgogICAgcmV0dXJuIHJlZl9jYW5kaWRhdGVzLCBzbXNfYW1vdW50"
    "CgpkZWYgX2RldGVjdF9zbXNfcHJvdmlkZXIodGV4dCwgc2VuZGVyPU5vbmUpOgogICAgIiIi4Ymg4Yqk4Yi14Yqk4Yid4Yqk4Yi1"
    "IOGMveGIgeGNjS/hiIvhiqog4YuN4Yi14YylIOGLq+GIiOGLjeGKlSDhiYHhiI3hjY0g4YmD4YiNIOGJsOGMoOGJheGIniDhi63h"
    "iIUg4Yqk4Yi14Yqk4Yid4Yqk4Yi1IOGKqOGLqOGJteGKm+GLjSDhi6jhiq3hjY3hi6sg4YuY4Yu0ICh0ZWxlYmlyci9jYmViaXJy"
    "L2NiZSkKICAgIOGKpeGKleGLsOGImOGMoyDhiIjhiJvhi4jhiYUg4Yuo4Yia4Yie4Yqt4YitIGhlbHBlcuGNoiDhi6jhibXhipvh"
    "i43hiJ0g4Yqr4YiN4Ymz4YuI4YmAIE5vbmUg4Yut4YiY4YiN4Yiz4YiN4Y2jIOGLreGIheGInSDhiJvhiIjhibUg4Yur4YiIIOGI"
    "jeGLqSDhjIjhi7DhiaUgKOGIm+GKleGKm+GLjeGInQogICAg4Ymy4Yqs4Ym1KSDhiqXhipPhjIjhjKPhjKXhiJvhiIjhipUgLSDh"
    "iIggU01TIGZvcndhcmRlciDhiqDhjZbhib0g4Yml4YuZ4YuN4YqVIOGMiuGLnCDhiIvhiqogKHNlbmRlcikg4Yi14YidIOGMjeGI"
    "jeGMvSDhiLXhiIvhiI3hiIbhipDhjaIiIiIKICAgIGxvdyA9IGYie3RleHQgb3IgJyd9IHtzZW5kZXIgb3IgJyd9Ii5sb3dlcigp"
    "CiAgICBmb3Iga2V5LCBtZXRob2QgaW4gUEFZTUVOVF9NRVRIT0RTLml0ZW1zKCk6CiAgICAgICAgaWYgYW55KGt3IGluIGxvdyBm"
    "b3Iga3cgaW4gbWV0aG9kWyJzZW5kZXJfa2V5d29yZHMiXSk6CiAgICAgICAgICAgIHJldHVybiBrZXkKICAgIHJldHVybiBOb25l"
    "CgpkZWYgX3BydW5lX3VubWF0Y2hlZF9zbXNfbG9nKCk6CiAgICBjdXRvZmYgPSBkYXRldGltZS5ub3coKSAtIHRpbWVkZWx0YSht"
    "aW51dGVzPVVOTUFUQ0hFRF9TTVNfVFRMX01JTlVURVMpCiAgICB1bm1hdGNoZWRfc21zX2xvZ1s6XSA9IFtlIGZvciBlIGluIHVu"
    "bWF0Y2hlZF9zbXNfbG9nIGlmIGVbInJlY2VpdmVkX2F0Il0gPj0gY3V0b2ZmXQoKYXN5bmMgZGVmIF9yZWxheV9zbXNfdG9fZXh0"
    "ZXJuYWxfd2ViaG9vayh0ZXh0LCBzZW5kZXIsIHNvdXJjZSk6CiAgICAiIiLhjIjhiaIg4Yuo4Yqt4Y2N4YurIOGKpOGIteGKpOGI"
    "neGKpOGIteGKlSDhiIbhiLXhibEg4YmgL3NldHNtc3dlYmhvb2sg4Yir4YixIOGLiOGLs+GIteGJgOGImOGMoOGLjSDhi43hjKvh"
    "i4ogVVJMICjhiqvhiIgpIOGJoOGMuOGMpeGJsyDhi6jhiJrhi6vhiLXhibDhiIvhiI3hjY0KICAgIGhlbHBlciAoZmlyZS1hbmQt"
    "Zm9yZ2V0KeGNoiDhi43hi7XhiYDhibUg4Ymi4Y2I4Yyg4YitIChVUkwg4Yyg4Y2N4Ym34YiNL3RpbWVvdXQv4Yi14YiF4Ymw4Ym1"
    "KSBhdXRvLXZlcmlmeSDhiILhi7DhibHhipUKICAgIOGNiOGMveGIniDhiqDhi6vhiLXhibDhjJPhjInhiI3hiJ0gLSDhiLXhiIXh"
    "ibDhibEg4Yml4Ym7IGxvZyDhi63hi7DhiKjhjIvhiI3hjaIiIiIKICAgIHVybCA9IE9VVEJPVU5EX1NNU19XRUJIT09LX1VSTAog"
    "ICAgaWYgbm90IHVybDoKICAgICAgICByZXR1cm4KICAgIHRyeToKICAgICAgICBhc3luYyB3aXRoIGFpb2h0dHAuQ2xpZW50U2Vz"
    "c2lvbigpIGFzIHNlc3Npb246CiAgICAgICAgICAgIGFzeW5jIHdpdGggc2Vzc2lvbi5wb3N0KAogICAgICAgICAgICAgICAgdXJs"
    "LAogICAgICAgICAgICAgICAganNvbj17CiAgICAgICAgICAgICAgICAgICAgImhvc3RfaWQiOiBDUkVESVRfU0VMTEVSX0hPU1Rf"
    "SUQsCiAgICAgICAgICAgICAgICAgICAgImhvc3RfbmFtZSI6IEhPU1RfTkFNRSwKICAgICAgICAgICAgICAgICAgICAidGV4dCI6"
    "IHRleHQsCiAgICAgICAgICAgICAgICAgICAgInNlbmRlciI6IHNlbmRlciwKICAgICAgICAgICAgICAgICAgICAic291cmNlIjog"
    "c291cmNlLAogICAgICAgICAgICAgICAgICAgICJyZWNlaXZlZF9hdCI6IGRhdGV0aW1lLm5vdygpLmlzb2Zvcm1hdCgpLAogICAg"
    "ICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAgIHRpbWVvdXQ9YWlvaHR0cC5DbGllbnRUaW1lb3V0KHRvdGFsPTEwKSwKICAg"
    "ICAgICAgICAgKSBhcyByZXNwOgogICAgICAgICAgICAgICAgaWYgcmVzcC5zdGF0dXMgPj0gNDAwOgogICAgICAgICAgICAgICAg"
    "ICAgIHByaW50KGYi4pqg77iPIOGLjeGMq+GLiiBTTVMgd2ViaG9vayAoe3VybH0pIOGIteGIheGJsOGJtSDhiJjhiI3hiLUg4Yiw"
    "4Yyg4Y2mIEhUVFAge3Jlc3Auc3RhdHVzfSIpCiAgICBleGNlcHQgRXhjZXB0aW9uIGFzIGU6CiAgICAgICAgcHJpbnQoZiLimqDv"
    "uI8g4YuI4YuwIOGLjeGMq+GLiiBTTVMgd2ViaG9vayAoe3VybH0pIOGKpOGIteGKpOGIneGKpOGItSDhiJvhiLXhibDhiIvhiIjh"
    "jY0g4Yqg4YiN4Ymw4Yiz4Yqr4Yid4Y2mIHtlfSIpCgphc3luYyBkZWYgdHJ5X2F1dG9fbWF0Y2hfYW5kX2FwcHJvdmUodGV4dCwg"
    "Ym90LCBzZW5kZXI9Tm9uZSwgc291cmNlPSJ0ZWxlZ3JhbSIpOgogICAgIiIi4Ymg4Yqk4Yi14Yqk4Yid4Yqk4Yi1IOGMveGIgeGN"
    "jSDhi43hiLXhjKUg4Yur4YiIIOGIquGNiOGIqOGKleGItSDhiYHhjKXhiK0g4YqoIFBFTkRJTkcg4Ymy4Yqs4Ym24Ym9IOGMi+GI"
    "rSAo4Ymg4YiB4YiJ4YidIOGLmeGIruGJvSDhi43hiLXhjKUpIOGLqOGImuGLq+GImOGIs+GKreGIreGNogogICAg4Yuo4Ymw4YyI"
    "4YqY4YuNIOGIquGNiOGIqOGKleGItSDhiaLhjIjhjKXhiJ3hiJ3hjaMg4Yuo4Ymw4YiL4Yqo4YuNIOGImOGMoOGKlSDhiqjhi5sg"
    "4Ymy4Yqs4Ym1IOGLmeGIrSDhi4vhjIsg4YyL4YitIOGKq+GIjeGJsOGImOGIs+GIsOGIiCAo4YuL4YyLIOGIiOGKpeGLq+GKleGL"
    "s+GKleGLsSDhi5nhiK0g4Yuo4Ymw4YiI4Yur4YuoIOGIteGIiOGIhuGKkCkKICAgIOGJoOGIq+GItS3hiLDhiK0g4Yqg4Yut4Yy4"
    "4Yu14YmF4YidIC0g4Yut4YiF4YidIOGLqOGJsOGIs+GIs+GJsCDhiJjhjKDhipUg4Yuo4Yqo4Y2I4YiI4YqVIOGIsOGLjSDhiIjh"
    "iJjhi6vhi50g4Yut4Yio4Yuz4YiN4Y2iIiIiCgogICAgIyDhi43hjKvhi4ogd2ViaG9vayDhiqjhibDhi4vhiYDhiKjhjaMg4Yut"
    "4YiFIOGKpOGIteGKpOGIneGKpOGItSDhiaLhjIjhjKXhiJ0g4Ymj4Yut4YyI4Yyl4YidIOGLiOGLsuGLq+GLjeGKkSAo4Ymg4YyA"
    "4Yit4YmjLyDhiaDhjLjhjKXhibMpIOGLiOGLsOGLmuGLqyDhi63hjIjhiIjhiaDhjKPhiI0KICAgIGlmIE9VVEJPVU5EX1NNU19X"
    "RUJIT09LX1VSTDoKICAgICAgICBhc3luY2lvLmNyZWF0ZV90YXNrKF9yZWxheV9zbXNfdG9fZXh0ZXJuYWxfd2ViaG9vayh0ZXh0"
    "LCBzZW5kZXIsIHNvdXJjZSkpCgogICAgaWYgbm90IF9sb29rc19saWtlX2Jhbmtfc21zKHRleHQpOgogICAgICAgICMg4Yut4YiF"
    "IOGMveGIgeGNjSDhi6jhiaPhipXhiq0g4Yqk4Yi14Yqk4Yid4Yqk4Yi1IOGKoOGLreGImOGIteGIjeGInSAo4YmB4YiN4Y2NIOGJ"
    "g+GIi+GJtSDhjKDhjY3hibDhi4vhiI0pIC0gYXV0by12ZXJpZnkg4YiL4YutIOGIteGIheGJsOGJtSDhiqXhipXhi7Phi63hjYjh"
    "jKDhiK0KICAgICAgICAjIOGMqOGIreGItiDhiJvhjKPhiKvhibUg4Yqg4YqV4Yie4Yqt4Yit4Yid4Y2iIOGLreGIhSDhiLXhiIXh"
    "ibDhibXhipUgKOGIiOGIneGIs+GIjCDhi6jhiqDhi7XhiJrhipUg4Ymw4YirIOGImOGIjeGLleGKreGJtSDhiaDhiqDhjIvhjKPh"
    "iJog4YiY4YyI4Yyj4Yyg4YidKSDhi63hiqjhiIvhiqjhiIvhiI3hjaIKICAgICAgICByZXR1cm4geyJvayI6IFRydWUsICJtYXRj"
    "aGVkIjogRmFsc2UsICJyZWFzb24iOiAibm90X2Jhbmtfc21zIn0KCiAgICByZWZfY2FuZGlkYXRlcywgc21zX2Ftb3VudCA9IF9w"
    "YXJzZV9zbXModGV4dCkKICAgIHNtc19wcm92aWRlciA9IF9kZXRlY3Rfc21zX3Byb3ZpZGVyKHRleHQsIHNlbmRlcikKCiAgICBp"
    "ZiBBTExPV0VEX1NNU19TRU5ERVJTIGFuZCBzZW5kZXIgaXMgbm90IE5vbmU6CiAgICAgICAgaWYgbm90IGFueShuYW1lLmxvd2Vy"
    "KCkgaW4gc2VuZGVyLmxvd2VyKCkgZm9yIG5hbWUgaW4gQUxMT1dFRF9TTVNfU0VOREVSUyk6CiAgICAgICAgICAgIHJldHVybiB7"
    "Im9rIjogVHJ1ZSwgIm1hdGNoZWQiOiBGYWxzZSwgInJlYXNvbiI6ICJzZW5kZXJfbm90X2FsbG93ZWQifQoKICAgIGZvciByYXdf"
    "cmVmIGluIHJlZl9jYW5kaWRhdGVzOgogICAgICAgIG5vcm1fcmVmID0gX25vcm1hbGl6ZV9yZWYocmF3X3JlZikKICAgICAgICBp"
    "ZiBub3Qgbm9ybV9yZWY6CiAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgaWYgbm9ybV9yZWYgaW4gdXNlZF9zbXNfcmVmczoK"
    "ICAgICAgICAgICAgIyDhi63hiIUg4Yiq4Y2I4Yio4YqV4Yi1IOGJgOGLteGIniDhjKXhiYXhiJ0g4YiL4YutIOGLjeGIj+GIjSAt"
    "IOGJoOGLneGIneGJsyDhiqjhiJvhiIjhjY0g4Yut4YiN4YmFIOGIiOGKoOGLteGImuGKlSDhiJvhiLPhi4jhiYUgKOGLteGMjeGM"
    "jeGInuGIvSDhiJnhiqjhiKsg4YiK4YiG4YqVIOGLreGJveGIi+GIjSkKICAgICAgICAgICAgYXdhaXQgX25vdGlmeV9kdXBsaWNh"
    "dGVfcmVmX2F0dGVtcHQoYm90LCBub3JtX3JlZiwgcmF3X3JlZiwgc291cmNlLCBleHRyYV90ZXh0PWYi4Yqk4Yi14Yqk4Yid4Yqk"
    "4Yi14Y2mIHt0ZXh0WzoyMDBdfSIpCiAgICAgICAgICAgIGNvbnRpbnVlCgogICAgICAgIGZvciByb3VuZF9pZCwgciBpbiByb3Vu"
    "ZHMuaXRlbXMoKToKICAgICAgICAgICAgaWYgclsic3RhdHVzIl0gIT0gIk9QRU4iOgogICAgICAgICAgICAgICAgY29udGludWUK"
    "ICAgICAgICAgICAgZm9yIHRfbnVtLCB0X2RhdGEgaW4gclsidGlja2V0cyJdLml0ZW1zKCk6CiAgICAgICAgICAgICAgICBpZiB0"
    "X2RhdGFbInN0YXR1cyJdICE9ICJQRU5ESU5HIiBvciBub3QgdF9kYXRhWyJyZWYiXToKICAgICAgICAgICAgICAgICAgICBjb250"
    "aW51ZQogICAgICAgICAgICAgICAgaWYgX25vcm1hbGl6ZV9yZWYodF9kYXRhWyJyZWYiXSkgIT0gbm9ybV9yZWY6CiAgICAgICAg"
    "ICAgICAgICAgICAgY29udGludWUgICMg4Ym14Yqt4Yqt4YiI4YqbIChleGFjdCkg4YyN4Yyl4Yid4Yyl4Yid4Ym1IOGJpeGJuwoK"
    "ICAgICAgICAgICAgICAgICMg4Yuo4Yqt4Y2N4YurIOGLmOGLtCDhiJvhjKPhiKvhibXhjaYg4Yqk4Yi14Yqk4Yid4Yqk4YixIOGK"
    "qOGLqOGJteGKm+GLjSDhiIvhiqov4YmD4YiL4Ym1IOGKpeGKleGLsOGImOGMoyDhiqjhibPhi4jhiYDhjaMg4Ymw4Yyr4YuL4Ym5"
    "IOGKqOGImOGIqOGMoOGLjQogICAgICAgICAgICAgICAgIyDhi6jhiq3hjY3hi6sg4YuY4Yu0IOGMi+GIrSDhiJjhjIjhjKPhjKDh"
    "iJ0g4Yqg4YiI4Ymg4Ym1ICjhiIjhiJ3hiLPhiIwg4YuoIENCRSDhiqThiLXhiqThiJ3hiqThiLUg4YiIIFRlbGVCaXJyIOGJsuGK"
    "rOGJtSDhiqDhi6vhjLjhi7XhiYXhiJ0pCiAgICAgICAgICAgICAgICB0X21ldGhvZCA9IHRfZGF0YS5nZXQoInBheW1lbnRfbWV0"
    "aG9kIikKICAgICAgICAgICAgICAgIGlmIHNtc19wcm92aWRlciBhbmQgdF9tZXRob2QgYW5kIHNtc19wcm92aWRlciAhPSB0X21l"
    "dGhvZDoKICAgICAgICAgICAgICAgICAgICBjb250aW51ZQoKICAgICAgICAgICAgICAgICMg4YuL4YyLIOGIm+GMo+GIq+GJteGN"
    "piDhi63hiIUg4YuZ4YitIOGKq+GIiOGLjSDhi4vhjIsg4YyL4YitIOGLqOGJsOGIi+GKqOGLjSBTTVMg4YiY4Yyg4YqVIOGImOGM"
    "iOGMo+GMoOGInSDhiqDhiIjhiaDhibUKICAgICAgICAgICAgICAgIGlmIHNtc19hbW91bnQgaXMgTm9uZSBvciBhYnMoc21zX2Ft"
    "b3VudCAtIHJbInByaWNlIl0pID4gMC4wMToKICAgICAgICAgICAgICAgICAgICBjb250aW51ZQoKICAgICAgICAgICAgICAgIF9t"
    "YXJrX3JlZl91c2VkKG5vcm1fcmVmLCByYXdfcmVmLCByb3VuZF9pZCwgdF9udW0pCiAgICAgICAgICAgICAgICBwcmludCgKICAg"
    "ICAgICAgICAgICAgICAgICBmIvCflI4gW0FVVE8tVkVSSUZZIEFVRElUXSByb3VuZD17cm91bmRfaWR9IHRpY2tldD17dF9udW19"
    "IHJlZj17cmF3X3JlZn0gIgogICAgICAgICAgICAgICAgICAgIGYiYW1vdW50PXtzbXNfYW1vdW50fSBzb3VyY2U9e3NvdXJjZX0g"
    "c21zX3RleHQ9e3RleHQhcn0iCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgICAgICBhd2FpdCBhcHByb3ZlX3RpY2tldChy"
    "b3VuZF9pZCwgdF9udW0sIGJvdCkKICAgICAgICAgICAgICAgIHRyeToKICAgICAgICAgICAgICAgICAgICBhd2FpdCBib3Quc2Vu"
    "ZF9tZXNzYWdlKAogICAgICAgICAgICAgICAgICAgICAgICBjaGF0X2lkPUFETUlOX0lELAogICAgICAgICAgICAgICAgICAgICAg"
    "ICB0ZXh0PSgKICAgICAgICAgICAgICAgICAgICAgICAgICAgIGYi4pyFIFt7c291cmNlfV0g4YmB4Yyl4YitIHt0X251bX0gKOGL"
    "meGIrSB7cm91bmRfaWR9KSDhiaDhiKvhiLUt4Yiw4YitIOGJoOGKpOGIteGKpOGIneGKpOGItSDhiKrhjYjhiKjhipXhiLUgKHty"
    "YXdfcmVmfSkg4Yy44Yu14YmL4YiN4Y2iXG4iCiAgICAgICAgICAgICAgICAgICAgICAgICAgICBmIvCfp74g4YyN4Yyl4Yia4Yur"
    "IOGLqOGJsOGLsOGIqOGMiOGIiOGJtSDhiqThiLXhiqThiJ3hiqThiLUg4Yy94YiB4Y2N4Y2mXG48Y29kZT57dGV4dFs6MzAwXX08"
    "L2NvZGU+IgogICAgICAgICAgICAgICAgICAgICAgICApLAogICAgICAgICAgICAgICAgICAgICAgICBwYXJzZV9tb2RlPSJIVE1M"
    "IgogICAgICAgICAgICAgICAgICAgICkKICAgICAgICAgICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgICAgICAg"
    "ICAgcGFzcwogICAgICAgICAgICAgICAgcmV0dXJuIHsib2siOiBUcnVlLCAibWF0Y2hlZCI6IFRydWUsICJ0aWNrZXQiOiB0X251"
    "bSwgInJvdW5kX2lkIjogcm91bmRfaWQsICJyZWYiOiByYXdfcmVmfQoKICAgICAgICAjIOGKqOGLqOGJteGKm+GLjeGInSDhibLh"
    "iqzhibUg4YyL4YitIOGKq+GIjeGMiOGMo+GMoOGImOGNoyDhiIjhiIbhiLXhibUg4Yqt4Yis4Yuy4Ym1IOGImOGImeGLqyDhjKXh"
    "i6vhiYQgKHBlbmRpbmdfY3JlZGl0X3RvcHVwcykg4YyL4YitIOGLreGInuGKreGIqQogICAgICAgIGlmIG5vcm1fcmVmIGluIHBl"
    "bmRpbmdfY3JlZGl0X3RvcHVwczoKICAgICAgICAgICAgdG9wdXAgPSBwZW5kaW5nX2NyZWRpdF90b3B1cHNbbm9ybV9yZWZdCiAg"
    "ICAgICAgICAgIHRfbWV0aG9kID0gdG9wdXAuZ2V0KCJtZXRob2QiKQogICAgICAgICAgICBwcm92aWRlcl9vayA9IChub3Qgc21z"
    "X3Byb3ZpZGVyKSBvciAobm90IHRfbWV0aG9kKSBvciAoc21zX3Byb3ZpZGVyID09IHRfbWV0aG9kKQogICAgICAgICAgICBpZiBw"
    "cm92aWRlcl9vayBhbmQgc21zX2Ftb3VudCBpcyBub3QgTm9uZSBhbmQgYWJzKHNtc19hbW91bnQgLSB0b3B1cFsiYW1vdW50Il0p"
    "IDw9IDAuMDE6CiAgICAgICAgICAgICAgICBfbWFya19jcmVkaXRfcmVmX3VzZWQobm9ybV9yZWYsIHJhd19yZWYsIHRvcHVwWyJh"
    "bW91bnQiXSwgdF9tZXRob2QpCiAgICAgICAgICAgICAgICBkZWwgcGVuZGluZ19jcmVkaXRfdG9wdXBzW25vcm1fcmVmXQogICAg"
    "ICAgICAgICAgICAgcHJpbnQoCiAgICAgICAgICAgICAgICAgICAgZiLwn5SOIFtBVVRPLVZFUklGWSBBVURJVF0gY3JlZGl0X3Rv"
    "cHVwIGFtb3VudD17dG9wdXBbJ2Ftb3VudCddfSByZWY9e3Jhd19yZWZ9ICIKICAgICAgICAgICAgICAgICAgICBmInNvdXJjZT17"
    "c291cmNlfSBzbXNfdGV4dD17dGV4dCFyfSIKICAgICAgICAgICAgICAgICkKICAgICAgICAgICAgICAgIGF3YWl0IF9hcHBseV92"
    "ZXJpZmllZF9jcmVkaXRfdG9wdXAodG9wdXBbImFtb3VudCJdLCB0X21ldGhvZCwgcmF3X3JlZiwgYm90LCBzb3VyY2U9c291cmNl"
    "KQogICAgICAgICAgICAgICAgcmV0dXJuIHsib2siOiBUcnVlLCAibWF0Y2hlZCI6IFRydWUsICJjcmVkaXRfdG9wdXAiOiBUcnVl"
    "LCAiYW1vdW50IjogdG9wdXBbImFtb3VudCJdLCAicmVmIjogcmF3X3JlZn0KCiAgICBfcHJ1bmVfdW5tYXRjaGVkX3Ntc19sb2co"
    "KQogICAgdW5tYXRjaGVkX3Ntc19sb2cuYXBwZW5kKHsKICAgICAgICAidGV4dCI6IHRleHQsCiAgICAgICAgInNlbmRlciI6IHNl"
    "bmRlciwKICAgICAgICAic291cmNlIjogc291cmNlLAogICAgICAgICJyZWZfY2FuZGlkYXRlcyI6IHJlZl9jYW5kaWRhdGVzLAog"
    "ICAgICAgICJhbW91bnQiOiBzbXNfYW1vdW50LAogICAgICAgICJyZWNlaXZlZF9hdCI6IGRhdGV0aW1lLm5vdygpLAogICAgfSkK"
    "ICAgIHNhdmVfc3RhdGUoKQoKICAgIHBlbmRpbmdfbGlzdCA9IFtdCiAgICBmb3Igcm91bmRfaWQsIHIgaW4gcm91bmRzLml0ZW1z"
    "KCk6CiAgICAgICAgZm9yIHRfbnVtLCB0X2RhdGEgaW4gclsidGlja2V0cyJdLml0ZW1zKCk6CiAgICAgICAgICAgIGlmIHRfZGF0"
    "YVsic3RhdHVzIl0gPT0gIlBFTkRJTkciIGFuZCB0X2RhdGFbInJlZiJdOgogICAgICAgICAgICAgICAgcGVuZGluZ19saXN0LmFw"
    "cGVuZCh7InJvdW5kX2lkIjogcm91bmRfaWQsICJ0aWNrZXQiOiB0X251bSwgInJlZiI6IHRfZGF0YVsicmVmIl19KQoKICAgIHJl"
    "dHVybiB7CiAgICAgICAgIm9rIjogVHJ1ZSwKICAgICAgICAibWF0Y2hlZCI6IEZhbHNlLAogICAgICAgICJkZXRlY3RlZF9yZWZz"
    "IjogcmVmX2NhbmRpZGF0ZXMsCiAgICAgICAgImRldGVjdGVkX2Ftb3VudCI6IHNtc19hbW91bnQsCiAgICAgICAgInBlbmRpbmdf"
    "dGlja2V0cyI6IHBlbmRpbmdfbGlzdCwKICAgIH0KCmFzeW5jIGRlZiBjaGVja191bm1hdGNoZWRfc21zX2Zvcl90aWNrZXQocm91"
    "bmRfaWQsIHRfbnVtLCByZWZfaW5wdXQsIGJvdCk6CiAgICAiIiLhibDhjKvhi4vhib0g4YyI4YqTIOGIquGNiOGIqOGKleGItSDh"
    "iLLhiI3hiq0g4YmA4Yu14YieIOGKq+GIjeGMiOGMo+GMoOGImSBTTVPhi47hib0g4Yib4Yi14Ymz4YuI4Yi7IOGLjeGIteGMpSDh"
    "i6vhiIgg4Ymw4YiY4Yiz4Yiz4YutIOGIquGNiOGIqOGKleGItSDhiqvhiIgg4Ymg4Yir4Yi1LeGIsOGIrSDhiJvhjL3hi7DhiYUi"
    "IiIKICAgIF9wcnVuZV91bm1hdGNoZWRfc21zX2xvZygpCiAgICBub3JtX3JlZiA9IF9ub3JtYWxpemVfcmVmKHJlZl9pbnB1dCkK"
    "ICAgIGlmIG5vdCBub3JtX3JlZiBvciBub3JtX3JlZiBpbiB1c2VkX3Ntc19yZWZzOgogICAgICAgIHJldHVybiBGYWxzZQoKICAg"
    "IHByaWNlID0gcm91bmRzW3JvdW5kX2lkXVsicHJpY2UiXQogICAgdF9tZXRob2QgPSByb3VuZHNbcm91bmRfaWRdWyJ0aWNrZXRz"
    "Il1bdF9udW1dLmdldCgicGF5bWVudF9tZXRob2QiKQoKICAgIGZvciBlbnRyeSBpbiBsaXN0KHVubWF0Y2hlZF9zbXNfbG9nKToK"
    "ICAgICAgICBmb3IgcmF3X3JlZiBpbiBlbnRyeVsicmVmX2NhbmRpZGF0ZXMiXToKICAgICAgICAgICAgaWYgX25vcm1hbGl6ZV9y"
    "ZWYocmF3X3JlZikgIT0gbm9ybV9yZWY6CiAgICAgICAgICAgICAgICBjb250aW51ZQoKICAgICAgICAgICAgaWYgZW50cnlbImFt"
    "b3VudCJdIGlzIE5vbmUgb3IgYWJzKGVudHJ5WyJhbW91bnQiXSAtIHByaWNlKSA+IDAuMDE6CiAgICAgICAgICAgICAgICBjb250"
    "aW51ZQoKICAgICAgICAgICAgZW50cnlfcHJvdmlkZXIgPSBfZGV0ZWN0X3Ntc19wcm92aWRlcihlbnRyeS5nZXQoInRleHQiKSwg"
    "ZW50cnkuZ2V0KCJzZW5kZXIiKSkKICAgICAgICAgICAgaWYgZW50cnlfcHJvdmlkZXIgYW5kIHRfbWV0aG9kIGFuZCBlbnRyeV9w"
    "cm92aWRlciAhPSB0X21ldGhvZDoKICAgICAgICAgICAgICAgIGNvbnRpbnVlCgogICAgICAgICAgICBfbWFya19yZWZfdXNlZChu"
    "b3JtX3JlZiwgcmF3X3JlZiwgcm91bmRfaWQsIHRfbnVtKQogICAgICAgICAgICB1bm1hdGNoZWRfc21zX2xvZy5yZW1vdmUoZW50"
    "cnkpCiAgICAgICAgICAgIGF3YWl0IGFwcHJvdmVfdGlja2V0KHJvdW5kX2lkLCB0X251bSwgYm90KQogICAgICAgICAgICByZXR1"
    "cm4gVHJ1ZQoKICAgIHJldHVybiBGYWxzZQoKYXN5bmMgZGVmIGNoZWNrX3VubWF0Y2hlZF9zbXNfZm9yX29yZGVyKHJvdW5kX2lk"
    "LCB0aWNrZXRzLCByZWZfaW5wdXQsIGJvdCk6CiAgICAiIiLhibDhjKvhi4vhib0g4YyI4YqTIOGIquGNiOGIqOGKleGItSDhiLLh"
    "iI3hiq0gKOGIiOGJpeGLmSDhiYHhjKXhiK0g4Ym14YuV4Yub4YudKSDhiYDhi7XhiJ4g4Yqr4YiN4YyI4Yyj4Yyg4YiZIFNNU+GL"
    "juGJvSDhiJvhiLXhibPhi4jhiLsg4YuN4Yi14YylIOGKq+GIiCDhjKDhiYXhiIvhiIsg4Yu14Yid4YitIOGMi+GIrSDhi6jhiJrh"
    "jIjhjKPhjKDhiJ0g4Yqr4YiICiAgICDhibXhi5Xhi5vhi5kg4YuN4Yi14YylIOGLq+GIieGJteGKlSDhiYHhjKXhiK7hib0g4YiB"
    "4YiJIOGJoOGKoOGKleGLtSDhiIvhi60g4Ymg4Yir4Yi1LeGIsOGIrSDhiJvhjL3hi7DhiYUiIiIKICAgIF9wcnVuZV91bm1hdGNo"
    "ZWRfc21zX2xvZygpCiAgICBub3JtX3JlZiA9IF9ub3JtYWxpemVfcmVmKHJlZl9pbnB1dCkKICAgIGlmIG5vdCBub3JtX3JlZiBv"
    "ciBub3JtX3JlZiBpbiB1c2VkX3Ntc19yZWZzOgogICAgICAgIHJldHVybiBGYWxzZQoKICAgIHByaWNlID0gcm91bmRzW3JvdW5k"
    "X2lkXVsicHJpY2UiXQogICAgdG90YWwgPSBwcmljZSAqIGxlbih0aWNrZXRzKQogICAgdF9tZXRob2QgPSByb3VuZHNbcm91bmRf"
    "aWRdWyJ0aWNrZXRzIl1bdGlja2V0c1swXV0uZ2V0KCJwYXltZW50X21ldGhvZCIpCgogICAgZm9yIGVudHJ5IGluIGxpc3QodW5t"
    "YXRjaGVkX3Ntc19sb2cpOgogICAgICAgIGZvciByYXdfcmVmIGluIGVudHJ5WyJyZWZfY2FuZGlkYXRlcyJdOgogICAgICAgICAg"
    "ICBpZiBfbm9ybWFsaXplX3JlZihyYXdfcmVmKSAhPSBub3JtX3JlZjoKICAgICAgICAgICAgICAgIGNvbnRpbnVlCgogICAgICAg"
    "ICAgICBpZiBlbnRyeVsiYW1vdW50Il0gaXMgTm9uZSBvciBhYnMoZW50cnlbImFtb3VudCJdIC0gdG90YWwpID4gMC4wMToKICAg"
    "ICAgICAgICAgICAgIGNvbnRpbnVlCgogICAgICAgICAgICBlbnRyeV9wcm92aWRlciA9IF9kZXRlY3Rfc21zX3Byb3ZpZGVyKGVu"
    "dHJ5LmdldCgidGV4dCIpLCBlbnRyeS5nZXQoInNlbmRlciIpKQogICAgICAgICAgICBpZiBlbnRyeV9wcm92aWRlciBhbmQgdF9t"
    "ZXRob2QgYW5kIGVudHJ5X3Byb3ZpZGVyICE9IHRfbWV0aG9kOgogICAgICAgICAgICAgICAgY29udGludWUKCiAgICAgICAgICAg"
    "IF9tYXJrX3JlZl91c2VkX2dyb3VwKG5vcm1fcmVmLCByYXdfcmVmLCByb3VuZF9pZCwgdGlja2V0cykKICAgICAgICAgICAgdW5t"
    "YXRjaGVkX3Ntc19sb2cucmVtb3ZlKGVudHJ5KQogICAgICAgICAgICBhd2FpdCBhcHByb3ZlX3RpY2tldF9ncm91cChyb3VuZF9p"
    "ZCwgdGlja2V0cywgYm90KQogICAgICAgICAgICByZXR1cm4gVHJ1ZQoKICAgIHJldHVybiBGYWxzZQoKZGVmIF9yZWdpc3Rlcl9t"
    "YW51YWxfc2FsZShyb3VuZF9pZCwgdGlja2V0X251bXMsIG5hbWUsIHBob25lLCBtYW51YWxfcmVmLCBzaGFyZWRfYnV5ZXIyPU5v"
    "bmUpOgogICAgIiIi4YiI4Ymw4Yiw4Yyh4Ym1IOGJsuGKrOGJtSjhibbhib0pICjhiqDhipXhi7Ug4YuI4Yut4YidIOGJpeGLmSkg"
    "4Ymg4Yql4YyFIOGIveGLq+GMrSDhiJ3hi53hjIjhiaMg4Yuo4Yia4Yur4Yqo4YqT4YuN4YqVIGhlbHBlcuGNogogICAg4Yi14Yqs"
    "4Ym1IOGKqOGIhuGKkCAoVHJ1ZSwgSFRNTCDhiJjhiI3hiqXhiq3hibUpIOGKq+GIjeGIhuGKkCAoRmFsc2UsIEhUTUwg4Yuo4Yi1"
    "4YiF4Ymw4Ym1IOGImOGIjeGKpeGKreGJtSkg4Yut4YiY4YiN4Yiz4YiN4Y2iCiAgICDhiaXhi5kg4YmB4Yyl4Yiu4Ym9IOGKqOGJ"
    "sOGIsOGMoSAo4Ymw4YmA4YqT4YyF4Ym2IOGLqOGJsOGImOGIqOGMoSkg4YiB4YiJ4YidIOGJoOGKoOGKleGLtSDhiIvhi60g4YiI"
    "4Ymw4YiY4Yiz4Yiz4YutIOGMiOGLoiBTT0xEIOGLreGIhuGKk+GIieGNowogICAg4Yql4YqTIFJlZmVyZW5jZSBJRCDhiqvhiIgg"
    "4YiI4YiB4YiJ4YidIOGJoOGMi+GIqyAoZ3JvdXApIOGLreGImOGLmOGMiOGJo+GIjeGNogogICAgc2hhcmVkX2J1eWVyMiDhiqjh"
    "ibDhiLDhjKAgKOGKkOGMoOGIiyDhibLhiqzhibUg4Yml4Ym7IOGIsuGIhuGKlSkg4Ymy4Yqs4YmxIOGJoDIg4Yiw4YuNIOGMjeGI"
    "m+GIvSDhi4vhjIsg4Yuo4Ymw4Yi44YygIOGJsOGLsOGIreGMjiDhi63hiJjhi5jhjIjhiaPhiI0KICAgICjwn5+jIOGJoOGNjeGI"
    "reGMjeGIreGMjSDhi43hiLXhjKUg4Yut4Ymz4Yur4YiNKeGNoiIiIgogICAgdGlja2V0X251bXMgPSBsaXN0KHRpY2tldF9udW1z"
    "KQogICAgaWYgbm90IHRpY2tldF9udW1zOgogICAgICAgIHJldHVybiBGYWxzZSwgIuKaoO+4jyDhiJ3hipXhiJ0g4YmB4Yyl4Yit"
    "IOGKoOGIjeGJsOGImOGIqOGMoOGIneGNoiIsIEZhbHNlCgogICAgaWYgcm91bmRfaWQgbm90IGluIHJvdW5kcyBvciByb3VuZHNb"
    "cm91bmRfaWRdWyJzdGF0dXMiXSAhPSAiT1BFTiI6CiAgICAgICAgcmV0dXJuIEZhbHNlLCAi4p2MIOGKpeGKleGLsOGLmuGIhSDh"
    "i6vhiIgg4YqV4YmBIOGLmeGIrSDhiqDhiI3hibDhjIjhipjhiJ3hjaIgL3JvdW5kcyDhi63hiJjhiI3hiqjhibHhjaIiLCBGYWxz"
    "ZQoKICAgIGlmIGhvc3RfcGF1c2VkOgogICAgICAgIHJlYXNvbl9saW5lID0gKAogICAgICAgICAgICBmIvCfkrMg4Yid4Yqt4YqV"
    "4Yur4Ym14Y2mIOGKreGIrOGLsuGJtSDhiqDhiI3hiYvhiI0gKOGLqOGJgOGIqCDhiJLhiLPhiaXhjaYge2hvc3RfY3JlZGl0Wydi"
    "YWxhbmNlJ106LjJmfSDhiaXhiK0p4Y2iIC9hZGRjcmVkaXQg4Ymw4Yyg4YmF4YiY4YuNIOGIkuGIs+GJpSDhi63hiJnhiInhjaIi"
    "CiAgICAgICAgICAgIGlmIGhvc3RfcGF1c2VkX3JlYXNvbiA9PSAiY3JlZGl0IgogICAgICAgICAgICBlbHNlICLihLnvuI8g4Yi9"
    "4Yur4YytIOGJoOGIm+GLleGKqOGIi+GLiiDhibDhiYbhjKPhjKPhiKogKE11bHRpLUhvc3QgQ29udHJvbGxlcikg4YiI4YyK4Yuc"
    "4YuNIOGJhuGIn+GIjeGNoiIKICAgICAgICApCiAgICAgICAgcmV0dXJuIEZhbHNlLCBmIuKPuO+4jyA8Yj7hiL3hi6vhjK0g4YiI"
    "4YyK4Yuc4YuNIOGJhuGIn+GIjeGNojwvYj5cblxue3JlYXNvbl9saW5lfSIsIEZhbHNlCgogICAgbnVtX3RpY2tldHMgPSByb3Vu"
    "ZHNbcm91bmRfaWRdWyJudW1fdGlja2V0cyJdCiAgICB0aWNrZXRzX2RhdGEgPSByb3VuZHNbcm91bmRfaWRdWyJ0aWNrZXRzIl0K"
    "CiAgICBvdXRfb2ZfcmFuZ2UgPSBbdG4gZm9yIHRuIGluIHRpY2tldF9udW1zIGlmIHRuIDwgMSBvciB0biA+IG51bV90aWNrZXRz"
    "XQogICAgaWYgb3V0X29mX3JhbmdlOgogICAgICAgIHJldHVybiBGYWxzZSwgZiLimqDvuI8g4YmB4Yyl4Yiu4Ym9IOGKqDEg4Yql"
    "4Yi14YqoIHtudW1fdGlja2V0c30g4YiY4Yqr4Yqo4YiNIOGImOGIhuGKlSDhiqDhiIjhiaPhibjhi43hjaIgKHsnLCAnLmpvaW4o"
    "c3RyKHgpIGZvciB4IGluIG91dF9vZl9yYW5nZSl9IOGJteGKreGKreGIjSDhiqDhi63hi7DhiInhiJ0pIiwgRmFsc2UKCiAgICBu"
    "b3RfYXZhaWxhYmxlID0gW3RuIGZvciB0biBpbiB0aWNrZXRfbnVtcyBpZiB0aWNrZXRzX2RhdGEuZ2V0KHRuLCB7fSkuZ2V0KCJz"
    "dGF0dXMiKSAhPSAiQVZBSUxBQkxFIl0KICAgIGlmIG5vdF9hdmFpbGFibGU6CiAgICAgICAgbnVtcyA9ICIsICIuam9pbihzdHIo"
    "eCkgZm9yIHggaW4gbm90X2F2YWlsYWJsZSkKICAgICAgICByZXR1cm4gRmFsc2UsICgKICAgICAgICAgICAgZiLinYwg4YmB4Yyl"
    "4YitKOGJtuGJvSkge251bXN9ICjhi5nhiK0ge3JvdW5kX2lkfSkg4Yqo4Yql4YqV4YyN4Yuy4YiFIOGLq+GIjeGJsOGLq+GLmSAo"
    "QVZBSUxBQkxFKSDhiqDhi63hi7DhiInhiJ3hjaIgIgogICAgICAgICAgICBmIuGImOGMgOGImOGIquGLqyAvbWFudWFsY2FuY2Vs"
    "IHtyb3VuZF9pZH0gJmx0O+GJgeGMpeGIrSZndDsg4Ymw4Yyg4YmF4YiY4YuNIOGLq+GIjeGJsOGLq+GLmSDhi6vhi7XhiK3hjJPh"
    "ibjhi43hjaIiCiAgICAgICAgKSwgRmFsc2UKCiAgICBub3JtX21hbnVhbF9yZWYgPSBfbm9ybWFsaXplX3JlZihtYW51YWxfcmVm"
    "KSBpZiBtYW51YWxfcmVmIGVsc2UgIiIKICAgIGlmIG5vcm1fbWFudWFsX3JlZiBhbmQgbm9ybV9tYW51YWxfcmVmIGluIHVzZWRf"
    "c21zX3JlZnM6CiAgICAgICAgaW5mbyA9IHVzZWRfc21zX3JlZnNbbm9ybV9tYW51YWxfcmVmXQogICAgICAgIHJldHVybiBGYWxz"
    "ZSwgKAogICAgICAgICAgICAi8J+aqyA8Yj7hi63hiIUgUmVmZXJlbmNlIElEIOGKoOGIteGJgOGLteGIniDhjKXhiYXhiJ0g4YiL"
    "4YutIOGLjeGIj+GIjeGNojwvYj5cblxuIgogICAgICAgICAgICBmIvCflKIgUmVmZXJlbmNlIElE4Y2mIDxjb2RlPnttYW51YWxf"
    "cmVmfTwvY29kZT5cbiIKICAgICAgICAgICAgZiLwn46f77iPIOGJgOGLsOGInSDhiaXhiI4g4Yuo4Ymw4Yyg4YmA4YiY4Ymg4Ym1"
    "4Y2mIOGLmeGIrSB7aW5mby5nZXQoJ3JvdW5kX2lkJyl9IOGJgeGMpeGIrSB7aW5mby5nZXQoJ3RpY2tldF9udW0nKX1cbiIKICAg"
    "ICAgICAgICAgIuKdjCDhi63hiIXhipUgUmVmZXJlbmNlIElEIOGIiOGIjOGIiyDhibLhiqzhibUg4YiY4Yyg4YmA4YidIOGKoOGL"
    "reGJu+GIjeGIneGNoiIKICAgICAgICApLCBGYWxzZQoKICAgIGZvciB0biBpbiB0aWNrZXRfbnVtczoKICAgICAgICB0aWNrZXRz"
    "X2RhdGFbdG5dID0gewogICAgICAgICAgICAic3RhdHVzIjogIlNPTEQiLAogICAgICAgICAgICAidXNlcl9pZCI6IE5vbmUsCiAg"
    "ICAgICAgICAgICJ1c2VybmFtZSI6ICLwn5OeIOGJoOGIteGIjeGKrSDhi6jhibDhjIjhi5siLAogICAgICAgICAgICAicmVmIjog"
    "bWFudWFsX3JlZiBvciAiTUFOVUFMLVBIT05FLVNBTEUiLAogICAgICAgICAgICAiZXhwaXJlc19hdCI6IE5vbmUsCiAgICAgICAg"
    "ICAgICJidXllcl9uYW1lIjogbmFtZSwKICAgICAgICAgICAgImJ1eWVyX3Bob25lIjogcGhvbmUsCiAgICAgICAgICAgICJwYXlt"
    "ZW50X21ldGhvZCI6IE5vbmUsCiAgICAgICAgICAgICJyZWZfYXR0ZW1wdHMiOiAwLAogICAgICAgICAgICAic2hhcmVkIjogYm9v"
    "bChzaGFyZWRfYnV5ZXIyKSwKICAgICAgICAgICAgImJ1eWVyX25hbWUyIjogc2hhcmVkX2J1eWVyMlsibmFtZSJdIGlmIHNoYXJl"
    "ZF9idXllcjIgZWxzZSBOb25lLAogICAgICAgICAgICAiYnV5ZXJfcGhvbmUyIjogc2hhcmVkX2J1eWVyMlsicGhvbmUiXSBpZiBz"
    "aGFyZWRfYnV5ZXIyIGVsc2UgTm9uZSwKICAgICAgICB9CiAgICBfY2xlYXJfcmVqZWN0ZWRfcmVmX2VudHJpZXMocm91bmRfaWQs"
    "IHRpY2tldF9udW1zKQoKICAgICMgUmVmZXJlbmNlIElEIOGKqOGJsOGIsOGMoCDhi4jhi7Lhi6vhi43hipEg4YuI4YuwIHVzZWRf"
    "c21zX3JlZnMg4Yyo4Yid4Yit4Y2kIOGIteGIiOGLmuGIhSDhiIzhiIsg4Ymw4Yyr4YuL4Ym9IOGJouGIjeGKqOGLjSDhiqXhipXh"
    "i7DhibDhjKDhiYDhiJgg4Yut4Ymz4YuI4YmD4YiNCiAgICBpZiBub3JtX21hbnVhbF9yZWY6CiAgICAgICAgaWYgbGVuKHRpY2tl"
    "dF9udW1zKSA+IDE6CiAgICAgICAgICAgIF9tYXJrX3JlZl91c2VkX2dyb3VwKG5vcm1fbWFudWFsX3JlZiwgbWFudWFsX3JlZiwg"
    "cm91bmRfaWQsIHRpY2tldF9udW1zKQogICAgICAgIGVsc2U6CiAgICAgICAgICAgIHVzZWRfc21zX3JlZnNbbm9ybV9tYW51YWxf"
    "cmVmXSA9IHsKICAgICAgICAgICAgICAgICJyYXdfcmVmIjogbWFudWFsX3JlZiwKICAgICAgICAgICAgICAgICJyb3VuZF9pZCI6"
    "IHJvdW5kX2lkLAogICAgICAgICAgICAgICAgInRpY2tldF9udW0iOiB0aWNrZXRfbnVtc1swXSwKICAgICAgICAgICAgICAgICJ0"
    "aWNrZXRfbnVtcyI6IGxpc3QodGlja2V0X251bXMpLAogICAgICAgICAgICAgICAgInVzZXJfaWQiOiBOb25lLAogICAgICAgICAg"
    "ICAgICAgImJ1eWVyX25hbWUiOiBuYW1lLAogICAgICAgICAgICAgICAgInBheW1lbnRfbWV0aG9kIjogTm9uZSwKICAgICAgICAg"
    "ICAgICAgICJ1c2VkX2F0IjogZGF0ZXRpbWUubm93KCksCiAgICAgICAgICAgIH0KCiAgICBzaG91bGRfbm90aWZ5ID0gX2RlZHVj"
    "dF9ob3N0X2NvbW1pc3Npb24ocm91bmRfaWQsIHRpY2tldF9udW1zKQogICAgc2F2ZV9zdGF0ZSgpCgogICAgcmVmX2xpbmUgPSBm"
    "Ilxu8J+UoiBSZWZlcmVuY2UgSUThjaYgPGNvZGU+e21hbnVhbF9yZWZ9PC9jb2RlPiAo4YuI4YuwIFVzZWQgUmVmZXJlbmNlcyDh"
    "ibDhiJjhi53hjI3hiafhiI0pIiBpZiBub3JtX21hbnVhbF9yZWYgZWxzZSAiXG7wn5SiIFJlZmVyZW5jZSBJROGNpiDhiqDhiI3h"
    "ibDhiLDhjKDhiJ0iCiAgICB0bnNfbGFiZWwgPSAiLCAiLmpvaW4oc3RyKHgpIGZvciB4IGluIHRpY2tldF9udW1zKQogICAgdG5f"
    "d29yZCA9ICLhiYHhjKXhiK0iIGlmIGxlbih0aWNrZXRfbnVtcykgPT0gMSBlbHNlICLhiYHhjKXhiK7hib0iCiAgICBwcmljZSA9"
    "IHJvdW5kc1tyb3VuZF9pZF1bInByaWNlIl0KICAgIHRvdGFsID0gcHJpY2UgKiBsZW4odGlja2V0X251bXMpCiAgICBpZiBzaGFy"
    "ZWRfYnV5ZXIyOgogICAgICAgIGhhbGYgPSBwcmljZSAvIDIKICAgICAgICBtc2cgPSAoCiAgICAgICAgICAgIGYi4pyFIPCfn6Mg"
    "e3RuX3dvcmR9IHt0bnNfbGFiZWx9ICjhi5nhiK0ge3JvdW5kX2lkfSkg4Ymg4Yi14YiN4YqtIOGMjeGLoiDhiIgyIOGIsOGLjSDh"
    "jI3hiJvhiL0g4YuL4YyLIOGJoOGKpeGMhSDhibDhiJjhi53hjI3hiafhiI3hjaJcbiIKICAgICAgICAgICAgZiLwn5GkIDHhipsg"
    "4YyI4Yui4Y2mIDxiPntuYW1lfTwvYj4gKHtwaG9uZX0pIOKAlCB7aGFsZjouMGZ9IOGJpeGIrVxuIgogICAgICAgICAgICBmIvCf"
    "kaQgMuGKmyDhjIjhi6LhjaYgPGI+e3NoYXJlZF9idXllcjJbJ25hbWUnXX08L2I+ICh7c2hhcmVkX2J1eWVyMlsncGhvbmUnXX0p"
    "IOKAlCB7aGFsZjouMGZ9IOGJpeGIrVxuIgogICAgICAgICAgICBmIvCfkrUg4Yyg4YmF4YiL4YiLIOGLi+GMi+GNpiB7dG90YWw6"
    "LjBmfSDhiaXhiK0iCiAgICAgICAgICAgICsgcmVmX2xpbmUKICAgICAgICApCiAgICBlbHNlOgogICAgICAgIG1zZyA9ICgKICAg"
    "ICAgICAgICAgZiLinIUge3RuX3dvcmR9IHt0bnNfbGFiZWx9ICjhi5nhiK0ge3JvdW5kX2lkfSkg4Ymg4Yi14YiN4YqtIOGMjeGL"
    "oiDhiIg8Yj57bmFtZX08L2I+ICh7cGhvbmV9KSDhiaDhiqXhjIUg4Ymw4YiY4Yud4YyN4Ymn4YiN4Y2iXG4iCiAgICAgICAgICAg"
    "IGYi8J+StSDhjKDhiYXhiIvhiIsg4YuL4YyL4Y2mIHt0b3RhbDouMGZ9IOGJpeGIrSAoe3ByaWNlOi4wZn0g4Yml4YitIMOXIHts"
    "ZW4odGlja2V0X251bXMpfSkiCiAgICAgICAgICAgICsgcmVmX2xpbmUKICAgICAgICApCiAgICByZXR1cm4gVHJ1ZSwgbXNnLCBz"
    "aG91bGRfbm90aWZ5Cgphc3luYyBkZWYgX3N0YXJ0X21hbnVhbHNlbGxfdGlja2V0X3BpY2tlcihjb250ZXh0LCByaWQpOgogICAg"
    "IiIiL21hbnVhbHNlbGwgPOGLmeGIrT4g4Yml4Ym7IOGIsuGIi+GKrSDhi4jhi63hiJ0gwqvwn5KwIOGIiOGLqOGJteGKm+GLjSDh"
    "i5nhiK0uLi7CuyDhiYHhiI3hjY0g4Yiy4Yyr4YqRIOGLqOGImuGMgOGIneGIrSDhi6jhibLhiqzhibUg4Y2N4Yit4YyN4Yit4YyN"
    "IOGImOGIq+GMrSIiIgogICAgdGlja2V0cyA9IHJvdW5kcy5nZXQocmlkLCB7fSkuZ2V0KCJ0aWNrZXRzIiwge30pCiAgICBpZiBu"
    "b3QgdGlja2V0czoKICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoY2hhdF9pZD1BRE1JTl9JRCwgdGV4dD0i"
    "4oS577iPIOGJoOGLmuGIhSDhi5nhiK0g4Ymy4Yqs4Ym1IOGLqOGIiOGIneGNoiIpCiAgICAgICAgcmV0dXJuCiAgICAjIOGKoOGL"
    "suGItSDhi5nhiK0g4Yiy4Yqo4Y2I4Ym1IOGKq+GIiOGNiCDhjIrhi5wg4Yuo4YmA4YioIOGIneGIreGMqy/hiIHhipThibMg4Yql"
    "4YqV4Yuz4Yut4YqW4YitIOGKoOGMveGLswogICAgbWFudWFsc2VsbF9jYXJ0LnBvcChBRE1JTl9JRCwgTm9uZSkKICAgIG1hbnVh"
    "bHNlbGxfc3RhdGUucG9wKEFETUlOX0lELCBOb25lKQogICAga2IgPSBfYnVpbGRfbWFudWFsX3NlbGxfa2V5Ym9hcmQocmlkLCBz"
    "ZXQoKSkKICAgIGdyaWRfbXNnID0gYXdhaXQgY29udGV4dC5ib3Quc2VuZF9tZXNzYWdlKAogICAgICAgIGNoYXRfaWQ9QURNSU5f"
    "SUQsCiAgICAgICAgdGV4dD1fbWFudWFsX3NlbGxfZ3JpZF90ZXh0KHJpZCwgdGlja2V0cywgc2V0KCkpLAogICAgICAgIHBhcnNl"
    "X21vZGU9IkhUTUwiLAogICAgICAgIHJlcGx5X21hcmt1cD1JbmxpbmVLZXlib2FyZE1hcmt1cChrYikKICAgICkKICAgIGFjdGlv"
    "bl9tc2cgPSBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoCiAgICAgICAgY2hhdF9pZD1BRE1JTl9JRCwKICAgICAgICB0"
    "ZXh0PV9tYW51YWxfc2VsbF9hY3Rpb25fdGV4dChyaWQsIHNldCgpKSwKICAgICkKICAgIG1hbnVhbHNlbGxfY2FydFtBRE1JTl9J"
    "RF0gPSB7CiAgICAgICAgInJvdW5kX2lkIjogcmlkLAogICAgICAgICJ0aWNrZXRzIjogc2V0KCksCiAgICAgICAgImdyaWRfbWVz"
    "c2FnZV9pZCI6IGdldGF0dHIoZ3JpZF9tc2csICJtZXNzYWdlX2lkIiwgTm9uZSksCiAgICAgICAgImFjdGlvbl9tZXNzYWdlX2lk"
    "IjogZ2V0YXR0cihhY3Rpb25fbXNnLCAibWVzc2FnZV9pZCIsIE5vbmUpLAogICAgfQoKCmFzeW5jIGRlZiBtYW51YWxfc2VsbCh1"
    "cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhiqDhi7XhiJrhipUg4Ymg"
    "4Yi14YiN4YqtIOGMjeGLoiDhiIvhi7DhiKjhjIgg4Yuw4YqV4Ymg4YqbIOGJgeGMpeGIrSAo4YuI4Yut4YidIOGJpeGLmSDhiYHh"
    "jKXhiK7hib0pIOGJoOGKpeGMhSDhi6jhiJrhiJjhi5jhjI3hiaXhiaDhibUg4Ym14YuV4Yub4YudCiAgICDhiqDhjKDhiYPhiYDh"
    "iJ3hjaYgL21hbnVhbHNlbGwgPOGLmeGIrT4gPOGJgeGMpeGIrT4gPOGIteGInT4gPOGIteGIjeGKrT4iIiIKICAgIGlmIHVwZGF0"
    "ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAgICB1aWQgPSB1cGRhdGUubWVzc2Fn"
    "ZS5mcm9tX3VzZXIuaWQKICAgIGNsZWFyZWQgPSBfY2FuY2VsX290aGVyX2FkbWluX3Rhc2tzKHVpZCwga2VlcD0ibWFudWFsc2Vs"
    "bCIpCiAgICBhd2FpdCBfbm90aWZ5X2NhbmNlbGxlZF90YXNrcyhjb250ZXh0LmJvdCwgdWlkLCBjbGVhcmVkKQoKICAgIGFyZ3Mg"
    "PSBjb250ZXh0LmFyZ3MKICAgIGlmIG5vdCBhcmdzOgogICAgICAgIGF3YWl0IF9zZW5kX3JvdW5kX3BpY2tlcigKICAgICAgICAg"
    "ICAgdXBkYXRlLCBjb250ZXh0LCAibWFudWFsc2VsbHJvdW5kIiwKICAgICAgICAgICAgW3JpZCBmb3IgcmlkLCByIGluIHJvdW5k"
    "cy5pdGVtcygpIGlmIHJbInN0YXR1cyJdID09ICJPUEVOIl0sCiAgICAgICAgICAgICLwn5KwIOGIiOGLqOGJteGKm+GLjSDhi5nh"
    "iK0g4Ymg4Yql4YyFIOGIveGLq+GMrSDhiJjhiJjhi53hjIjhiaUg4Yut4Y2I4YiN4YyL4YiJPyIsCiAgICAgICAgICAgICLihLnv"
    "uI8g4Ymg4Yqg4YiB4YqRIOGIsOGLk+GJtSDhipXhiYEgKE9QRU4pIOGLmeGIrSDhi6jhiIjhiJ3hjaIiCiAgICAgICAgKQogICAg"
    "ICAgIHJldHVybgoKICAgIHRyeToKICAgICAgICByb3VuZF9pZCA9IGludChhcmdzWzBdKQogICAgZXhjZXB0IFZhbHVlRXJyb3I6"
    "CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4pqg77iPIOGJteGKreGKreGIiOGKmyDhi6jhi5nhiK0g"
    "4YmB4Yyl4YitIOGLq+GIteGMiOGJoeGNoiIpCiAgICAgICAgcmV0dXJuCgogICAgaWYgcm91bmRfaWQgbm90IGluIHJvdW5kczoK"
    "ICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLinYwg4Yql4YqV4Yuw4Yua4YiFIOGLq+GIiCDhi5nhiK0g"
    "4Yqg4YiN4Ymw4YyI4YqY4Yid4Y2iIikKICAgICAgICByZXR1cm4KCiAgICBpZiBsZW4oYXJncykgPCAyOgogICAgICAgICMg4YuZ"
    "4YitIOGJpeGJuyDhibDhiLDhjKXhibfhiI0gLT4g4YmA4Yyl4YiOIOGLqOGJsuGKrOGJtSDhiYHhjKXhiK0o4Ym24Ym9KSDhiaDh"
    "iYHhiI3hjY0g4Y2N4Yit4YyN4Yit4YyNIOGLreGMoOGLqOGJg+GIjQogICAgICAgIGF3YWl0IF9zdGFydF9tYW51YWxzZWxsX3Rp"
    "Y2tldF9waWNrZXIoY29udGV4dCwgcm91bmRfaWQpCiAgICAgICAgcmV0dXJuCgogICAgdHJ5OgogICAgICAgIHRfbnVtcyA9IFtp"
    "bnQoeCkgZm9yIHggaW4gYXJnc1sxXS5zcGxpdCgiLCIpIGlmIHguc3RyaXAoKSAhPSAiIl0KICAgICAgICBpZiBub3QgdF9udW1z"
    "OgogICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yCiAgICBleGNlcHQgVmFsdWVFcnJvcjoKICAgICAgICBhd2FpdCB1cGRhdGUu"
    "bWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Ym14Yqt4Yqt4YiI4YqbIOGLqOGJsuGKrOGJtSDhiYHhjKXhiK0o4Ym24Ym9KSDh"
    "i6vhiLXhjIjhiaHhjaIgKOGJpeGLmSDhiqvhiIkg4Ymg4Yqu4YibIOGLreGIiOGLq+GLqeGNpiA0NSw0Niw0NykiKQogICAgICAg"
    "IHJldHVybgoKICAgIGlmIGxlbihhcmdzKSA8IDM6CiAgICAgICAgIyDhi5nhiK0gKyDhibLhiqzhibUg4Yml4Ym7IOGJsOGIsOGM"
    "peGJt+GIjSAtPiDhiYDhjKXhiI4g4Yi14YidIOGJpeGJuyDhiaDhjL3hiIHhjY0g4Yut4Yyg4Yuo4YmD4YiNCiAgICAgICAgbWFu"
    "dWFsc2VsbF9jYXJ0LnBvcCh1aWQsIE5vbmUpCiAgICAgICAgbWFudWFsc2VsbF9zdGF0ZVt1aWRdID0geyJyb3VuZF9pZCI6IHJv"
    "dW5kX2lkLCAidGlja2V0cyI6IHRfbnVtcywgInN0ZXAiOiAibmFtZSJ9CiAgICAgICAgdG5zX2xhYmVsID0gIiwgIi5qb2luKHN0"
    "cih4KSBmb3IgeCBpbiB0X251bXMpCiAgICAgICAgcHJpY2UgPSByb3VuZHNbcm91bmRfaWRdWyJwcmljZSJdCiAgICAgICAgdG90"
    "YWwgPSBwcmljZSAqIGxlbih0X251bXMpCiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAg"
    "ICAgZiLwn5KwIDxiPntyb3VuZF9sYWJlbChyb3VuZF9pZCl9IOKAlCDhiYHhjKXhiK0o4Ym24Ym9KSB7dG5zX2xhYmVsfTwvYj5c"
    "biIKICAgICAgICAgICAgZiLwn5K1IOGMoOGJheGIi+GIiyDhi4vhjIvhjaYge3RvdGFsOi4wZn0g4Yml4YitICh7cHJpY2U6LjBm"
    "fSDhiaXhiK0gw5cge2xlbih0X251bXMpfSlcblxuIgogICAgICAgICAgICAi8J+RpCDhiqXhiaPhiq3hi44g4Yuo4YyI4Yui4YuN"
    "4YqVIDxiPuGIteGInTwvYj4g4Yml4Ym7IOGLq+GIteGMiOGJoeGNolxuIgogICAgICAgICAgICAi4YiI4Yid4Yiz4YiM4Y2mIDxj"
    "b2RlPuGKoOGJoOGJoCDhiqjhiaDhi7A8L2NvZGU+XG5cbiIKICAgICAgICAgICAgIuKEue+4jyDhiIjhiJjhiLDhiKjhi50gL2Nh"
    "bmNlbCDhi63hiIvhiqnhjaIiLAogICAgICAgICAgICBwYXJzZV9tb2RlPSJIVE1MIgogICAgICAgICkKICAgICAgICByZXR1cm4K"
    "CiAgICBpZiBsZW4oYXJncykgPCA0OgogICAgICAgICMg4YuZ4YitICsg4Ymy4Yqs4Ym1ICsgKOGMiOGKkyDhi6vhiI3hibDhjKDh"
    "ipPhiYDhiYAg4Yi14YidKSDhibDhiLDhjKXhibfhiI0gLT4g4Yuo4Ymw4Yy74Y2I4YuN4YqVIOGKpeGKleGLsCDhiLXhiJ0g4YuI"
    "4Yi14Yuw4YqVIOGIteGIjeGKrSDhiaXhibsg4Yql4YqV4Yyg4Yut4YmFCiAgICAgICAgbmFtZV9zb19mYXIgPSAiICIuam9pbihh"
    "cmdzWzI6XSkuc3RyaXAoKQogICAgICAgIG1hbnVhbHNlbGxfY2FydC5wb3AodWlkLCBOb25lKQogICAgICAgIG1hbnVhbHNlbGxf"
    "c3RhdGVbdWlkXSA9IHsicm91bmRfaWQiOiByb3VuZF9pZCwgInRpY2tldHMiOiB0X251bXMsICJzdGVwIjogInBob25lIiwgIm5h"
    "bWUiOiBuYW1lX3NvX2Zhcn0KICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICBmIvCf"
    "k7Eg4Yqg4YiY4Yiw4YyN4YqT4YiI4YiB4Y2iIOGKoOGIgeGKlSDhi6h7bmFtZV9zb19mYXJ9IOGIteGIjeGKrSDhiYHhjKXhiK0g"
    "4Yur4Yi14YyI4Ymh4Y2mXG4iCiAgICAgICAgICAgICLhiIjhiJ3hiLPhiIzhjaYgMDkxMjM0NTY3OFxuXG4iCiAgICAgICAgICAg"
    "ICLihLnvuI8g4YiI4YiY4Yiw4Yio4YudIC9jYW5jZWwg4Yut4YiL4Yqp4Y2iIgogICAgICAgICkKICAgICAgICByZXR1cm4KCiAg"
    "ICAjIOGJgOGMpeGJsyAvbWFudWFsc2VsbCDhibXhi5Xhi5vhi50g4YqoUmVmZXJlbmNlIElEIOGMi+GIrSDhi4jhi63hiJ0g4Yur"
    "4YiIIFJlZmVyZW5jZSBJRCDhiIrhiLDhiKsg4Yut4Ym94YiL4YiNCiAgICBpZiBsZW4oYXJncykgPj0gNToKICAgICAgICBwaG9u"
    "ZSA9IGFyZ3NbLTJdCiAgICAgICAgbmFtZSA9ICIgIi5qb2luKGFyZ3NbMjotMl0pLnN0cmlwKCkKICAgICAgICBtYW51YWxfcmVm"
    "ID0gYXJnc1stMV0uc3RyaXAoKQogICAgZWxzZToKICAgICAgICBwaG9uZSA9IGFyZ3NbLTFdCiAgICAgICAgbmFtZSA9ICIgIi5q"
    "b2luKGFyZ3NbMjotMV0pLnN0cmlwKCkKICAgICAgICBtYW51YWxfcmVmID0gIiIKCiAgICBpZiBub3QgbmFtZToKICAgICAgICBh"
    "d2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Yql4Ymj4Yqt4YuOIOGLqOGMiOGLouGLjeGKlSDhiLXhiJ0g"
    "4Yur4Yi14YyI4Ymh4Y2iIikKICAgICAgICByZXR1cm4KCiAgICBpZiBub3QgUEhPTkVfUEFUVEVSTi5tYXRjaChwaG9uZS5yZXBs"
    "YWNlKCIgIiwgIiIpKToKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Yuo4Yi14YiN4Yqt"
    "IOGJgeGMpeGIqSDhibXhiq3hiq3hiI0g4Yqg4Yut4YiY4Yi14YiN4Yid4Y2iICjhiIjhiJ3hiLPhiIzhjaYgMDkxMjM0NTY3OCki"
    "KQogICAgICAgIHJldHVybgoKICAgIG9rLCBtc2csIHNob3VsZF9ub3RpZnkgPSBfcmVnaXN0ZXJfbWFudWFsX3NhbGUocm91bmRf"
    "aWQsIHRfbnVtcywgbmFtZSwgcGhvbmUsIG1hbnVhbF9yZWYpCiAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KG1z"
    "ZywgcGFyc2VfbW9kZT0iSFRNTCIpCiAgICBpZiBzaG91bGRfbm90aWZ5OgogICAgICAgIGF3YWl0IF9ub3RpZnlfc3VwZXJfYWRt"
    "aW5fbG93X2NyZWRpdChjb250ZXh0LmJvdCkKCmFzeW5jIGRlZiBfc3RhcnRfbWFudWFsY2FuY2VsX3RpY2tldF9waWNrZXIoY29u"
    "dGV4dCwgcmlkKToKICAgICIiIi9tYW51YWxjYW5jZWwgPOGLmeGIrT4g4Yml4Ym7IOGIsuGIi+GKrSDhi4jhi63hiJ0g4YmB4YiN"
    "4Y2NIOGIsuGMq+GKkSDhi6jhibXhipvhi40g4YmB4Yyl4YitIOGLq+GIjeGJsOGLq+GLmSDhiqXhipXhi7DhiJrhi7DhiKjhjI0g"
    "4Yuo4Yia4Yyg4Yut4YmFIGhlbHBlciIiIgogICAgdGlja2V0cyA9IHJvdW5kcy5nZXQocmlkLCB7fSkuZ2V0KCJ0aWNrZXRzIiwg"
    "e30pCiAgICBjYW5kaWRhdGVzID0gc29ydGVkKHRfbnVtIGZvciB0X251bSwgdCBpbiB0aWNrZXRzLml0ZW1zKCkgaWYgdFsic3Rh"
    "dHVzIl0gaW4gKCJTT0xEIiwgIlBFTkRJTkciKSkKICAgIGlmIG5vdCBjYW5kaWRhdGVzOgogICAgICAgIGF3YWl0IGNvbnRleHQu"
    "Ym90LnNlbmRfbWVzc2FnZShjaGF0X2lkPUFETUlOX0lELCB0ZXh0PSLihLnvuI8g4YiI4Yua4YiFIOGLmeGIrSDhi6vhiI3hibDh"
    "i6vhi5kg4Yuo4Yia4Yuw4Yio4YyNIOGJgeGMpeGIrSDhi6jhiIjhiJ3hjaIiKQogICAgICAgIHJldHVybgogICAga2IgPSBbCiAg"
    "ICAgICAgW0lubGluZUtleWJvYXJkQnV0dG9uKGYi4YmB4Yyl4YitIHt0X251bX0gKHt0aWNrZXRzW3RfbnVtXVsnc3RhdHVzJ119"
    "KSIsIGNhbGxiYWNrX2RhdGE9ZiJjYW5jZWx0aWNrZXRfe3JpZH1fe3RfbnVtfSIpXQogICAgICAgIGZvciB0X251bSBpbiBjYW5k"
    "aWRhdGVzCiAgICBdCiAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoCiAgICAgICAgY2hhdF9pZD1BRE1JTl9JRCwK"
    "ICAgICAgICB0ZXh0PWYi8J+UkyDhiqh7cm91bmRfbGFiZWwocmlkKX0g4Yuo4Ym14Yqb4YuN4YqVIOGJgeGMpeGIrSDhi6vhiI3h"
    "ibDhi6vhi5kg4Yib4Yu14Yio4YyNIOGLreGNiOGIjeGMi+GIiT8iLAogICAgICAgIHJlcGx5X21hcmt1cD1JbmxpbmVLZXlib2Fy"
    "ZE1hcmt1cChrYikKICAgICkKCgphc3luYyBkZWYgbWFudWFsX2NhbmNlbCh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4"
    "dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhiqDhi7XhiJrhipUg4YmA4Yuw4YidIOGJpeGIjiDhi6jhibDhi6vhi5gv4Yuo"
    "4Ymw4Yi44YygIOGJgeGMpeGIreGKlSDhi6vhiI3hibDhi6vhi5kg4Yuo4Yia4Yur4Yuw4Yit4YyN4Ymg4Ym1IOGJteGLleGLm+GL"
    "nQogICAg4Yqg4Yyg4YmD4YmA4Yid4Y2mIC9tYW51YWxjYW5jZWwgPOGLmeGIrT4gPOGJgeGMpeGIrT4iIiIKICAgIGlmIHVwZGF0"
    "ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAgICBhcmdzID0gY29udGV4dC5hcmdz"
    "CiAgICBpZiBub3QgYXJnczoKICAgICAgICBjYW5jZWxhYmxlX3JvdW5kX2lkcyA9IFsKICAgICAgICAgICAgcmlkIGZvciByaWQs"
    "IHIgaW4gcm91bmRzLml0ZW1zKCkKICAgICAgICAgICAgaWYgYW55KHRbInN0YXR1cyJdIGluICgiU09MRCIsICJQRU5ESU5HIikg"
    "Zm9yIHQgaW4gclsidGlja2V0cyJdLnZhbHVlcygpKQogICAgICAgIF0KICAgICAgICBhd2FpdCBfc2VuZF9yb3VuZF9waWNrZXIo"
    "CiAgICAgICAgICAgIHVwZGF0ZSwgY29udGV4dCwgImNhbmNlbHJvdW5kIiwKICAgICAgICAgICAgY2FuY2VsYWJsZV9yb3VuZF9p"
    "ZHMsCiAgICAgICAgICAgICLwn5STIOGLqOGLqOGJteGKm+GLjSDhi5nhiK0g4YmB4Yyl4YitIOGLq+GIjeGJsOGLq+GLmSDhiJvh"
    "i7XhiKjhjI0g4Yut4Y2I4YiN4YyL4YiJPyIsCiAgICAgICAgICAgICLihLnvuI8g4Ymg4Yqg4YiB4YqRIOGIsOGLk+GJtSDhi6vh"
    "iI3hibDhi6vhi5kg4Yuo4Yia4Yuw4Yio4YyNIChTT0xEL1BFTkRJTkcpIOGJgeGMpeGIrSDhi6vhiIjhi40g4YuZ4YitIOGLqOGI"
    "iOGIneGNoiIKICAgICAgICApCiAgICAgICAgcmV0dXJuCgogICAgaWYgbGVuKGFyZ3MpIDwgMjoKICAgICAgICB0cnk6CiAgICAg"
    "ICAgICAgIHJvdW5kX2lkID0gaW50KGFyZ3NbMF0pCiAgICAgICAgZXhjZXB0IFZhbHVlRXJyb3I6CiAgICAgICAgICAgIGF3YWl0"
    "IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKaoO+4jyDhibXhiq3hiq3hiIjhipsg4Yuo4YuZ4YitIOGJgeGMpeGIrSDhi6vh"
    "iLXhjIjhiaHhjaIiKQogICAgICAgICAgICByZXR1cm4KICAgICAgICBpZiByb3VuZF9pZCBub3QgaW4gcm91bmRzOgogICAgICAg"
    "ICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLinYwg4Yql4YqV4Yuw4Yua4YiFIOGLq+GIiCDhi5nhiK0g4Yqg"
    "4YiN4Ymw4YyI4YqY4Yid4Y2iIikKICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgYXdhaXQgX3N0YXJ0X21hbnVhbGNhbmNlbF90"
    "aWNrZXRfcGlja2VyKGNvbnRleHQsIHJvdW5kX2lkKQogICAgICAgIHJldHVybgoKICAgIHRyeToKICAgICAgICByb3VuZF9pZCA9"
    "IGludChhcmdzWzBdKQogICAgICAgIHRfbnVtID0gaW50KGFyZ3NbMV0pCiAgICBleGNlcHQgVmFsdWVFcnJvcjoKICAgICAgICBh"
    "d2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Ym14Yqt4Yqt4YiI4YqbIOGLqOGLmeGIrSDhiqXhipMg4Ymy"
    "4Yqs4Ym1IOGJgeGMpeGIrSDhi6vhiLXhjIjhiaHhjaIiKQogICAgICAgIHJldHVybgoKICAgIGlmIHJvdW5kX2lkIG5vdCBpbiBy"
    "b3VuZHM6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4p2MIOGKpeGKleGLsOGLmuGIhSDhi6vhiIgg"
    "4YuZ4YitIOGKoOGIjeGJsOGMiOGKmOGIneGNoiIpCiAgICAgICAgcmV0dXJuCgogICAgbnVtX3RpY2tldHMgPSByb3VuZHNbcm91"
    "bmRfaWRdWyJudW1fdGlja2V0cyJdCiAgICBpZiB0X251bSA8IDEgb3IgdF9udW0gPiBudW1fdGlja2V0czoKICAgICAgICBhd2Fp"
    "dCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KGYi4pqg77iPIOGJgeGMpeGIqSDhiqgxIOGKpeGIteGKqCB7bnVtX3RpY2tldHN9"
    "IOGImOGIhuGKlSDhiqDhiIjhiaDhibXhjaIiKQogICAgICAgIHJldHVybgoKICAgIHByZXYgPSByb3VuZHNbcm91bmRfaWRdWyJ0"
    "aWNrZXRzIl1bdF9udW1dCiAgICB1X2lkID0gcHJldlsidXNlcl9pZCJdCgogICAgcm91bmRzW3JvdW5kX2lkXVsidGlja2V0cyJd"
    "W3RfbnVtXSA9IF9lbXB0eV90aWNrZXQoKQogICAgaWYgdV9pZCBpbiB1c2VyX3NlbGVjdGlvbnM6CiAgICAgICAgZGVsIHVzZXJf"
    "c2VsZWN0aW9uc1t1X2lkXQogICAgc2F2ZV9zdGF0ZSgpCgogICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dChmIvCf"
    "lJMg4YmB4Yyl4YitIHt0X251bX0gKOGLmeGIrSB7cm91bmRfaWR9KSDhi6vhiI3hibDhi6vhi5kg4YuI4Yyl4Ym34YiNIChBVkFJ"
    "TEFCTEUp4Y2iIikKCiAgICBpZiB1X2lkOgogICAgICAgIHRyeToKICAgICAgICAgICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9t"
    "ZXNzYWdlKAogICAgICAgICAgICAgICAgY2hhdF9pZD11X2lkLAogICAgICAgICAgICAgICAgdGV4dD1mIuKEue+4jyDhiYHhjKXh"
    "iK0ge3RfbnVtfSAo4YuZ4YitIHtyb3VuZF9pZH0pIOGJoOGKoOGLteGImuGKlSDhibDhiLDhiK3hi5/hiI0v4Yur4YiN4Ymw4Yur"
    "4YuZIOGLiOGMpeGJt+GIjeGNoiDhiqXhiaPhiq3hi44gL3BsYXkge3JvdW5kX2lkfSDhiaXhiIjhi40g4YiM4YiLIOGLreGInuGK"
    "reGIqeGNoiIKICAgICAgICAgICAgKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgIHBhc3MKCmRlZiBfbmV3"
    "cm91bmRfYmFja19rYigpOgogICAgIiIi4Yqo4YiY4YyA4YiY4Yiq4Yur4YuNIOGLsOGIqOGMgyDhi43hjK0g4YiL4YiJIOGIgeGI"
    "ieGInSDhi6gvbmV3cm91bmQg4Yyl4Yur4YmE4YuO4Ym9IOGLqOGImuGJs+GKqOGIjSDCq+Kshe+4jyDhibDhiJjhiIjhiLUgKEJh"
    "Y2spwrsg4YmB4YiN4Y2NIC0KICAgIOGLreGIhSDhi6jhiJrhi6vhi7DhiK3hjIjhi40g4Yqr4YiI4YqV4Ymg4Ym1IOGLsOGIqOGM"
    "gyDhiaXhibsg4YuI4YuwIOGJgOGLsOGImOGLjSDhi7DhiKjhjIMg4YiY4YiY4YiI4Yi1IOGKkOGLjeGNoyDhiqDhjKDhiYPhiIvh"
    "i60g4YuZ4YitLeGImOGNjeGMoOGIqeGKlSDhiqDhi6vhiYvhiK3hjKXhiJ0KICAgICjhi6vhipXhipUg4YiI4Yib4Yu14Yio4YyN"
    "IC9jYW5jZWwg4YuI4Yut4YidIMKr4p2MIOGJsOGLiOGLjcK7IOGLqOGJsOGJo+GIiOGLjSDhi6jhibDhiIjhi6gg4YmB4YiN4Y2N"
    "IOGKoOGIiCnhjaIiIiIKICAgIHJldHVybiBJbmxpbmVLZXlib2FyZE1hcmt1cChbWwogICAgICAgIElubGluZUtleWJvYXJkQnV0"
    "dG9uKCLirIXvuI8g4Ymw4YiY4YiI4Yi1IChCYWNrKSIsIGNhbGxiYWNrX2RhdGE9Im5ld3JvdW5kYmFjayIpLAogICAgXV0pCgph"
    "c3luYyBkZWYgX3Byb21wdF9uZXdyb3VuZF9zdGVwKHVwZGF0ZSwgY29udGV4dCwgdXNlcl9pZCwgc3RlcCk6CiAgICAiIiLhi6gv"
    "bmV3cm91bmQg4YuK4Yub4Yit4Yu1IOGLjeGIteGMpSDhiIjhibDhiLDhjKDhi40g4Yuw4Yio4YyDIChzdGVwKSDhibDhjIjhiaLh"
    "i43hipUg4Yyl4Yur4YmEIOGLqOGImuGIjeGKrSDhi6jhjIvhiKsgKHNoYXJlZCkgaGVscGVy4Y2iCiAgICDhi4jhi7Ag4Y2K4Ym1"
    "IOGIsuGIhOGLteGInSAo4YmA4Yyj4YutIOGLsOGIqOGMgyDhiLLhjKDhi6jhiYUpIOGLiOGLsCDhiovhiIsgwqvirIXvuI8g4Ymw"
    "4YiY4YiI4Yi1wrsvwqsvYmFja8K7IOGIsuGJo+GIjeGInSAo4YmA4Yuw4YiY4YuNIOGLsOGIqOGMgyDhi7DhjI3hiJ4g4Yiy4Yyg"
    "4Yuo4YmFKQogICAg4Ymg4YiB4YiI4Ymx4YidIOGKoOGJheGMo+GMqyDhibDhiJjhiLPhiLPhi60g4Yut4YiFIOGJsOGMjeGJo+GI"
    "rSDhjKXhiYXhiJ0g4YiL4YutIOGLreGLjeGIi+GIjeGNoyDhiLXhiIjhi5rhiIUg4Yuo4Yyl4Yur4YmEIOGMveGIgeGNiSDhi6jh"
    "ibXhiJ0g4Ymi4Yuw4YyI4YidIOGLiOGMpSDhiIbhipYg4Yut4YmA4Yir4YiN4Y2iIiIiCiAgICBuZXdyb3VuZF9zdGF0ZVt1c2Vy"
    "X2lkXSA9IHN0ZXAKICAgIGtiID0gX25ld3JvdW5kX2JhY2tfa2IoKSBpZiBzdGVwICE9IE5FV1JPVU5EX1NURVBTWzBdIGVsc2Ug"
    "Tm9uZQogICAgYmFja19oaW50ID0gIiIgaWYgc3RlcCA9PSBORVdST1VORF9TVEVQU1swXSBlbHNlICJcbuKshe+4jyDhi4jhi7Ag"
    "4YmA4Yuw4YiY4YuNIOGLsOGIqOGMgyDhiIjhiJjhiJjhiIjhiLUgL2JhY2sg4Yut4YiL4YqpIOGLiOGLreGInSDhiqjhibPhib0g"
    "4Yur4YiI4YuN4YqVIOGJgeGIjeGNjSDhi63hjKvhipHhjaIiCgogICAgaWYgc3RlcCA9PSAiYXdhaXRpbmdfbmFtZSI6CiAgICAg"
    "ICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgIuGKoOGMoOGJg+GJgOGIneGNpiDhi7DhiKjh"
    "jIMg4Ymg4Yuw4Yio4YyDIOGLreGImOGIjeGIsSAo4Yi14YidIOKGkiDhibLhiqzhibUg4Yml4Yub4Ym1IOKGkiDhi4vhjIsg4oaS"
    "IOGImOGMjeGIiOGMqyDihpIg4Yi94YiN4Yib4Ym24Ym9IOKGkiDhiJ3hiLXhiI0pXG5cbiIKICAgICAgICAgICAgIvCfhpUg4Yqg"
    "4Yuy4Yi1IOGLmeGIrSDhiaDhi53hjI3hjIXhibUg4YiL4YutLi4uXG5cbiIKICAgICAgICAgICAgIvCfj7cg4YiI4Yua4YiFIOGL"
    "meGIrSDhiLXhiJ0g4YiY4Yi14Yyg4Ym1IOGLreGNiOGIjeGMi+GIiT8g4Yqg4Yyt4Yit4Y2jIOGKoOGKleGLtS3hiJjhiLXhiJjh"
    "iK0g4Yi14YidIOGLreGIi+GKqSAo4YiI4Yid4Yiz4YiM4Y2mIOGLqOGJoOGLk+GIjSDhi4vhi5zhiJsg4YuV4YyjKeGNoyDhi4jh"
    "i63hiJ0g4Yi14YidIOGKq+GIjeGNiOGIiOGMiSAvc2tpcCDhi4jhi63hiJ0gwqvhi53hiIjhiI3CuyDhi63hiIvhiqnhjaJcblxu"
    "IgogICAgICAgICAgICAi4YiI4YiY4Yiw4Yio4YudIC9jYW5jZWwg4Yut4Yyr4YqR4Y2iIiwKICAgICAgICAgICAgcmVwbHlfbWFy"
    "a3VwPWtiLAogICAgICAgICkKICAgIGVsaWYgc3RlcCA9PSAiYXdhaXRpbmdfY291bnQiOgogICAgICAgIGF3YWl0IHVwZGF0ZS5t"
    "ZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAgICAgICLhiqDhjKDhiYPhiYDhiJ3hjaYg4YmB4Yyl4YitIOGJpeGJuyDhi63hiIvh"
    "iqkgKOGIiOGIneGIs+GIjOGNpiAxMDApXG5cbiIKICAgICAgICAgICAgIvCfjp8g4YiI4Yua4YiFIOGLmeGIrSDhiLXhipXhibUg"
    "4Ymy4Yqs4Ym1IOGIm+GLmOGMi+GMgOGJtSDhi63hjYjhiI3hjIvhiIk/IiArIGJhY2tfaGludCwKICAgICAgICAgICAgcmVwbHlf"
    "bWFya3VwPWtiLAogICAgICAgICkKICAgIGVsaWYgc3RlcCA9PSAiYXdhaXRpbmdfcHJpY2UiOgogICAgICAgIGNvdW50ID0gbmV3"
    "cm91bmRfdGVtcC5nZXQodXNlcl9pZCwge30pLmdldCgiY291bnQiKQogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5"
    "X3RleHQoCiAgICAgICAgICAgIGYi4Yqg4Yyg4YmD4YmA4Yid4Y2mIOGLi+GMiyDhiYHhjKXhiK0g4Yml4Ym7IOGLreGIi+GKqSAo"
    "4YiI4Yid4Yiz4YiM4Y2mIDUwKVxuXG4iCiAgICAgICAgICAgIGYi8J+StSDhiqXhiLrhjaMge2NvdW50fSDhibLhiqzhibbhib3h"
    "jaIg4Yqg4YiB4YqVIOGLqOGKoOGKleGLtSDhibLhiqzhibUg4YuL4YyLIOGJoOGJpeGIrSDhi6vhiLXhjIjhiaHhjaYiICsgYmFj"
    "a19oaW50LAogICAgICAgICAgICByZXBseV9tYXJrdXA9a2IsCiAgICAgICAgKQogICAgZWxpZiBzdGVwID09ICJhd2FpdGluZ19k"
    "ZXNjcmlwdGlvbiI6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgIuGKoOGMoOGJ"
    "g+GJgOGIneGNpiDhjL3hiIHhjY0g4Yut4YiL4Yqp4Y2jIOGLiOGLreGInSDhiJjhjI3hiIjhjKsg4Yqr4YiN4Y2I4YiI4YyJIC9z"
    "a2lwIOGLiOGLreGInSDCq+GLneGIiOGIjcK7IOGLreGIi+GKqVxuXG4iCiAgICAgICAgICAgICLwn5OdIOGIteGIiOGLmuGIhSDh"
    "i5nhiK0g4Yqg4Yyt4YitIOGImOGMjeGIiOGMqyAo4YiI4Ymw4Yyr4YuL4Ym+4Ym9IOGLqOGImuGJs+GLrSkg4Yut4YiL4Yqp4Y2m"
    "IiArIGJhY2tfaGludCwKICAgICAgICAgICAgcmVwbHlfbWFya3VwPWtiLAogICAgICAgICkKICAgIGVsaWYgc3RlcCA9PSAiYXdh"
    "aXRpbmdfcHJpemVzIjoKICAgICAgICBwcml6ZXMgPSBuZXdyb3VuZF90ZW1wLmdldCh1c2VyX2lkLCB7fSkuZ2V0KCJwcml6ZXMi"
    "KSBvciBbXQogICAgICAgIG9yZGluYWwgPSBfb3JkaW5hbF9hbShsZW4ocHJpemVzKSArIDEpCiAgICAgICAgaWYgbm90IHByaXpl"
    "czoKICAgICAgICAgICAgYm9keSA9ICgKICAgICAgICAgICAgICAgIGYi8J+PhiB7b3JkaW5hbH0g4Yi94YiN4Yib4Ym1IOGIneGK"
    "leGLteGKlSDhipDhi40/XG4iCiAgICAgICAgICAgICAgICAi4YyI4YqV4YuY4YmlICjhiIjhiJ3hiLPhiIzhjaYgwqs1MDAwIOGJ"
    "peGIrcK7KSDhi4jhi63hiJ0g4YyI4YqV4YuY4YmlIOGLq+GIjeGIhuGKkCDhi5XhiYMg4Yi14YidICjhiIjhiJ3hiLPhiIzhjaYg"
    "wqvhiqDhi7LhiLUg4Yi14YiN4YqtwrvhjaMgwqvhiIvhjZXhibbhjZXCuykg4YiY4YiL4YqtIOGLreGJveGIi+GIieGNolxuIgog"
    "ICAgICAgICAgICAgICAgIuGIiOGLmuGIhSDhi5nhiK0g4Yid4YqV4YidIOGIveGIjeGIm+GJtSDhiJvhiLXhiYDhiJjhjKUg4Yqr"
    "4YiN4Y2I4YiI4YyJIC9za2lwIOGLiOGLreGInSDCq+GLneGIiOGIjcK7IOGLreGIi+GKqeGNoiIKICAgICAgICAgICAgKQogICAg"
    "ICAgICAgICBiYWNrX2hpbnQyID0gIlxu4qyF77iPIOGLiOGLsCDhiJjhjI3hiIjhjKsg4Yuw4Yio4YyDIOGIiOGImOGImOGIiOGI"
    "tSAvYmFjayDhi63hiIvhiqkg4YuI4Yut4YidIOGKqOGJs+GJvSDhi6vhiIjhi43hipUg4YmB4YiN4Y2NIOGLreGMq+GKkeGNoiIK"
    "ICAgICAgICBlbHNlOgogICAgICAgICAgICBhZGRlZCA9ICJcbiIuam9pbihfZm9ybWF0X3ByaXplc19saW5lcyhwcml6ZXMpKQog"
    "ICAgICAgICAgICBib2R5ID0gKAogICAgICAgICAgICAgICAgZiLinIUg4Yql4Yi14Yqr4YiB4YqVIOGLqOGJsOGImOGLmOGMiOGJ"
    "oSDhiL3hiI3hiJvhibbhib3hjaZcbnthZGRlZH1cblxuIgogICAgICAgICAgICAgICAgZiLwn4+GIHtvcmRpbmFsfSDhiL3hiI3h"
    "iJvhibUg4Yid4YqV4Yu14YqVIOGKkOGLjT8gKOGMiOGKleGLmOGJpSDhi4jhi63hiJ0g4YuV4YmDIOGIteGInSDhi63hiIvhiqkp"
    "XG4iCiAgICAgICAgICAgICAgICAi4Ymw4Yyo4Yib4YiqIOGIveGIjeGIm+GJtSDhiqjhiIzhiIggL3NraXAg4YuI4Yut4YidIMKr"
    "4Yud4YiI4YiNwrsg4Yut4YiL4YqpIOGLiOGLsCDhiYDhjKPhi60gKOGIneGIteGIjSkg4Yuw4Yio4YyDIOGIiOGImOGIhOGLteGN"
    "oiIKICAgICAgICAgICAgKQogICAgICAgICAgICBiYWNrX2hpbnQyID0gIlxu4qyF77iPIOGLqOGImOGMqOGIqOGIu+GLjeGKlSDh"
    "iL3hiI3hiJvhibUg4Yml4Ym7IOGIiOGIm+GMpeGNi+GJtSAvYmFjayDhi63hiIvhiqkg4YuI4Yut4YidIOGKqOGJs+GJvSDhi6vh"
    "iIjhi43hipUg4YmB4YiN4Y2NIOGLreGMq+GKkeGNoiIKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KGJv"
    "ZHkgKyBiYWNrX2hpbnQyLCByZXBseV9tYXJrdXA9a2IpCiAgICBlbGlmIHN0ZXAgPT0gImF3YWl0aW5nX2ltYWdlIjoKICAgICAg"
    "ICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICAi4Yqg4Yyg4YmD4YmA4Yid4Y2mIOGNjuGJtiDh"
    "i63hiIvhiqnhjaMg4YuI4Yut4YidIOGIneGIteGIjSDhiqvhiI3hjYjhiIjhjIkgL3NraXAg4YuI4Yut4YidIMKr4Yud4YiI4YiN"
    "wrsg4Yut4YiL4YqpXG5cbiIKICAgICAgICAgICAgIvCflrwg4YiI4Yua4YiFIOGLmeGIrSDhiJvhiLXhibPhi4jhiYLhi6sg4Yuo"
    "4Yia4YiG4YqVIOGIneGIteGIjSDhi63hiIvhiqkgKOGKq+GIteGNiOGIiOGMiCnhjaYiICsgYmFja19oaW50LAogICAgICAgICAg"
    "ICByZXBseV9tYXJrdXA9a2IsCiAgICAgICAgKQoKYXN5bmMgZGVmIF9uZXdyb3VuZF9zdGVwX2JhY2sodXBkYXRlLCBjb250ZXh0"
    "LCB1c2VyX2lkKToKICAgICIiIi9uZXdyb3VuZCDhi4rhi5vhiK3hi7Ug4YuN4Yi14YylIOGKq+GIiOGKleGJoOGJtSDhi7DhiKjh"
    "jIMg4YuI4YuwIOGJgOGLsOGImOGLjSDhi7DhiKjhjIMg4Yml4Ym7IOGLqOGImuGImOGIjeGItSAoVW5kbyBvbmUgc3RlcCkgaGVs"
    "cGVy4Y2iCiAgICDhi63hiIUg4Yuo4Yia4Yur4Yuw4Yit4YyI4YuNIOGLq+GIiOGNiOGLjeGKlSDhjKXhi6vhiYQg4Yml4Ym7IOGL"
    "sOGMjeGIniDhiJjhjKDhi6jhiYUg4YqQ4YuNIOGKpeGKleGMgiDhiqDhjKDhiYPhiIvhi60gL25ld3JvdW5kIOGJteGLleGLm+GL"
    "meGKlSDhiJjhiLDhiKjhi50g4Yqg4Yut4Yuw4YiI4YidCiAgICAo4YmA4Yu14Yie4YuN4YqRIOGIi+GIiOGLjSDhi43hiILhiaUg"
    "LSDhiLXhiJ0v4Ymy4Yqs4Ym1L+GLi+GMiy/hiJjhjI3hiIjhjKsv4Yi94YiN4Yib4Ym24Ym9IC0g4Yid4YqV4YidIOGKoOGLreGM"
    "oOGNi+GIneGNoyDhiYDhi7DhiJjhi40g4Yyl4Yur4YmEIOGJpeGJuyDhiqXhipXhi7DhjIjhipMg4Yut4Ymz4Yur4YiNKeGNogog"
    "ICAg4YmgwqvhiL3hiI3hiJvhibbhib3CuyDhi7DhiKjhjIMg4YuN4Yi14YylIOGKqOGIhuGKleGKlSDhiqXhipMg4Ymi4Yur4YqV"
    "4Yi1IOGKoOGKleGLtSDhiL3hiI3hiJvhibUg4Ymw4YiY4Yud4YyN4YmmIOGKqOGIhuGKkOGNoyDhi63hiIUg4Yuo4YiY4Yyo4Yio"
    "4Yi74YuN4YqVIOGIveGIjeGIm+GJtSDhiaXhibsg4Yqg4Yyl4Y2N4Ym2CiAgICDhiqXhipXhi7DhjIjhipMg4Yur4YqV4YqRIOGL"
    "sOGIqOGMgyDhi63hjKDhi63hiYPhiI0gKOGLiOGLsCDhiJjhjI3hiIjhjKsg4Yuw4Yio4YyDIOGKoOGLreGLmOGIjeGInSkgLSDh"
    "iLXhiIjhi5rhiIUg4Yi94YiN4Yib4Ym24Ym5IOGIq+GIs+GJuOGLjSDhiaDhibDhipPhjKDhiI0gJ1VuZG8nIOGLreGLsOGIqOGM"
    "jeGIi+GJuOGLi+GIjeGNoiIiIgogICAgc3RhdGUgPSBuZXdyb3VuZF9zdGF0ZS5nZXQodXNlcl9pZCkKICAgIGlmIHN0YXRlIG5v"
    "dCBpbiBORVdST1VORF9TVEVQUzoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLihLnvuI8g4Ymg4Yqg"
    "4YiB4YqRIOGIsOGLk+GJtSDhipXhiYEg4YuoL25ld3JvdW5kIOGIguGLsOGJtSDhi6jhiIjhiJ3hjaIiKQogICAgICAgIHJldHVy"
    "bgoKICAgIGlmIHN0YXRlID09ICJhd2FpdGluZ19wcml6ZXMiOgogICAgICAgIHByaXplcyA9IG5ld3JvdW5kX3RlbXAuZ2V0KHVz"
    "ZXJfaWQsIHt9KS5nZXQoInByaXplcyIpIG9yIFtdCiAgICAgICAgaWYgcHJpemVzOgogICAgICAgICAgICByZW1vdmVkID0gcHJp"
    "emVzLnBvcCgpCiAgICAgICAgICAgIG5ld3JvdW5kX3RlbXBbdXNlcl9pZF1bInByaXplcyJdID0gcHJpemVzCiAgICAgICAgICAg"
    "IGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoZiLwn5eRIMKre3JlbW92ZWR9wrsg4Ymw4Yml4YiOIOGLqOGMiOGJo+GL"
    "jSDhiL3hiI3hiJvhibUg4Ymw4YqQ4Yi14Ym34YiN4Y2iIikKICAgICAgICAgICAgYXdhaXQgX3Byb21wdF9uZXdyb3VuZF9zdGVw"
    "KHVwZGF0ZSwgY29udGV4dCwgdXNlcl9pZCwgImF3YWl0aW5nX3ByaXplcyIpCiAgICAgICAgICAgIHJldHVybgogICAgICAgICMg"
    "4YyI4YqTIOGIneGKleGInSDhiL3hiI3hiJvhibUg4Yqr4YiN4YyI4YmjICjhi6jhiJjhjIDhiJjhiKrhi6vhi40g4Yuo4Yi94YiN"
    "4Yib4Ym1IOGMpeGLq+GJhCkgLSDhiqjhi5rhiIUg4Ymg4Ymz4Ym9IOGLiOGLs+GIiOGLjSDhiJjhi7DhiaDhipsg4Yuw4Yio4YyD"
    "LeGJoOGLsOGIqOGMgyDhiqDhiJjhiq3hipXhi64g4Yut4YmA4Yyl4YiL4YiNCgogICAgaWR4ID0gTkVXUk9VTkRfU1RFUFMuaW5k"
    "ZXgoc3RhdGUpCiAgICBpZiBpZHggPT0gMDoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAg"
    "ICAgICAi4oS577iPIOGLreGIhSDhi6jhiJjhjIDhiJjhiKrhi6vhi40g4Yuw4Yio4YyDIOGKkOGLjeGNoyDhiqjhi5rhiIUg4Ymg"
    "4YiL4YutIOGLiOGLsCDhiovhiIsg4YiY4YiY4YiI4Yi1IOGKoOGLreGJu+GIjeGIneGNolxuIgogICAgICAgICAgICAi4Yqg4Yyg"
    "4YmD4YiL4YutIOGLmeGIrS3hiJjhjY3hjKDhiKnhipUg4YiZ4YiJIOGJoOGImeGIiSDhiIjhiJjhiLDhiKjhi50gL2NhbmNlbCDh"
    "i63hjKvhipHhjaIiCiAgICAgICAgKQogICAgICAgIHJldHVybgogICAgcHJldl9zdGVwID0gTkVXUk9VTkRfU1RFUFNbaWR4IC0g"
    "MV0KICAgIGF3YWl0IF9wcm9tcHRfbmV3cm91bmRfc3RlcCh1cGRhdGUsIGNvbnRleHQsIHVzZXJfaWQsIHByZXZfc3RlcCkKCmFz"
    "eW5jIGRlZiBoYW5kbGVfbmV3cm91bmRfYmFja19idXR0b24odXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5E"
    "RUZBVUxUX1RZUEUpOgogICAgIiIiL25ld3JvdW5kIOGLiuGLm+GIreGLtSDhi43hiLXhjKUg4Yqo4Ymz4Ym9IOGLq+GIiOGLjeGK"
    "lSDCq+Kshe+4jyDhibDhiJjhiIjhiLUgKEJhY2spwrsg4YmB4YiN4Y2NIOGIsuGMq+GKkSDhiI3hiq0gL2JhY2sg4Yql4YqV4Yuw"
    "4YiY4YiL4YqtIOGLqOGImuGIsOGIqwogICAgY2FsbGJhY2sgLSDhi4jhi7Ag4YmA4Yuw4YiY4YuNIOGLsOGIqOGMgyDhiaXhibsg"
    "4Yut4YiY4YiN4Yiz4YiN4Y2jIOGKoOGMoOGJg+GIi+GLrSDhi5nhiK0t4YiY4Y2N4Yyg4Yip4YqVIOGKoOGLq+GJi+GIreGMpeGI"
    "nSAo4Yur4YqV4YqVIOGLqOGImuGLq+GLsOGIreGMiOGLjSDinYwg4Ymw4YuI4YuNIOGKkOGLjSnhjaIiIiIKICAgIHF1ZXJ5ID0g"
    "dXBkYXRlLmNhbGxiYWNrX3F1ZXJ5CiAgICBpZiBxdWVyeS5mcm9tX3VzZXIuaWQgIT0gQURNSU5fSUQ6CiAgICAgICAgYXdhaXQg"
    "cXVlcnkuYW5zd2VyKCkKICAgICAgICByZXR1cm4KICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICBmYWtlX3VwZGF0ZSA9IF9G"
    "YWtlVXBkYXRlKF9GYWtlTXNnKHF1ZXJ5Lm1lc3NhZ2UsIHF1ZXJ5LmZyb21fdXNlcikpCiAgICBhd2FpdCBfbmV3cm91bmRfc3Rl"
    "cF9iYWNrKGZha2VfdXBkYXRlLCBjb250ZXh0LCBxdWVyeS5mcm9tX3VzZXIuaWQpCgphc3luYyBkZWYgbmV3X3JvdW5kKHVwZGF0"
    "ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIuGKoOGLsuGItSDhi5nhiK0g4Yqo"
    "4YqQ4Ymj4Yiu4Ym5IOGMi+GIrSDhiaDhibXhi63hi6kg4Yuo4Yia4Yqo4Y2N4Ym1IOGJteGLleGLm+GLnQogICAg4Yqg4Yyg4YmD"
    "4YmA4Yid4Y2mIC9uZXdyb3VuZCA84Ymy4Yqs4Ym1IOGJpeGLm+GJtT4gPOGLi+GMiz4gW+GIteGInS4uLl0gICjhiIjhiJ3hiLPh"
    "iIzhjaYgL25ld3JvdW5kIDUwIDMwIOGLqOGMiOGKkyDhiI7hibDhiKopCiAgICDhi6vhiIggYXJncyDhiqjhibDhjKDhiYDhiJkg"
    "4Yuw4Yio4YyDIOGJoOGLsOGIqOGMgyAo4Yi14YidIOKGkiDhibLhiqzhibUg4oaSIOGLi+GMiyDihpIg4YiY4YyN4YiI4YyrIOKG"
    "kiDhiL3hiI3hiJvhibbhib0g4oaSIOGIneGIteGIjSkg4Yut4Yyg4Yut4YmF4YuO4Ymz4YiN4Y2jCiAgICDhiaDhiJvhipXhipvh"
    "i43hiJ0g4Yuw4Yio4YyDIOGIi+GLrSDCq+Kshe+4jyDhibDhiJjhiIjhiLXCuy/Cqy9iYWNrwrsg4Yml4YiI4YuNIOGLiOGLsCDh"
    "iYDhi7DhiJjhi40g4Yuw4Yio4YyDIOGJpeGJuyDhiJjhiJjhiIjhiLUg4Yut4Ym94YiL4YiJ4Y2iIiIiCiAgICBnbG9iYWwgbmV4"
    "dF9yb3VuZF9pZAoKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4K"
    "CiAgICB1aWQgPSB1cGRhdGUubWVzc2FnZS5mcm9tX3VzZXIuaWQKICAgIGNsZWFyZWQgPSBfY2FuY2VsX290aGVyX2FkbWluX3Rh"
    "c2tzKHVpZCwga2VlcD0ibmV3cm91bmQiKQogICAgYXdhaXQgX25vdGlmeV9jYW5jZWxsZWRfdGFza3MoY29udGV4dC5ib3QsIHVp"
    "ZCwgY2xlYXJlZCkKCiAgICAjIE5vcm1hbGl6ZSBhcmd1bWVudHMgc28gdGhlIHdpemFyZCB3b3JrcyBmcm9tIGJvdGggL25ld3Jv"
    "dW5kIGFuZCB0aGUKICAgICMgYm90dG9tIGFkbWluIGJ1dHRvbiAod2hpY2ggaGFzIG5vIGNvbW1hbmQgYXJndW1lbnRzKS4KICAg"
    "IGFyZ3MgPSBsaXN0KGdldGF0dHIoY29udGV4dCwgImFyZ3MiLCBOb25lKSBvciBbXSkKICAgIGlmIGxlbihhcmdzKSA+PSAyOgog"
    "ICAgICAgIHRyeToKICAgICAgICAgICAgY291bnQgPSBpbnQoYXJnc1swXSkKICAgICAgICAgICAgcHJpY2UgPSBmbG9hdChhcmdz"
    "WzFdKQogICAgICAgIGV4Y2VwdCBWYWx1ZUVycm9yOgogICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0"
    "KAogICAgICAgICAgICAgICAgIuGKoOGMoOGJg+GJgOGIneGNpiAvbmV3cm91bmQgPOGJsuGKrOGJtSDhiaXhi5vhibU+IDzhi4vh"
    "jIs+IFvhiLXhiJ0uLi5dXG4iCiAgICAgICAgICAgICAgICAi4pqg77iPIOGJsuGKrOGJtSDhiaXhi5vhibUg4Yql4YqTIOGLi+GM"
    "iyDhiYHhjKXhiK0g4YiY4YiG4YqVIOGKoOGIiOGJo+GJuOGLjeGNoiDhiIjhiJ3hiLPhiIzhjaYgL25ld3JvdW5kIDUwIDMwIOGL"
    "qOGMiOGKkyDhiI7hibDhiKoiCiAgICAgICAgICAgICkKICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgbmFtZSA9ICIgIi5qb2lu"
    "KGFyZ3NbMjpdKS5zdHJpcCgpIG9yIE5vbmUKICAgICAgICBhd2FpdCBfc2hvd19uZXdyb3VuZF9jb25maXJtKHVwZGF0ZSwgY29u"
    "dGV4dCwgY291bnQsIHByaWNlLCBuYW1lPW5hbWUpCiAgICAgICAgcmV0dXJuCgogICAgbmV3cm91bmRfdGVtcFt1cGRhdGUubWVz"
    "c2FnZS5mcm9tX3VzZXIuaWRdID0ge30KICAgIGF3YWl0IF9wcm9tcHRfbmV3cm91bmRfc3RlcCh1cGRhdGUsIGNvbnRleHQsIHVw"
    "ZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCwgImF3YWl0aW5nX25hbWUiKQoKZGVmIF9vcmRpbmFsX2FtKG46IGludCkgLT4gc3Ry"
    "OgogICAgIiIi4YuoIG4g4YqbIOGLsOGIqOGMgyDhiqDhiJvhiK3hipsg4Ymw4YirLeGJgeGMpeGIrSDhiJ3hiI3hiq3hibUgKDHh"
    "ipsvMuGKmy8z4YqbLzThipsuLi4pIOGLreGImOGIjeGIs+GIjSAtIOGKqOGKpeGKleGMjeGIiuGLneGKm+GLjSAxc3QvMm5kLzNy"
    "ZC80dGgKICAgIOGIiOGLqOGJtSDhiaPhiIgg4YiY4YiN4YqpICfhipsnIOGLqOGJsOGJo+GIiOGLjSDhiYXhjKXhi6sg4YiI4YiB"
    "4YiJ4YidIOGJgeGMpeGIrSDhibDhiJjhiLPhiLPhi60g4YiG4YqWIOGIteGIiOGImuGLq+GMiOGIiOGMjeGIjSDhiJ3hipXhiJ0g"
    "4YiN4YupIOGIgeGKlOGJsyDhiqDhi6vhiLXhjYjhiI3hjIjhi43hiJ3hjaIiIiIKICAgIHJldHVybiBmIntufeGKmyIKCmRlZiBf"
    "Y2xlYW5fcHJpemVfbGluZSh0ZXh0OiBzdHIpIC0+IHN0cjoKICAgICIiIuGKoOGLteGImuGKkSAo4YmgL25ld3JvdW5kIMKr4Yi9"
    "4YiN4Yib4Ym24Ym9wrsg4Yuw4Yio4YyDIOGLjeGIteGMpSDhiaDigLlmbG93LWJ5LWZsb3figLog4Yuw4Yio4YyDIOGJoOGLsOGI"
    "qOGMgykg4YiI4Yqg4YqV4Yu1IOGKkOGMoOGIiyDhiL3hiI3hiJvhibUKICAgIOGLqOGIi+GKqOGLjeGKlSDhjL3hiIHhjY0g4Yur"
    "4Yyg4Yir4YiN4Y2iIOGKoOGLteGImuGKkSDhiaDhiI3hiJvhi7UgJzHhipsnLycxLicvJzEpJyDhi6jhiJjhiLPhiLDhiIgg4Ymw"
    "4YirLeGJgeGMpeGIrSDhiYXhi7XhiJgt4YmF4Yyl4YurIOGJouGMqOGIneGIrSDhiaXhibsg4Yur4YuI4Yyj4YuL4YiNCiAgICAo"
    "4Yqo4YmB4Yyl4YipIOGMi+GIrSDhipsvIHN0L25kL3JkL3RoIOGLiOGLreGInSDhipDhjKXhiaUv4YmF4YqV4Y2NIOGIneGIjeGK"
    "reGJtSDhibDhjKPhiaXhiYYg4Yiy4YyI4YqdIOGJpeGJuykgLSDhibDhiKsg4YmB4Yyl4YipIOGIq+GIsSAo4Yi14YqV4Ymw4Yqb"
    "IOGIveGIjeGIm+GJtQogICAg4Yql4YqV4Yuw4YiG4YqQKSDhiKvhiLEg4Ymg4Yir4Yi1LeGIsOGIrSDhiLXhiIjhiJrhibPhi4jh"
    "iYUv4Yi14YiI4Yia4Ymz4Yqo4YiNIOGLteGMjeGMjeGInuGIvSDhiqDhi6vhiLXhjYjhiI3hjI3hiJ3hjaIKICAgIOKaoO+4jyDh"
    "i6vhiIgg4Yid4YqV4YidIOGIneGIjeGKreGJtSDhiaXhibvhi43hipUg4Yuo4YyI4YmjIOGJgeGMpeGIrSAo4YiI4Yid4Yiz4YiM"
    "IMKrMTUwMDAg4Yml4Yitwrsg4YuN4Yi14YylIOGLq+GIiOGLjSAxNTAwMCkg4Yuo4Yi94YiN4Yib4YmxIOGMiOGKleGLmOGJpSDh"
    "iJjhjKDhipUg4Yir4YixCiAgICDhiLXhiIjhiIbhipAg4Yqg4Yut4YqQ4Yqr4YidL+GKoOGLreGLiOGMiOGLteGInSAtIOGJsOGI"
    "qy3hiYHhjKXhiK0g4YmF4Yu14YiYLeGJheGMpeGLqyDhipDhi40g4Ymw4Yml4YiOIOGLqOGImuGLiOGIsOGLsOGLjSDhjI3hiI3h"
    "jL0g4Yid4YiN4Yqt4Ym1ICjhipsvLi8pKSDhibDhjKPhiaXhiYYg4Yiy4YyI4YqdIOGJpeGJuyDhipDhi43hjaIKICAgIOGIveGI"
    "jeGIm+GJsSDhjIjhipXhi5jhiaUgKCI1MDAwIOGJpeGIrSIpIOGLiOGLreGInSDhjIjhipXhi5jhiaUg4Yur4YiN4YiG4YqQIOGL"
    "leGJgyAoIuGIteGIjeGKrSLhjaMgIuGIi+GNleGJtuGNlSIpIOGIiuGIhuGKlSDhi63hib3hiIvhiI0gLSDhiIHhiIjhibHhiJ0g"
    "4YqQ4Yy7IOGMveGIgeGNjQogICAg4Yi14YiI4YiG4YqRIOGJoOGJsOGImOGIs+GIs+GLrSDhiJjhipXhjIjhi7Ug4Yut4Yur4Yub"
    "4YiJ4Y2jIOGIneGKleGInSDhiI3hi6kg4Yib4Yyj4Yir4Ym1IOGKoOGLq+GIteGNiOGIjeGMi+GJuOGLjeGIneGNoiIiIgogICAg"
    "bGluZSA9ICh0ZXh0IG9yICIiKS5zdHJpcCgpCiAgICBjbGVhbmVkID0gcmUuc3ViKAogICAgICAgIHIiXlxzKlxkK1xzKig/OuGK"
    "m3xzdHxuZHxyZHx0aHxbXC5cKTpdKVxzKiIsICIiLCBsaW5lLCBmbGFncz1yZS5JR05PUkVDQVNFCiAgICApLnN0cmlwKCkKICAg"
    "IHJldHVybiBjbGVhbmVkIG9yIGxpbmUKCmRlZiBfZm9ybWF0X3ByaXplc19saW5lcyhwcml6ZXMpOgogICAgIiIi4YiI4Yib4Yiz"
    "4YurICjhi6jhiJvhiKjhjIvhjIjhjKsg4YiY4YiN4YuV4Yqt4Ym1L+GLqOGLmeGIrSDhiJvhjKDhiYPhiIjhi6sv4YiI4Ymw4Yyr"
    "4YuL4Ym+4Ym9IOGIm+GIteGJs+GLiOGJguGLqykg4Ymw4YirLeGJgeGMpeGIrSDhi6jhibDhiLDhjKPhibjhi40g4Yuo4Yi94YiN"
    "4Yib4Ym1CiAgICDhiJjhiLXhiJjhiK7hib3hipUgKCcgIDHhipsgLSA1MDAwIOGJpeGIrScg4Yuo4YiY4Yiz4Yiw4YiIKSDhi53h"
    "iK3hi53hiK0g4Yut4YiY4YiN4Yiz4YiN4Y2iIiIiCiAgICBpZiBub3QgcHJpemVzOgogICAgICAgIHJldHVybiBbXQogICAgcmV0"
    "dXJuIFtmIiAge19vcmRpbmFsX2FtKGkgKyAxKX0gLSB7cH0iIGZvciBpLCBwIGluIGVudW1lcmF0ZShwcml6ZXMpXQoKZGVmIF9z"
    "YW5pdGl6ZV9yb3VuZF9uYW1lKG5hbWUpOgogICAgIiIi4Yuo4YuZ4YitIOGIteGInSDhipXhjYHhiIUg4YiI4Yib4Yu14Yio4YyN"
    "IC0g4Yi14YiZIFRlbGVncmFtIOGJgeGIjeGNjSAoaW5saW5lIGJ1dHRvbikg4Yy94YiB4Y2NIOGLjeGIteGMpSDhiLXhiIjhiJrh"
    "jIjhiaMKICAgICjhiYHhiI3hjY7hib0g4Yuo4Yml4YuZLeGImOGIteGImOGIrSDhjL3hiIHhjY3hipUg4Ymg4Ym14Yqt4Yqt4YiN"
    "IOGIteGIiOGIm+GLq+GIs+GLqSDhiqXhipMg4Yql4Yi14YqoIDY0IOGNiuGLsOGIi+GJtSDhiaXhibsg4Yi14YiI4Yia4Y2I4YmF"
    "4YuxKeGNpgogICAgMSkg4Yuo4YiY4Yi14YiY4YitIOGImOGJgOGLqOGIquGLq+GLjuGJveGKlSAoXFxuKSDhiqXhipMg4Ymw4Yuw"
    "4YyL4YyL4YiaIOGKreGNjeGJsOGJtuGJveGKlSDhi4jhi7Ag4Yqg4YqV4Yu1IOGKreGNjeGJsOGJtSDhi63hiYDhi63hiKvhiI3h"
    "jaMKICAgIDIpIOGKqDQwIOGNiuGLsOGIi+GJtSDhiaDhiIvhi60g4Yqo4YiG4YqQIOGLq+GIs+GMpeGIq+GIjeGNogogICAg4Yuo"
    "4Ymw4YiY4YiI4Yiw4YuNICjhipXhjYHhiIVf4Yi14YidLCDhibDhiYDhi63hiK/hiI0/KSDhjKXhipXhi7Ug4YqQ4YuN4Y2iIiIi"
    "CiAgICBpZiBub3QgbmFtZToKICAgICAgICByZXR1cm4gbmFtZSwgRmFsc2UKICAgIGNsZWFuZWQgPSByZS5zdWIociJccysiLCAi"
    "ICIsIG5hbWUpLnN0cmlwKCkKICAgIGNoYW5nZWQgPSBjbGVhbmVkICE9IG5hbWUuc3RyaXAoKQogICAgaWYgbGVuKGNsZWFuZWQp"
    "ID4gNDA6CiAgICAgICAgY2xlYW5lZCA9IGNsZWFuZWRbOjM5XS5yc3RyaXAoKSArICLigKYiCiAgICAgICAgY2hhbmdlZCA9IFRy"
    "dWUKICAgIHJldHVybiBjbGVhbmVkLCBjaGFuZ2VkCgphc3luYyBkZWYgX3Nob3dfbmV3cm91bmRfY29uZmlybSh1cGRhdGUsIGNv"
    "bnRleHQsIGNvdW50LCBwcmljZSwgbmFtZT1Ob25lLCBkZXNjcmlwdGlvbj1Ob25lLCBwcml6ZXM9Tm9uZSwgaW1hZ2VfZmlsZV9p"
    "ZD1Ob25lKToKICAgICIiIuGKoOGLsuGItSDhi5nhiK0g4Yqo4YiY4Y2N4Yyg4YipIOGJoOGNiuGJtSDhiqDhi7XhiJrhipEg4Yud"
    "4Yit4Yud4Yip4YqVICjhiL3hiI3hiJvhibbhib3hipUg4Yyo4Yid4YiuKSDhiqDhi63hibYg4Ymg4pyFL+KdjCDhi6jhiJrhi6vh"
    "iKjhjIvhjI3hjKXhiaDhibUg4Yuo4YiY4Yyo4Yio4Yi7IOGLsOGIqOGMg+GNogogICAg4Yql4YuN4YqQ4Ymw4Yqb4YuNIOGImOGN"
    "jeGMoOGIrSDhi6jhiJrhjYjhjLjhiJjhi40gaGFuZGxlX25ld3JvdW5kX2NvbmZpcm0g4YuN4Yi14YylIOKchSDhiqjhibDhjKvh"
    "ipAg4Ymg4YqL4YiLIOGJpeGJuyDhipDhi43hjaIiIiIKICAgIGFkbWluX2lkID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlk"
    "IGlmIHVwZGF0ZS5tZXNzYWdlIGVsc2UgQURNSU5fSUQKICAgIGlmIGNvdW50IDwgMToKICAgICAgICBhd2FpdCB1cGRhdGUubWVz"
    "c2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Yuo4Ymy4Yqs4Ym1IOGJpeGLm+GJtSDhiqgxIOGJoOGIi+GLrSDhiJjhiIbhipUg4Yqg"
    "4YiI4Ymg4Ym14Y2iIikKICAgICAgICByZXR1cm4KICAgIGlmIHByaWNlIDw9IDA6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3Nh"
    "Z2UucmVwbHlfdGV4dCgi4pqg77iPIOGLi+GMi+GLjSDhiqgwIOGJoOGIi+GLrSDhiJjhiIbhipUg4Yqg4YiI4Ymg4Ym14Y2iIikK"
    "ICAgICAgICByZXR1cm4KCiAgICBuYW1lLCBuYW1lX2NoYW5nZWQgPSBfc2FuaXRpemVfcm91bmRfbmFtZShuYW1lKQogICAgcHJp"
    "emVzID0gcHJpemVzIG9yIFtdCgogICAgcGVuZGluZ19yb3VuZF9jb25maXJtW2FkbWluX2lkXSA9IHsKICAgICAgICAiY291bnQi"
    "OiBjb3VudCwgInByaWNlIjogcHJpY2UsICJuYW1lIjogbmFtZSwKICAgICAgICAiZGVzY3JpcHRpb24iOiBkZXNjcmlwdGlvbiwg"
    "InByaXplcyI6IHByaXplcywgImltYWdlX2ZpbGVfaWQiOiBpbWFnZV9maWxlX2lkLAogICAgfQoKICAgIGxpbmVzID0gWyLwn6e+"
    "IDxiPuGKpeGJo+GKreGLjiDhi6jhi5nhiKnhipUg4Yud4Yit4Yud4YitIOGLq+GIqOGMi+GMjeGMoTwvYj4iXQogICAgaWYgbmFt"
    "ZToKICAgICAgICBsaW5lcy5hcHBlbmQoZiLwn4+3IOGIteGIneGNpiB7bmFtZX0iKQogICAgICAgIGlmIG5hbWVfY2hhbmdlZDoK"
    "ICAgICAgICAgICAgbGluZXMuYXBwZW5kKCIgICDihLnvuI8gKOGIteGImSDhiIvhi60g4Yuo4YqQ4Ymg4YipIOGLqOGImOGIteGI"
    "mOGIrSDhiJjhiYDhi6jhiKrhi6vhi47hib0v4Ymw4Yuw4YyL4YyL4YiaIOGKreGNjeGJsOGJtuGJvSDhibDhiLXhibDhiqvhiq3h"
    "iIjhi4vhiI3hjaMg4Yid4Yqt4YqV4Yur4Ymx4YidIOGIteGImSDhiaDhiYHhiI3hjY0gKGJ1dHRvbikg4YiL4YutIOGIteGIiOGI"
    "muGJs+GLrSDhiqDhipXhi7Ug4Yqg4Yyt4YitIOGImOGIteGImOGIrSDhiaXhibsg4oCL4YiY4YiG4YqVIOGKoOGIiOGJoOGJteGN"
    "oiDhi53hiK3hi53hiK0g4YiY4Yio4YyDIOGKq+GIiCDhiaDwn5OdIOGImOGMjeGIiOGMqyDhi43hiLXhjKUg4Yur4Yi14YmA4Yid"
    "4Yyh4Y2iKSIpCiAgICBsaW5lcy5hcHBlbmQoZiLwn46fIOGJsuGKrOGJtuGJveGNpiB7Y291bnR9IikKICAgIGxpbmVzLmFwcGVu"
    "ZChmIvCfkrUg4YuL4YyL4Y2mIHtwcmljZTouMGZ9IOGJpeGIrS/hibLhiqzhibUiKQogICAgbGluZXMuYXBwZW5kKGYi8J+SsCDh"
    "jKDhiYXhiIvhiIsg4Yuo4Yia4Yyg4Ymg4YmFIOGMiOGJouGNpiB7Y291bnQgKiBwcmljZTosLjBmfSDhiaXhiK0iKQogICAgaWYg"
    "ZGVzY3JpcHRpb246CiAgICAgICAgbGluZXMuYXBwZW5kKGYi8J+TnSDhiJjhjI3hiIjhjKvhjaYge2Rlc2NyaXB0aW9ufSIpCiAg"
    "ICBpZiBwcml6ZXM6CiAgICAgICAgbGluZXMuYXBwZW5kKCLwn4+GIOGIveGIjeGIm+GJtuGJveGNpiIpCiAgICAgICAgbGluZXMu"
    "ZXh0ZW5kKF9mb3JtYXRfcHJpemVzX2xpbmVzKHByaXplcykpCiAgICBsaW5lcy5hcHBlbmQoZiLwn5a8IOGIneGIteGIjeGNpiB7"
    "J+GKoOGIiCcgaWYgaW1hZ2VfZmlsZV9pZCBlbHNlICfhi6jhiIjhiJ0nfSIpCiAgICBsaW5lcy5hcHBlbmQoIlxu4Yut4YiF4YqV"
    "IOGLmeGIrSDhiJjhjY3hjKDhiK0g4Yut4Y2I4YiN4YyL4YiJPyIpCgogICAga2IgPSBJbmxpbmVLZXlib2FyZE1hcmt1cChbWwog"
    "ICAgICAgIElubGluZUtleWJvYXJkQnV0dG9uKCLinIUg4Y2N4Yyg4YitIiwgY2FsbGJhY2tfZGF0YT0ibmV3cm91bmRjb25maXJt"
    "IiksCiAgICAgICAgSW5saW5lS2V5Ym9hcmRCdXR0b24oIuKdjCDhibDhi4jhi40iLCBjYWxsYmFja19kYXRhPSJuZXdyb3VuZGNh"
    "bmNlbCIpLAogICAgXV0pCiAgICBpZiBpbWFnZV9maWxlX2lkOgogICAgICAgIGF3YWl0IGNvbnRleHQuYm90LnNlbmRfcGhvdG8o"
    "CiAgICAgICAgICAgIGNoYXRfaWQ9YWRtaW5faWQsIHBob3RvPWltYWdlX2ZpbGVfaWQsCiAgICAgICAgICAgIGNhcHRpb249Ilxu"
    "Ii5qb2luKGxpbmVzKSwgcGFyc2VfbW9kZT0iSFRNTCIsIHJlcGx5X21hcmt1cD1rYiwKICAgICAgICApCiAgICBlbHNlOgogICAg"
    "ICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIlxuIi5qb2luKGxpbmVzKSwgcGFyc2VfbW9kZT0iSFRNTCIsIHJl"
    "cGx5X21hcmt1cD1rYikKCmFzeW5jIGRlZiBoYW5kbGVfbmV3cm91bmRfY29uZmlybSh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDog"
    "Q29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiIvbmV3cm91bmQg4YiL4YutIOGLqOGImOGMqOGIqOGIu+GLjeGKlSDC"
    "q+KchSDhjY3hjKDhiK3CuyDhiYHhiI3hjY0g4Yiy4Yyr4YqRIOGKpeGLjeGKkOGJsOGKm+GLjeGKlSDhi5nhiK0g4YiY4Y2N4Yyg"
    "4YitIOGLqOGImuGNiOGMveGInSBjYWxsYmFjayIiIgogICAgcXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIGlmIHF1"
    "ZXJ5LmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIHJldHVybgog"
    "ICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgIGRhdGEgPSBwZW5kaW5nX3JvdW5kX2NvbmZpcm0ucG9wKHF1ZXJ5LmZyb21fdXNl"
    "ci5pZCwgTm9uZSkKICAgIGlmIG5vdCBkYXRhOgogICAgICAgIHRyeToKICAgICAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNz"
    "YWdlX3RleHQoIuKaoO+4jyDhi63hiIUg4Yyl4Yur4YmEIOGMiuGLnOGLjSDhiqDhiI3hjY7hiaDhibPhiI3hjaMgL25ld3JvdW5k"
    "IOGLsOGMjeGImOGLjSDhi63hjIDhiJ3hiKnhjaIiKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgIHBhc3MK"
    "ICAgICAgICByZXR1cm4KICAgIHRyeToKICAgICAgICBpZiBxdWVyeS5tZXNzYWdlLnBob3RvOgogICAgICAgICAgICBhd2FpdCBx"
    "dWVyeS5lZGl0X21lc3NhZ2VfY2FwdGlvbihjYXB0aW9uPSLij7Mg4YuZ4YitIOGJoOGImOGNjeGMoOGIrSDhiIvhi60uLi4iKQog"
    "ICAgICAgIGVsc2U6CiAgICAgICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KCLij7Mg4YuZ4YitIOGJoOGImOGN"
    "jeGMoOGIrSDhiIvhi60uLi4iKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICBwYXNzCiAgICBmYWtlX3VwZGF0ZSA9IF9G"
    "YWtlVXBkYXRlKF9GYWtlTXNnKHF1ZXJ5Lm1lc3NhZ2UsIHF1ZXJ5LmZyb21fdXNlcikpCiAgICBhd2FpdCBfY3JlYXRlX3JvdW5k"
    "KAogICAgICAgIGZha2VfdXBkYXRlLCBjb250ZXh0LAogICAgICAgIGRhdGFbImNvdW50Il0sIGRhdGFbInByaWNlIl0sCiAgICAg"
    "ICAgbmFtZT1kYXRhWyJuYW1lIl0sIGRlc2NyaXB0aW9uPWRhdGFbImRlc2NyaXB0aW9uIl0sCiAgICAgICAgcHJpemVzPWRhdGEu"
    "Z2V0KCJwcml6ZXMiKSwgaW1hZ2VfZmlsZV9pZD1kYXRhWyJpbWFnZV9maWxlX2lkIl0sCiAgICApCgphc3luYyBkZWYgaGFuZGxl"
    "X25ld3JvdW5kX2NhbmNlbCh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAi"
    "IiIvbmV3cm91bmQg4YiL4YutIMKr4p2MIOGJsOGLiOGLjcK7IOGJgeGIjeGNjSDhiLLhjKvhipEg4Yur4YiIIOGIneGKleGInSDh"
    "jY3hjKXhiKjhibUg4Yuo4Yia4Yur4YmL4Yit4YylIGNhbGxiYWNrIiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVy"
    "eQogICAgaWYgcXVlcnkuZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAg"
    "ICAgcmV0dXJuCiAgICBwZW5kaW5nX3JvdW5kX2NvbmZpcm0ucG9wKHF1ZXJ5LmZyb21fdXNlci5pZCwgTm9uZSkKICAgIGF3YWl0"
    "IHF1ZXJ5LmFuc3dlcigi4Ymw4Yiw4Yit4Yuf4YiN4Y2iIikKICAgIHRyeToKICAgICAgICBpZiBxdWVyeS5tZXNzYWdlLnBob3Rv"
    "OgogICAgICAgICAgICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfY2FwdGlvbihjYXB0aW9uPSLinYwg4Ymw4Yiw4Yit4Yuf4YiN"
    "4Y2jIOGIneGKleGInSDhiqDhi7LhiLUg4YuZ4YitIOGKoOGIjeGJsOGNiOGMoOGIqOGIneGNoiIpCiAgICAgICAgZWxzZToKICAg"
    "ICAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoIuKdjCDhibDhiLDhiK3hi5/hiI3hjaMg4Yid4YqV4YidIOGK"
    "oOGLsuGItSDhi5nhiK0g4Yqg4YiN4Ymw4Y2I4Yyg4Yio4Yid4Y2iIikKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcGFz"
    "cwoKYXN5bmMgZGVmIF9jcmVhdGVfcm91bmQodXBkYXRlLCBjb250ZXh0LCBjb3VudCwgcHJpY2UsIG5hbWU9Tm9uZSwgZGVzY3Jp"
    "cHRpb249Tm9uZSwgcHJpemVzPU5vbmUsIGltYWdlX2ZpbGVfaWQ9Tm9uZSk6CiAgICBnbG9iYWwgbmV4dF9yb3VuZF9pZAoKICAg"
    "IGlmIGNvdW50IDwgMToKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Yuo4Ymy4Yqs4Ym1"
    "IOGJpeGLm+GJtSDhiqgxIOGJoOGIi+GLrSDhiJjhiIbhipUg4Yqg4YiI4Ymg4Ym14Y2iIikKICAgICAgICByZXR1cm4KICAgIGlm"
    "IHByaWNlIDw9IDA6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4pqg77iPIOGLi+GMi+GLjSDhiqgw"
    "IOGJoOGIi+GLrSDhiJjhiIbhipUg4Yqg4YiI4Ymg4Ym14Y2iIikKICAgICAgICByZXR1cm4KCiAgICBwcml6ZXMgPSBwcml6ZXMg"
    "b3IgW10KICAgIHJvdW5kX2lkID0gbmV4dF9yb3VuZF9pZAogICAgbmV4dF9yb3VuZF9pZCArPSAxCiAgICByb3VuZHNbcm91bmRf"
    "aWRdID0gewogICAgICAgICJuYW1lIjogbmFtZSwKICAgICAgICAiZGVzY3JpcHRpb24iOiBkZXNjcmlwdGlvbiwKICAgICAgICAi"
    "cHJpemVzIjogcHJpemVzLCAgIyBbc3RyLCAuLi5dIOGJoOGLsOGIqOGMgyDhibDhiqjhibXhiI4gKDHhipsvMuGKmy8z4YqbLi4u"
    "KSAtIOGKqOGImOGNjeGMoOGIquGLqyDhjIrhi5wg4Yuo4Ymw4Yur4YuYIOGLqOGIveGIjeGIm+GJtSDhi53hiK3hi53hiK0KICAg"
    "ICAgICAiaW1hZ2VfZmlsZV9pZCI6IGltYWdlX2ZpbGVfaWQsCiAgICAgICAgIm51bV90aWNrZXRzIjogY291bnQsCiAgICAgICAg"
    "InByaWNlIjogcHJpY2UsCiAgICAgICAgInN0YXR1cyI6ICJPUEVOIiwKICAgICAgICAidGlja2V0cyI6IGJ1aWxkX3RpY2tldHMo"
    "Y291bnQpLAogICAgICAgICJ3aW5uZXJzIjogW10sICAjIFt7InRpY2tldF9udW0iOiBpbnQsICJwcml6ZSI6IHN0cnxOb25lLCAi"
    "c2V0X2F0IjogZGF0ZXRpbWV9LCAuLi5dIC0g4Yql4YyjIOGKqOGLiOGMoyDhiaDhiovhiIsgL3NldHdpbm5lciDhi63hiJ7hiIvh"
    "iI0KICAgIH0KICAgIHNhdmVfc3RhdGUoKQoKICAgIHN1bW1hcnkgPSAoCiAgICAgICAgZiLinIUge3JvdW5kX2xhYmVsKHJvdW5k"
    "X2lkKX0g4Ymw4Yqo4Y2N4Ym34YiNIVxuIgogICAgICAgIGYi8J+OnyDhibLhiqzhibbhib3hjaYge2NvdW50fVxuIgogICAgICAg"
    "IGYi8J+StSDhi4vhjIvhjaYge3ByaWNlOi4wZn0g4Yml4YitL+GJsuGKrOGJtSIKICAgICkKICAgIGlmIGRlc2NyaXB0aW9uOgog"
    "ICAgICAgIHN1bW1hcnkgKz0gZiJcbvCfk50g4YiY4YyN4YiI4Yyr4Y2mIHtkZXNjcmlwdGlvbn0iCiAgICBpZiBwcml6ZXM6CiAg"
    "ICAgICAgc3VtbWFyeSArPSAiXG7wn4+GIOGIveGIjeGIm+GJtuGJveGNplxuIiArICJcbiIuam9pbihfZm9ybWF0X3ByaXplc19s"
    "aW5lcyhwcml6ZXMpKQogICAgdW5kb19rYiA9IElubGluZUtleWJvYXJkTWFya3VwKFtbCiAgICAgICAgSW5saW5lS2V5Ym9hcmRC"
    "dXR0b24oIvCflJkgVW5kbyAo4Yut4YiF4YqVIOGLmeGIrSDhiqDhjKXhjYspIiwgY2FsbGJhY2tfZGF0YT1mInVuZG9yb3VuZF97"
    "cm91bmRfaWR9IiksCiAgICBdXSkKICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoc3VtbWFyeSwgcmVwbHlfbWFy"
    "a3VwPXVuZG9fa2IpCgogICAgbm90aWZpZWQgPSAwCiAgICBmYWlsZWQgPSAwCiAgICBwcml6ZXNfYmxvY2sgPSAoIvCfj4Yg4Yi9"
    "4YiN4Yib4Ym24Ym94Y2mXG4iICsgIlxuIi5qb2luKF9mb3JtYXRfcHJpemVzX2xpbmVzKHByaXplcykpICsgIlxuXG4iKSBpZiBw"
    "cml6ZXMgZWxzZSAiIgogICAgcGxheWVyX2NhcHRpb24gPSAoCiAgICAgICAgZiLwn46yIDxiPuGKoOGLsuGItSDhi5nhiK0gKHty"
    "b3VuZF9sYWJlbChyb3VuZF9pZCl9KSDhibDhiqjhjY3hibfhiI0hPC9iPlxuXG4iCiAgICAgICAgZiLwn46fIHtjb3VudH0g4Ymy"
    "4Yqs4Ym24Ym9IOGJoHtwcmljZTouMGZ9IOGJpeGIrSDhiqXhi6vhipXhi7PhipXhi7HhjaJcbiIKICAgICAgICArIChmIvCfk50g"
    "e2Rlc2NyaXB0aW9ufVxuXG4iIGlmIGRlc2NyaXB0aW9uIGVsc2UgIlxuIikKICAgICAgICArIHByaXplc19ibG9jawogICAgICAg"
    "ICsgZiLhiIjhiJjhiLPhibDhjY0gL3BsYXkge3JvdW5kX2lkfSDhiaXhiIjhi40g4Yut4Yyr4YqR4Y2iIgogICAgKQogICAgZm9y"
    "IHVpZCBpbiBwbGF5ZXJzOgogICAgICAgIHRyeToKICAgICAgICAgICAgaWYgaW1hZ2VfZmlsZV9pZDoKICAgICAgICAgICAgICAg"
    "IGF3YWl0IGNvbnRleHQuYm90LnNlbmRfcGhvdG8oY2hhdF9pZD11aWQsIHBob3RvPWltYWdlX2ZpbGVfaWQsIGNhcHRpb249cGxh"
    "eWVyX2NhcHRpb24sIHBhcnNlX21vZGU9IkhUTUwiKQogICAgICAgICAgICBlbHNlOgogICAgICAgICAgICAgICAgYXdhaXQgY29u"
    "dGV4dC5ib3Quc2VuZF9tZXNzYWdlKGNoYXRfaWQ9dWlkLCB0ZXh0PXBsYXllcl9jYXB0aW9uLCBwYXJzZV9tb2RlPSJIVE1MIikK"
    "ICAgICAgICAgICAgbm90aWZpZWQgKz0gMQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgIGZhaWxlZCArPSAx"
    "CgogICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dChmIvCfk6Mg4Yib4Yiz4YuI4YmC4Yur4YuNIOGIiHtub3RpZmll"
    "ZH0g4Ymw4Yyr4YuL4Ym+4Ym9IOGJsOGIjeGKs+GIjeGNoiAoe2ZhaWxlZH0g4Yqg4YiN4Ymw4Yiz4Yqr4YidKSIpCgphc3luYyBk"
    "ZWYgaGFuZGxlX25ld3JvdW5kX2Zsb3codXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUp"
    "OgogICAgIiIi4Yqg4Yu14Yia4YqVIC9uZXdyb3VuZCDhi6vhiIggYXJncyDhiqvhiLXhjIDhiJjhiKgg4Ymg4YqL4YiLIOGIteGI"
    "nS/hibLhiqzhibUg4Yml4Yub4Ym1L+GLi+GMiy/hiJjhjI3hiIjhjKsv4Yi94YiN4Yib4Ym24Ym9L+GIneGIteGIjSDhi6jhiJrh"
    "i6vhiLXhjIjhiaPhiaDhibUg4Yuw4Yio4YyD4Y2iCiAgICDhiaDhiJvhipXhipvhi43hiJ0g4Yuw4Yio4YyDIOGIi+GLrSDCqy9i"
    "YWNrwrsvwqvhibDhiJjhiIjhiLXCuyDhiqjhibDhiIvhiqgg4YuI4YuwIOGJgOGLsOGImOGLjSDhi7DhiKjhjIMg4Yml4Ym7IOGL"
    "reGImOGIjeGIs+GIjSAo4YiC4Yuw4Ymx4YqVIOGKoOGLq+GJi+GIreGMpeGInSnhjaIiIiIKICAgIHVzZXJfaWQgPSB1cGRhdGUu"
    "bWVzc2FnZS5mcm9tX3VzZXIuaWQKICAgIHN0YXRlID0gbmV3cm91bmRfc3RhdGUuZ2V0KHVzZXJfaWQpCiAgICB0ZXh0ID0gdXBk"
    "YXRlLm1lc3NhZ2UudGV4dC5zdHJpcCgpCgogICAgaWYgX2lzX2JhY2tfdGV4dCh0ZXh0KToKICAgICAgICBhd2FpdCBfbmV3cm91"
    "bmRfc3RlcF9iYWNrKHVwZGF0ZSwgY29udGV4dCwgdXNlcl9pZCkKICAgICAgICByZXR1cm4KCiAgICBpZiBzdGF0ZSA9PSAiYXdh"
    "aXRpbmdfbmFtZSI6CiAgICAgICAgaWYgbm90IF9pc19za2lwX3RleHQodGV4dCk6CiAgICAgICAgICAgIG5ld3JvdW5kX3RlbXBb"
    "dXNlcl9pZF1bIm5hbWUiXSA9IHRleHQKICAgICAgICBhd2FpdCBfcHJvbXB0X25ld3JvdW5kX3N0ZXAodXBkYXRlLCBjb250ZXh0"
    "LCB1c2VyX2lkLCAiYXdhaXRpbmdfY291bnQiKQogICAgICAgIHJldHVybgoKICAgIGlmIHN0YXRlID09ICJhd2FpdGluZ19jb3Vu"
    "dCI6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBjb3VudCA9IGludCh0ZXh0KQogICAgICAgIGV4Y2VwdCBWYWx1ZUVycm9yOgog"
    "ICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Yql4Ymj4Yqt4YuOIOGJteGKreGKreGI"
    "iOGKmyDhiYHhjKXhiK0g4Yml4Ym7IOGLreGIi+GKqSAo4YiI4Yid4Yiz4YiM4Y2mIDEwMCnhjaIiKQogICAgICAgICAgICByZXR1"
    "cm4KICAgICAgICBpZiBjb3VudCA8IDE6CiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKaoO+4"
    "jyDhiYHhjKXhiKkg4YqoMSDhiaDhiIvhi60g4YiY4YiG4YqVIOGKoOGIiOGJoOGJteGNoiIpCiAgICAgICAgICAgIHJldHVybgoK"
    "ICAgICAgICBuZXdyb3VuZF90ZW1wW3VzZXJfaWRdWyJjb3VudCJdID0gY291bnQKICAgICAgICBhd2FpdCBfcHJvbXB0X25ld3Jv"
    "dW5kX3N0ZXAodXBkYXRlLCBjb250ZXh0LCB1c2VyX2lkLCAiYXdhaXRpbmdfcHJpY2UiKQogICAgICAgIHJldHVybgoKICAgIGlm"
    "IHN0YXRlID09ICJhd2FpdGluZ19wcmljZSI6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBwcmljZSA9IGZsb2F0KHRleHQucmVw"
    "bGFjZSgiLCIsICIiKSkKICAgICAgICBleGNlcHQgVmFsdWVFcnJvcjoKICAgICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2Uu"
    "cmVwbHlfdGV4dCgi4pqg77iPIOGKpeGJo+GKreGLjiDhibXhiq3hiq3hiIjhipsg4YuL4YyLIOGJgeGMpeGIrSDhiaXhibsg4Yut"
    "4YiL4YqpICjhiIjhiJ3hiLPhiIzhjaYgNTAp4Y2iIikKICAgICAgICAgICAgcmV0dXJuCgogICAgICAgIG5ld3JvdW5kX3RlbXBb"
    "dXNlcl9pZF1bInByaWNlIl0gPSBwcmljZQogICAgICAgIGF3YWl0IF9wcm9tcHRfbmV3cm91bmRfc3RlcCh1cGRhdGUsIGNvbnRl"
    "eHQsIHVzZXJfaWQsICJhd2FpdGluZ19kZXNjcmlwdGlvbiIpCiAgICAgICAgcmV0dXJuCgogICAgaWYgc3RhdGUgPT0gImF3YWl0"
    "aW5nX2Rlc2NyaXB0aW9uIjoKICAgICAgICBpZiBub3QgX2lzX3NraXBfdGV4dCh0ZXh0KToKICAgICAgICAgICAgbmV3cm91bmRf"
    "dGVtcFt1c2VyX2lkXVsiZGVzY3JpcHRpb24iXSA9IHRleHQKICAgICAgICBhd2FpdCBfcHJvbXB0X25ld3JvdW5kX3N0ZXAodXBk"
    "YXRlLCBjb250ZXh0LCB1c2VyX2lkLCAiYXdhaXRpbmdfcHJpemVzIikKICAgICAgICByZXR1cm4KCiAgICBpZiBzdGF0ZSA9PSAi"
    "YXdhaXRpbmdfcHJpemVzIjoKICAgICAgICBpZiBfaXNfc2tpcF90ZXh0KHRleHQpOgogICAgICAgICAgICBhd2FpdCBfcHJvbXB0"
    "X25ld3JvdW5kX3N0ZXAodXBkYXRlLCBjb250ZXh0LCB1c2VyX2lkLCAiYXdhaXRpbmdfaW1hZ2UiKQogICAgICAgIGVsc2U6CiAg"
    "ICAgICAgICAgIGNsZWFuZWQgPSBfY2xlYW5fcHJpemVfbGluZSh0ZXh0KQogICAgICAgICAgICBuZXdyb3VuZF90ZW1wW3VzZXJf"
    "aWRdLnNldGRlZmF1bHQoInByaXplcyIsIFtdKS5hcHBlbmQoY2xlYW5lZCkKICAgICAgICAgICAgYXdhaXQgX3Byb21wdF9uZXdy"
    "b3VuZF9zdGVwKHVwZGF0ZSwgY29udGV4dCwgdXNlcl9pZCwgImF3YWl0aW5nX3ByaXplcyIpCiAgICAgICAgcmV0dXJuCgogICAg"
    "aWYgc3RhdGUgPT0gImF3YWl0aW5nX2ltYWdlIjoKICAgICAgICBpZiBfaXNfc2tpcF90ZXh0KHRleHQpOgogICAgICAgICAgICB0"
    "ZW1wID0gbmV3cm91bmRfdGVtcC5wb3AodXNlcl9pZCwge30pCiAgICAgICAgICAgIG5ld3JvdW5kX3N0YXRlLnBvcCh1c2VyX2lk"
    "LCBOb25lKQogICAgICAgICAgICBhd2FpdCBfc2hvd19uZXdyb3VuZF9jb25maXJtKAogICAgICAgICAgICAgICAgdXBkYXRlLCBj"
    "b250ZXh0LAogICAgICAgICAgICAgICAgdGVtcC5nZXQoImNvdW50IiksIHRlbXAuZ2V0KCJwcmljZSIpLAogICAgICAgICAgICAg"
    "ICAgbmFtZT10ZW1wLmdldCgibmFtZSIpLCBkZXNjcmlwdGlvbj10ZW1wLmdldCgiZGVzY3JpcHRpb24iKSwKICAgICAgICAgICAg"
    "ICAgIHByaXplcz10ZW1wLmdldCgicHJpemVzIiksCiAgICAgICAgICAgICkKICAgICAgICBlbHNlOgogICAgICAgICAgICBhd2Fp"
    "dCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLwn5a8IOGIneGIteGIjSDhiaDhjL3hiIHhjY0g4Yib4Yi14YyI4Ymj4Ym1IOGK"
    "oOGLreGJu+GIjeGIneGNoiDhjY7hibYg4Yut4YiL4YqpIOGLiOGLreGInSAvc2tpcCDhi63hjKvhipHhjaIiKQogICAgICAgIHJl"
    "dHVybgoKYXN5bmMgZGVmIGhhbmRsZV9uZXdyb3VuZF9pbWFnZSh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVz"
    "LkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhi6gvbmV3cm91bmQg4Yuw4Yio4YyDIOGIi+GLrSDhiJ3hiLXhiI0gKOGNjuGJtikg4Yiy"
    "4YiL4YqtIOGLqOGImuGLq+GLnSBoYW5kbGVyIiIiCiAgICB1c2VyX2lkID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkCiAg"
    "ICBpZiBuZXdyb3VuZF9zdGF0ZS5nZXQodXNlcl9pZCkgIT0gImF3YWl0aW5nX2ltYWdlIjoKICAgICAgICByZXR1cm4KCiAgICBp"
    "bWFnZV9maWxlX2lkID0gdXBkYXRlLm1lc3NhZ2UucGhvdG9bLTFdLmZpbGVfaWQKICAgIHRlbXAgPSBuZXdyb3VuZF90ZW1wLnBv"
    "cCh1c2VyX2lkLCB7fSkKICAgIG5ld3JvdW5kX3N0YXRlLnBvcCh1c2VyX2lkLCBOb25lKQogICAgYXdhaXQgX3Nob3dfbmV3cm91"
    "bmRfY29uZmlybSgKICAgICAgICB1cGRhdGUsIGNvbnRleHQsCiAgICAgICAgdGVtcC5nZXQoImNvdW50IiksIHRlbXAuZ2V0KCJw"
    "cmljZSIpLAogICAgICAgIG5hbWU9dGVtcC5nZXQoIm5hbWUiKSwgZGVzY3JpcHRpb249dGVtcC5nZXQoImRlc2NyaXB0aW9uIiks"
    "CiAgICAgICAgcHJpemVzPXRlbXAuZ2V0KCJwcml6ZXMiKSwKICAgICAgICBpbWFnZV9maWxlX2lkPWltYWdlX2ZpbGVfaWQsCiAg"
    "ICApCgphc3luYyBkZWYgY2xvc2Vfcm91bmQodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZ"
    "UEUpOgogICAgIiIi4YqV4YmBIOGLmeGIreGKlSDhi6jhiJrhi5jhjIsg4Ym14YuV4Yub4YudICjhi43hiILhiaEg4YyN4YqVIOGI"
    "iOGImOGLneGMiOGJpSDhi63hiYDhiKvhiI0pCiAgICDhiqDhjKDhiYPhiYDhiJ3hjaYgL2Nsb3Nlcm91bmQgPOGLmeGIrT4iIiIK"
    "ICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAgICBhcmdzID0g"
    "Y29udGV4dC5hcmdzCiAgICBpZiBub3QgYXJnczoKICAgICAgICBhd2FpdCBfc2VuZF9yb3VuZF9waWNrZXIoCiAgICAgICAgICAg"
    "IHVwZGF0ZSwgY29udGV4dCwgImNsb3NlIiwKICAgICAgICAgICAgW3JpZCBmb3IgcmlkLCByIGluIHJvdW5kcy5pdGVtcygpIGlm"
    "IHJbInN0YXR1cyJdID09ICJPUEVOIl0sCiAgICAgICAgICAgICLwn5SSIOGLqOGJteGKm+GLjeGKlSDhi5nhiK0g4YiY4Yud4YyL"
    "4Ym1IOGLreGNiOGIjeGMi+GIiT8iLAogICAgICAgICAgICAi4oS577iPIOGJoOGKoOGIgeGKkSDhiLDhi5PhibUg4YiK4YuY4YyL"
    "IOGLqOGImuGJveGIjSDhipXhiYEgKE9QRU4pIOGLmeGIrSDhi6jhiIjhiJ3hjaIiCiAgICAgICAgKQogICAgICAgIHJldHVybgoK"
    "ICAgIHRyeToKICAgICAgICByb3VuZF9pZCA9IGludChhcmdzWzBdKQogICAgZXhjZXB0IFZhbHVlRXJyb3I6CiAgICAgICAgYXdh"
    "aXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4pqg77iPIOGJteGKreGKreGIiOGKmyDhi6jhi5nhiK0g4YmB4Yyl4YitIOGL"
    "q+GIteGMiOGJoeGNoiIpCiAgICAgICAgcmV0dXJuCgogICAgaWYgcm91bmRfaWQgbm90IGluIHJvdW5kczoKICAgICAgICBhd2Fp"
    "dCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLinYwg4Yql4YqV4Yuw4Yua4YiFIOGLq+GIiCDhi5nhiK0g4Yqg4YiN4Ymw4YyI"
    "4YqY4Yid4Y2iIikKICAgICAgICByZXR1cm4KCiAgICBpZiByb3VuZHNbcm91bmRfaWRdWyJzdGF0dXMiXSA9PSAiQ0xPU0VEIjoK"
    "ICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KGYi4oS577iPIOGLmeGIrSB7cm91bmRfaWR9IOGKoOGIteGJ"
    "gOGLteGIniDhibDhi5jhjI3hibfhiI3hjaIiKQogICAgICAgIHJldHVybgoKICAgIHJvdW5kc1tyb3VuZF9pZF1bInN0YXR1cyJd"
    "ID0gIkNMT1NFRCIKICAgIHNhdmVfc3RhdGUoKQogICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dChmIvCflJIge3Jv"
    "dW5kX2xhYmVsKHJvdW5kX2lkKX0g4Ymw4YuY4YyN4Ym34YiN4Y2iICgvbm90aWZ5ZHJhdyB7cm91bmRfaWR9IOGJsOGMoOGJheGI"
    "mOGLjSDhjIjhi6Lhi47hib3hipUg4Yib4Yiz4YuI4YmFIOGLreGJveGIi+GIiSkiKQoKYXN5bmMgZGVmIHBhdXNlX3JvdW5kKHVw"
    "ZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIuGLmeGIreGKlSDhiIjhjIrh"
    "i5zhi40g4Yqo4Ymw4Yyr4YuL4Ym+4Ym9IOGLleGLreGJsyDhi6jhiJrhi7DhiaXhiYUg4Ym14YuV4Yub4YudICjhibLhiqzhibbh"
    "ibkg4Yql4YqV4Yuw4Ymw4Yur4YuZIOGLreGJhuGLq+GIiSAtIOGKi+GIiyAvcmVzdW1lcm91bmQg4Yib4Yu14Yio4YyNIOGLreGJ"
    "u+GIi+GIjSkKICAgIOGKoOGMoOGJg+GJgOGIneGNpiAvcGF1c2Vyb3VuZCA84YuZ4YitPiIiIgogICAgaWYgdXBkYXRlLm1lc3Nh"
    "Z2UuZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgogICAgICAgIHJldHVybgoKICAgIGFyZ3MgPSBjb250ZXh0LmFyZ3MKICAgIGlm"
    "IG5vdCBhcmdzOgogICAgICAgIGF3YWl0IF9zZW5kX3JvdW5kX3BpY2tlcigKICAgICAgICAgICAgdXBkYXRlLCBjb250ZXh0LCAi"
    "cGF1c2UiLAogICAgICAgICAgICBbcmlkIGZvciByaWQsIHIgaW4gcm91bmRzLml0ZW1zKCkgaWYgclsic3RhdHVzIl0gPT0gIk9Q"
    "RU4iXSwKICAgICAgICAgICAgIuKPuCDhi6jhibXhipvhi43hipUg4YuZ4YitIOGIm+GJhuGInSDhi63hjYjhiI3hjIvhiIk/IiwK"
    "ICAgICAgICAgICAgIuKEue+4jyDhiaDhiqDhiIHhipEg4Yiw4YuT4Ym1IOGIiuGJhuGInSDhi6jhiJrhib3hiI0g4YqV4YmBIChP"
    "UEVOKSDhi5nhiK0g4Yuo4YiI4Yid4Y2iIgogICAgICAgICkKICAgICAgICByZXR1cm4KICAgIHRyeToKICAgICAgICByb3VuZF9p"
    "ZCA9IGludChhcmdzWzBdKQogICAgZXhjZXB0IFZhbHVlRXJyb3I6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlf"
    "dGV4dCgi4pqg77iPIOGJteGKreGKreGIiOGKmyDhi6jhi5nhiK0g4YmB4Yyl4YitIOGLq+GIteGMiOGJoeGNoiIpCiAgICAgICAg"
    "cmV0dXJuCiAgICBpZiByb3VuZF9pZCBub3QgaW4gcm91bmRzOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3Rl"
    "eHQoIuKdjCDhiqXhipXhi7Dhi5rhiIUg4Yur4YiIIOGLmeGIrSDhiqDhiI3hibDhjIjhipjhiJ3hjaIiKQogICAgICAgIHJldHVy"
    "bgogICAgaWYgcm91bmRzW3JvdW5kX2lkXVsic3RhdHVzIl0gIT0gIk9QRU4iOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdl"
    "LnJlcGx5X3RleHQoZiLihLnvuI8ge3JvdW5kX2xhYmVsKHJvdW5kX2lkKX0g4Ymg4Yqg4YiB4YqRIOGIsOGLk+GJtSBPUEVOIOGI"
    "teGIi+GIjeGIhuGKkCDhiJvhiYbhiJ0g4Yqg4Yut4Ym74YiN4YidICjhiIHhipThibPhjaYge3JvdW5kc1tyb3VuZF9pZF1bJ3N0"
    "YXR1cyddfSnhjaIiKQogICAgICAgIHJldHVybgoKICAgIHJvdW5kc1tyb3VuZF9pZF1bInN0YXR1cyJdID0gIlBBVVNFRCIKICAg"
    "IHNhdmVfc3RhdGUoKQogICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICBmIuKPuCB7cm91bmRfbGFi"
    "ZWwocm91bmRfaWQpfSDhiIjhjIrhi5zhi40g4YmG4Yif4YiN4Y2iIOGJsOGMq+GLi+GJvuGJvSDhiqDhi6vhi6nhibXhiJ3hjaMg"
    "4YqQ4YyI4YitIOGMjeGKlSDhibLhiqzhibbhibkg4Yql4YqV4Yuz4YiJIOGLreGJhuGLq+GIieGNolxuIgogICAgICAgIGYi4YiI"
    "4YiY4YmA4Yyg4YiN4Y2mIC9yZXN1bWVyb3VuZCB7cm91bmRfaWR9IgogICAgKQoKYXN5bmMgZGVmIHJlc3VtZV9yb3VuZCh1cGRh"
    "dGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhiYbhiJ4g4Yuo4YqQ4Ymg4Yio"
    "IOGLmeGIreGKlSDhiJjhiI3hiLYg4YqV4YmBIOGLqOGImuGLq+GLsOGIreGMjSDhibXhi5Xhi5vhi50KICAgIOGKoOGMoOGJg+GJ"
    "gOGIneGNpiAvcmVzdW1lcm91bmQgPOGLmeGIrT4iIiIKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1J"
    "Tl9JRDoKICAgICAgICByZXR1cm4KCiAgICBhcmdzID0gY29udGV4dC5hcmdzCiAgICBpZiBub3QgYXJnczoKICAgICAgICBhd2Fp"
    "dCBfc2VuZF9yb3VuZF9waWNrZXIoCiAgICAgICAgICAgIHVwZGF0ZSwgY29udGV4dCwgInJlc3VtZSIsCiAgICAgICAgICAgIFty"
    "aWQgZm9yIHJpZCwgciBpbiByb3VuZHMuaXRlbXMoKSBpZiByWyJzdGF0dXMiXSA9PSAiUEFVU0VEIl0sCiAgICAgICAgICAgICLi"
    "lrbvuI8g4Yuo4Ym14Yqb4YuN4YqVIOGLmeGIrSDhiJjhiYDhjKDhiI0g4Yut4Y2I4YiN4YyL4YiJPyIsCiAgICAgICAgICAgICLi"
    "hLnvuI8g4Ymg4Yqg4YiB4YqRIOGIsOGLk+GJtSBQQVVTRUQg4YiL4YutIOGLq+GIiCDhi5nhiK0g4Yuo4YiI4Yid4Y2iIgogICAg"
    "ICAgICkKICAgICAgICByZXR1cm4KICAgIHRyeToKICAgICAgICByb3VuZF9pZCA9IGludChhcmdzWzBdKQogICAgZXhjZXB0IFZh"
    "bHVlRXJyb3I6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4pqg77iPIOGJteGKreGKreGIiOGKmyDh"
    "i6jhi5nhiK0g4YmB4Yyl4YitIOGLq+GIteGMiOGJoeGNoiIpCiAgICAgICAgcmV0dXJuCiAgICBpZiByb3VuZF9pZCBub3QgaW4g"
    "cm91bmRzOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKdjCDhiqXhipXhi7Dhi5rhiIUg4Yur4YiI"
    "IOGLmeGIrSDhiqDhiI3hibDhjIjhipjhiJ3hjaIiKQogICAgICAgIHJldHVybgogICAgaWYgcm91bmRzW3JvdW5kX2lkXVsic3Rh"
    "dHVzIl0gIT0gIlBBVVNFRCI6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dChmIuKEue+4jyB7cm91bmRf"
    "bGFiZWwocm91bmRfaWQpfSBQQVVTRUQg4YiL4YutIOGKoOGLreGLsOGIiOGInSAo4YiB4YqU4Ymz4Y2mIHtyb3VuZHNbcm91bmRf"
    "aWRdWydzdGF0dXMnXX0p4Y2iIikKICAgICAgICByZXR1cm4KCiAgICByb3VuZHNbcm91bmRfaWRdWyJzdGF0dXMiXSA9ICJPUEVO"
    "IgogICAgc2F2ZV9zdGF0ZSgpCiAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KGYi4pa277iPIHtyb3VuZF9sYWJl"
    "bChyb3VuZF9pZCl9IOGKpeGKleGLsOGMiOGKkyDhipXhiYEg4YiG4YqX4YiN4Y2iIikKCmFzeW5jIGRlZiByZXN0YXJ0X3JvdW5k"
    "KHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIuGLmeGIreGKlSDhi4jh"
    "i7Ag4YiY4YyA4YiY4Yiq4Yur4YuNIOGIgeGKlOGJsyAo4YiB4YiJ4YidIOGJsuGKrOGJtuGJvSBBVkFJTEFCTEUpIOGLqOGImuGI"
    "mOGIjeGItSDhibXhi5Xhi5vhi50gLSDhiLXhiJ0v4YuL4YyLL+GJsuGKrOGJtSDhiaXhi5vhibUg4Yql4YqV4Yuw4Ymw4Yyg4Ymg"
    "4YmAIOGLreGJhuGLq+GIjQogICAg4Yib4Yi14Yyg4YqV4YmA4YmC4Yur4Y2mIOGKoOGIteGJgOGLteGIniDhi6jhibDhiLjhjKEg"
    "4Ymy4Yqs4Ym24Ym9IOGKq+GIiSDhi63hiLDhiKjhi5vhiIkgKOGMiOGLouGLjuGJvSDhiJvhiLPhi4jhiYLhi6sg4Yut4Yuw4Yit"
    "4Yiz4Ym44YuL4YiNKQogICAg4Yqg4Yyg4YmD4YmA4Yid4Y2mIC9yZXN0YXJ0cm91bmQgPOGLmeGIrT4iIiIKICAgIGlmIHVwZGF0"
    "ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAgICBhcmdzID0gY29udGV4dC5hcmdz"
    "CiAgICBpZiBub3QgYXJnczoKICAgICAgICBhd2FpdCBfc2VuZF9yb3VuZF9waWNrZXIoCiAgICAgICAgICAgIHVwZGF0ZSwgY29u"
    "dGV4dCwgInJlc3RhcnQiLAogICAgICAgICAgICBsaXN0KHJvdW5kcy5rZXlzKCkpLAogICAgICAgICAgICAi8J+UhCDhi6jhibXh"
    "ipvhi43hipUg4YuZ4YitIOGLs+GMjeGInSDhiJjhjIDhiJjhiK0g4Yut4Y2I4YiN4YyL4YiJPyIsCiAgICAgICAgICAgICLihLnv"
    "uI8g4Ymg4Yqg4YiB4YqRIOGIsOGLk+GJtSDhiJ3hipXhiJ0g4YuZ4YitIOGLqOGIiOGIneGNoiIKICAgICAgICApCiAgICAgICAg"
    "cmV0dXJuCiAgICB0cnk6CiAgICAgICAgcm91bmRfaWQgPSBpbnQoYXJnc1swXSkKICAgIGV4Y2VwdCBWYWx1ZUVycm9yOgogICAg"
    "ICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKaoO+4jyDhibXhiq3hiq3hiIjhipsg4Yuo4YuZ4YitIOGJgeGM"
    "peGIrSDhi6vhiLXhjIjhiaHhjaIiKQogICAgICAgIHJldHVybgogICAgaWYgcm91bmRfaWQgbm90IGluIHJvdW5kczoKICAgICAg"
    "ICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLinYwg4Yql4YqV4Yuw4Yua4YiFIOGLq+GIiCDhi5nhiK0g4Yqg4YiN"
    "4Ymw4YyI4YqY4Yid4Y2iIikKICAgICAgICByZXR1cm4KCiAgICByID0gcm91bmRzW3JvdW5kX2lkXQogICAgc29sZF9idXllcnMg"
    "PSBbKHRbInVzZXJfaWQiXSwgdF9udW0pIGZvciB0X251bSwgdCBpbiByWyJ0aWNrZXRzIl0uaXRlbXMoKSBpZiB0WyJzdGF0dXMi"
    "XSA9PSAiU09MRCIgYW5kIHRbInVzZXJfaWQiXV0KCiAgICByWyJ0aWNrZXRzIl0gPSBidWlsZF90aWNrZXRzKHJbIm51bV90aWNr"
    "ZXRzIl0pCiAgICByWyJzdGF0dXMiXSA9ICJPUEVOIgogICAgZm9yIHVpZCwgXyBpbiBsaXN0KHVzZXJfc2VsZWN0aW9ucy5pdGVt"
    "cygpKToKICAgICAgICBpZiB1c2VyX3NlbGVjdGlvbnNbdWlkXVsicm91bmRfaWQiXSA9PSByb3VuZF9pZDoKICAgICAgICAgICAg"
    "ZGVsIHVzZXJfc2VsZWN0aW9uc1t1aWRdCiAgICBzYXZlX3N0YXRlKCkKCiAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90"
    "ZXh0KAogICAgICAgIGYi8J+UhCB7cm91bmRfbGFiZWwocm91bmRfaWQpfSDhi7PhjI3hiJ0g4Ymw4YyA4Yid4Yiv4YiN4Y2iIOGI"
    "geGIieGInSDhibLhiqzhibbhib0g4YuI4YuwIEFWQUlMQUJMRSDhibDhiJjhiI3hiLDhi4vhiI3hjaIiCiAgICApCgogICAgZm9y"
    "IHVpZCwgdF9udW0gaW4gc29sZF9idXllcnM6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5k"
    "X21lc3NhZ2UoCiAgICAgICAgICAgICAgICBjaGF0X2lkPXVpZCwKICAgICAgICAgICAgICAgIHRleHQ9ZiLimqDvuI8ge3JvdW5k"
    "X2xhYmVsKHJvdW5kX2lkKX0g4Yuz4YyN4YidIOGJsOGMgOGIneGIr+GIjeGNoyDhi6jhjIjhi5nhibUg4YmB4Yyl4YitIHt0X251"
    "bX0g4Ymw4Yiw4Yit4Yuf4YiN4Y2iIOGKpeGJo+GKreGLjiDhiqDhi7XhiJrhipHhipUg4Yi14YiIIOGJsOGImOGIi+GIvSDhjIjh"
    "ipXhi5jhiaUg4Yur4YqQ4YyL4YyN4Yip4Y2iIgogICAgICAgICAgICApCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAg"
    "ICAgICAgcGFzcwoKYXN5bmMgZGVmIGRlbGV0ZV9yb3VuZCh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRF"
    "RkFVTFRfVFlQRSk6CiAgICAiIiLhi5nhiK3hipUg4YiZ4YiJIOGJoOGImeGIiSDhi6jhiJrhi6vhjKDhjYsg4Ym14YuV4Yub4Yud"
    "4Y2iCiAgICDhiJ3hipXhiJ0g4Ymy4Yqs4Ym1IOGLq+GIjeGJsOGIuOGMoOGJoOGJtSDhi4jhi63hiJ0g4Yyl4YmC4Ym1IOGJsuGK"
    "rOGJtSDhiaXhibsg4Yuo4Ymw4Yi44Yyg4Ymg4Ym1IOGLmeGIrSDhiaXhibsg4Yur4YiIIOGJsOGMqOGIm+GIqiDhiJvhiKjhjIvh"
    "jIjhjKsg4Yut4Yyg4Y2L4YiN4Y2kCiAgICDhiaXhi5kg4Ymy4Yqs4Ym1IOGJsOGIuOGMpiDhiqjhiIbhipAg4Ymg4YiY4YyA4YiY"
    "4Yiq4YurIOGIm+GIqOGMi+GMiOGMqyDhi63hjKDhi6jhiYPhiI0g4Yql4YqTIOGIiOGMiOGLouGLjuGJvSDhi6jhibDhiJjhiIvh"
    "iL0g4YyI4YqV4YuY4YmlIOGIm+GIteGJs+GLiOGJguGLqyDhi63hiIvhiqvhiI3hjaIKICAgIOGKoOGMoOGJg+GJgOGIneGNpiAv"
    "ZGVsZXRlcm91bmQgPOGLmeGIrT4iIiIKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAg"
    "ICAgICByZXR1cm4KCiAgICBhcmdzID0gY29udGV4dC5hcmdzCiAgICBpZiBub3QgYXJnczoKICAgICAgICBhd2FpdCBfc2VuZF9y"
    "b3VuZF9waWNrZXIoCiAgICAgICAgICAgIHVwZGF0ZSwgY29udGV4dCwgImRlbGV0ZSIsCiAgICAgICAgICAgIGxpc3Qocm91bmRz"
    "LmtleXMoKSksCiAgICAgICAgICAgICLwn5eRIOGLqOGJteGKm+GLjeGKlSDhi5nhiK0g4Yib4Yyl4Y2L4Ym1IOGLreGNiOGIjeGM"
    "i+GIiT8iLAogICAgICAgICAgICAi4oS577iPIOGJoOGKoOGIgeGKkSDhiLDhi5PhibUg4Yid4YqV4YidIOGLmeGIrSDhi6jhiIjh"
    "iJ3hjaIiCiAgICAgICAgKQogICAgICAgIHJldHVybgogICAgdHJ5OgogICAgICAgIHJvdW5kX2lkID0gaW50KGFyZ3NbMF0pCiAg"
    "ICBleGNlcHQgVmFsdWVFcnJvcjoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Ym14Yqt"
    "4Yqt4YiI4YqbIOGLqOGLmeGIrSDhiYHhjKXhiK0g4Yur4Yi14YyI4Ymh4Y2iIikKICAgICAgICByZXR1cm4KICAgIGlmIHJvdW5k"
    "X2lkIG5vdCBpbiByb3VuZHM6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4p2MIOGKpeGKleGLsOGL"
    "muGIhSDhi6vhiIgg4YuZ4YitIOGKoOGIjeGJsOGMiOGKmOGIneGNoiIpCiAgICAgICAgcmV0dXJuCgogICAgciA9IHJvdW5kc1ty"
    "b3VuZF9pZF0KICAgIHNvbGQgPSB7dF9udW06IHQgZm9yIHRfbnVtLCB0IGluIHJbInRpY2tldHMiXS5pdGVtcygpIGlmIHRbInN0"
    "YXR1cyJdID09ICJTT0xEIn0KICAgIFNNQUxMX1RIUkVTSE9MRCA9IDIgICMg4Yur4YiIIOGJsOGMqOGIm+GIqiDhiJvhiKjhjIvh"
    "jIjhjKsg4Yir4YixIOGJoOGIq+GIsSDhiJvhjKXhjYvhibUg4Yuo4Yia4Y2I4YmA4Yu14Ymg4Ym1IOGLqOGJsOGIuOGMoCDhibLh"
    "iqzhibUg4YyI4Yuw4YmlCgogICAgaWYgbGVuKHNvbGQpID4gU01BTExfVEhSRVNIT0xEIGFuZCBwZW5kaW5nX2RlbGV0ZV9jb25m"
    "aXJtLmdldCh1cGRhdGUubWVzc2FnZS5mcm9tX3VzZXIuaWQpICE9IHJvdW5kX2lkOgogICAgICAgIHBlbmRpbmdfZGVsZXRlX2Nv"
    "bmZpcm1bdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkXSA9IHJvdW5kX2lkCiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2Uu"
    "cmVwbHlfdGV4dCgKICAgICAgICAgICAgZiLimqDvuI8ge3JvdW5kX2xhYmVsKHJvdW5kX2lkKX0g4YiL4YutIHtsZW4oc29sZCl9"
    "IOGJsuGKrOGJtuGJvSDhibDhiLjhjKDhi4vhiI3hjaJcbiIKICAgICAgICAgICAgZiLhiqvhjKDhjYnhibUg4YyI4Yui4YuO4Ym9"
    "IOGIneGKleGInSDhiJvhiLPhi4jhiYLhi6sg4Yqg4Yut4Yuw4Yit4Yiz4Ym44YuN4YidIChyZWZ1bmQg4Ymg4Yql4YyFIOGIm+GL"
    "teGIqOGMjSDhi6vhiLXhjYjhiI3hjIvhiI0p4Y2iXG5cbiIKICAgICAgICAgICAgZiLhiIjhiJvhiKjhjIvhjIjhjKUg4Yql4YqV"
    "4Yuw4YyI4YqTIOGLreGIi+GKqeGNpiAvZGVsZXRlcm91bmQge3JvdW5kX2lkfSIKICAgICAgICApCiAgICAgICAgcmV0dXJuCgog"
    "ICAgcGVuZGluZ19kZWxldGVfY29uZmlybS5wb3AodXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkLCBOb25lKQoKICAgIGZvciB1"
    "aWQsIHNlbCBpbiBsaXN0KHVzZXJfc2VsZWN0aW9ucy5pdGVtcygpKToKICAgICAgICBpZiBzZWxbInJvdW5kX2lkIl0gPT0gcm91"
    "bmRfaWQ6CiAgICAgICAgICAgIGRlbCB1c2VyX3NlbGVjdGlvbnNbdWlkXQoKICAgIGxhYmVsID0gcm91bmRfbGFiZWwocm91bmRf"
    "aWQpCiAgICBkZWwgcm91bmRzW3JvdW5kX2lkXQogICAgc2F2ZV9zdGF0ZSgpCiAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBs"
    "eV90ZXh0KGYi8J+XkSB7bGFiZWx9IOGImeGIiSDhiaDhiJnhiIkg4Yyg4Y2N4Ym34YiN4Y2iICh7bGVuKHNvbGQpfSDhibDhiLjh"
    "jKDhi40g4Yuo4YqQ4Ymg4YipIOGJsuGKrOGJtuGJvSAtIOGMiOGLouGLjuGJvSDhiJ3hipXhiJ0g4Yib4Yiz4YuI4YmC4YurIOGK"
    "oOGIjeGLsOGIqOGIs+GJuOGLjeGInSkiKQoKYXN5bmMgZGVmIGhhbmRsZV91bmRvX3JvdW5kKHVwZGF0ZTogVXBkYXRlLCBjb250"
    "ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIuGKoOGLsuGItSDhi5nhiK0g4YiN4YqtIOGKqOGJsOGNiOGM"
    "oOGIqCDhiaDhiovhiIsg4Ymg4Ymz4Yuo4YuNIMKr8J+UmSBVbmRvwrsg4YmB4YiN4Y2NIOGIsuGMq+GKkSDhi6vhipXhipEg4YuZ"
    "4YitIOGLiOGLsuGLq+GLjeGKkSDhi6jhiJrhi6vhjKDhjYsgY2FsbGJhY2sgLQogICAg4YqQ4Ymj4Yip4YqVIC9kZWxldGVyb3Vu"
    "ZCDhiqDhiJjhiq3hipXhi64gKOGJpeGLmSDhibLhiqzhibUg4Ymw4Yi44YymIOGKqOGIhuGKkCDhiJvhiKjhjIvhjIjhjKsg4Yuo"
    "4YiY4Yyg4Yuo4YmFIOGLsOGKleGJpeGKlSDhjKjhiJ3hiK4pIOGLq+GIiCDhi7XhjI3hjI3hiJ7hiL0g4Yut4Yyg4YmA4Yib4YiN"
    "4Y2iIiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgaWYgcXVlcnkuZnJvbV91c2VyLmlkICE9IEFETUlO"
    "X0lEOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgcmV0dXJuCiAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQog"
    "ICAgdHJ5OgogICAgICAgIHJvdW5kX2lkID0gaW50KHF1ZXJ5LmRhdGEuc3BsaXQoIl8iLCAxKVsxXSkKICAgIGV4Y2VwdCAoSW5k"
    "ZXhFcnJvciwgVmFsdWVFcnJvcik6CiAgICAgICAgcmV0dXJuCiAgICBpZiByb3VuZF9pZCBub3QgaW4gcm91bmRzOgogICAgICAg"
    "IHRyeToKICAgICAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3JlcGx5X21hcmt1cChyZXBseV9tYXJrdXA9Tm9uZSkK"
    "ICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICBwYXNzCiAgICAgICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9t"
    "ZXNzYWdlKGNoYXRfaWQ9QURNSU5fSUQsIHRleHQ9IuKEue+4jyDhi63hiIUg4YuZ4YitIOGKoOGIteGJgOGLteGIniDhjKDhjY3h"
    "ibfhiI0g4YuI4Yut4YidIOGKoOGIjeGJsOGMiOGKmOGIneGNoiIpCiAgICAgICAgcmV0dXJuCiAgICB0cnk6CiAgICAgICAgYXdh"
    "aXQgcXVlcnkuZWRpdF9tZXNzYWdlX3JlcGx5X21hcmt1cChyZXBseV9tYXJrdXA9Tm9uZSkKICAgIGV4Y2VwdCBFeGNlcHRpb246"
    "CiAgICAgICAgcGFzcwogICAgZmFrZV91cGRhdGUgPSBfRmFrZVVwZGF0ZShfRmFrZU1zZyhxdWVyeS5tZXNzYWdlLCBxdWVyeS5m"
    "cm9tX3VzZXIpKQogICAgZmFrZV9jb250ZXh0ID0gU2ltcGxlTmFtZXNwYWNlKGFyZ3M9W3N0cihyb3VuZF9pZCldLCBib3Q9Y29u"
    "dGV4dC5ib3QpCiAgICBhd2FpdCBkZWxldGVfcm91bmQoZmFrZV91cGRhdGUsIGZha2VfY29udGV4dCkKCmFzeW5jIGRlZiBkZWxl"
    "dGVfYWxsX3JvdW5kcyh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLh"
    "iIHhiInhipXhiJ0g4YuZ4Yiu4Ym9IOGImeGIiSDhiaDhiJnhiIkg4Yuo4Yia4Yur4Yyg4Y2LIOGKpeGKkyDhiqjhiaPhi7Yg4Yuo"
    "4Yia4YyA4Yid4YitIOGJteGLleGLm+GLnSAtIOGLqOGJsOGImOGLmOGMiOGJoSDhibDhjKvhi4vhib7hib0gKHBsYXllcnMpIOGM"
    "jeGKlSDhiqDhi63hipDhiqnhiJ3hjaIKICAgIOGIm+GKleGKm+GLjeGInSDhibLhiqzhibUg4Ymw4Yi44YymIOGKqOGIhuGKkCDh"
    "jIjhi6Lhi47hib0g4Yut4YiFIOGLmeGIrSDhibDhiLDhiK3hi54g4Ymw4YiY4YiL4Yi9IOGMiOGKleGLmOGJpSDhiqXhipXhi7Dh"
    "iJrhi7DhiKjhjI3hiIvhibjhi40g4Yib4Yiz4YuI4YmC4YurIOGLreGLsOGIreGIs+GJuOGLi+GIjeGNogogICAg4Yuw4YiF4YqV"
    "4YqQ4Ym1IOGIteGKleGIjeGNpiDhi6jhiJjhjIDhiJjhiKrhi6sg4Yyl4YiqIOGIm+GIqOGMi+GMiOGMqyDhiaXhibsg4Yut4Yyg"
    "4Yut4YmD4YiN4Y2jIDLhipsg4Ymw4YiY4Yiz4Yiz4YutIOGMpeGIqiDhiIvhi60g4Yml4Ym7IOGLreGNiOGMuOGIm+GIjeGNogog"
    "ICAg4Yqg4Yyg4YmD4YmA4Yid4Y2mIC9kZWxldGVhbGxyb3VuZHMiIiIKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5p"
    "ZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAgICBnbG9iYWwgbmV4dF9yb3VuZF9pZAogICAgYWRtaW5faWQgPSB1cGRh"
    "dGUubWVzc2FnZS5mcm9tX3VzZXIuaWQKCiAgICBpZiBub3Qgcm91bmRzOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJl"
    "cGx5X3RleHQoIuKEue+4jyDhiqXhiLXhiqvhiIHhipUg4Yid4YqV4YidIOGLmeGIrSDhi6jhiIjhiJ3hjaMg4Yib4Yyl4Y2L4Ym1"
    "IOGLqOGImuGLq+GIteGNiOGIjeGMjSDhipDhjIjhiK0g4Yuo4YiI4Yid4Y2iIikKICAgICAgICByZXR1cm4KCiAgICBpZiBwZW5k"
    "aW5nX2RlbGV0ZV9jb25maXJtLmdldChhZG1pbl9pZCkgIT0gIkFMTCI6CiAgICAgICAgcGVuZGluZ19kZWxldGVfY29uZmlybVth"
    "ZG1pbl9pZF0gPSAiQUxMIgogICAgICAgIHRvdGFsX3NvbGQgPSBzdW0oMSBmb3IgciBpbiByb3VuZHMudmFsdWVzKCkgZm9yIHQg"
    "aW4gclsidGlja2V0cyJdLnZhbHVlcygpIGlmIHRbInN0YXR1cyJdID09ICJTT0xEIikKICAgICAgICBhd2FpdCB1cGRhdGUubWVz"
    "c2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICBmIuKaoO+4jyDhi63hiIUge2xlbihyb3VuZHMpfSDhi5nhiK7hib3hipUgKOGJ"
    "oOGMoOGJheGIi+GIiyB7dG90YWxfc29sZH0g4Yuo4Ymw4Yi44YyhIOGJsuGKrOGJtuGJveGKlSDhjKjhiJ3hiK4pIOGImeGIiSDh"
    "iaDhiJnhiIkg4Yur4Yyg4Y2L4YiN4Y2iXG4iCiAgICAgICAgICAgIGYi8J+RpSDhi6jhibDhiJjhi5jhjIjhiaEg4Ymw4Yyr4YuL"
    "4Ym+4Ym9IChwbGF5ZXJzKSDhiqDhi63hipDhiqnhiJ0gLSDhiaXhibsg4YuZ4Yiu4Ym9IOGLreGMoOGNi+GIieGNolxuIgogICAg"
    "ICAgICAgICBmIvCflJUg4YyI4Yui4YuO4Ym9IOGIneGKleGInSDhiJvhiLPhi4jhiYLhi6sg4Yqg4Yut4Yuw4Yit4Yiz4Ym44YuN"
    "4YidIChyZWZ1bmQg4Ymg4Yql4YyFIOGIm+GLteGIqOGMjSDhi6vhiLXhjYjhiI3hjIvhiI0p4Y2iXG5cbiIKICAgICAgICAgICAg"
    "ZiLhiIjhiJvhiKjhjIvhjIjhjKUg4Yql4YqV4Yuw4YyI4YqTIOGLreGIi+GKqeGNpiAvZGVsZXRlYWxscm91bmRzIgogICAgICAg"
    "ICkKICAgICAgICByZXR1cm4KCiAgICBwZW5kaW5nX2RlbGV0ZV9jb25maXJtLnBvcChhZG1pbl9pZCwgTm9uZSkKCiAgICB0b3Rh"
    "bF9zb2xkID0gc3VtKDEgZm9yIHIgaW4gcm91bmRzLnZhbHVlcygpIGZvciB0IGluIHJbInRpY2tldHMiXS52YWx1ZXMoKSBpZiB0"
    "WyJzdGF0dXMiXSA9PSAiU09MRCIpCgogICAgcm91bmRzLmNsZWFyKCkKICAgIHVzZXJfc2VsZWN0aW9ucy5jbGVhcigpCiAgICB1"
    "c2VkX3Ntc19yZWZzLmNsZWFyKCkKICAgIHVubWF0Y2hlZF9zbXNfbG9nLmNsZWFyKCkKICAgIHRpY2tldF93YXRjaGVycy5jbGVh"
    "cigpCiAgICByZWNlaXB0X2NvbnRleHRzLmNsZWFyKCkKICAgIG5leHRfcm91bmRfaWQgPSAxCiAgICBzYXZlX3N0YXRlKCkKCiAg"
    "ICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgIGYi8J+XkSDhiIHhiInhiJ0g4YuZ4Yiu4Ym9IOGMoOGN"
    "jeGJsOGLi+GIjeGNoyDhiqjhiaPhi7Yg4YiI4YiY4YyA4YiY4YitIOGJsOGLmOGMi+GMheGJt+GIjSAo4Yuo4YuZ4YitIOGJgeGM"
    "peGIrSDhiqgxIOGLreGMgOGIneGIq+GIjSnhjaJcbiIKICAgICAgICBmIvCfkaUg4Yuo4Ymw4YiY4YuY4YyI4YmhIOGJsOGMq+GL"
    "i+GJvuGJvSDhiqXhipXhi7Ag4YqQ4Ymg4YipIOGJgOGIreGJsOGLi+GIjeGNolxuIgogICAgICAgIGYi8J+UlSAoe3RvdGFsX3Nv"
    "bGR9IOGJsOGIuOGMoOGLjSDhi6jhipDhiaDhiKkg4Ymy4Yqs4Ym24Ym9IC0g4YyI4Yui4YuO4Ym9IOGIneGKleGInSDhiJvhiLPh"
    "i4jhiYLhi6sg4Yqg4YiN4Yuw4Yio4Yiz4Ym44YuN4YidKSIKICAgICkKCmFzeW5jIGRlZiByZXNldF9mYWN0b3J5X2RlZmF1bHQo"
    "dXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4YiB4YiJ4YqV4YidIOGK"
    "kOGMiOGIrSAo4YuZ4Yiu4Ym94Y2jIOGJsOGMq+GLi+GJvuGJveGNoyDhiq3hiKzhi7LhibXhjaMg4Yuo4Yqt4Y2N4YurIOGKoOGK"
    "q+GLjeGKleGJteGNoyDhiKrhjYjhiKjhipXhiLbhib3hjaMg4YqV4YmBIOGLjeGLreGLreGJtuGJvSDhiIHhiIkpIOGImeGIiSDh"
    "iaDhiJnhiIkg4Yqg4Yyl4Y2N4Ym2IOGLiOGLsCDhjYvhiaXhiKrhiqsg4YqQ4Ymj4YiqCiAgICDhiIHhipThibMg4Yuo4Yia4YiY"
    "4YiN4Yi1IOGJteGLleGLm+GLnSAtIOGIjeGKrSDhiabhibEg4YyI4YqTIOGKpeGKleGLsCDhiqDhi7LhiLUg4Yuo4Ymw4YyA4YiY"
    "4YioIOGLq+GKreGIjSDhi6vhi7DhiK3hjIjhi4vhiI3hjaIg4Yqt4Yis4Yuy4Ym1IOGLiOGLsCBIT1NUX1NUQVJUSU5HX0NSRURJ"
    "VCDhi63hiJjhiIjhiLPhiI3hjaMKICAgIOGLqOGJtOGIjOGJpeGIrS/hiLLhiaLhiqIg4Yml4YitIOGKoOGKq+GLjeGKleGJtSAo"
    "4YmA4Yuw4YidIOGJpeGIjiDhiaAvZWRpdHBheW1lbnQg4Ymw4Yi14Ymw4Yqr4Yqt4YiOIOGKqOGKkOGJoOGIqCkg4YuI4YuwIOGK"
    "puGIquGMheGKk+GIjSAo4Yi14Yqt4Yiq4Y2V4YmxIOGLjeGIteGMpSDhi6jhibDhiYDhiJjhjKApIOGLreGImOGIiOGIs+GIjeGN"
    "ogogICAg4Yuw4YiF4YqV4YqQ4Ym1IOGIteGKleGIjeGNpiDhiJvhiaXhiKvhiKrhi6sg4Yml4Ym7IOGLq+GIs+GLq+GIjSDhiqXh"
    "ipMg4Yib4Yio4YyL4YyI4YyrIOGJoOGJgeGIjeGNjSAoYnV0dG9uKSDhi63hjKDhi63hiYPhiI0gLSDhiJ3hipXhiJ0g4Yy94YiB"
    "4Y2NIOGImOGJsOGLqOGJpSDhiqDhi6vhiLXhjYjhiI3hjI3hiJ3hjaIKICAgIPCflJUg4Yib4Yi14Ymz4YuI4Yi74Y2mIOGMiOGL"
    "ouGLjuGJvSDhiJ3hipXhiJ0g4Yib4Yiz4YuI4YmC4YurIOGKoOGLreGLsOGIreGIs+GJuOGLjeGInSAo4Yql4YqV4YuwIC9kZWxl"
    "dGVhbGxyb3VuZHMg4Ymw4YiY4Yiz4Yiz4YutKeGNogogICAg4Yqg4Yyg4YmD4YmA4Yid4Y2mIC9yZXNldGZhY3RvcnkiIiIKICAg"
    "IGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAgICB0b3RhbF9zb2xk"
    "ID0gc3VtKDEgZm9yIHIgaW4gcm91bmRzLnZhbHVlcygpIGZvciB0IGluIHJbInRpY2tldHMiXS52YWx1ZXMoKSBpZiB0WyJzdGF0"
    "dXMiXSA9PSAiU09MRCIpCiAgICBrYiA9IFsKICAgICAgICBbSW5saW5lS2V5Ym9hcmRCdXR0b24oIvCfmqgg4Yqg4YuO4Y2jIOGI"
    "geGIieGKleGInSDhiqDhjKXhjYsgKEZhY3RvcnkgUmVzZXQpIiwgY2FsbGJhY2tfZGF0YT0icmVzZXRmYWN0b3J5X2NvbmZpcm0i"
    "KV0sCiAgICAgICAgW0lubGluZUtleWJvYXJkQnV0dG9uKCLinYwg4Ymw4YuI4YuNIChDYW5jZWwpIiwgY2FsbGJhY2tfZGF0YT0i"
    "cmVzZXRmYWN0b3J5X2NhbmNlbCIpXSwKICAgIF0KICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAg"
    "IvCfmqggPGI+4Yib4Yi14Yyg4YqV4YmA4YmC4Yur4Y2mIOGImeGIiSDhjYvhiaXhiKrhiqsg4Yuz4YyN4YidIOGIm+GIteGMgOGI"
    "mOGIquGLqyAoRmFjdG9yeSBSZXNldCk8L2I+XG5cbiIKICAgICAgICBmIuGLreGIhSDhi6jhiJrhi6vhjKDhjYvhi43hjaZcbiIK"
    "ICAgICAgICBmIiAg4oCiIOGIgeGIieGKleGInSDhi5nhiK7hib0gKHtsZW4ocm91bmRzKX0pIOGKpeGKkyDhibLhiqzhibbhibvh"
    "ibjhi43hipUgKOGJoOGMoOGJheGIi+GIiyB7dG90YWxfc29sZH0g4Yuo4Ymw4Yi44YyhIOGJsuGKrOGJtuGJveGKlSDhjKjhiJ3h"
    "iK4pXG4iCiAgICAgICAgZiIgIOKAoiDhiIHhiInhipXhiJ0g4Yuo4Ymw4YiY4YuY4YyI4YmhIOGJsOGMq+GLi+GJvuGJvSAoe2xl"
    "bihwbGF5ZXJzKX0pXG4iCiAgICAgICAgZiIgIOKAoiDhi6jhiq3hiKzhi7LhibUg4YiS4Yiz4YmlIC0g4YuI4YuwIOGImOGKkOGI"
    "uyAoe0hPU1RfU1RBUlRJTkdfQ1JFRElUOi4yZn0g4Yml4YitKSDhi63hiJjhiIjhiLPhiI1cbiIKICAgICAgICBmIiAg4oCiIOGL"
    "qOGKreGNjeGLqyDhiqDhiqvhi43hipXhibUgKOGJtOGIjOGJpeGIrS/hiLLhiaLhiqIg4Yml4YitKSAtIOGLiOGLsCDhiqbhiKrh"
    "jIXhipPhiI0g4Yuo4YiG4Yi14Ym1IOGImOGMiOGIiOGMqyDhi63hiJjhiIjhiLPhiI1cbiIKICAgICAgICBmIiAg4oCiIOGIgeGI"
    "ieGKleGInSDhi6jhibDhjKDhiYDhiJkv4Yur4YiN4YyI4Yyg4YiZIFNNUyDhiKrhjYjhiKjhipXhiLbhib0g4Yql4YqTIOGKleGJ"
    "gSDhi43hi63hi63hibbhib0gKHdpemFyZHMpXG5cbiIKICAgICAgICAi8J+UlSDhjIjhi6Lhi47hib0g4Yid4YqV4YidIOGIm+GI"
    "s+GLiOGJguGLqyDhiqDhi63hi7DhiK3hiLPhibjhi43hiJ0gKHJlZnVuZCDhiaDhiqXhjIUg4Yib4Yu14Yio4YyNIOGLq+GIteGN"
    "iOGIjeGMi+GIjSnhjaJcbiIKICAgICAgICAi4p2MIOGLreGIhSDhibDhjI3hiaPhiK0g4YuI4YuwIOGKi+GIiyDhiqDhi63hiJjh"
    "iIjhiLXhiJ0hXG5cbiIKICAgICAgICAi4Yqo4Ymz4Ym9IOGKq+GIieGJtSDhiYHhiI3hjY7hib0g4Yut4Yid4Yio4Yyh4Y2mIiwK"
    "ICAgICAgICBwYXJzZV9tb2RlPSJIVE1MIiwKICAgICAgICByZXBseV9tYXJrdXA9SW5saW5lS2V5Ym9hcmRNYXJrdXAoa2IpLAog"
    "ICAgKQoKYXN5bmMgZGVmIGhhbmRsZV9yZXNldF9mYWN0b3J5X2NvbmZpcm0odXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRl"
    "eHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIiL3Jlc2V0ZmFjdG9yeSDhiJvhiLXhjKDhipXhiYDhiYLhi6sg4Yi14YitIOGL"
    "q+GIieGJtSDCq+GKoOGLjiDhiqDhjKXhjYvCuy/Cq+GJsOGLiOGLjcK7IOGJgeGIjeGNjuGJvSDhiLLhjKvhipEg4Yuo4Yia4Yur"
    "4YudIGNhbGxiYWNrIiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgdWlkID0gcXVlcnkuZnJvbV91c2Vy"
    "LmlkCiAgICBpZiB1aWQgIT0gQURNSU5fSUQ6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgICAgICByZXR1cm4KCiAg"
    "ICBhY3Rpb24gPSBxdWVyeS5kYXRhLnNwbGl0KCJfIiwgMSlbLTFdICAjICJjb25maXJtIiB8ICJjYW5jZWwiCiAgICBhd2FpdCBx"
    "dWVyeS5hbnN3ZXIoKQoKICAgIGlmIGFjdGlvbiA9PSAiY2FuY2VsIjoKICAgICAgICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2Vf"
    "dGV4dCgi4p2MIOGNi+GJpeGIquGKqyDhi7PhjI3hiJ0g4Yib4Yi14YyA4YiY4YitIOGJsOGIsOGIreGLn+GIjeGNoyDhiJ3hipXh"
    "iJ0g4Yqg4YiN4Ymw4YmA4Yuo4Yio4Yid4Y2iIikKICAgICAgICByZXR1cm4KCiAgICBnbG9iYWwgbmV4dF9yb3VuZF9pZCwgaG9z"
    "dF9wYXVzZWQsIGhvc3RfcGF1c2VkX3JlYXNvbgoKICAgIHRvdGFsX3NvbGQgPSBzdW0oMSBmb3IgciBpbiByb3VuZHMudmFsdWVz"
    "KCkgZm9yIHQgaW4gclsidGlja2V0cyJdLnZhbHVlcygpIGlmIHRbInN0YXR1cyJdID09ICJTT0xEIikKICAgIHRvdGFsX3BsYXll"
    "cnMgPSBsZW4ocGxheWVycykKCiAgICAjIC0tLSDhi5nhiK7hib0v4Ymy4Yqs4Ym24Ym9IC0tLQogICAgcm91bmRzLmNsZWFyKCkK"
    "ICAgIG5leHRfcm91bmRfaWQgPSAxCgogICAgIyAtLS0g4Ymw4Yyr4YuL4Ym+4Ym9IOGKpeGKkyDhiYvhipXhiYsv4Yid4Yud4YyI"
    "4YmjIOGIgeGKlOGJsyAtLS0KICAgIHBsYXllcnMuY2xlYXIoKQogICAgcmVnaXN0cmF0aW9uX3N0YXRlLmNsZWFyKCkKICAgIHJl"
    "Z2lzdHJhdGlvbl90ZW1wLmNsZWFyKCkKICAgIHBsYXllcl9sYW5nLmNsZWFyKCkKCiAgICAjIC0tLSDhiq3hiKzhi7LhibUgLS0t"
    "CiAgICBob3N0X2NyZWRpdFsiYmFsYW5jZSJdID0gSE9TVF9TVEFSVElOR19DUkVESVQKICAgIGhvc3RfY3JlZGl0WyJ0b3RhbF9k"
    "ZWR1Y3RlZCJdID0gMC4wCiAgICBob3N0X2NyZWRpdFsibG93X2NyZWRpdF9ub3RpZmllZCJdID0gRmFsc2UKICAgIGhvc3RfcGF1"
    "c2VkID0gRmFsc2UKICAgIGhvc3RfcGF1c2VkX3JlYXNvbiA9IE5vbmUKCiAgICAjIC0tLSDhi6jhiq3hjY3hi6sg4Yqg4Yqr4YuN"
    "4YqV4Ym1ICjhibThiIzhiaXhiK0v4Yiy4Ymi4YqiIOGJpeGIrSkgLSDhi4jhi7Ag4Yqm4Yiq4YyF4YqT4YiNIOGLqOGIhuGIteGJ"
    "tSDhiJjhjIjhiIjhjKsgLS0tCiAgICBmb3Iga2V5LCBkZWZhdWx0cyBpbiBfRkFDVE9SWV9ERUZBVUxUX1BBWU1FTlRfTUVUSE9E"
    "Uy5pdGVtcygpOgogICAgICAgIGlmIGtleSBpbiBQQVlNRU5UX01FVEhPRFM6CiAgICAgICAgICAgIFBBWU1FTlRfTUVUSE9EU1tr"
    "ZXldWyJhY2NvdW50Il0gPSBkZWZhdWx0c1siYWNjb3VudCJdCiAgICAgICAgICAgIFBBWU1FTlRfTUVUSE9EU1trZXldWyJob2xk"
    "ZXIiXSA9IGRlZmF1bHRzWyJob2xkZXIiXQoKICAgICMgLS0tIOGKleGJgSDhiJ3hiK3hjKvhi47hib0v4YuN4Yut4Yut4Ym24Ym9"
    "ICh3aXphcmRzKSAtLS0KICAgIHVzZXJfc2VsZWN0aW9ucy5jbGVhcigpCiAgICB1c2VyX2NhcnRzLmNsZWFyKCkKICAgIHRpY2tl"
    "dF93YXRjaGVycy5jbGVhcigpCiAgICByZWNlaXB0X2NvbnRleHRzLmNsZWFyKCkKICAgIGNyZWRpdF90b3B1cF9zdGF0ZS5jbGVh"
    "cigpCiAgICBwZW5kaW5nX2NyZWRpdF90b3B1cHMuY2xlYXIoKQogICAgbmV3cm91bmRfc3RhdGUuY2xlYXIoKQogICAgbmV3cm91"
    "bmRfdGVtcC5jbGVhcigpCiAgICBtYW51YWxzZWxsX3N0YXRlLmNsZWFyKCkKICAgIG1hbnVhbHNlbGxfY2FydC5jbGVhcigpCiAg"
    "ICBicm9hZGNhc3Rfc3RhdGUuY2xlYXIoKQogICAgcGVuZGluZ19kZWxldGVfY29uZmlybS5jbGVhcigpCiAgICBlZGl0X3BheW1l"
    "bnRfc3RhdGUuY2xlYXIoKQoKICAgICMgLS0tIOGIm+GMo+GJgOGIuyDhibPhiKrhiq0gKHJlZmVyZW5jZSBoaXN0b3J5KSAtLS0K"
    "ICAgIHVzZWRfc21zX3JlZnMuY2xlYXIoKQogICAgdW5tYXRjaGVkX3Ntc19sb2cuY2xlYXIoKQogICAgcmVqZWN0ZWRfcmVmcy5j"
    "bGVhcigpCgogICAgc2F2ZV9zdGF0ZSgpCgogICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoCiAgICAgICAgIuKchSA8"
    "Yj7hjYvhiaXhiKrhiqsg4Yuz4YyN4YidIOGIm+GIteGMgOGImOGIrSDhibDhjKDhipPhiYXhiYvhiI3hjaI8L2I+XG5cbiIKICAg"
    "ICAgICBmIvCfkrMg4Yqt4Yis4Yuy4Ym1IOGLiOGLsCB7SE9TVF9TVEFSVElOR19DUkVESVQ6LjJmfSDhiaXhiK0g4Ymw4YiY4YiN"
    "4Yi34YiN4Y2iXG4iCiAgICAgICAgIvCfkrMg4Yuo4Yqt4Y2N4YurIOGKoOGKq+GLjeGKleGJtSDhi4jhi7Ag4Yqm4Yiq4YyF4YqT"
    "4YiNIOGJsOGImOGIjeGIt+GIjeGNolxuIgogICAgICAgIGYi8J+OsiDhi5nhiK7hib0g4Yql4YqTIHt0b3RhbF9zb2xkfSDhi6jh"
    "ibDhiLjhjKEg4Ymy4Yqs4Ym24Ym9IOGMoOGNjeGJsOGLi+GIjeGNolxuIgogICAgICAgIGYi8J+RpSB7dG90YWxfcGxheWVyc30g"
    "4Ymw4Yyr4YuL4Ym+4Ym9IOGKpeGKkyDhiIHhiInhiJ0g4Ymz4Yiq4YqtIOGMoOGNjeGJsOGLi+GIjSAtIOGIjeGKrSDhiqDhi7Lh"
    "iLUg4YiG4Yi14Ym1IOGLq+GKreGIjSDhipDhi43hjaJcbiIKICAgICAgICAi8J+UlSDhjIjhi6Lhi47hib0g4Yid4YqV4YidIOGI"
    "m+GIs+GLiOGJguGLqyDhiqDhiI3hi7DhiKjhiLPhibjhi43hiJ3hjaIiLAogICAgICAgIHBhcnNlX21vZGU9IkhUTUwiLAogICAg"
    "KQoKYXN5bmMgZGVmIF9icm9hZGNhc3RfdGV4dChjb250ZXh0LCB0ZXh0KToKICAgIHNlbnQsIGZhaWxlZCA9IDAsIDAKICAgIGZv"
    "ciB1aWQgaW4gcGxheWVyczoKICAgICAgICB0cnk6CiAgICAgICAgICAgIGF3YWl0IGNvbnRleHQuYm90LnNlbmRfbWVzc2FnZSgK"
    "ICAgICAgICAgICAgICAgIGNoYXRfaWQ9dWlkLAogICAgICAgICAgICAgICAgdGV4dD1mIvCfk6IgPGI+4Yib4Yi14Ymz4YuI4YmC"
    "4YurPC9iPlxuXG57dGV4dH0iLAogICAgICAgICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIKICAgICAgICAgICAgKQogICAgICAg"
    "ICAgICBzZW50ICs9IDEKICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICBmYWlsZWQgKz0gMQogICAgcmV0dXJu"
    "IHNlbnQsIGZhaWxlZAoKYXN5bmMgZGVmIF9icm9hZGNhc3RfbWVkaWEoY29udGV4dCwgbWVzc2FnZSk6CiAgICBjYXB0aW9uID0g"
    "bWVzc2FnZS5jYXB0aW9uIG9yICIiCiAgICBjYXB0aW9uID0gZiLwn5OiIDxiPuGIm+GIteGJs+GLiOGJguGLqzwvYj5cblxue2Nh"
    "cHRpb259Ii5zdHJpcCgpCiAgICBzZW50LCBmYWlsZWQgPSAwLCAwCgogICAgZm9yIHVpZCBpbiBwbGF5ZXJzOgogICAgICAgIHRy"
    "eToKICAgICAgICAgICAgaWYgbWVzc2FnZS5waG90bzoKICAgICAgICAgICAgICAgIGF3YWl0IGNvbnRleHQuYm90LnNlbmRfcGhv"
    "dG8oY2hhdF9pZD11aWQsIHBob3RvPW1lc3NhZ2UucGhvdG9bLTFdLmZpbGVfaWQsIGNhcHRpb249Y2FwdGlvbiwgcGFyc2VfbW9k"
    "ZT0iSFRNTCIpCiAgICAgICAgICAgIGVsaWYgbWVzc2FnZS52aWRlbzoKICAgICAgICAgICAgICAgIGF3YWl0IGNvbnRleHQuYm90"
    "LnNlbmRfdmlkZW8oY2hhdF9pZD11aWQsIHZpZGVvPW1lc3NhZ2UudmlkZW8uZmlsZV9pZCwgY2FwdGlvbj1jYXB0aW9uLCBwYXJz"
    "ZV9tb2RlPSJIVE1MIikKICAgICAgICAgICAgZWxpZiBtZXNzYWdlLnZvaWNlOgogICAgICAgICAgICAgICAgYXdhaXQgY29udGV4"
    "dC5ib3Quc2VuZF92b2ljZShjaGF0X2lkPXVpZCwgdm9pY2U9bWVzc2FnZS52b2ljZS5maWxlX2lkLCBjYXB0aW9uPWNhcHRpb24s"
    "IHBhcnNlX21vZGU9IkhUTUwiKQogICAgICAgICAgICBlbGlmIG1lc3NhZ2UuYXVkaW86CiAgICAgICAgICAgICAgICBhd2FpdCBj"
    "b250ZXh0LmJvdC5zZW5kX2F1ZGlvKGNoYXRfaWQ9dWlkLCBhdWRpbz1tZXNzYWdlLmF1ZGlvLmZpbGVfaWQsIGNhcHRpb249Y2Fw"
    "dGlvbiwgcGFyc2VfbW9kZT0iSFRNTCIpCiAgICAgICAgICAgIGVsaWYgbWVzc2FnZS5kb2N1bWVudDoKICAgICAgICAgICAgICAg"
    "IGF3YWl0IGNvbnRleHQuYm90LnNlbmRfZG9jdW1lbnQoY2hhdF9pZD11aWQsIGRvY3VtZW50PW1lc3NhZ2UuZG9jdW1lbnQuZmls"
    "ZV9pZCwgY2FwdGlvbj1jYXB0aW9uLCBwYXJzZV9tb2RlPSJIVE1MIikKICAgICAgICAgICAgZWxzZToKICAgICAgICAgICAgICAg"
    "IGNvbnRpbnVlCiAgICAgICAgICAgIHNlbnQgKz0gMQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgIGZhaWxl"
    "ZCArPSAxCiAgICByZXR1cm4gc2VudCwgZmFpbGVkCgphc3luYyBkZWYgX2Jyb2FkY2FzdF9jb21wb3NlZChjb250ZXh0LCB0ZXh0"
    "PU5vbmUsIHBob3RvX2ZpbGVfaWQ9Tm9uZSwgdmlkZW9fZmlsZV9pZD1Ob25lLCB2b2ljZV9maWxlX2lkPU5vbmUpOgogICAgIiIi"
    "4Yy94YiB4Y2NL+GNjuGJti/hiarhi7Xhi64v4Yu14Yid4Yy94YqVICjhiJvhipXhipvhi43hipXhiJ0g4Yyl4Yid4Yio4Ym1KSDh"
    "iqDhipXhi7Ug4YiL4YutIOGKoOGLi+GIheGLtiDhiIjhiIHhiInhiJ0g4Ymw4Yyr4YuL4Ym+4Ym9IOGLqOGImuGLq+GIsOGIq+GM"
    "rSBoZWxwZXLhjaIKICAgIOGImOGMjeGIiOGMq+GLjSAoY2FwdGlvbikg4Ymg4YiY4YyA4YiY4Yiq4Yur4YuNIOGJoOGJsOGIi+GK"
    "qOGLjSBtZWRpYSDhiIvhi60g4Yml4Ym7IOGLreGJs+GLq+GIjSAobWVkaWEg4Yid4YqV4YidIOGKqOGIjOGIiCDhiqXhipXhi7Ag"
    "4Ymw4YirIOGMveGIgeGNjSDhi63hiIvhiqvhiI0p4Y2iIiIiCiAgICBjYXB0aW9uID0gZiLwn5OiIDxiPuGIm+GIteGJs+GLiOGJ"
    "guGLqzwvYj5cblxue3RleHR9Ii5zdHJpcCgpIGlmIHRleHQgZWxzZSAi8J+ToiA8Yj7hiJvhiLXhibPhi4jhiYLhi6s8L2I+Igog"
    "ICAgc2VudCwgZmFpbGVkID0gMCwgMAoKICAgIGZvciB1aWQgaW4gcGxheWVyczoKICAgICAgICB0cnk6CiAgICAgICAgICAgIGNh"
    "cHRpb25fdXNlZCA9IEZhbHNlCiAgICAgICAgICAgIGlmIHBob3RvX2ZpbGVfaWQ6CiAgICAgICAgICAgICAgICBhd2FpdCBjb250"
    "ZXh0LmJvdC5zZW5kX3Bob3RvKGNoYXRfaWQ9dWlkLCBwaG90bz1waG90b19maWxlX2lkLCBjYXB0aW9uPWNhcHRpb24sIHBhcnNl"
    "X21vZGU9IkhUTUwiKQogICAgICAgICAgICAgICAgY2FwdGlvbl91c2VkID0gVHJ1ZQogICAgICAgICAgICBpZiB2aWRlb19maWxl"
    "X2lkOgogICAgICAgICAgICAgICAga3dhcmdzID0geyJjYXB0aW9uIjogY2FwdGlvbiwgInBhcnNlX21vZGUiOiAiSFRNTCJ9IGlm"
    "IG5vdCBjYXB0aW9uX3VzZWQgZWxzZSB7fQogICAgICAgICAgICAgICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF92aWRlbyhjaGF0"
    "X2lkPXVpZCwgdmlkZW89dmlkZW9fZmlsZV9pZCwgKiprd2FyZ3MpCiAgICAgICAgICAgICAgICBjYXB0aW9uX3VzZWQgPSBUcnVl"
    "CiAgICAgICAgICAgIGlmIHZvaWNlX2ZpbGVfaWQ6CiAgICAgICAgICAgICAgICBrd2FyZ3MgPSB7ImNhcHRpb24iOiBjYXB0aW9u"
    "LCAicGFyc2VfbW9kZSI6ICJIVE1MIn0gaWYgbm90IGNhcHRpb25fdXNlZCBlbHNlIHt9CiAgICAgICAgICAgICAgICBhd2FpdCBj"
    "b250ZXh0LmJvdC5zZW5kX3ZvaWNlKGNoYXRfaWQ9dWlkLCB2b2ljZT12b2ljZV9maWxlX2lkLCAqKmt3YXJncykKICAgICAgICAg"
    "ICAgICAgIGNhcHRpb25fdXNlZCA9IFRydWUKICAgICAgICAgICAgaWYgbm90IGNhcHRpb25fdXNlZDoKICAgICAgICAgICAgICAg"
    "IGF3YWl0IGNvbnRleHQuYm90LnNlbmRfbWVzc2FnZShjaGF0X2lkPXVpZCwgdGV4dD1jYXB0aW9uLCBwYXJzZV9tb2RlPSJIVE1M"
    "IikKICAgICAgICAgICAgc2VudCArPSAxCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgZmFpbGVkICs9IDEK"
    "ICAgIHJldHVybiBzZW50LCBmYWlsZWQKCmFzeW5jIGRlZiBfc2hvd19icm9hZGNhc3RfcHJldmlldyh1cGRhdGU6IFVwZGF0ZSwg"
    "Y29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSwgdXNlcl9pZDogaW50KToKICAgICIiIuGLqOGIm+GIteGJs+GLiOGJ"
    "guGLqyDhi4rhi5vhiK3hi7Ug4Yuw4Yio4YyD4YuO4Ym9ICjhjL3hiIHhjY3ihpLhjY7hibbihpLhiarhi7Xhi64v4Yu14Yid4Yy9"
    "KSDhiqvhiIjhiYEg4Ymg4YqL4YiLIC0g4YyI4YqTIOGLiOGLsCDhibDhjKvhi4vhib7hib0g4Yiz4Yut4YiL4YqtIOGKoOGLteGI"
    "muGKkSDhi6jhiJjhjKjhiKjhiLsKICAgIOGIm+GIqOGMi+GMiOGMqyAocHJldmlldyArIOKchS/wn5qrIOGJgeGIjeGNjuGJvSkg"
    "4Yql4YqV4Yuy4Yur4YutIOGLqOGImuGLq+GLsOGIreGMjSBoZWxwZXLhjaIgYnJvYWRjYXN0X3N0YXRlIOGLjeGIteGMpSDhi6vh"
    "iIjhi40g4YiY4Yio4YyDCiAgICDhiqXhiLXhiqgg4Yib4Yio4YyL4YyI4YyrIOGLteGIqOGItSDhiqDhi63hjKDhjYvhiJ0gKHN0"
    "ZXAg4Yml4Ym7IOGLiOGLsCAiY29uZmlybSIg4Yut4YmA4Yuo4Yir4YiNKeGNoiIiIgogICAgc3RhdGUgPSBicm9hZGNhc3Rfc3Rh"
    "dGUuZ2V0KHVzZXJfaWQpCiAgICBpZiBub3QgaXNpbnN0YW5jZShzdGF0ZSwgZGljdCk6CiAgICAgICAgcmV0dXJuCgogICAgdGV4"
    "dCA9IHN0YXRlLmdldCgidGV4dCIpCiAgICBwaG90b19maWxlX2lkID0gc3RhdGUuZ2V0KCJwaG90byIpCiAgICB2aWRlb19maWxl"
    "X2lkID0gc3RhdGUuZ2V0KCJ2aWRlbyIpCiAgICB2b2ljZV9maWxlX2lkID0gc3RhdGUuZ2V0KCJ2b2ljZSIpCgogICAgaWYgbm90"
    "ICh0ZXh0IG9yIHBob3RvX2ZpbGVfaWQgb3IgdmlkZW9fZmlsZV9pZCBvciB2b2ljZV9maWxlX2lkKToKICAgICAgICBicm9hZGNh"
    "c3Rfc3RhdGUucG9wKHVzZXJfaWQsIE5vbmUpCiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4oS577iP"
    "IOGIneGKleGInSDhiJjhiI3hi5Xhiq3hibUg4Yi14YiL4YiN4YyI4YmjIOGIm+GIteGJs+GLiOGJguGLq+GLjSDhiqDhiI3hibDh"
    "iIvhiqjhiJ3hjaIiKQogICAgICAgIHJldHVybgoKICAgIHN0YXRlWyJzdGVwIl0gPSAiY29uZmlybSIKCiAgICBjYXB0aW9uID0g"
    "ZiLwn5OiIDxiPuGIm+GIteGJs+GLiOGJguGLqyAo4YmF4Yu14YiYLeGKpeGLreGJsyk8L2I+XG5cbnt0ZXh0fSIuc3RyaXAoKSBp"
    "ZiB0ZXh0IGVsc2UgIvCfk6IgPGI+4Yib4Yi14Ymz4YuI4YmC4YurICjhiYXhi7XhiJgt4Yql4Yut4YmzKTwvYj4iCiAgICBjYXB0"
    "aW9uICs9ICJcblxu8J+RhyDhibXhiq3hiq3hiI0g4Yqo4YiG4YqQIOGKoOGIqOGMi+GMjeGMoOGLjSDhi63hiIvhiqnhjaMg4Yqr"
    "4YiN4YiG4YqQIOGLreGIsOGIreGLmeGNoiIKICAgIGtiID0gSW5saW5lS2V5Ym9hcmRNYXJrdXAoW1sKICAgICAgICBJbmxpbmVL"
    "ZXlib2FyZEJ1dHRvbigi4pyFIOGKoOGIqOGMi+GMjeGMpSDhiqXhipMg4YiL4YqtIiwgY2FsbGJhY2tfZGF0YT0iYnJvYWRjYXN0"
    "Y29uZmlybSIpLAogICAgICAgIElubGluZUtleWJvYXJkQnV0dG9uKCLwn5qrIOGIsOGIreGLnSIsIGNhbGxiYWNrX2RhdGE9ImJy"
    "b2FkY2FzdGNhbmNlbCIpLAogICAgXV0pCgogICAgdHJ5OgogICAgICAgIGlmIHBob3RvX2ZpbGVfaWQ6CiAgICAgICAgICAgIGF3"
    "YWl0IGNvbnRleHQuYm90LnNlbmRfcGhvdG8oY2hhdF9pZD11c2VyX2lkLCBwaG90bz1waG90b19maWxlX2lkLCBjYXB0aW9uPWNh"
    "cHRpb24sIHBhcnNlX21vZGU9IkhUTUwiLCByZXBseV9tYXJrdXA9a2IpCiAgICAgICAgZWxpZiB2aWRlb19maWxlX2lkOgogICAg"
    "ICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX3ZpZGVvKGNoYXRfaWQ9dXNlcl9pZCwgdmlkZW89dmlkZW9fZmlsZV9pZCwg"
    "Y2FwdGlvbj1jYXB0aW9uLCBwYXJzZV9tb2RlPSJIVE1MIiwgcmVwbHlfbWFya3VwPWtiKQogICAgICAgIGVsaWYgdm9pY2VfZmls"
    "ZV9pZDoKICAgICAgICAgICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF92b2ljZShjaGF0X2lkPXVzZXJfaWQsIHZvaWNlPXZvaWNl"
    "X2ZpbGVfaWQsIGNhcHRpb249Y2FwdGlvbiwgcGFyc2VfbW9kZT0iSFRNTCIsIHJlcGx5X21hcmt1cD1rYikKICAgICAgICBlbHNl"
    "OgogICAgICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoY2hhdF9pZD11c2VyX2lkLCB0ZXh0PWNhcHRpb24s"
    "IHBhcnNlX21vZGU9IkhUTUwiLCByZXBseV9tYXJrdXA9a2IpCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIGF3YWl0IHVw"
    "ZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKaoO+4jyDhiYXhi7XhiJgt4Yql4Yut4YmzIOGImOGIi+GKrSDhiqDhiI3hibDhibvh"
    "iIjhiJ3hjaIgL2NhbmNlbCDhi4jhi63hiJ0gwqvhiLDhiK3hi53CuyDhiaXhiIjhi40g4Yql4YqV4Yuw4YyI4YqTIOGLreGInuGK"
    "reGIqeGNoiIpCgphc3luYyBkZWYgaGFuZGxlX2Jyb2FkY2FzdF9jb25maXJtKHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250"
    "ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIuGJoOGJheGLteGImC3hiqXhi63hibPhi40g4YiL4YutIMKr4pyFIOGKoOGI"
    "qOGMi+GMjeGMpSDhiqXhipMg4YiL4Yqtwrsg4Yiy4Yyr4YqRIOGJpeGJuyDhiJvhiLXhibPhi4jhiYLhi6vhi43hipUg4Ymg4Ym1"
    "4Yqt4Yqt4YiNIOGIiOGIgeGIieGInSDhibDhjKvhi4vhib7hib0g4Yuo4Yia4YiN4YqtIGNhbGxiYWNrIiIiCiAgICBxdWVyeSA9"
    "IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgaWYgcXVlcnkuZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgogICAgICAgIGF3YWl0"
    "IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgcmV0dXJuCiAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgdXNlcl9pZCA9IHF1ZXJ5"
    "LmZyb21fdXNlci5pZAoKICAgIHN0YXRlID0gYnJvYWRjYXN0X3N0YXRlLnBvcCh1c2VyX2lkLCBOb25lKQogICAgdHJ5OgogICAg"
    "ICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV9yZXBseV9tYXJrdXAocmVwbHlfbWFya3VwPU5vbmUpCiAgICBleGNlcHQgRXhj"
    "ZXB0aW9uOgogICAgICAgIHBhc3MKCiAgICBpZiBub3QgaXNpbnN0YW5jZShzdGF0ZSwgZGljdCk6CiAgICAgICAgdHJ5OgogICAg"
    "ICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoY2hhdF9pZD11c2VyX2lkLCB0ZXh0PSLimqDvuI8g4Yut4YiF"
    "IOGJheGLteGImC3hiqXhi63hibMg4YyK4Yuc4YuNIOGKoOGIjeGNjuGJoOGJs+GIjSDhi4jhi63hiJ0g4Yqo4Yua4YiFIOGJoOGN"
    "iuGJtSDhibDhiI3hirPhiI0v4Ymw4Yiw4Yit4Yuf4YiN4Y2iIikKICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAg"
    "ICBwYXNzCiAgICAgICAgcmV0dXJuCgogICAgc2VudCwgZmFpbGVkID0gYXdhaXQgX2Jyb2FkY2FzdF9jb21wb3NlZCgKICAgICAg"
    "ICBjb250ZXh0LAogICAgICAgIHRleHQ9c3RhdGUuZ2V0KCJ0ZXh0IiksCiAgICAgICAgcGhvdG9fZmlsZV9pZD1zdGF0ZS5nZXQo"
    "InBob3RvIiksCiAgICAgICAgdmlkZW9fZmlsZV9pZD1zdGF0ZS5nZXQoInZpZGVvIiksCiAgICAgICAgdm9pY2VfZmlsZV9pZD1z"
    "dGF0ZS5nZXQoInZvaWNlIiksCiAgICApCiAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoY2hhdF9pZD11c2VyX2lk"
    "LCB0ZXh0PWYi4pyFIOGIm+GIteGJs+GLiOGJguGLq+GLjSDhiIh7c2VudH0g4Ymw4Yyr4YuL4Ym+4Ym9IOGJsOGIjeGKs+GIjeGN"
    "oiAoe2ZhaWxlZH0g4Yqg4YiN4Ymw4Yiz4Yqr4YidKSIpCgphc3luYyBkZWYgaGFuZGxlX2Jyb2FkY2FzdF9jYW5jZWwodXBkYXRl"
    "OiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4Ymg4YmF4Yu14YiYLeGKpeGLreGJ"
    "s+GLjSDhiIvhi60gwqvwn5qrIOGIsOGIreGLncK7IOGIsuGMq+GKkSDhiJ3hipXhiJ0g4Yiz4Yut4YiL4YqtIOGIm+GIteGJs+GL"
    "iOGJguGLq+GLjeGKlSDhi6jhiJrhiLDhiK3hi50gY2FsbGJhY2siIiIKICAgIHF1ZXJ5ID0gdXBkYXRlLmNhbGxiYWNrX3F1ZXJ5"
    "CiAgICBpZiBxdWVyeS5mcm9tX3VzZXIuaWQgIT0gQURNSU5fSUQ6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgICAg"
    "ICByZXR1cm4KICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICB1c2VyX2lkID0gcXVlcnkuZnJvbV91c2VyLmlkCgogICAgYnJv"
    "YWRjYXN0X3N0YXRlLnBvcCh1c2VyX2lkLCBOb25lKQogICAgdHJ5OgogICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV9y"
    "ZXBseV9tYXJrdXAocmVwbHlfbWFya3VwPU5vbmUpCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKICAgIHRyeToK"
    "ICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoY2hhdF9pZD11c2VyX2lkLCB0ZXh0PSLwn5qrIOGIm+GIteGJ"
    "s+GLiOGJguGLq+GLjSDhibDhiLDhiK3hi5/hiI3hjaMg4Yid4YqV4YidIOGKoOGIjeGJsOGIi+GKqOGIneGNoiIpCiAgICBleGNl"
    "cHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKCmFzeW5jIGRlZiBhbm5vdW5jZSh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29u"
    "dGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhiIjhiIHhiInhiJ0g4Yuo4Ymw4YiY4YuY4YyI4YmhIOGJsOGMq+GLi+GJ"
    "vuGJvSDhiJjhiI3hi5Xhiq3hibUgKOGMveGIgeGNjSDhiJjhjI3hiIjhjKsgKyDhiJ3hiLXhiI0gKyDhiarhi7Xhi64v4Yu14Yid"
    "4Yy9IOGIm+GKleGKm+GLjeGKleGInSDhjKXhiJ3hiKjhibUpIOGLqOGImuGLq+GIsOGIq+GMrSDhibXhi5Xhi5vhi53hjaIKICAg"
    "IOGIm+GIteGJs+GLiOGIu+GNpiDhiIHhiInhipXhiJ0g4Yuw4Yio4YyD4YuO4Ym9ICjhjL3hiIHhjY3ihpLhjY7hibbihpLhiarh"
    "i7Xhi64v4Yu14Yid4Yy9KSDhiqXhiLXhiqrhjKjhiK3hiLEg4Yu14Yio4Yi1IOGKpeGKkyDhi6jhiJjhjKjhiKjhiLvhi43hipUg"
    "4YmF4Yu14YiYLeGKpeGLreGJsyDhiaDinIUg4Yql4Yi14Yqq4Yur4Yio4YyL4YyN4YyhCiAgICDhi7XhiKjhiLUg4Yid4YqV4Yid"
    "IOGKkOGMiOGIrSDhiIjhibDhjKvhi4vhib7hib0g4Yqg4Yut4YiL4Yqt4Yid4Y2iIiIiCiAgICBpZiB1cGRhdGUubWVzc2FnZS5m"
    "cm9tX3VzZXIuaWQgIT0gQURNSU5fSUQ6CiAgICAgICAgcmV0dXJuCgogICAgdWlkID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2Vy"
    "LmlkCiAgICBjbGVhcmVkID0gX2NhbmNlbF9vdGhlcl9hZG1pbl90YXNrcyh1aWQsIGtlZXA9ImFubm91bmNlIikKICAgIGF3YWl0"
    "IF9ub3RpZnlfY2FuY2VsbGVkX3Rhc2tzKGNvbnRleHQuYm90LCB1aWQsIGNsZWFyZWQpCgogICAgaWYgbm90IHBsYXllcnM6CiAg"
    "ICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4oS577iPIOGKpeGIteGKq+GIgeGKlSDhi6jhibDhiJjhi5jh"
    "jIjhiaAg4Ymw4Yyr4YuL4Ym9IOGLqOGIiOGIneGNoiIpCiAgICAgICAgcmV0dXJuCgogICAgIyBOT1RFOiB1c2UgY29udGV4dC5h"
    "cmdzIChhdXRvLXBhcnNlZCBieSBDb21tYW5kSGFuZGxlciBmb3IgIi9hbm5vdW5jZSAuLi4iKQogICAgIyBpbnN0ZWFkIG9mIHNw"
    "bGl0dGluZyB1cGRhdGUubWVzc2FnZS50ZXh0IC0gdGhlIGJvdHRvbS1tZW51IGJ1dHRvbidzIG93bgogICAgIyBsYWJlbCAoIvCf"
    "k6Ig4Yib4Yi14Ymz4YuI4YmC4YurIikgY29udGFpbnMgYSBzcGFjZSwgc28gc3BsaXR0aW5nIHRoZSByYXcgdGV4dCB3b3VsZAog"
    "ICAgIyBtaXNyZWFkIHRoZSBidXR0b24gcHJlc3MgaXRzZWxmIGFzIHF1aWNrLWJyb2FkY2FzdCB0ZXh0IGFuZCBza2lwIHRoZSB3"
    "aXphcmQuCiAgICBhcmdzID0gbGlzdChnZXRhdHRyKGNvbnRleHQsICJhcmdzIiwgTm9uZSkgb3IgW10pCiAgICBpbml0aWFsX3Rl"
    "eHQgPSAiICIuam9pbihhcmdzKS5zdHJpcCgpIG9yIE5vbmUKCiAgICBpZiBpbml0aWFsX3RleHQ6CiAgICAgICAgIyDhjL3hiIHh"
    "jYkg4Yi14YiI4YyI4YmjIOGLiOGLsCDhiYDhjKPhi60g4Yuw4Yio4YyDICjhjY7hibYpIOGJoOGJgOGMpeGJsyDhi63hiIjhjYkg"
    "LSDhjI3hipUg4Yqg4YiB4YqV4YidIOGNjuGJti/hiarhi7Xhi64t4YuI4Yut4YidLeGLteGIneGMvSDhi7DhiKjhjIPhi47hib3h"
    "ipUg4Yqg4YiN4Y2OCiAgICAgICAgIyDhiJvhiKjhjIvhjIjhjKsg4Yqr4YiL4YyI4YqYIOGJoOGIteGJsOGJgOGIrSDhi4jhi7Ag"
    "4Ymw4Yyr4YuL4Ym+4Ym9IOGIneGKleGInSDhiqDhi63hiIvhiq3hiJ3hjaIKICAgICAgICBicm9hZGNhc3Rfc3RhdGVbdXBkYXRl"
    "Lm1lc3NhZ2UuZnJvbV91c2VyLmlkXSA9IHsKICAgICAgICAgICAgInN0ZXAiOiAicGhvdG8iLCAidGV4dCI6IGluaXRpYWxfdGV4"
    "dCwgInBob3RvIjogTm9uZSwgInZpZGVvIjogTm9uZSwgInZvaWNlIjogTm9uZSwKICAgICAgICB9CiAgICAgICAgYXdhaXQgdXBk"
    "YXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgIvCfk6Ig4Yqg4Yuy4Yi1IOGIm+GIteGJs+GLiOGJguGLqyDhiaDh"
    "i53hjI3hjIXhibUg4YiL4YutLi4uICjhjY7hibYg4oaSIOGJquGLteGLri/hi7XhiJ3hjL0g4oaSIOGIm+GIqOGMi+GMiOGMqylc"
    "blxuIgogICAgICAgICAgICAi8J+WvCDhiJ3hiLXhiI0gKOGNjuGJtikg4Yib4Yur4Yur4YudIOGLreGNiOGIjeGMi+GIiT8g4Y2O"
    "4Ym2IOGLreGIi+GKqeGNoyDhi4jhi63hiJ0g4Yqr4YiN4Y2I4YiI4YyJIC9za2lwIOGLiOGLreGInSDCq+GLneGIiOGIjcK7IOGL"
    "reGIi+GKqeGNolxuXG4iCiAgICAgICAgICAgICLhiIjhiJjhiLDhiKjhi50gL2NhbmNlbCDhi63hjKvhipHhjaIiCiAgICAgICAg"
    "KQogICAgICAgIHJldHVybgoKICAgIGJyb2FkY2FzdF9zdGF0ZVt1cGRhdGUubWVzc2FnZS5mcm9tX3VzZXIuaWRdID0gewogICAg"
    "ICAgICJzdGVwIjogInRleHQiLCAidGV4dCI6IE5vbmUsICJwaG90byI6IE5vbmUsICJ2aWRlbyI6IE5vbmUsICJ2b2ljZSI6IE5v"
    "bmUsCiAgICB9CiAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICLwn5OiIOGKoOGLsuGItSDhiJvh"
    "iLXhibPhi4jhiYLhi6sg4Ymg4Yud4YyN4YyF4Ym1IOGIi+GLrS4uLiAo4Yy94YiB4Y2NIOKGkiDhiJ3hiLXhiI0g4oaSIOGJquGL"
    "teGLri/hi7XhiJ3hjL0g4oaSIOGIm+GIqOGMi+GMiOGMqylcblxuIgogICAgICAgICLwn5OdIOGImOGMjeGIiOGMqyAo4Yy94YiB"
    "4Y2NKSDhi63hiIvhiqnhjaMg4YuI4Yut4YidIOGMveGIgeGNjSDhiqvhiI3hjYjhiIjhjIkgL3NraXAg4YuI4Yut4YidIMKr4Yud"
    "4YiI4YiNwrsg4Yut4YiL4Yqp4Y2iXG5cbiIKICAgICAgICAi4YiI4YiY4Yiw4Yio4YudIC9jYW5jZWwg4Yut4Yyr4YqR4Y2iIgog"
    "ICAgKQoKCmFzeW5jIGRlZiBoYW5kbGVfYW5ub3VuY2VfZmxvdyh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVz"
    "LkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhi6gvYW5ub3VuY2Ug4YuK4Yub4Yit4Yu1IOGIi+GLrSDhjL3hiIHhjY0g4Yiy4YiL4Yqt"
    "IOGLqOGImuGLq+GLnSAtIOGLsOGIqOGMg+GNpiB0ZXh0IOKGkiBwaG90byDihpIgbWVkaWEgKHZpZGVvL3ZvaWNlKSDihpIgY29u"
    "ZmlybSIiIgogICAgdXNlcl9pZCA9IHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZAogICAgc3RhdGUgPSBicm9hZGNhc3Rfc3Rh"
    "dGUuZ2V0KHVzZXJfaWQpCiAgICBpZiBub3QgaXNpbnN0YW5jZShzdGF0ZSwgZGljdCk6CiAgICAgICAgcmV0dXJuCgogICAgdGV4"
    "dCA9ICh1cGRhdGUubWVzc2FnZS50ZXh0IG9yICIiKS5zdHJpcCgpCiAgICBzdGVwID0gc3RhdGUuZ2V0KCJzdGVwIikKCiAgICBp"
    "ZiBzdGVwID09ICJjb25maXJtIjoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICAi"
    "4oS577iPIOGKpeGJo+GKreGLjiDhiqjhiIvhi60g4Ymj4YiI4YuNIOGJheGLteGImC3hiqXhi63hibMg4YiL4YutIOKchSDhiqDh"
    "iKjhjIvhjI3hjKUg4YuI4Yut4YidIPCfmqsg4Yiw4Yit4YudIOGJgeGIjeGNjeGKlSDhi63hjKDhiYDhiJkgKOGLiOGLreGInSAv"
    "Y2FuY2VsIOGLiOGLreGInSDCq+GIsOGIreGLncK7IOGLreGIi+GKqSnhjaIiCiAgICAgICAgKQogICAgICAgIHJldHVybgoKICAg"
    "IGlmIHN0ZXAgPT0gInRleHQiOgogICAgICAgIGlmIG5vdCBfaXNfc2tpcF90ZXh0KHRleHQpOgogICAgICAgICAgICBzdGF0ZVsi"
    "dGV4dCJdID0gdGV4dAogICAgICAgIHN0YXRlWyJzdGVwIl0gPSAicGhvdG8iCiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2Uu"
    "cmVwbHlfdGV4dCgKICAgICAgICAgICAgIvCflrwg4Yid4Yi14YiNICjhjY7hibYpIOGIm+GLq+GLq+GLnSDhi63hjYjhiI3hjIvh"
    "iIk/IOGNjuGJtiDhi63hiIvhiqnhjaMg4YuI4Yut4YidIOGKq+GIjeGNiOGIiOGMiSAvc2tpcCDhi4jhi63hiJ0gwqvhi53hiIjh"
    "iI3CuyDhi63hiIvhiqnhjaIiCiAgICAgICAgKQogICAgICAgIHJldHVybgoKICAgIGlmIHN0ZXAgPT0gInBob3RvIjoKICAgICAg"
    "ICBpZiBfaXNfc2tpcF90ZXh0KHRleHQpOgogICAgICAgICAgICBzdGF0ZVsic3RlcCJdID0gIm1lZGlhIgogICAgICAgICAgICBh"
    "d2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICAgICAgIvCfjqXwn46ZIOGJquGLteGLriDhi4jhi63h"
    "iJ0g4Yu14Yid4Yy9ICh2b2ljZSkg4Yib4Yur4Yur4YudIOGLreGNiOGIjeGMi+GIiT8g4Yqo4YiB4YiI4YmxIOGKoOGKleGLseGK"
    "lSDhi63hiIvhiqnhjaMg4YuI4Yut4YidIOGKq+GIjeGNiOGIiOGMiSAvc2tpcCDhi4jhi63hiJ0gwqvhi53hiIjhiI3CuyDhi63h"
    "iIvhiqnhjaIiCiAgICAgICAgICAgICkKICAgICAgICBlbHNlOgogICAgICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBs"
    "eV90ZXh0KCLwn5a8IOGNjuGJtiDhiaXhibsg4Yut4YiL4YqpIOGLiOGLreGInSAvc2tpcCDhi63hjKvhipHhjaIiKQogICAgICAg"
    "IHJldHVybgoKICAgIGlmIHN0ZXAgPT0gIm1lZGlhIjoKICAgICAgICBpZiBfaXNfc2tpcF90ZXh0KHRleHQpOgogICAgICAgICAg"
    "ICBhd2FpdCBfc2hvd19icm9hZGNhc3RfcHJldmlldyh1cGRhdGUsIGNvbnRleHQsIHVzZXJfaWQpCiAgICAgICAgZWxzZToKICAg"
    "ICAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi8J+OpfCfjpkg4Ymq4Yu14YuuIOGLiOGLreGInSDhi7Xh"
    "iJ3hjL0g4Yml4Ym7IOGLreGIi+GKqSDhi4jhi63hiJ0gL3NraXAg4Yut4Yyr4YqR4Y2iIikKICAgICAgICByZXR1cm4KCmFzeW5j"
    "IGRlZiBjYW5jZWxfYnJvYWRjYXN0KHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToK"
    "ICAgICIiIuGLqOGJsOGMgOGImOGIqCDhi6jhiJvhiLXhibPhi4jhiYLhi6sg4YuI4Yut4YidIOGLqC9uZXdyb3VuZCDhiIHhipDh"
    "ibPhipUg4Yuo4Yia4Yiw4Yit4YudIOGJteGLleGLm+GLnSIiIgogICAgaWYgdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkICE9"
    "IEFETUlOX0lEOgogICAgICAgIHJldHVybgoKICAgIHVzZXJfaWQgPSB1cGRhdGUubWVzc2FnZS5mcm9tX3VzZXIuaWQKICAgIGNh"
    "bmNlbGxlZF9zb21ldGhpbmcgPSBGYWxzZQoKICAgIGlmIGJyb2FkY2FzdF9zdGF0ZS5wb3AodXNlcl9pZCwgTm9uZSk6CiAgICAg"
    "ICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi8J+aqyDhi6jhiJvhiLXhibPhi4jhiYLhi6sg4YiB4YqQ4Ymz4YuN"
    "IOGJsOGIsOGIreGLn+GIjeGNoiIpCiAgICAgICAgY2FuY2VsbGVkX3NvbWV0aGluZyA9IFRydWUKCiAgICBpZiBuZXdyb3VuZF9z"
    "dGF0ZS5wb3AodXNlcl9pZCwgTm9uZSkgaXMgbm90IE5vbmU6CiAgICAgICAgbmV3cm91bmRfdGVtcC5wb3AodXNlcl9pZCwgTm9u"
    "ZSkKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLwn5qrIOGLqC9uZXdyb3VuZCDhiILhi7DhibEg4Ymw"
    "4Yiw4Yit4Yuf4YiN4Y2jIOGIneGKleGInSDhiqDhiI3hibDhiYDhi6jhiKjhiJ3hjaIiKQogICAgICAgIGNhbmNlbGxlZF9zb21l"
    "dGhpbmcgPSBUcnVlCgogICAgaWYgcGVuZGluZ19kZWxldGVfY29uZmlybS5wb3AodXNlcl9pZCwgTm9uZSkgaXMgbm90IE5vbmU6"
    "CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi8J+aqyDhi6jhiJvhjKXhjYvhibUv4Yuz4YyN4YidIOGI"
    "m+GIteGMgOGImOGIquGLqyDhjKXhi6vhiYThi40g4Ymw4Yiw4Yit4Yuf4YiN4Y2jIOGIneGKleGInSDhiqDhiI3hibDhiYDhi6jh"
    "iKjhiJ3hjaIiKQogICAgICAgIGNhbmNlbGxlZF9zb21ldGhpbmcgPSBUcnVlCgogICAgaWYgZWRpdF9wYXltZW50X3N0YXRlLnBv"
    "cCh1c2VyX2lkLCBOb25lKSBpcyBub3QgTm9uZToKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLwn5qr"
    "IOGLqOGKreGNjeGLqyDhiqDhiqvhi43hipXhibUg4Yib4Yi14Ymw4Yqr4Yqo4YurIOGIguGLsOGJsSDhibDhiLDhiK3hi5/hiI3h"
    "jaMg4Yid4YqV4YidIOGKoOGIjeGJsOGJgOGLqOGIqOGIneGNoiIpCiAgICAgICAgY2FuY2VsbGVkX3NvbWV0aGluZyA9IFRydWUK"
    "CiAgICBpZiBub3QgY2FuY2VsbGVkX3NvbWV0aGluZzoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLi"
    "hLnvuI8g4Yuo4Yia4Yiw4Yio4YudIOGKleGJgSDhiILhi7DhibUg4Yuo4YiI4Yid4Y2iIikKCmFzeW5jIGRlZiBoYW5kbGVfYWRt"
    "aW5fbWVkaWEodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgdXNlcl9pZCA9"
    "IHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZAogICAgaWYgdXNlcl9pZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAg"
    "ICBpZiBuZXdyb3VuZF9zdGF0ZS5nZXQodXNlcl9pZCkgPT0gImF3YWl0aW5nX2ltYWdlIiBhbmQgdXBkYXRlLm1lc3NhZ2UucGhv"
    "dG86CiAgICAgICAgYXdhaXQgaGFuZGxlX25ld3JvdW5kX2ltYWdlKHVwZGF0ZSwgY29udGV4dCkKICAgICAgICByZXR1cm4KCiAg"
    "ICBzdGF0ZSA9IGJyb2FkY2FzdF9zdGF0ZS5nZXQodXNlcl9pZCkKICAgIGlmIGlzaW5zdGFuY2Uoc3RhdGUsIGRpY3QpOgogICAg"
    "ICAgIHN0ZXAgPSBzdGF0ZS5nZXQoInN0ZXAiKQoKICAgICAgICBpZiBzdGVwID09ICJjb25maXJtIjoKICAgICAgICAgICAgYXdh"
    "aXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAgICAgICAgICLihLnvuI8g4Yql4Ymj4Yqt4YuOIOGKqOGIi+GL"
    "rSDhiaPhiIjhi40g4YmF4Yu14YiYLeGKpeGLreGJsyDhiIvhi60g4pyFIOGKoOGIqOGMi+GMjeGMpSDhi4jhi63hiJ0g8J+aqyDh"
    "iLDhiK3hi50g4YmB4YiN4Y2N4YqVIOGLreGMoOGJgOGImSAo4YuI4Yut4YidIC9jYW5jZWwg4YuI4Yut4YidIMKr4Yiw4Yit4Yud"
    "wrsg4Yut4YiL4YqpKeGNoiIKICAgICAgICAgICAgKQogICAgICAgICAgICByZXR1cm4KCiAgICAgICAgaWYgc3RlcCA9PSAicGhv"
    "dG8iIGFuZCB1cGRhdGUubWVzc2FnZS5waG90bzoKICAgICAgICAgICAgc3RhdGVbInBob3RvIl0gPSB1cGRhdGUubWVzc2FnZS5w"
    "aG90b1stMV0uZmlsZV9pZAogICAgICAgICAgICBzdGF0ZVsic3RlcCJdID0gIm1lZGlhIgogICAgICAgICAgICBhd2FpdCB1cGRh"
    "dGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICAgICAgIvCfjqXwn46ZIOGJquGLteGLriDhi4jhi63hiJ0g4Yu14Yid"
    "4Yy9ICh2b2ljZSkg4Yib4Yur4Yur4YudIOGLreGNiOGIjeGMi+GIiT8g4Yqo4YiB4YiI4YmxIOGKoOGKleGLseGKlSDhi63hiIvh"
    "iqnhjaMg4YuI4Yut4YidIOGKq+GIjeGNiOGIiOGMiSAvc2tpcCDhi4jhi63hiJ0gwqvhi53hiIjhiI3CuyDhi63hiIvhiqnhjaIi"
    "CiAgICAgICAgICAgICkKICAgICAgICAgICAgcmV0dXJuCgogICAgICAgIGlmIHN0ZXAgPT0gIm1lZGlhIiBhbmQgKHVwZGF0ZS5t"
    "ZXNzYWdlLnZpZGVvIG9yIHVwZGF0ZS5tZXNzYWdlLnZvaWNlKToKICAgICAgICAgICAgaWYgdXBkYXRlLm1lc3NhZ2UudmlkZW86"
    "CiAgICAgICAgICAgICAgICBzdGF0ZVsidmlkZW8iXSA9IHVwZGF0ZS5tZXNzYWdlLnZpZGVvLmZpbGVfaWQKICAgICAgICAgICAg"
    "ZWxpZiB1cGRhdGUubWVzc2FnZS52b2ljZToKICAgICAgICAgICAgICAgIHN0YXRlWyJ2b2ljZSJdID0gdXBkYXRlLm1lc3NhZ2Uu"
    "dm9pY2UuZmlsZV9pZAogICAgICAgICAgICBhd2FpdCBfc2hvd19icm9hZGNhc3RfcHJldmlldyh1cGRhdGUsIGNvbnRleHQsIHVz"
    "ZXJfaWQpCiAgICAgICAgICAgIHJldHVybgoKICAgICAgICBpZiBzdGVwID09ICJwaG90byI6CiAgICAgICAgICAgIGF3YWl0IHVw"
    "ZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIvCflrwg4Y2O4Ym2IOGJpeGJuyDhi63hiIvhiqkg4YuI4Yut4YidIC9za2lwIOGLreGM"
    "q+GKkeGNoiIpCiAgICAgICAgICAgIHJldHVybgoKICAgICAgICBpZiBzdGVwID09ICJtZWRpYSI6CiAgICAgICAgICAgIGF3YWl0"
    "IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIvCfjqXwn46ZIOGJquGLteGLriDhi4jhi63hiJ0g4Yu14Yid4Yy9IOGJpeGJuyDh"
    "i63hiIvhiqkg4YuI4Yut4YidIC9za2lwIOGLreGMq+GKkeGNoiIpCiAgICAgICAgICAgIHJldHVybgoKICAgICAgICByZXR1cm4K"
    "CmFzeW5jIGRlZiBub3RpZnlfZHJhdyh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6"
    "CiAgICAiIiLhiqXhjKPhi40g4YiK4YuI4YyjIOGIsuGIjSDhiYHhjKXhiK0g4Yuo4YyI4YuZIOGJsOGMq+GLi+GJvuGJveGKlSDh"
    "i6jhiJrhi6vhiLPhi43hiYUg4Ym14YuV4Yub4YudCiAgICDhiqDhjKDhiYPhiYDhiJ3hjaYgL25vdGlmeWRyYXcgPOGLmeGIrT4i"
    "IiIKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAgICBhcmdz"
    "ID0gY29udGV4dC5hcmdzCiAgICBpZiBub3QgYXJnczoKICAgICAgICBzb2xkX3JvdW5kX2lkcyA9IFtyaWQgZm9yIHJpZCwgciBp"
    "biByb3VuZHMuaXRlbXMoKSBpZiBhbnkodFsic3RhdHVzIl0gPT0gIlNPTEQiIGZvciB0IGluIHJbInRpY2tldHMiXS52YWx1ZXMo"
    "KSldCiAgICAgICAgYXdhaXQgX3NlbmRfcm91bmRfcGlja2VyKAogICAgICAgICAgICB1cGRhdGUsIGNvbnRleHQsICJub3RpZnlk"
    "cmF3IiwKICAgICAgICAgICAgc29sZF9yb3VuZF9pZHMsCiAgICAgICAgICAgICLwn5OjIOGIteGIiCDhi6jhibXhipvhi40g4YuZ"
    "4YitIOGKpeGMoyDhjIjhi6Lhi47hib3hipUg4Yib4Yiz4YuI4YmFIOGLreGNiOGIjeGMi+GIiT8iLAogICAgICAgICAgICAi4oS5"
    "77iPIOGJoOGKoOGIgeGKkSDhiLDhi5PhibUg4Yuo4Ymw4Yi44YygIOGJsuGKrOGJtSDhi6vhiIjhi40g4YuZ4YitIOGLqOGIiOGI"
    "neGNoiIKICAgICAgICApCiAgICAgICAgcmV0dXJuCgogICAgdHJ5OgogICAgICAgIHJvdW5kX2lkID0gaW50KGFyZ3NbMF0pCiAg"
    "ICBleGNlcHQgVmFsdWVFcnJvcjoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Ym14Yqt"
    "4Yqt4YiI4YqbIOGLqOGLmeGIrSDhiYHhjKXhiK0g4Yur4Yi14YyI4Ymh4Y2iIikKICAgICAgICByZXR1cm4KCiAgICBpZiByb3Vu"
    "ZF9pZCBub3QgaW4gcm91bmRzOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKdjCDhiqXhipXhi7Dh"
    "i5rhiIUg4Yur4YiIIOGLmeGIrSDhiqDhiI3hibDhjIjhipjhiJ3hjaIiKQogICAgICAgIHJldHVybgoKICAgIHNvbGQgPSB7aTog"
    "dCBmb3IgaSwgdCBpbiByb3VuZHNbcm91bmRfaWRdWyJ0aWNrZXRzIl0uaXRlbXMoKSBpZiB0WyJzdGF0dXMiXSA9PSAiU09MRCJ9"
    "CgogICAgaWYgbm90IHNvbGQ6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dChmIuKEue+4jyDhiaDhi5nh"
    "iK0ge3JvdW5kX2lkfSDhiJ3hipXhiJ0g4Yuo4Ymw4Yi44YygIOGJgeGMpeGIrSDhi6jhiIjhiJ3hjaIiKQogICAgICAgIHJldHVy"
    "bgoKICAgIG5vdGlmaWVkID0gMAogICAgZmFpbGVkID0gMAogICAgbWFudWFsX2J1eWVycyA9IFtdCgogICAgZm9yIHRfbnVtLCB0"
    "IGluIHNvbGQuaXRlbXMoKToKICAgICAgICB1aWQgPSB0WyJ1c2VyX2lkIl0KICAgICAgICBpZiB1aWQ6CiAgICAgICAgICAgIHRy"
    "eToKICAgICAgICAgICAgICAgIGF3YWl0IGNvbnRleHQuYm90LnNlbmRfbWVzc2FnZSgKICAgICAgICAgICAgICAgICAgICBjaGF0"
    "X2lkPXVpZCwKICAgICAgICAgICAgICAgICAgICB0ZXh0PSgKICAgICAgICAgICAgICAgICAgICAgICAgZiLwn46yIDxiPuGKpeGM"
    "o+GLjSDhiIrhi4jhjKMg4YqQ4YuNITwvYj5cblxuIgogICAgICAgICAgICAgICAgICAgICAgICBmIuGLqOGMiOGLmeGJtSDhiYHh"
    "jKXhiK0gPGI+e3RfbnVtfTwvYj4g4Ymg4YuZ4YitIHtyb3VuZF9pZH0g4Yql4YyjIOGLjeGIteGMpSDhibDhiqvhibXhibfhiI3h"
    "jaIg4YiY4YiN4Yqr4YidIOGKpeGLteGIjSEg8J+NgCIKICAgICAgICAgICAgICAgICAgICApLAogICAgICAgICAgICAgICAgICAg"
    "IHBhcnNlX21vZGU9IkhUTUwiCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgICAgICBub3RpZmllZCArPSAxCiAgICAgICAg"
    "ICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgICAgICBmYWlsZWQgKz0gMQogICAgICAgIGVsc2U6CiAgICAgICAgICAg"
    "IG1hbnVhbF9idXllcnMuYXBwZW5kKCh0X251bSwgdC5nZXQoImJ1eWVyX25hbWUiKSwgdC5nZXQoImJ1eWVyX3Bob25lIikpKQoK"
    "ICAgIHN1bW1hcnkgPSBmIvCfk6Mg4YiIe25vdGlmaWVkfSDhibDhjKvhi4vhib7hib0gKOGJoOGJpuGJtSDhi6jhjIjhi5kpIOGI"
    "m+GIs+GLiOGJguGLqyDhibDhiI3hirPhiI3hjaIgKHtmYWlsZWR9IOGKoOGIjeGJsOGIs+GKq+GInSkiCiAgICBpZiBtYW51YWxf"
    "YnV5ZXJzOgogICAgICAgIHN1bW1hcnkgKz0gIlxuXG7wn5OeIOGJoOGIteGIjeGKrSDhi6jhjIjhi5kgKOGKpeGKkOGIseGKlSDh"
    "iaDhjI3hiI0g4Yib4Yiz4YuI4YmFIOGLq+GIteGNiOGIjeGMi+GIjSnhjaZcbiIKICAgICAgICBzdW1tYXJ5ICs9ICJcbiIuam9p"
    "bihmIuKWqu+4jyDhiYHhjKXhiK0ge259OiB7bmFtZSBvciAnTi9BJ30gKHtwaG9uZSBvciAnTi9BJ30pIiBmb3IgbiwgbmFtZSwg"
    "cGhvbmUgaW4gbWFudWFsX2J1eWVycykKCiAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KHN1bW1hcnksIHBhcnNl"
    "X21vZGU9IkhUTUwiKQoKZGVmIF93aW5uZXJfcG9zaXRpb25fZW1vamkoaWR4KToKICAgICIiIuGJoOGLneGIreGLneGIqSDhi43h"
    "iLXhjKUg4Ymj4YiI4YuNIOGJheGLsOGInSDhibDhiqjhibDhiI0gKDA94YiY4YyA4YiY4Yiq4YurIOGLqOGJsOGImOGLmOGMiOGJ"
    "oOGLjSkg4YiL4YutIOGJsOGImOGIteGIreGJtiDhi6jhi7DhiKjhjIMg4Yqg4Yit4YibIOGLqOGImuGImOGIjeGItSBoZWxwZXIi"
    "IiIKICAgIG1lZGFscyA9IFsi8J+lhyIsICLwn6WIIiwgIvCfpYkiXQogICAgcmV0dXJuIG1lZGFsc1tpZHhdIGlmIGlkeCA8IGxl"
    "bihtZWRhbHMpIGVsc2UgIvCfjpbvuI8iCgphc3luYyBkZWYgX3NlbmRfd2lubmVyX3RpY2tldF9waWNrZXIodXBkYXRlLCBjb250"
    "ZXh0LCByb3VuZF9pZCk6CiAgICAiIiIvc2V0d2lubmVyIOGLmeGIrSDhiaXhibsg4Ymw4Yyg4YmF4Yi2ICjhi4jhi63hiJ0g4Ymg"
    "4YmB4YiN4Y2NIOGJsOGImOGIreGMpikg4Yiy4YiL4YqtIOGKqOGJsOGIuOGMoSDhiYHhjKXhiK7hib0g4YuN4Yi14YylIOGKoOGI"
    "uOGKk+GNiuGLjeGKlSDhiaDhiYHhiI3hjY0g4YiI4YiY4Yid4Yio4YylIOGLqOGImuGLq+GIs+GLrSBoZWxwZXIiIiIKICAgIHIg"
    "PSByb3VuZHMuZ2V0KHJvdW5kX2lkLCB7fSkKICAgIHNvbGQgPSB7aTogdCBmb3IgaSwgdCBpbiByLmdldCgidGlja2V0cyIsIHt9"
    "KS5pdGVtcygpIGlmIHRbInN0YXR1cyJdID09ICJTT0xEIn0KICAgIGlmIG5vdCBzb2xkOgogICAgICAgIGF3YWl0IHVwZGF0ZS5t"
    "ZXNzYWdlLnJlcGx5X3RleHQoZiLihLnvuI8g4Ymge3JvdW5kX2xhYmVsKHJvdW5kX2lkKX0g4YiL4YutIOGIneGKleGInSDhi6jh"
    "ibDhiLjhjKAg4YmB4Yyl4YitIOGLqOGIiOGIneGKkyDhiqDhiLjhipPhjYog4YiY4YiY4Yud4YyI4YmlIOGKoOGLreGJu+GIjeGI"
    "neGNoiIpCiAgICAgICAgcmV0dXJuCiAgICBhbHJlYWR5ID0ge3dbInRpY2tldF9udW0iXSBmb3IgdyBpbiByLmdldCgid2lubmVy"
    "cyIsIFtdKX0KICAgIGZpbGxlZCA9IGxlbihyLmdldCgid2lubmVycyIsIFtdKSkKICAgIGtiID0gWwogICAgICAgIFtJbmxpbmVL"
    "ZXlib2FyZEJ1dHRvbigKICAgICAgICAgICAgZiJ7J/Cfj4YgJyBpZiB0biBpbiBhbHJlYWR5IGVsc2UgJyd94YmB4Yyl4YitIHt0"
    "bn0g4oCUIHtzb2xkW3RuXS5nZXQoJ2J1eWVyX25hbWUnKSBvciAnTi9BJ30iLAogICAgICAgICAgICBjYWxsYmFja19kYXRhPWYi"
    "c2V0d2lubmVydGlja2V0X3tyb3VuZF9pZH1fe3RufSIKICAgICAgICApXQogICAgICAgIGZvciB0biBpbiBzb3J0ZWQoc29sZCkK"
    "ICAgIF0KICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAgZiLwn4+GIOGKqHtyb3VuZF9sYWJlbChy"
    "b3VuZF9pZCl9IOGLqOGJsOGIuOGMoSDhiYHhjKXhiK7hib0g4YiY4Yqr4Yqo4YiNIOGLqOGJteGKm+GLjSDhiqDhiLjhipDhjYg/"
    "ICIKICAgICAgICBmIijwn4+GIOGIneGIjeGKreGJtSDhi6vhiIjhiaDhibUg4YmA4Yu14Yie4YuN4YqRIOGJsOGImOGLneGMjeGJ"
    "p+GIjSDigJQge2ZpbGxlZH0ve1dJTk5FUl9TTE9UU30g4Ymm4YmzIOGJsOGLreGLn+GIjSkiLAogICAgICAgIHJlcGx5X21hcmt1"
    "cD1JbmxpbmVLZXlib2FyZE1hcmt1cChrYikKICAgICkKCmFzeW5jIGRlZiBfc3RhcnRfc2V0d2lubmVyX3ByaXplX3N0ZXAodXBk"
    "YXRlLCBjb250ZXh0LCByb3VuZF9pZCwgdF9udW0sIGNoYXRfaWQpOgogICAgIiIi4YmB4Yyl4YitIOGKqOGJsOGImOGIqOGMoCDh"
    "iaDhiovhiIsgKOGJoOGJgeGIjeGNjSDhi4jhi63hiJ0g4YmgIC9zZXR3aW5uZXIgPOGLmeGIrT4gPOGJgeGMpeGIrT4pIOGLqOGI"
    "veGIjeGIm+GJtSDhjL3hiIHhjY0g4Yuw4Yio4YyDIOGLqOGImuGMgOGIneGIrSBoZWxwZXIgLQogICAg4YyI4YqV4YmlIOGLiOGL"
    "sCDhiJvhiKjhjIvhjIjhjKsg4Yuw4Yio4YyDIOGLqOGImuGLq+GIjeGNiOGLjSDhiqDhi7XhiJrhipEg4Yy94YiB4Y2NIOGLiOGL"
    "reGInSAvc2tpcCDhiqjhiIvhiqgg4Ymg4YqL4YiLIOGJpeGJuyDhipDhi40gKGhhbmRsZV9hZG1pbl9zbXNfb3JfY29tbWFuZHMg"
    "4YuN4Yi14YylKeGNoiIiIgogICAgYWRtaW5faWQgPSBBRE1JTl9JRAogICAgciA9IHJvdW5kcy5nZXQocm91bmRfaWQsIHt9KQog"
    "ICAgd2lubmVycyA9IHIuZ2V0KCJ3aW5uZXJzIiwgW10pCiAgICBpZiB0X251bSBub3QgaW4ge3dbInRpY2tldF9udW0iXSBmb3Ig"
    "dyBpbiB3aW5uZXJzfSBhbmQgbGVuKHdpbm5lcnMpID49IFdJTk5FUl9TTE9UUzoKICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5z"
    "ZW5kX21lc3NhZ2UoCiAgICAgICAgICAgIGNoYXRfaWQ9Y2hhdF9pZCwKICAgICAgICAgICAgdGV4dD0oCiAgICAgICAgICAgICAg"
    "ICBmIuKaoO+4jyDhi6h7cm91bmRfbGFiZWwocm91bmRfaWQpfSDhiqDhiLjhipPhjYog4Ymm4Ymz4YuO4Ym9IOGImeGIiSDhipPh"
    "ibjhi40gKOGKqOGNjeGJsOGKmyB7V0lOTkVSX1NMT1RTfSnhjaIgIgogICAgICAgICAgICAgICAgIuGJsOGMqOGIm+GIqiDhiJvh"
    "iqjhiI0g4Yqo4Y2I4YiI4YyJIC93aW5uZXJzbG90cyDhibDhjKDhiYXhiJjhi40g4YmB4Yyl4Yip4YqVIOGLreGMqOGIneGIqeGN"
    "oyDhi4jhi63hiJ0g4YqQ4Ymj4YitIOGKoOGIuOGKk+GNiiDhiIvhi60g4Yml4Ym7IOGLq+GLteGIreGMieGNoiIKICAgICAgICAg"
    "ICAgKSwKICAgICAgICApCiAgICAgICAgcmV0dXJuCiAgICBzZXR3aW5uZXJfdGVtcFthZG1pbl9pZF0gPSB7InJvdW5kX2lkIjog"
    "cm91bmRfaWQsICJ0aWNrZXRfbnVtIjogdF9udW19CiAgICBzZXR3aW5uZXJfc3RhdGVbYWRtaW5faWRdID0gImF3YWl0aW5nX3By"
    "aXplIgogICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9tZXNzYWdlKAogICAgICAgIGNoYXRfaWQ9Y2hhdF9pZCwKICAgICAgICB0"
    "ZXh0PSgKICAgICAgICAgICAgZiLwn46BIOGIiOGJgeGMpeGIrSB7dF9udW19ICh7cm91bmRfbGFiZWwocm91bmRfaWQpfSkg4Yi9"
    "4YiN4Yib4Ym1ICjhiIjhiJ3hiLPhiIzhjaYgNSwwMDAg4Yml4YitKSDhjLvhjYnhjaJcbiIKICAgICAgICAgICAgIuGIveGIjeGI"
    "m+GJtSDhiJjhjI3hiIjhjKsg4Yqr4YiN4Y2I4YiI4YyJIC9za2lwIOGLiOGLreGInSDCq+GLneGIiOGIjcK7IOGLreGIi+GKqeGN"
    "oyDhiIjhiJjhiLDhiKjhi50gL2NhbmNlbCDhi4jhi63hiJ0gwqvhiLDhiK3hi53CuyDhi63hiIvhiqnhjaIiCiAgICAgICAgKSwK"
    "ICAgICkKCmFzeW5jIGRlZiBfc2hvd19zZXR3aW5uZXJfY29uZmlybSh1cGRhdGUsIGNvbnRleHQsIHJvdW5kX2lkLCB0X251bSwg"
    "cHJpemUsIGNoYXRfaWQ9Tm9uZSk6CiAgICAiIiIvc2V0d2lubmVyIOGLqOGImOGMqOGIqOGIuyDhi7DhiKjhjIMgLSDhiqDhiLjh"
    "ipPhjYrhi43hipUg4Yqo4YiY4YiY4Yud4YyI4YmlL+GKqOGIm+GIs+GLiOGJhSDhiaDhjYrhibUg4Yqg4Yu14Yia4YqRIOGJoOKc"
    "hS/inYwg4Yuo4Yia4Yur4Yio4YyL4YyN4Yyl4Ymg4Ym1IGhlbHBlciAo4Yia4Yi14Yyl4Yir4YuKIOGJteGLleGLm+GLnSDhiJvh"
    "iKjhjIvhjIjhjKspIiIiCiAgICBhZG1pbl9pZCA9IEFETUlOX0lECiAgICByID0gcm91bmRzLmdldChyb3VuZF9pZCwge30pCiAg"
    "ICB3aW5uZXJzID0gci5nZXQoIndpbm5lcnMiLCBbXSkKICAgIGV4aXN0aW5nID0gbmV4dCgodyBmb3IgdyBpbiB3aW5uZXJzIGlm"
    "IHdbInRpY2tldF9udW0iXSA9PSB0X251bSksIE5vbmUpCiAgICBpZHggPSB3aW5uZXJzLmluZGV4KGV4aXN0aW5nKSBpZiBleGlz"
    "dGluZyBlbHNlIGxlbih3aW5uZXJzKQogICAgZW1vamkgPSBfd2lubmVyX3Bvc2l0aW9uX2Vtb2ppKGlkeCkKICAgIHQgPSByLmdl"
    "dCgidGlja2V0cyIsIHt9KS5nZXQodF9udW0sIHt9KQogICAgbmFtZSA9IHQuZ2V0KCJidXllcl9uYW1lIikgb3IgIk4vQSIKCiAg"
    "ICBwZW5kaW5nX3dpbm5lcl9jb25maXJtW2FkbWluX2lkXSA9IHsicm91bmRfaWQiOiByb3VuZF9pZCwgInRpY2tldF9udW0iOiB0"
    "X251bSwgInByaXplIjogcHJpemV9CgogICAgbGluZXMgPSBbCiAgICAgICAgIvCfj4YgPGI+4Yql4Ymj4Yqt4YuOIOGLq+GIqOGM"
    "i+GMjeGMoTwvYj4iLAogICAgICAgIGYie3JvdW5kX2xhYmVsKHJvdW5kX2lkKX0g4oCUIOGJgeGMpeGIrSAje3RfbnVtfSIsCiAg"
    "ICAgICAgZiLwn5GkIOGMiOGLouGNpiB7bmFtZX0iLAogICAgICAgIGYie2Vtb2ppfSDhi7DhiKjhjIPhjaYge2lkeCArIDF9IiwK"
    "ICAgIF0KICAgIGlmIHByaXplOgogICAgICAgIGxpbmVzLmFwcGVuZChmIvCfjoEg4Yi94YiN4Yib4Ym14Y2mIHtwcml6ZX0iKQog"
    "ICAgaWYgZXhpc3Rpbmc6CiAgICAgICAgbGluZXMuYXBwZW5kKCLinI/vuI8g4Yib4Yiz4Yiw4Ymi4Yur4Y2mIOGLreGIhSDhiYHh"
    "jKXhiK0g4YmA4Yu14Yie4YuN4YqRIOGKoOGIuOGKk+GNiiDhiIbhipYg4Ymw4YiY4Yud4YyN4Ymn4YiN4Y2jIOGKq+GIqOGMi+GM"
    "iOGMoSDhi6jhiL3hiI3hiJvhibUg4YiY4Yio4YyD4YuNIOGJpeGJuyDhi63hiLvhiLvhiIvhiI0gKOGKoOGLsuGItSDhiJvhiLPh"
    "i4jhiYLhi6sg4Yqg4Yut4YiL4Yqt4YidKeGNoiIpCiAgICBlbHNlOgogICAgICAgIGJ1eWVyX3VpZCA9IHQuZ2V0KCJ1c2VyX2lk"
    "IikKICAgICAgICBpZiBidXllcl91aWQ6CiAgICAgICAgICAgIGxpbmVzLmFwcGVuZCgi8J+TqSDhiqvhiKjhjIvhjIjhjKEg4YyI"
    "4Yui4YuNIOGLiOGLsuGLq+GLjeGKkSDhiaDhiKvhiLUt4Yiw4YitIOGLsOGIteGJsyDhiJvhiLPhi4jhiYLhi6sg4Yut4Yuw4Yit"
    "4Yiw4YuL4YiN4Y2iIikKICAgICAgICBlbGlmIHQuZ2V0KCJidXllcl9uYW1lIik6CiAgICAgICAgICAgIGxpbmVzLmFwcGVuZCgi"
    "8J+TniDhi63hiIUg4YmB4Yyl4YitIOGJoOGIteGIjeGKrSDhi6jhibDhiLjhjKAg4Yi14YiI4YiG4YqQIOGIq+GItS3hiLDhiK0g"
    "4Yib4Yiz4YuI4YmC4YurIOGKoOGLreGIi+GKreGInSAtIOGKpeGIreGIteGLjiDhiaDhjI3hiI0g4Yib4Yiz4YuI4YmFIOGLreGK"
    "luGIreGJpeGLjuGJs+GIjeGNoiIpCgogICAga2IgPSBJbmxpbmVLZXlib2FyZE1hcmt1cChbWwogICAgICAgIElubGluZUtleWJv"
    "YXJkQnV0dG9uKCLinIUg4Yqg4Yio4YyL4YyN4YylIOGKpeGKkyDhiJjhi53hjI3hiaUiLCBjYWxsYmFja19kYXRhPSJ3aW5uZXJj"
    "b25maXJtIiksCiAgICAgICAgSW5saW5lS2V5Ym9hcmRCdXR0b24oIuKdjCDhibDhi4jhi40iLCBjYWxsYmFja19kYXRhPSJ3aW5u"
    "ZXJjYW5jZWwiKSwKICAgIF1dKQogICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9tZXNzYWdlKAogICAgICAgIGNoYXRfaWQ9Y2hh"
    "dF9pZCBvciBhZG1pbl9pZCwKICAgICAgICB0ZXh0PSJcbiIuam9pbihsaW5lcyksCiAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIs"
    "CiAgICAgICAgcmVwbHlfbWFya3VwPWtiLAogICAgKQoKYXN5bmMgZGVmIF9yZWdpc3Rlcl93aW5uZXIodXBkYXRlLCBjb250ZXh0"
    "LCByb3VuZF9pZCwgdF9udW0sIHByaXplLCBib3Q9Tm9uZSk6CiAgICAiIiLhiIjhibDhjKDhiYDhiLDhi40g4YuZ4YitL+GJsuGK"
    "rOGJtSDhiYHhjKXhiK0g4Yqg4Yi44YqT4Y2K4YqQ4Ym14YqVIOGLqOGImuGImOGLmOGMjeGJpSAo4YuI4Yut4YidIOGJgOGLteGI"
    "niDhiqjhibDhiJjhi5jhjIjhiaAg4Yuo4Yi94YiN4Yib4Ym1IOGImOGIqOGMgyDhi6jhiJrhi6vhiLvhiL3hiI0pIGhlbHBlciAt"
    "IOKchSDhiJvhiKjhjIvhjIjhjKsg4Yqo4Ymw4Yyr4YqQIOGJoOGKi+GIiyDhiaXhibsg4Yuo4Yia4Yyg4Yir4Y2iCiAgICDhjIjh"
    "i6Lhi40g4Ymg4Ymm4Ym1IOGLqOGMiOGLmyDhiqjhiIbhipAgKHVzZXJfaWQg4Yqr4YiI4YuNKSDhiqDhi7LhiLUg4Yqg4Yi44YqT"
    "4Y2KIOGIsuGIhuGKlSDhiaXhibsg4Yuo4Yuw4Yi14YmzIOGIm+GIs+GLiOGJguGLqyDhiaDhiKvhiLUt4Yiw4YitIOGLreGIi+GK"
    "reGIiOGJs+GIjeGNoiIiIgogICAgYm90ID0gYm90IG9yIGNvbnRleHQuYm90CiAgICByID0gcm91bmRzLmdldChyb3VuZF9pZCkK"
    "ICAgIGlmIG5vdCByOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKdjCDhiqXhipXhi7Dhi5rhiIUg"
    "4Yur4YiIIOGLmeGIrSDhiqDhiI3hibDhjIjhipjhiJ3hjaIiKQogICAgICAgIHJldHVybgoKICAgIHRpY2tldCA9IHJbInRpY2tl"
    "dHMiXS5nZXQodF9udW0pCiAgICBpZiBub3QgdGlja2V0OgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQo"
    "ZiLinYwg4YmB4Yyl4YitIHt0X251bX0g4Ymge3JvdW5kX2xhYmVsKHJvdW5kX2lkKX0g4YiL4YutIOGKoOGIjeGJsOGMiOGKmOGI"
    "neGNoiIpCiAgICAgICAgcmV0dXJuCiAgICBpZiB0aWNrZXRbInN0YXR1cyJdICE9ICJTT0xEIjoKICAgICAgICBhd2FpdCB1cGRh"
    "dGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICAgICBmIuKaoO+4jyDhiYHhjKXhiK0ge3RfbnVtfSDhjIjhipMg4Yqg4YiN"
    "4Ymw4Yi44Yyg4YidICjhiqDhiIHhipPhi4og4YiB4YqU4Ymz4Y2mIHt0aWNrZXRbJ3N0YXR1cyddfSnhjaIg4Yqg4Yi44YqT4Y2K"
    "IOGIm+GLteGIqOGMjSDhi6jhiJrhibvhiIjhi40g4YiI4Ymw4Yi44YygIOGJgeGMpeGIrSDhiaXhibsg4YqQ4YuN4Y2iIgogICAg"
    "ICAgICkKICAgICAgICByZXR1cm4KCiAgICB3aW5uZXJzID0gci5zZXRkZWZhdWx0KCJ3aW5uZXJzIiwgW10pCiAgICBleGlzdGlu"
    "ZyA9IG5leHQoKHcgZm9yIHcgaW4gd2lubmVycyBpZiB3WyJ0aWNrZXRfbnVtIl0gPT0gdF9udW0pLCBOb25lKQogICAgaWYgbm90"
    "IGV4aXN0aW5nIGFuZCBsZW4od2lubmVycykgPj0gV0lOTkVSX1NMT1RTOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJl"
    "cGx5X3RleHQoCiAgICAgICAgICAgIGYi4pqg77iPIOGLqHtyb3VuZF9sYWJlbChyb3VuZF9pZCl9IOGKoOGIuOGKk+GNiiDhiabh"
    "ibPhi47hib0g4YiZ4YiJIOGKk+GJuOGLjSAo4Yqo4Y2N4Ymw4YqbIHtXSU5ORVJfU0xPVFN9KeGNoiAiCiAgICAgICAgICAgICLh"
    "ibDhjKjhiJvhiKog4Yib4Yqo4YiNIOGKqOGNiOGIiOGMiSDhiJjhjIDhiJjhiKrhi6sgL3dpbm5lcnNsb3RzIOGJsOGMoOGJheGI"
    "mOGLjSDhiYHhjKXhiKnhipUg4Yut4Yyo4Yid4YipIOGLiOGLreGInSDhipDhiaPhiK0g4Yqg4Yi44YqT4Y2KIOGLq+GIteGJsOGK"
    "q+GKreGIieGNoiIKICAgICAgICApCiAgICAgICAgcmV0dXJuCgogICAgaWYgZXhpc3Rpbmc6CiAgICAgICAgZXhpc3RpbmdbInBy"
    "aXplIl0gPSBwcml6ZQogICAgICAgIGV4aXN0aW5nWyJzZXRfYXQiXSA9IGRhdGV0aW1lLm5vdygpCiAgICAgICAgbXNnID0gZiLi"
    "nI/vuI8g4YmB4Yyl4YitIHt0X251bX0gKHtyb3VuZF9sYWJlbChyb3VuZF9pZCl9KSDhiYDhi7DhiJ0g4Yml4YiOIOGLqOGJsOGI"
    "mOGLmOGMiOGJoCDhiqDhiLjhipPhjYog4Yi14YiI4YiG4YqQIOGLqOGIveGIjeGIm+GJtSDhiJjhiKjhjIPhi40g4Ymw4Yi14Ymw"
    "4Yqr4Yqt4YiP4YiN4Y2iIgogICAgICAgIGlzX25ldyA9IEZhbHNlCiAgICBlbHNlOgogICAgICAgIHdpbm5lcnMuYXBwZW5kKHsi"
    "dGlja2V0X251bSI6IHRfbnVtLCAicHJpemUiOiBwcml6ZSwgInNldF9hdCI6IGRhdGV0aW1lLm5vdygpfSkKICAgICAgICBtc2cg"
    "PSBmIuKchSDhiYHhjKXhiK0ge3RfbnVtfSAoe3JvdW5kX2xhYmVsKHJvdW5kX2lkKX0pIOGKpeGKleGLsCDhiqDhiLjhipPhjYog"
    "4Ymw4YiY4Yud4YyN4Ymn4YiN4Y2iIgogICAgICAgIGlzX25ldyA9IFRydWUKICAgIHNhdmVfc3RhdGUoKQoKICAgIGlkeCA9IG5l"
    "eHQoaSBmb3IgaSwgdyBpbiBlbnVtZXJhdGUod2lubmVycykgaWYgd1sidGlja2V0X251bSJdID09IHRfbnVtKQogICAgZW1vamkg"
    "PSBfd2lubmVyX3Bvc2l0aW9uX2Vtb2ppKGlkeCkKICAgIG1zZyA9IGYie2Vtb2ppfSB7bXNnfSIKICAgIGlmIHByaXplOgogICAg"
    "ICAgIG1zZyArPSBmIlxu8J+OgSDhiL3hiI3hiJvhibXhjaYge3ByaXplfSIKCiAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBs"
    "eV90ZXh0KAogICAgICAgIG1zZywKICAgICAgICByZXBseV9tYXJrdXA9KAogICAgICAgICAgICBJbmxpbmVLZXlib2FyZE1hcmt1"
    "cChbWwogICAgICAgICAgICAgICAgSW5saW5lS2V5Ym9hcmRCdXR0b24oIvCflJkgVW5kbyAo4Yqg4Yi44YqT4Y2K4YqQ4Ymx4YqV"
    "IOGKoOGMpeGNiykiLCBjYWxsYmFja19kYXRhPWYidW5kb3dpbm5lcl97cm91bmRfaWR9X3t0X251bX0iKSwKICAgICAgICAgICAg"
    "XV0pIGlmIGlzX25ldyBlbHNlIE5vbmUKICAgICAgICApLAogICAgKQoKICAgIHVpZCA9IHRpY2tldC5nZXQoInVzZXJfaWQiKQog"
    "ICAgaWYgaXNfbmV3IGFuZCB1aWQ6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBwcml6ZV9saW5lID0gZiJcbvCfjoEg4Yi94YiN"
    "4Yib4Ym14Y2mIHtwcml6ZX0iIGlmIHByaXplIGVsc2UgIiIKICAgICAgICAgICAgYXdhaXQgYm90LnNlbmRfbWVzc2FnZSgKICAg"
    "ICAgICAgICAgICAgIGNoYXRfaWQ9dWlkLAogICAgICAgICAgICAgICAgdGV4dD0oCiAgICAgICAgICAgICAgICAgICAgZiLwn46J"
    "8J+PhiDhiqXhipXhirPhipUg4Yuw4Yi1IOGKoOGIiOGLjuGJtSEg4YmB4Yyl4YitICN7dF9udW19ICh7cm91bmRfbGFiZWwocm91"
    "bmRfaWQpfSkg4Yqg4Yi44YqT4Y2KIOGIhuGKl+GIjSF7cHJpemVfbGluZX1cblxuIgogICAgICAgICAgICAgICAgICAgICLhiIjh"
    "ibDhjKjhiJvhiKog4YiY4Yio4YyDIOGKqOGIhuGIteGJtSDhjIvhiK0g4Yur4YyN4YqZ4Y2iIgogICAgICAgICAgICAgICAgKSwK"
    "ICAgICAgICAgICAgICAgIHBhcnNlX21vZGU9IkhUTUwiCiAgICAgICAgICAgICkKICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgog"
    "ICAgICAgICAgICBwYXNzCiAgICBlbGlmIGlzX25ldyBhbmQgbm90IHVpZCBhbmQgdGlja2V0LmdldCgiYnV5ZXJfbmFtZSIpOgog"
    "ICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAgICAgIGYi8J+TniDhi63hiIUg4YmB4Yyl4Yit"
    "IOGJoOGIteGIjeGKrSDhi6jhibDhiLjhjKAg4Yi14YiI4YiG4YqQIOGIq+GItS3hiLDhiK0g4Yib4Yiz4YuI4YmC4YurIOGKoOGI"
    "jeGJsOGIi+GKqOGIneGNoiDhjIjhi6LhjaYge3RpY2tldC5nZXQoJ2J1eWVyX25hbWUnKX0gIgogICAgICAgICAgICBmIih7dGlj"
    "a2V0LmdldCgnYnV5ZXJfcGhvbmUnKSBvciAnTi9BJ30pIC0g4Yql4Ymj4Yqt4YuOIOGJoOGMjeGIjSDhi6vhiLPhi43hiYvhibjh"
    "i43hjaIiCiAgICAgICAgKQoKYXN5bmMgZGVmIHNldF93aW5uZXJfY29tbWFuZCh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29u"
    "dGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhiqDhi7XhiJrhipUg4Yql4YyjIOGKqOGLiOGMoyDhiaDhiovhiIsg4Yuo"
    "4Yqg4YqV4Yu1IOGLmeGIrSDhiqDhiLjhipPhjYog4YmB4Yyl4YitKOGLjuGJvSkg4Yuw4Yio4YyDLeGJoOGLsOGIqOGMgyDhi6jh"
    "iJrhiJjhi5jhjI3hiaXhiaDhibUg4Ym14YuV4Yub4YudICjhiqXhiLXhiqggV0lOTkVSX1NMT1RTIOGLteGIqOGItSAtIOGJoCAv"
    "d2lubmVyc2xvdHMg4Yut4YmA4Yuo4Yir4YiNKQogICAg4Yqg4Yyg4YmD4YmA4Yid4Y2mIC9zZXR3aW5uZXIgPOGLmeGIrT4gW+GJ"
    "geGMpeGIrV0gW+GIveGIjeGIm+GJtS4uLl0gLSDhi7DhiKjhjIPhi40g4Ymj4YiN4Yyg4YmA4Yix4Ym1IOGJgeGMpeGIrSDhiI3h"
    "iq0g4Yut4YmA4Yyl4YiL4YiN4Y2mCiAgICDilqrvuI8g4Yur4YiIIGFyZ3PhjaYg4YiY4YyA4YiY4Yiq4YurIOGLmeGIrSDhiaDh"
    "iYHhiI3hjY0g4Yut4Yid4Yio4YyhCiAgICDilqrvuI8g4YuZ4YitIOGJpeGJu+GNpiDhiqjhi5rhi6sg4Yuo4Ymw4Yi44YygIOGJ"
    "geGMpeGIrSDhiaDhiYHhiI3hjY0g4Yut4Yid4Yio4YyhCiAgICDilqrvuI8g4YuZ4YitK+GJgeGMpeGIrSAo4Yur4YiIIOGIveGI"
    "jeGIm+GJtSnhjaYg4YmA4Yyl4YiOIOGIveGIjeGIm+GJtSDhjL3hiIHhjY0g4Yut4Yyg4Yuo4YmD4YiJICjhi4jhi63hiJ0gL3Nr"
    "aXApCiAgICDilqrvuI8g4YuZ4YitK+GJgeGMpeGIrSvhiL3hiI3hiJvhibXhjaYg4Ymg4YmA4Yyl4YmzIOGLiOGLsCDinIUv4p2M"
    "IOGLqOGImOGMqOGIqOGIuyDhiJvhiKjhjIvhjIjhjKsg4Yut4YiE4Yuz4YiNICjhiJ3hipXhiJ0g4YqQ4YyI4YitIOGIq+GItS3h"
    "iLDhiK0g4Yqg4Yut4YiY4YuY4YyI4Yml4YidKSIiIgogICAgaWYgdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkICE9IEFETUlO"
    "X0lEOgogICAgICAgIHJldHVybgoKICAgIGFyZ3MgPSBjb250ZXh0LmFyZ3MKICAgIGlmIG5vdCBhcmdzOgogICAgICAgIGVsaWdp"
    "YmxlID0gW3JpZCBmb3IgcmlkLCByIGluIHJvdW5kcy5pdGVtcygpIGlmIGFueSh0WyJzdGF0dXMiXSA9PSAiU09MRCIgZm9yIHQg"
    "aW4gclsidGlja2V0cyJdLnZhbHVlcygpKV0KICAgICAgICBhd2FpdCBfc2VuZF9yb3VuZF9waWNrZXIoCiAgICAgICAgICAgIHVw"
    "ZGF0ZSwgY29udGV4dCwgInNldHdpbm5lciIsCiAgICAgICAgICAgIGVsaWdpYmxlLAogICAgICAgICAgICAi8J+PhiDhi6jhi6jh"
    "ibXhipvhi40g4YuZ4YitIOGKoOGIuOGKk+GNiiDhiJjhiJjhi53hjIjhiaUg4Yut4Y2I4YiN4YyL4YiJPyIsCiAgICAgICAgICAg"
    "ICLihLnvuI8g4Yuo4Ymw4Yi44YygIOGJgeGMpeGIrSDhi6vhiIjhi40g4YuZ4YitIOGLqOGIiOGIneGNoiIKICAgICAgICApCiAg"
    "ICAgICAgcmV0dXJuCgogICAgdHJ5OgogICAgICAgIHJvdW5kX2lkID0gaW50KGFyZ3NbMF0pCiAgICBleGNlcHQgVmFsdWVFcnJv"
    "cjoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4Ym14Yqt4Yqt4YiI4YqbIOGLqOGLmeGI"
    "rSDhiYHhjKXhiK0g4Yur4Yi14YyI4Ymh4Y2iIikKICAgICAgICByZXR1cm4KCiAgICBpZiByb3VuZF9pZCBub3QgaW4gcm91bmRz"
    "OgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKdjCDhiqXhipXhi7Dhi5rhiIUg4Yur4YiIIOGLmeGI"
    "rSDhiqDhiI3hibDhjIjhipjhiJ3hjaIiKQogICAgICAgIHJldHVybgoKICAgIGlmIGxlbihhcmdzKSA8IDI6CiAgICAgICAgYXdh"
    "aXQgX3NlbmRfd2lubmVyX3RpY2tldF9waWNrZXIodXBkYXRlLCBjb250ZXh0LCByb3VuZF9pZCkKICAgICAgICByZXR1cm4KCiAg"
    "ICB0cnk6CiAgICAgICAgdF9udW0gPSBpbnQoYXJnc1sxXSkKICAgIGV4Y2VwdCBWYWx1ZUVycm9yOgogICAgICAgIGF3YWl0IHVw"
    "ZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKaoO+4jyDhibXhiq3hiq3hiIjhipsg4Yuo4Ymy4Yqs4Ym1IOGJgeGMpeGIrSDhi6vh"
    "iLXhjIjhiaHhjaIiKQogICAgICAgIHJldHVybgoKICAgIGlmIGxlbihhcmdzKSA8IDM6CiAgICAgICAgYXdhaXQgX3N0YXJ0X3Nl"
    "dHdpbm5lcl9wcml6ZV9zdGVwKHVwZGF0ZSwgY29udGV4dCwgcm91bmRfaWQsIHRfbnVtLCBjaGF0X2lkPXVwZGF0ZS5tZXNzYWdl"
    "LmNoYXRfaWQpCiAgICAgICAgcmV0dXJuCgogICAgcHJpemUgPSAiICIuam9pbihhcmdzWzI6XSkuc3RyaXAoKSBvciBOb25lCiAg"
    "ICBhd2FpdCBfc2hvd19zZXR3aW5uZXJfY29uZmlybSh1cGRhdGUsIGNvbnRleHQsIHJvdW5kX2lkLCB0X251bSwgcHJpemUsIGNo"
    "YXRfaWQ9dXBkYXRlLm1lc3NhZ2UuY2hhdF9pZCkKCmFzeW5jIGRlZiBfcmVzb2x2ZV9yb3VuZF9mb3Jfd2lubmVyc192aWV3KHVw"
    "ZGF0ZSwgYXJncyk6CiAgICAiIiIvd2lubmVycyDhibXhi5Xhi5vhi50g4YiL4YutIOGLqOGJteGKm+GLjeGKlSDhi5nhiK0g4Yql"
    "4YqV4Yuw4Yia4Yur4YiY4YiI4Yqt4Ym1IOGLqOGImuGLiOGIteGKlSBoZWxwZXIgKOGKoOGLteGImuGKleGInSDhibDhjKvhi4vh"
    "ib3hiJ0g4Yut4Yyg4YmA4YiZ4Ymg4Ymz4YiNKeGNogogICAgcm91bmRfaWQg4Ymw4Yyg4YmF4Yi2IOGKqOGIhuGKkCDhi6vhipXh"
    "ipUg4Yut4Yyg4YmA4Yib4YiNICjhiJvhipXhipvhi43hiJ0g4YiB4YqU4YmzIOGLq+GIiOGLjSDhi5nhiK0g4Ymi4YiG4YqV4Yid"
    "KeGNpCDhiqvhiI3hibDhjKDhiYDhiLAg4YyN4YqVIOGKpeGMoyDhi6jhi4jhjKPhiIvhibjhi40g4YuZ4Yiu4Ym9IOGJpeGJuyDh"
    "iaDhiYHhiI3hjY0g4Yut4Ymz4Yur4YiJ4Y2iIiIiCiAgICBpZiBhcmdzOgogICAgICAgIHRyeToKICAgICAgICAgICAgcmlkID0g"
    "aW50KGFyZ3NbMF0pCiAgICAgICAgZXhjZXB0IFZhbHVlRXJyb3I6CiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJl"
    "cGx5X3RleHQoIuKaoO+4jyDhibXhiq3hiq3hiIjhipsg4Yuo4YuZ4YitIOGJgeGMpeGIrSDhi6vhiLXhjIjhiaHhjaIiKQogICAg"
    "ICAgICAgICByZXR1cm4gTm9uZQogICAgICAgIGlmIHJpZCBub3QgaW4gcm91bmRzOgogICAgICAgICAgICBhd2FpdCB1cGRhdGUu"
    "bWVzc2FnZS5yZXBseV90ZXh0KCLinYwg4Yql4YqV4Yuw4Yua4YiFIOGLq+GIiCDhi5nhiK0g4Yqg4YiN4Ymw4YyI4YqY4Yid4Y2i"
    "IikKICAgICAgICAgICAgcmV0dXJuIE5vbmUKICAgICAgICByZXR1cm4gcmlkCgogICAgd2lubmVyX3JvdW5kX2lkcyA9IHNvcnRl"
    "ZChyaWQgZm9yIHJpZCwgciBpbiByb3VuZHMuaXRlbXMoKSBpZiByLmdldCgid2lubmVycyIpKQogICAgaWYgbm90IHdpbm5lcl9y"
    "b3VuZF9pZHM6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4oS577iPIOGKpeGIteGKq+GIgeGKlSDh"
    "iIjhi6jhibXhipvhi43hiJ0g4YuZ4YitIOGKoOGIuOGKk+GNiiDhiqDhiI3hibDhiJjhi5jhjIjhiaDhiJ3hjaIiKQogICAgICAg"
    "IHJldHVybiBOb25lCgogICAga2IgPSBbCiAgICAgICAgW0lubGluZUtleWJvYXJkQnV0dG9uKAogICAgICAgICAgICBmIntyb3Vu"
    "ZF9sYWJlbChyaWQpfSDigJQge2xlbihyb3VuZHNbcmlkXVsnd2lubmVycyddKX0g4Yqg4Yi44YqT4Y2KKOGLjuGJvSkiLAogICAg"
    "ICAgICAgICBjYWxsYmFja19kYXRhPWYicGxheWVycGlja193aW5uZXJzX3tyaWR9IgogICAgICAgICldCiAgICAgICAgZm9yIHJp"
    "ZCBpbiB3aW5uZXJfcm91bmRfaWRzCiAgICBdCiAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICLw"
    "n4+GIOGLqOGLqOGJteGKm+GLjSDhi5nhiK0g4Yqg4Yi44YqT4Y2K4YuO4Ym9IOGIm+GLqOGJtSDhi63hjYjhiI3hjIvhiIk/IiwK"
    "ICAgICAgICByZXBseV9tYXJrdXA9SW5saW5lS2V5Ym9hcmRNYXJrdXAoa2IpCiAgICApCiAgICByZXR1cm4gTm9uZQoKYXN5bmMg"
    "ZGVmIHdpbm5lcnNfY29tbWFuZCh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAg"
    "ICAiIiLhiJvhipXhipvhi43hiJ0g4Ymw4Yyr4YuL4Ym9IOGLiOGLreGInSDhiqDhi7XhiJrhipUg4Yuo4Yqg4YqV4Yu1IOGLmeGI"
    "rSDhiqDhiLjhipPhjYoo4YuO4Ym9KSDhi6jhiJrhi6vhi63hiaDhibUg4Ym14YuV4Yub4YudCiAgICDhiqDhjKDhiYPhiYDhiJ3h"
    "jaYgL3dpbm5lcnMgPOGLmeGIrT4gKOGKq+GIjeGMiOGIiOGMuSDhiqXhjKMg4Yuo4YuI4Yyj4YiL4Ym44YuNIOGLmeGIruGJvSDh"
    "iaXhibsg4Ymg4YmB4YiN4Y2NIOGLreGJs+GLq+GIiSkiIiIKICAgIHJvdW5kX2lkID0gYXdhaXQgX3Jlc29sdmVfcm91bmRfZm9y"
    "X3dpbm5lcnNfdmlldyh1cGRhdGUsIGNvbnRleHQuYXJncykKICAgIGlmIHJvdW5kX2lkIGlzIE5vbmU6CiAgICAgICAgcmV0dXJu"
    "CgogICAgciA9IHJvdW5kc1tyb3VuZF9pZF0KICAgIHdpbm5lcnMgPSByLmdldCgid2lubmVycyIsIFtdKQogICAgaWYgbm90IHdp"
    "bm5lcnM6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dChmIuKEue+4jyDhiIh7cm91bmRfbGFiZWwocm91"
    "bmRfaWQpfSDhiqXhiLXhiqvhiIHhipUg4Yid4YqV4YidIOGKoOGIuOGKk+GNiiDhiqDhiI3hibDhiJjhi5jhjIjhiaDhiJ3hjaIg"
    "4Yql4Yyj4YuNIOGMiOGKkyDhiqvhiI3hi4jhjKMg4Ym14YqV4Yi9IOGJhuGLreGJsOGLjSDhi63hiJ7hiq3hiKnhjaIiKQogICAg"
    "ICAgIHJldHVybgoKICAgIGlzX2FkbWluID0gdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkID09IEFETUlOX0lECiAgICBsaW5l"
    "cyA9IFtmIvCfj4YgPGI+4Yqg4Yi44YqT4Y2K4YuO4Ym9IOKAlCB7cm91bmRfbGFiZWwocm91bmRfaWQpfTwvYj4gKHtsZW4od2lu"
    "bmVycyl9L3tXSU5ORVJfU0xPVFN9KVxuIl0KICAgIGZvciBpZHgsIHcgaW4gZW51bWVyYXRlKHdpbm5lcnMpOgogICAgICAgIHRf"
    "bnVtID0gd1sidGlja2V0X251bSJdCiAgICAgICAgdCA9IHJbInRpY2tldHMiXS5nZXQodF9udW0sIHt9KQogICAgICAgIG5hbWUg"
    "PSB0LmdldCgiYnV5ZXJfbmFtZSIpIG9yICJOL0EiCiAgICAgICAgcGhvbmUgPSB0LmdldCgiYnV5ZXJfcGhvbmUiKQogICAgICAg"
    "IHBob25lX3R4dCA9IHBob25lIGlmIGlzX2FkbWluIGVsc2UgbWFza19waG9uZShwaG9uZSkKICAgICAgICBlbW9qaSA9IF93aW5u"
    "ZXJfcG9zaXRpb25fZW1vamkoaWR4KQogICAgICAgIGxpbmUgPSBmIntlbW9qaX0g4YmB4Yyl4YitICN7dF9udW19IOKAlCB7bmFt"
    "ZX0gKHtwaG9uZV90eHQgb3IgJ04vQSd9KSIKICAgICAgICBpZiB3LmdldCgicHJpemUiKToKICAgICAgICAgICAgbGluZSArPSBm"
    "IlxuICAg8J+OgSDhiL3hiI3hiJvhibXhjaYge3dbJ3ByaXplJ119IgogICAgICAgIGxpbmVzLmFwcGVuZChsaW5lKQoKICAgIGF3"
    "YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIlxuIi5qb2luKGxpbmVzKSwgcGFyc2VfbW9kZT0iSFRNTCIpCgphc3luYyBk"
    "ZWYgc2V0X3dpbm5lcl9zbG90c19jb21tYW5kKHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9U"
    "WVBFKToKICAgICIiIuGKoOGLteGImuGKlSDhiIjhi5rhiIUg4YiG4Yi14Ym1IOGIteGKleGJtSDhi6jhiqDhiLjhipPhjYog4Ymm"
    "4YmzICgx4YqbLzLhipsvM+GKmy804YqbLi4uKSDhiqXhipXhi7DhiJrhjYjhiYDhi7Ug4Yuo4Yia4YmA4Yut4Yit4Ymg4Ym1IOGJ"
    "teGLleGLm+GLnSAo4Yia4Yi14Yyl4Yir4YuKIOGIteGIiOGIhuGKkCDinIUv4p2MIOGIm+GIqOGMi+GMiOGMqyDhi63hjKDhi63h"
    "iYPhiI0pCiAgICDhiqDhjKDhiYPhiYDhiJ3hjaYgL3dpbm5lcnNsb3RzIDzhiYHhjKXhiK0+ICAo4YqoMSDhiqXhiLXhiqggMTAg"
    "4YiY4Yqr4Yqo4YiN4Y2jIOGKkOGJo+GIqiAzKSIiIgogICAgaWYgdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkICE9IEFETUlO"
    "X0lEOgogICAgICAgIHJldHVybgoKICAgIGFyZ3MgPSBjb250ZXh0LmFyZ3MKICAgIGlmIG5vdCBhcmdzOgogICAgICAgIHVpZCA9"
    "IHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZAogICAgICAgIGNsZWFyZWQgPSBfY2FuY2VsX290aGVyX2FkbWluX3Rhc2tzKHVp"
    "ZCwga2VlcD0id2lubmVyc2xvdHMiKQogICAgICAgIGF3YWl0IF9ub3RpZnlfY2FuY2VsbGVkX3Rhc2tzKGNvbnRleHQuYm90LCB1"
    "aWQsIGNsZWFyZWQpCiAgICAgICAgd2lubmVyc2xvdHNfc3RhdGUuYWRkKHVpZCkKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2Fn"
    "ZS5yZXBseV90ZXh0KAogICAgICAgICAgICBmIvCfj4Ug4Yqg4YiB4YqVIOGIi+GLrSDhi6jhibDhjYjhiYDhi7Dhi40g4Yuo4Yqg"
    "4Yi44YqT4Y2KIOGJpuGJs+GLjuGJvSDhiaXhi5vhibXhjaYge1dJTk5FUl9TTE9UU31cblxuIgogICAgICAgICAgICAi4Yi14YqV"
    "4Ym1IOGKoOGLsuGItSDhiYHhjKXhiK0g4Yut4Y2I4YiN4YyL4YiJPyDhiqgxIOGKpeGIteGKqCAxMCDhiJjhiqvhiqjhiI0g4YmB"
    "4Yyl4YitIOGJpeGJuyDhi63hiIvhiqnhjaZcblxuIgogICAgICAgICAgICAi4YiI4YiY4Yiw4Yio4YudIC9jYW5jZWwg4Yut4YiL"
    "4Yqp4Y2iIgogICAgICAgICkKICAgICAgICByZXR1cm4KCiAgICB0cnk6CiAgICAgICAgbiA9IGludChhcmdzWzBdKQogICAgZXhj"
    "ZXB0IFZhbHVlRXJyb3I6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4pqg77iPIOGJteGKreGKreGI"
    "iOGKmyDhiYHhjKXhiK0g4Yur4Yi14YyI4Ymh4Y2iIikKICAgICAgICByZXR1cm4KICAgIGlmIG4gPCAxIG9yIG4gPiAxMDoKICAg"
    "ICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLimqDvuI8g4YqoMSDhiqXhiLXhiqggMTAg4YiY4Yqr4Yqo4YiN"
    "IOGJgeGMpeGIrSDhi6vhiLXhjIjhiaHhjaIiKQogICAgICAgIHJldHVybgogICAgaWYgbiA9PSBXSU5ORVJfU0xPVFM6CiAgICAg"
    "ICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dChmIuKEue+4jyDhi6jhiqDhiLjhipPhjYog4Ymm4Ymz4YuO4Ym9IOGJ"
    "geGMpeGIrSDhiqDhiLXhiYDhi7XhiJ4ge259IOGKkOGLjeGNoiIpCiAgICAgICAgcmV0dXJuCgogICAgcGVuZGluZ193aW5uZXJz"
    "bG90c19jb25maXJtW3VwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZF0gPSBuCiAgICBrYiA9IElubGluZUtleWJvYXJkTWFya3Vw"
    "KFtbCiAgICAgICAgSW5saW5lS2V5Ym9hcmRCdXR0b24oIuKchSDhiqDhi47hjaMg4YmA4Yut4YitIiwgY2FsbGJhY2tfZGF0YT0i"
    "d2lubmVyc2xvdHNjb25maXJtIiksCiAgICAgICAgSW5saW5lS2V5Ym9hcmRCdXR0b24oIuKdjCDhiqDhi63hjaMg4Ymw4YuI4YuN"
    "IiwgY2FsbGJhY2tfZGF0YT0id2lubmVyc2xvdHNjYW5jZWwiKSwKICAgIF1dKQogICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVw"
    "bHlfdGV4dCgKICAgICAgICBmIuKaoO+4jyDhi6jhiqDhiLjhipPhjYog4Ymm4Ymz4YuO4Ym9IOGJpeGLm+GJtSDhiqh7V0lOTkVS"
    "X1NMT1RTfSDhi4jhi7Age259IOGImOGJgOGLqOGIrSDhiqXhiK3hjI3hjKDhipsg4YqQ4YuO4Ym1P1xuIgogICAgICAgICIo4YqQ"
    "4Ymj4YitIOGLqOGJsOGImOGLmOGMiOGJoSDhiqDhiLjhipPhjYrhi47hib0g4Yqg4Yut4YqQ4Yqp4YidIC0g4YuI4Yuw4Y2K4Ym1"
    "IOGIiOGImuGImOGLmOGMiOGJoeGJtSDhiaXhibsg4Ymw4Y2F4Yql4YqWIOGLreGKluGIqOGLi+GIjSkiLAogICAgICAgIHJlcGx5"
    "X21hcmt1cD1rYgogICAgKQoKYXN5bmMgZGVmIGhhbmRsZV93aW5uZXJzbG90c19jb25maXJtKHVwZGF0ZTogVXBkYXRlLCBjb250"
    "ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgIGdsb2JhbCBXSU5ORVJfU0xPVFMKICAgIHF1ZXJ5ID0gdXBkYXRl"
    "LmNhbGxiYWNrX3F1ZXJ5CiAgICBpZiBxdWVyeS5mcm9tX3VzZXIuaWQgIT0gQURNSU5fSUQ6CiAgICAgICAgYXdhaXQgcXVlcnku"
    "YW5zd2VyKCkKICAgICAgICByZXR1cm4KICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICBuID0gcGVuZGluZ193aW5uZXJzbG90"
    "c19jb25maXJtLnBvcChxdWVyeS5mcm9tX3VzZXIuaWQsIE5vbmUpCiAgICBpZiBuIGlzIE5vbmU6CiAgICAgICAgdHJ5OgogICAg"
    "ICAgICAgICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfdGV4dCgi4pqg77iPIOGLreGIhSDhjKXhi6vhiYQg4YyK4Yuc4YuNIOGK"
    "oOGIjeGNjuGJoOGJs+GIjeGNoyAvd2lubmVyc2xvdHMg4Yuw4YyN4YiY4YuNIOGLreGIi+GKqeGNoiIpCiAgICAgICAgZXhjZXB0"
    "IEV4Y2VwdGlvbjoKICAgICAgICAgICAgcGFzcwogICAgICAgIHJldHVybgogICAgV0lOTkVSX1NMT1RTID0gbgogICAgc2F2ZV9z"
    "dGF0ZSgpCiAgICB0cnk6CiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoZiLinIUg4Yuo4Yqg4Yi44YqT4Y2K"
    "IOGJpuGJs+GLjuGJvSDhiaXhi5vhibUg4YuI4YuwIHtufSDhibDhiYDhi63hiK/hiI3hjaIiKQogICAgZXhjZXB0IEV4Y2VwdGlv"
    "bjoKICAgICAgICBwYXNzCgphc3luYyBkZWYgaGFuZGxlX3dpbm5lcnNsb3RzX2NhbmNlbCh1cGRhdGU6IFVwZGF0ZSwgY29udGV4"
    "dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgaWYgcXVl"
    "cnkuZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgcmV0dXJuCiAg"
    "ICBwZW5kaW5nX3dpbm5lcnNsb3RzX2NvbmZpcm0ucG9wKHF1ZXJ5LmZyb21fdXNlci5pZCwgTm9uZSkKICAgIGF3YWl0IHF1ZXJ5"
    "LmFuc3dlcigi4Ymw4Yiw4Yit4Yuf4YiN4Y2iIikKICAgIHRyeToKICAgICAgICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfdGV4"
    "dCgi4p2MIOGJsOGIsOGIreGLn+GIjeGNoyDhiJ3hipXhiJ0g4Yqg4YiN4Ymw4YmA4Yuo4Yio4Yid4Y2iIikKICAgIGV4Y2VwdCBF"
    "eGNlcHRpb246CiAgICAgICAgcGFzcwoKYXN5bmMgZGVmIGhhbmRsZV93aW5uZXJfY29uZmlybSh1cGRhdGU6IFVwZGF0ZSwgY29u"
    "dGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiIvc2V0d2lubmVyIOGIi+GLrSDhi6jhiJjhjKjhiKjhiLvh"
    "i43hipUgwqvinIUg4Yqg4Yio4YyL4YyN4YylIOGKpeGKkyDhiJjhi53hjI3hiaXCuyDhiYHhiI3hjY0g4Yiy4Yyr4YqRIOGKpeGL"
    "jeGKkOGJsOGKm+GLjeGKlSDhiJ3hi53hjIjhiaMv4Yib4Yiz4YuI4YmC4YurIOGLqOGImuGNiOGMveGInSBjYWxsYmFjayIiIgog"
    "ICAgcXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIGlmIHF1ZXJ5LmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAg"
    "ICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIHJldHVybgogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgIGRhdGEg"
    "PSBwZW5kaW5nX3dpbm5lcl9jb25maXJtLnBvcChxdWVyeS5mcm9tX3VzZXIuaWQsIE5vbmUpCiAgICBpZiBub3QgZGF0YToKICAg"
    "ICAgICB0cnk6CiAgICAgICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KCLimqDvuI8g4Yut4YiFIOGMpeGLq+GJ"
    "hCDhjIrhi5zhi40g4Yqg4YiN4Y2O4Ymg4Ymz4YiN4Y2jIC9zZXR3aW5uZXIg4Yuw4YyN4YiY4YuNIOGLreGMgOGIneGIqeGNoiIp"
    "CiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgcGFzcwogICAgICAgIHJldHVybgogICAgdHJ5OgogICAgICAg"
    "IGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KCLij7Mg4Ymg4YiY4YiY4Yud4YyI4YmlIOGIi+GLrS4uLiIpCiAgICBleGNl"
    "cHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKICAgIGZha2VfdXBkYXRlID0gX0Zha2VVcGRhdGUoX0Zha2VNc2cocXVlcnkubWVz"
    "c2FnZSwgcXVlcnkuZnJvbV91c2VyKSkKICAgIGF3YWl0IF9yZWdpc3Rlcl93aW5uZXIoZmFrZV91cGRhdGUsIGNvbnRleHQsIGRh"
    "dGFbInJvdW5kX2lkIl0sIGRhdGFbInRpY2tldF9udW0iXSwgZGF0YVsicHJpemUiXSwgYm90PWNvbnRleHQuYm90KQoKYXN5bmMg"
    "ZGVmIGhhbmRsZV93aW5uZXJfY2FuY2VsKHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBF"
    "KToKICAgICIiIi9zZXR3aW5uZXIg4YiL4YutIMKr4p2MIOGJsOGLiOGLjcK7IOGJgeGIjeGNjSDhiLLhjKvhipEg4Yur4YiIIOGI"
    "neGKleGInSDhiJ3hi53hjIjhiaMv4Yib4Yiz4YuI4YmC4YurIOGLqOGImuGLq+GJi+GIreGMpSBjYWxsYmFjayIiIgogICAgcXVl"
    "cnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIGlmIHF1ZXJ5LmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICBh"
    "d2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIHJldHVybgogICAgcGVuZGluZ193aW5uZXJfY29uZmlybS5wb3AocXVlcnkuZnJv"
    "bV91c2VyLmlkLCBOb25lKQogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCLhibDhiLDhiK3hi5/hiI3hjaIiKQogICAgdHJ5OgogICAg"
    "ICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KCLinYwg4Ymw4Yiw4Yit4Yuf4YiN4Y2jIOGIneGKleGInSDhiqDhiLjh"
    "ipPhjYog4Yqg4YiN4Ymw4YiY4YuY4YyI4Ymg4YidL+GKoOGIjeGJsOGIteGJsOGKq+GKqOGIiOGIneGNoiIpCiAgICBleGNlcHQg"
    "RXhjZXB0aW9uOgogICAgICAgIHBhc3MKCmFzeW5jIGRlZiBoYW5kbGVfdW5kb193aW5uZXIodXBkYXRlOiBVcGRhdGUsIGNvbnRl"
    "eHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4Yqg4Yi44YqT4Y2KIOGIjeGKrSDhiqjhibDhiJjhi5jhjIjh"
    "iaAg4Ymg4YqL4YiLIOGJoOGJs+GLqOGLjSDCq/CflJkgVW5kb8K7IOGJgeGIjeGNjSDhiLLhjKvhipEg4Yur4YqV4YqRIOGLqOGK"
    "oOGIuOGKk+GNiuGKkOGJtSDhiJ3hi53hjIjhiaMg4Yuo4Yia4Yur4Yyg4Y2LIGNhbGxiYWNrIC0KICAgIOGMiOGLouGLjSDhiqDh"
    "iLXhiYDhi7XhiJ4g4Yuo4Yuw4Yi14YmzIOGIm+GIs+GLiOGJguGLqyDhi7DhiK3hiLbhibUg4Yqo4YiG4YqQIOGLqyDhiqDhi63h"
    "iJjhiIjhiLXhiJ3hjaMg4YqQ4YyI4YitIOGMjeGKlSDhi6jhi43hiLXhjKUg4YiY4Yud4YyI4YmhIOGLreGMuOGLs+GIjS/hiabh"
    "ibPhi40g4Yut4YiI4YmA4YmD4YiN4Y2iIiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgaWYgcXVlcnku"
    "ZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgcmV0dXJuCiAgICBh"
    "d2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgdHJ5OgogICAgICAgIF8sIHJpZF9zdHIsIHRfc3RyID0gcXVlcnkuZGF0YS5zcGxpdCgi"
    "XyIsIDIpCiAgICAgICAgcm91bmRfaWQsIHRfbnVtID0gaW50KHJpZF9zdHIpLCBpbnQodF9zdHIpCiAgICBleGNlcHQgKFZhbHVl"
    "RXJyb3IsIEluZGV4RXJyb3IpOgogICAgICAgIHJldHVybgogICAgdHJ5OgogICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2Fn"
    "ZV9yZXBseV9tYXJrdXAocmVwbHlfbWFya3VwPU5vbmUpCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKICAgIHIg"
    "PSByb3VuZHMuZ2V0KHJvdW5kX2lkKQogICAgaWYgbm90IHI6CiAgICAgICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9tZXNzYWdl"
    "KGNoYXRfaWQ9QURNSU5fSUQsIHRleHQ9IuKEue+4jyDhi63hiIUg4YuZ4YitIOGKoOGIjeGJsOGMiOGKmOGIneGNoiIpCiAgICAg"
    "ICAgcmV0dXJuCiAgICB3aW5uZXJzID0gci5zZXRkZWZhdWx0KCJ3aW5uZXJzIiwgW10pCiAgICBiZWZvcmUgPSBsZW4od2lubmVy"
    "cykKICAgIHdpbm5lcnNbOl0gPSBbdyBmb3IgdyBpbiB3aW5uZXJzIGlmIHdbInRpY2tldF9udW0iXSAhPSB0X251bV0KICAgIGlm"
    "IGxlbih3aW5uZXJzKSA9PSBiZWZvcmU6CiAgICAgICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9tZXNzYWdlKGNoYXRfaWQ9QURN"
    "SU5fSUQsIHRleHQ9IuKEue+4jyDhi63hiIUg4Yqg4Yi44YqT4Y2K4YqQ4Ym1IOGKoOGIteGJgOGLteGIniDhjKDhjY3hibfhiI0v"
    "4Ymw4YqQ4Yi14Ym34YiN4Y2iIikKICAgICAgICByZXR1cm4KICAgIHNhdmVfc3RhdGUoKQogICAgYXdhaXQgY29udGV4dC5ib3Qu"
    "c2VuZF9tZXNzYWdlKAogICAgICAgIGNoYXRfaWQ9QURNSU5fSUQsCiAgICAgICAgdGV4dD1mIvCflJkg4YmB4Yyl4YitIHt0X251"
    "bX0gKHtyb3VuZF9sYWJlbChyb3VuZF9pZCl9KSDhiqDhiLjhipPhjYrhipDhibUg4Ymw4YqQ4Yi14Ym34YiNL+GJsOGIsOGIreGL"
    "n+GIjeGNoiIKICAgICkKCmFzeW5jIGRlZiBfc2VuZF9jaHVua2VkKHVwZGF0ZSwgbGluZXMpOgogICAgY2h1bmsgPSAiIgogICAg"
    "Zm9yIGxpbmUgaW4gbGluZXM6CiAgICAgICAgaWYgbGVuKGNodW5rKSArIGxlbihsaW5lKSArIDEgPiAzNTAwOgogICAgICAgICAg"
    "ICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KGNodW5rLCBwYXJzZV9tb2RlPSJIVE1MIikKICAgICAgICAgICAgY2h1"
    "bmsgPSAiIgogICAgICAgIGNodW5rICs9IGxpbmUgKyAiXG4iCiAgICBpZiBjaHVuazoKICAgICAgICBhd2FpdCB1cGRhdGUubWVz"
    "c2FnZS5yZXBseV90ZXh0KGNodW5rLCBwYXJzZV9tb2RlPSJIVE1MIikKCmFzeW5jIGRlZiBsaXN0X3B1cmNoYXNlZCh1cGRhdGU6"
    "IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhiJvhipXhipvhi43hiJ0g4Ymw4Yyr"
    "4YuL4Ym9IOGLqOGJsOGIuOGMoSDhibLhiqzhibbhib3hipUg4Yml4Ym7IOGLqOGImuGLq+GLreGJoOGJtSDhibXhi5Xhi5vhi50g"
    "KOGLqOGMiOGLouGLjeGKlSDhiLXhiI3hiq0g4Ymg4Yqo4Y2K4YiNIOGJsOGIuOGNjeGKliDhi6vhiLPhi6vhiI0pCiAgICDhiqDh"
    "jKDhiYPhiYDhiJ3hjaYgL3B1cmNoYXNlZCA84YuZ4YitPiAo4Yqr4YiN4YyI4YiI4Yy5IOGKpeGKkyDhiqDhipXhi7Ug4YqV4YmB"
    "IOGLmeGIrSDhiaXhibsg4Yqr4YiIIOGJoOGIq+GItS3hiLDhiK0g4Yut4YiY4Yit4Yyj4YiNKSIiIgogICAgaWYgYXdhaXQgX2Js"
    "b2NrX3dpdGhfcGVuZGluZ19zZWxlY3Rpb25fbm90aWNlKHVwZGF0ZSwgdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkKToKICAg"
    "ICAgICByZXR1cm4KCiAgICByb3VuZF9pZCA9IGF3YWl0IF9yZXNvbHZlX3JvdW5kX2Zvcl9wbGF5ZXIodXBkYXRlLCBjb250ZXh0"
    "LmFyZ3MsIGFjdGlvbl9rZXk9InB1cmNoYXNlZCIsIHJlcXVpcmVfb3Blbj1GYWxzZSkKICAgIGlmIHJvdW5kX2lkIGlzIE5vbmU6"
    "CiAgICAgICAgcmV0dXJuCgogICAgc29sZCA9IHtpOiB0IGZvciBpLCB0IGluIHJvdW5kc1tyb3VuZF9pZF1bInRpY2tldHMiXS5p"
    "dGVtcygpIGlmIHRbInN0YXR1cyJdID09ICJTT0xEIn0KCiAgICBpZiBub3Qgc29sZDoKICAgICAgICBhd2FpdCB1cGRhdGUubWVz"
    "c2FnZS5yZXBseV90ZXh0KGYi4oS577iPIOGJoOGLmeGIrSB7cm91bmRfaWR9IOGKpeGIteGKq+GIgeGKlSDhiJ3hipXhiJ0g4Yuo"
    "4Ymw4Yi44YygIOGJgeGMpeGIrSDhi6jhiIjhiJ3hjaIiKQogICAgICAgIHJldHVybgoKICAgIGxpbmVzID0gW2Yi8J+UtCA8Yj7h"
    "i6jhibDhiLjhjKEg4Ymy4Yqs4Ym24Ym9IC0g4YuZ4YitIHtyb3VuZF9pZH08L2I+XG4iXQogICAgZm9yIGkgaW4gc29ydGVkKHNv"
    "bGQpOgogICAgICAgIHQgPSBzb2xkW2ldCiAgICAgICAgaWYgdC5nZXQoInNoYXJlZCIpOgogICAgICAgICAgICBtYXNrZWQyID0g"
    "bWFza19waG9uZSh0LmdldCgiYnV5ZXJfcGhvbmUyIikpCiAgICAgICAgICAgIG1hc2tlZCA9IG1hc2tfcGhvbmUodC5nZXQoImJ1"
    "eWVyX3Bob25lIikpCiAgICAgICAgICAgIGxpbmVzLmFwcGVuZChmIvCfn6MgI3tpfSAtIHttYXNrZWR9ICYge21hc2tlZDJ9ICjh"
    "iIgyIOGIsOGLjSDhjI3hiJvhiL0g4YuL4YyLKSIpCiAgICAgICAgZWxzZToKICAgICAgICAgICAgbWFza2VkID0gbWFza19waG9u"
    "ZSh0LmdldCgiYnV5ZXJfcGhvbmUiKSkKICAgICAgICAgICAgbGluZXMuYXBwZW5kKGYi8J+UtCAje2l9IC0ge21hc2tlZH0iKQoK"
    "ICAgIGF3YWl0IF9zZW5kX2NodW5rZWQodXBkYXRlLCBsaW5lcykKCmFzeW5jIGRlZiBsaXN0X2F2YWlsYWJsZSh1cGRhdGU6IFVw"
    "ZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhiJvhipXhipvhi43hiJ0g4Ymw4Yyr4YuL"
    "4Ym9IOGLq+GIjeGJsOGLq+GLmSDhi6vhiIkg4Ymy4Yqs4Ym24Ym94YqVIOGJpeGJuyDhi6jhiJrhi6vhi63hiaDhibUg4Ym14YuV"
    "4Yub4YudCiAgICDhiqDhjKDhiYPhiYDhiJ3hjaYgL2F2YWlsYWJsZSA84YuZ4YitPiAo4Yqr4YiN4YyI4YiI4Yy5IOGKpeGKkyDh"
    "iqDhipXhi7Ug4YqV4YmBIOGLmeGIrSDhiaXhibsg4Yqr4YiIIOGJoOGIq+GItS3hiLDhiK0g4Yut4YiY4Yit4Yyj4YiNKSIiIgog"
    "ICAgaWYgYXdhaXQgX2Jsb2NrX3dpdGhfcGVuZGluZ19zZWxlY3Rpb25fbm90aWNlKHVwZGF0ZSwgdXBkYXRlLm1lc3NhZ2UuZnJv"
    "bV91c2VyLmlkKToKICAgICAgICByZXR1cm4KCiAgICByb3VuZF9pZCA9IGF3YWl0IF9yZXNvbHZlX3JvdW5kX2Zvcl9wbGF5ZXIo"
    "dXBkYXRlLCBjb250ZXh0LmFyZ3MsIGFjdGlvbl9rZXk9ImF2YWlsYWJsZSIpCiAgICBpZiByb3VuZF9pZCBpcyBOb25lOgogICAg"
    "ICAgIHJldHVybgoKICAgIGF2YWlsYWJsZSA9IFtpIGZvciBpLCB0IGluIHJvdW5kc1tyb3VuZF9pZF1bInRpY2tldHMiXS5pdGVt"
    "cygpIGlmIHRbInN0YXR1cyJdID09ICJBVkFJTEFCTEUiXQoKICAgIGlmIG5vdCBhdmFpbGFibGU6CiAgICAgICAgYXdhaXQgdXBk"
    "YXRlLm1lc3NhZ2UucmVwbHlfdGV4dChmIuKEue+4jyDhiaDhi5nhiK0ge3JvdW5kX2lkfSDhiJ3hipXhiJ0g4Yur4YiN4Ymw4Yur"
    "4YuZIOGJgeGMpeGIrSDhiqDhiI3hiYDhiKjhiJ3hjaIiKQogICAgICAgIHJldHVybgoKICAgIGxpbmVzID0gW2Yi8J+foiA8Yj7h"
    "i6vhiI3hibDhi6vhi5kg4Ymy4Yqs4Ym24Ym9IC0g4YuZ4YitIHtyb3VuZF9pZH08L2I+XG4iXQogICAgZm9yIGkgaW4gc29ydGVk"
    "KGF2YWlsYWJsZSk6CiAgICAgICAgbGluZXMuYXBwZW5kKGYi8J+foiAje2l9IikKCiAgICBhd2FpdCBfc2VuZF9jaHVua2VkKHVw"
    "ZGF0ZSwgbGluZXMpCgphc3luYyBkZWYgdXNlZF9yZWZzX2NvbW1hbmQodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRU"
    "eXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4Yqg4Yu14Yia4YqVIOGKpeGIteGKq+GIgeGKlSDhjKXhiYXhiJ0g4YiL4YutIOGL"
    "qOGLi+GIiSDhiIHhiInhipXhiJ0g4Yuo4Yqt4Y2N4YurIOGIquGNiOGIqOGKleGItuGJvSAoYXVkaXQgdHJhaWwpIOGLqOGImuGL"
    "q+GLreGJoOGJtSDhibXhi5Xhi5vhi50gLSDhiaDhiq3hjY3hi6sg4YuY4Yu0IChUZWxlQmlyci9DQkUgQmlycikg4Ymw4Yyj4Yit"
    "4Ym2CiAgICDhiqDhjKDhiYPhiYDhiJ3hjaYgL3VzZWRyZWZzIiIiCiAgICBpZiB1cGRhdGUubWVzc2FnZS5mcm9tX3VzZXIuaWQg"
    "IT0gQURNSU5fSUQ6CiAgICAgICAgcmV0dXJuCgogICAgaWYgbm90IHVzZWRfc21zX3JlZnM6CiAgICAgICAgYXdhaXQgdXBkYXRl"
    "Lm1lc3NhZ2UucmVwbHlfdGV4dCgi4oS577iPIOGKpeGIteGKq+GIgeGKlSDhiJ3hipXhiJ0g4Yyl4YmF4YidIOGIi+GLrSDhi6jh"
    "i4vhiIgg4Yiq4Y2I4Yio4YqV4Yi1IOGLqOGIiOGIneGNoiIpCiAgICAgICAgcmV0dXJuCgogICAga2IgPSBbCiAgICAgICAgW0lu"
    "bGluZUtleWJvYXJkQnV0dG9uKG1bImxhYmVsIl0sIGNhbGxiYWNrX2RhdGE9ZiJ1c2VkcmVmc197a2V5fSIpXQogICAgICAgIGZv"
    "ciBrZXksIG0gaW4gUEFZTUVOVF9NRVRIT0RTLml0ZW1zKCkKICAgIF0KICAgIGtiLmFwcGVuZChbSW5saW5lS2V5Ym9hcmRCdXR0"
    "b24oIvCfk4sg4YiB4YiJ4YqV4YidIOGKoOGIs+GLrSAoQWxsKSIsIGNhbGxiYWNrX2RhdGE9InVzZWRyZWZzX2FsbCIpXSkKICAg"
    "IGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoCiAgICAgICAgIvCfp74g4YiI4Yuo4Ym14Yqb4YuNIOGLqOGKreGNjeGL"
    "qyDhi5jhi7Qg4Yyl4YmF4YidIOGIi+GLrSDhi6jhi4vhiIkg4Yiq4Y2I4Yio4YqV4Yi24Ym94YqVIOGIm+GLqOGJtSDhi63hjYjh"
    "iI3hjIvhiIk/IiwKICAgICAgICByZXBseV9tYXJrdXA9SW5saW5lS2V5Ym9hcmRNYXJrdXAoa2IpLAogICAgKQoKZGVmIF9yZWpl"
    "Y3RlZF9yZWZfY2FyZChrZXksIGUpOgogICAgIiIi4YiIIC9yZWplY3RlZHJlZnMg4Ym14YuV4Yub4YudIOGKpeGKkyDhiIggYXV0"
    "by1yZWplY3Qg4YmF4Yy94Ymg4Ymz4YuKIOGIm+GIs+GLiOGJguGLqyDhi6jhiJrhi6vhjIjhiIjhjI3hiI0g4Yuo4YyL4YirIChz"
    "aGFyZWQpIOGKq+GIreGLtQogICAg4YyI4YqV4YmiIC0g4YiB4YiI4Ymx4YidIOGJpuGJsyDhibDhiJjhiLPhiLPhi60g4Yy94YiB"
    "4Y2NL+GJgeGIjeGNjuGJvSDhiqXhipXhi7LhipbhiKvhibjhi40gKGFwcHJvdmUvcmVqZWN0IGNhbGxiYWNrX2RhdGEg4Yyo4Yid"
    "4YiuKeGNogogICAg4Yut4YiY4YiN4Yiz4YiN4Y2mICh0ZXh0LCBJbmxpbmVLZXlib2FyZE1hcmt1cCkiIiIKICAgIG51bXMgPSAi"
    "LCAiLmpvaW4oc3RyKHgpIGZvciB4IGluIGVbInRpY2tldHMiXSkKICAgIHRleHQgPSAoCiAgICAgICAgZiLwn5qrIDxiPuGLjeGL"
    "teGJhSDhi6jhibDhi7DhiKjhjIgg4Yiq4Y2I4Yio4YqV4Yi1PC9iPlxuXG4iCiAgICAgICAgZiLwn5OdIOGIquGNiOGIqOGKleGI"
    "tTogPGNvZGU+e2VbJ3JlZiddfTwvY29kZT5cbiIKICAgICAgICBmIvCflKIg4YmB4Yyl4YitKOGLjuGJvSk6IHtudW1zfSAo4YuZ"
    "4YitIHtlWydyb3VuZF9pZCddfSlcbiIKICAgICAgICBmIvCfhpQg4Ymw4Yyr4YuL4Ym9IElEOiB7ZVsndXNlcl9pZCddfSIKICAg"
    "ICkKICAgIGtiID0gSW5saW5lS2V5Ym9hcmRNYXJrdXAoW1sKICAgICAgICBJbmxpbmVLZXlib2FyZEJ1dHRvbigi4pyFIOGKoOGM"
    "veGLteGJhSAoQXBwcm92ZSkiLCBjYWxsYmFja19kYXRhPWYicmVqZWN0ZWRyZWZhcHByb3ZlX3trZXl9IiksCiAgICAgICAgSW5s"
    "aW5lS2V5Ym9hcmRCdXR0b24oIuKdjCDhi43hi7XhiYUgKFJlamVjdCkiLCBjYWxsYmFja19kYXRhPWYicmVqZWN0ZWRyZWZyZWpl"
    "Y3Rfe2tleX0iKSwKICAgIF1dKQogICAgcmV0dXJuIHRleHQsIGtiCgoKYXN5bmMgZGVmIF9ub3RpZnlfYWRtaW5fcmVqZWN0ZWRf"
    "cmVmKGJvdCwga2V5LCBlKToKICAgICIiImF1dG8tcmVqZWN0IOGJoOGJsOGNiOGMoOGIqCDhiYXhjL3hiaDhibUg4YuI4Yuy4Yur"
    "4YuN4YqRIOGIiCBBRE1JTl9JRCAo4Yut4YiFIOGIhuGIteGJtSDhiKvhiLEpIOGIm+GIs+GLiOGJguGLqyDhi63hiI3hiqvhiI0g"
    "LSDhiqjCq+KchSDhiqDhjL3hi7XhiYXCuy/Cq+KdjCDhi43hi7XhiYXCuwogICAg4YmB4YiN4Y2O4Ym9IOGMi+GIrSDhiaDhiYDh"
    "jKXhibMg4Yqo4Yua4Yur4YuNIOGImOGIjeGLleGKreGJtSDhiIvhi60g4YiY4YuI4Yiw4YqVIOGKpeGKleGLsuGJveGIjeGNoyAv"
    "cmVqZWN0ZWRyZWZzIOGJpeGIiOGLjSDhiJjhjYjhiIjhjI0g4Yiz4Yur4Yi14Y2I4YiN4YyL4Ym44YuN4Y2iCiAgICDhiJjhiIvh"
    "iq0g4Ymi4Yur4YiN4Y2NICjhiIjhiJ3hiLPhiIwg4YiG4Yi14YmxIOGJpuGJseGKlSDhiqDhjI3hi7bhibUg4Yqo4YiG4YqQKSDh"
    "i53hiJ0g4Yml4YiOIOGLq+GIjeGNi+GIjSAtIGVudHJ5IOGMjeGKlSByZWplY3RlZF9yZWZzLwogICAgL3JlamVjdGVkcmVmcyDh"
    "i43hiLXhjKUg4Yqg4YiB4YqV4YidIOGLreGJgOGIq+GIjeGNoyDhiLXhiIjhi5rhiIUg4Yid4YqV4YidIOGImOGIqOGMgyDhiqDh"
    "i63hjKDhjYvhiJ3hjaIiIiIKICAgIHRleHQsIGtiID0gX3JlamVjdGVkX3JlZl9jYXJkKGtleSwgZSkKICAgIHRyeToKICAgICAg"
    "ICBhd2FpdCBib3Quc2VuZF9tZXNzYWdlKGNoYXRfaWQ9QURNSU5fSUQsIHRleHQ9dGV4dCwgcGFyc2VfbW9kZT0iSFRNTCIsIHJl"
    "cGx5X21hcmt1cD1rYikKICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZXg6CiAgICAgICAgbG9nLndhcm5pbmcoZiJyZWplY3RlZC1y"
    "ZWYgbm90aWZpY2F0aW9uIHRvIEFETUlOX0lEPXtBRE1JTl9JRH0g4Yqg4YiN4Ymw4YiL4Yqo4Yid4Y2mIHtleH0iKQoKCmFzeW5j"
    "IGRlZiByZWplY3RlZF9yZWZzX2NvbW1hbmQodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZ"
    "UEUpOgogICAgIiIiYXV0by1yZWplY3Qg4Yuo4Ymw4Yuw4Yio4YyJICjhjIjhjKPhjKPhiJogU01TIOGMiOGKkyDhi6vhiI3hi7Dh"
    "iKjhiLApIOGIquGNiOGIqOGKleGItuGJveGKlSDhiKrhjYjhiKjhipXhiLUgKyDhi6jhibDhiJjhiKjhjKAg4YmB4Yyl4YitICsg"
    "4Ymw4Yyr4YuL4Ym9IElEIOGJpeGJuwogICAg4Yqg4Yiz4Yut4Ym2IOGKqMKr4pyFIOGKoOGMveGLteGJhcK7IOGJgeGIjeGNjSDh"
    "jIvhiK0g4Ymg4Yqg4YqV4Yu1IOGMq+GImuGJtSDhiaDhiqXhjIUg4Yib4Yy94Yuw4YmFIOGLqOGImuGLq+GIteGJveGIjSDhibXh"
    "i5Xhi5vhi50KICAgIOGKoOGMoOGJg+GJgOGIneGNpiAvcmVqZWN0ZWRyZWZzIiIiCiAgICBpZiB1cGRhdGUubWVzc2FnZS5mcm9t"
    "X3VzZXIuaWQgIT0gQURNSU5fSUQ6CiAgICAgICAgcmV0dXJuCgogICAgaWYgbm90IHJlamVjdGVkX3JlZnM6CiAgICAgICAgYXdh"
    "aXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4oS577iPIOGJoOGKoOGIgeGKkSDhiLDhi5PhibUg4Yid4YqV4YidIOGLjeGL"
    "teGJhSDhi6jhibDhi7DhiKjhjIggKHJlamVjdGVkKSDhiKrhjYjhiKjhipXhiLUg4Yuo4YiI4Yid4Y2iIikKICAgICAgICByZXR1"
    "cm4KCiAgICBmb3Iga2V5LCBlIGluIHNvcnRlZChyZWplY3RlZF9yZWZzLml0ZW1zKCksIGtleT1sYW1iZGEga3Y6IGt2WzFdLmdl"
    "dCgicmVqZWN0ZWRfYXQiKSBvciBkYXRldGltZS5taW4pOgogICAgICAgIHRleHQsIGtiID0gX3JlamVjdGVkX3JlZl9jYXJkKGtl"
    "eSwgZSkKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KHRleHQsIHBhcnNlX21vZGU9IkhUTUwiLCByZXBs"
    "eV9tYXJrdXA9a2IpCgoKYXN5bmMgZGVmIGhhbmRsZV9yZWplY3RlZF9yZWZfYXBwcm92ZSh1cGRhdGU6IFVwZGF0ZSwgY29udGV4"
    "dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiIvcmVqZWN0ZWRyZWZzIOGLjeGIteGMpSDhiqvhiIjhi40g4Yud"
    "4Yit4Yud4YitIOGLjeGIteGMpSDCq+KchSDhiqDhjL3hi7XhiYXCuyDhiYHhiI3hjY0g4Yiy4Yyr4YqRIOGLqOGImuGIsOGIqyBj"
    "YWxsYmFjayAtIOGJsOGMk+GLs+GKmeGKlSDhibLhiqzhibUo4Ym24Ym9KSDhiaDhiqXhjIUg4Yur4Yy44Yu14YmD4YiNIiIiCiAg"
    "ICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgaWYgcXVlcnkuZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgogICAg"
    "ICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgcmV0dXJuCiAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQoKICAgIGtleSA9"
    "IHF1ZXJ5LmRhdGFbbGVuKCJyZWplY3RlZHJlZmFwcHJvdmVfIik6XQogICAgZW50cnkgPSByZWplY3RlZF9yZWZzLmdldChrZXkp"
    "CiAgICBpZiBub3QgZW50cnk6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfdGV4dCgi"
    "4pqg77iPIOGLreGIhSDhjI3hiaThibUg4Yqo4Yua4YiFIOGJoOGNiuGJtSDhibDhiLXhibDhipPhjI3hi7fhiI0g4YuI4Yut4Yid"
    "IOGMoOGNjeGJt+GIjeGNoiIpCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgcGFzcwogICAgICAgIHJldHVy"
    "bgoKICAgIHJvdW5kX2lkID0gZW50cnlbInJvdW5kX2lkIl0KICAgIHRpY2tldHMgPSBlbnRyeVsidGlja2V0cyJdCiAgICBsaXZl"
    "X3RpY2tldHMgPSBbCiAgICAgICAgdG4gZm9yIHRuIGluIHRpY2tldHMKICAgICAgICBpZiByb3VuZHMuZ2V0KHJvdW5kX2lkLCB7"
    "fSkuZ2V0KCJ0aWNrZXRzIiwge30pLmdldCh0biwge30pLmdldCgic3RhdHVzIikgPT0gIlBFTkRJTkciCiAgICBdCiAgICBpZiBu"
    "b3QgbGl2ZV90aWNrZXRzOgogICAgICAgIHJlamVjdGVkX3JlZnMucG9wKGtleSwgTm9uZSkKICAgICAgICBzYXZlX3N0YXRlKCkK"
    "ICAgICAgICB0cnk6CiAgICAgICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KCLimqDvuI8g4Yut4YiFIOGJteGL"
    "leGLm+GLnSDhiqjhi5rhiIUg4Ymg4Y2K4Ym1IOGJsOGIteGJsOGKk+GMjeGLt+GIjSDhi4jhi63hiJ0g4YyK4Yuc4YuNIOGKoOGI"
    "jeGNjuGJoOGJs+GIjeGNoiIpCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgcGFzcwogICAgICAgIHJldHVy"
    "bgoKICAgIG5vcm1fcmVmID0gX25vcm1hbGl6ZV9yZWYoZW50cnlbInJlZiJdKQogICAgaWYgbGVuKGxpdmVfdGlja2V0cykgPT0g"
    "MToKICAgICAgICBpZiBub3JtX3JlZjoKICAgICAgICAgICAgX21hcmtfcmVmX3VzZWQobm9ybV9yZWYsIGVudHJ5WyJyZWYiXSwg"
    "cm91bmRfaWQsIGxpdmVfdGlja2V0c1swXSkKICAgICAgICBhd2FpdCBhcHByb3ZlX3RpY2tldChyb3VuZF9pZCwgbGl2ZV90aWNr"
    "ZXRzWzBdLCBjb250ZXh0LmJvdCkKICAgIGVsc2U6CiAgICAgICAgaWYgbm9ybV9yZWY6CiAgICAgICAgICAgIF9tYXJrX3JlZl91"
    "c2VkX2dyb3VwKG5vcm1fcmVmLCBlbnRyeVsicmVmIl0sIHJvdW5kX2lkLCBsaXZlX3RpY2tldHMpCiAgICAgICAgYXdhaXQgYXBw"
    "cm92ZV90aWNrZXRfZ3JvdXAocm91bmRfaWQsIGxpdmVfdGlja2V0cywgY29udGV4dC5ib3QpCgogICAgdHJ5OgogICAgICAgIGF3"
    "YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KAogICAgICAgICAgICBmIuKchSDhiYHhjKXhiK0o4YuO4Ym9KSB7JywgJy5qb2lu"
    "KHN0cih4KSBmb3IgeCBpbiBsaXZlX3RpY2tldHMpfSAo4YuZ4YitIHtyb3VuZF9pZH0pIOGJoOGKpeGMhSDhjLjhi7XhiYvhiI3h"
    "jaIgKOGIquGNiOGIqOGKleGIteGNpiB7ZW50cnlbJ3JlZiddfSkiCiAgICAgICAgKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAg"
    "ICAgICBwYXNzCgphc3luYyBkZWYgaGFuZGxlX3JlamVjdGVkX3JlZl9yZWplY3QodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENv"
    "bnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIiL3JlamVjdGVkcmVmcyDhi43hiLXhjKUg4Yqr4YiI4YuNIOGLneGIreGL"
    "neGIrSDhi43hiLXhjKUgwqvinYwg4YuN4Yu14YmFwrsg4YmB4YiN4Y2NIOGIsuGMq+GKkSDhi6jhiJrhiLDhiKsgY2FsbGJhY2sg"
    "LSDhibDhjJPhi7PhipnhipUg4Ymy4Yqs4Ym1KOGJtuGJvSkKICAgIOGJoOGKpeGMhSDhi43hi7XhiYUg4Yqg4Yu14Yit4YyOIOGL"
    "reGIiOGJg+GIjSAo4Ymw4Yyr4YuL4Ym5IOGLsOGMjeGIniDhi43hi7XhiYUg4YiY4Yuw4Yio4YyJ4YqVIOGLreGKkOGMiOGIqOGL"
    "i+GIjSnhjaIiIiIKICAgIHF1ZXJ5ID0gdXBkYXRlLmNhbGxiYWNrX3F1ZXJ5CiAgICBpZiBxdWVyeS5mcm9tX3VzZXIuaWQgIT0g"
    "QURNSU5fSUQ6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgICAgICByZXR1cm4KICAgIGF3YWl0IHF1ZXJ5LmFuc3dl"
    "cigpCgogICAga2V5ID0gcXVlcnkuZGF0YVtsZW4oInJlamVjdGVkcmVmcmVqZWN0XyIpOl0KICAgIGVudHJ5ID0gcmVqZWN0ZWRf"
    "cmVmcy5nZXQoa2V5KQogICAgaWYgbm90IGVudHJ5OgogICAgICAgIHRyeToKICAgICAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9t"
    "ZXNzYWdlX3RleHQoIuKaoO+4jyDhi63hiIUg4YyN4Ymk4Ym1IOGKqOGLmuGIhSDhiaDhjYrhibUg4Ymw4Yi14Ymw4YqT4YyN4Yu3"
    "4YiNIOGLiOGLreGInSDhjKDhjY3hibfhiI3hjaIiKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgIHBhc3MK"
    "ICAgICAgICByZXR1cm4KCiAgICByb3VuZF9pZCA9IGVudHJ5WyJyb3VuZF9pZCJdCiAgICB0aWNrZXRzID0gZW50cnlbInRpY2tl"
    "dHMiXQogICAgbGl2ZV90aWNrZXRzID0gWwogICAgICAgIHRuIGZvciB0biBpbiB0aWNrZXRzCiAgICAgICAgaWYgcm91bmRzLmdl"
    "dChyb3VuZF9pZCwge30pLmdldCgidGlja2V0cyIsIHt9KS5nZXQodG4sIHt9KS5nZXQoInN0YXR1cyIpID09ICJQRU5ESU5HIgog"
    "ICAgXQogICAgaWYgbm90IGxpdmVfdGlja2V0czoKICAgICAgICByZWplY3RlZF9yZWZzLnBvcChrZXksIE5vbmUpCiAgICAgICAg"
    "c2F2ZV9zdGF0ZSgpCiAgICAgICAgdHJ5OgogICAgICAgICAgICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfdGV4dCgi4pqg77iP"
    "IOGLreGIhSDhibXhi5Xhi5vhi50g4Yqo4Yua4YiFIOGJoOGNiuGJtSDhibDhiLXhibDhipPhjI3hi7fhiI0g4YuI4Yut4YidIOGM"
    "iuGLnOGLjSDhiqDhiI3hjY7hiaDhibPhiI3hjaIiKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgIHBhc3MK"
    "ICAgICAgICByZXR1cm4KCiAgICBhd2FpdCByZWplY3RfdGlja2V0X2dyb3VwKHJvdW5kX2lkLCBsaXZlX3RpY2tldHMsIGNvbnRl"
    "eHQuYm90KQogICAgIyByZWplY3RfdGlja2V0X2dyb3VwIOGJgOGLteGInuGLjeGKkSBfY2xlYXJfcmVqZWN0ZWRfcmVmX2VudHJp"
    "ZXMg4Yut4Yyg4Yir4YiNIChyZWplY3RlZF9yZWZzW2tleV0g4YqVIOGMqOGIneGIriDhi6vhjKDhjYvhiI0pCiAgICAjIOGIm+GI"
    "teGJs+GLiOGIu+GNpiDhi43hi7XhiYUt4YqQ4YqtIOGIm+GIs+GLiOGJguGLq+GLjuGJvSDhiIHhiIkg4YiI4Yqg4Yu14Yia4YqR"
    "IOGJsOGIsOGLjeGIqOGLi+GIjSAtIOGIm+GIqOGMi+GMiOGMqyDhiqjhiJjhiIvhiq0g4Yut4YiN4YmFIOGIq+GIsSDhiJjhiI3h"
    "i5Xhiq3hibHhipUg4Yql4YqT4Yyg4Y2L4YuL4YiI4YqV4Y2iCiAgICB0cnk6CiAgICAgICAgYXdhaXQgcXVlcnkubWVzc2FnZS5k"
    "ZWxldGUoKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICBwYXNzCgphc3luYyBkZWYgaGFuZGxlX2FkbWluX3JvdW5kX3Bp"
    "Y2sodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4Yqg4Yu14Yia4YqV"
    "IOGKreGIreGKreGIrSDhiLPhi63hiLDhjKUgL2Nsb3Nlcm91bmQsIC9wYXVzZXJvdW5kLCAvcmVzdW1lcm91bmQsIC9yZXN0YXJ0"
    "cm91bmQsIC9kZWxldGVyb3VuZCwKICAgIC9zb2xkLCAvbm90aWZ5ZHJhdyDhiLLhiI3hiq0g4Yqo4Ymz4Yuo4YuNIOGLmeGIrS3h"
    "iJjhiJ3hiKjhjKsg4YmB4YiN4Y2NIChidXR0b24pIOGIi+GLrSDhiLLhiJjhiK3hjKUg4Yuo4Yia4Yiw4YirIGNhbGxiYWNrIiIi"
    "CiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgaWYgcXVlcnkuZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgog"
    "ICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgcmV0dXJuCiAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgdHJ5"
    "OgogICAgICAgIF8sIGFjdGlvbl9rZXksIHJpZF9zdHIgPSBxdWVyeS5kYXRhLnNwbGl0KCJfIiwgMikKICAgIGV4Y2VwdCBWYWx1"
    "ZUVycm9yOgogICAgICAgIHJldHVybgogICAgZnVuYyA9IHsKICAgICAgICAiY2xvc2UiOiBjbG9zZV9yb3VuZCwKICAgICAgICAi"
    "cGF1c2UiOiBwYXVzZV9yb3VuZCwKICAgICAgICAicmVzdW1lIjogcmVzdW1lX3JvdW5kLAogICAgICAgICJyZXN0YXJ0IjogcmVz"
    "dGFydF9yb3VuZCwKICAgICAgICAiZGVsZXRlIjogZGVsZXRlX3JvdW5kLAogICAgICAgICJzb2xkIjogc29sZF9saXN0LAogICAg"
    "ICAgICJhbGx0aWNrZXRzIjogYWxsX3RpY2tldHNfY29tbWFuZCwKICAgICAgICAidW5zb2xkIjogdW5zb2xkX2xpc3QsCiAgICAg"
    "ICAgIm5vdGlmeWRyYXciOiBub3RpZnlfZHJhdywKICAgICAgICAic2V0d2lubmVyIjogc2V0X3dpbm5lcl9jb21tYW5kLAogICAg"
    "fS5nZXQoYWN0aW9uX2tleSkKCiAgICAjIOGKpeGKkOGLmuGIhSAyIOGKpeGIreGIneGMg+GLjuGJvSDhibDhiJjhiIvhiL0g4Yuo"
    "4Yib4Yut4YiG4YqRL+GMoOGKleGKq+GIqyDhibDhjL3hi5XhipYg4Yi14YiL4YiL4Ym44YuNICjhi5nhiK0g4YiY4Yud4YyL4Ym1"
    "IOGJsOGMq+GLi+GJvuGJveGKlSDhi6vhjI3hi7PhiI3hjaMg4Yuz4YyN4YidIOGImOGMgOGImOGIrSDhi6jhibDhiLjhjKEKICAg"
    "ICMg4Ymy4Yqs4Ym24Ym94YqVIOGLq+GMoOGNi+GIjSkg4Ymg4YmA4Yyl4YmzIOGKqOGImOGNiOGMuOGInSDhi63hiI3hiYUg4YiY"
    "4YyA4YiY4Yiq4YurIMKr4pyFIOGKoOGIqOGMi+GMjeGMpSAvIOKdjCDhibDhi4jhi43CuyDhiJvhiKjhjIvhjIjhjKsg4Yut4Yyg"
    "4Yut4YmD4YiJ4Y2iCiAgICBDT05GSVJNX1JFUVVJUkVEX0FDVElPTlMgPSB7CiAgICAgICAgImNsb3NlIjogIvCflJIg4Yut4YiF"
    "4YqVIOGLmeGIrSDhiJjhi53hjIvhibUg4Yql4Yit4YyN4Yyg4YqbIOGKkOGLjuGJtT8g4Yqo4Ymw4YuY4YyLIOGJoOGKi+GIiyDh"
    "ibDhjKvhi4vhib7hib0g4YuI4YuwIOGKpeGIsSDhiJjhjI3hi5vhibUg4Yqg4Yut4Ym94YiJ4Yid4Y2iIiwKICAgICAgICAicmVz"
    "dGFydCI6ICLwn5SEIOGLreGIheGKlSDhi5nhiK0g4Yuz4YyN4YidIOGImOGMgOGImOGIrSDhiqXhiK3hjI3hjKDhipsg4YqQ4YuO"
    "4Ym1PyDhi6jhibDhiLjhjKEg4Ymy4Yqs4Ym24Ym9IOGKq+GIiSDhi63hiLDhiKjhi5vhiIkg4Yql4YqTIOGIgeGIieGInSDhiYHh"
    "jKXhiK7hib0g4YuI4YuwIEFWQUlMQUJMRSDhi63hiJjhiIjhiLPhiIkgKOGLiOGLsCDhiovhiIsg4Yqg4Yut4YiY4YiI4Yi14Yid"
    "KeGNoiIsCiAgICB9CiAgICBpZiBhY3Rpb25fa2V5IGluIENPTkZJUk1fUkVRVUlSRURfQUNUSU9OUzoKICAgICAgICByaWQgPSBp"
    "bnQocmlkX3N0cikKICAgICAgICBrYiA9IElubGluZUtleWJvYXJkTWFya3VwKFtbCiAgICAgICAgICAgIElubGluZUtleWJvYXJk"
    "QnV0dG9uKCLinIUg4Yqg4YuO4Y2jIOGKpeGIreGMjeGMoOGKmyDhipDhip0iLCBjYWxsYmFja19kYXRhPWYiYWRtaW5jb25maXJt"
    "X3thY3Rpb25fa2V5fV97cmlkfSIpLAogICAgICAgICAgICBJbmxpbmVLZXlib2FyZEJ1dHRvbigi4p2MIOGKoOGLreGNoyDhibDh"
    "i4jhi40iLCBjYWxsYmFja19kYXRhPWYiYWRtaW5jYW5jZWxfe2FjdGlvbl9rZXl9X3tyaWR9IiksCiAgICAgICAgXV0pCiAgICAg"
    "ICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9tZXNzYWdlKAogICAgICAgICAgICBjaGF0X2lkPUFETUlOX0lELAogICAgICAgICAg"
    "ICB0ZXh0PWYi4pqg77iPIHtyb3VuZF9sYWJlbChyaWQpfVxuXG57Q09ORklSTV9SRVFVSVJFRF9BQ1RJT05TW2FjdGlvbl9rZXld"
    "fSIsCiAgICAgICAgICAgIHJlcGx5X21hcmt1cD1rYiwKICAgICAgICApCiAgICAgICAgcmV0dXJuCgogICAgaWYgYWN0aW9uX2tl"
    "eSA9PSAiY2FuY2Vscm91bmQiOgogICAgICAgIHJpZCA9IGludChyaWRfc3RyKQogICAgICAgIGF3YWl0IF9zdGFydF9tYW51YWxj"
    "YW5jZWxfdGlja2V0X3BpY2tlcihjb250ZXh0LCByaWQpCiAgICAgICAgcmV0dXJuCgogICAgaWYgYWN0aW9uX2tleSA9PSAibWFu"
    "dWFsc2VsbHJvdW5kIjoKICAgICAgICByaWQgPSBpbnQocmlkX3N0cikKICAgICAgICBhd2FpdCBfc3RhcnRfbWFudWFsc2VsbF90"
    "aWNrZXRfcGlja2VyKGNvbnRleHQsIHJpZCkKICAgICAgICByZXR1cm4KCiAgICBpZiBub3QgZnVuYzoKICAgICAgICByZXR1cm4K"
    "ICAgIGZha2VfdXBkYXRlID0gX0Zha2VVcGRhdGUoX0Zha2VNc2cocXVlcnkubWVzc2FnZSwgcXVlcnkuZnJvbV91c2VyKSkKICAg"
    "IGZha2VfY29udGV4dCA9IFNpbXBsZU5hbWVzcGFjZShhcmdzPVtyaWRfc3RyXSwgYm90PWNvbnRleHQuYm90KQogICAgYXdhaXQg"
    "ZnVuYyhmYWtlX3VwZGF0ZSwgZmFrZV9jb250ZXh0KQoKYXN5bmMgZGVmIGhhbmRsZV9hZG1pbl9hY3Rpb25fY29uZmlybSh1cGRh"
    "dGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiIvY2xvc2Vyb3VuZCwgL3Jlc3Rh"
    "cnRyb3VuZCDhiIvhi60g4Yuo4Ymz4Yuo4YuN4YqVIMKr4pyFIOGKoOGLjuGNoyDhiqXhiK3hjI3hjKDhipsg4YqQ4Yqdwrsg4YmB"
    "4YiN4Y2NIOGIsuGMq+GKkSDhibXhiq3hiq3hiIjhipvhi43hipUg4Yql4Yit4Yid4YyDIOGLqOGImuGNiOGMveGInSBjYWxsYmFj"
    "ayIiIgogICAgcXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIGlmIHF1ZXJ5LmZyb21fdXNlci5pZCAhPSBBRE1JTl9J"
    "RDoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIHJldHVybgogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAg"
    "IHRyeToKICAgICAgICBfLCBhY3Rpb25fa2V5LCByaWRfc3RyID0gcXVlcnkuZGF0YS5zcGxpdCgiXyIsIDIpCiAgICBleGNlcHQg"
    "VmFsdWVFcnJvcjoKICAgICAgICByZXR1cm4KICAgIGZ1bmMgPSB7ImNsb3NlIjogY2xvc2Vfcm91bmQsICJyZXN0YXJ0IjogcmVz"
    "dGFydF9yb3VuZH0uZ2V0KGFjdGlvbl9rZXkpCiAgICBpZiBub3QgZnVuYzoKICAgICAgICByZXR1cm4KICAgIHRyeToKICAgICAg"
    "ICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfdGV4dCgi4o+zIOGJoOGIguGLsOGJtSDhiIvhi60uLi4iKQogICAgZXhjZXB0IEV4"
    "Y2VwdGlvbjoKICAgICAgICBwYXNzCiAgICBmYWtlX3VwZGF0ZSA9IF9GYWtlVXBkYXRlKF9GYWtlTXNnKHF1ZXJ5Lm1lc3NhZ2Us"
    "IHF1ZXJ5LmZyb21fdXNlcikpCiAgICBmYWtlX2NvbnRleHQgPSBTaW1wbGVOYW1lc3BhY2UoYXJncz1bcmlkX3N0cl0sIGJvdD1j"
    "b250ZXh0LmJvdCkKICAgIGF3YWl0IGZ1bmMoZmFrZV91cGRhdGUsIGZha2VfY29udGV4dCkKCmFzeW5jIGRlZiBoYW5kbGVfYWRt"
    "aW5fYWN0aW9uX2NhbmNlbCh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAi"
    "IiIvY2xvc2Vyb3VuZCwgL3Jlc3RhcnRyb3VuZCDhiIvhi60g4Yuo4Ymz4Yuo4YuN4YqVIMKr4p2MIOGKoOGLreGNoyDhibDhi4jh"
    "i43CuyDhiYHhiI3hjY0g4Yiy4Yyr4YqRIOGLq+GIiCDhiJ3hipXhiJ0g4YiI4YuN4YylIOGLqOGImuGLq+GJi+GIreGMpSBjYWxs"
    "YmFjayIiIgogICAgcXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIGlmIHF1ZXJ5LmZyb21fdXNlci5pZCAhPSBBRE1J"
    "Tl9JRDoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIHJldHVybgogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCLh"
    "ibDhiLDhiK3hi5/hiI3hjaIiKQogICAgdHJ5OgogICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KCLinYwg4Yut"
    "4YiFIOGKpeGIreGIneGMgyDhibDhiLDhiK3hi5/hiI3hjaMg4Yid4YqV4YidIOGKoOGIjeGJsOGIiOGLiOGMoOGIneGNoiIpCiAg"
    "ICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKCmFzeW5jIGRlZiBoYW5kbGVfY2FuY2VsX3RpY2tldF9waWNrKHVwZGF0"
    "ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIi9tYW51YWxjYW5jZWwg4YuZ4Yit"
    "LeGKqOGImOGIqOGMoSDhiaDhiovhiIsg4Yuo4Yia4Ymz4Yuo4YuN4YqVIOGJgeGMpeGIrS3hiJ3hiK3hjKsg4YmB4YiN4Y2NIChi"
    "dXR0b24pIOGJsOGMreGKliDhi6jhiJjhjKjhiKjhiLsg4Yib4Yio4YyL4YyI4YyrICjinIUv4p2MKSDhi6jhiJrhi6vhiLPhi60g"
    "Y2FsbGJhY2sgLQogICAg4Ym14Yqt4Yqt4YiI4Yqb4YuNIOGImOGIjeGJgOGJhSDhi6jhiJrhjYjhjLjhiJjhi40g4Yqg4Yu14Yia"
    "4YqRIOGKq+GIqOGMi+GMiOGMoCDhiaDhiovhiIsgKGhhbmRsZV90aWNrZXRfcmVsZWFzZV9jb25maXJtKSDhiaXhibsg4YqQ4YuN"
    "4Y2iIiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgaWYgcXVlcnkuZnJvbV91c2VyLmlkICE9IEFETUlO"
    "X0lEOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgcmV0dXJuCiAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQog"
    "ICAgdHJ5OgogICAgICAgIF8sIHJpZF9zdHIsIHRfc3RyID0gcXVlcnkuZGF0YS5zcGxpdCgiXyIpCiAgICAgICAgcmlkLCB0X251"
    "bSA9IGludChyaWRfc3RyKSwgaW50KHRfc3RyKQogICAgZXhjZXB0IFZhbHVlRXJyb3I6CiAgICAgICAgcmV0dXJuCgogICAgdGlj"
    "a2V0ID0gcm91bmRzLmdldChyaWQsIHt9KS5nZXQoInRpY2tldHMiLCB7fSkuZ2V0KHRfbnVtKQogICAgc3RhdHVzID0gdGlja2V0"
    "LmdldCgic3RhdHVzIikgaWYgdGlja2V0IGVsc2UgTm9uZQogICAgc3RhdHVzX2xhYmVsID0geyJTT0xEIjogIvCflLQg4Ymw4Yi9"
    "4Yyn4YiNIiwgIlBFTkRJTkciOiAi8J+foSDhiaDhiJjhjKDhiaPhiaDhiYUg4YiL4YutIn0uZ2V0KHN0YXR1cywgc3RhdHVzIG9y"
    "ICLhi6vhiI3hibPhi4jhiYAiKQogICAgYnV5ZXJfbGluZSA9ICIiCiAgICBpZiB0aWNrZXQgYW5kIHRpY2tldC5nZXQoImJ1eWVy"
    "X25hbWUiKToKICAgICAgICBidXllcl9saW5lID0gZiJcbvCfkaQg4YyI4Yui4Y2mIHt0aWNrZXRbJ2J1eWVyX25hbWUnXX0gKHt0"
    "aWNrZXQuZ2V0KCdidXllcl9waG9uZScpIG9yICdOL0EnfSkiCgogICAga2IgPSBJbmxpbmVLZXlib2FyZE1hcmt1cChbWwogICAg"
    "ICAgIElubGluZUtleWJvYXJkQnV0dG9uKCLinIUg4Yqg4YuO4Y2jIOGIjeGJgOGJhSIsIGNhbGxiYWNrX2RhdGE9ZiJ0aWNrZXRy"
    "ZWxlYXNlY29uZmlybV97cmlkfV97dF9udW19IiksCiAgICAgICAgSW5saW5lS2V5Ym9hcmRCdXR0b24oIuKdjCDhiqDhi63hjaMg"
    "4Ymw4YuI4YuNIiwgY2FsbGJhY2tfZGF0YT1mInRpY2tldHJlbGVhc2VjYW5jZWxfe3JpZH1fe3RfbnVtfSIpLAogICAgXV0pCiAg"
    "ICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoCiAgICAgICAgY2hhdF9pZD1BRE1JTl9JRCwKICAgICAgICB0ZXh0PSgK"
    "ICAgICAgICAgICAgZiLimqDvuI8g4YmB4Yyl4YitIHt0X251bX0gKHtyb3VuZF9sYWJlbChyaWQpfSkg4Ymg4Yqg4YiB4YqRIOGI"
    "sOGLk+GJtSB7c3RhdHVzX2xhYmVsfSDhipDhi43hjaJ7YnV5ZXJfbGluZX1cblxuIgogICAgICAgICAgICAi4Yut4YiF4YqVIOGJ"
    "geGMpeGIrSDhi6vhiI3hibDhi6vhi5kgKEFWQUlMQUJMRSkg4Yib4Yu14Yio4YyNIOGKpeGIreGMjeGMoOGKmyDhipDhi47hibU/"
    "IOGMiOGLouGLjSDhiqvhiIgg4YmB4Yyl4Yip4YqVIOGLq+GMo+GIjeGNoiIKICAgICAgICApLAogICAgICAgIHJlcGx5X21hcmt1"
    "cD1rYiwKICAgICkKCmFzeW5jIGRlZiBoYW5kbGVfdW5kb19tYW51YWxfc2FsZSh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29u"
    "dGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLhi6jhiqXhjIUg4Yi94Yur4YytIOGIjeGKrSDhiqjhibDhiJjhi5jhjIjh"
    "iaAg4Ymg4YqL4YiLIOGJoOGJs+GLqOGLjSDCq/CflJkgVW5kb8K7IOGJgeGIjeGNjSDhiLLhjKvhipEg4Yuo4Ymw4Yi44Yyh4Ym1"
    "4YqVIOGJgeGMpeGIrSjhibbhib0pIOGLiOGLsCBBVkFJTEFCTEUg4Yuo4Yia4YiY4YiN4Yi1IGNhbGxiYWNrIC0KICAgIOGKkOGJ"
    "o+GIqeGKlSBtYW51YWxfY2FuY2VsIOGKoOGImOGKreGKleGLriDhiIjhiqXhi6vhipXhi7PhipXhi7Eg4YmB4Yyl4YitIOGJoOGJ"
    "sOGIqyDhi63hjKDhiYDhiJvhiI0gKOGIm+GIteGJs+GLiOGJguGLqyDhi4jhi7Ag4YyI4YuiIOGKoOGLreGIi+GKreGIneGNoyDh"
    "jIjhi6Lhi40g4Ymg4Yi14YiN4YqtL+GJoOGKpeGMhSDhi6jhibDhiJjhi5jhjIjhiaAg4Yi14YiI4YiG4YqQKeGNoiIiIgogICAg"
    "cXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIGlmIHF1ZXJ5LmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAg"
    "ICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIHJldHVybgogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgIHRyeToKICAg"
    "ICAgICBfLCByaWRfc3RyLCBudW1zX3N0ciA9IHF1ZXJ5LmRhdGEuc3BsaXQoIl8iLCAyKQogICAgICAgIHJvdW5kX2lkID0gaW50"
    "KHJpZF9zdHIpCiAgICAgICAgdGlja2V0X251bXMgPSBbaW50KHgpIGZvciB4IGluIG51bXNfc3RyLnNwbGl0KCItIikgaWYgeF0K"
    "ICAgIGV4Y2VwdCAoVmFsdWVFcnJvciwgSW5kZXhFcnJvcik6CiAgICAgICAgcmV0dXJuCiAgICB0cnk6CiAgICAgICAgYXdhaXQg"
    "cXVlcnkuZWRpdF9tZXNzYWdlX3JlcGx5X21hcmt1cChyZXBseV9tYXJrdXA9Tm9uZSkKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAg"
    "ICAgICAgcGFzcwogICAgcmVsZWFzZWQgPSBbXQogICAgYWxyZWFkeV9mcmVlID0gW10KICAgIGZvciB0X251bSBpbiB0aWNrZXRf"
    "bnVtczoKICAgICAgICB0aWNrZXQgPSByb3VuZHMuZ2V0KHJvdW5kX2lkLCB7fSkuZ2V0KCJ0aWNrZXRzIiwge30pLmdldCh0X251"
    "bSkKICAgICAgICBpZiB0aWNrZXQgaXMgTm9uZToKICAgICAgICAgICAgY29udGludWUKICAgICAgICBpZiB0aWNrZXRbInN0YXR1"
    "cyJdID09ICJBVkFJTEFCTEUiOgogICAgICAgICAgICBhbHJlYWR5X2ZyZWUuYXBwZW5kKHRfbnVtKQogICAgICAgICAgICBjb250"
    "aW51ZQogICAgICAgIGZha2VfdXBkYXRlID0gX0Zha2VVcGRhdGUoX0Zha2VNc2cocXVlcnkubWVzc2FnZSwgcXVlcnkuZnJvbV91"
    "c2VyKSkKICAgICAgICBmYWtlX2NvbnRleHQgPSBTaW1wbGVOYW1lc3BhY2UoYXJncz1bc3RyKHJvdW5kX2lkKSwgc3RyKHRfbnVt"
    "KV0sIGJvdD1jb250ZXh0LmJvdCkKICAgICAgICBhd2FpdCBtYW51YWxfY2FuY2VsKGZha2VfdXBkYXRlLCBmYWtlX2NvbnRleHQp"
    "CiAgICAgICAgcmVsZWFzZWQuYXBwZW5kKHRfbnVtKQogICAgaWYgbm90IHJlbGVhc2VkIGFuZCBhbHJlYWR5X2ZyZWU6CiAgICAg"
    "ICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9tZXNzYWdlKGNoYXRfaWQ9QURNSU5fSUQsIHRleHQ9IuKEue+4jyDhi63hiIUg4Yi9"
    "4Yur4YytIOGKoOGIteGJgOGLteGIniDhibDhiLXhibDhiqvhiq3hiI/hiI0v4Ymw4YiY4YiN4Yi34YiN4Y2iIikKCmFzeW5jIGRl"
    "ZiBoYW5kbGVfc2V0d2lubmVyX3RpY2tldF9waWNrKHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVM"
    "VF9UWVBFKToKICAgICIiIi9zZXR3aW5uZXIg4YuZ4YitLeGJpeGJuyDhibDhjKDhiYXhiLYgKOGLiOGLreGInSDhiaDhiYHhiI3h"
    "jY0g4Ymw4YiY4Yit4YymKSDhiqjhiYDhiKjhiaDhi40g4Yuo4Ymw4Yi44YygLeGJgeGMpeGIrSDhiJ3hiK3hjKsg4YmB4YiN4Y2N"
    "IOGIi+GLrSDhiqDhi7XhiJrhipEg4YmB4Yyl4YitIOGIsuGImOGIreGMpSDhi6jhiL3hiI3hiJvhibUg4Yy94YiB4Y2NIOGLsOGI"
    "qOGMgyDhi6jhiJrhjIDhiJ3hiK0gY2FsbGJhY2siIiIKICAgIHF1ZXJ5ID0gdXBkYXRlLmNhbGxiYWNrX3F1ZXJ5CiAgICBpZiBx"
    "dWVyeS5mcm9tX3VzZXIuaWQgIT0gQURNSU5fSUQ6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgICAgICByZXR1cm4K"
    "ICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICB0cnk6CiAgICAgICAgXywgcmlkX3N0ciwgdF9zdHIgPSBxdWVyeS5kYXRhLnNw"
    "bGl0KCJfIikKICAgICAgICByaWQsIHRfbnVtID0gaW50KHJpZF9zdHIpLCBpbnQodF9zdHIpCiAgICBleGNlcHQgVmFsdWVFcnJv"
    "cjoKICAgICAgICByZXR1cm4KICAgIGF3YWl0IF9zdGFydF9zZXR3aW5uZXJfcHJpemVfc3RlcCh1cGRhdGUsIGNvbnRleHQsIHJp"
    "ZCwgdF9udW0sIGNoYXRfaWQ9cXVlcnkubWVzc2FnZS5jaGF0X2lkKQoKYXN5bmMgZGVmIGhhbmRsZV90aWNrZXRfcmVsZWFzZV9j"
    "b25maXJtKHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIuGJsuGKrOGJ"
    "tSDhiI3hiYDhiYMg4YiL4YutIOGLqOGJs+GLqOGLjeGKlSDCq+KchSDhiqDhi47hjaMg4YiN4YmA4YmFwrsg4YmB4YiN4Y2NIOGI"
    "suGMq+GKkSDhibXhiq3hiq3hiIjhipvhi43hipUg4YiY4YiN4YmA4YmFIOGLqOGImuGNiOGMveGInSBjYWxsYmFjayIiIgogICAg"
    "cXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIGlmIHF1ZXJ5LmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAg"
    "ICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIHJldHVybgogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgIHRyeToKICAg"
    "ICAgICBfLCByaWRfc3RyLCB0X3N0ciA9IHF1ZXJ5LmRhdGEuc3BsaXQoIl8iLCAyKQogICAgZXhjZXB0IFZhbHVlRXJyb3I6CiAg"
    "ICAgICAgcmV0dXJuCiAgICB0cnk6CiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoIuKPsyDhiaDhiILhi7Dh"
    "ibUg4YiL4YutLi4uIikKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcGFzcwogICAgZmFrZV91cGRhdGUgPSBfRmFrZVVw"
    "ZGF0ZShfRmFrZU1zZyhxdWVyeS5tZXNzYWdlLCBxdWVyeS5mcm9tX3VzZXIpKQogICAgZmFrZV9jb250ZXh0ID0gU2ltcGxlTmFt"
    "ZXNwYWNlKGFyZ3M9W3JpZF9zdHIsIHRfc3RyXSwgYm90PWNvbnRleHQuYm90KQogICAgYXdhaXQgbWFudWFsX2NhbmNlbChmYWtl"
    "X3VwZGF0ZSwgZmFrZV9jb250ZXh0KQoKYXN5bmMgZGVmIGhhbmRsZV90aWNrZXRfcmVsZWFzZV9jYW5jZWwodXBkYXRlOiBVcGRh"
    "dGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4Ymy4Yqs4Ym1IOGIjeGJgOGJgyDhiIvhi60g"
    "4Yuo4Ymz4Yuo4YuN4YqVIMKr4p2MIOGKoOGLreGNoyDhibDhi4jhi43CuyDhiYHhiI3hjY0g4Yiy4Yyr4YqRIOGLq+GIiCDhiJ3h"
    "ipXhiJ0g4YiI4YuN4YylIOGLqOGImuGLq+GJi+GIreGMpSBjYWxsYmFjayIiIgogICAgcXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tf"
    "cXVlcnkKICAgIGlmIHF1ZXJ5LmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQog"
    "ICAgICAgIHJldHVybgogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCLhibDhiLDhiK3hi5/hiI3hjaIiKQogICAgdHJ5OgogICAgICAg"
    "IGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KCLinYwg4YiN4YmA4YmDIOGJsOGIsOGIreGLn+GIjeGNoyDhiYHhjKXhiKkg"
    "4Yql4YqV4Yuw4YqQ4Ymg4YioIOGJgOGMpeGIj+GIjeGNoiIpCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKCmRl"
    "ZiBfYnVpbGRfbWFudWFsX3NlbGxfa2V5Ym9hcmQocm91bmRfaWQsIHNlbGVjdGVkKToKICAgICIiIk1hbnVhbC1zYWxlIGdyaWQg"
    "4YmB4YiN4Y2O4Ym94YqVIOGLqOGImuGMiOGKkOGJoyBoZWxwZXIgKOGLqOGJgeGMpeGIrSDhjY3hiK3hjI3hiK3hjI0g4Yml4Ym7"
    "IC0g4Yur4YiIIOGJgOGMpeGIjS/hiqDhjL3hi7Mg4YmB4YiN4Y2O4Ym9KeGNogogICAg8J+foiA9IOGLq+GIjeGJsOGLq+GLmCAo"
    "QVZBSUxBQkxFKSDigJQg4YiK4YiY4Yio4YylIOGLreGJveGIi+GIjeGNoyDinIUgPSDhiqDhiIHhipUg4Yuo4Ymw4YiY4Yio4Yyg"
    "4Y2jCiAgICDwn5+hID0g4Ymg4YiY4Yyg4Ymj4Ymg4YmFIOGIi+GLrSAoUEVORElORynhjaMg8J+UtCA9IOGJsOGIveGMp+GIjSAo"
    "U09MRCkg4oCUIOGIgeGKlOGJsyDhiaXhibsg4Yib4Yiz4Yuo4Ym1IOGLreGJu+GIi+GIjeGNogogICAg4Yib4Yi14Ymz4YuI4Yi7"
    "4Y2mIMKr4pyFIOGJgOGMpeGIjcK7L8Kr8J+Xke+4jyDhiJ3hiK3hjKsg4Yqg4Yy94Yuzwrsg4YmB4YiN4Y2O4Ym9IOGKqOGLmuGI"
    "hSDhjY3hiK3hjI3hiK3hjI0g4Ymw4YiI4Yut4Ymw4YuNIOGJoOGIq+GIs+GJuOGLjSDhiJjhiI3hiqXhiq3hibUg4YiL4YutIOGL"
    "reGJs+GLq+GIiSAtCiAgICDhiJ3hiq3hipXhi6vhibHhiJ0g4Ym04YiM4YyN4Yir4YidIOGJoOGKoOGKleGLtSDhiJjhiI3hiqXh"
    "iq3hibUg4YiL4YutIOGKqDEwMCDhiYHhiI3hjY0g4Ymg4YiL4YutIOGIm+GIs+GLqOGJtSDhiLXhiIjhiJvhi63hib3hiI3hjaMg"
    "MTAwIOGJsuGKrOGJtSDhiIvhiIjhi40g4YuZ4YitCiAgICDhjY3hiK3hjI3hiK3hjI0g4Yml4Ym74YuN4YqVIDEwMCDhiYHhiI3h"
    "jY0g4Yi14YiI4Yia4Yut4YudIOGJsOGMqOGIm+GIqiDhiYHhiI3hjY7hib0g4Ymi4Yyo4YiY4YipIOGIi+GLreGJs+GLqSDhi63h"
    "ib3hiIvhiInhjaIiIiIKICAgIHRpY2tldHMgPSByb3VuZHMuZ2V0KHJvdW5kX2lkLCB7fSkuZ2V0KCJ0aWNrZXRzIiwge30pCiAg"
    "ICBrYiA9IFtdCiAgICByb3cgPSBbXQogICAgZm9yIHRfbnVtIGluIHNvcnRlZCh0aWNrZXRzKToKICAgICAgICBzdGF0dXMgPSB0"
    "aWNrZXRzW3RfbnVtXS5nZXQoInN0YXR1cyIsICJBVkFJTEFCTEUiKQogICAgICAgIGlmIHRfbnVtIGluIHNlbGVjdGVkOgogICAg"
    "ICAgICAgICBsYWJlbCA9IGYi4pyFIHt0X251bX0iCiAgICAgICAgICAgIGNhbGxiYWNrID0gZiJtYW51YWxzZWxsdG9nZ2xlX3ty"
    "b3VuZF9pZH1fe3RfbnVtfSIKICAgICAgICBlbGlmIHN0YXR1cyA9PSAiQVZBSUxBQkxFIjoKICAgICAgICAgICAgbGFiZWwgPSBm"
    "IvCfn6Ige3RfbnVtfSIKICAgICAgICAgICAgY2FsbGJhY2sgPSBmIm1hbnVhbHNlbGx0b2dnbGVfe3JvdW5kX2lkfV97dF9udW19"
    "IgogICAgICAgIGVsaWYgc3RhdHVzID09ICJQRU5ESU5HIjoKICAgICAgICAgICAgbGFiZWwgPSBmIvCfn6Ege3RfbnVtfSIKICAg"
    "ICAgICAgICAgY2FsbGJhY2sgPSBmIm1hbnVhbHN0YXR1c197cm91bmRfaWR9X3t0X251bX0iCiAgICAgICAgZWxpZiB0aWNrZXRz"
    "W3RfbnVtXS5nZXQoInNoYXJlZCIpOgogICAgICAgICAgICBsYWJlbCA9IGYi8J+foyB7dF9udW19IgogICAgICAgICAgICBjYWxs"
    "YmFjayA9IGYibWFudWFsc3RhdHVzX3tyb3VuZF9pZH1fe3RfbnVtfSIKICAgICAgICBlbHNlOgogICAgICAgICAgICBsYWJlbCA9"
    "IGYi8J+UtCB7dF9udW19IgogICAgICAgICAgICBjYWxsYmFjayA9IGYibWFudWFsc3RhdHVzX3tyb3VuZF9pZH1fe3RfbnVtfSIK"
    "ICAgICAgICByb3cuYXBwZW5kKElubGluZUtleWJvYXJkQnV0dG9uKGxhYmVsLCBjYWxsYmFja19kYXRhPWNhbGxiYWNrKSkKICAg"
    "ICAgICBpZiBsZW4ocm93KSA9PSA1OgogICAgICAgICAgICBrYi5hcHBlbmQocm93KQogICAgICAgICAgICByb3cgPSBbXQogICAg"
    "aWYgcm93OgogICAgICAgIGtiLmFwcGVuZChyb3cpCiAgICByZXR1cm4ga2IKCmRlZiBfYnVpbGRfbWFudWFsX3NlbGxfYWN0aW9u"
    "X2tleWJvYXJkKHJvdW5kX2lkLCBzZWxlY3RlZCk6CiAgICAiIiLCq+KchSDhiYDhjKXhiI3Cuy/Cq/Cfl5HvuI8g4Yid4Yit4Yyr"
    "IOGKoOGMveGLs8K7IOGJgeGIjeGNjuGJveGKlSDhi6jhi6vhi5gg4Ymw4YiI4Yur4YutICjhi6jhjY3hiK3hjI3hiK3hjI0t4YuN"
    "4YytKSDhiYHhiI3hjY0t4Yiw4YiM4YuzIOGLqOGImuGMiOGKkOGJoyBoZWxwZXLhjaIKICAgIOGKqOGNjeGIreGMjeGIreGMjSDh"
    "iYHhiI3hjY7hib0g4Ymw4YiI4Yut4Ym2IOGJoOGIq+GIsSDhiJjhiI3hiqXhiq3hibUg4YiL4YutIOGIteGIiOGImuGIi+GKreGN"
    "oyAxMDAt4Ymy4Yqs4Ym1IOGLmeGIrSDhiaLhiIbhipXhiJ0g4Yql4YqV4YqzIOGIgeGIjeGMiuGLnCDhi63hibPhi6vhiI0KICAg"
    "ICjhibThiIzhjI3hiKvhiJ0g4Ymg4Yqg4YqV4Yu1IOGImOGIjeGKpeGKreGJtSDhiIvhi60g4YqoMTAwIOGJgeGIjeGNjSDhiaDh"
    "iIvhi60g4Yi14YiI4Yib4Yur4Yiz4YutKeGNoiIiIgogICAgaWYgbm90IHNlbGVjdGVkOgogICAgICAgIHJldHVybiBOb25lCiAg"
    "ICBwcmljZSA9IHJvdW5kcy5nZXQocm91bmRfaWQsIHt9KS5nZXQoInByaWNlIiwgMCkKICAgIHRvdGFsID0gcHJpY2UgKiBsZW4o"
    "c2VsZWN0ZWQpCiAgICBrYiA9IFsKICAgICAgICBbSW5saW5lS2V5Ym9hcmRCdXR0b24oCiAgICAgICAgICAgIGYi4pyFIOGJgOGM"
    "peGIjSAoe2xlbihzZWxlY3RlZCl9IOGJgeGMpeGIrSjhibbhib0pIOKAoiB7dG90YWw6LjBmfSDhiaXhiK0pIiwgY2FsbGJhY2tf"
    "ZGF0YT1mIm1hbnVhbHNlbGxjb25maXJtX3tyb3VuZF9pZH0iCiAgICAgICAgKV0sCiAgICAgICAgW0lubGluZUtleWJvYXJkQnV0"
    "dG9uKCLwn5eR77iPIOGIneGIreGMqyDhiqDhjL3hi7MiLCBjYWxsYmFja19kYXRhPWYibWFudWFsc2VsbGNsZWFyX3tyb3VuZF9p"
    "ZH0iKV0sCiAgICBdCiAgICByZXR1cm4gSW5saW5lS2V5Ym9hcmRNYXJrdXAoa2IpCgpkZWYgX21hbnVhbF9zZWxsX2FjdGlvbl90"
    "ZXh0KHJvdW5kX2lkLCBzZWxlY3RlZCk6CiAgICAiIiLhibDhiIjhi6vhi60g4YuowqvhiYDhjKXhiI0v4Yqg4Yy94Yuzwrsg4YiY"
    "4YiN4Yql4Yqt4Ym1IOGIi+GLrSDhi6jhiJrhibPhi60g4Yy94YiB4Y2NIOGLqOGImuGMiOGKkOGJoyBoZWxwZXIgLSDhi6jhibDh"
    "iJjhiKjhjKEg4YmB4Yyl4Yiu4Ym9IOGJpeGLm+GJtSDhiqXhipMg4Yyg4YmF4YiL4YiLIOGLi+GMiyAoYW1vdW50KSDhi6vhiLPh"
    "i6vhiI3hjaIiIiIKICAgIGlmIG5vdCBzZWxlY3RlZDoKICAgICAgICByZXR1cm4gIvCfkYYg4Yqo4YiL4YutIOGKq+GIiOGLjSDh"
    "jY3hiK3hjI3hiK3hjI0g8J+foiDhiYHhjKXhiK0o4Ym24Ym9KSDhi63hiJ3hiKjhjKHhjaIiCiAgICBudW1zID0gIiwgIi5qb2lu"
    "KHN0cih4KSBmb3IgeCBpbiBzb3J0ZWQoc2VsZWN0ZWQpKQogICAgcHJpY2UgPSByb3VuZHMuZ2V0KHJvdW5kX2lkLCB7fSkuZ2V0"
    "KCJwcmljZSIsIDApCiAgICB0b3RhbCA9IHByaWNlICogbGVuKHNlbGVjdGVkKQogICAgcmV0dXJuICgKICAgICAgICBmIuKchSDh"
    "i6jhibDhiJjhiKjhjKEg4YmB4Yyl4Yiu4Ym9ICh7bGVuKHNlbGVjdGVkKX0pOiB7bnVtc31cbiIKICAgICAgICBmIvCfkrUg4Yyg"
    "4YmF4YiL4YiLIOGLi+GMi+GNpiB7dG90YWw6LjBmfSDhiaXhiK0gKHtwcmljZTouMGZ9IOGJpeGIrSDDlyB7bGVuKHNlbGVjdGVk"
    "KX0pXG5cbiIKICAgICAgICAi8J+RhyDhjKjhiK3hiLDhi4vhiI0g4Yqr4YiJIMKr4YmA4Yyl4YiNwrsg4Yut4Yyr4YqR4Y2jIOGL"
    "iOGLreGInSDhiqjhiIvhi60g4Ymw4Yyo4Yib4YiqIOGLreGIneGIqOGMoeGNoiIKICAgICkKCmRlZiBfbWFudWFsX3NlbGxfZ3Jp"
    "ZF90ZXh0KHJvdW5kX2lkLCB0aWNrZXRzLCBzZWxlY3RlZCk6CiAgICAiIiJNYW51YWwtc2FsZSBncmlkIOGIi+GLrSDhiqjhiYHh"
    "iI3hjY7hibkg4Ymg4YiL4YutIOGLqOGImuGJs+GLrSDhiJvhjKDhiYPhiIjhi6sg4Yy94YiB4Y2NIOGLqOGImuGMiOGKkOGJoyBo"
    "ZWxwZXLhjaIiIiIKICAgIGF2YWlsYWJsZV9jb3VudCA9IHN1bSgxIGZvciB0IGluIHRpY2tldHMudmFsdWVzKCkgaWYgdC5nZXQo"
    "InN0YXR1cyIpID09ICJBVkFJTEFCTEUiKQogICAgcGVuZGluZ19jb3VudCA9IHN1bSgxIGZvciB0IGluIHRpY2tldHMudmFsdWVz"
    "KCkgaWYgdC5nZXQoInN0YXR1cyIpID09ICJQRU5ESU5HIikKICAgIHNoYXJlZF9jb3VudCA9IHN1bSgxIGZvciB0IGluIHRpY2tl"
    "dHMudmFsdWVzKCkgaWYgdC5nZXQoInN0YXR1cyIpID09ICJTT0xEIiBhbmQgdC5nZXQoInNoYXJlZCIpKQogICAgc29sZF9jb3Vu"
    "dCA9IHN1bSgxIGZvciB0IGluIHRpY2tldHMudmFsdWVzKCkgaWYgdC5nZXQoInN0YXR1cyIpID09ICJTT0xEIikgLSBzaGFyZWRf"
    "Y291bnQKICAgIHRleHQgPSAoCiAgICAgICAgZiLwn5KwIDxiPntyb3VuZF9sYWJlbChyb3VuZF9pZCl9PC9iPiDigJQg4Ymg4Yql"
    "4YyFIOGIveGLq+GMrVxuXG4iCiAgICAgICAgZiLwn5+iIOGLq+GIjeGJsOGLq+GLmToge2F2YWlsYWJsZV9jb3VudH0gICAgIgog"
    "ICAgICAgIGYi8J+foSBQZW5kaW5nOiB7cGVuZGluZ19jb3VudH0gICAgIgogICAgICAgIGYi8J+UtCDhibDhiL3hjKfhiI06IHtz"
    "b2xkX2NvdW50fSAgICAiCiAgICAgICAgZiLwn5+jIOGIiDIg4Yiw4YuNICjhjI3hiJvhiL0g4YuL4YyLKToge3NoYXJlZF9jb3Vu"
    "dH1cblxuIgogICAgICAgICLwn5GHIPCfn6Ig4Yur4YiN4Ymw4Yur4YuZIOGJgeGMpeGIruGJveGKlSDhi63hiJ3hiKjhjKEgKOGK"
    "qOGKoOGKleGLtSDhiaDhiIvhi60g4YiY4Yid4Yio4YylIOGLreGJveGIi+GIiSnhjaIgIgogICAgICAgICLhiqjhibPhib0g4Ymj"
    "4YiI4YuNIOGImOGIjeGKpeGKreGJtSDhiIvhi60gwqvhiYDhjKXhiI3CuyDhiYHhiI3hjY0g4Yur4YyI4Yqb4YiJ4Y2iIgogICAg"
    "KQogICAgcmV0dXJuIHRleHQKCmFzeW5jIGRlZiBoYW5kbGVfbWFudWFsX3N0YXR1c19waWNrKHVwZGF0ZTogVXBkYXRlLCBjb250"
    "ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIk1hbnVhbC1zYWxlIGdyaWQg4YuN4Yi14YylIFBlbmRpbmcv"
    "U29sZCDhiYHhjKXhiK7hib0g4Yiy4Yyr4YqRIOGIgeGKlOGJs+GJuOGLjeGKlSDhiaXhibsg4Yur4Yiz4Yur4YiN4Y2iIiIiCiAg"
    "ICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgaWYgcXVlcnkuZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgogICAg"
    "ICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgcmV0dXJuCiAgICB0cnk6CiAgICAgICAgXywgcmlkX3N0ciwgdF9zdHIg"
    "PSBxdWVyeS5kYXRhLnNwbGl0KCJfIikKICAgICAgICByaWQsIHRfbnVtID0gaW50KHJpZF9zdHIpLCBpbnQodF9zdHIpCiAgICBl"
    "eGNlcHQgVmFsdWVFcnJvcjoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIHJldHVybgogICAgdGlja2V0ID0g"
    "cm91bmRzLmdldChyaWQsIHt9KS5nZXQoInRpY2tldHMiLCB7fSkuZ2V0KHRfbnVtKQogICAgaWYgbm90IHRpY2tldDoKICAgICAg"
    "ICBhd2FpdCBxdWVyeS5hbnN3ZXIoIuKdjCDhiYHhjKXhiKkg4Yqg4YiN4Ymw4YyI4YqY4Yid4Y2iIiwgc2hvd19hbGVydD1UcnVl"
    "KQogICAgICAgIHJldHVybgogICAgc3RhdHVzID0gdGlja2V0LmdldCgic3RhdHVzIiwgIkFWQUlMQUJMRSIpCiAgICBpZiBzdGF0"
    "dXMgPT0gIlBFTkRJTkciOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigi8J+foSDhi63hiIUg4YmB4Yyl4YitIOGJoOGIjOGI"
    "iyDhibDhjKvhi4vhib0g4Ymw4Yut4Yuf4YiNIChQRU5ESU5HKeGNoiIsIHNob3dfYWxlcnQ9VHJ1ZSkKICAgIGVsaWYgc3RhdHVz"
    "ID09ICJTT0xEIiBhbmQgdGlja2V0LmdldCgic2hhcmVkIik6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKAogICAgICAgICAg"
    "ICBmIvCfn6Mg4YiIMiDhiLDhi40g4YyN4Yib4Yi9IOGLi+GMiyDhibDhiL3hjKfhiI3hjaZcbjHhipvhjaYge3RpY2tldC5nZXQo"
    "J2J1eWVyX25hbWUnKX0gKHt0aWNrZXQuZ2V0KCdidXllcl9waG9uZScpfSlcbiIKICAgICAgICAgICAgZiIy4Yqb4Y2mIHt0aWNr"
    "ZXQuZ2V0KCdidXllcl9uYW1lMicpfSAoe3RpY2tldC5nZXQoJ2J1eWVyX3Bob25lMicpfSkiLAogICAgICAgICAgICBzaG93X2Fs"
    "ZXJ0PVRydWUsCiAgICAgICAgKQogICAgZWxpZiBzdGF0dXMgPT0gIlNPTEQiOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigi"
    "8J+UtCDhi63hiIUg4YmB4Yyl4YitIOGJgOGLteGIniDhibDhiL3hjKfhiI3hjaIiLCBzaG93X2FsZXJ0PVRydWUpCiAgICBlbHNl"
    "OgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigi8J+foiDhi63hiIUg4YmB4Yyl4YitIOGLq+GIjeGJsOGLq+GLmCDhipDhi43h"
    "jaIiLCBzaG93X2FsZXJ0PVRydWUpCgphc3luYyBkZWYgaGFuZGxlX21hbnVhbF9zZWxsX3RvZ2dsZSh1cGRhdGU6IFVwZGF0ZSwg"
    "Y29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiIvbWFudWFsc2VsbCBncmlkIOGLjeGIteGMpSDwn5+i"
    "IOGJgeGMpeGIrSDhiLLhjKvhipEg4YuI4YuwIOGIneGIreGMqyAo4pyFKSDhi6jhiJrhjKjhiJ3hiK0v4Yuo4Yia4Yur4Yi14YuI"
    "4YyN4Yu1IGNhbGxiYWNrIC0KICAgIOGKqOGKoOGKleGLtSDhiaDhiIvhi60g4YmB4Yyl4YitIOGImOGIreGMpiDhiIjhiqDhipXh"
    "i7Ug4YyI4YuiIOGJoOGKoOGKleGLtSDhiIvhi60g4YiI4YiY4Yi44YylIOGLq+GIteGJveGIi+GIjeGNoiIiIgogICAgcXVlcnkg"
    "PSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIGFuc3dlcmVkID0gRmFsc2UKICAgIHRyeToKICAgICAgICBpZiBxdWVyeS5mcm9t"
    "X3VzZXIuaWQgIT0gQURNSU5fSUQ6CiAgICAgICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgICAgIHJldHVybgog"
    "ICAgICAgIHRyeToKICAgICAgICAgICAgXywgcmlkX3N0ciwgdF9zdHIgPSBxdWVyeS5kYXRhLnNwbGl0KCJfIikKICAgICAgICAg"
    "ICAgcm91bmRfaWQsIHRfbnVtID0gaW50KHJpZF9zdHIpLCBpbnQodF9zdHIpCiAgICAgICAgZXhjZXB0IFZhbHVlRXJyb3I6CiAg"
    "ICAgICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgICAgIHJldHVybgoKICAgICAgICBpZiByb3VuZF9pZCBub3Qg"
    "aW4gcm91bmRzIG9yIHJvdW5kc1tyb3VuZF9pZF1bInN0YXR1cyJdICE9ICJPUEVOIjoKICAgICAgICAgICAgYXdhaXQgcXVlcnku"
    "YW5zd2VyKCLinYwg4Yut4YiFIOGLmeGIrSDhiqjhiqXhipXhjI3hi7LhiIUg4YqV4YmBIOGKoOGLreGLsOGIiOGIneGNoiIsIHNo"
    "b3dfYWxlcnQ9VHJ1ZSkKICAgICAgICAgICAgYW5zd2VyZWQgPSBUcnVlCiAgICAgICAgICAgIHJldHVybgoKICAgICAgICB0aWNr"
    "ZXRzID0gcm91bmRzW3JvdW5kX2lkXVsidGlja2V0cyJdCiAgICAgICAgdGlja2V0ID0gdGlja2V0cy5nZXQodF9udW0pCiAgICAg"
    "ICAgaWYgbm90IHRpY2tldDoKICAgICAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKCLinYwg4YmB4Yyl4YipIOGKoOGIjeGJsOGM"
    "iOGKmOGIneGNoiIsIHNob3dfYWxlcnQ9VHJ1ZSkKICAgICAgICAgICAgYW5zd2VyZWQgPSBUcnVlCiAgICAgICAgICAgIHJldHVy"
    "bgoKICAgICAgICBjYXJ0X2VudHJ5ID0gbWFudWFsc2VsbF9jYXJ0LmdldChBRE1JTl9JRCkKICAgICAgICBpZiBub3QgY2FydF9l"
    "bnRyeSBvciBjYXJ0X2VudHJ5LmdldCgicm91bmRfaWQiKSAhPSByb3VuZF9pZDoKICAgICAgICAgICAgY2FydF9lbnRyeSA9IHsi"
    "cm91bmRfaWQiOiByb3VuZF9pZCwgInRpY2tldHMiOiBzZXQoKSwKICAgICAgICAgICAgICAgICAgICAgICAgICAgImdyaWRfbWVz"
    "c2FnZV9pZCI6IE5vbmUsICJhY3Rpb25fbWVzc2FnZV9pZCI6IE5vbmV9CiAgICAgICAgICAgIG1hbnVhbHNlbGxfY2FydFtBRE1J"
    "Tl9JRF0gPSBjYXJ0X2VudHJ5CiAgICAgICAgc2VsZWN0ZWQgPSBjYXJ0X2VudHJ5WyJ0aWNrZXRzIl0KCiAgICAgICAgaWYgdF9u"
    "dW0gaW4gc2VsZWN0ZWQ6CiAgICAgICAgICAgIHNlbGVjdGVkLmRpc2NhcmQodF9udW0pCiAgICAgICAgICAgIGF3YWl0IHF1ZXJ5"
    "LmFuc3dlcihmIuKeliDhiYHhjKXhiK0ge3RfbnVtfSDhiqjhiJ3hiK3hjKsg4Ymw4YqQ4Yi14Ym34YiN4Y2iIikKICAgICAgICBl"
    "bHNlOgogICAgICAgICAgICBpZiB0aWNrZXQuZ2V0KCJzdGF0dXMiKSAhPSAiQVZBSUxBQkxFIjoKICAgICAgICAgICAgICAgIGF3"
    "YWl0IHF1ZXJ5LmFuc3dlcigi4p2MIOGLreGIhSDhiYHhjKXhiK0g4Yqo4Yql4YqV4YyN4Yuy4YiFIOGLq+GIjeGJsOGLq+GLmSAo"
    "QVZBSUxBQkxFKSDhiqDhi63hi7DhiIjhiJ3hjaIiLCBzaG93X2FsZXJ0PVRydWUpCiAgICAgICAgICAgICAgICBhbnN3ZXJlZCA9"
    "IFRydWUKICAgICAgICAgICAgICAgIHJldHVybgogICAgICAgICAgICBzZWxlY3RlZC5hZGQodF9udW0pCiAgICAgICAgICAgIGF3"
    "YWl0IHF1ZXJ5LmFuc3dlcihmIuKchSDhiYHhjKXhiK0ge3RfbnVtfSDhibDhiJjhiK3hjKfhiI3hjaIiKQogICAgICAgIGFuc3dl"
    "cmVkID0gVHJ1ZQoKICAgICAgICAjIDEpIOGLqOGJgeGMpeGIrSDhjY3hiK3hjI3hiK3hjI0g4Yir4YixIChjaGVja21hcmtzKSDh"
    "iJvhi5jhiJjhipUgLSDhi63hiIUg4Yml4Ym74YuN4YqVIOGKpeGIteGKqCAxMDAg4YmB4YiN4Y2NIOGIteGIiOGLq+GLmCDhiIHh"
    "iI3hjIrhi5wg4Yuw4YiF4YqTIOGKkOGLjQogICAgICAgIGtiID0gX2J1aWxkX21hbnVhbF9zZWxsX2tleWJvYXJkKHJvdW5kX2lk"
    "LCBzZWxlY3RlZCkKICAgICAgICB0cnk6CiAgICAgICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KAogICAgICAg"
    "ICAgICAgICAgdGV4dD1fbWFudWFsX3NlbGxfZ3JpZF90ZXh0KHJvdW5kX2lkLCB0aWNrZXRzLCBzZWxlY3RlZCksCiAgICAgICAg"
    "ICAgICAgICBwYXJzZV9tb2RlPSJIVE1MIiwKICAgICAgICAgICAgICAgIHJlcGx5X21hcmt1cD1JbmxpbmVLZXlib2FyZE1hcmt1"
    "cChrYikKICAgICAgICAgICAgKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgIGxvZ2dlci5leGNlcHRpb24o"
    "Im1hbnVhbF9zZWxsX3RvZ2dsZTogZ3JpZCBlZGl0X21lc3NhZ2VfdGV4dCBmYWlsZWQiKQoKICAgICAgICAjIDIpIOGJsOGIiOGL"
    "q+GLqSDhi6jCq+GJgOGMpeGIjS/hiqDhjL3hi7PCuyDhiJjhiI3hiqXhiq3hibUg4Yib4YuY4YiY4YqVIC0g4Yi14YiI4Ymw4YiI"
    "4YuoIOGImOGIjeGKpeGKreGJtSDhiIHhiI3hjIrhi5wg4YqoMTAwIOGJgeGIjeGNjSDhjIjhi7DhiaUg4YuN4YytIOGKkOGLjQog"
    "ICAgICAgIGFjdGlvbl9tZXNzYWdlX2lkID0gY2FydF9lbnRyeS5nZXQoImFjdGlvbl9tZXNzYWdlX2lkIikKICAgICAgICBhY3Rp"
    "b25fdGV4dCA9IF9tYW51YWxfc2VsbF9hY3Rpb25fdGV4dChyb3VuZF9pZCwgc2VsZWN0ZWQpCiAgICAgICAgYWN0aW9uX2tiID0g"
    "X2J1aWxkX21hbnVhbF9zZWxsX2FjdGlvbl9rZXlib2FyZChyb3VuZF9pZCwgc2VsZWN0ZWQpCiAgICAgICAgdHJ5OgogICAgICAg"
    "ICAgICBpZiBhY3Rpb25fbWVzc2FnZV9pZDoKICAgICAgICAgICAgICAgIGF3YWl0IGNvbnRleHQuYm90LmVkaXRfbWVzc2FnZV90"
    "ZXh0KAogICAgICAgICAgICAgICAgICAgIGNoYXRfaWQ9QURNSU5fSUQsCiAgICAgICAgICAgICAgICAgICAgbWVzc2FnZV9pZD1h"
    "Y3Rpb25fbWVzc2FnZV9pZCwKICAgICAgICAgICAgICAgICAgICB0ZXh0PWFjdGlvbl90ZXh0LAogICAgICAgICAgICAgICAgICAg"
    "IHJlcGx5X21hcmt1cD1hY3Rpb25fa2IsCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgIGVsc2U6CiAgICAgICAgICAgICAg"
    "ICBzZW50ID0gYXdhaXQgY29udGV4dC5ib3Quc2VuZF9tZXNzYWdlKAogICAgICAgICAgICAgICAgICAgIGNoYXRfaWQ9QURNSU5f"
    "SUQsIHRleHQ9YWN0aW9uX3RleHQsIHJlcGx5X21hcmt1cD1hY3Rpb25fa2IKICAgICAgICAgICAgICAgICkKICAgICAgICAgICAg"
    "ICAgIGNhcnRfZW50cnlbImFjdGlvbl9tZXNzYWdlX2lkIl0gPSBnZXRhdHRyKHNlbnQsICJtZXNzYWdlX2lkIiwgTm9uZSkKICAg"
    "ICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICBsb2dnZXIuZXhjZXB0aW9uKCJtYW51YWxfc2VsbF90b2dnbGU6IGFj"
    "dGlvbiBtZXNzYWdlIHVwZGF0ZSBmYWlsZWQiKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICBsb2dnZXIuZXhjZXB0aW9u"
    "KCJtYW51YWxfc2VsbF90b2dnbGU6IHVuaGFuZGxlZCBlcnJvciIpCiAgICAgICAgaWYgbm90IGFuc3dlcmVkOgogICAgICAgICAg"
    "ICB0cnk6CiAgICAgICAgICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoIuKaoO+4jyDhiLXhiIXhibDhibUg4Ymw4Y2I4Yyl4Yiv"
    "4YiN4Y2jIOGKpeGJo+GKreGLjiAvbWFudWFsc2VsbCDhiaXhiIjhi40g4Yql4YqV4Yuw4YyI4YqTIOGLreGInuGKreGIqeGNoiIs"
    "IHNob3dfYWxlcnQ9VHJ1ZSkKICAgICAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgICAgIHBhc3MKCmFzeW5j"
    "IGRlZiBoYW5kbGVfbWFudWFsX3NlbGxfY29uZmlybSh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFV"
    "TFRfVFlQRSk6CiAgICAiIiIvbWFudWFsc2VsbCBncmlkIOGIi+GLrSDCq+KchSDhiYDhjKXhiI3CuyDhiLLhjKvhipEg4Yuo4Ymw"
    "4YiY4Yio4Yyh4Ym14YqVIOGJgeGMpeGIruGJvSDhi5jhjI3hibYg4Yuo4YyI4Yui4YuN4YqVIOGIteGInSvhiLXhiI3hiq0g4Ymg"
    "4Yy94YiB4Y2NIOGKpeGKleGLsuGIjeGKqSDhi6jhiJrhjKDhi63hiYUgY2FsbGJhY2siIiIKICAgIHF1ZXJ5ID0gdXBkYXRlLmNh"
    "bGxiYWNrX3F1ZXJ5CiAgICB0cnk6CiAgICAgICAgcmV0dXJuIGF3YWl0IF9oYW5kbGVfbWFudWFsX3NlbGxfY29uZmlybV9pbm5l"
    "cihxdWVyeSwgY29udGV4dCkKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgbG9nZ2VyLmV4Y2VwdGlvbigibWFudWFsX3Nl"
    "bGxfY29uZmlybTogdW5oYW5kbGVkIGVycm9yIikKICAgICAgICB0cnk6CiAgICAgICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigi"
    "4pqg77iPIOGIteGIheGJsOGJtSDhibDhjYjhjKXhiK/hiI3hjaMg4Yql4Ymj4Yqt4YuOIC9tYW51YWxzZWxsIOGJpeGIiOGLjSDh"
    "iqXhipXhi7DhjIjhipMg4Yut4Yie4Yqt4Yip4Y2iIiwgc2hvd19hbGVydD1UcnVlKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246"
    "CiAgICAgICAgICAgIHBhc3MKCmFzeW5jIGRlZiBfaGFuZGxlX21hbnVhbF9zZWxsX2NvbmZpcm1faW5uZXIocXVlcnksIGNvbnRl"
    "eHQpOgogICAgaWYgcXVlcnkuZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAg"
    "ICAgICAgcmV0dXJuCiAgICB0cnk6CiAgICAgICAgXywgcmlkX3N0ciA9IHF1ZXJ5LmRhdGEuc3BsaXQoIl8iLCAxKQogICAgICAg"
    "IHJvdW5kX2lkID0gaW50KHJpZF9zdHIpCiAgICBleGNlcHQgVmFsdWVFcnJvcjoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIo"
    "KQogICAgICAgIHJldHVybgoKICAgIGNhcnRfZW50cnkgPSBtYW51YWxzZWxsX2NhcnQuZ2V0KEFETUlOX0lEKQogICAgaWYgbm90"
    "IGNhcnRfZW50cnkgb3IgY2FydF9lbnRyeS5nZXQoInJvdW5kX2lkIikgIT0gcm91bmRfaWQgb3Igbm90IGNhcnRfZW50cnlbInRp"
    "Y2tldHMiXToKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoIuKaoO+4jyDhiqXhiaPhiq3hi44g4YiY4YyA4YiY4Yiq4YurIOGJ"
    "ouGLq+GKleGItSDhiqDhipXhi7Ug4YmB4Yyl4YitIOGLreGIneGIqOGMoeGNoiIsIHNob3dfYWxlcnQ9VHJ1ZSkKICAgICAgICBy"
    "ZXR1cm4KCiAgICBpZiByb3VuZF9pZCBub3QgaW4gcm91bmRzIG9yIHJvdW5kc1tyb3VuZF9pZF1bInN0YXR1cyJdICE9ICJPUEVO"
    "IjoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoIuKdjCDhi63hiIUg4YuZ4YitIOGKqOGKpeGKleGMjeGLsuGIhSDhipXhiYEg"
    "4Yqg4Yut4Yuw4YiI4Yid4Y2iIiwgc2hvd19hbGVydD1UcnVlKQogICAgICAgIG1hbnVhbHNlbGxfY2FydC5wb3AoQURNSU5fSUQs"
    "IE5vbmUpCiAgICAgICAgcmV0dXJuCgogICAgdGlja2V0cyA9IHJvdW5kc1tyb3VuZF9pZF1bInRpY2tldHMiXQogICAgc2VsZWN0"
    "ZWQgPSBjYXJ0X2VudHJ5WyJ0aWNrZXRzIl0KICAgIG5vdF9hdmFpbGFibGUgPSBbdG4gZm9yIHRuIGluIHNlbGVjdGVkIGlmIHRp"
    "Y2tldHMuZ2V0KHRuLCB7fSkuZ2V0KCJzdGF0dXMiKSAhPSAiQVZBSUxBQkxFIl0KICAgIGlmIG5vdF9hdmFpbGFibGU6CiAgICAg"
    "ICAgZm9yIHRuIGluIG5vdF9hdmFpbGFibGU6CiAgICAgICAgICAgIHNlbGVjdGVkLmRpc2NhcmQodG4pCiAgICAgICAgbnVtcyA9"
    "ICIsICIuam9pbihzdHIoeCkgZm9yIHggaW4gbm90X2F2YWlsYWJsZSkKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoZiLinYwg"
    "4YmB4Yyl4YitKOGJtuGJvSkge251bXN9IOGKqOGKpeGKleGMjeGLsuGIhSDhi6vhiI3hibDhi6vhi5kg4Yqg4Yut4Yuw4YiJ4Yid"
    "IC0g4Yqo4Yid4Yit4YyrIOGJsOGKkOGIteGJsOGLi+GIjeGNoiIsIHNob3dfYWxlcnQ9VHJ1ZSkKICAgICAgICAjIOGNjeGIreGM"
    "jeGIreGMjSDhiJjhiI3hiqXhiq3hibHhipUg4Yqg4YuY4Yid4YqVICjhiJvhiLXhibPhi4jhiLvhjaYg4Yut4YiFIGNhbGxiYWNr"
    "IOGLqOGImOGMo+GLjSDhiqjhibDhiIjhi6vhi6ggwqvhiYDhjKXhiI0v4Yqg4Yy94Yuzwrsg4YiY4YiN4Yql4Yqt4Ym1IOGKkOGL"
    "jeGNowogICAgICAgICMg4Yi14YiI4Yua4YiFIOGNjeGIreGMjeGIreGMjSDhiJjhiI3hiqXhiq3hibHhipUg4YiI4Yib4YuY4YiY"
    "4YqVIGdyaWRfbWVzc2FnZV9pZCDhi6vhiLXhjYjhiI3hjIvhiI0pCiAgICAgICAgZ3JpZF9tZXNzYWdlX2lkID0gY2FydF9lbnRy"
    "eS5nZXQoImdyaWRfbWVzc2FnZV9pZCIpCiAgICAgICAgaWYgZ3JpZF9tZXNzYWdlX2lkOgogICAgICAgICAgICBrYiA9IF9idWls"
    "ZF9tYW51YWxfc2VsbF9rZXlib2FyZChyb3VuZF9pZCwgc2VsZWN0ZWQpCiAgICAgICAgICAgIHRyeToKICAgICAgICAgICAgICAg"
    "IGF3YWl0IGNvbnRleHQuYm90LmVkaXRfbWVzc2FnZV90ZXh0KAogICAgICAgICAgICAgICAgICAgIGNoYXRfaWQ9QURNSU5fSUQs"
    "CiAgICAgICAgICAgICAgICAgICAgbWVzc2FnZV9pZD1ncmlkX21lc3NhZ2VfaWQsCiAgICAgICAgICAgICAgICAgICAgdGV4dD1f"
    "bWFudWFsX3NlbGxfZ3JpZF90ZXh0KHJvdW5kX2lkLCB0aWNrZXRzLCBzZWxlY3RlZCksCiAgICAgICAgICAgICAgICAgICAgcGFy"
    "c2VfbW9kZT0iSFRNTCIsCiAgICAgICAgICAgICAgICAgICAgcmVwbHlfbWFya3VwPUlubGluZUtleWJvYXJkTWFya3VwKGtiKQog"
    "ICAgICAgICAgICAgICAgKQogICAgICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICAgICAgbG9nZ2VyLmV4Y2Vw"
    "dGlvbigibWFudWFsX3NlbGxfY29uZmlybTogZ3JpZCByZWZyZXNoIGZhaWxlZCIpCiAgICAgICAgdHJ5OgogICAgICAgICAgICBh"
    "d2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfdGV4dCgKICAgICAgICAgICAgICAgIHRleHQ9X21hbnVhbF9zZWxsX2FjdGlvbl90ZXh0"
    "KHJvdW5kX2lkLCBzZWxlY3RlZCksCiAgICAgICAgICAgICAgICByZXBseV9tYXJrdXA9X2J1aWxkX21hbnVhbF9zZWxsX2FjdGlv"
    "bl9rZXlib2FyZChyb3VuZF9pZCwgc2VsZWN0ZWQpLAogICAgICAgICAgICApCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAg"
    "ICAgICAgICAgbG9nZ2VyLmV4Y2VwdGlvbigibWFudWFsX3NlbGxfY29uZmlybTogYWN0aW9uIHJlZnJlc2ggZmFpbGVkIikKICAg"
    "ICAgICByZXR1cm4KCiAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgc2VsZWN0ZWRfbGlzdCA9IHNvcnRlZChzZWxlY3RlZCkK"
    "ICAgIG1hbnVhbHNlbGxfY2FydC5wb3AoQURNSU5fSUQsIE5vbmUpCiAgICBwcmljZSA9IHJvdW5kc1tyb3VuZF9pZF1bInByaWNl"
    "Il0KICAgIHRvdGFsID0gcHJpY2UgKiBsZW4oc2VsZWN0ZWRfbGlzdCkKICAgIHRuc19sYWJlbCA9ICIsICIuam9pbihzdHIoeCkg"
    "Zm9yIHggaW4gc2VsZWN0ZWRfbGlzdCkKCiAgICBpZiBsZW4oc2VsZWN0ZWRfbGlzdCkgPT0gMToKICAgICAgICAjIOGKkOGMoOGI"
    "iyDhibLhiqzhibUg4Yqo4YiG4YqQIOGJpeGJuyAi4YiZ4YiJIOGLi+GMiyIg4YuI4Yut4YidICLhjI3hiJvhiL0g4YuL4YyLIOGI"
    "iDIg4Yiw4YuNIiDhi6jhiJrhiI0g4Yid4Yit4YyrIOGKpeGKk+GJgOGIreGJo+GIiOGKlQogICAgICAgIG1hbnVhbHNlbGxfc3Rh"
    "dGVbQURNSU5fSURdID0gewogICAgICAgICAgICAicm91bmRfaWQiOiByb3VuZF9pZCwKICAgICAgICAgICAgInRpY2tldHMiOiBz"
    "ZWxlY3RlZF9saXN0LAogICAgICAgICAgICAic3RlcCI6ICJjaG9vc2VfbW9kZSIsCiAgICAgICAgfQogICAgICAgIGhhbGYgPSBw"
    "cmljZSAvIDIKICAgICAgICBhd2FpdCBxdWVyeS5lZGl0X21lc3NhZ2VfdGV4dCgKICAgICAgICAgICAgZiLwn5KwIDxiPntyb3Vu"
    "ZF9sYWJlbChyb3VuZF9pZCl9IOKAlCDhiYHhjKXhiK0ge3NlbGVjdGVkX2xpc3RbMF19PC9iPlxuIgogICAgICAgICAgICBmIvCf"
    "krUg4YiZ4YiJIOGLi+GMi+GNpiB7cHJpY2U6LjBmfSDhiaXhiK1cblxuIgogICAgICAgICAgICAi4Yql4YqV4Yu04Ym1IOGImOGI"
    "uOGMpSDhi63hjYjhiI3hjIvhiIk/IiwKICAgICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIsCiAgICAgICAgICAgIHJlcGx5X21h"
    "cmt1cD1JbmxpbmVLZXlib2FyZE1hcmt1cChbCiAgICAgICAgICAgICAgICBbSW5saW5lS2V5Ym9hcmRCdXR0b24oZiLwn5KzIOGI"
    "meGIiSDhi4vhjIsgKDEg4YyI4YuiKSDigJQge3ByaWNlOi4wZn0g4Yml4YitIiwgY2FsbGJhY2tfZGF0YT1mInNlbGxtb2RlX2Z1"
    "bGxfe3JvdW5kX2lkfSIpXSwKICAgICAgICAgICAgICAgIFtJbmxpbmVLZXlib2FyZEJ1dHRvbihmIvCfpJ0g4YyN4Yib4Yi9IOGL"
    "i+GMiyDhiIgyIOGIsOGLjSAo4Yql4Yur4YqV4Yuz4YqV4YuxIHtoYWxmOi4wZn0g4Yml4YitKSIsIGNhbGxiYWNrX2RhdGE9ZiJz"
    "ZWxsbW9kZV9zcGxpdF97cm91bmRfaWR9IildLAogICAgICAgICAgICBdKSwKICAgICAgICApCiAgICAgICAgcmV0dXJuCgogICAg"
    "bWFudWFsc2VsbF9zdGF0ZVtBRE1JTl9JRF0gPSB7CiAgICAgICAgInJvdW5kX2lkIjogcm91bmRfaWQsCiAgICAgICAgInRpY2tl"
    "dHMiOiBzZWxlY3RlZF9saXN0LAogICAgICAgICJzdGVwIjogIm5hbWUiLAogICAgICAgICJtb2RlIjogImZ1bGwiLAogICAgfQog"
    "ICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoCiAgICAgICAgZiLwn5KwIDxiPntyb3VuZF9sYWJlbChyb3VuZF9pZCl9"
    "IOKAlCDhiYHhjKXhiK0o4Ym24Ym9KSB7dG5zX2xhYmVsfTwvYj5cbiIKICAgICAgICBmIvCfkrUg4Yyg4YmF4YiL4YiLIOGLi+GM"
    "i+GNpiB7dG90YWw6LjBmfSDhiaXhiK0gKHtwcmljZTouMGZ9IOGJpeGIrSDDlyB7bGVuKHNlbGVjdGVkX2xpc3QpfSlcblxuIgog"
    "ICAgICAgICLwn5GkIOGKpeGJo+GKreGLjiDhi6jhjIjhi6Lhi43hipUgPGI+4Yi14YidPC9iPiDhiaXhibsg4Yur4Yi14YyI4Ymh"
    "4Y2iXG4iCiAgICAgICAgIuGIiOGIneGIs+GIjOGNpiA8Y29kZT7hiqDhiaDhiaAg4Yqo4Ymg4YuwPC9jb2RlPlxuXG4iCiAgICAg"
    "ICAgIuKEue+4jyDhiIjhiJjhiLDhiKjhi50gL2NhbmNlbCDhi63hiIvhiqnhjaIiLAogICAgICAgIHBhcnNlX21vZGU9IkhUTUwi"
    "CiAgICApCgphc3luYyBkZWYgaGFuZGxlX21hbnVhbF9zZWxsX21vZGUodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRU"
    "eXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4Yuowqvwn5KzIOGImeGIiSDhi4vhjIvCuyAvIMKr8J+knSDhjI3hiJvhiL0g4YuL"
    "4YyLIOGIiDIg4Yiw4YuNwrsg4Yid4Yit4YyrIGNhbGxiYWNrIiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQog"
    "ICAgaWYgcXVlcnkuZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAg"
    "cmV0dXJuCiAgICB0cnk6CiAgICAgICAgXywgbW9kZSwgcmlkX3N0ciA9IHF1ZXJ5LmRhdGEuc3BsaXQoIl8iLCAyKQogICAgICAg"
    "IHJvdW5kX2lkID0gaW50KHJpZF9zdHIpCiAgICBleGNlcHQgVmFsdWVFcnJvcjoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIo"
    "KQogICAgICAgIHJldHVybgogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKCiAgICBwZW5kaW5nID0gbWFudWFsc2VsbF9zdGF0ZS5n"
    "ZXQoQURNSU5fSUQpCiAgICBpZiBub3QgcGVuZGluZyBvciBwZW5kaW5nLmdldCgicm91bmRfaWQiKSAhPSByb3VuZF9pZCBvciBw"
    "ZW5kaW5nLmdldCgic3RlcCIpICE9ICJjaG9vc2VfbW9kZSI6CiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQo"
    "IuKaoO+4jyDhi63hiIUg4YiC4Yuw4Ym1IOGMiuGLnOGLjSDhiqDhiI3hjY7hiaDhibPhiI3hjaMgL21hbnVhbHNlbGwg4Yuw4YyN"
    "4YiY4YuNIOGLreGMgOGIneGIqeGNoiIpCiAgICAgICAgcmV0dXJuCgogICAgdGlja2V0X251bSA9IHBlbmRpbmdbInRpY2tldHMi"
    "XVswXQogICAgcHJpY2UgPSByb3VuZHNbcm91bmRfaWRdWyJwcmljZSJdCgogICAgaWYgbW9kZSA9PSAiZnVsbCI6CiAgICAgICAg"
    "cGVuZGluZ1sibW9kZSJdID0gImZ1bGwiCiAgICAgICAgcGVuZGluZ1sic3RlcCJdID0gIm5hbWUiCiAgICAgICAgYXdhaXQgcXVl"
    "cnkuZWRpdF9tZXNzYWdlX3RleHQoCiAgICAgICAgICAgIGYi8J+SsCA8Yj57cm91bmRfbGFiZWwocm91bmRfaWQpfSDigJQg4YmB"
    "4Yyl4YitIHt0aWNrZXRfbnVtfTwvYj5cbiIKICAgICAgICAgICAgZiLwn5K1IOGLi+GMi+GNpiB7cHJpY2U6LjBmfSDhiaXhiK1c"
    "blxuIgogICAgICAgICAgICAi8J+RpCDhiqXhiaPhiq3hi44g4Yuo4YyI4Yui4YuN4YqVIDxiPuGIteGInTwvYj4g4Yml4Ym7IOGL"
    "q+GIteGMiOGJoeGNolxuIgogICAgICAgICAgICAi4YiI4Yid4Yiz4YiM4Y2mIDxjb2RlPuGKoOGJoOGJoCDhiqjhiaDhi7A8L2Nv"
    "ZGU+XG5cbiIKICAgICAgICAgICAgIuKEue+4jyDhiIjhiJjhiLDhiKjhi50gL2NhbmNlbCDhi63hiIvhiqnhjaIiLAogICAgICAg"
    "ICAgICBwYXJzZV9tb2RlPSJIVE1MIiwKICAgICAgICApCiAgICAgICAgcmV0dXJuCgogICAgIyBtb2RlID09ICJzcGxpdCIKICAg"
    "IGhhbGYgPSBwcmljZSAvIDIKICAgIHBlbmRpbmdbIm1vZGUiXSA9ICJzcGxpdCIKICAgIHBlbmRpbmdbInN0ZXAiXSA9ICJuYW1l"
    "IgogICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoCiAgICAgICAgZiLwn6SdIDxiPntyb3VuZF9sYWJlbChyb3VuZF9p"
    "ZCl9IOKAlCDhiYHhjKXhiK0ge3RpY2tldF9udW19ICjhiIgyIOGIsOGLjSDhjI3hiJvhiL0g4YuL4YyLKTwvYj5cbiIKICAgICAg"
    "ICBmIvCfkrUg4Yql4Yur4YqV4Yuz4YqV4YuxIOGMiOGLoiDhi6jhiJrhiqjhjY3hiIjhi43hjaYge2hhbGY6LjBmfSDhiaXhiK0g"
    "KOGMoOGJheGIi+GIiyB7cHJpY2U6LjBmfSDhiaXhiK0pXG5cbiIKICAgICAgICAi8J+RpCA8Yj4x4YqbIOGMiOGLojwvYj4g4Yi1"
    "4YidIOGLq+GIteGMiOGJoeGNplxuIgogICAgICAgICLhiIjhiJ3hiLPhiIzhjaYgPGNvZGU+4Yqg4Ymg4YmgIOGKqOGJoOGLsDwv"
    "Y29kZT5cblxuIgogICAgICAgICLihLnvuI8g4YiI4YiY4Yiw4Yio4YudIC9jYW5jZWwg4Yut4YiL4Yqp4Y2iIiwKICAgICAgICBw"
    "YXJzZV9tb2RlPSJIVE1MIiwKICAgICkKCmFzeW5jIGRlZiBoYW5kbGVfbWFudWFsX3NlbGxfY2xlYXIodXBkYXRlOiBVcGRhdGUs"
    "IGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIiL21hbnVhbHNlbGwgZ3JpZCDhiIvhi60gwqvwn5eR"
    "77iPIOGIneGIreGMqyDhiqDhjL3hi7PCuyDhiLLhjKvhipEg4Yuo4Ymw4YiY4Yio4Yyh4Ym14YqVIOGJgeGMpeGIruGJvSDhiIHh"
    "iIkg4Yuo4Yia4Yur4Yy44YuzIGNhbGxiYWNrIiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgaWYgcXVl"
    "cnkuZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgcmV0dXJuCiAg"
    "ICB0cnk6CiAgICAgICAgXywgcmlkX3N0ciA9IHF1ZXJ5LmRhdGEuc3BsaXQoIl8iLCAxKQogICAgICAgIHJvdW5kX2lkID0gaW50"
    "KHJpZF9zdHIpCiAgICBleGNlcHQgVmFsdWVFcnJvcjoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIHJldHVy"
    "bgoKICAgIGNhcnRfZW50cnkgPSBtYW51YWxzZWxsX2NhcnQuZ2V0KEFETUlOX0lEKQogICAgZ3JpZF9tZXNzYWdlX2lkID0gY2Fy"
    "dF9lbnRyeS5nZXQoImdyaWRfbWVzc2FnZV9pZCIpIGlmIGNhcnRfZW50cnkgZWxzZSBOb25lCiAgICBtYW51YWxzZWxsX2NhcnQu"
    "cG9wKEFETUlOX0lELCBOb25lKQogICAgYXdhaXQgcXVlcnkuYW5zd2VyKCLwn5eR77iPIOGIneGIreGMqyDhjLjhi7XhibfhiI3h"
    "jaIiKQoKICAgIHRpY2tldHMgPSByb3VuZHMuZ2V0KHJvdW5kX2lkLCB7fSkuZ2V0KCJ0aWNrZXRzIiwge30pCiAgICBpZiBub3Qg"
    "dGlja2V0czoKICAgICAgICByZXR1cm4KICAgIGlmIGdyaWRfbWVzc2FnZV9pZDoKICAgICAgICBrYiA9IF9idWlsZF9tYW51YWxf"
    "c2VsbF9rZXlib2FyZChyb3VuZF9pZCwgc2V0KCkpCiAgICAgICAgdHJ5OgogICAgICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5l"
    "ZGl0X21lc3NhZ2VfdGV4dCgKICAgICAgICAgICAgICAgIGNoYXRfaWQ9QURNSU5fSUQsCiAgICAgICAgICAgICAgICBtZXNzYWdl"
    "X2lkPWdyaWRfbWVzc2FnZV9pZCwKICAgICAgICAgICAgICAgIHRleHQ9X21hbnVhbF9zZWxsX2dyaWRfdGV4dChyb3VuZF9pZCwg"
    "dGlja2V0cywgc2V0KCkpLAogICAgICAgICAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIsCiAgICAgICAgICAgICAgICByZXBseV9t"
    "YXJrdXA9SW5saW5lS2V5Ym9hcmRNYXJrdXAoa2IpCiAgICAgICAgICAgICkKICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAg"
    "ICAgICAgICBsb2dnZXIuZXhjZXB0aW9uKCJtYW51YWxfc2VsbF9jbGVhcjogZ3JpZCByZWZyZXNoIGZhaWxlZCIpCiAgICB0cnk6"
    "CiAgICAgICAgYXdhaXQgcXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoCiAgICAgICAgICAgIHRleHQ9X21hbnVhbF9zZWxsX2FjdGlv"
    "bl90ZXh0KHJvdW5kX2lkLCBzZXQoKSksCiAgICAgICAgICAgIHJlcGx5X21hcmt1cD1fYnVpbGRfbWFudWFsX3NlbGxfYWN0aW9u"
    "X2tleWJvYXJkKHJvdW5kX2lkLCBzZXQoKSksCiAgICAgICAgKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICBsb2dnZXIu"
    "ZXhjZXB0aW9uKCJtYW51YWxfc2VsbF9jbGVhcjogYWN0aW9uIHJlZnJlc2ggZmFpbGVkIikKCmFzeW5jIGRlZiBoYW5kbGVfcGxh"
    "eWVyX3JvdW5kX3BpY2sodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi"
    "4Ymw4Yyr4YuL4Ym9IOGKqOGKoOGKleGLtSDhiaDhiIvhi60g4YqV4YmBIOGLmeGIrSDhiLLhipbhiK0g4Yqo4YmB4YiN4Y2NIChi"
    "dXR0b24pIOGIi+GLrSDhi5nhiK0g4Yiy4YiY4Yit4YylIOGLqOGImuGIsOGIqyBjYWxsYmFjayAtCiAgICAvcGxheSwgL3B1cmNo"
    "YXNlZCwgL2F2YWlsYWJsZSDhiIvhi60g4Yur4YiI4YuN4YqVIOGJsOGImOGIs+GIs+GLrSDhibXhi5Xhi5vhi50g4Yur4YqV4YqV"
    "IOGLmeGIrSDhiaDhiJjhi6vhi50g4Ymg4Yu14YyL4YiaIOGLq+GIteGKrOGLs+GIjSIiIgogICAgcXVlcnkgPSB1cGRhdGUuY2Fs"
    "bGJhY2tfcXVlcnkKICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICB0cnk6CiAgICAgICAgXywgYWN0aW9uX2tleSwgcmlkX3N0"
    "ciA9IHF1ZXJ5LmRhdGEuc3BsaXQoIl8iLCAyKQogICAgZXhjZXB0IFZhbHVlRXJyb3I6CiAgICAgICAgcmV0dXJuCiAgICBmdW5j"
    "ID0geyJwbGF5IjogcGxheSwgInB1cmNoYXNlZCI6IGxpc3RfcHVyY2hhc2VkLCAiYXZhaWxhYmxlIjogbGlzdF9hdmFpbGFibGUs"
    "ICJ3aW5uZXJzIjogd2lubmVyc19jb21tYW5kfS5nZXQoYWN0aW9uX2tleSkKICAgIGlmIG5vdCBmdW5jOgogICAgICAgIHJldHVy"
    "bgogICAgZmFrZV91cGRhdGUgPSBfRmFrZVVwZGF0ZShfRmFrZU1zZyhxdWVyeS5tZXNzYWdlLCBxdWVyeS5mcm9tX3VzZXIpKQog"
    "ICAgZmFrZV9jb250ZXh0ID0gU2ltcGxlTmFtZXNwYWNlKGFyZ3M9W3JpZF9zdHJdLCBib3Q9Y29udGV4dC5ib3QpCiAgICBhd2Fp"
    "dCBmdW5jKGZha2VfdXBkYXRlLCBmYWtlX2NvbnRleHQpCgphc3luYyBkZWYgaGFuZGxlX3VzZWRfcmVmc19maWx0ZXIodXBkYXRl"
    "OiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4YuoL3VzZWRyZWZzIOGLqOGKreGN"
    "jeGLqyDhi5jhi7Qg4Yib4Yyj4Yiq4YurIOGJgeGIjeGNjuGJveGKlSDhi6jhiJrhi63hi50gY2FsbGJhY2siIiIKICAgIHF1ZXJ5"
    "ID0gdXBkYXRlLmNhbGxiYWNrX3F1ZXJ5CiAgICBpZiBxdWVyeS5mcm9tX3VzZXIuaWQgIT0gQURNSU5fSUQ6CiAgICAgICAgYXdh"
    "aXQgcXVlcnkuYW5zd2VyKCkKICAgICAgICByZXR1cm4KICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCgogICAgZmlsdGVyX2tleSA9"
    "IHF1ZXJ5LmRhdGEuc3BsaXQoIl8iLCAxKVsxXSAgIyAidGVsZWJpcnIiIHwgImNiZWJpcnIiIHwgImFsbCIKCiAgICBpZiBmaWx0"
    "ZXJfa2V5ID09ICJhbGwiOgogICAgICAgIGl0ZW1zID0gbGlzdCh1c2VkX3Ntc19yZWZzLml0ZW1zKCkpCiAgICAgICAgdGl0bGUg"
    "PSAi8J+TiyA8Yj7hjKXhiYXhiJ0g4YiL4YutIOGLqOGLi+GIiSDhiIHhiInhiJ0g4Yiq4Y2I4Yio4YqV4Yi24Ym9PC9iPlxuIgog"
    "ICAgZWxzZToKICAgICAgICBpdGVtcyA9IFsKICAgICAgICAgICAgKG5vcm1fcmVmLCBpbmZvKSBmb3Igbm9ybV9yZWYsIGluZm8g"
    "aW4gdXNlZF9zbXNfcmVmcy5pdGVtcygpCiAgICAgICAgICAgIGlmIGluZm8uZ2V0KCJwYXltZW50X21ldGhvZCIpID09IGZpbHRl"
    "cl9rZXkKICAgICAgICBdCiAgICAgICAgbWV0aG9kID0gUEFZTUVOVF9NRVRIT0RTLmdldChmaWx0ZXJfa2V5KQogICAgICAgIHRp"
    "dGxlID0gZiLwn6e+IDxiPuGMpeGJheGInSDhiIvhi60g4Yuo4YuL4YiJIOGIquGNiOGIqOGKleGItuGJvSDigJQge21ldGhvZFsn"
    "bGFiZWwnXSBpZiBtZXRob2QgZWxzZSBmaWx0ZXJfa2V5fTwvYj5cbiIKCiAgICBpZiBub3QgaXRlbXM6CiAgICAgICAgYXdhaXQg"
    "cXVlcnkuZWRpdF9tZXNzYWdlX3RleHQoIuKEue+4jyDhiIjhi5rhiIUg4Yib4Yyj4Yiq4YurIOGIneGKleGInSDhjKXhiYXhiJ0g"
    "4YiL4YutIOGLqOGLi+GIiCDhiKrhjYjhiKjhipXhiLUg4Yqg4YiN4Ymw4YyI4YqY4Yid4Y2iIikKICAgICAgICByZXR1cm4KCiAg"
    "ICBpdGVtcy5zb3J0KGtleT1sYW1iZGEga3Y6IGt2WzFdLmdldCgidXNlZF9hdCIpIG9yIGRhdGV0aW1lLm1pbiwgcmV2ZXJzZT1U"
    "cnVlKQoKICAgIGxpbmVzID0gW3RpdGxlXQogICAgZm9yIG5vcm1fcmVmLCBpbmZvIGluIGl0ZW1zOgogICAgICAgIHVzZWRfYXQg"
    "PSBpbmZvLmdldCgidXNlZF9hdCIpCiAgICAgICAgd2hlbiA9IHVzZWRfYXQuc3RyZnRpbWUoIiVZLSVtLSVkICVIOiVNIikgaWYg"
    "dXNlZF9hdCBlbHNlICJOL0EiCiAgICAgICAgcG0gPSBQQVlNRU5UX01FVEhPRFMuZ2V0KGluZm8uZ2V0KCJwYXltZW50X21ldGhv"
    "ZCIpKQogICAgICAgIHBtX2xhYmVsID0gcG1bImxhYmVsIl0gaWYgcG0gZWxzZSAiTi9BIgogICAgICAgIGxpbmVzLmFwcGVuZCgK"
    "ICAgICAgICAgICAgZiLilqrvuI8gPGNvZGU+e2luZm8uZ2V0KCdyYXdfcmVmJykgb3Igbm9ybV9yZWZ9PC9jb2RlPiDigJQge3Jv"
    "dW5kX2xhYmVsKGluZm8uZ2V0KCdyb3VuZF9pZCcpKSBpZiBpbmZvLmdldCgncm91bmRfaWQnKSBpcyBub3QgTm9uZSBlbHNlICdO"
    "L0EnfSAiCiAgICAgICAgICAgIGYi4YmB4Yyl4YitIHtpbmZvLmdldCgndGlja2V0X251bScsICdOL0EnKX0g4oCUIHtpbmZvLmdl"
    "dCgnYnV5ZXJfbmFtZScpIG9yICdOL0EnfSDigJQge3BtX2xhYmVsfSDigJQge3doZW59IgogICAgICAgICkKCgogICAgY2h1bmsg"
    "PSAiIgogICAgZm9yIGxpbmUgaW4gbGluZXM6CiAgICAgICAgaWYgbGVuKGNodW5rKSArIGxlbihsaW5lKSArIDEgPiAzNTAwOgog"
    "ICAgICAgICAgICBhd2FpdCBjb250ZXh0LmJvdC5zZW5kX21lc3NhZ2UoY2hhdF9pZD1BRE1JTl9JRCwgdGV4dD1jaHVuaywgcGFy"
    "c2VfbW9kZT0iSFRNTCIpCiAgICAgICAgICAgIGNodW5rID0gIiIKICAgICAgICBjaHVuayArPSBsaW5lICsgIlxuIgogICAgaWYg"
    "Y2h1bms6CiAgICAgICAgYXdhaXQgY29udGV4dC5ib3Quc2VuZF9tZXNzYWdlKGNoYXRfaWQ9QURNSU5fSUQsIHRleHQ9Y2h1bmss"
    "IHBhcnNlX21vZGU9IkhUTUwiKQoKYXN5bmMgZGVmIGFsbF90aWNrZXRzX2NvbW1hbmQodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6"
    "IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4Yqg4Yu14Yia4YqVIOGJoOGKoOGKleGLtSDhi5nhiK0g4YuN4Yi1"
    "4YylIOGLq+GIiSDhiIHhiInhipXhiJ0g4Ymy4Yqs4Ym24Ym9ICjhi6jhibDhiLjhjKHhiJ0g4YiG4YqRIOGLq+GIjeGJsOGIuOGM"
    "oS/hiaDhiILhi7DhibUg4YiL4YutIOGLq+GIiSkg4YqoIyDhiYHhjKXhiK0g4Yql4YqTIOGIteGIjeGKrSDhiYHhjKXhiK0g4YyL"
    "4YitCiAgICDhiqXhipXhi7Ag4Yud4Yit4Yud4YitICjhiIrhiLXhibUg4Yql4YqV4YyCIGdyaWQva2V5Ym9hcmQg4Yqg4Yut4Yuw"
    "4YiI4YidKSDhi6jhiJrhi6vhiLPhi60g4Ym14YuV4Yub4YudCiAgICDhiqDhjKDhiYPhiYDhiJ3hjaYgL2FsbHRpY2tldHMgPOGL"
    "meGIrT4iIiIKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAg"
    "ICBhcmdzID0gY29udGV4dC5hcmdzCiAgICBpZiBub3QgYXJnczoKICAgICAgICBhd2FpdCBfc2VuZF9yb3VuZF9waWNrZXIoCiAg"
    "ICAgICAgICAgIHVwZGF0ZSwgY29udGV4dCwgImFsbHRpY2tldHMiLAogICAgICAgICAgICByb3VuZHMua2V5cygpLAogICAgICAg"
    "ICAgICAi8J+Xgu+4jyDhi6jhi6jhibXhipvhi40g4YuZ4YitIOGIgeGIieGKleGInSDhibLhiqzhibbhib0gKOGLqOGJsOGIuOGM"
    "oeGKkyDhi6vhiI3hibDhiLjhjKEpIOGIm+GLqOGJtSDhi63hjYjhiI3hjIvhiIk/IiwKICAgICAgICAgICAgIuKEue+4jyDhiaDh"
    "iqDhiIHhipEg4Yiw4YuT4Ym1IOGIneGKleGInSDhi5nhiK0g4Yuo4YiI4Yid4Y2iIgogICAgICAgICkKICAgICAgICByZXR1cm4K"
    "CiAgICB0cnk6CiAgICAgICAgcm91bmRfaWQgPSBpbnQoYXJnc1swXSkKICAgIGV4Y2VwdCBWYWx1ZUVycm9yOgogICAgICAgIGF3"
    "YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKaoO+4jyDhibXhiq3hiq3hiIjhipsg4Yuo4YuZ4YitIOGJgeGMpeGIrSDh"
    "i6vhiLXhjIjhiaHhjaIiKQogICAgICAgIHJldHVybgoKICAgIGlmIHJvdW5kX2lkIG5vdCBpbiByb3VuZHM6CiAgICAgICAgYXdh"
    "aXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4p2MIOGKpeGKleGLsOGLmuGIhSDhi6vhiIgg4YuZ4YitIOGKoOGIjeGJsOGM"
    "iOGKmOGIneGNoiIpCiAgICAgICAgcmV0dXJuCgogICAgciA9IHJvdW5kc1tyb3VuZF9pZF0KICAgIHRpY2tldHMgPSByWyJ0aWNr"
    "ZXRzIl0KICAgIGlmIG5vdCB0aWNrZXRzOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoZiLihLnvuI8g"
    "4Ymg4YuZ4YitIHtyb3VuZF9pZH0g4Yid4YqV4YidIOGJsuGKrOGJtSDhi6jhiIjhiJ3hjaIiKQogICAgICAgIHJldHVybgoKICAg"
    "IGxpbmVzID0gW2Yi8J+XgiA8Yj7hiIHhiInhiJ0g4Ymy4Yqs4Ym24Ym9IC0g4YuZ4YitIHtyb3VuZF9pZH08L2I+XG4iXQogICAg"
    "c29sZF9jb3VudCA9IDAKICAgIHBlbmRpbmdfY291bnQgPSAwCiAgICBhdmFpbGFibGVfY291bnQgPSAwCgogICAgZm9yIHRfbnVt"
    "IGluIHNvcnRlZCh0aWNrZXRzKToKICAgICAgICB0ID0gdGlja2V0c1t0X251bV0KICAgICAgICBzdGF0dXMgPSB0LmdldCgic3Rh"
    "dHVzIiwgIkFWQUlMQUJMRSIpCiAgICAgICAgaWYgc3RhdHVzID09ICJTT0xEIjoKICAgICAgICAgICAgc29sZF9jb3VudCArPSAx"
    "CiAgICAgICAgICAgIHBob25lID0gbWFza19waG9uZV9sYXN0Myh0LmdldCgiYnV5ZXJfcGhvbmUiKSkKICAgICAgICAgICAgdmlh"
    "ID0gIvCfk54g4Ymg4Yi14YiN4YqtIiBpZiB0LmdldCgicmVmIikgPT0gIk1BTlVBTC1QSE9ORS1TQUxFIiBlbHNlICLwn6SWIOGJ"
    "oOGJpuGJtSIKICAgICAgICAgICAgaWYgdC5nZXQoInNoYXJlZCIpOgogICAgICAgICAgICAgICAgcGhvbmUyID0gbWFza19waG9u"
    "ZV9sYXN0Myh0LmdldCgiYnV5ZXJfcGhvbmUyIikpCiAgICAgICAgICAgICAgICBsaW5lcy5hcHBlbmQoZiLwn5+jICN7dF9udW19"
    "IOKAlCDhiIgyIOGIsOGLjSDhjI3hiJvhiL0g4YuL4YyLIOKAlCB7cGhvbmV9ICYge3Bob25lMn0gW3t2aWF9XSIpCiAgICAgICAg"
    "ICAgIGVsc2U6CiAgICAgICAgICAgICAgICBsaW5lcy5hcHBlbmQoZiLwn5S0ICN7dF9udW19IOKAlCDhibDhiLjhjKfhiI0g4oCU"
    "IHtwaG9uZX0gW3t2aWF9XSIpCiAgICAgICAgZWxpZiBzdGF0dXMgPT0gIlBFTkRJTkciOgogICAgICAgICAgICBwZW5kaW5nX2Nv"
    "dW50ICs9IDEKICAgICAgICAgICAgYnV5ZXJfaWQgPSB0LmdldCgidXNlcl9pZCIpCiAgICAgICAgICAgIHBsYXllciA9IHBsYXll"
    "cnMuZ2V0KGJ1eWVyX2lkLCB7fSkgaWYgYnV5ZXJfaWQgZWxzZSB7fQogICAgICAgICAgICBwaG9uZSA9IG1hc2tfcGhvbmVfbGFz"
    "dDMocGxheWVyLmdldCgicGhvbmUiKSkKICAgICAgICAgICAgbGluZXMuYXBwZW5kKGYi8J+foSAje3RfbnVtfSDigJQg4Ymg4YiC"
    "4Yuw4Ym1IOGIi+GLrSDigJQge3Bob25lfSIpCiAgICAgICAgZWxzZToKICAgICAgICAgICAgYXZhaWxhYmxlX2NvdW50ICs9IDEK"
    "ICAgICAgICAgICAgbGluZXMuYXBwZW5kKGYi8J+foiAje3RfbnVtfSDigJQg4Yqg4YiN4Ymw4Yi44Yyg4YidIikKCiAgICBsaW5l"
    "cy5hcHBlbmQoCiAgICAgICAgZiJcbvCfk4og4Yyg4YmF4YiL4YiL4Y2mIHtyWydudW1fdGlja2V0cyddfSAg8J+UtCDhibDhiLjh"
    "jKfhiI3hjaYge3NvbGRfY291bnR9ICDwn5+hIOGJoOGIguGLsOGJtSDhiIvhi63hjaYge3BlbmRpbmdfY291bnR9ICDwn5+iIOGL"
    "q+GIjeGJsOGLq+GLmOGNpiB7YXZhaWxhYmxlX2NvdW50fSIKICAgICkKICAgIGxpbmVzLmFwcGVuZChmIvCfkrUg4Yyg4YmF4YiL"
    "4YiLIOGMiOGJoiAo4YyN4Yid4Ym1KeGNpiB7c29sZF9jb3VudCAqIHJbJ3ByaWNlJ106LjJmfSDhiaXhiK0iKQoKICAgIGF3YWl0"
    "IF9zZW5kX2NodW5rZWQodXBkYXRlLCBsaW5lcykKCmFzeW5jIGRlZiBzb2xkX2xpc3QodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6"
    "IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4Yqg4Yu14Yia4YqVIOGLqOGJsOGIuOGMoSDhibLhiqzhibbhib3h"
    "ipUg4YiB4YiJIOGLneGIreGLneGIrSAo4Yi14YidL+GIteGIjeGKrS/hiqXhipXhi7ThibUg4Yql4YqV4Yuw4Ymw4Yi44YygKSDh"
    "i6jhiJrhi6vhi63hiaDhibUg4Ym14YuV4Yub4YudCiAgICDhiqDhjKDhiYPhiYDhiJ3hjaYgL3NvbGQgPOGLmeGIrT4iIiIKICAg"
    "IGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAgICBhcmdzID0gY29u"
    "dGV4dC5hcmdzCiAgICBpZiBub3QgYXJnczoKICAgICAgICBzb2xkX3JvdW5kX2lkcyA9IFtyaWQgZm9yIHJpZCwgciBpbiByb3Vu"
    "ZHMuaXRlbXMoKSBpZiBhbnkodFsic3RhdHVzIl0gPT0gIlNPTEQiIGZvciB0IGluIHJbInRpY2tldHMiXS52YWx1ZXMoKSldCiAg"
    "ICAgICAgYXdhaXQgX3NlbmRfcm91bmRfcGlja2VyKAogICAgICAgICAgICB1cGRhdGUsIGNvbnRleHQsICJzb2xkIiwKICAgICAg"
    "ICAgICAgc29sZF9yb3VuZF9pZHMgaWYgc29sZF9yb3VuZF9pZHMgZWxzZSBsaXN0KHJvdW5kcy5rZXlzKCkpLAogICAgICAgICAg"
    "ICAi8J+UtCDhi6jhi6jhibXhipvhi40g4YuZ4YitIOGLqOGJsOGIuOGMoSDhibLhiqzhibbhib3hipUg4Yib4Yuo4Ym1IOGLreGN"
    "iOGIjeGMi+GIiT8iLAogICAgICAgICAgICAi4oS577iPIOGJoOGKoOGIgeGKkSDhiLDhi5PhibUg4Yid4YqV4YidIOGLmeGIrSDh"
    "i6jhiIjhiJ3hjaIiCiAgICAgICAgKQogICAgICAgIHJldHVybgoKICAgIHRyeToKICAgICAgICByb3VuZF9pZCA9IGludChhcmdz"
    "WzBdKQogICAgZXhjZXB0IFZhbHVlRXJyb3I6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4pqg77iP"
    "IOGJteGKreGKreGIiOGKmyDhi6jhi5nhiK0g4YmB4Yyl4YitIOGLq+GIteGMiOGJoeGNoiIpCiAgICAgICAgcmV0dXJuCgogICAg"
    "aWYgcm91bmRfaWQgbm90IGluIHJvdW5kczoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLinYwg4Yql"
    "4YqV4Yuw4Yua4YiFIOGLq+GIiCDhi5nhiK0g4Yqg4YiN4Ymw4YyI4YqY4Yid4Y2iIikKICAgICAgICByZXR1cm4KCiAgICByID0g"
    "cm91bmRzW3JvdW5kX2lkXQogICAgc29sZCA9IHtpOiB0IGZvciBpLCB0IGluIHJbInRpY2tldHMiXS5pdGVtcygpIGlmIHRbInN0"
    "YXR1cyJdID09ICJTT0xEIn0KCiAgICBpZiBub3Qgc29sZDoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0"
    "KGYi4oS577iPIOGJoOGLmeGIrSB7cm91bmRfaWR9IOGKpeGIteGKq+GIgeGKlSDhiJ3hipXhiJ0g4Yuo4Ymw4Yi44YygIOGJgeGM"
    "peGIrSDhi6jhiIjhiJ3hjaIiKQogICAgICAgIHJldHVybgoKICAgIGxpbmVzID0gW2Yi8J+OnyA8Yj7hi6jhibDhiLjhjKEg4Ymy"
    "4Yqs4Ym24Ym9IC0g4YuZ4YitIHtyb3VuZF9pZH08L2I+XG4iXQogICAgZm9yIHRfbnVtIGluIHNvcnRlZChzb2xkKToKICAgICAg"
    "ICB0ID0gc29sZFt0X251bV0KICAgICAgICBuYW1lID0gdC5nZXQoImJ1eWVyX25hbWUiKSBvciAiTi9BIgogICAgICAgIHBob25l"
    "ID0gdC5nZXQoImJ1eWVyX3Bob25lIikgb3IgIk4vQSIKICAgICAgICB2aWEgPSAi8J+TniDhiaDhiLXhiI3hiq0iIGlmIHQuZ2V0"
    "KCJyZWYiKSA9PSAiTUFOVUFMLVBIT05FLVNBTEUiIGVsc2UgIvCfpJYg4Ymg4Ymm4Ym1IgogICAgICAgIGxpbmVzLmFwcGVuZChm"
    "IuKWqu+4jyAje3RfbnVtfSDigJQge25hbWV9ICh7cGhvbmV9KSBbe3ZpYX1dIikKCiAgICB0b3RhbCA9IGxlbihzb2xkKQogICAg"
    "bGluZXMuYXBwZW5kKGYiXG7wn5OKIOGMoOGJheGIi+GIiyDhi6jhibDhiLjhjKEg4YmB4Yyl4Yiu4Ym94Y2mIHt0b3RhbH0ve3Jb"
    "J251bV90aWNrZXRzJ119IikKICAgIGxpbmVzLmFwcGVuZChmIvCfkrUg4Yyg4YmF4YiL4YiLIOGMiOGJoiAo4YyN4Yid4Ym1KeGN"
    "piB7dG90YWwgKiByWydwcmljZSddOi4yZn0g4Yml4YitIikKCiAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCJc"
    "biIuam9pbihsaW5lcyksIHBhcnNlX21vZGU9IkhUTUwiKQoKYXN5bmMgZGVmIHVuc29sZF9saXN0KHVwZGF0ZTogVXBkYXRlLCBj"
    "b250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIuGKoOGLteGImuGKlSDhi6vhiI3hibDhiLjhjKEgKEFW"
    "QUlMQUJMRSkg4Ymy4Yqs4Ym24Ym94YqVIOGIgeGIiSDhi53hiK3hi53hiK0g4Yuo4Yia4Yur4Yut4Ymg4Ym1IOGJteGLleGLm+GL"
    "nQogICAg4Yqg4Yyg4YmD4YmA4Yid4Y2mIC91bnNvbGQgPOGLmeGIrT4iIiIKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNl"
    "ci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICByZXR1cm4KCiAgICBhcmdzID0gY29udGV4dC5hcmdzCiAgICBpZiBub3QgYXJnczoK"
    "ICAgICAgICB1bnNvbGRfcm91bmRfaWRzID0gW3JpZCBmb3IgcmlkLCByIGluIHJvdW5kcy5pdGVtcygpIGlmIGFueSh0WyJzdGF0"
    "dXMiXSA9PSAiQVZBSUxBQkxFIiBmb3IgdCBpbiByWyJ0aWNrZXRzIl0udmFsdWVzKCkpXQogICAgICAgIGF3YWl0IF9zZW5kX3Jv"
    "dW5kX3BpY2tlcigKICAgICAgICAgICAgdXBkYXRlLCBjb250ZXh0LCAidW5zb2xkIiwKICAgICAgICAgICAgdW5zb2xkX3JvdW5k"
    "X2lkcyBpZiB1bnNvbGRfcm91bmRfaWRzIGVsc2UgbGlzdChyb3VuZHMua2V5cygpKSwKICAgICAgICAgICAgIvCfn6Ig4Yuo4Yuo"
    "4Ym14Yqb4YuNIOGLmeGIrSDhi6vhiI3hibDhiLjhjKEg4Ymy4Yqs4Ym24Ym94YqVIOGIm+GLqOGJtSDhi63hjYjhiI3hjIvhiIk/"
    "IiwKICAgICAgICAgICAgIuKEue+4jyDhiaDhiqDhiIHhipEg4Yiw4YuT4Ym1IOGIneGKleGInSDhi5nhiK0g4Yuo4YiI4Yid4Y2i"
    "IgogICAgICAgICkKICAgICAgICByZXR1cm4KCiAgICB0cnk6CiAgICAgICAgcm91bmRfaWQgPSBpbnQoYXJnc1swXSkKICAgIGV4"
    "Y2VwdCBWYWx1ZUVycm9yOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKaoO+4jyDhibXhiq3hiq3h"
    "iIjhipsg4Yuo4YuZ4YitIOGJgeGMpeGIrSDhi6vhiLXhjIjhiaHhjaIiKQogICAgICAgIHJldHVybgoKICAgIGlmIHJvdW5kX2lk"
    "IG5vdCBpbiByb3VuZHM6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4p2MIOGKpeGKleGLsOGLmuGI"
    "hSDhi6vhiIgg4YuZ4YitIOGKoOGIjeGJsOGMiOGKmOGIneGNoiIpCiAgICAgICAgcmV0dXJuCgogICAgciA9IHJvdW5kc1tyb3Vu"
    "ZF9pZF0KICAgIHVuc29sZCA9IHtpOiB0IGZvciBpLCB0IGluIHJbInRpY2tldHMiXS5pdGVtcygpIGlmIHRbInN0YXR1cyJdID09"
    "ICJBVkFJTEFCTEUifQoKICAgIGlmIG5vdCB1bnNvbGQ6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dChm"
    "IuKEue+4jyDhiaDhi5nhiK0ge3JvdW5kX2lkfSDhiIvhi60g4Yur4YiN4Ymw4Yi44YygIOGJgeGMpeGIrSDhi6jhiIjhiJ0gKOGI"
    "geGIieGInSDhibDhi63hi5jhi4vhiI0v4Ymw4Yi44Yyg4YuL4YiNKeGNoiIpCiAgICAgICAgcmV0dXJuCgogICAgbnVtcyA9IHNv"
    "cnRlZCh1bnNvbGQua2V5cygpKQogICAgdG90YWwgPSBsZW4obnVtcykKICAgIGxpbmVzID0gW2Yi8J+foiA8Yj7hi6vhiI3hibDh"
    "iLjhjKEg4Ymy4Yqs4Ym24Ym9IC0g4YuZ4YitIHtyb3VuZF9pZH08L2I+XG4iXQogICAgY2h1bmsgPSAxMAogICAgZm9yIGkgaW4g"
    "cmFuZ2UoMCwgdG90YWwsIGNodW5rKToKICAgICAgICBsaW5lcy5hcHBlbmQoIiwgIi5qb2luKHN0cihuKSBmb3IgbiBpbiBudW1z"
    "W2k6aSArIGNodW5rXSkpCiAgICBsaW5lcy5hcHBlbmQoZiJcbvCfk4og4Yyg4YmF4YiL4YiLIOGLq+GIjeGJsOGIuOGMoSDhiYHh"
    "jKXhiK7hib3hjaYge3RvdGFsfS97clsnbnVtX3RpY2tldHMnXX0iKQoKICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3Rl"
    "eHQoIlxuIi5qb2luKGxpbmVzKSwgcGFyc2VfbW9kZT0iSFRNTCIpCgphc3luYyBkZWYgcGxheWVyc19jb21tYW5kKHVwZGF0ZTog"
    "VXBkYXRlLCBjb250ZXh0OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgIGlmIHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNl"
    "ci5pZCAhPSBBRE1JTl9JRDoKICAgICAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KCLwn5qrIOGLreGIhSDhiq3h"
    "jY3hiI0g4YiI4Yqg4Yi14Ymw4Yuz4Yuz4YiqIOGJpeGJuyDhipDhi43hjaIiKQogICAgICAgIHJldHVybgogICAgaWYgbm90IHBs"
    "YXllcnM6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4oS577iPIOGKpeGIteGKq+GIgeGKlSDhi6jh"
    "ibDhiJjhi5jhjIjhiaAg4Ymw4Yyr4YuL4Ym9IOGLqOGIiOGIneGNoiIsIHJlcGx5X21hcmt1cD1hZG1pbl9rZXlib2FyZCgpKQog"
    "ICAgICAgIHJldHVybgogICAgbGluZXM9W2Yi8J+RpSA8Yj7hi6jhibDhiJjhi5jhjIjhiaEg4Ymw4Yyr4YuL4Ym+4Ym94Y2mIHts"
    "ZW4ocGxheWVycyl9PC9iPlxuIl0KICAgIGZvciBuLCh1aWQsaW5mbykgaW4gZW51bWVyYXRlKHNvcnRlZChwbGF5ZXJzLml0ZW1z"
    "KCksIGtleT1sYW1iZGEga3Y6IGt2WzFdLmdldCgnbmFtZScsJycpLmxvd2VyKCkpLDEpOgogICAgICAgIHVzZXJuYW1lPWluZm8u"
    "Z2V0KCd1c2VybmFtZScpIG9yICfigJQnCiAgICAgICAgbGluZXMuYXBwZW5kKGYie259LiDwn5GkIDxiPntpbmZvLmdldCgnbmFt"
    "ZScsJ04vQScpfTwvYj5cbiAgIPCfk7Ege2luZm8uZ2V0KCdwaG9uZScpIG9yICdOL0EnfVxuICAg8J+UlyBAe3VzZXJuYW1lfVxu"
    "ICAg8J+GlCB7dWlkfSIpCiAgICB0ZXh0PSJcbiIuam9pbihsaW5lcykKICAgIGZvciBpIGluIHJhbmdlKDAsbGVuKHRleHQpLDM1"
    "MDApOgogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQodGV4dFtpOmkrMzUwMF0sIHBhcnNlX21vZGU9IkhU"
    "TUwiLCByZXBseV9tYXJrdXA9YWRtaW5fa2V5Ym9hcmQoKSBpZiBpKzM1MDA+PWxlbih0ZXh0KSBlbHNlIE5vbmUpCgphc3luYyBk"
    "ZWYgZ2FtZV9zdGF0cyh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiLh"
    "iIHhiInhipXhiJ0g4YuZ4Yiu4Ym9ICjhipXhiYEg4Yql4YqTIOGLqOGJsOGLmOGMiSkg4Yuo4Yia4Yur4Yyg4YmD4YiN4YiNIOGJ"
    "teGLleGLm+GLnSIiIgogICAgaWYgdXBkYXRlLm1lc3NhZ2UuZnJvbV91c2VyLmlkICE9IEFETUlOX0lEOgogICAgICAgIHJldHVy"
    "bgoKICAgIGlmIG5vdCByb3VuZHM6CiAgICAgICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgi4oS577iPIOGKpeGI"
    "teGKq+GIgeGKlSDhiJ3hipXhiJ0g4YuZ4YitIOGKoOGIjeGJsOGKqOGNiOGJsOGIneGNoiAvbmV3cm91bmQg4Yut4Yyg4YmA4YiZ"
    "4Y2iIikKICAgICAgICByZXR1cm4KCiAgICBsaW5lcyA9IFsi8J+TiiA8Yj7hi6jhiIHhiInhiJ0g4YuZ4Yiu4Ym9IOGIm+GMoOGJ"
    "g+GIiOGLqzwvYj5cbiJdCiAgICBncmFuZF90b3RhbF9yZXZlbnVlID0gMC4wCgogICAgZm9yIHJpZCBpbiBzb3J0ZWQocm91bmRz"
    "KToKICAgICAgICByID0gcm91bmRzW3JpZF0KICAgICAgICBhdmFpbGFibGUgPSBzdW0oMSBmb3IgdCBpbiByWyJ0aWNrZXRzIl0u"
    "dmFsdWVzKCkgaWYgdFsic3RhdHVzIl0gPT0gIkFWQUlMQUJMRSIpCiAgICAgICAgcGVuZGluZyA9IHN1bSgxIGZvciB0IGluIHJb"
    "InRpY2tldHMiXS52YWx1ZXMoKSBpZiB0WyJzdGF0dXMiXSA9PSAiUEVORElORyIpCiAgICAgICAgc29sZCA9IHN1bSgxIGZvciB0"
    "IGluIHJbInRpY2tldHMiXS52YWx1ZXMoKSBpZiB0WyJzdGF0dXMiXSA9PSAiU09MRCIpCiAgICAgICAgcmV2ZW51ZSA9IHNvbGQg"
    "KiByWyJwcmljZSJdCiAgICAgICAgZ3JhbmRfdG90YWxfcmV2ZW51ZSArPSByZXZlbnVlCgogICAgICAgIHN0YXR1c19pY29uID0g"
    "eyJPUEVOIjogIvCfn6IiLCAiUEFVU0VEIjogIuKPuCIsICJDTE9TRUQiOiAi8J+UkiJ9LmdldChyWyJzdGF0dXMiXSwgIvCflJIi"
    "KQogICAgICAgIGxpbmVzLmFwcGVuZCgKICAgICAgICAgICAgZiJ7c3RhdHVzX2ljb259IDxiPntyb3VuZF9sYWJlbChyaWQpfTwv"
    "Yj4gKHtyWydzdGF0dXMnXX0pIOKAlCB7clsncHJpY2UnXTouMGZ9IOGJpeGIrS/hibLhiqzhibVcbiIKICAgICAgICAgICAgZiIg"
    "ICDwn5+iIOGLq+GIjeGJsOGLq+GLmeGNpiB7YXZhaWxhYmxlfSB8IPCfn6Eg4Ymw4Yut4YuY4YuL4YiN4Y2mIHtwZW5kaW5nfSB8"
    "IPCflLQg4Ymw4Yi44Yyg4YuL4YiN4Y2mIHtzb2xkfS97clsnbnVtX3RpY2tldHMnXX1cbiIKICAgICAgICAgICAgZiIgICDwn5K1"
    "IOGMiOGJoiAo4YyN4Yid4Ym1KeGNpiB7cmV2ZW51ZTouMmZ9IOGJpeGIrSIKICAgICAgICApCgogICAgbGluZXMuYXBwZW5kKGYi"
    "XG7wn5GlIOGMoOGJheGIi+GIiyDhi6jhibDhiJjhi5jhjIjhiaEg4Ymw4Yyr4YuL4Ym+4Ym94Y2mIHtsZW4ocGxheWVycyl9IikK"
    "ICAgIGxpbmVzLmFwcGVuZChmIvCfkrAg4Yyg4YmF4YiL4YiLIOGMiOGJoiAo4YiB4YiJ4YidIOGLmeGIruGJvSnhjaYge2dyYW5k"
    "X3RvdGFsX3JldmVudWU6LjJmfSDhiaXhiK0iKQoKICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIlxuIi5qb2lu"
    "KGxpbmVzKSwgcGFyc2VfbW9kZT0iSFRNTCIpCgphc3luYyBkZWYgaG9zdF9wcm9maWxlKHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0"
    "OiBDb250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIuGLqOGIhuGIteGJtSDhiJjhjIjhiIjhjKvhjaYg4Yi14Yid4Y2j"
    "IOGLqOGKreGIrOGLsuGJtSDhiJLhiLPhiaXhjaMg4Yqu4Yia4Yi94YqVIOGImOGMoOGKleGNoyDhiqDhjKDhiYPhiIvhi60g4Yi9"
    "4Yur4YytIC0g4YiI4YiG4Yi14Ym1IOGKoOGLteGImuGKlSDhiqXhipMg4YiIU3VwZXIgQWRtaW4g4Yml4Ym7IiIiCiAgICB1aWQg"
    "PSB1cGRhdGUubWVzc2FnZS5mcm9tX3VzZXIuaWQKICAgIGlmIHVpZCAhPSBBRE1JTl9JRCBhbmQgdWlkICE9IFNVUEVSX0FETUlO"
    "X0lEOgogICAgICAgIHJldHVybgoKICAgIHN0YXRzID0gX2hvc3RfYXBpX3N0YXRzKCkKICAgIGlmIGhvc3RfcGF1c2VkOgogICAg"
    "ICAgIHN0YXR1c19saW5lID0gKAogICAgICAgICAgICAi4o+477iPIDxiPuGJhuGIn+GIjSAo4Yqt4Yis4Yuy4Ym1IOGIteGIi+GI"
    "iOGJgCk8L2I+IiBpZiBob3N0X3BhdXNlZF9yZWFzb24gPT0gImNyZWRpdCIKICAgICAgICAgICAgZWxzZSAi4o+477iPIDxiPuGJ"
    "huGIn+GIjSAo4Ymg4Yib4YuV4Yqo4YiL4YuKIOGJsOGJhuGMo+GMo+GIqik8L2I+IgogICAgICAgICkKICAgIGVsc2U6CiAgICAg"
    "ICAgc3RhdHVzX2xpbmUgPSAi8J+foiA8Yj7hipXhiYEg4YqQ4YuNICjhi63hiLjhjKPhiI0pPC9iPiIKCiAgICBjb21taXNzaW9u"
    "X2xpbmUgPSBmIvCfk4og4Yuo4Yqu4Yia4Yi94YqVIOGImOGMoOGKleGNpiB7SE9TVF9DT01NSVNTSU9OX1BFUkNFTlQ6LjFmfSUg"
    "4Ymg4Yql4Yur4YqV4Yuz4YqV4YuxIOGJsuGKrOGJtVxuXG4iIGlmIHVpZCA9PSBTVVBFUl9BRE1JTl9JRCBlbHNlICIiCiAgICB0"
    "ZXh0ID0gKAogICAgICAgIGYi8J+PoCA8Yj7hi6jhiIbhiLXhibUg4YiY4YyI4YiI4YyrIOKAlCB7SE9TVF9OQU1FfTwvYj5cblxu"
    "IgogICAgICAgIGYi8J+TtiDhiIHhipThibPhjaYge3N0YXR1c19saW5lfVxuXG4iCiAgICAgICAgZiLwn5KzIDxiPuGKreGIrOGL"
    "suGJtSDhiJLhiLPhiaXhjaY8L2I+IHtob3N0X2NyZWRpdFsnYmFsYW5jZSddOi4yZn0g4Yml4YitXG4iCiAgICAgICAgZiLwn5OJ"
    "IOGKpeGIteGKq+GIgeGKlSDhi6jhibDhiYDhipDhiLAg4Yqu4Yia4Yi94YqV4Y2mIHtob3N0X2NyZWRpdC5nZXQoJ3RvdGFsX2Rl"
    "ZHVjdGVkJywgMC4wKTouMmZ9IOGJpeGIrVxuIgogICAgICAgIGYie2NvbW1pc3Npb25fbGluZX0iCiAgICAgICAgZiLwn46yIOGM"
    "oOGJheGIi+GIiyDhi5nhiK7hib3hjaYge3N0YXRzWyd0b3RhbF9yb3VuZHMnXX0gKPCfn6Ige3N0YXRzWydvcGVuX3JvdW5kcydd"
    "fSB8IOKPuCB7c3RhdHNbJ3BhdXNlZF9yb3VuZHMnXX0gfCDwn5SSIHtzdGF0c1snY2xvc2VkX3JvdW5kcyddfSlcbiIKICAgICAg"
    "ICBmIvCfjp/vuI8g4Ymy4Yqs4Ym24Ym94Y2mIPCfn6Ige3N0YXRzWydhdmFpbGFibGVfdGlja2V0cyddfSB8IPCfn6Ege3N0YXRz"
    "WydwZW5kaW5nX3RpY2tldHMnXX0gfCDwn5S0IHtzdGF0c1snc29sZF90aWNrZXRzJ119IC8ge3N0YXRzWyd0b3RhbF90aWNrZXRz"
    "J119XG4iCiAgICAgICAgZiLwn5GlIOGLqOGJsOGImOGLmOGMiOGJoSDhibDhjKvhi4vhib7hib3hjaYge3N0YXRzWydyZWdpc3Rl"
    "cmVkX3BsYXllcnMnXX1cbiIKICAgICAgICBmIvCfkrUg4Yyg4YmF4YiL4YiLIOGIveGLq+GMrSAo4YyI4YmiKeGNpiB7c3RhdHNb"
    "J3NhbGVzJ106LjJmfSDhiaXhiK1cbiIKICAgICkKICAgIGlmIGhvc3RfY3JlZGl0WydiYWxhbmNlJ10gPD0gMDoKICAgICAgICB0"
    "ZXh0ICs9ICJcbvCfmqgg4Yqt4Yis4Yuy4Ym1IOGKoOGIjeGJi+GIjSEg4Yi94Yur4YytIOGIiOGImOGJgOGMoOGIjSAvYWRkY3Jl"
    "ZGl0IOGJsOGMoOGJheGImOGLjSDhiJLhiLPhiaUg4Yut4YiZ4YiJ4Y2iIgogICAgYXdhaXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlf"
    "dGV4dCh0ZXh0LCBwYXJzZV9tb2RlPSJIVE1MIikKCmRlZiBfZWRpdGFibGVfcGF5bWVudF9tZXRob2RzKCk6CiAgICAiIiLhibDh"
    "jKvhi4vhib7hib0g4YiI4YiG4Yi14YmxIOGMiOGKleGLmOGJpSDhi6jhiJrhiI3hiqnhiaPhibjhi43hjaMg4YmgL2VkaXRwYXlt"
    "ZW50IOGIiuGIteGJsOGKq+GKqOGIiSDhi6jhiJrhib3hiIkg4Yuo4Yqt4Y2N4YurIOGLmOGLtOGLjuGJvSAobWFudWFsX2NhbGwg"
    "4Yqg4Yut4Yqr4Ymw4Ym14YidKSIiIgogICAgcmV0dXJuIHtrOiBtIGZvciBrLCBtIGluIFBBWU1FTlRfTUVUSE9EUy5pdGVtcygp"
    "IGlmIGsgaW4gKCJ0ZWxlYmlyciIsICJjYmViaXJyIil9Cgphc3luYyBkZWYgZWRpdF9wYXltZW50X2FjY291bnQodXBkYXRlOiBV"
    "cGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIiL2VkaXRwYXltZW50IC0g4Yuo4Ym04YiM"
    "4Yml4YitL+GIsuGJouGKoiDhiaXhiK0g4Yqg4Yqr4YuN4YqV4Ym1IOGJgeGMpeGIrSAo4Yi14YiN4YqtIOGJgeGMpeGIrSkg4Yql"
    "4YqTIOGLqOGJo+GIiOGJpOGJtSDhiLXhiJ0g4YiI4Yib4Yi14Ymw4Yqr4Yqo4YiNIOGLsOGIqOGMgy3hiaDhi7DhiKjhjIMg4YiC"
    "4Yuw4Ym1CiAgICDhi6jhiJrhjIDhiJ3hiK0g4Ym14YuV4Yub4YudICjhiIjhiIbhiLXhibUg4Yqg4Yu14Yia4YqVIOGLiOGLreGI"
    "nSBTdXBlciBBZG1pbiDhiaXhibsp4Y2iIOGJsOGMq+GLi+GJvuGJvSDhi4jhi7Dhi5rhiIUg4Yqg4Yqr4YuN4YqV4Ym1IOGMiOGK"
    "leGLmOGJpSDhiI3hiqjhi40g4Ymy4Yqs4Ym1L+GKreGIrOGLsuGJtSDhi63hjIjhi5vhiInhjaMKICAgIOGIteGIiOGLmuGIhSDh"
    "iqXhi5rhiIUg4Yuo4Yia4Yi14Ymw4Yqr4Yqo4YiI4YuNIOGJgeGMpeGIrSDhi4jhi7Lhi6vhi43hipEg4YmgwqvhjI3hi6LCuyDh"
    "jIjhjL0g4YiL4YutIOGIiOGJsOGMq+GLi+GJvuGJvSDhi63hibPhi6vhiI3hjaIKICAgIOGKoOGMoOGJg+GJgOGIneGNpiAvZWRp"
    "dHBheW1lbnQiIiIKICAgIHVpZCA9IHVwZGF0ZS5tZXNzYWdlLmZyb21fdXNlci5pZAogICAgaWYgdWlkICE9IEFETUlOX0lEIGFu"
    "ZCB1aWQgIT0gU1VQRVJfQURNSU5fSUQ6CiAgICAgICAgcmV0dXJuCgogICAgY2xlYXJlZCA9IF9jYW5jZWxfb3RoZXJfYWRtaW5f"
    "dGFza3ModWlkLCBrZWVwPSJlZGl0cGF5bWVudCIpCiAgICBhd2FpdCBfbm90aWZ5X2NhbmNlbGxlZF90YXNrcyhjb250ZXh0LmJv"
    "dCwgdWlkLCBjbGVhcmVkKQoKICAgIG1ldGhvZHMgPSBfZWRpdGFibGVfcGF5bWVudF9tZXRob2RzKCkKICAgIGtiID0gWwogICAg"
    "ICAgIFtJbmxpbmVLZXlib2FyZEJ1dHRvbihmInttWydsYWJlbCddfSAoe21bJ2FjY291bnQnXX0pIiwgY2FsbGJhY2tfZGF0YT1m"
    "ImVkaXRwYXltZXRob2Rfe2tleX0iKV0KICAgICAgICBmb3Iga2V5LCBtIGluIG1ldGhvZHMuaXRlbXMoKQogICAgXQogICAgYXdh"
    "aXQgdXBkYXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAi8J+SsyA8Yj7hi6jhiq3hjY3hi6sg4Yqg4Yqr4YuN4YqV4Ym1"
    "IOGIm+GIteGJsOGKq+GKqOGLqzwvYj5cblxuIgogICAgICAgICLhi6jhibXhipvhi43hipUg4Yuo4Yqt4Y2N4YurIOGLmOGLtCDh"
    "iqDhiqvhi43hipXhibUv4Yi14YiN4YqtIOGJgeGMpeGIrSDhiqXhipMg4Ymj4YiI4Ymk4Ym1IOGIteGInSDhiJvhiLXhibDhiqvh"
    "iqjhiI0g4Yut4Y2I4YiN4YyL4YiJ4Y2mXG5cbiIKICAgICAgICAi4p2MIOGIiOGIm+GJi+GIqOGMpSAvY2FuY2VsIOGLreGIi+GK"
    "qeGNoiIsCiAgICAgICAgcGFyc2VfbW9kZT0iSFRNTCIsCiAgICAgICAgcmVwbHlfbWFya3VwPUlubGluZUtleWJvYXJkTWFya3Vw"
    "KGtiKSwKICAgICkKCmFzeW5jIGRlZiBoYW5kbGVfZWRpdF9wYXltZW50X21ldGhvZCh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDog"
    "Q29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiIvZWRpdHBheW1lbnQg4YiC4Yuw4Ym1IOGLjeGIteGMpSDhi6jhiq3h"
    "jY3hi6sg4YuY4Yu0IChUZWxlYmlyci9DQkUgQmlycikg4Yiy4YiY4Yio4YylIOGLqOGImuGLq+GLnSBjYWxsYmFjayIiIgogICAg"
    "cXVlcnkgPSB1cGRhdGUuY2FsbGJhY2tfcXVlcnkKICAgIHVpZCA9IHF1ZXJ5LmZyb21fdXNlci5pZAogICAgaWYgdWlkICE9IEFE"
    "TUlOX0lEIGFuZCB1aWQgIT0gU1VQRVJfQURNSU5fSUQ6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgICAgICByZXR1"
    "cm4KICAgIHRyeToKICAgICAgICBfLCBtZXRob2Rfa2V5ID0gcXVlcnkuZGF0YS5zcGxpdCgiXyIsIDEpCiAgICBleGNlcHQgVmFs"
    "dWVFcnJvcjoKICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIHJldHVybgoKICAgIG1ldGhvZCA9IFBBWU1FTlRf"
    "TUVUSE9EUy5nZXQobWV0aG9kX2tleSkKICAgIGlmIG5vdCBtZXRob2Qgb3IgbWV0aG9kX2tleSBub3QgaW4gKCJ0ZWxlYmlyciIs"
    "ICJjYmViaXJyIik6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgICAgICByZXR1cm4KCiAgICBlZGl0X3BheW1lbnRf"
    "c3RhdGVbdWlkXSA9IHsibWV0aG9kIjogbWV0aG9kX2tleSwgInN0ZXAiOiAiYWNjb3VudCJ9CiAgICBhd2FpdCBxdWVyeS5hbnN3"
    "ZXIoKQoKICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90ZXh0KAogICAgICAgIGYie21ldGhvZFsnZW1vamknXX0ge21ldGhv"
    "ZFsnbGFiZWwnXX0g4Ymw4YiY4Yit4Yyn4YiN4Y2iXG5cbiIKICAgICAgICBmIvCfk7Eg4Yuo4Yqg4YiB4YqRIOGIteGIjeGKrSDh"
    "iYHhjKXhiK0gKOGKoOGKq+GLjeGKleGJtSnhjaYgPGNvZGU+e21ldGhvZFsnYWNjb3VudCddfTwvY29kZT5cbiIKICAgICAgICBm"
    "IvCfkaQg4Yuo4Yqg4YiB4YqRIOGJo+GIiOGJpOGJtSDhiLXhiJ3hjaYge21ldGhvZFsnaG9sZGVyJ119XG5cbiIKICAgICAgICAi"
    "8J+UoiDhiqDhi7LhiLHhipUg4Yi14YiN4YqtIOGJgeGMpeGIrSAo4Yqg4Yqr4YuN4YqV4Ym1KSDhi6vhiLXhjIjhiaHhjaZcbiIK"
    "ICAgICAgICAi4YiI4Yid4Yiz4YiM4Y2mIDA5MTIzNDU2NzhcblxuIgogICAgICAgICLinYwg4YiI4Yib4YmL4Yio4YylIC9jYW5j"
    "ZWwg4Yut4YiL4Yqp4Y2iIiwKICAgICAgICBwYXJzZV9tb2RlPSJIVE1MIiwKICAgICkKCmFzeW5jIGRlZiBzZXRfc21zX3dlYmhv"
    "b2tfY29tbWFuZCh1cGRhdGU6IFVwZGF0ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiIvc2V0"
    "c21zd2ViaG9vayAtIOGLjeGMq+GLiiBVUkwg4YiI4Yib4YuL4YmA4YitIOGLqOGImuGLq+GIteGJveGIjSDhibXhi5Xhi5vhi53h"
    "jaYg4YuI4Yuw4Yua4YiFIOGJpuGJtSDhiIjhiq3hjY3hi6sg4Yib4Yio4YyL4YyI4YyrIOGLqOGImuGLsOGIreGItSDhiqXhi6vh"
    "ipXhi7PhipXhi7EKICAgIOGKpOGIteGKpOGIneGKpOGItSAo4Ymg4Ym04YiM4YyN4Yir4YidIOGNjuGIreGLi+GIreGLtSDhi4jh"
    "i63hiJ0g4YmgL3Ntcy13ZWJob29rIOGJoOGKqeGIjSDhiaLhiJjhjKPhiJ0pIOGLiOGLsOGLmuGIhSBVUkwg4YyI4YiN4Yml4Yym"
    "IChyZWxheSkg4Yut4YiL4Yqr4YiNCiAgICAo4YiI4YiG4Yi14Ym1IOGKoOGLteGImuGKlSDhi4jhi63hiJ0gU3VwZXIgQWRtaW4g"
    "4Yml4Ym7KeGNoiDhiqDhjKDhiYPhiYDhiJ3hjaYgL3NldHNtc3dlYmhvb2siIiIKICAgIHVpZCA9IHVwZGF0ZS5tZXNzYWdlLmZy"
    "b21fdXNlci5pZAogICAgaWYgdWlkICE9IEFETUlOX0lEIGFuZCB1aWQgIT0gU1VQRVJfQURNSU5fSUQ6CiAgICAgICAgcmV0dXJu"
    "CgogICAgY2xlYXJlZCA9IF9jYW5jZWxfb3RoZXJfYWRtaW5fdGFza3ModWlkLCBrZWVwPSJzZXRzbXN3ZWJob29rIikKICAgIGF3"
    "YWl0IF9ub3RpZnlfY2FuY2VsbGVkX3Rhc2tzKGNvbnRleHQuYm90LCB1aWQsIGNsZWFyZWQpCgogICAgc2V0X3Ntc193ZWJob29r"
    "X3N0YXRlLmFkZCh1aWQpCiAgICBjdXJyZW50ID0gKAogICAgICAgIGYi8J+UlyDhi6jhiqDhiIHhipEgVVJM4Y2mIDxjb2RlPntP"
    "VVRCT1VORF9TTVNfV0VCSE9PS19VUkx9PC9jb2RlPiIKICAgICAgICBpZiBPVVRCT1VORF9TTVNfV0VCSE9PS19VUkwgZWxzZSAi"
    "8J+UlyDhi6jhiqDhiIHhipEg4YiB4YqU4Ymz4Y2mIOGKoOGIjeGJsOGLi+GJgOGIqOGInSAo4Yid4YqV4YidIOGLjeGMq+GLiiB3"
    "ZWJob29rIOGLqOGIiOGInSkiCiAgICApCiAgICBhd2FpdCB1cGRhdGUubWVzc2FnZS5yZXBseV90ZXh0KAogICAgICAgICLwn5SX"
    "IDxiPuGLqFNNUyBXZWJob29rIFVSTCDhiJvhi4vhiYDhiKrhi6s8L2I+XG5cbiIKICAgICAgICBmIntjdXJyZW50fVxuXG4iCiAg"
    "ICAgICAgIuGLiOGLsOGLmuGIhSDhiabhibUg4YiI4Yqt4Y2N4YurIOGIm+GIqOGMi+GMiOGMqyDhi6jhiJrhi7DhiK3hiLEg4Yqk"
    "4Yi14Yqk4Yid4Yqk4Yi14YuO4Ym9ICjhjY7hiK3hi4vhiK3hi7Ug4Yuo4Ymw4Yuw4Yio4YyJ4YidIOGIhuGKkCDhiaBTTVMg4Y2O"
    "4Yit4YuL4Yit4Yu1IOGKoOGMiOGIjeGMjeGIjuGJtS93ZWJob29rICIKICAgICAgICAi4Yuo4YiY4YyhKSDhi4jhi7DhiJrhi6vh"
    "iLXhjIjhiaHhibUgVVJMIOGIq+GIsSAoUE9TVCDhiaBKU09OKSDhjIjhiI3hiaXhjKDhi40g4Yut4YiL4Yqr4YiJ4Y2iXG5cbiIK"
    "ICAgICAgICAi8J+UoiDhiqDhi7LhiLHhipUg4YiZ4YiJIGh0dHBzOi8vIFVSTCDhi6vhiLXhjIjhiaHhjaZcbiIKICAgICAgICAi"
    "4YiI4Yid4Yiz4YiM4Y2mIGh0dHBzOi8vZXhhbXBsZS5jb20vbXktd2ViaG9va1xuXG4iCiAgICAgICAgIvCfl5HvuI8g4YuN4Yyr"
    "4YuKIHdlYmhvb2sg4YiI4Yib4Yyl4Y2L4Ym1ICdvZmYnIOGLiOGLreGInSDCq+GKoOGMpeGNi8K7IOGJpeGIiOGLjSDhi63hiIvh"
    "iqnhjaJcbiIKICAgICAgICAi4p2MIOGIiOGIm+GJi+GIqOGMpSAvY2FuY2VsIOGLreGIi+GKqeGNoiIsCiAgICAgICAgcGFyc2Vf"
    "bW9kZT0iSFRNTCIsCiAgICApCgphc3luYyBkZWYgX3NlbmRfY3JlZGl0X3JlcXVlc3RfdG9fc2VsbGVyKGFtb3VudCwgbWV0aG9k"
    "LCByZWZlcmVuY2UsIG1lc3NhZ2UpOgogICAgIiIiUE9TVCB0byB0aGUgY3JlZGl0IHNlbGxlciBib3QncyBwdXNoIEFQSSBzbyBT"
    "dXBlciBBZG1pbiBhcHByb3Zlcy8KICAgIHJlamVjdHMgdGhlcmUsIGluc3RlYWQgb2YgY3JlZGl0aW5nIGluc3RhbnRseSB3aXRo"
    "IG5vIHZlcmlmaWNhdGlvbi4KICAgIFJldHVybnMgKG9rLCBpbmZvKSB3aGVyZSBpbmZvIGlzIHRoZSByZXF1ZXN0X2lkIG9uIHN1"
    "Y2Nlc3Mgb3IgYW4gZXJyb3IKICAgIG1lc3NhZ2Ugb24gZmFpbHVyZS4iIiIKICAgIGlmIG5vdCBDUkVESVRfU0VMTEVSX0hPU1Rf"
    "SUQ6CiAgICAgICAgcmV0dXJuIEZhbHNlLCAiQ1JFRElUX1NFTExFUl9IT1NUX0lEIGlzIG5vdCBjb25maWd1cmVkLiIKICAgIHRy"
    "eToKICAgICAgICBhc3luYyB3aXRoIGFpb2h0dHAuQ2xpZW50U2Vzc2lvbigpIGFzIHNlc3Npb246CiAgICAgICAgICAgIGFzeW5j"
    "IHdpdGggc2Vzc2lvbi5wb3N0KAogICAgICAgICAgICAgICAgQ1JFRElUX1NFTExFUl9BUElfVVJMLnJzdHJpcCgiLyIpICsgIi9h"
    "cGkvcmVxdWVzdF9jcmVkaXQiLAogICAgICAgICAgICAgICAgaGVhZGVycz17CiAgICAgICAgICAgICAgICAgICAgIlgtUmVxdWVz"
    "dC1BUEktS2V5IjogQ1JFRElUX1NFTExFUl9BUElfU0VDUkVULAogICAgICAgICAgICAgICAgICAgICJDb250ZW50LVR5cGUiOiAi"
    "YXBwbGljYXRpb24vanNvbiIsCiAgICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICAganNvbj17CiAgICAgICAgICAgICAg"
    "ICAgICAgImhvc3RfaWQiOiBDUkVESVRfU0VMTEVSX0hPU1RfSUQsCiAgICAgICAgICAgICAgICAgICAgImFtb3VudCI6IGFtb3Vu"
    "dCwKICAgICAgICAgICAgICAgICAgICAibWV0aG9kIjogbWV0aG9kLAogICAgICAgICAgICAgICAgICAgICJyZWZlcmVuY2UiOiBy"
    "ZWZlcmVuY2UsCiAgICAgICAgICAgICAgICAgICAgIm1lc3NhZ2UiOiBtZXNzYWdlLAogICAgICAgICAgICAgICAgfSwKICAgICAg"
    "ICAgICAgICAgIHRpbWVvdXQ9YWlvaHR0cC5DbGllbnRUaW1lb3V0KHRvdGFsPTE1KSwKICAgICAgICAgICAgKSBhcyByZXNwOgog"
    "ICAgICAgICAgICAgICAgdHJ5OgogICAgICAgICAgICAgICAgICAgIGRhdGEgPSBhd2FpdCByZXNwLmpzb24oY29udGVudF90eXBl"
    "PU5vbmUpCiAgICAgICAgICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICAgICAgICAgIGRhdGEgPSB7fQogICAg"
    "ICAgIGlmIHJlc3Auc3RhdHVzICE9IDIwMCBvciBub3QgZGF0YS5nZXQoIm9rIik6CiAgICAgICAgICAgIHJldHVybiBGYWxzZSwg"
    "ZGF0YS5nZXQoImVycm9yIiwgZiJIVFRQIHtyZXNwLnN0YXR1c30iKQogICAgICAgIHJldHVybiBUcnVlLCBkYXRhLmdldCgicmVx"
    "dWVzdF9pZCIpCiAgICBleGNlcHQgRXhjZXB0aW9uIGFzIGU6CiAgICAgICAgcmV0dXJuIEZhbHNlLCBmIkNvdWxkIG5vdCByZWFj"
    "aCBjcmVkaXQgc2VsbGVyIGJvdDoge2V9IgoKCmFzeW5jIGRlZiBhZGRfY3JlZGl0KHVwZGF0ZTogVXBkYXRlLCBjb250ZXh0OiBD"
    "b250ZXh0VHlwZXMuREVGQVVMVF9UWVBFKToKICAgICIiIi9hZGRjcmVkaXQgLSDhiIjhiIbhiLXhibEg4Yqt4Yis4Yuy4Ym1IOGI"
    "kuGIs+GJpSDhjIjhipXhi5jhiaUg4Yuo4Yia4Yyo4Yid4YitIOGJteGLleGLm+GLnSAo4Yuo4YiG4Yi14Ym1IOGKoOGLteGImuGK"
    "lSDhi4jhi63hiJ0gU3VwZXIgQWRtaW4g4Yml4Ym7IOGLreGJveGIi+GIiSnhjaIKICAgIOGLq+GIiCBhcmd1bWVudCDhiqjhibDh"
    "iIvhiqjhjaYg4YiY4Yyg4YqVIOKGkiDhi6jhiq3hjY3hi6sg4YuY4Yu0IOKGkiDhiKrhjYjhiKjhipXhiLUg4Yyg4Yut4YmGIOGJ"
    "oOGIq+GItS3hiLDhiK0gKGF1dG8tdmVyaWZ5KSDhi6jhiJrhi6vhiKjhjIvhjI3hjKUg4Yuw4Yio4YyDLeGJoOGLsOGIqOGMgyDh"
    "iILhi7DhibUg4Yut4YyA4Yid4Yir4YiNCiAgICAo4YiN4YqtIOGKpeGKleGLsCDhibLhiqzhibUg4YyN4YuiIOGIq+GItS3hiLDh"
    "iK0g4Yib4Yio4YyL4YyI4YyrKeGNoiBBcmd1bWVudCDhjIvhiK0g4Yqo4Ymw4YiL4YqoICgvYWRkY3JlZGl0IDUwMCnhjaYg4YuI"
    "4Yuy4Yur4YuN4YqRIOGKqOGImOGMqOGImOGIrSDhi63hiI3hiYUg4Yyl4Yur4YmE4YuNIOGLiOGLsAogICAg4Yqt4Yis4Yuy4Ym1"
    "IOGIu+GMrSDhiabhibUg4Ymw4YiN4YquIFN1cGVyIEFkbWluIOGKpeGLmuGLqyDhiqXhiLXhiqrhi6vhjLjhi7XhiYUv4YuN4Yu1"
    "4YmFIOGKpeGIteGKquGLq+GLsOGIreGMjSDhi7XhiKjhiLUg4Yut4Yyg4Yml4YmD4YiN4Y2iIiIiCiAgICB1aWQgPSB1cGRhdGUu"
    "bWVzc2FnZS5mcm9tX3VzZXIuaWQKICAgIGlmIHVpZCAhPSBBRE1JTl9JRCBhbmQgdWlkICE9IFNVUEVSX0FETUlOX0lEOgogICAg"
    "ICAgIHJldHVybgoKICAgIGNsZWFyZWQgPSBfY2FuY2VsX290aGVyX2FkbWluX3Rhc2tzKHVpZCwga2VlcD0iYWRkY3JlZGl0IikK"
    "ICAgIGF3YWl0IF9ub3RpZnlfY2FuY2VsbGVkX3Rhc2tzKGNvbnRleHQuYm90LCB1aWQsIGNsZWFyZWQpCgogICAgYXJncyA9IGNv"
    "bnRleHQuYXJncwogICAgaWYgYXJnczoKICAgICAgICB0cnk6CiAgICAgICAgICAgIGFtb3VudCA9IGZsb2F0KGFyZ3NbMF0pCiAg"
    "ICAgICAgZXhjZXB0IFZhbHVlRXJyb3I6CiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKaoO+4"
    "jyDhibXhiq3hiq3hiIjhipsg4Yuo4YyI4YqV4YuY4YmlIOGImOGMoOGKlSDhi6vhiLXhjIjhiaHhjaIg4YiI4Yid4Yiz4YiM4Y2m"
    "IC9hZGRjcmVkaXQgNTAwIikKICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgaWYgYW1vdW50IDw9IDA6CiAgICAgICAgICAgIGF3"
    "YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQoIuKaoO+4jyDhi6jhiJrhjKjhiJjhiKjhi40g4YiY4Yyg4YqVIOGKqOGLnOGI"
    "riDhiaDhiIvhi60g4YiY4YiG4YqVIOGKoOGIiOGJoOGJteGNoiIpCiAgICAgICAgICAgIHJldHVybgoKICAgICAgICBpZiBub3Qg"
    "Q1JFRElUX1NFTExFUl9QQVlNRU5UX01FVEhPRFM6CiAgICAgICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJlcGx5X3RleHQo"
    "CiAgICAgICAgICAgICAgICAi4pqg77iPIOGLqOGKreGIrOGLsuGJtSDhiLvhjK0gKFN1cGVyIEFkbWluKSDhi6jhiq3hjY3hi6sg"
    "4Yqg4Yqr4YuN4YqV4Ym1IOGMiOGKkyDhiqDhiI3hibDhi4vhiYDhiKjhiJ3hjaIgIgogICAgICAgICAgICAgICAgIlN1cGVyIEFk"
    "bWluIHN1cGVyYWRtaW5fYWxsX2luX29uZS5weSDhi43hiLXhjKUgU0VMTEVSX1RFTEVCSVJSX0FDQ09VTlQvIgogICAgICAgICAg"
    "ICAgICAgIlNFTExFUl9DQkVCSVJSX0FDQ09VTlQg4Yib4Yi14YmA4YiY4YylIOGKoOGIiOGJoOGJteGNoiIKICAgICAgICAgICAg"
    "KQogICAgICAgICAgICByZXR1cm4KCiAgICAgICAgY3JlZGl0X3RvcHVwX3N0YXRlW3VpZF0gPSB7InN0ZXAiOiAibWV0aG9kIiwg"
    "ImFtb3VudCI6IGFtb3VudH0KICAgICAgICBrYiA9IFsKICAgICAgICAgICAgW0lubGluZUtleWJvYXJkQnV0dG9uKG1bImxhYmVs"
    "Il0sIGNhbGxiYWNrX2RhdGE9ZiJhZGRjcmVkaXRtZXRob2Rfe2tleX0iKV0KICAgICAgICAgICAgZm9yIGtleSwgbSBpbiBDUkVE"
    "SVRfU0VMTEVSX1BBWU1FTlRfTUVUSE9EUy5pdGVtcygpCiAgICAgICAgXQogICAgICAgIGF3YWl0IHVwZGF0ZS5tZXNzYWdlLnJl"
    "cGx5X3RleHQoCiAgICAgICAgICAgIGYi8J+SsCDhiJjhjKDhipXhjaYgPGI+e2Ftb3VudDouMmZ9IOGJpeGIrTwvYj5cblxuIgog"
    "ICAgICAgICAgICAi8J+SsyDhi6jhiq3hjY3hi6sg4YuY4Yu0IOGLreGIneGIqOGMoeGNpiIsCiAgICAgICAgICAgIHBhcnNlX21v"
    "ZGU9IkhUTUwiLAogICAgICAgICAgICByZXBseV9tYXJrdXA9SW5saW5lS2V5Ym9hcmRNYXJrdXAoa2IpLAogICAgICAgICkKICAg"
    "ICAgICByZXR1cm4KCiAgICBjcmVkaXRfdG9wdXBfc3RhdGVbdWlkXSA9IHsic3RlcCI6ICJhbW91bnQifQogICAgYXdhaXQgdXBk"
    "YXRlLm1lc3NhZ2UucmVwbHlfdGV4dCgKICAgICAgICAi4p6VIDxiPuGKreGIrOGLsuGJtSDhjKXhi6vhiYQ8L2I+XG5cbiIKICAg"
    "ICAgICAi8J+SsCDhi6jhiJrhjKDhi63hiYHhibXhipUg4Yuo4YyI4YqV4YuY4YmlIOGImOGMoOGKlSDhiaAgRVRCIOGLq+GIteGM"
    "iOGJoeGNolxuXG4iCiAgICAgICAgZiLwn5KzIOGLqOGKoOGIgeGKkSDhiJLhiLPhiaXhjaYge2hvc3RfY3JlZGl0WydiYWxhbmNl"
    "J106LjJmfSDhiaXhiK1cbiIKICAgICAgICAi4p2MIOGIiOGIm+GJi+GIqOGMpSAvY2FuY2VsIOGLreGIi+GKqeGNoiIsCiAgICAg"
    "ICAgcGFyc2VfbW9kZT0iSFRNTCIKICAgICkKCmFzeW5jIGRlZiBoYW5kbGVfYWRkY3JlZGl0X21ldGhvZCh1cGRhdGU6IFVwZGF0"
    "ZSwgY29udGV4dDogQ29udGV4dFR5cGVzLkRFRkFVTFRfVFlQRSk6CiAgICAiIiIvYWRkY3JlZGl0IOGIguGLsOGJtSDhi43hiLXh"
    "jKUg4Yuo4Yqt4Y2N4YurIOGLmOGLtCAoVGVsZWJpcnIvQ0JFIEJpcnIvTWFudWFsKSDhiLLhiJjhiKjhjKUg4Yuo4Yia4Yur4Yud"
    "IGNhbGxiYWNrIiIiCiAgICBxdWVyeSA9IHVwZGF0ZS5jYWxsYmFja19xdWVyeQogICAgdWlkID0gcXVlcnkuZnJvbV91c2VyLmlk"
    "CiAgICBpZiB1aWQgIT0gQURNSU5fSUQ6CiAgICAgICAgYXdhaXQgcXVlcnkuYW5zd2VyKCkKICAgICAgICByZXR1cm4KICAgIHRy"
    "eToKICAgICAgICBfLCBtZXRob2Rfa2V5ID0gcXVlcnkuZGF0YS5zcGxpdCgiXyIsIDEpCiAgICBleGNlcHQgVmFsdWVFcnJvcjoK"
    "ICAgICAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQogICAgICAgIHJldHVybgoKICAgIHN0YXRlID0gY3JlZGl0X3RvcHVwX3N0YXRl"
    "LmdldCh1aWQpCiAgICBpZiBub3Qgc3RhdGUgb3Igc3RhdGUuZ2V0KCJzdGVwIikgIT0gIm1ldGhvZCI6CiAgICAgICAgYXdhaXQg"
    "cXVlcnkuYW5zd2VyKCLijJsg4Yut4YiFIOGMpeGLq+GJhCDhjIrhi5zhi40g4Yqg4YiN4Y2O4Ymg4Ymz4YiN4Y2jIC9hZGRjcmVk"
    "aXQg4Yml4YiI4YuNIOGKpeGKleGLsOGMiOGKkyDhi63hjIDhiJ3hiKnhjaIiLCBzaG93X2FsZXJ0PVRydWUpCiAgICAgICAgcmV0"
    "dXJuCgogICAgbWV0aG9kID0gQ1JFRElUX1NFTExFUl9QQVlNRU5UX01FVEhPRFMuZ2V0KG1ldGhvZF9rZXkpCiAgICBpZiBub3Qg"
    "bWV0aG9kOgogICAgICAgIGF3YWl0IHF1ZXJ5LmFuc3dlcigpCiAgICAgICAgcmV0dXJuCgogICAgc3RhdGVbIm1ldGhvZCJdID0g"
    "bWV0aG9kX2tleQogICAgc3RhdGVbInN0ZXAiXSA9ICJyZWZlcmVuY2UiCiAgICBhd2FpdCBxdWVyeS5hbnN3ZXIoKQoKICAgIHRl"
    "eHQgPSAoCiAgICAgICAgZiJ7bWV0aG9kWydlbW9qaSddfSB7bWV0aG9kWydsYWJlbCddfSDhibDhiJjhiK3hjKfhiI3hjaJcblxu"
    "IgogICAgICAgIGYi8J+TjCDhi4jhi7Dhi5rhiIUg4Yqg4Yqr4YuN4YqV4Ym1IDxiPntzdGF0ZVsnYW1vdW50J106LjJmfSDhiaXh"
    "iK08L2I+IOGLreGIi+GKqeGNplxuIgogICAgICAgIGYi8J+RpCB7bWV0aG9kWydob2xkZXInXX1cbiIKICAgICAgICBmIvCfk7Eg"
    "PGNvZGU+e21ldGhvZFsnYWNjb3VudCddfTwvY29kZT5cblxuIgogICAgICAgICLhiqjhiqjhjYjhiIkg4Ymg4YqL4YiLIOGLqOGI"
    "muGKqOGJsOGIiOGLjeGKlSDhi6vhi7XhiK3hjInhjaZcbiIKICAgICAgICAi4oCiIOGLqOGJo+GKleGKrS/hibThiIzhiaXhiK0g"
    "4Yqk4Yi14Yqk4Yid4Yqk4Yix4YqVIOGJoOGJgOGMpeGJsyDhjY7hiK3hi4vhiK3hi7Ug4Yur4Yu14Yit4YyJICjhiKvhiLUt4Yiw"
    "4YitIOGLreGIqOGMi+GMiOGMo+GIjSnhjaMg4YuI4Yut4YidXG4iCiAgICAgICAgIuKAoiDhi6jhiKrhjYjhiKjhipXhiLUg4YmB"
    "4Yyl4Yip4YqVIOGJpeGJuyDhi63hiIvhiqkgKOGKpOGIteGKpOGIneGKpOGIsSDhjIjhipMg4Yqr4YiN4Yuw4Yio4YiwIOGIsuGL"
    "sOGIreGItSDhiaDhiKvhiLUt4Yiw4YitIOGLreGMiOGMo+GMoOGIm+GIjSlcblxuIgogICAgICAgICLinYwg4YiI4Yib4YmL4Yio"
    "4YylIC9jYW5jZWwg4Yut4YiL4Yqp4Y2iIgogICAgKQogICAgdHJ5OgogICAgICAgIGF3YWl0IHF1ZXJ5LmVkaXRfbWVzc2FnZV90"
    "ZXh0KHRleHQsIHBhcnNlX21vZGU9IkhUTUwiKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICBhd2FpdCBjb250ZXh0LmJv"
    "dC5zZW5kX21lc3NhZ2UoY2hhdF9pZD1BRE1JTl9JRCwgdGV4dD10ZXh0LCBwYXJzZV9tb2RlPSJIVE1MIikKCmFzeW5jIGRlZiBo"
    "ZWxwX2NvbW1hbmQodXBkYXRlOiBVcGRhdGUsIGNvbnRleHQ6IENvbnRleHRUeXBlcy5ERUZBVUxUX1RZUEUpOgogICAgIiIi4Yuo"
    "4Ym14YuV4Yub4Yue4Ym94YqVIOGLneGIreGLneGIrSDhi6jhiJrhi6vhiLPhi60g4Ym14YuV4Yub4YudICjhiIjhiqDhi7XhiJrh"
    "ipUg4Yql4YqTIOGIiOGJsOGMq+GLi+GJvSDhi6jhibDhiIjhi6vhi6gg4Yut4YuY4Ym1KSIiIgogICAgdXNlcl9pZCA9IHVwZGF0"
    "ZS5tZXNzYWdlLmZyb21fdXNlci5pZAoKICAgIGlmIHVzZXJfaWQgIT0gQURNSU5fSUQgYW5kIGF3YWl0IF9ibG9ja193aXRoX3Bl"
    "bmRpbmdfc2VsZWN0aW9uX25vdGljZSh1cGRhdGUsIHVzZXJfaWQpOgogICAgICAgIHJldHVybgoKICAgIGlmIHVzZXJfaWQgPT0g"
    "QURNSU5fSUQ6CiAgICAgICAgdGV4dCA9ICgKICAgICAgICAgICAgIvCfm6AgPGI+4Yuo4Yqg4Yu14Yia4YqVIOGJteGLleGLm+GL"
    "nuGJvSDhi53hiK3hi53hiK08L2I+XG5cbiIKICAgICAgICAgICAgIvCfkaQgPGI+4YiI4Ymw4Yyr4YuL4Ym+4Ym9IOGLqOGImuGL"
    "q+GMiOGIiOGMjeGIieGNpjwvYj5cbiIKICAgICAgICAgICAgIi9zdGFydCAtIOGImOGImOGLneGMiOGJpSAvIOGKpeGKleGKs+GK"
    "lSDhi7DhiIXhipMg4YiY4YyhIOGImOGIjeGLleGKreGJtVxuIgogICAgICAgICAgICAiL3JvdW5kcyAtIOGKleGJgSDhi5nhiK7h"
    "ib3hipUg4Yud4Yit4Yud4YitIOGIm+GIs+GLqOGJtVxuIgogICAgICAgICAgICAiL3BsYXkgJmx0O+GLmeGIrSZndDsgLSDhi6jh"
    "iYHhjKXhiK7hib0g4Yiw4YiM4YuzIOGIm+GIs+GLqOGJtVxuIgogICAgICAgICAgICAiL3B1cmNoYXNlZCAmbHQ74YuZ4YitJmd0"
    "OyAtIOGLqOGJsOGIuOGMoSDhibLhiqzhibbhib3hipUg4Yml4Ym7ICjhiLXhiI3hiq0g4Ymg4Yqo4Y2K4YiNIOGJsOGIuOGNjeGK"
    "likg4Yib4Yuo4Ym1XG4iCiAgICAgICAgICAgICIvYXZhaWxhYmxlICZsdDvhi5nhiK0mZ3Q7IC0g4Yur4YiN4Ymw4Yur4YuZIOGJ"
    "suGKrOGJtuGJveGKlSDhiaXhibsg4Yib4Yuo4Ym1XG4iCiAgICAgICAgICAgICIvd2lubmVycyAmbHQ74YuZ4YitJmd0OyAtIOGL"
    "qOGKoOGKleGLtSDhi5nhiK0g4Yqg4Yi44YqT4Y2KKOGLjuGJvSkg4Yib4Yuo4Ym1IChhcmdzIOGKq+GIjeGMiOGIiOGMuSDhiqXh"
    "jKMg4Yuo4YuI4Yyj4YiL4Ym44YuNIOGLmeGIruGJvSDhiaDhiYHhiI3hjY0g4Yut4Ymz4Yur4YiJKVxuIgogICAgICAgICAgICAi"
    "L2hlbHAgLSDhi63hiIXhipUg4Yuo4Ym14YuV4Yub4Yue4Ym9IOGLneGIreGLneGIrSDhiJvhiLPhi6jhibVcblxuIgogICAgICAg"
    "ICAgICAi8J+OriA8Yj7hi6jhjKjhi4vhibMg4Yqg4Yi14Ymw4Yuz4Yuw4YitICjhiaDhiK3hiqvhibMg4YuZ4Yiu4Ym9IOGJoOGJ"
    "teGLreGLqSDhi63hibvhiIvhiI0p4Y2mPC9iPlxuIgogICAgICAgICAgICAiL25ld3JvdW5kICZsdDvhibLhiqzhibUg4Yml4Yub"
    "4Ym1Jmd0OyAmbHQ74YuL4YyLJmd0OyBb4Yi14YidLi4uXSAtIOGKoOGLsuGItSDhi5nhiK0g4YiY4Yqt4Y2I4Ym1ICjhiIjhiJ3h"
    "iLPhiIzhjaYgL25ld3JvdW5kIDUwIDMwIOGLqOGMiOGKkyDhiI7hibDhiKopXG4iCiAgICAgICAgICAgICIgICDilqrvuI8gYXJn"
    "cyDhiqvhiI3hjKDhiYDhiLEg4Yi14YidL+GJsuGKrOGJtS/hi4vhjIsv4YiY4YyN4YiI4YyrL+GIneGIteGIjSDhiaDhi7DhiKjh"
    "jIMg4Yut4Yyg4Yuo4YmD4YiJXG4iCiAgICAgICAgICAgICIvcGF1c2Vyb3VuZCAmbHQ74YuZ4YitJmd0OyAtIOGLmeGIreGKlSDh"
    "iIjhjIrhi5zhi40g4Yqo4Ymw4Yyr4YuL4Ym+4Ym9IOGImOGLsOGJoOGJhSAo4Ymy4Yqs4Ym24Ym9IOGKpeGKleGLsOGJsOGLq+GL"
    "mSDhi63hiYbhi6vhiIkpXG4iCiAgICAgICAgICAgICIvcmVzdW1lcm91bmQgJmx0O+GLmeGIrSZndDsgLSDhi6jhiYbhiJgg4YuZ"
    "4Yit4YqVIOGImOGIjeGItiDhiJvhipXhiYPhibVcbiIKICAgICAgICAgICAgIi9yZXN0YXJ0cm91bmQgJmx0O+GLmeGIrSZndDsg"
    "LSDhi5nhiK3hipUg4YuI4YuwIOGImOGMgOGImOGIquGLq+GLjSDhiJjhiJjhiIjhiLUgKOGIgeGIieGInSDhibLhiqzhibUgQVZB"
    "SUxBQkxFIOGLreGIhuGKk+GIjSlcbiIKICAgICAgICAgICAgIi9kZWxldGVyb3VuZCAmbHQ74YuZ4YitJmd0OyAtIOGLmeGIreGK"
    "lSDhiJnhiIkg4Ymg4YiZ4YiJIOGIm+GMpeGNi+GJtSAo4Yml4YuZIOGJsuGKrOGJtSDhibDhiLjhjKYg4Yqo4YiG4YqQIOGIm+GI"
    "qOGMi+GMiOGMqyDhi63hjKDhi6jhiYPhiI0pXG4iCiAgICAgICAgICAgICIvZGVsZXRlYWxscm91bmRzIC0g4YiB4YiJ4YqV4Yid"
    "IOGLmeGIruGJvSDhiqDhjKXhjY3hibYg4Yqo4Ymj4Yu2IOGImOGMgOGImOGIrSAo4Ymw4Yyr4YuL4Ym+4Ym9IOGKoOGLreGKkOGK"
    "qeGIneGNoyAyIOGMiuGLnCDhiJjhiIvhiq0g4Yib4Yio4YyL4YyI4YyrIOGLreGNiOGIjeGMi+GIjSlcbiIKICAgICAgICAgICAg"
    "Ii9yZXNldGZhY3RvcnkgZGVmYXVsdCAtIPCfmqgg4YiZ4YiJIOGNi+GJpeGIquGKqyDhi7PhjI3hiJ0g4Yib4Yi14YyA4YiY4Yiq"
    "4Yur4Y2mIOGLmeGIruGJvSvhibDhjKvhi4vhib7hib0r4Yqt4Yis4Yuy4Ym1K+GLqOGKreGNjeGLqyDhiqDhiqvhi43hipXhibUr"
    "4Ymz4Yiq4YqtIOGIgeGIiSDhjKDhjY3hibYg4YuI4YuwIOGImOGKkOGIuyDhi63hiJjhiIjhiLPhiI0gKOGIm+GIqOGMi+GMiOGM"
    "qyDhiaDhiYHhiI3hjY0g4Yut4Yyg4Yuo4YmD4YiNKVxuIgogICAgICAgICAgICAiL2Nsb3Nlcm91bmQgJmx0O+GLmeGIrSZndDsg"
    "LSDhiqDhipXhi7Ug4Yuo4Ymw4YuI4Yiw4YqQIOGLmeGIreGKlSDhiJjhi53hjIvhibVcbiIKICAgICAgICAgICAgIi9tYW51YWxz"
    "ZWxsICZsdDvhi5nhiK0mZ3Q7ICZsdDvhiYHhjKXhiK0o4Ym24Ym9KSZndDsgJmx0O+GIteGInSZndDsgJmx0O+GIteGIjeGKrSZn"
    "dDsgLSDhiaDhiLXhiI3hiq0g4Yuo4Ymw4YyI4YubIOGJgeGMpeGIrSjhibbhib0pIOGJoOGKpeGMhSDhiJjhiJjhi53hjIjhiaUg"
    "IgogICAgICAgICAgICAiKGFyZ3Mg4Yqr4YiN4Yyg4YmA4YixIOGJgeGIjeGNjSDhibDhjK3hipDhi40g4Yml4YuZIOGJgeGMpeGI"
    "rSDhiaDhiqDhipXhi7Ug4YyK4YucIOGImOGIneGIqOGMpSDhi63hib3hiIvhiInhjaMg4YuI4Yut4YidIOGJgeGMpeGIruGJveGK"
    "lSDhiaDhiq7hiJsg4Yut4YiI4Yur4Yup4Y2mIDQ1LDQ2LDQ3KVxuIgogICAgICAgICAgICAiL21hbnVhbGNhbmNlbCAmbHQ74YuZ"
    "4YitJmd0OyAmbHQ74YmB4Yyl4YitJmd0OyAtIOGLqOGJsOGLq+GLmC/hi6jhibDhiLjhjKAg4YmB4Yyl4Yit4YqVIOGLq+GIjeGJ"
    "sOGLq+GLmSDhiJvhi7XhiKjhjI1cbiIKICAgICAgICAgICAgIi9ub3RpZnlkcmF3ICZsdDvhi5nhiK0mZ3Q7IC0g4Yql4Yyj4YuN"
    "IOGIiuGLiOGMoyDhiLLhiI0g4YmB4Yyl4YitIOGMiOGLoiDhibDhjKvhi4vhib7hib3hipUg4Yib4Yiz4YuI4YmFXG4iCiAgICAg"
    "ICAgICAgICIvc2V0d2lubmVyICZsdDvhi5nhiK0mZ3Q7ICZsdDvhiYHhjKXhiK0mZ3Q7IFvhiL3hiI3hiJvhibUuLi5dIC0g4Yql"
    "4YyjIOGKqOGLiOGMoyDhiaDhiovhiIsg4Yqg4Yi44YqT4Y2K4YuN4YqVIOGJgeGMpeGIrSDhiJjhiJjhi53hjIjhiaUgIgogICAg"
    "ICAgICAgICAiKGFyZ3Mg4Yqr4YiN4Yyg4YmA4YixIOGImOGMgOGImOGIquGLqyDhi5nhiK0g4Yqo4Yua4YurIOGLqOGJsOGIuOGM"
    "oCDhiYHhjKXhiK0g4Ymg4YmB4YiN4Y2NIOGImOGIneGIqOGMpSDhi63hib3hiIvhiInhjaMg4YmA4Yyl4YiOIOGIveGIjeGIm+GJ"
    "tSDhi63hjKDhi6jhiYPhiIkgKOGLiOGLreGInSAvc2tpcCnhjaMgIgogICAgICAgICAgICAi4Ymg4YiY4Yyo4Yio4Yi7IOKchS/i"
    "nYwg4Yib4Yio4YyL4YyI4YyrIOGLreGIi+GKq+GIjSAtIOGKq+GIqOGMi+GMiOGMoSDhiaXhibsg4Yut4YiY4YuY4YyI4Ymj4YiN"
    "L+GLreGIi+GKq+GIjeGNoyDhiaXhi5kg4Yi94YiN4Yib4Ym1IOGKq+GIiCDhi7DhjIvhjI3hiJjhi40g4Yut4YiL4YqpKVxuIgog"
    "ICAgICAgICAgICAiL3dpbm5lcnNsb3RzICZsdDvhiYHhjKXhiK0mZ3Q7IC0g4Yi14YqV4Ym1IOGLqOGKoOGIuOGKk+GNiiDhiabh"
    "ibMgKDHhipsvMuGKmy8z4YqbLzThipsuLi4pIOGKpeGKleGLsOGImuGNiOGJgOGLtSDhiJjhiYDhi6jhiK0gKOGKkOGJo+GIqiAz"
    "4Y2jIOGIm+GIqOGMi+GMiOGMqyDhi63hjKDhi63hiYPhiI0pXG4iCiAgICAgICAgICAgICIvd2lubmVycyAmbHQ74YuZ4YitJmd0"
    "OyAtIOGLqOGJsOGImOGLmOGMiOGJoSDhiqDhiLjhipPhjYrhi47hib3hipUg4Yib4Yuo4Ym1XG5cbiIKICAgICAgICAgICAgIvCf"
    "k4sgPGI+4Yib4Yyg4YmD4YiI4YurIChTdW1tYXJ5KeGNpjwvYj5cbiIKICAgICAgICAgICAgIi9zb2xkICZsdDvhi5nhiK0mZ3Q7"
    "IC0g4Yuo4Ymw4Yi44YyhIOGJsuGKrOGJtuGJveGKlSDhiIHhiIkg4Yud4Yit4Yud4YitICjhiLXhiJ0v4Yi14YiN4YqtL+GKpeGK"
    "leGLtOGJtSDhiqXhipXhi7DhibDhjIjhi5kpIOGIm+GLqOGJtVxuIgogICAgICAgICAgICAiL3Vuc29sZCAmbHQ74YuZ4YitJmd0"
    "OyAtIOGLq+GIjeGJsOGIuOGMoSAoQVZBSUxBQkxFKSDhiYHhjKXhiK7hib3hipUg4YiB4YiJIOGLneGIreGLneGIrSDhiJvhi6jh"
    "ibVcbiIKICAgICAgICAgICAgIi91c2VkcmVmcyAtIOGMpeGJheGInSDhiIvhi60g4Yuo4YuL4YiJIOGIgeGIieGKleGInSDhi6jh"
    "iq3hjY3hi6sg4Yiq4Y2I4Yio4YqV4Yi24Ym9IOGIm+GLqOGJtSAoYXVkaXQpXG4iCiAgICAgICAgICAgICIvc3RhdHMgLSDhiIHh"
    "iInhipXhiJ0g4YuZ4Yiu4Ym9IOGLqOGImuGLq+GMoOGJg+GIjeGIjSDhiJvhjKDhiYPhiIjhi6tcbiIKICAgICAgICAgICAgIi9o"
    "b3N0cHJvZmlsZSAtIOGLqOGIhuGIteGJtSDhiJjhjIjhiIjhjKsgKOGKreGIrOGLsuGJtSDhiJLhiLPhiaUv4Yqu4Yia4Yi94YqV"
    "L+GIgeGKlOGJsykg4Yib4Yuo4Ym1XG4iCiAgICAgICAgICAgICIvZWRpdHBheW1lbnQgLSDhi6jhibThiIzhiaXhiK0v4Yiy4Ymi"
    "4YqiIOGJpeGIrSDhiqDhiqvhi43hipXhibUg4Yi14YiN4YqtIOGJgeGMpeGIrSDhiqXhipMg4Ymj4YiI4Ymk4Ym1IOGIteGInSDh"
    "iJvhiLXhibDhiqvhiqjhiI1cbiIKICAgICAgICAgICAgIi9hZGRjcmVkaXQgJmx0O+GImOGMoOGKlSZndDsgLSDhiIjhiIbhiLXh"
    "ibUg4Yqt4Yis4Yuy4Ym1IOGMiOGKleGLmOGJpSDhiJjhjKjhiJjhiK0gKOGKreGIrOGLsuGJtSDhiqvhiIjhiYAg4Yi94Yur4Yyt"
    "4YqVIOGIq+GItS3hiLDhiK0g4Yut4YmA4Yyl4YiL4YiNKVxuIgogICAgICAgICAgICAiL3NldHNtc3dlYmhvb2sgLSDhi43hjKvh"
    "i4ogV2ViaG9vayBVUkwg4Yib4YuL4YmA4Yit4Y2mIOGIiOGKreGNjeGLqyDhiJvhiKjhjIvhjIjhjKsg4Yuo4Yia4Yuw4Yit4Yi1"
    "IOGKpOGIteGKpOGIneGKpOGItSDhi4jhi7Dhi5rhiIUgVVJMIOGMiOGIjeGJpeGMpiAocmVsYXkpIOGLreGIi+GKq+GIjSAob2Zm"
    "IOGJpeGIiOGLjSDhiJvhjKXhjYvhibUg4Yut4Ym74YiL4YiNKVxuXG4iCiAgICAgICAgICAgICLwn5OiIDxiPuGIm+GIteGJs+GL"
    "iOGJguGLqyAoQW5ub3VuY2Up4Y2mPC9iPlxuIgogICAgICAgICAgICAiL2Fubm91bmNlICZsdDvhiJjhiI3hi5Xhiq3hibUmZ3Q7"
    "IC0g4Yy94YiB4Y2NIOGLreGLniDhi4rhi5vhiK3hi7HhipUg4Yut4YyA4Yid4Yir4YiNICjhjY7hibbihpLhiarhi7Xhi64v4Yu1"
    "4Yid4Yy94oaS4Yib4Yio4YyL4YyI4YyrKeGNoyDhiqXhiK3hiLXhi44g4pyFIOGKpeGIteGKquGLq+GIqOGMi+GMjeGMoSDhi7Xh"
    "iKjhiLUg4YiI4Yib4YqV4YidIOGKoOGLreGIi+GKreGInVxuIgogICAgICAgICAgICAiL2Fubm91bmNlICjhi6vhiIgg4Yy94YiB"
    "4Y2NKSAtIOGKqOGLmyDhjL3hiIHhjY0v4Y2O4Ym2L+GJquGLteGLri3hi4jhi63hiJ0t4Yu14Yid4Yy9IOGJoOGJheGLsOGInSDh"
    "ibDhiqjhibDhiI0g4Yut4YiL4Yqp4Y2jIOGJoOGImOGMqOGIqOGIuyDhiYXhi7XhiJgt4Yql4Yut4YmzIOGJs+GLreGJtiDinIUg"
    "4Yqr4Yio4YyL4YyI4YyhIOGJoOGKi+GIiyDhiaXhibsg4YiI4YiB4YiJ4YidIOGJsOGMq+GLi+GJvuGJvSDhi63hiLDhiKvhjKvh"
    "iI1cbiIKICAgICAgICAgICAgIi9jYW5jZWwgLSDhi6jhibDhjIDhiJjhiKgg4Yuo4Yib4Yi14Ymz4YuI4YmC4YurIOGLiOGLreGI"
    "nSAvbmV3cm91bmQg4YiB4YqQ4Ymz4YqVIOGImOGIsOGIqOGLnVxuXG4iCiAgICAgICAgICAgICLinIUgPGI+4Yqt4Y2N4YurIOGI"
    "m+GMveGLsOGJheGNpjwvYj5cbiIKICAgICAgICAgICAgIi9hcHByb3ZlXyZsdDvhi5nhiK0mZ3Q7XyZsdDvhiYHhjKXhiK0xJmd0"
    "O18mbHQ74YmB4Yyl4YitMiZndDsuLi4gLSDhiq3hjY3hi6vhipUg4Ymg4Yql4YyFIOGIm+GMveGLsOGJheGNoyDhiaXhi5kg4YmB"
    "4Yyl4Yiu4Ym94YqVIOGJoOGKoOGKleGLtSDhjIrhi5wgKOGIiOGIneGIs+GIjCAvYXBwcm92ZV8xXzQ1IOGLiOGLreGInSAvYXBw"
    "cm92ZV8xXzQ1XzQ2XzQ3KVxuIgogICAgICAgICAgICAiL3JlamVjdF8mbHQ74YuZ4YitJmd0O18mbHQ74YmB4Yyl4YitMSZndDtf"
    "Jmx0O+GJgeGMpeGIrTImZ3Q7Li4uIFvhiJ3hiq3hipXhi6vhibVdIC0g4Yqt4Y2N4Yur4YqVIOGLjeGLteGJhSDhiJvhi7XhiKjh"
    "jI3hjaMg4Yml4YuZIOGJgeGMpeGIruGJveGKlSDhiaDhiqDhipXhi7Ug4YyK4Yuc4Y2jIOGIneGKreGKleGLq+GJtSDhiqDhiJvh"
    "iKvhjK0g4YqQ4YuNICjhiIjhiJ3hiLPhiIwgL3JlamVjdF8xXzQ1XzQ2IHJlY2VpcHQg4Yuw4Yml4Yub4YubIOGIteGIiOGIhuGK"
    "kClcbiIKICAgICAgICAgICAgIvCfk6kg4Yuo4Ymj4YqV4YqtIOGKpOGIteGKpOGIneGKpOGItSDhi4jhi7Dhi5rhiIUg4Ymm4Ym1"
    "IOGJoOGJgOGMpeGJsyDhjY7hiK3hi4vhiK3hi7Ug4Yqr4Yuw4Yio4YyJIOGLiOGLreGInSDhiqThiLXhiqThiJ3hiqThiLUg4Y2O"
    "4Yit4YuL4Yit4Yu1IOGKoOGMiOGIjeGMjeGIjuGJtSDhiqvhjIjhipPhipnhjaMgIgogICAgICAgICAgICAi4Yqo4Ymw4Yyr4YuL"
    "4Ym+4Ym9IOGIquGNiOGIqOGKleGItSDhiYHhjKXhiK0g4YyL4YitIOGIq+GIsSDhiaDhiKvhiLUt4Yiw4YitIOGKoOGMiOGKk+GK"
    "neGJtiDhi63hiLjhjKPhiI0gKGF1dG8tdmVyaWZ5KeGNoyDhi4vhjIvhi43hiJ0g4Yqo4Yuo4YuZ4YipIOGMi+GIrSDhibDhi6vh"
    "iI3hibDhi6vhi5nhjL3hiK4g4Yut4Yio4YyL4YyI4Yyj4YiN4Y2iIgogICAgICAgICkKICAgIGVsc2U6CiAgICAgICAgdGV4dCA9"
    "IEwodXNlcl9pZCwgImhlbHBfdGV4dCIsIG1pbnM9VElDS0VUX0hPTERfTUlOVVRFUykKCiAgICBhd2FpdCB1cGRhdGUubWVzc2Fn"
    "ZS5yZXBseV90ZXh0KHRleHQsIHBhcnNlX21vZGU9IkhUTUwiKQoKYXN5bmMgZGVmIHNtc193ZWJob29rX2hhbmRsZXIocmVxdWVz"
    "dDogd2ViLlJlcXVlc3QpOgogICAgIiIiU01TIGZvcndhcmRlciBhcHAg4Yuo4Yia4Yuw4YuN4YiI4YuNIEhUVFAgZW5kcG9pbnQi"
    "IiIKICAgIHByaW50KGYiXG7wn5OpIFt7ZGF0ZXRpbWUubm93KCkuc3RyZnRpbWUoJyVIOiVNOiVTJyl9XSBXZWJob29rIOGMpeGI"
    "qiDhi7DhiKjhiLAgKElQOiB7cmVxdWVzdC5yZW1vdGV9KSIpCgogICAgdG9rZW4gPSByZXF1ZXN0LmhlYWRlcnMuZ2V0KCJYLVdl"
    "Ymhvb2stVG9rZW4iKSBvciByZXF1ZXN0LnF1ZXJ5LmdldCgidG9rZW4iKQogICAgaWYgbm90IFdFQkhPT0tfU0VDUkVUIG9yIHRv"
    "a2VuICE9IFdFQkhPT0tfU0VDUkVUOgogICAgICAgIHByaW50KCIgICDinYwg4YuN4Yu14YmFIOGJsOGLsOGIreGMk+GIjeGNpiDh"
    "iLXhiIXhibDhibUg4YuI4Yut4YidIOGMoOGNjeGJtiDhi6vhiIggdG9rZW4iKQogICAgICAgIHJldHVybiB3ZWIuanNvbl9yZXNw"
    "b25zZSh7Im9rIjogRmFsc2UsICJlcnJvciI6ICJ1bmF1dGhvcml6ZWQifSwgc3RhdHVzPTQwMSkKCiAgICB0cnk6CiAgICAgICAg"
    "aWYgcmVxdWVzdC5jb250ZW50X3R5cGUgPT0gImFwcGxpY2F0aW9uL2pzb24iOgogICAgICAgICAgICBkYXRhID0gYXdhaXQgcmVx"
    "dWVzdC5qc29uKCkKICAgICAgICBlbHNlOgogICAgICAgICAgICBkYXRhID0gZGljdChhd2FpdCByZXF1ZXN0LnBvc3QoKSkKICAg"
    "IGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgZGF0YSA9IHt9CgogICAgdGV4dCA9IGRhdGEuZ2V0KCJtZXNzYWdlIikgb3IgZGF0"
    "YS5nZXQoInRleHQiKSBvciBkYXRhLmdldCgiYm9keSIpIG9yICIiCiAgICBzZW5kZXIgPSBkYXRhLmdldCgic2VuZGVyIikgb3Ig"
    "ZGF0YS5nZXQoImZyb20iKSBvciBkYXRhLmdldCgib3JpZ2luYXRvciIpIG9yICIiCgogICAgcHJpbnQoZiIgICBzZW5kZXI6IHtz"
    "ZW5kZXIhcn0iKQogICAgcHJpbnQoZiIgICBtZXNzYWdlOiB7dGV4dCFyfSIpCgogICAgaWYgbm90IHRleHQ6CiAgICAgICAgcHJp"
    "bnQoIiAgIOKdjCDhiJ3hipXhiJ0g4Yuo4YiY4YiN4YuV4Yqt4Ym1IOGMveGIgeGNjSDhiqDhiI3hibDhjIjhipjhiJ0iKQogICAg"
    "ICAgIHJldHVybiB3ZWIuanNvbl9yZXNwb25zZSh7Im9rIjogRmFsc2UsICJlcnJvciI6ICJubyBtZXNzYWdlIHRleHQgcHJvdmlk"
    "ZWQifSwgc3RhdHVzPTQwMCkKCiAgICBib3QgPSByZXF1ZXN0LmFwcFsiYm90Il0KICAgIHJlc3VsdCA9IGF3YWl0IHRyeV9hdXRv"
    "X21hdGNoX2FuZF9hcHByb3ZlKHRleHQsIGJvdCwgc2VuZGVyPXNlbmRlciwgc291cmNlPSJ3ZWJob29rIikKICAgIHByaW50KGYi"
    "ICAg4YuN4Yyk4Ym14Y2mIHtyZXN1bHR9IikKICAgIHJldHVybiB3ZWIuanNvbl9yZXNwb25zZShyZXN1bHQpCgoKIyAtLS0tLS0t"
    "LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLQojIE11"
    "bHRpLUhvc3QgQ29udHJvbCBBUEkKIyAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0t"
    "LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLQojIFRoZXNlIGVuZHBvaW50cyBhcmUgaW50ZW50aW9uYWxseSBzZXBhcmF0ZSBmcm9t"
    "IC9zbXMtd2ViaG9vay4gIFRoZSBjZW50cmFsCiMgTXVsdGktSG9zdCBjb250cm9sbGVyIHVzZXMgdGhlbSB0byBtb25pdG9yL2Nv"
    "bnRyb2wgdGhpcyBob3N0IGluc3RhbmNlLgoKCmRlZiBfaG9zdF9hcGlfYXV0aG9yaXplZChyZXF1ZXN0OiB3ZWIuUmVxdWVzdCkg"
    "LT4gYm9vbDoKICAgIHRva2VuID0gcmVxdWVzdC5oZWFkZXJzLmdldCgiWC1Ib3N0LUFQSS1LZXkiKSBvciByZXF1ZXN0LnF1ZXJ5"
    "LmdldCgiYXBpX2tleSIpCiAgICByZXR1cm4gYm9vbChIT1NUX0FQSV9TRUNSRVQpIGFuZCB0b2tlbiA9PSBIT1NUX0FQSV9TRUNS"
    "RVQKCgpkZWYgX2hvc3RfYXBpX2Vycm9yKG1lc3NhZ2U6IHN0ciwgc3RhdHVzOiBpbnQgPSA0MDEpOgogICAgcmV0dXJuIHdlYi5q"
    "c29uX3Jlc3BvbnNlKHsib2siOiBGYWxzZSwgImVycm9yIjogbWVzc2FnZX0sIHN0YXR1cz1zdGF0dXMpCgoKZGVmIF9ob3N0X2Fw"
    "aV9zdGF0cygpOgogICAgdG90YWxfdGlja2V0cyA9IHN1bShpbnQoci5nZXQoIm51bV90aWNrZXRzIiwgMCkpIGZvciByIGluIHJv"
    "dW5kcy52YWx1ZXMoKSkKICAgIGF2YWlsYWJsZSA9IHBlbmRpbmcgPSBzb2xkID0gMAogICAgcmV2ZW51ZSA9IDAuMAogICAgb3Bl"
    "bl9yb3VuZHMgPSBwYXVzZWRfcm91bmRzID0gY2xvc2VkX3JvdW5kcyA9IDAKCiAgICBmb3IgciBpbiByb3VuZHMudmFsdWVzKCk6"
    "CiAgICAgICAgc3RhdHVzID0gci5nZXQoInN0YXR1cyIpCiAgICAgICAgaWYgc3RhdHVzID09ICJPUEVOIjoKICAgICAgICAgICAg"
    "b3Blbl9yb3VuZHMgKz0gMQogICAgICAgIGVsaWYgc3RhdHVzID09ICJQQVVTRUQiOgogICAgICAgICAgICBwYXVzZWRfcm91bmRz"
    "ICs9IDEKICAgICAgICBlbGlmIHN0YXR1cyA9PSAiQ0xPU0VEIjoKICAgICAgICAgICAgY2xvc2VkX3JvdW5kcyArPSAxCiAgICAg"
    "ICAgZm9yIHQgaW4gci5nZXQoInRpY2tldHMiLCB7fSkudmFsdWVzKCk6CiAgICAgICAgICAgIHN0ID0gdC5nZXQoInN0YXR1cyIp"
    "CiAgICAgICAgICAgIGlmIHN0ID09ICJBVkFJTEFCTEUiOgogICAgICAgICAgICAgICAgYXZhaWxhYmxlICs9IDEKICAgICAgICAg"
    "ICAgZWxpZiBzdCA9PSAiUEVORElORyI6CiAgICAgICAgICAgICAgICBwZW5kaW5nICs9IDEKICAgICAgICAgICAgZWxpZiBzdCA9"
    "PSAiU09MRCI6CiAgICAgICAgICAgICAgICBzb2xkICs9IDEKICAgICAgICAgICAgICAgIHJldmVudWUgKz0gZmxvYXQoci5nZXQo"
    "InByaWNlIiwgMCkgb3IgMCkKCiAgICAjIE5PVEU6IGNvbW1pc3Npb24gaXMgdGhlIFJFQUwgY3VtdWxhdGl2ZSBhbW91bnQgYWxy"
    "ZWFkeSBkZWR1Y3RlZCBwZXIKICAgICMgc2FsZSAoc2VlIF9kZWR1Y3RfaG9zdF9jb21taXNzaW9uKSwgdXNpbmcgd2hhdGV2ZXIg"
    "Y29tbWlzc2lvbl9wZXJjZW50CiAgICAjIHdhcyBpbiBlZmZlY3QgYXQgdGhlIG1vbWVudCBlYWNoIHRpY2tldCB3YXMgc29sZC4g"
    "V2UgZGVsaWJlcmF0ZWx5IGRvCiAgICAjIE5PVCByZWNvbXB1dGUgaXQgYXMgYHJldmVudWUgKiBIT1NUX0NPTU1JU1NJT05fUEVS"
    "Q0VOVGAgaGVyZSwgYmVjYXVzZQogICAgIyB0aGF0IHdvdWxkIHJldHJvYWN0aXZlbHkgcmUtcHJpY2UgZXZlcnkgcGFzdCBzYWxl"
    "IGF0IHRvZGF5J3MgcmF0ZQogICAgIyB3aGVuZXZlciBhbiBhZG1pbiBlZGl0cyB0aGUgY29tbWlzc2lvbiDigJQgaS5lLiBhbiAv"
    "ZWRpdGNvbW1pc3Npb24KICAgICMgY2hhbmdlIHdvdWxkIGluY29ycmVjdGx5IGFwcGx5IHRvIHNhbGVzIG1hZGUgYmVmb3JlIHRo"
    "ZSBlZGl0IHRvby4KICAgIGNvbW1pc3Npb24gPSBob3N0X2NyZWRpdC5nZXQoInRvdGFsX2RlZHVjdGVkIiwgMC4wKQogICAgcmV0"
    "dXJuIHsKICAgICAgICAicmVnaXN0ZXJlZF9wbGF5ZXJzIjogbGVuKHBsYXllcnMpLAogICAgICAgICJ0b3RhbF9yb3VuZHMiOiBs"
    "ZW4ocm91bmRzKSwKICAgICAgICAib3Blbl9yb3VuZHMiOiBvcGVuX3JvdW5kcywKICAgICAgICAicGF1c2VkX3JvdW5kcyI6IHBh"
    "dXNlZF9yb3VuZHMsCiAgICAgICAgImNsb3NlZF9yb3VuZHMiOiBjbG9zZWRfcm91bmRzLAogICAgICAgICJ0b3RhbF90aWNrZXRz"
    "IjogdG90YWxfdGlja2V0cywKICAgICAgICAiYXZhaWxhYmxlX3RpY2tldHMiOiBhdmFpbGFibGUsCiAgICAgICAgInBlbmRpbmdf"
    "dGlja2V0cyI6IHBlbmRpbmcsCiAgICAgICAgInNvbGRfdGlja2V0cyI6IHNvbGQsCiAgICAgICAgInNhbGVzIjogcm91bmQocmV2"
    "ZW51ZSwgMiksCiAgICAgICAgImNvbW1pc3Npb25fcGVyY2VudCI6IEhPU1RfQ09NTUlTU0lPTl9QRVJDRU5ULAogICAgICAgICJj"
    "b21taXNzaW9uIjogcm91bmQoY29tbWlzc2lvbiwgMiksCiAgICB9CgoKYXN5bmMgZGVmIGhvc3RfYXBpX2FkbWluaW5mbyhyZXF1"
    "ZXN0OiB3ZWIuUmVxdWVzdCk6CiAgICBpZiBub3QgX2hvc3RfYXBpX2F1dGhvcml6ZWQocmVxdWVzdCk6CiAgICAgICAgcmV0dXJu"
    "IF9ob3N0X2FwaV9lcnJvcigidW5hdXRob3JpemVkIikKICAgIGJvdCA9IHJlcXVlc3QuYXBwLmdldCgiYm90IikKICAgIHVzZXJu"
    "YW1lID0gTm9uZQogICAgZmlyc3RfbmFtZSA9IE5vbmUKICAgIHRyeToKICAgICAgICBjaGF0ID0gYXdhaXQgYm90LmdldF9jaGF0"
    "KEFETUlOX0lEKQogICAgICAgIHVzZXJuYW1lID0gY2hhdC51c2VybmFtZQogICAgICAgIGZpcnN0X25hbWUgPSBjaGF0LmZpcnN0"
    "X25hbWUKICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgICAgICByZXR1cm4gd2ViLmpzb25fcmVzcG9uc2UoeyJvayI6IEZh"
    "bHNlLCAiZXJyb3IiOiBzdHIoZSl9KQogICAgcmV0dXJuIHdlYi5qc29uX3Jlc3BvbnNlKHsKICAgICAgICAib2siOiBUcnVlLAog"
    "ICAgICAgICJhZG1pbl9pZCI6IEFETUlOX0lELAogICAgICAgICJ1c2VybmFtZSI6IHVzZXJuYW1lLAogICAgICAgICJmaXJzdF9u"
    "YW1lIjogZmlyc3RfbmFtZSwKICAgIH0pCgoKYXN5bmMgZGVmIGhvc3RfYXBpX3N0YXR1cyhyZXF1ZXN0OiB3ZWIuUmVxdWVzdCk6"
    "CiAgICBpZiBub3QgX2hvc3RfYXBpX2F1dGhvcml6ZWQocmVxdWVzdCk6CiAgICAgICAgcmV0dXJuIF9ob3N0X2FwaV9lcnJvcigi"
    "dW5hdXRob3JpemVkIikKICAgIHN0YXRzID0gX2hvc3RfYXBpX3N0YXRzKCkKICAgIHJldHVybiB3ZWIuanNvbl9yZXNwb25zZSh7"
    "CiAgICAgICAgIm9rIjogVHJ1ZSwKICAgICAgICAiaG9zdF9zdGF0dXMiOiAicGF1c2VkIiBpZiBob3N0X3BhdXNlZCBlbHNlICJy"
    "dW5uaW5nIiwKICAgICAgICAic2VsbGluZ19lbmFibGVkIjogbm90IGhvc3RfcGF1c2VkLAogICAgICAgICJvbmxpbmUiOiBUcnVl"
    "LAogICAgICAgICJ0aW1lc3RhbXAiOiBkYXRldGltZS5ub3coKS5pc29mb3JtYXQoKSwKICAgICAgICAiY3JlZGl0X2JhbGFuY2Ui"
    "OiBob3N0X2NyZWRpdFsiYmFsYW5jZSJdLAogICAgICAgICJiYWxhbmNlIjogaG9zdF9jcmVkaXRbImJhbGFuY2UiXSwKICAgICAg"
    "ICAidG90YWxfZGVkdWN0ZWQiOiBob3N0X2NyZWRpdC5nZXQoInRvdGFsX2RlZHVjdGVkIiwgMC4wKSwKICAgICAgICAqKnN0YXRz"
    "LAogICAgfSkKCgphc3luYyBkZWYgaG9zdF9hcGlfaGVhbHRoKHJlcXVlc3Q6IHdlYi5SZXF1ZXN0KToKICAgIGlmIG5vdCBfaG9z"
    "dF9hcGlfYXV0aG9yaXplZChyZXF1ZXN0KToKICAgICAgICByZXR1cm4gX2hvc3RfYXBpX2Vycm9yKCJ1bmF1dGhvcml6ZWQiKQog"
    "ICAgcmV0dXJuIHdlYi5qc29uX3Jlc3BvbnNlKHsib2siOiBUcnVlLCAib25saW5lIjogVHJ1ZSwgInRpbWVzdGFtcCI6IGRhdGV0"
    "aW1lLm5vdygpLmlzb2Zvcm1hdCgpfSkKCgphc3luYyBkZWYgaG9zdF9hcGlfcGF1c2UocmVxdWVzdDogd2ViLlJlcXVlc3QpOgog"
    "ICAgZ2xvYmFsIGhvc3RfcGF1c2VkLCBob3N0X3BhdXNlZF9yZWFzb24KICAgIGlmIG5vdCBfaG9zdF9hcGlfYXV0aG9yaXplZChy"
    "ZXF1ZXN0KToKICAgICAgICByZXR1cm4gX2hvc3RfYXBpX2Vycm9yKCJ1bmF1dGhvcml6ZWQiKQogICAgaG9zdF9wYXVzZWQgPSBU"
    "cnVlCiAgICBob3N0X3BhdXNlZF9yZWFzb24gPSAibWFudWFsIgogICAgc2F2ZV9zdGF0ZSgpCiAgICByZXR1cm4gd2ViLmpzb25f"
    "cmVzcG9uc2UoewogICAgICAgICJvayI6IFRydWUsCiAgICAgICAgImhvc3Rfc3RhdHVzIjogInBhdXNlZCIsCiAgICAgICAgInNl"
    "bGxpbmdfZW5hYmxlZCI6IEZhbHNlLAogICAgICAgICJtZXNzYWdlIjogIkhvc3Qgc2VsbGluZyBoYXMgYmVlbiBwYXVzZWQuIiwK"
    "ICAgIH0pCgoKYXN5bmMgZGVmIGhvc3RfYXBpX3Jlc3VtZShyZXF1ZXN0OiB3ZWIuUmVxdWVzdCk6CiAgICBnbG9iYWwgaG9zdF9w"
    "YXVzZWQsIGhvc3RfcGF1c2VkX3JlYXNvbgogICAgaWYgbm90IF9ob3N0X2FwaV9hdXRob3JpemVkKHJlcXVlc3QpOgogICAgICAg"
    "IHJldHVybiBfaG9zdF9hcGlfZXJyb3IoInVuYXV0aG9yaXplZCIpCiAgICBob3N0X3BhdXNlZCA9IEZhbHNlCiAgICBob3N0X3Bh"
    "dXNlZF9yZWFzb24gPSBOb25lCiAgICBzYXZlX3N0YXRlKCkKICAgIHJldHVybiB3ZWIuanNvbl9yZXNwb25zZSh7CiAgICAgICAg"
    "Im9rIjogVHJ1ZSwKICAgICAgICAiaG9zdF9zdGF0dXMiOiAicnVubmluZyIsCiAgICAgICAgInNlbGxpbmdfZW5hYmxlZCI6IFRy"
    "dWUsCiAgICAgICAgIm1lc3NhZ2UiOiAiSG9zdCBzZWxsaW5nIGhhcyBiZWVuIHJlc3VtZWQuIiwKICAgIH0pCgoKYXN5bmMgZGVm"
    "IGhvc3RfYXBpX3N0YXRpc3RpY3MocmVxdWVzdDogd2ViLlJlcXVlc3QpOgogICAgaWYgbm90IF9ob3N0X2FwaV9hdXRob3JpemVk"
    "KHJlcXVlc3QpOgogICAgICAgIHJldHVybiBfaG9zdF9hcGlfZXJyb3IoInVuYXV0aG9yaXplZCIpCiAgICByZXR1cm4gd2ViLmpz"
    "b25fcmVzcG9uc2UoeyJvayI6IFRydWUsICoqX2hvc3RfYXBpX3N0YXRzKCl9KQoKCmFzeW5jIGRlZiBob3N0X2FwaV9jb21taXNz"
    "aW9uKHJlcXVlc3Q6IHdlYi5SZXF1ZXN0KToKICAgIGlmIG5vdCBfaG9zdF9hcGlfYXV0aG9yaXplZChyZXF1ZXN0KToKICAgICAg"
    "ICByZXR1cm4gX2hvc3RfYXBpX2Vycm9yKCJ1bmF1dGhvcml6ZWQiKQogICAgc3RhdHMgPSBfaG9zdF9hcGlfc3RhdHMoKQogICAg"
    "IyBPcHRpb25hbCA/cGVyY2VudD0xMCBsZXRzIGEgY2FsbGVyIGFzayAid2hhdCB3b3VsZCBjb21taXNzaW9uIGJlIGF0CiAgICAj"
    "IHRoaXMgaHlwb3RoZXRpY2FsIHJhdGUgb24gdG90YWwgc2FsZXMiIFdJVEhPVVQgY2hhbmdpbmcgdGhpcyBob3N0J3MKICAgICMg"
    "c2F2ZWQgY29uZmlndXJhdGlvbi4gV2l0aG91dCB0aGF0IG92ZXJyaWRlIHdlIHJlcG9ydCB0aGUgUkVBTAogICAgIyBjdW11bGF0"
    "aXZlIGNvbW1pc3Npb24gYWxyZWFkeSBkZWR1Y3RlZCBwZXIgc2FsZSAoc3RhdHNbImNvbW1pc3Npb24iXSwKICAgICMgZnJvbSBo"
    "b3N0X2NyZWRpdFsidG90YWxfZGVkdWN0ZWQiXSkg4oCUIGkuZS4gc2FsZXMgbWFkZSBiZWZvcmUgYW4KICAgICMgL2VkaXRjb21t"
    "aXNzaW9uIGNoYW5nZSBrZWVwIHRoZSByYXRlIHRoYXQgd2FzIGluIGVmZmVjdCB3aGVuIHRoZXkKICAgICMgd2VyZSBzb2xkLCBh"
    "bmQgb25seSBzYWxlcyBtYWRlIGFmdGVyIHRoZSBjaGFuZ2UgdXNlIHRoZSBuZXcgcmF0ZS4KICAgIHJhd19wZXJjZW50ID0gcmVx"
    "dWVzdC5xdWVyeS5nZXQoInBlcmNlbnQiKQogICAgaWYgcmF3X3BlcmNlbnQgaXMgTm9uZToKICAgICAgICByZXR1cm4gd2ViLmpz"
    "b25fcmVzcG9uc2UoewogICAgICAgICAgICAib2siOiBUcnVlLAogICAgICAgICAgICAic2FsZXMiOiBzdGF0c1sic2FsZXMiXSwK"
    "ICAgICAgICAgICAgImNvbW1pc3Npb25fcGVyY2VudCI6IHN0YXRzWyJjb21taXNzaW9uX3BlcmNlbnQiXSwKICAgICAgICAgICAg"
    "ImNvbW1pc3Npb24iOiBzdGF0c1siY29tbWlzc2lvbiJdLAogICAgICAgIH0pCiAgICB0cnk6CiAgICAgICAgcGVyY2VudCA9IGZs"
    "b2F0KHJhd19wZXJjZW50KQogICAgZXhjZXB0IChUeXBlRXJyb3IsIFZhbHVlRXJyb3IpOgogICAgICAgIHJldHVybiBfaG9zdF9h"
    "cGlfZXJyb3IoImludmFsaWQgY29tbWlzc2lvbiBwZXJjZW50IiwgNDAwKQogICAgaWYgcGVyY2VudCA8IDAgb3IgcGVyY2VudCA+"
    "IDEwMDoKICAgICAgICByZXR1cm4gX2hvc3RfYXBpX2Vycm9yKCJjb21taXNzaW9uIHBlcmNlbnQgbXVzdCBiZSBiZXR3ZWVuIDAg"
    "YW5kIDEwMCIsIDQwMCkKICAgIGNvbW1pc3Npb24gPSBzdGF0c1sic2FsZXMiXSAqIHBlcmNlbnQgLyAxMDAuMAogICAgcmV0dXJu"
    "IHdlYi5qc29uX3Jlc3BvbnNlKHsKICAgICAgICAib2siOiBUcnVlLAogICAgICAgICJzYWxlcyI6IHN0YXRzWyJzYWxlcyJdLAog"
    "ICAgICAgICJjb21taXNzaW9uX3BlcmNlbnQiOiBwZXJjZW50LAogICAgICAgICJjb21taXNzaW9uIjogcm91bmQoY29tbWlzc2lv"
    "biwgMiksCiAgICB9KQoKCmFzeW5jIGRlZiBob3N0X2FwaV9hZGRjcmVkaXQocmVxdWVzdDogd2ViLlJlcXVlc3QpOgogICAgIiIi"
    "UE9TVCAvYXBpL2FkZGNyZWRpdCAgeyJhbW91bnQiOiA1MDB9ICAtIHJlbW90ZSBjcmVkaXQgdG9wLXVwLCBjYWxsZWQgYnkgYW4K"
    "ICAgIGV4dGVybmFsIGNyZWRpdC1zZWxsaW5nIGJvdCBhZnRlciBpdCBhdXRvLXZlcmlmaWVzIGEgaG9zdGVyJ3MgcGF5bWVudC4i"
    "IiIKICAgIGdsb2JhbCBob3N0X3BhdXNlZCwgaG9zdF9wYXVzZWRfcmVhc29uCiAgICBpZiBub3QgX2hvc3RfYXBpX2F1dGhvcml6"
    "ZWQocmVxdWVzdCk6CiAgICAgICAgcmV0dXJuIF9ob3N0X2FwaV9lcnJvcigidW5hdXRob3JpemVkIikKCiAgICB0cnk6CiAgICAg"
    "ICAgZGF0YSA9IGF3YWl0IHJlcXVlc3QuanNvbigpCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIGRhdGEgPSB7fQogICAg"
    "dHJ5OgogICAgICAgIGFtb3VudCA9IGZsb2F0KGRhdGEuZ2V0KCJhbW91bnQiKSkKICAgIGV4Y2VwdCAoVHlwZUVycm9yLCBWYWx1"
    "ZUVycm9yKToKICAgICAgICByZXR1cm4gX2hvc3RfYXBpX2Vycm9yKCJpbnZhbGlkIGFtb3VudCIsIDQwMCkKICAgIGlmIGFtb3Vu"
    "dCA8PSAwOgogICAgICAgIHJldHVybiBfaG9zdF9hcGlfZXJyb3IoImFtb3VudCBtdXN0IGJlID4gMCIsIDQwMCkKCiAgICBob3N0"
    "X2NyZWRpdFsiYmFsYW5jZSJdID0gcm91bmQoaG9zdF9jcmVkaXRbImJhbGFuY2UiXSArIGFtb3VudCwgMikKICAgIHJlc3VtZWQg"
    "PSBGYWxzZQogICAgaWYgaG9zdF9jcmVkaXRbImJhbGFuY2UiXSA+IDAgYW5kIGhvc3RfcGF1c2VkX3JlYXNvbiA9PSAiY3JlZGl0"
    "IjoKICAgICAgICBob3N0X3BhdXNlZCA9IEZhbHNlCiAgICAgICAgaG9zdF9wYXVzZWRfcmVhc29uID0gTm9uZQogICAgICAgIGhv"
    "c3RfY3JlZGl0WyJsb3dfY3JlZGl0X25vdGlmaWVkIl0gPSBGYWxzZQogICAgICAgIHJlc3VtZWQgPSBUcnVlCiAgICBzYXZlX3N0"
    "YXRlKCkKCiAgICBib3QgPSByZXF1ZXN0LmFwcC5nZXQoImJvdCIpCiAgICBpZiBib3QgaXMgbm90IE5vbmU6CiAgICAgICAgdHJ5"
    "OgogICAgICAgICAgICBtc2cgPSAoCiAgICAgICAgICAgICAgICBmIuKchSBbcmVtb3RlXSB7YW1vdW50Oi4yZn0g4Yml4YitIOGL"
    "iOGLsCDhi6jhiIbhiLXhibUg4Yqt4Yis4Yuy4Ym1IOGJoOGIq+GIsS3hiLDhiK0g4Ymw4Yyo4Yid4Yiv4YiNICjhi43hjKvhi4og"
    "4Yqt4Yis4Yuy4Ym1IOGJpuGJtSnhjaJcbiIKICAgICAgICAgICAgICAgIGYi8J+SsyDhiqDhi7LhiLEg4YiS4Yiz4Yml4Y2mIHto"
    "b3N0X2NyZWRpdFsnYmFsYW5jZSddOi4yZn0g4Yml4YitIgogICAgICAgICAgICApCiAgICAgICAgICAgIGlmIHJlc3VtZWQ6CiAg"
    "ICAgICAgICAgICAgICBtc2cgKz0gIlxuXG7ilrbvuI8g4Yi94Yur4YytIOGIq+GItS3hiLDhiK0g4Yql4YqV4Yuw4YyI4YqTIOGJ"
    "sOGMgOGIneGIr+GIjeGNoiIKICAgICAgICAgICAgYXdhaXQgYm90LnNlbmRfbWVzc2FnZShjaGF0X2lkPUFETUlOX0lELCB0ZXh0"
    "PW1zZykKICAgICAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICAgICBwYXNzCgogICAgcmV0dXJuIHdlYi5qc29uX3Jlc3Bv"
    "bnNlKHsKICAgICAgICAib2siOiBUcnVlLAogICAgICAgICJiYWxhbmNlIjogaG9zdF9jcmVkaXRbImJhbGFuY2UiXSwKICAgICAg"
    "ICAidG90YWxfZGVkdWN0ZWQiOiBob3N0X2NyZWRpdFsidG90YWxfZGVkdWN0ZWQiXSwKICAgICAgICAicmVzdW1lZCI6IHJlc3Vt"
    "ZWQsCiAgICB9KQoKCmFzeW5jIGRlZiBob3N0X2FwaV9yb3VuZHMocmVxdWVzdDogd2ViLlJlcXVlc3QpOgogICAgaWYgbm90IF9o"
    "b3N0X2FwaV9hdXRob3JpemVkKHJlcXVlc3QpOgogICAgICAgIHJldHVybiBfaG9zdF9hcGlfZXJyb3IoInVuYXV0aG9yaXplZCIp"
    "CiAgICByZXN1bHQgPSBbXQogICAgZm9yIHJpZCBpbiBzb3J0ZWQocm91bmRzKToKICAgICAgICByID0gcm91bmRzW3JpZF0KICAg"
    "ICAgICBhdmFpbGFibGUgPSBwZW5kaW5nID0gc29sZCA9IDAKICAgICAgICByZXZlbnVlID0gMC4wCiAgICAgICAgZm9yIHQgaW4g"
    "ci5nZXQoInRpY2tldHMiLCB7fSkudmFsdWVzKCk6CiAgICAgICAgICAgIHN0ID0gdC5nZXQoInN0YXR1cyIpCiAgICAgICAgICAg"
    "IGlmIHN0ID09ICJBVkFJTEFCTEUiOiBhdmFpbGFibGUgKz0gMQogICAgICAgICAgICBlbGlmIHN0ID09ICJQRU5ESU5HIjogcGVu"
    "ZGluZyArPSAxCiAgICAgICAgICAgIGVsaWYgc3QgPT0gIlNPTEQiOgogICAgICAgICAgICAgICAgc29sZCArPSAxCiAgICAgICAg"
    "ICAgICAgICByZXZlbnVlICs9IGZsb2F0KHIuZ2V0KCJwcmljZSIsIDApIG9yIDApCiAgICAgICAgcmVzdWx0LmFwcGVuZCh7CiAg"
    "ICAgICAgICAgICJyb3VuZF9pZCI6IHJpZCwKICAgICAgICAgICAgIm5hbWUiOiByLmdldCgibmFtZSIpLAogICAgICAgICAgICAi"
    "c3RhdHVzIjogci5nZXQoInN0YXR1cyIpLAogICAgICAgICAgICAibnVtX3RpY2tldHMiOiByLmdldCgibnVtX3RpY2tldHMiLCAw"
    "KSwKICAgICAgICAgICAgInByaWNlIjogci5nZXQoInByaWNlIiwgMCksCiAgICAgICAgICAgICJhdmFpbGFibGUiOiBhdmFpbGFi"
    "bGUsCiAgICAgICAgICAgICJwZW5kaW5nIjogcGVuZGluZywKICAgICAgICAgICAgInNvbGQiOiBzb2xkLAogICAgICAgICAgICAi"
    "c2FsZXMiOiByb3VuZChyZXZlbnVlLCAyKSwKICAgICAgICB9KQogICAgcmV0dXJuIHdlYi5qc29uX3Jlc3BvbnNlKHsib2siOiBU"
    "cnVlLCAiaG9zdF9zdGF0dXMiOiAicGF1c2VkIiBpZiBob3N0X3BhdXNlZCBlbHNlICJydW5uaW5nIiwgInJvdW5kcyI6IHJlc3Vs"
    "dH0pCgoKYXN5bmMgZGVmIHJ1bl9ob3N0X2NvbnRyb2xfYXBpKGJvdD1Ob25lKToKICAgIGFwaV9hcHAgPSB3ZWIuQXBwbGljYXRp"
    "b24oKQogICAgYXBpX2FwcFsiYm90Il0gPSBib3QKICAgIGFwaV9hcHAucm91dGVyLmFkZF9nZXQoIi9hcGkvaGVhbHRoIiwgaG9z"
    "dF9hcGlfaGVhbHRoKQogICAgYXBpX2FwcC5yb3V0ZXIuYWRkX2dldCgiL2FwaS9hZG1pbmluZm8iLCBob3N0X2FwaV9hZG1pbmlu"
    "Zm8pCiAgICBhcGlfYXBwLnJvdXRlci5hZGRfZ2V0KCIvYXBpL3N0YXR1cyIsIGhvc3RfYXBpX3N0YXR1cykKICAgIGFwaV9hcHAu"
    "cm91dGVyLmFkZF9nZXQoIi9hcGkvc3RhdGlzdGljcyIsIGhvc3RfYXBpX3N0YXRpc3RpY3MpCiAgICBhcGlfYXBwLnJvdXRlci5h"
    "ZGRfZ2V0KCIvYXBpL2NvbW1pc3Npb24iLCBob3N0X2FwaV9jb21taXNzaW9uKQogICAgYXBpX2FwcC5yb3V0ZXIuYWRkX2dldCgi"
    "L2FwaS9yb3VuZHMiLCBob3N0X2FwaV9yb3VuZHMpCiAgICBhcGlfYXBwLnJvdXRlci5hZGRfcG9zdCgiL2FwaS9wYXVzZSIsIGhv"
    "c3RfYXBpX3BhdXNlKQogICAgYXBpX2FwcC5yb3V0ZXIuYWRkX3Bvc3QoIi9hcGkvcmVzdW1lIiwgaG9zdF9hcGlfcmVzdW1lKQog"
    "ICAgYXBpX2FwcC5yb3V0ZXIuYWRkX3Bvc3QoIi9hcGkvYWRkY3JlZGl0IiwgaG9zdF9hcGlfYWRkY3JlZGl0KQogICAgcnVubmVy"
    "ID0gd2ViLkFwcFJ1bm5lcihhcGlfYXBwKQogICAgYXdhaXQgcnVubmVyLnNldHVwKCkKICAgIHNpdGUgPSB3ZWIuVENQU2l0ZShy"
    "dW5uZXIsIEhPU1RfQVBJX0hPU1QsIEhPU1RfQVBJX1BPUlQpCiAgICBhd2FpdCBzaXRlLnN0YXJ0KCkKICAgIHByaW50KGYi8J+T"
    "oSBIb3N0IENvbnRyb2wgQVBJOiBodHRwOi8ve0hPU1RfQVBJX0hPU1R9OntIT1NUX0FQSV9QT1JUfS9hcGkvc3RhdHVzIikKICAg"
    "IHJldHVybiBydW5uZXIKCgphc3luYyBkZWYgcnVuX3dlYmhvb2tfc2VydmVyKGJvdCk6CiAgICB3ZWJfYXBwID0gd2ViLkFwcGxp"
    "Y2F0aW9uKCkKICAgIHdlYl9hcHBbImJvdCJdID0gYm90CiAgICB3ZWJfYXBwLnJvdXRlci5hZGRfcG9zdCgiL3Ntcy13ZWJob29r"
    "Iiwgc21zX3dlYmhvb2tfaGFuZGxlcikKICAgIHJ1bm5lciA9IHdlYi5BcHBSdW5uZXIod2ViX2FwcCkKICAgIGF3YWl0IHJ1bm5l"
    "ci5zZXR1cCgpCiAgICBzaXRlID0gd2ViLlRDUFNpdGUocnVubmVyLCBXRUJIT09LX0hPU1QsIFdFQkhPT0tfUE9SVCkKICAgIGF3"
    "YWl0IHNpdGUuc3RhcnQoKQogICAgcHJpbnQoZiLwn5OhIOGLqFNNUyBXZWJob29rIOGIsOGIreGJqOGIrSDhiaAgaHR0cDovL3tX"
    "RUJIT09LX0hPU1R9OntXRUJIT09LX1BPUlR9L3Ntcy13ZWJob29rIOGIi+GLrSDhiqXhi6jhiLDhiKsg4YqQ4YuNLi4uIikKICAg"
    "IHJldHVybiBydW5uZXIKCgphc3luYyBkZWYgZXhwaXJlX3RpY2tldHNfbG9vcChib3QpOgogICAgIiIi4YuoMTAg4Yuw4YmC4YmD"
    "IOGJhuGLreGJsyDhi6vhiIjhiaPhibjhi43hipUg4Ymy4Yqs4Ym24Ym9IOGJoOGMgOGIreGJoyDhiILhi7DhibUg4Yut4YiI4YmD"
    "4YiN4Y2jIOGLqOGMoOGJoOGJgSDhibDhjKvhi4vhib7hib3hipXhiJ0g4Yur4Yiz4YuN4YmD4YiN4Y2iCiAgICDhiaXhi5kg4YmB"
    "4Yyl4YitIOGJoOGKoOGKleGLtSDhibXhi5Xhi5vhi50g4Yuo4Yur4YuYIOGJsOGMq+GLi+GJvSDhiqvhiIgg4YiB4YiJ4YidIOGJ"
    "geGMpeGIruGJvSDhiaDhiqDhipXhi7Ug4YiL4YutIOGIteGIiOGImuGLq+GIjeGJgSAo4Ymw4YiY4Yiz4Yiz4YutIGV4cGlyZXNf"
    "YXQg4Yi14YiL4YiL4Ym44YuNKSDhiqDhipXhi7Ug4YiL4YutIOGJsOGMoOGJg+GIiOGLjSDhi63hipDhjIjhiKvhiI3hjaIiIiIK"
    "ICAgIHdoaWxlIFRydWU6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBub3c9ZGF0ZXRpbWUubm93KCkKICAgICAgICAgICAgZXhw"
    "aXJlZF9ieV91c2VyID0ge30KICAgICAgICAgICAgbm9fb3duZXIgPSBbXQogICAgICAgICAgICBmb3IgcmlkLHIgaW4gbGlzdChy"
    "b3VuZHMuaXRlbXMoKSk6CiAgICAgICAgICAgICAgICBmb3IgdG4sdCBpbiBsaXN0KHIuZ2V0KCd0aWNrZXRzJyx7fSkuaXRlbXMo"
    "KSk6CiAgICAgICAgICAgICAgICAgICAgaWYgdC5nZXQoJ3N0YXR1cycpPT0nUEVORElORycgYW5kIHQuZ2V0KCdleHBpcmVzX2F0"
    "JykgYW5kIHRbJ2V4cGlyZXNfYXQnXSA8IG5vdzoKICAgICAgICAgICAgICAgICAgICAgICAgdWlkID0gdC5nZXQoJ3VzZXJfaWQn"
    "KQogICAgICAgICAgICAgICAgICAgICAgICBpZiB1aWQgaXMgbm90IE5vbmU6CiAgICAgICAgICAgICAgICAgICAgICAgICAgICBl"
    "eHBpcmVkX2J5X3VzZXIuc2V0ZGVmYXVsdCgodWlkLCByaWQpLCBbXSkuYXBwZW5kKHRuKQogICAgICAgICAgICAgICAgICAgICAg"
    "ICBlbHNlOgogICAgICAgICAgICAgICAgICAgICAgICAgICAgbm9fb3duZXIuYXBwZW5kKChyaWQsIHRuKSkKCiAgICAgICAgICAg"
    "IGZvciAodWlkLCByaWQpLCB0bnMgaW4gZXhwaXJlZF9ieV91c2VyLml0ZW1zKCk6CiAgICAgICAgICAgICAgICBzZWwgPSB1c2Vy"
    "X3NlbGVjdGlvbnMuZ2V0KHVpZCkKICAgICAgICAgICAgICAgIGlmIHNlbCBhbmQgc2VsLmdldCgncm91bmRfaWQnKSA9PSByaWQ6"
    "CiAgICAgICAgICAgICAgICAgICAgZGVsIHVzZXJfc2VsZWN0aW9uc1t1aWRdCiAgICAgICAgICAgICAgICBmb3IgdG4gaW4gdG5z"
    "OgogICAgICAgICAgICAgICAgICAgIGF3YWl0IHJlbGVhc2VfdGlja2V0KHJpZCwgdG4sIGJvdCwgbm90aWZ5PVRydWUpCiAgICAg"
    "ICAgICAgICAgICB0cnk6CiAgICAgICAgICAgICAgICAgICAgbnVtcyA9ICIsICIuam9pbihzdHIobikgZm9yIG4gaW4gc29ydGVk"
    "KHRucykpCiAgICAgICAgICAgICAgICAgICAgYXdhaXQgYm90LnNlbmRfbWVzc2FnZSgKICAgICAgICAgICAgICAgICAgICAgICAg"
    "Y2hhdF9pZD11aWQsCiAgICAgICAgICAgICAgICAgICAgICAgIHRleHQ9TCh1aWQsICJiYWNrZ3JvdW5kX2hvbGRfZXhwaXJlZCIs"
    "IG1pbnM9VElDS0VUX0hPTERfTUlOVVRFUywgcmxhYmVsPXJvdW5kX2xhYmVsKHJpZCksIHRucz1udW1zKSwKICAgICAgICAgICAg"
    "ICAgICAgICAgICAgcmVwbHlfbWFya3VwPXBsYXllcl9rZXlib2FyZChnZXRfbGFuZyh1aWQpKQogICAgICAgICAgICAgICAgICAg"
    "ICkKICAgICAgICAgICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgICAgICAgICAgcGFzcwoKICAgICAgICAgICAg"
    "Zm9yIHJpZCwgdG4gaW4gbm9fb3duZXI6CiAgICAgICAgICAgICAgICBhd2FpdCByZWxlYXNlX3RpY2tldChyaWQsIHRuLCBib3Qs"
    "IG5vdGlmeT1UcnVlKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgICAgICAgICAgbG9nZ2luZy5lcnJvcihmImV4"
    "cGlyZV90aWNrZXRzX2xvb3AgZXJyb3I6IHtlfSIpCiAgICAgICAgYXdhaXQgYXN5bmNpby5zbGVlcCgxNSkKCmFzeW5jIGRlZiBt"
    "YWluX2FzeW5jKCk6CiAgICBsb2FkX3N0YXRlKCkKICAgIGFwcCA9IEFwcGxpY2F0aW9uLmJ1aWxkZXIoKS50b2tlbihCT1RfVE9L"
    "RU4pLmJ1aWxkKCkKICAgIGFkbWluX2ZpbHRlciA9IGZpbHRlcnMuVXNlcih1c2VyX2lkPUFETUlOX0lEKQoKICAgICMgLS0tIOGI"
    "mOGIsOGIqOGJs+GLiiDhibXhi5Xhi5vhi57hib0gLS0tCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoInN0YXJ0"
    "Iiwgc3RhcnQpKQogICAgYXBwLmFkZF9oYW5kbGVyKENvbW1hbmRIYW5kbGVyKCJyb3VuZHMiLCByb3VuZHNfY29tbWFuZCkpCiAg"
    "ICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoInBsYXkiLCBwbGF5KSkKICAgIGFwcC5hZGRfaGFuZGxlcihDb21tYW5k"
    "SGFuZGxlcigicHVyY2hhc2VkIiwgbGlzdF9wdXJjaGFzZWQpKQogICAgYXBwLmFkZF9oYW5kbGVyKENvbW1hbmRIYW5kbGVyKCJh"
    "dmFpbGFibGUiLCBsaXN0X2F2YWlsYWJsZSkpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoImhlbHAiLCBoZWxw"
    "X2NvbW1hbmQpKQoKICAgICMgLS0tIOGLqOGKoOGLteGImuGKlSDhibXhi5Xhi5vhi57hib0gLS0tCiAgICBhcHAuYWRkX2hhbmRs"
    "ZXIoQ29tbWFuZEhhbmRsZXIoIm1hbnVhbHNlbGwiLCBtYW51YWxfc2VsbCkpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhh"
    "bmRsZXIoIm1hbnVhbGNhbmNlbCIsIG1hbnVhbF9jYW5jZWwpKQogICAgYXBwLmFkZF9oYW5kbGVyKENvbW1hbmRIYW5kbGVyKCJu"
    "ZXdyb3VuZCIsIG5ld19yb3VuZCkpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoImNsb3Nlcm91bmQiLCBjbG9z"
    "ZV9yb3VuZCkpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoInBhdXNlcm91bmQiLCBwYXVzZV9yb3VuZCkpCiAg"
    "ICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoInJlc3VtZXJvdW5kIiwgcmVzdW1lX3JvdW5kKSkKICAgIGFwcC5hZGRf"
    "aGFuZGxlcihDb21tYW5kSGFuZGxlcigicmVzdGFydHJvdW5kIiwgcmVzdGFydF9yb3VuZCkpCiAgICBhcHAuYWRkX2hhbmRsZXIo"
    "Q29tbWFuZEhhbmRsZXIoImRlbGV0ZXJvdW5kIiwgZGVsZXRlX3JvdW5kKSkKICAgIGFwcC5hZGRfaGFuZGxlcihDb21tYW5kSGFu"
    "ZGxlcigiZGVsZXRlYWxscm91bmRzIiwgZGVsZXRlX2FsbF9yb3VuZHMpKQogICAgYXBwLmFkZF9oYW5kbGVyKENvbW1hbmRIYW5k"
    "bGVyKCJyZXNldGZhY3RvcnkiLCByZXNldF9mYWN0b3J5X2RlZmF1bHQpKQogICAgYXBwLmFkZF9oYW5kbGVyKENvbW1hbmRIYW5k"
    "bGVyKCJhbm5vdW5jZSIsIGFubm91bmNlKSkKICAgIGFwcC5hZGRfaGFuZGxlcihDb21tYW5kSGFuZGxlcigiY2FuY2VsIiwgY2Fu"
    "Y2VsX2Jyb2FkY2FzdCkpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoIm5vdGlmeWRyYXciLCBub3RpZnlfZHJh"
    "dykpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoInNvbGQiLCBzb2xkX2xpc3QpKQogICAgYXBwLmFkZF9oYW5k"
    "bGVyKENvbW1hbmRIYW5kbGVyKCJ1bnNvbGQiLCB1bnNvbGRfbGlzdCkpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRs"
    "ZXIoImFsbHRpY2tldHMiLCBhbGxfdGlja2V0c19jb21tYW5kKSkKICAgIGFwcC5hZGRfaGFuZGxlcihDb21tYW5kSGFuZGxlcigi"
    "dXNlZHJlZnMiLCB1c2VkX3JlZnNfY29tbWFuZCkpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoInJlamVjdGVk"
    "cmVmcyIsIHJlamVjdGVkX3JlZnNfY29tbWFuZCkpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoInN0YXRzIiwg"
    "Z2FtZV9zdGF0cykpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoInBsYXllcnMiLCBwbGF5ZXJzX2NvbW1hbmQp"
    "KQogICAgYXBwLmFkZF9oYW5kbGVyKENvbW1hbmRIYW5kbGVyKCJob3N0cHJvZmlsZSIsIGhvc3RfcHJvZmlsZSkpCiAgICBhcHAu"
    "YWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoImVkaXRwYXltZW50IiwgZWRpdF9wYXltZW50X2FjY291bnQpKQogICAgYXBwLmFk"
    "ZF9oYW5kbGVyKENvbW1hbmRIYW5kbGVyKCJzZXRzbXN3ZWJob29rIiwgc2V0X3Ntc193ZWJob29rX2NvbW1hbmQpKQogICAgYXBw"
    "LmFkZF9oYW5kbGVyKENvbW1hbmRIYW5kbGVyKCJhZGRjcmVkaXQiLCBhZGRfY3JlZGl0KSkKICAgIGFwcC5hZGRfaGFuZGxlcihD"
    "b21tYW5kSGFuZGxlcigid2lubmVycyIsIHdpbm5lcnNfY29tbWFuZCkpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRs"
    "ZXIoInNldHdpbm5lciIsIHNldF93aW5uZXJfY29tbWFuZCkpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ29tbWFuZEhhbmRsZXIoIndp"
    "bm5lcnNsb3RzIiwgc2V0X3dpbm5lcl9zbG90c19jb21tYW5kKSkKCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhh"
    "bmRsZXIoaGFuZGxlX3RpY2tldCwgcGF0dGVybj1yIl50aWNrZXRfIikpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVy"
    "eUhhbmRsZXIoaGFuZGxlX3RpY2tldF9wYWdlLCBwYXR0ZXJuPXIiXnRpY2tldHBhZ2VfIikpCiAgICBhcHAuYWRkX2hhbmRsZXIo"
    "Q2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX2NhcnRfY2hlY2tvdXQsIHBhdHRlcm49ciJeY2FydGNoZWNrb3V0XyIpKQogICAg"
    "YXBwLmFkZF9oYW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVyKGhhbmRsZV9jYXJ0X2NvbmZpcm0sIHBhdHRlcm49ciJeY2FydGNv"
    "bmZpcm1fIikpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX2NhcnRfcmVqZWN0LCBwYXR0"
    "ZXJuPXIiXmNhcnRyZWplY3RfIikpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX2NhcnRf"
    "Y2xlYXIsIHBhdHRlcm49ciJeY2FydGNsZWFyXyIpKQogICAgYXBwLmFkZF9oYW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVyKGhh"
    "bmRsZV9ub29wX2NhbGxiYWNrLCBwYXR0ZXJuPXIiXm5vb3AkIikpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhh"
    "bmRsZXIoaGFuZGxlX3BheW1lbnRfbWV0aG9kLCBwYXR0ZXJuPXIiXnBheW1ldGhvZF8iKSkKICAgIGFwcC5hZGRfaGFuZGxlcihD"
    "YWxsYmFja1F1ZXJ5SGFuZGxlcihoYW5kbGVfY2hhbmdlX3BheW1lbnQsIHBhdHRlcm49ciJeY2hhbmdlcGF5XyIpKQogICAgYXBw"
    "LmFkZF9oYW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVyKGhhbmRsZV9rZWVwX3BheW1lbnQsIHBhdHRlcm49ciJea2VlcHBheV8i"
    "KSkKICAgIGFwcC5hZGRfaGFuZGxlcihDYWxsYmFja1F1ZXJ5SGFuZGxlcihoYW5kbGVfd2F0Y2hfdGlja2V0LCBwYXR0ZXJuPXIi"
    "XndhdGNoXyIpKQogICAgYXBwLmFkZF9oYW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVyKGhhbmRsZV9jYW5jZWxfcGVuZGluZ19z"
    "ZWxlY3Rpb24sIHBhdHRlcm49ciJeY2FuY2VscGVuZGluZ18iKSkKICAgIGFwcC5hZGRfaGFuZGxlcihDYWxsYmFja1F1ZXJ5SGFu"
    "ZGxlcihoYW5kbGVfcmV0dXJuX3RvX3BlbmRpbmcsIHBhdHRlcm49ciJecmV0dXJucGVuZGluZ18iKSkKICAgIGFwcC5hZGRfaGFu"
    "ZGxlcihDYWxsYmFja1F1ZXJ5SGFuZGxlcihoYW5kbGVfc2V0X2xhbmd1YWdlLCBwYXR0ZXJuPXIiXihzZXRsYW5nfHN3aXRjaGxh"
    "bmcpXyIpKQogICAgYXBwLmFkZF9oYW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVyKGhhbmRsZV91c2VkX3JlZnNfZmlsdGVyLCBw"
    "YXR0ZXJuPXIiXnVzZWRyZWZzXyIpKQogICAgYXBwLmFkZF9oYW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVyKGhhbmRsZV9yZWpl"
    "Y3RlZF9yZWZfYXBwcm92ZSwgcGF0dGVybj1yIl5yZWplY3RlZHJlZmFwcHJvdmVfIikpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2Fs"
    "bGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX3JlamVjdGVkX3JlZl9yZWplY3QsIHBhdHRlcm49ciJecmVqZWN0ZWRyZWZyZWplY3Rf"
    "IikpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX2Jyb2FkY2FzdF9jb25maXJtLCBwYXR0"
    "ZXJuPXIiXmJyb2FkY2FzdGNvbmZpcm0kIikpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxl"
    "X2Jyb2FkY2FzdF9jYW5jZWwsIHBhdHRlcm49ciJeYnJvYWRjYXN0Y2FuY2VsJCIpKQogICAgYXBwLmFkZF9oYW5kbGVyKENhbGxi"
    "YWNrUXVlcnlIYW5kbGVyKGhhbmRsZV9wbGF5ZXJfcm91bmRfcGljaywgcGF0dGVybj1yIl5wbGF5ZXJwaWNrXyIpKQogICAgYXBw"
    "LmFkZF9oYW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVyKGhhbmRsZV9hZG1pbl9yb3VuZF9waWNrLCBwYXR0ZXJuPXIiXmFkbWlu"
    "cGlja18iKSkKICAgIGFwcC5hZGRfaGFuZGxlcihDYWxsYmFja1F1ZXJ5SGFuZGxlcihoYW5kbGVfYWRtaW5fYWN0aW9uX2NvbmZp"
    "cm0sIHBhdHRlcm49ciJeYWRtaW5jb25maXJtXyIpKQogICAgYXBwLmFkZF9oYW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVyKGhh"
    "bmRsZV9hZG1pbl9hY3Rpb25fY2FuY2VsLCBwYXR0ZXJuPXIiXmFkbWluY2FuY2VsXyIpKQogICAgYXBwLmFkZF9oYW5kbGVyKENh"
    "bGxiYWNrUXVlcnlIYW5kbGVyKGhhbmRsZV9jYW5jZWxfdGlja2V0X3BpY2ssIHBhdHRlcm49ciJeY2FuY2VsdGlja2V0XyIpKQog"
    "ICAgYXBwLmFkZF9oYW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVyKGhhbmRsZV9zZXR3aW5uZXJfdGlja2V0X3BpY2ssIHBhdHRl"
    "cm49ciJec2V0d2lubmVydGlja2V0XyIpKQogICAgYXBwLmFkZF9oYW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVyKGhhbmRsZV93"
    "aW5uZXJfY29uZmlybSwgcGF0dGVybj1yIl53aW5uZXJjb25maXJtJCIpKQogICAgYXBwLmFkZF9oYW5kbGVyKENhbGxiYWNrUXVl"
    "cnlIYW5kbGVyKGhhbmRsZV93aW5uZXJfY2FuY2VsLCBwYXR0ZXJuPXIiXndpbm5lcmNhbmNlbCQiKSkKICAgIGFwcC5hZGRfaGFu"
    "ZGxlcihDYWxsYmFja1F1ZXJ5SGFuZGxlcihoYW5kbGVfbmV3cm91bmRfY29uZmlybSwgcGF0dGVybj1yIl5uZXdyb3VuZGNvbmZp"
    "cm0kIikpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX25ld3JvdW5kX2NhbmNlbCwgcGF0"
    "dGVybj1yIl5uZXdyb3VuZGNhbmNlbCQiKSkKICAgIGFwcC5hZGRfaGFuZGxlcihDYWxsYmFja1F1ZXJ5SGFuZGxlcihoYW5kbGVf"
    "bmV3cm91bmRfYmFja19idXR0b24sIHBhdHRlcm49ciJebmV3cm91bmRiYWNrJCIpKQogICAgYXBwLmFkZF9oYW5kbGVyKENhbGxi"
    "YWNrUXVlcnlIYW5kbGVyKGhhbmRsZV91bmRvX3JvdW5kLCBwYXR0ZXJuPXIiXnVuZG9yb3VuZF9cZCskIikpCiAgICBhcHAuYWRk"
    "X2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX3VuZG9fbWFudWFsX3NhbGUsIHBhdHRlcm49ciJedW5kb21hbnVh"
    "bHNhbGVfXGQrX1tcZFwtXSskIikpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX3VuZG9f"
    "d2lubmVyLCBwYXR0ZXJuPXIiXnVuZG93aW5uZXJfXGQrX1xkKyQiKSkKICAgIGFwcC5hZGRfaGFuZGxlcihDYWxsYmFja1F1ZXJ5"
    "SGFuZGxlcihoYW5kbGVfd2lubmVyc2xvdHNfY29uZmlybSwgcGF0dGVybj1yIl53aW5uZXJzbG90c2NvbmZpcm0kIikpCiAgICBh"
    "cHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX3dpbm5lcnNsb3RzX2NhbmNlbCwgcGF0dGVybj1yIl53"
    "aW5uZXJzbG90c2NhbmNlbCQiKSkKICAgIGFwcC5hZGRfaGFuZGxlcihDYWxsYmFja1F1ZXJ5SGFuZGxlcihoYW5kbGVfdGlja2V0"
    "X3JlbGVhc2VfY29uZmlybSwgcGF0dGVybj1yIl50aWNrZXRyZWxlYXNlY29uZmlybV8iKSkKICAgIGFwcC5hZGRfaGFuZGxlcihD"
    "YWxsYmFja1F1ZXJ5SGFuZGxlcihoYW5kbGVfdGlja2V0X3JlbGVhc2VfY2FuY2VsLCBwYXR0ZXJuPXIiXnRpY2tldHJlbGVhc2Vj"
    "YW5jZWxfIikpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX21hbnVhbF9zZWxsX3RvZ2ds"
    "ZSwgcGF0dGVybj1yIl5tYW51YWxzZWxsdG9nZ2xlXyIpKQogICAgYXBwLmFkZF9oYW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVy"
    "KGhhbmRsZV9tYW51YWxfc2VsbF9jb25maXJtLCBwYXR0ZXJuPXIiXm1hbnVhbHNlbGxjb25maXJtXyIpKQogICAgYXBwLmFkZF9o"
    "YW5kbGVyKENhbGxiYWNrUXVlcnlIYW5kbGVyKGhhbmRsZV9tYW51YWxfc2VsbF9tb2RlLCBwYXR0ZXJuPXIiXnNlbGxtb2RlXyhm"
    "dWxsfHNwbGl0KV9cZCskIikpCiAgICBhcHAuYWRkX2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX21hbnVhbF9z"
    "ZWxsX2NsZWFyLCBwYXR0ZXJuPXIiXm1hbnVhbHNlbGxjbGVhcl8iKSkKICAgIGFwcC5hZGRfaGFuZGxlcihDYWxsYmFja1F1ZXJ5"
    "SGFuZGxlcihoYW5kbGVfYWRkY3JlZGl0X21ldGhvZCwgcGF0dGVybj1yIl5hZGRjcmVkaXRtZXRob2RfIikpCiAgICBhcHAuYWRk"
    "X2hhbmRsZXIoQ2FsbGJhY2tRdWVyeUhhbmRsZXIoaGFuZGxlX2VkaXRfcGF5bWVudF9tZXRob2QsIHBhdHRlcm49ciJeZWRpdHBh"
    "eW1ldGhvZF8iKSkKICAgIGFwcC5hZGRfaGFuZGxlcihDYWxsYmFja1F1ZXJ5SGFuZGxlcihoYW5kbGVfcmVzZXRfZmFjdG9yeV9j"
    "b25maXJtLCBwYXR0ZXJuPXIiXnJlc2V0ZmFjdG9yeV8iKSkKICAgIGFwcC5hZGRfaGFuZGxlcihDYWxsYmFja1F1ZXJ5SGFuZGxl"
    "cihoYW5kbGVfbWFudWFsX3N0YXR1c19waWNrLCBwYXR0ZXJuPXIiXm1hbnVhbHN0YXR1c18iKSkKCiAgICAjIC0tLSDhi6jhibPh"
    "ib3hipvhi40g4Yic4YqRIOGJgeGIjeGNjuGJvSAo4YiB4YiJ4YidIOGJi+GKleGJiyAtIOGJsOGMq+GLi+GJveGInSDhiIbhiLXh"
    "ibXhiJ0g4Yuo4Yir4YixIOGJi+GKleGJiyDhiJjhiK3hjKYg4YiK4Yyt4YqT4Ym44YuNIOGIteGIiOGImuGJveGIjSkgLS0tCiAg"
    "ICBfcGxheWVyX2J0bl9rZXlzID0gWyJidG5fcGxheSIsICJidG5fcm91bmRzIiwgImJ0bl9teWluZm8iLCAiYnRuX2hlbHAiLCAi"
    "YnRuX2NvbnRhY3QiLCAiYnRuX2xhbmd1YWdlIiwKICAgICAgICAgICAgICAgICAgICAgICAgICJidG5fYmVjb21lX2hvc3QiLCAi"
    "YnRuX3dpbm5lcnMiXQogICAgX3BsYXllcl9idG5fdGV4dHMgPSBbVFhUW2tdW2xhbmddIGZvciBrIGluIF9wbGF5ZXJfYnRuX2tl"
    "eXMgZm9yIGxhbmcgaW4gTEFOR1NdCiAgICBfYWRtaW5fYnRuX2tleXMgPSBbImFkbWluX2J0bl9uZXdfcm91bmQiLCAiYWRtaW5f"
    "YnRuX3JvdW5kc19saXN0IiwgImFkbWluX2J0bl9wbGF5ZXJzIiwgImFkbWluX2J0bl9zb2xkIiwKICAgICAgICAgICAgICAgICAg"
    "ICAgICAgImFkbWluX2J0bl9hbGxfdGlja2V0cyIsICJhZG1pbl9idG5fdW5zb2xkIiwgImJ0bl93aW5uZXJzIiwgImFkbWluX2J0"
    "bl9zZXRfd2lubmVyIiwKICAgICAgICAgICAgICAgICAgICAgICAgImFkbWluX2J0bl9wYXltZW50cyIsICJhZG1pbl9idG5fc3Rh"
    "dHMiLCAiYWRtaW5fYnRuX3BhdXNlIiwgImFkbWluX2J0bl9yZXN1bWUiLAogICAgICAgICAgICAgICAgICAgICAgICAiYWRtaW5f"
    "YnRuX2Nsb3NlX3JvdW5kIiwgImFkbWluX2J0bl9yZXN0YXJ0X3JvdW5kIiwgImFkbWluX2J0bl9kZWxldGVfcm91bmQiLAogICAg"
    "ICAgICAgICAgICAgICAgICAgICAiYWRtaW5fYnRuX21hbnVhbF9zYWxlIiwgImFkbWluX2J0bl9yZWxlYXNlX3RpY2tldCIsICJh"
    "ZG1pbl9idG5faG9zdF9wcm9maWxlIiwKICAgICAgICAgICAgICAgICAgICAgICAgImFkbWluX2J0bl9hZGRfY3JlZGl0IiwgImFk"
    "bWluX2J0bl9wYXltZW50X2FjY291bnQiLCAiYWRtaW5fYnRuX2Fubm91bmNlIiwKICAgICAgICAgICAgICAgICAgICAgICAgImFk"
    "bWluX2J0bl9oZWxwIiwgImJ0bl9sYW5ndWFnZSJdCiAgICBfYWRtaW5fYnRuX3RleHRzID0gW1RYVFtrXVtsYW5nXSBmb3IgayBp"
    "biBfYWRtaW5fYnRuX2tleXMgZm9yIGxhbmcgaW4gTEFOR1NdCiAgICBfYWxsX21lbnVfdGV4dHMgPSAifCIuam9pbihyZS5lc2Nh"
    "cGUodCkgZm9yIHQgaW4gKF9wbGF5ZXJfYnRuX3RleHRzICsgX2FkbWluX2J0bl90ZXh0cykpCiAgICBtZW51X2ZpbHRlciA9IGZp"
    "bHRlcnMuUmVnZXgoZiJeKHtfYWxsX21lbnVfdGV4dHN9KSQiKQogICAgYXBwLmFkZF9oYW5kbGVyKE1lc3NhZ2VIYW5kbGVyKGZp"
    "bHRlcnMuVEVYVCAmIGZpbHRlcnMuQ2hhdFR5cGUuUFJJVkFURSAmIG1lbnVfZmlsdGVyLCBoYW5kbGVfbWVudV9idXR0b24pKQoK"
    "ICAgICMgLS0tIOGLqOGJsOGMq+GLi+GJvSDhiJ3hi53hjIjhiaMgKOGIteGInS/hiLXhiI3hiq0pIC0tLQogICAgYXBwLmFkZF9o"
    "YW5kbGVyKE1lc3NhZ2VIYW5kbGVyKAogICAgICAgIGZpbHRlcnMuVEVYVCAmIH5maWx0ZXJzLkNPTU1BTkQgJiBmaWx0ZXJzLkNo"
    "YXRUeXBlLlBSSVZBVEUgJiByZWdpc3RyYXRpb25fZmlsdGVyLAogICAgICAgIGhhbmRsZV9yZWdpc3RyYXRpb24KICAgICkpCgog"
    "ICAgIyAtLS0g4Yuo4Yqg4Yu14Yia4YqVIOGMveGIgeGNjSAoU01TIOGNjuGIreGLi+GIreGLtSAvIGFwcHJvdmUtcmVqZWN0IC8g"
    "4Yib4Yi14Ymz4YuI4YmC4YurIOGMveGIgeGNjSkgLS0tCiAgICBhcHAuYWRkX2hhbmRsZXIoTWVzc2FnZUhhbmRsZXIoZmlsdGVy"
    "cy5URVhUICYgZmlsdGVycy5DaGF0VHlwZS5QUklWQVRFICYgYWRtaW5fZmlsdGVyLCBoYW5kbGVfYWRtaW5fc21zX29yX2NvbW1h"
    "bmRzKSkKCiAgICAjIC0tLSDhi6jhibDhjKvhi4vhib0g4Yuw4Yio4Yiw4YqdIHNjcmVlbnNob3QgLS0tCiAgICBhcHAuYWRkX2hh"
    "bmRsZXIoTWVzc2FnZUhhbmRsZXIoCiAgICAgICAgZmlsdGVycy5QSE9UTyAmIGZpbHRlcnMuQ2hhdFR5cGUuUFJJVkFURSAmIH5h"
    "ZG1pbl9maWx0ZXIsCiAgICAgICAgaGFuZGxlX3JlY2VpcHRfcGhvdG8KICAgICkpCgogICAgIyAtLS0g4Yuo4Yqg4Yu14Yia4YqV"
    "IOGNjuGJti/hiarhi7Xhi64v4Yu14Yid4Yy9L+GIsOGKkOGLtSAo4YiI4Yib4Yi14Ymz4YuI4YmC4YurKSAtLS0KICAgIGFwcC5h"
    "ZGRfaGFuZGxlcihNZXNzYWdlSGFuZGxlcigKICAgICAgICAoZmlsdGVycy5QSE9UTyB8IGZpbHRlcnMuVklERU8gfCBmaWx0ZXJz"
    "LlZPSUNFIHwgZmlsdGVycy5BVURJTyB8IGZpbHRlcnMuRG9jdW1lbnQuQUxMKQogICAgICAgICYgZmlsdGVycy5DaGF0VHlwZS5Q"
    "UklWQVRFICYgYWRtaW5fZmlsdGVyLAogICAgICAgIGhhbmRsZV9hZG1pbl9tZWRpYQogICAgKSkKCiAgICAjIC0tLSDhi6jhibDh"
    "jKvhi4vhib0g4Yiq4Y2I4Yio4YqV4Yi1IOGJgeGMpeGIrSAo4Yqg4Yu14Yia4YqR4YqVIOGIs+GLreGMqOGIneGIrSkgLS0tCiAg"
    "ICBhcHAuYWRkX2hhbmRsZXIoTWVzc2FnZUhhbmRsZXIoCiAgICAgICAgZmlsdGVycy5URVhUICYgfmZpbHRlcnMuQ09NTUFORCAm"
    "IGZpbHRlcnMuQ2hhdFR5cGUuUFJJVkFURSAmIH5hZG1pbl9maWx0ZXIsCiAgICAgICAgaGFuZGxlX3VzZXJfcmVmCiAgICApKQoK"
    "ICAgICMgLS0tIOGJpuGJseGKlSDhiqXhipMg4YuoU01TIFdlYmhvb2sg4Yiw4Yit4Ymo4Yit4YqVIOGKoOGJpeGIqOGLjSDhiJvh"
    "iLXhipDhiLPhibUgLS0tCiAgICAjIOGLqMKrL8K7IOGJteGLleGLm+GLnuGJvSDhiJ3hipPhiIwgKOGLqOGImOGIjeGLleGKreGJ"
    "tSDhiLPhjKXhipEg4Yqg4Yyg4YyI4YmlIOGLq+GIiOGLjSDhibXhipXhiL0g4Yid4YqT4YiMIOGKoOGLtikgLSDhiIjhibDhjKvh"
    "i4vhib7hib0g4Yql4YqTIOGIiOGKoOGLteGImuGKkSDhi6jhibDhiIjhi6vhi6gg4Yud4Yit4Yud4YitCiAgICB0cnk6CiAgICAg"
    "ICAgYXdhaXQgYXBwLmJvdC5zZXRfbXlfY29tbWFuZHMoCiAgICAgICAgICAgIFsKICAgICAgICAgICAgICAgIEJvdENvbW1hbmQo"
    "InBsYXkiLCAi8J+OriDhibLhiqzhibUg4YiI4YiY4YyN4Yub4Ym1IOGLmeGIrSDhi63hiJ3hiKjhjKEgLyBQbGF5IiksCiAgICAg"
    "ICAgICAgICAgICBCb3RDb21tYW5kKCJyb3VuZHMiLCAi8J+TiyDhi6vhiInhibXhipUg4YuZ4Yiu4Ym9IOGLreGImOGIjeGKqOGJ"
    "sSAvIExpc3Qgcm91bmRzIiksCiAgICAgICAgICAgICAgICBCb3RDb21tYW5kKCJwdXJjaGFzZWQiLCAi8J+On++4jyDhi6jhjIjh"
    "i5nhi4vhibjhi40g4Ymy4Yqs4Ym24Ym9IC8gTXkgdGlja2V0cyIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgiYXZhaWxh"
    "YmxlIiwgIvCfn6Ig4Yur4YiN4Ymw4Yur4YuZIOGJgeGMpeGIruGJvSAvIEF2YWlsYWJsZSBudW1iZXJzIiksCiAgICAgICAgICAg"
    "ICAgICBCb3RDb21tYW5kKCJ3aW5uZXJzIiwgIvCfj4Yg4Yqg4Yi44YqT4Y2K4YuO4Ym9IC8gV2lubmVycyIpLAogICAgICAgICAg"
    "ICAgICAgQm90Q29tbWFuZCgiaGVscCIsICLwn4aYIOGKpeGIreGLs+GJsyAvIEhlbHAiKSwKICAgICAgICAgICAgXSwKICAgICAg"
    "ICAgICAgc2NvcGU9Qm90Q29tbWFuZFNjb3BlRGVmYXVsdCgpLAogICAgICAgICkKICAgICAgICBhd2FpdCBhcHAuYm90LnNldF9t"
    "eV9jb21tYW5kcygKICAgICAgICAgICAgWwogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgibmV3cm91bmQiLCAi4p6VIOGKoOGL"
    "suGItSDhi5nhiK0g4Yut4Yqt4Y2I4YmxIiksCiAgICAgICAgICAgICAgICBCb3RDb21tYW5kKCJyb3VuZHMiLCAi8J+TiyDhi5nh"
    "iK7hib0g4Yud4Yit4Yud4YitIiksCiAgICAgICAgICAgICAgICBCb3RDb21tYW5kKCJwbGF5ZXJzIiwgIvCfkaUg4Ymw4Yyr4YuL"
    "4Ym+4Ym9IiksCiAgICAgICAgICAgICAgICBCb3RDb21tYW5kKCJzb2xkIiwgIvCfjp/vuI8g4Yuo4Ymw4Yi44YyhIOGJsuGKrOGJ"
    "tuGJvSIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgidW5zb2xkIiwgIvCfn6Ig4Yur4YiN4Ymw4Yi44YyhIOGJsuGKrOGJ"
    "tuGJvSIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgiYWxsdGlja2V0cyIsICLwn5eC77iPIOGIgeGIieGInSDhibLhiqzh"
    "ibbhib0iKSwKICAgICAgICAgICAgICAgIEJvdENvbW1hbmQoInVzZWRyZWZzIiwgIvCfkrAg4Yuo4Ymw4Yyg4YmA4YiZIOGKreGN"
    "jeGLq+GLjuGJvSIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgicmVqZWN0ZWRyZWZzIiwgIvCfmqsg4YuN4Yu14YmFIOGL"
    "qOGJsOGLsOGIqOGMiSDhiKrhjYjhiKjhipXhiLbhib0iKSwKICAgICAgICAgICAgICAgIEJvdENvbW1hbmQoInN0YXRzIiwgIvCf"
    "k4og4Yi14Ymz4Ymy4Yi14Ymy4Yqt4Yi1IiksCiAgICAgICAgICAgICAgICBCb3RDb21tYW5kKCJob3N0cHJvZmlsZSIsICLwn4+g"
    "IOGLqOGIhuGIteGJtSDhiJjhjIjhiIjhjKsiKSwKICAgICAgICAgICAgICAgIEJvdENvbW1hbmQoImFkZGNyZWRpdCIsICLinpUg"
    "4Yqt4Yis4Yuy4Ym1IOGMqOGIneGIrSIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgiZWRpdHBheW1lbnQiLCAi8J+SsyDh"
    "iq3hjY3hi6sg4Yqg4Yqr4YuN4YqV4Ym1IiksCiAgICAgICAgICAgICAgICBCb3RDb21tYW5kKCJzZXRzbXN3ZWJob29rIiwgIvCf"
    "lJcgU01TIFdlYmhvb2sgVVJMIiksCiAgICAgICAgICAgICAgICBCb3RDb21tYW5kKCJtYW51YWxzZWxsIiwgIvCfkrUg4Ymg4Yql"
    "4YyFIOGIveGLq+GMrSIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgibWFudWFsY2FuY2VsIiwgIvCflJMg4Ymy4Yqs4Ym1"
    "IOGIjeGJgOGJhSIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgicGF1c2Vyb3VuZCIsICLij7jvuI8g4Yi94Yur4YytIOGK"
    "oOGJgeGInSIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgicmVzdW1lcm91bmQiLCAi4pa277iPIOGIveGLq+GMrSDhiYDh"
    "jKXhiI0iKSwKICAgICAgICAgICAgICAgIEJvdENvbW1hbmQoImNsb3Nlcm91bmQiLCAi8J+UkiDhi5nhiK0g4Yud4YyLIiksCiAg"
    "ICAgICAgICAgICAgICBCb3RDb21tYW5kKCJyZXN0YXJ0cm91bmQiLCAi8J+UhCDhi5nhiK0g4Yql4YqV4Yuw4YyI4YqTIOGMgOGI"
    "neGIrSIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgiZGVsZXRlcm91bmQiLCAi8J+Xke+4jyDhi5nhiK0g4Yiw4Yit4Yud"
    "IiksCiAgICAgICAgICAgICAgICBCb3RDb21tYW5kKCJub3RpZnlkcmF3IiwgIvCfjq8g4Yuo4Yql4YyjIOGIm+GIs+GLiOGJguGL"
    "qyIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgic2V0d2lubmVyIiwgIvCfjq8g4Yqg4Yi44YqT4Y2KIOGImOGLneGMjeGJ"
    "pSIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgid2lubmVyc2xvdHMiLCAi8J+PhSDhi6jhiqDhiLjhipPhjYog4Ymm4Ymz"
    "4YuO4Ym9IOGJpeGLm+GJtSIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgid2lubmVycyIsICLwn4+GIOGKoOGIuOGKk+GN"
    "iuGLjuGJvSIpLAogICAgICAgICAgICAgICAgQm90Q29tbWFuZCgiYW5ub3VuY2UiLCAi8J+ToiDhiJvhiLXhibPhi4jhiYLhi6si"
    "KSwKICAgICAgICAgICAgICAgIEJvdENvbW1hbmQoImhlbHAiLCAi8J+GmCDhiqXhiK3hi7PhibMiKSwKICAgICAgICAgICAgXSwK"
    "ICAgICAgICAgICAgc2NvcGU9Qm90Q29tbWFuZFNjb3BlQ2hhdChjaGF0X2lkPUFETUlOX0lEKSwKICAgICAgICApCiAgICBleGNl"
    "cHQgRXhjZXB0aW9uIGFzIGU6CiAgICAgICAgbG9nZ2luZy5lcnJvcihmInNldF9teV9jb21tYW5kcyBlcnJvcjoge2V9IikKCiAg"
    "ICBhd2FpdCBhcHAuaW5pdGlhbGl6ZSgpCiAgICBhd2FpdCBhcHAuc3RhcnQoKQogICAgYXdhaXQgYXBwLnVwZGF0ZXIuc3RhcnRf"
    "cG9sbGluZygpCiAgICBydW5uZXIgPSBhd2FpdCBydW5fd2ViaG9va19zZXJ2ZXIoYXBwLmJvdCkKICAgIGhvc3RfYXBpX3J1bm5l"
    "ciA9IGF3YWl0IHJ1bl9ob3N0X2NvbnRyb2xfYXBpKGFwcC5ib3QpCiAgICBleHBpcnlfdGFzayA9IGFzeW5jaW8uY3JlYXRlX3Rh"
    "c2soZXhwaXJlX3RpY2tldHNfbG9vcChhcHAuYm90KSkKCiAgICBwcmludCgi4Ymm4YmxIOGJoOGJsOGIs+GKqyDhiIHhipThibMg"
    "4Ymw4YqQ4Yi14Ym34YiNLi4uIikKICAgIHRyeToKICAgICAgICBhd2FpdCBhc3luY2lvLkV2ZW50KCkud2FpdCgpCiAgICBmaW5h"
    "bGx5OgogICAgICAgIGV4cGlyeV90YXNrLmNhbmNlbCgpCiAgICAgICAgdHJ5OgogICAgICAgICAgICBhd2FpdCBleHBpcnlfdGFz"
    "awogICAgICAgIGV4Y2VwdCBhc3luY2lvLkNhbmNlbGxlZEVycm9yOgogICAgICAgICAgICBwYXNzCiAgICAgICAgYXdhaXQgcnVu"
    "bmVyLmNsZWFudXAoKQogICAgICAgIGF3YWl0IGhvc3RfYXBpX3J1bm5lci5jbGVhbnVwKCkKICAgICAgICBhd2FpdCBhcHAudXBk"
    "YXRlci5zdG9wKCkKICAgICAgICBhd2FpdCBhcHAuc3RvcCgpCiAgICAgICAgYXdhaXQgYXBwLnNodXRkb3duKCkKCmRlZiBtYWlu"
    "KCk6CiAgICB0cnk6CiAgICAgICAgYXN5bmNpby5ydW4obWFpbl9hc3luYygpKQogICAgZXhjZXB0IEtleWJvYXJkSW50ZXJydXB0"
    "OgogICAgICAgIHByaW50KCJcbvCfm5Eg4Ymm4YmxIOGJsOGJi+GIreGMp+GIjeGNoiIpCiAgICBleGNlcHQgRXhjZXB0aW9uOgog"
    "ICAgICAgIGltcG9ydCB0cmFjZWJhY2sKICAgICAgICBwcmludCgiXG7inYwg4Ymm4YmxIOGIsuGKkOGIsyDhi4jhi63hiJ0g4Yql"
    "4Yuo4Yiw4YirIOGIs+GIiCDhiLXhiIXhibDhibUg4Ymw4Y2I4Yyl4Yiv4YiN4Y2mXG4iKQogICAgICAgIHRyYWNlYmFjay5wcmlu"
    "dF9leGMoKQogICAgICAgIHByaW50KAogICAgICAgICAgICAiXG7hi6jhibDhiIjhiJjhi7Eg4Yid4Yqt4YqV4Yur4Ym24Ym94Y2m"
    "XG4iCiAgICAgICAgICAgICIgIOKAoiBCT1RfVE9LRU4g4Yuo4Ymw4Yiz4Yiz4YmwIOGLiOGLreGInSDhiqIt4Ym14Yqt4Yqt4YiI"
    "4YqbIOGKqOGIhuGKkCAoVW5hdXRob3JpemVkKVxuIgogICAgICAgICAgICAiICDigKIg4Y2W4Yit4Ym1IDgwODEg4YmA4Yu14Yie"
    "IOGJoOGIjOGIiyDhjZXhiK7hjI3hiKvhiJ0g4Ymw4Yut4YueIOGKqOGIhuGKkCAoQWRkcmVzcyBhbHJlYWR5IGluIHVzZSlcbiIK"
    "ICAgICAgICAgICAgIiAg4oCiIOGLqOGKouGKleGJsOGIreGKlOGJtSDhjI3hipXhipnhipDhibUg4Yqo4YiM4YiIXG4iCiAgICAg"
    "ICAgKQogICAgZmluYWxseToKICAgICAgICBpbnB1dCgiXG7hiJjhiLXhiq7hibHhipUg4YiI4YiY4Yud4YyL4Ym1IEVudGVyIOGL"
    "reGMq+GKkS4uLiIpCgppZiBfX25hbWVfXyA9PSAiX19tYWluX18iOgogICAgbWFpbigpCg=="
)

# =============================================================================
# State: hosts + credit requests
# =============================================================================
# hosts[host_id] = {
#     "bot_token", "admin_id", "host_api_port", "webhook_port",
#     "host_api_secret", "name", "created_at", "running": bool,
#     "last_error": str|None,
# }
hosts = {}
credit_requests = {}
host_signup_requests = {}
_counters = {"next_host_num": 1, "next_request_num": 1}

# Running instances: host_id -> {"namespace": module_globals_dict, "task": asyncio.Task}
_live = {}

# --- ሚስጥራዊ (sensitive) ትዕዛዞች ማረጋገጫ በመጠባበቅ ላይ ---
# /editcommission እና /addcredit ገንዘብ ነክ/ተግባራዊ ተፅእኖ ስላላቸው ከመፈጸማቸው በፊት Super Admin
# በ✅/❌ ማረጋገጫ ማለፍ አለበት - super_admin_id -> {ዝርዝር መረጃ}
pending_commission_confirm = {}  # {"host_id": str, "percent": float, "old_percent": float}
pending_addcredit_confirm = {}   # {"host_id": str, "amount": float}
addcredit_state = {}      # uid -> host_id (awaiting amount text)
editcommission_state = {}  # uid -> host_id (awaiting percent text)


def save_state():
    try:
        persistable_hosts = {
            hid: {k: v for k, v in h.items() if k not in ("running", "last_error")}
            for hid, h in hosts.items()
        }
        STORE.write_json(
            "superadmin_state.json",
            {
                "hosts": persistable_hosts,
                "credit_requests": credit_requests,
                "host_signup_requests": host_signup_requests,
                "counters": _counters,
            },
        )
    except Exception as e:
        log.error(f"save_state error: {e}")


def load_state():
    global hosts, credit_requests, host_signup_requests, _counters
    data = STORE.read_json("superadmin_state.json")
    if data is None:
        return
    try:
        hosts = data.get("hosts", {})
        for h in hosts.values():
            h.setdefault("running", False)
            h.setdefault("last_error", None)
            h.setdefault("revoked", False)
        credit_requests = data.get("credit_requests", {})
        host_signup_requests = data.get("host_signup_requests", {})
        _counters = data.get("counters", {"next_host_num": 1, "next_request_num": 1})
    except Exception as e:
        log.error(f"load_state error: {e}")


def _new_host_id():
    n = _counters.get("next_host_num", 1)
    _counters["next_host_num"] = n + 1
    return f"H{n:03d}"


def _new_request_id():
    n = _counters.get("next_request_num", 1)
    _counters["next_request_num"] = n + 1
    return f"R{n:04d}"


def _new_host_signup_request_id():
    n = _counters.get("next_hostreq_num", 1)
    _counters["next_hostreq_num"] = n + 1
    return f"HR{n:04d}"


def _next_free_port(base, used_ports):
    p = base
    while p in used_ports:
        p += 1
    return p


connect_state = {}  # uid -> {"step": ...}


def _is_super_admin(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID


# =============================================================================
# Dynamically launching a jemo_2 host instance inside THIS SAME process
# =============================================================================

def _decode_jemo_source():
    return base64.b64decode(JEMO_SOURCE_B64.encode("ascii")).decode("utf-8")


async def _launch_host_instance(host_id, bot: "telegram.Bot" = None):
    """Compiles + execs a fresh copy of the jemo_2 host bot source with this
    host's config injected, then schedules its main_async() as a background
    task in the current event loop. Isolated failures don't crash the rest."""
    h = hosts[host_id]
    if h.get("revoked"):
        return False, "ይህ ሆስት ተሰርዟል (revoked)። መጀመሪያ /unrevokehost ተጠቅመው ያንቁት።"
    host_dir = os.path.join(HOSTS_DIR, host_id)
    os.makedirs(host_dir, exist_ok=True)

    injected = {
        "bot_token": h["bot_token"],
        "admin_id": h["admin_id"],
        "webhook_port": h["webhook_port"],
        "host_api_secret": h["host_api_secret"],
        "host_api_port": h["host_api_port"],
        "credit_seller_api_url": f"http://{API_HOST}:{API_PORT}",
        "host_id": host_id,
        "super_admin_id": SUPER_ADMIN_ID,
        "credit_seller_payment_methods": CREDIT_SELLER_PAYMENT_METHODS,
        "commission_percent": h.get("commission_percent", 5.0),
        # Same live PersistentStore abstraction as this file's own
        # save_state()/load_state() -- writes lottery_state.json to S3 when
        # S3_BUCKET is configured, local disk under DATA_DIR/hosts/<id>/
        # otherwise. Passed as a real object (same process/interpreter), not
        # serialized, so jemo_2's save_state/load_state just call it.
        "storage": PersistentStore(prefix=f"hosts/{host_id}"),
    }

    source = _decode_jemo_source()
    namespace = {
        "__name__": f"jemo_host_{host_id}",
        "__file__": os.path.join(host_dir, "jemo_2.py"),
        "__INJECTED__": injected,
    }

    try:
        code = compile(source, filename=f"<jemo_host_{host_id}>", mode="exec")
        exec(code, namespace)
    except Exception as e:
        h["running"] = False
        h["last_error"] = f"compile/exec error: {e}"
        save_state()
        log.error(f"[{host_id}] failed to load host code: {e}")
        return False, str(e)

    main_async_fn = namespace.get("main_async")
    if main_async_fn is None:
        h["running"] = False
        h["last_error"] = "main_async() not found in host source"
        save_state()
        return False, "main_async() not found in host source"

    async def _runner():
        try:
            h["running"] = True
            h["last_error"] = None
            save_state()
            await main_async_fn()
        except InvalidToken:
            h["running"] = False
            h["last_error"] = "Invalid bot token"
            save_state()
            log.error(f"[{host_id}] Invalid bot token")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            h["running"] = False
            h["last_error"] = str(e)
            save_state()
            log.error(f"[{host_id}] crashed: {e}")

    task = asyncio.create_task(_runner(), name=f"host_{host_id}")
    _live[host_id] = {"namespace": namespace, "task": task}
    return True, None


async def _stop_host_instance(host_id):
    live = _live.pop(host_id, None)
    if live and not live["task"].done():
        live["task"].cancel()
        try:
            await live["task"]
        except (asyncio.CancelledError, Exception):
            pass
    hosts[host_id]["running"] = False
    save_state()


# =============================================================================
# Host Control (HTTP calls into each dynamically-launched host's own
# /api/... server, exactly as jemo_2.py already exposes it - unchanged)
# =============================================================================

async def _host_api_get(host, path, params=None):
    url = f"http://127.0.0.1:{host['host_api_port']}{path}"
    headers = {"X-Host-API-Key": host["host_api_secret"]}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params,
                                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                return resp.status, data
    except Exception as e:
        return 0, {"ok": False, "error": str(e)}


async def _host_api_post(host, path, json_body=None):
    url = f"http://127.0.0.1:{host['host_api_port']}{path}"
    headers = {"X-Host-API-Key": host["host_api_secret"], "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=json_body or {},
                                     timeout=aiohttp.ClientTimeout(total=15)) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                return resp.status, data
    except Exception as e:
        return 0, {"ok": False, "error": str(e)}


def _host_label(host_id, host):
    base = f"{host_id}({host.get('name') or host_id})"
    uname = host.get("admin_username")
    if uname:
        base += f" 👤@{uname}"
    elif host.get("admin_first_name"):
        base += f" 👤{host['admin_first_name']}"
    return f"🚫 {base} [revoked]" if host.get("revoked") else base


async def _ensure_admin_username(host_id, host):
    """የሆስቱን አድሚን የቴሌግራም username (ካለው) ፈልጎ በ host dict ውስጥ ያስቀምጣል፣ ስለዚህ ቀጣይ ጊዜ ደግሞ መጠየቅ አያስፈልግም።
    ሆስቱ እየሮጠ ካልሆነ ወይም ቀድሞ ተፈትሾ ከሆነ ምንም አያደርግም (ደግሞ ደጋግሞ አላስፈላጊ API ጥሪ እንዳይደረግ)።"""
    if host.get("admin_username_checked"):
        return
    if not host.get("running"):
        return
    status, data = await _host_api_get(host, "/api/admininfo")
    if status == 200 and data.get("ok"):
        host["admin_username"] = data.get("username") or ""
        host["admin_first_name"] = data.get("first_name") or ""
        host["admin_username_checked"] = True
        save_state()


def _fmt_status_reply(host_id, host, status, data):
    if status == 0:
        return f"❌ {_host_label(host_id, host)} ላይ መድረስ አልተቻለም፦\n{data.get('error')}"
    if not data.get("ok"):
        return f"❌ {_host_label(host_id, host)} ስህተት፦ {data.get('error')}"
    state_emoji = "🟢 እየሸጠ ነው" if data.get("selling_enabled") else "⏸️ ቆሟል"
    return (
        f"🏠 <b>{_host_label(host_id, host)}</b>\n{state_emoji}\n"
        f"💳 ክሬዲት ሒሳብ፦ {data.get('credit_balance', data.get('balance', 0)):.2f} ብር\n"
        f"👥 ተመዝጋቢዎች፦ {data.get('registered_players', '-')}\n"
        f"📋 ዙሮች፦ ክፍት {data.get('open_rounds', '-')} / ቆመ {data.get('paused_rounds', '-')} / ተዘጋ {data.get('closed_rounds', '-')}\n"
        f"🎟️ ቲኬቶች፦ ተሸጠ {data.get('sold_tickets', '-')} / ጠባቂ {data.get('pending_tickets', '-')} / ነጻ {data.get('available_tickets', '-')}\n"
        f"💰 ሽያጭ፦ {data.get('sales', 0):.2f} ብር | ኮሚሽን፦ {data.get('commission', 0):.2f} ብር"
    )


def _host_menu_kb(host_id):
    host = hosts.get(host_id, {})
    revoke_row = (
        [InlineKeyboardButton("✅ መልስ አንቃ (Unrevoke)", callback_data=f"hunrevoke_{host_id}")]
        if host.get("revoked") else
        [InlineKeyboardButton("🚫 አሰናክል (Revoke)", callback_data=f"hrevoke_{host_id}")]
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 ሁኔታ", callback_data=f"hstatus_{host_id}"),
         InlineKeyboardButton("📋 ዙሮች", callback_data=f"hrounds_{host_id}")],
        [InlineKeyboardButton("⏸️ አቁም", callback_data=f"hpause_{host_id}"),
         InlineKeyboardButton("▶️ ቀጥል", callback_data=f"hresume_{host_id}")],
        [InlineKeyboardButton("💰 ኮሚሽን", callback_data=f"hcomm_{host_id}"),
         InlineKeyboardButton("✏️ ኮሚሽን ቀይር", callback_data=f"heditcomm_{host_id}")],
        [InlineKeyboardButton("➕ ክሬዲት ጨምር", callback_data=f"haddcredit_{host_id}")],
        [InlineKeyboardButton("🔄 እንደገና አስነሳ", callback_data=f"hrestart_{host_id}")],
        revoke_row,
    ])


SUPERADMIN_MENU_BUTTONS = {
    "🆕 አዲስ ሆስት መዝግብ": "newhost",
    "🏠 ሆስቶች ዝርዝር": "hosts",
    "📊 ስታቲስቲክስ": "stats",
    "💰 የክሬዲት ጥያቄዎች": "requests",
    "🆘 እርዳታ": "help",
    "❌ ተወው": "cancel",
}


def superadmin_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["🆕 አዲስ ሆስት መዝግብ", "🏠 ሆስቶች ዝርዝር"],
            ["📊 ስታቲስቲክስ", "💰 የክሬዲት ጥያቄዎች"],
            ["🆘 እርዳታ", "❌ ተወው"],
        ],
        resize_keyboard=True, is_persistent=True,
    )


async def _notify_host_admin(bot, host, text):
    try:
        await bot.send_message(chat_id=host["admin_id"], text=text)
    except Exception:
        pass


# =============================================================================
# Telegram Handlers (Super Admin bot)
# =============================================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if _is_super_admin(uid):
        await update.message.reply_text(
            "👑 <b>Super Admin App</b>\n\n"
            "/newhost - አዲስ ሆስት መዝግብ (ስም፣ ስልክ፣ bot token፣ admin ID)\n"
            "/hosts - የተመዘገቡ ሆስቶች ዝርዝር\n"
            "/host &lt;ID&gt; - የአንድ ሆስት ዝርዝር እና መቆጣጠሪያ\n"
            "/requests - በመጠባበቅ ላይ ያሉ የክሬዲት ጥያቄዎች\n"
            "/hostrequests - በመጠባበቅ ላይ ያሉ «ሆስት መሆን እፈልጋለሁ» ጥያቄዎች\n"
            "/addcredit &lt;ID&gt; &lt;መጠን&gt; - ለሆስት በቀጥታ ክሬዲት መጨመር\n"
            "/editcommission &lt;ID&gt; &lt;ፐርሰንት&gt; - የሆስት ኮሚሽን መቶኛ መቀየር\n"
            "/stats - የሁሉም ሆስቶች ጠቅላላ ስታቲስቲክስ\n"
            "/revokehost &lt;ID&gt; - ሆስት ማሰናከል/ማቆም (ውሂቡ አይጠፋም - ቋሚ ነው)\n"
            "/unrevokehost &lt;ID&gt; - የተሰናከለ ሆስት መልሶ ማንቃት\n"
            "/help - እርዳታ",
            parse_mode="HTML",
            reply_markup=superadmin_keyboard(),
        )
    else:
        await update.message.reply_text("👋 ይህ የ Super Admin ውስጣዊ መሳሪያ ነው።")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


# --- /newhost: only asks for bot token + admin telegram ID, everything else is automatic ---

async def cmd_newhost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if not _is_super_admin(uid):
        await update.message.reply_text("⚠️ ይህ ትዕዛዝ ለ Super Admin ብቻ ነው። እባክዎ አዲስ ሆስት እንዲመዘገብ Super Admin ን ያግኙ።")
        return
    connect_state[uid] = {"step": "name"}
    await update.message.reply_text(
        "🆕 <b>አዲስ ሆስት መዝግብ</b>\n\n"
        "1️⃣ የሆስቱን ስም ያስገቡ (ለምሳሌ፦ Jemo Lottery Addis)፦\n\n"
        "❌ ለማቋረጥ /cancel ይላኩ።",
        parse_mode="HTML",
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    cancelled = connect_state.pop(uid, None) or addcredit_state.pop(uid, None) or editcommission_state.pop(uid, None)
    if cancelled:
        await update.message.reply_text("❌ ተቋርጧል።")


async def handle_superadmin_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """If the incoming text matches one of the persistent menu buttons, dispatch
    to the matching command and return True. Otherwise return False so the
    caller can fall through to the /newhost step-by-step wizard."""
    uid = update.message.from_user.id
    if not _is_super_admin(uid):
        return False
    text = (update.message.text or "").strip()
    action = SUPERADMIN_MENU_BUTTONS.get(text)
    if action is None:
        return False

    if action == "cancel":
        await cmd_cancel(update, context)
        return True

    connect_state.pop(uid, None)  # a menu tap abandons any in-progress /newhost wizard
    addcredit_state.pop(uid, None)
    editcommission_state.pop(uid, None)
    if action == "newhost":
        await cmd_newhost(update, context)
    elif action == "hosts":
        await cmd_hosts(update, context)
    elif action == "stats":
        await cmd_stats(update, context)
    elif action == "requests":
        await cmd_requests(update, context)
    elif action == "help":
        await cmd_help(update, context)
    return True


async def handle_newhost_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    text = (update.message.text or "").strip()

    if uid in addcredit_state:
        if text.lower() == "/cancel":
            addcredit_state.pop(uid, None)
            await update.message.reply_text("❌ ተቋርጧል።")
            return
        host_id = addcredit_state[uid]
        host = hosts.get(host_id)
        if not host:
            addcredit_state.pop(uid, None)
            await update.message.reply_text("❌ ያልታወቀ host_id።")
            return
        try:
            amount = float(text.replace(",", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ ትክክለኛ የገንዘብ መጠን ቁጥር ብቻ ያስገቡ (ለምሳሌ፦ 500)፦")
            return
        addcredit_state.pop(uid, None)
        pending_addcredit_confirm[uid] = {"host_id": host_id, "amount": amount}
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ አዎ፣ ጨምር", callback_data="credaddconfirm"),
            InlineKeyboardButton("❌ አይ፣ ተወው", callback_data="credaddcancel"),
        ]])
        await update.message.reply_text(
            f"⚠️ {amount:.2f} ብር ወደ {_host_label(host_id, host)} መጨመር እርግጠኛ ነዎት?",
            reply_markup=kb
        )
        return

    if uid in editcommission_state:
        if text.lower() == "/cancel":
            editcommission_state.pop(uid, None)
            await update.message.reply_text("❌ ተቋርጧል።")
            return
        host_id = editcommission_state[uid]
        host = hosts.get(host_id)
        if not host:
            editcommission_state.pop(uid, None)
            await update.message.reply_text("❌ ያልታወቀ host_id።")
            return
        try:
            percent = float(text.replace(",", ""))
        except ValueError:
            await update.message.reply_text("⚠️ ትክክለኛ ቁጥር (0-100) ብቻ ያስገቡ፦")
            return
        if percent < 0 or percent > 100:
            await update.message.reply_text("⚠️ ኮሚሽን በ0 እና 100 መካከል መሆን አለበት፣ እንደገና ያስገቡ፦")
            return
        editcommission_state.pop(uid, None)
        old_percent = host.get("commission_percent", 5.0)
        if percent == old_percent:
            await update.message.reply_text(f"ℹ️ የ{_host_label(host_id, host)} ኮሚሽን አስቀድሞ {percent:.1f}% ነው።")
            return
        pending_commission_confirm[uid] = {
            "host_id": host_id, "percent": percent, "old_percent": old_percent,
        }
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ አዎ፣ ቀይር", callback_data="commconfirm"),
            InlineKeyboardButton("❌ አይ፣ ተወው", callback_data="commcancel"),
        ]])
        restart_note = "ተግባራዊ ለማድረግ ሆስቱ በራስ-ሰር ዳግም ይነሣል።" if host.get("running") else "ሆስቱ ስላልሮጠ ቀጣይ ጊዜ ሲነሳ ተግባራዊ ይሆናል።"
        await update.message.reply_text(
            f"⚠️ የ{_host_label(host_id, host)} ኮሚሽን ከ{old_percent:.1f}% ወደ {percent:.1f}% መቀየር እርግጠኛ ነዎት?\n"
            f"ℹ️ ይህ ወደፊት ለሚደረጉ ሽያጮች ብቻ ተፅእኖ ይኖረዋል (ያለፉት ሽያጮች እንዳሉ ይቆያሉ)። {restart_note}",
            reply_markup=kb
        )
        return

    if await handle_superadmin_menu_button(update, context):
        return
    state = connect_state.get(uid)
    if not state or not _is_super_admin(uid):
        return
    text = (update.message.text or "").strip()

    if state["step"] == "name":
        if not text:
            await update.message.reply_text("⚠️ ባዶ ስም መላክ አይቻልም። የሆስቱን ስም ያስገቡ፦")
            return
        state["name"] = text
        state["step"] = "phone"
        await update.message.reply_text("2️⃣ የሆስቱን ስልክ ቁጥር ያስገቡ (ለምሳሌ፦ 0912345678)፦")
        return

    if state["step"] == "phone":
        digits = re.sub(r"[^\d+]", "", text)
        if len(digits) < 9:
            await update.message.reply_text("⚠️ ትክክለኛ ስልክ ቁጥር አይመስልም። እንደገና ያስገቡ (ለምሳሌ፦ 0912345678)፦")
            return
        state["phone"] = text
        state["step"] = "bot_token"
        await update.message.reply_text(
            "3️⃣ የሆስቱን BotFather ቦት ቶከን ይላኩ (ለምሳሌ፦ 123456:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx)"
        )
        return

    if state["step"] == "bot_token":
        if ":" not in text or len(text) < 20:
            await update.message.reply_text("⚠️ ትክክለኛ ቶከን አይመስልም። እንደገና ይላኩ (ለምሳሌ 123456:AAxxxxxxx)፦")
            return
        state["bot_token"] = text
        state["step"] = "admin_id"
        await update.message.reply_text("4️⃣ የሆስቱን አድሚን የቴሌግራም ID ቁጥር ያስገቡ (ለምሳሌ፦ 123456789)፦")
        return

    if state["step"] == "admin_id":
        try:
            admin_id = int(text)
        except ValueError:
            await update.message.reply_text("⚠️ ቁጥር ID ብቻ ያስገቡ፦")
            return

        used_api_ports = {h["host_api_port"] for h in hosts.values()}
        used_webhook_ports = {h["webhook_port"] for h in hosts.values()}
        host_id = _new_host_id()
        hosts[host_id] = {
            "bot_token": state["bot_token"],
            "admin_id": admin_id,
            "host_api_port": _next_free_port(_HOST_API_PORT_BASE, used_api_ports),
            "webhook_port": _next_free_port(_WEBHOOK_PORT_BASE, used_webhook_ports),
            "host_api_secret": secrets.token_urlsafe(24),
            "name": state.get("name") or f"Host {host_id}",
            "phone": state.get("phone", "N/A"),
            "created_at": datetime.now().isoformat(),
            "running": False,
            "last_error": None,
            "commission_percent": 5.0,
        }
        save_state()
        connect_state.pop(uid, None)

        await update.message.reply_text(f"⏳ {host_id} እየተነሳ ነው...")
        ok, err = await _launch_host_instance(host_id, bot=context.bot)
        if ok:
            await _ensure_admin_username(host_id, hosts[host_id])
            await update.message.reply_text(
                f"✅ ሆስት {_host_label(host_id, hosts[host_id])} ተነስቷል እና እየሮጠ ነው!\n"
                f"👤 አድሚን ID፦ {admin_id}\n\n"
                f"ያ ቦት ላይ /start ብለው ማየት ይችላሉ።",
                parse_mode="HTML",
            )
            await _notify_host_admin(
                context.bot, hosts[host_id],
                "🎉 የእርስዎ ሆስት ቦት በ Super Admin ተመዝግቦ ተነስቷል! ቦትዎ ላይ /start ይላኩ።",
            )
        else:
            await update.message.reply_text(f"❌ ማስነሳት አልተሳካም፦ {err}\nID/ቶከን በድጋሚ ያረጋግጡ እና /newhost እንደገና ይሞክሩ።")


# --- host management ---

async def cmd_hosts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_super_admin(update.message.from_user.id):
        return
    if not hosts:
        await update.message.reply_text("ምንም ሆስት አልተመዘገበም። /newhost ይላኩ።")
        return
    kb = []
    lines = ["🏠 <b>የተመዘገቡ ሆስቶች</b>\n"]
    for hid, h in hosts.items():
        await _ensure_admin_username(hid, h)
        state = "🟢" if h.get("running") else "🔴"
        lines.append(f"{state} {_host_label(hid, h)}")
        kb.append([InlineKeyboardButton(f"{state} {_host_label(hid, h)}", callback_data=f"hmenu_{hid}")])
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_super_admin(update.message.from_user.id):
        return
    if not hosts:
        await update.message.reply_text("ምንም ሆስት አልተመዘገበም። /newhost ይላኩ።")
        return

    total_sales = total_commission = total_credit = 0.0
    total_players = total_sold = 0
    running_count = 0
    lines = ["📊 <b>የሁሉም ሆስቶች ስታቲስቲክስ</b>\n"]

    for hid, h in hosts.items():
        await _ensure_admin_username(hid, h)
        state_emoji = "🟢" if h.get("running") else "🔴"
        if h.get("running"):
            running_count += 1
        status, data = await _host_api_get(h, "/api/status")
        if status == 200 and data.get("ok"):
            sales = data.get("sales", 0) or 0
            commission = data.get("commission", 0) or 0
            credit = data.get("credit_balance", data.get("balance", 0)) or 0
            players = data.get("registered_players", 0) or 0
            sold = data.get("sold_tickets", 0) or 0
            total_sales += sales
            total_commission += commission
            total_credit += credit
            total_players += players
            total_sold += sold
            lines.append(
                f"{state_emoji} {_host_label(hid, h)} — "
                f"ሽያጭ {sales:.2f} ብር | ኮሚሽን {commission:.2f} ብር ({h.get('commission_percent', 5.0):.1f}%) | "
                f"ክሬዲት {credit:.2f} ብር"
            )
        else:
            lines.append(f"{state_emoji} {_host_label(hid, h)} — ⚠️ መረጃ አልተገኘም")

    lines.append(
        f"\n<b>ጠቅላላ</b>፦ {len(hosts)} ሆስቶች ({running_count} እየሮጡ)\n"
        f"💰 ጠቅላላ ሽያጭ፦ {total_sales:.2f} ብር\n"
        f"💵 ጠቅላላ ኮሚሽን፦ {total_commission:.2f} ብር\n"
        f"💳 ጠቅላላ ክሬዲት ሒሳብ፦ {total_credit:.2f} ብር\n"
        f"👥 ጠቅላላ ተመዝጋቢዎች፦ {total_players}\n"
        f"🎟️ ጠቅላላ የተሸጡ ቲኬቶች፦ {total_sold}"
    )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_host(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_super_admin(update.message.from_user.id):
        return
    if not context.args:
        await cmd_hosts(update, context)
        return
    host_id = context.args[0].upper()
    host = hosts.get(host_id)
    if not host:
        await update.message.reply_text("❌ ያልታወቀ host_id።")
        return
    await _ensure_admin_username(host_id, host)
    state = "🟢 እየሮጠ ነው" if host.get("running") else f"🔴 አልሮጠም ({host.get('last_error') or 'stopped'})"
    await update.message.reply_text(
        f"🏠 <b>{_host_label(host_id, host)}</b>\n{state}\n\n"
        f"📱 ስልክ፦ {host.get('phone', 'N/A')}\n"
        f"🔌 SMS Webhook ወደብ (ለNginx)፦ <code>{host['webhook_port']}</code>\n"
        f"🔌 Host API ወደብ (ውስጣዊ)፦ <code>{host['host_api_port']}</code>",
        parse_mode="HTML", reply_markup=_host_menu_kb(host_id),
    )


async def cmd_revokehost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ሆስቱን ያቆማል እና ዳግም እንዳይነሳ ያሰናክላል (revoked) - የሆስቱ ውሂብ (bot_token, admin_id, ኮሚሽን፣
    ስም፣ ወዘተ) ግን ፈጽሞ አይሰረዝም። ውሂብ ሁሉ ቋሚ ነው - የሚጠፋው በ/resetfactory (በሆስቱ ራሱ) ብቻ ነው።
    ተመልሶ ለማንቃት፦ /unrevokehost <ID>"""
    if not _is_super_admin(update.message.from_user.id):
        return
    if not context.args:
        await update.message.reply_text("🚫 የትኛውን ሆስት ማሰናከል ይፈልጋሉ? ከዝርዝሩ ይምረጡ፣ ቀጥሎም «🚫 አሰናክል» የሚለውን ይጫኑ፦")
        await cmd_hosts(update, context)
        return
    host_id = context.args[0].upper()
    host = hosts.get(host_id)
    if not host:
        await update.message.reply_text("❌ ያልታወቀ host_id።")
        return
    if host.get("revoked"):
        await update.message.reply_text(f"ℹ️ {_host_label(host_id, host)} ቀድሞውኑ ተሰናክሏል (revoked)።")
        return
    await _stop_host_instance(host_id)
    host["revoked"] = True
    host["revoked_at"] = datetime.now().isoformat()
    save_state()
    await update.message.reply_text(
        f"🚫 {_host_label(host_id, host)} ቆሟል እና ተሰናክሏል (revoked)።\n"
        f"ℹ️ ውሂቡ አልጠፋም - ተመልሶ ለማንቃት፦ /unrevokehost {host_id}"
    )


async def cmd_unrevokehost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ቀደም ብሎ በ/revokehost የተሰናከለ ሆስት መልሶ የሚያነቃ (እና በራስ-ሰር የሚያስነሳ) ትዕዛዝ።
    አጠቃቀም፦ /unrevokehost <ID>"""
    if not _is_super_admin(update.message.from_user.id):
        return
    if not context.args:
        await update.message.reply_text("✅ የትኛውን ሆስት መልሶ ማንቃት ይፈልጋሉ? ከዝርዝሩ ይምረጡ፣ ቀጥሎም «✅ መልስ አንቃ» የሚለውን ይጫኑ፦")
        await cmd_hosts(update, context)
        return
    host_id = context.args[0].upper()
    host = hosts.get(host_id)
    if not host:
        await update.message.reply_text("❌ ያልታወቀ host_id።")
        return
    if not host.get("revoked"):
        await update.message.reply_text(f"ℹ️ {_host_label(host_id, host)} አልተሰናከለም።")
        return
    host["revoked"] = False
    host.pop("revoked_at", None)
    save_state()
    ok, err = await _launch_host_instance(host_id, bot=context.bot)
    if ok:
        await update.message.reply_text(f"✅ {_host_label(host_id, host)} ተመልሶ ነቅቷል እና ተነስቷል።")
    else:
        await update.message.reply_text(f"✅ {_host_label(host_id, host)} ተመልሶ ነቅቷል፣ ግን ማስነሳት አልተሳካም፦ {err}")


async def _ask_addcredit_amount(update, context, host_id, host):
    uid = update.message.from_user.id if update.message else update.effective_user.id
    editcommission_state.pop(uid, None)
    addcredit_state[uid] = host_id
    text = (
        f"➕ ወደ {_host_label(host_id, host)} የሚጨመረውን የገንዘብ መጠን (ብር) ብቻ ይላኩ፦\n\n"
        "ለምሳሌ፦ 500\n"
        "ለመሰረዝ /cancel ይላኩ።"
    )
    if update.message:
        await update.message.reply_text(text)
    else:
        await context.bot.send_message(chat_id=uid, text=text)


async def cmd_addcredit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_super_admin(update.message.from_user.id):
        return
    if not context.args:
        await update.message.reply_text("➕ ክሬዲት ለየትኛው ሆስት ማከል ይፈልጋሉ? ከዝርዝሩ ይምረጡ፣ ቀጥሎም «➕ ክሬዲት ጨምር» የሚለውን ይጫኑ፦")
        await cmd_hosts(update, context)
        return
    host_id = context.args[0].upper()
    host = hosts.get(host_id)
    if not host:
        await update.message.reply_text("❌ ያልታወቀ host_id።")
        return
    if len(context.args) < 2:
        await _ask_addcredit_amount(update, context, host_id, host)
        return
    try:
        amount = float(context.args[1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ ትክክለኛ የገንዘብ መጠን ያስገቡ።")
        return

    pending_addcredit_confirm[update.message.from_user.id] = {"host_id": host_id, "amount": amount}
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ አዎ፣ ጨምር", callback_data="credaddconfirm"),
        InlineKeyboardButton("❌ አይ፣ ተወው", callback_data="credaddcancel"),
    ]])
    await update.message.reply_text(
        f"⚠️ {amount:.2f} ብር ወደ {_host_label(host_id, host)} መጨመር እርግጠኛ ነዎት?",
        reply_markup=kb
    )


async def _apply_addcredit(bot, super_admin_id, host_id, amount):
    host = hosts.get(host_id)
    if not host:
        await bot.send_message(chat_id=super_admin_id, text="❌ ያልታወቀ host_id።")
        return
    status, data = await _host_api_post(host, "/api/addcredit", {"amount": amount})
    if status == 200 and data.get("ok"):
        await bot.send_message(
            chat_id=super_admin_id,
            text=(
                f"✅ {amount:.2f} ብር ወደ {_host_label(host_id, host)} ተጨምሯል።\n"
                f"💳 አዲሱ ሒሳብ፦ {data.get('balance', 0):.2f} ብር"
            )
        )
        await _notify_host_admin(bot, host,
                                  f"✅ {amount:.2f} ብር Super Admin በቀጥታ ጨምሮልዎታል።\n💳 አዲሱ ሒሳብ፦ {data.get('balance', 0):.2f} ብር")
    else:
        await bot.send_message(chat_id=super_admin_id, text=f"❌ አልተሳካም፦ {data.get('error', f'HTTP {status}')}")


async def _ask_editcommission_percent(update, context, host_id, host):
    uid = update.message.from_user.id if update.message else update.effective_user.id
    addcredit_state.pop(uid, None)
    editcommission_state[uid] = host_id
    current = host.get("commission_percent", 5.0)
    text = (
        f"✏️ የ{_host_label(host_id, host)} የአሁኑ ኮሚሽን፦ {current:.1f}%\n\n"
        "አዲሱን ፐርሰንት (0-100) ብቻ ይላኩ፦\n\n"
        "ለምሳሌ፦ 7\n"
        "ለመሰረዝ /cancel ይላኩ።"
    )
    if update.message:
        await update.message.reply_text(text)
    else:
        await context.bot.send_message(chat_id=uid, text=text)


async def cmd_editcommission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_super_admin(update.message.from_user.id):
        return
    if not context.args:
        await update.message.reply_text("✏️ የየትኛው ሆስት ኮሚሽን መቀየር ይፈልጋሉ? ከዝርዝሩ ይምረጡ፣ ቀጥሎም «✏️ ኮሚሽን ቀይር» የሚለውን ይጫኑ፦")
        await cmd_hosts(update, context)
        return
    host_id = context.args[0].upper()
    host = hosts.get(host_id)
    if not host:
        await update.message.reply_text("❌ ያልታወቀ host_id።")
        return
    if len(context.args) < 2:
        await _ask_editcommission_percent(update, context, host_id, host)
        return
    try:
        percent = float(context.args[1])
    except ValueError:
        await update.message.reply_text("⚠️ ትክክለኛ ቁጥር (0-100) ያስገቡ።")
        return
    if percent < 0 or percent > 100:
        await update.message.reply_text("⚠️ ኮሚሽን በ0 እና 100 መካከል መሆን አለበት።")
        return

    old_percent = host.get("commission_percent", 5.0)
    if percent == old_percent:
        await update.message.reply_text(f"ℹ️ የ{_host_label(host_id, host)} ኮሚሽን አስቀድሞ {percent:.1f}% ነው።")
        return

    pending_commission_confirm[update.message.from_user.id] = {
        "host_id": host_id, "percent": percent, "old_percent": old_percent,
    }
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ አዎ፣ ቀይር", callback_data="commconfirm"),
        InlineKeyboardButton("❌ አይ፣ ተወው", callback_data="commcancel"),
    ]])
    restart_note = "ተግባራዊ ለማድረግ ሆስቱ በራስ-ሰር ዳግም ይነሣል።" if host.get("running") else "ሆስቱ ስላልሮጠ ቀጣይ ጊዜ ሲነሳ ተግባራዊ ይሆናል።"
    await update.message.reply_text(
        f"⚠️ የ{_host_label(host_id, host)} ኮሚሽን ከ{old_percent:.1f}% ወደ {percent:.1f}% መቀየር እርግጠኛ ነዎት?\n"
        f"ℹ️ ይህ ወደፊት ለሚደረጉ ሽያጮች ብቻ ተፅእኖ ይኖረዋል (ያለፉት ሽያጮች እንዳሉ ይቆያሉ)። {restart_note}",
        reply_markup=kb
    )


async def _apply_commission_change(bot, super_admin_id, host_id, percent, old_percent):
    host = hosts.get(host_id)
    if not host:
        await bot.send_message(chat_id=super_admin_id, text="❌ ያልታወቀ host_id።")
        return

    host["commission_percent"] = percent
    save_state()

    was_running = host.get("running", False)
    if was_running:
        await bot.send_message(
            chat_id=super_admin_id,
            text=(
                f"⏳ የ{_host_label(host_id, host)} ኮሚሽን ከ{old_percent:.1f}% ወደ {percent:.1f}% ተቀይሯል፣ "
                f"ተግባራዊ ለማድረግ ሆስቱ በራስ-ሰር እንደገና እየተነሳ ነው..."
            )
        )
        await _stop_host_instance(host_id)
        ok, err = await _launch_host_instance(host_id, bot=bot)
        if ok:
            await bot.send_message(chat_id=super_admin_id,
                                    text=f"✅ {_host_label(host_id, host)} እንደገና ተነስቷል፣ አዲሱ ኮሚሽን ({percent:.1f}%) ተግባራዊ ሆኗል።")
        else:
            await bot.send_message(chat_id=super_admin_id, text=f"❌ እንደገና ማስነሳት አልተሳካም፦ {err}")
    else:
        await bot.send_message(
            chat_id=super_admin_id,
            text=(
                f"✅ የ{_host_label(host_id, host)} ኮሚሽን ከ{old_percent:.1f}% ወደ {percent:.1f}% ተቀይሯል "
                f"(ሆስቱ ስላልሮጠ ቀጣይ ጊዜ ሲነሳ ተግባራዊ ይሆናል)።"
            )
        )
    await _notify_host_admin(bot, host, f"ℹ️ Super Admin የኮሚሽንዎን መጠን ወደ {percent:.1f}% ቀይሯል።")


async def cmd_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_super_admin(update.message.from_user.id):
        return
    pending = {rid: r for rid, r in credit_requests.items() if r["status"] == "pending"}
    if not pending:
        await update.message.reply_text("✅ በመጠባበቅ ላይ ያለ የክሬዲት ጥያቄ የለም።")
        return
    for rid, r in pending.items():
        await _send_credit_request_card(context.bot, rid, r)


async def _send_credit_request_card(bot, request_id, r):
    host = hosts.get(r["host_id"], {})
    text = (
        f"💰 <b>አዲስ የክሬዲት ጥያቄ</b> (#{request_id})\n\n"
        f"🏠 ሆስት፦ {_host_label(r['host_id'], host)}\n"
        f"💵 መጠን፦ {r['amount']:.2f} ብር\n"
        f"💳 ዘዴ፦ {r.get('method', '-')}\n"
        f"🔖 ሪፈረንስ፦ {r.get('reference', '-')}\n"
        f"📝 መልዕክት፦ {r.get('message', '-')}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ማጽደቅ", callback_data=f"creqapprove_{request_id}"),
        InlineKeyboardButton("❌ ውድቅ", callback_data=f"creqreject_{request_id}"),
    ]])
    try:
        await bot.send_message(chat_id=SUPER_ADMIN_ID, text=text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        log.error(f"_send_credit_request_card error: {e}")


async def cmd_hostrequests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_super_admin(update.message.from_user.id):
        return
    pending = {rid: r for rid, r in host_signup_requests.items() if r["status"] == "pending"}
    if not pending:
        await update.message.reply_text("✅ በመጠባበቅ ላይ ያለ የሆስት መሆን ጥያቄ የለም።")
        return
    for rid, r in pending.items():
        await _send_host_signup_request_card(context.bot, rid, r)


async def _send_host_signup_request_card(bot, request_id, r):
    host = hosts.get(r["host_id"], {})
    from_host = _host_label(r["host_id"], host) if host else (r.get("host_name") or r.get("host_id") or "-")
    username_line = f"@{r['username']}" if r.get("username") else "—"
    text = (
        f"🖥️ <b>አዲስ የሆስት መሆን ጥያቄ</b> (#{request_id})\n\n"
        f"👤 ስም፦ {r.get('name', 'N/A')}\n"
        f"📱 ስልክ፦ {r.get('phone', 'N/A')}\n"
        f"🔗 ዩዘርኔም፦ {username_line}\n"
        f"🆔 Telegram ID፦ <code>{r.get('telegram_user_id')}</code>\n"
        f"📨 ከየትኛው ሆስት ቦት እንደመጣ፦ {from_host}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ተስተናግዷል", callback_data=f"hostsignup_handled_{request_id}"),
        InlineKeyboardButton("❌ አልፍ", callback_data=f"hostsignup_dismiss_{request_id}"),
    ]])
    try:
        await bot.send_message(chat_id=SUPER_ADMIN_ID, text=text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        log.error(f"_send_host_signup_request_card error: {e}")


# =============================================================================
# Callback query handler
# =============================================================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if not _is_super_admin(uid):
        await query.answer()
        return
    data = query.data

    if data == "commconfirm":
        await query.answer()
        info = pending_commission_confirm.pop(uid, None)
        if not info:
            try:
                await query.edit_message_text("⚠️ ይህ ጥያቄ ጊዜው አልፎበታል፣ /editcommission ደግመው ይላኩ።")
            except Exception:
                pass
            return
        try:
            await query.edit_message_text("⏳ በመፈጸም ላይ...")
        except Exception:
            pass
        await _apply_commission_change(context.bot, uid, info["host_id"], info["percent"], info["old_percent"])
        return

    if data == "commcancel":
        pending_commission_confirm.pop(uid, None)
        await query.answer("ተሰርዟል።")
        try:
            await query.edit_message_text("❌ ተሰርዟል፣ ኮሚሽኑ አልተቀየረም።")
        except Exception:
            pass
        return

    if data == "credaddconfirm":
        await query.answer()
        info = pending_addcredit_confirm.pop(uid, None)
        if not info:
            try:
                await query.edit_message_text("⚠️ ይህ ጥያቄ ጊዜው አልፎበታል፣ /addcredit ደግመው ይላኩ።")
            except Exception:
                pass
            return
        try:
            await query.edit_message_text("⏳ በመፈጸም ላይ...")
        except Exception:
            pass
        await _apply_addcredit(context.bot, uid, info["host_id"], info["amount"])
        return

    if data == "credaddcancel":
        pending_addcredit_confirm.pop(uid, None)
        await query.answer("ተሰርዟል።")
        try:
            await query.edit_message_text("❌ ተሰርዟል፣ ምንም ክሬዲት አልተጨመረም።")
        except Exception:
            pass
        return

    if data.startswith("hmenu_"):
        host_id = data.split("_", 1)[1]
        host = hosts.get(host_id)
        await query.answer()
        if not host:
            await query.edit_message_text("❌ ያልታወቀ host_id።")
            return
        state = "🟢 እየሮጠ ነው" if host.get("running") else "🔴 አልሮጠም"
        await query.edit_message_text(f"🏠 <b>{_host_label(host_id, host)}</b>\n{state}",
                                       parse_mode="HTML", reply_markup=_host_menu_kb(host_id))
        return

    if data.startswith("hstatus_"):
        host_id = data.split("_", 1)[1]
        host = hosts.get(host_id)
        await query.answer("በመመልከት ላይ...")
        if not host:
            return
        status, resp = await _host_api_get(host, "/api/status")
        await query.edit_message_text(_fmt_status_reply(host_id, host, status, resp),
                                       parse_mode="HTML", reply_markup=_host_menu_kb(host_id))
        return

    if data.startswith("hrounds_"):
        host_id = data.split("_", 1)[1]
        host = hosts.get(host_id)
        await query.answer("በመመልከት ላይ...")
        if not host:
            return
        status, resp = await _host_api_get(host, "/api/rounds")
        if status != 200 or not resp.get("ok"):
            await query.edit_message_text(f"❌ ስህተት፦ {resp.get('error')}", reply_markup=_host_menu_kb(host_id))
            return
        lines = [f"📋 <b>{_host_label(host_id, host)}</b> - ዙሮች\n"]
        for r in resp.get("rounds", []):
            lines.append(
                f"• #{r['round_id']} {r.get('name', '')} [{r['status']}] "
                f"ነጻ {r['available']} / ጠባቂ {r['pending']} / ተሸጠ {r['sold']} — {r['sales']:.2f} ብር"
            )
        await query.edit_message_text("\n".join(lines) or "ምንም ዙር የለም።", parse_mode="HTML",
                                       reply_markup=_host_menu_kb(host_id))
        return

    if data.startswith("hcomm_"):
        host_id = data.split("_", 1)[1]
        host = hosts.get(host_id)
        await query.answer("በመመልከት ላይ...")
        if not host:
            return
        status, resp = await _host_api_get(host, "/api/commission")
        if status != 200 or not resp.get("ok"):
            await query.edit_message_text(f"❌ ስህተት፦ {resp.get('error')}", reply_markup=_host_menu_kb(host_id))
            return
        await query.edit_message_text(
            f"💰 <b>{_host_label(host_id, host)}</b>\nሽያጭ፦ {resp['sales']:.2f} ብር\n"
            f"ኮሚሽን ({resp['commission_percent']}%)፦ {resp['commission']:.2f} ብር",
            parse_mode="HTML", reply_markup=_host_menu_kb(host_id),
        )
        return

    if data.startswith("hpause_") or data.startswith("hresume_"):
        action = "pause" if data.startswith("hpause_") else "resume"
        host_id = data.split("_", 1)[1]
        host = hosts.get(host_id)
        await query.answer("በማስፈጸም ላይ...")
        if not host:
            return
        status, resp = await _host_api_post(host, f"/api/{action}")
        if status == 200 and resp.get("ok"):
            emoji = "⏸️" if action == "pause" else "▶️"
            await query.edit_message_text(f"{emoji} {_host_label(host_id, host)} - {resp.get('message', '')}",
                                           reply_markup=_host_menu_kb(host_id))
            await _notify_host_admin(context.bot, host,
                                      f"{emoji} Super Admin ሽያጭዎን {'አቁሟል' if action=='pause' else 'አስቀጥሏል'}።")
        else:
            await query.edit_message_text(f"❌ ስህተት፦ {resp.get('error', f'HTTP {status}')}",
                                           reply_markup=_host_menu_kb(host_id))
        return

    if data.startswith("haddcredit_"):
        host_id = data.split("_", 1)[1]
        host = hosts.get(host_id)
        await query.answer()
        if not host:
            return
        await _ask_addcredit_amount(update, context, host_id, host)
        return

    if data.startswith("heditcomm_"):
        host_id = data.split("_", 1)[1]
        host = hosts.get(host_id)
        await query.answer()
        if not host:
            return
        await _ask_editcommission_percent(update, context, host_id, host)
        return

    if data.startswith("hrestart_"):
        host_id = data.split("_", 1)[1]
        host = hosts.get(host_id)
        await query.answer("እንደገና በማስነሳት ላይ...")
        if not host:
            return
        await _stop_host_instance(host_id)
        ok, err = await _launch_host_instance(host_id, bot=context.bot)
        if ok:
            await query.edit_message_text(f"🔄 {_host_label(host_id, host)} እንደገና ተነስቷል።",
                                           reply_markup=_host_menu_kb(host_id))
        else:
            await query.edit_message_text(f"❌ እንደገና ማስነሳት አልተሳካም፦ {err}", reply_markup=_host_menu_kb(host_id))
        return

    if data.startswith("hrevoke_"):
        host_id = data.split("_", 1)[1]
        host = hosts.get(host_id)
        await query.answer()
        if not host:
            return
        if host.get("revoked"):
            await query.answer(f"ℹ️ ቀድሞውኑ ተሰናክሏል።", show_alert=True)
            return
        await _stop_host_instance(host_id)
        host["revoked"] = True
        host["revoked_at"] = datetime.now().isoformat()
        save_state()
        await query.edit_message_text(
            f"🚫 {_host_label(host_id, host)} ቆሟል እና ተሰናክሏል (revoked)።\n"
            f"ℹ️ ውሂቡ አልጠፋም - ተመልሶ ለማንቃት፦ /unrevokehost {host_id}"
        )
        return

    if data.startswith("hunrevoke_"):
        host_id = data.split("_", 1)[1]
        host = hosts.get(host_id)
        await query.answer("በማንቃት ላይ...")
        if not host:
            return
        if not host.get("revoked"):
            return
        host["revoked"] = False
        host.pop("revoked_at", None)
        save_state()
        ok, err = await _launch_host_instance(host_id, bot=context.bot)
        if ok:
            await query.edit_message_text(f"✅ {_host_label(host_id, host)} ተመልሶ ነቅቷል እና ተነስቷል።",
                                           reply_markup=_host_menu_kb(host_id))
        else:
            await query.edit_message_text(f"✅ ነቅቷል፣ ግን ማስነሳት አልተሳካም፦ {err}", reply_markup=_host_menu_kb(host_id))
        return

    if data.startswith("creqapprove_") or data.startswith("creqreject_"):
        approve = data.startswith("creqapprove_")
        request_id = data.split("_", 1)[1]
        req = credit_requests.get(request_id)
        await query.answer()
        if not req:
            await query.edit_message_text("❌ ይህ ጥያቄ አልተገኘም (ምናልባት ተሰርዟል)።")
            return
        if req["status"] != "pending":
            await query.edit_message_text(f"ℹ️ ይህ ጥያቄ ቀድሞውኑ {req['status']} ተደርጓል።")
            return

        host = hosts.get(req["host_id"])
        if not host:
            await query.edit_message_text("❌ ይህ ሆስት ከመዝገብ ጠፍቷል።")
            return

        if approve:
            status, resp = await _host_api_post(host, "/api/addcredit", {"amount": req["amount"]})
            if status == 200 and resp.get("ok"):
                req["status"] = "approved"
                req["decided_at"] = datetime.now().isoformat()
                req["decided_by"] = query.from_user.id
                save_state()
                await query.edit_message_text(
                    f"✅ ጸድቋል (#{request_id})\n{_host_label(req['host_id'], host)} - "
                    f"{req['amount']:.2f} ብር ተጨምሯል።\n💳 አዲሱ ሒሳብ፦ {resp.get('balance', 0):.2f} ብር"
                )
                await _notify_host_admin(
                    context.bot, host,
                    f"✅ የ{req['amount']:.2f} ብር ክሬዲት ጥያቄዎ ጸድቋል!\n💳 አዲሱ ሒሳብ፦ {resp.get('balance', 0):.2f} ብር",
                )
            else:
                await query.edit_message_text(
                    f"❌ ማጽደቅ አልተሳካም (#{request_id})፦ {resp.get('error', f'HTTP {status}')}"
                )
        else:
            req["status"] = "rejected"
            req["decided_at"] = datetime.now().isoformat()
            req["decided_by"] = query.from_user.id
            save_state()
            await query.edit_message_text(f"❌ ውድቅ ተደርጓል (#{request_id})")
            await _notify_host_admin(
                context.bot, host,
                f"❌ የ{req['amount']:.2f} ብር ክሬዲት ጥያቄዎ ውድቅ ተደርጓል። ደረሰኙን በድጋሚ ያረጋግጡ ወይም Super Admin ያግኙ።",
            )
        return

    if data.startswith("hostsignup_handled_") or data.startswith("hostsignup_dismiss_"):
        handled = data.startswith("hostsignup_handled_")
        request_id = data.split("_", 2)[2]
        req = host_signup_requests.get(request_id)
        await query.answer()
        if not req:
            await query.edit_message_text("❌ ይህ ጥያቄ አልተገኘም (ምናልባት ተሰርዟል)።")
            return
        if req["status"] != "pending":
            await query.edit_message_text(f"ℹ️ ይህ ጥያቄ ቀድሞውኑ {req['status']} ተደርጓል።")
            return

        req["status"] = "handled" if handled else "dismissed"
        req["decided_at"] = datetime.now().isoformat()
        req["decided_by"] = query.from_user.id
        save_state()
        label = "✅ ተስተናግዷል" if handled else "❌ ታልፏል"
        await query.edit_message_text(f"{label} (#{request_id})")
        return


# =============================================================================
# HTTP API — each launched jemo_2 host instance POSTs here for /addcredit requests
# =============================================================================

def _find_host_by_id_and_secret(host_id, secret):
    host = hosts.get(host_id)
    if not host or host["host_api_secret"] != secret:
        return None
    return host


async def api_request_credit(request: web.Request):
    token = request.headers.get("X-Request-API-Key")
    try:
        data = await request.json()
    except Exception:
        data = {}

    host_id = str(data.get("host_id", "")).upper()
    host = _find_host_by_id_and_secret(host_id, token)
    if not host:
        return web.json_response({"ok": False, "error": "unauthorized or unknown host_id"}, status=401)

    try:
        amount = float(data.get("amount"))
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "invalid amount"}, status=400)

    request_id = _new_request_id()
    credit_requests[request_id] = {
        "host_id": host_id,
        "amount": amount,
        "method": data.get("method", "-"),
        "reference": data.get("reference", "-"),
        "message": data.get("message", "-"),
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "decided_at": None,
        "decided_by": None,
    }
    save_state()

    bot = request.app.get("bot")
    if bot is not None:
        await _send_credit_request_card(bot, request_id, credit_requests[request_id])

    return web.json_response({"ok": True, "request_id": request_id})


async def api_request_host_signup(request: web.Request):
    """ማንኛውም ተጫዋች ከየትኛውም ሆስት ቦት «🖥️ ሆስት መሆን እፈልጋለሁ» ሲል ወደዚህ ይደርሳል - Super Admin's
    own bot process ስለሚያስተናግደው (ከየትኛውም ሆስት ቦት ውጭ) ማድረሱ ዋስትና ያለው ነው።"""
    token = request.headers.get("X-Request-API-Key")
    try:
        data = await request.json()
    except Exception:
        data = {}

    host_id = str(data.get("host_id", "")).upper()
    host = _find_host_by_id_and_secret(host_id, token)
    if not host:
        return web.json_response({"ok": False, "error": "unauthorized or unknown host_id"}, status=401)

    try:
        telegram_user_id = int(data.get("telegram_user_id"))
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "invalid telegram_user_id"}, status=400)

    request_id = _new_host_signup_request_id()
    host_signup_requests[request_id] = {
        "host_id": host_id,
        "host_name": data.get("host_name") or host.get("name") or host_id,
        "telegram_user_id": telegram_user_id,
        "name": data.get("name", "N/A"),
        "phone": data.get("phone", "N/A"),
        "username": data.get("username", ""),
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "decided_at": None,
        "decided_by": None,
    }
    save_state()

    bot = request.app.get("bot")
    if bot is not None:
        await _send_host_signup_request_card(bot, request_id, host_signup_requests[request_id])

    return web.json_response({"ok": True, "request_id": request_id})


async def api_health(request: web.Request):
    return web.json_response({"ok": True, "online": True, "timestamp": datetime.now().isoformat()})


async def run_superadmin_api(bot):
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/api/health", api_health)
    app.router.add_post("/api/request_credit", api_request_credit)
    app.router.add_post("/api/request_host_signup", api_request_host_signup)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, API_HOST, API_PORT)
    await site.start()
    print(f"📡 Super Admin API: http://{API_HOST}:{API_PORT}/api/request_credit")
    return runner


# =============================================================================
# Bootstrap
# =============================================================================

async def main_async():
    load_state()
    app = Application.builder().token(SUPER_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("newhost", cmd_newhost))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("hosts", cmd_hosts))
    app.add_handler(CommandHandler("host", cmd_host))
    app.add_handler(CommandHandler("revokehost", cmd_revokehost))
    app.add_handler(CommandHandler("unrevokehost", cmd_unrevokehost))
    app.add_handler(CommandHandler("addcredit", cmd_addcredit))
    app.add_handler(CommandHandler("editcommission", cmd_editcommission))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("requests", cmd_requests))
    app.add_handler(CommandHandler("hostrequests", cmd_hostrequests))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_newhost_flow))

    try:
        await app.bot.set_my_commands([
            BotCommand("start", "🏠 ጀምር"),
            BotCommand("newhost", "🆕 አዲስ ሆስት መዝግብ"),
            BotCommand("hosts", "🏠 ሆስቶች ዝርዝር"),
            BotCommand("host", "🔎 የአንድ ሆስት ዝርዝር"),
            BotCommand("requests", "💰 የክሬዲት ጥያቄዎች"),
            BotCommand("addcredit", "➕ ክሬዲት ጨምር"),
            BotCommand("editcommission", "✏️ ኮሚሽን ቀይር"),
            BotCommand("stats", "📊 ስታቲስቲክስ"),
            BotCommand("revokehost", "🚫 ሆስት አሰናክል (ውሂብ አይጠፋም)"),
            BotCommand("unrevokehost", "✅ ሆስት መልስ አንቃ"),
            BotCommand("help", "🆘 እርዳታ"),
        ])
    except Exception as e:
        log.error(f"set_my_commands error: {e}")

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    api_runner = await run_superadmin_api(app.bot)

    # Re-launch every previously registered host (in-process) on startup.
    for host_id in list(hosts.keys()):
        print(f"🚀 በማስነሳት ላይ፦ {host_id} ...")
        ok, err = await _launch_host_instance(host_id, bot=app.bot)
        if not ok:
            print(f"   ❌ {host_id} አልተነሳም፦ {err}")

    print("👑 Super Admin + ሁሉም ሆስቶች በአንድ ፕሮሰስ ውስጥ ተነስተዋል...")
    try:
        await asyncio.Event().wait()
    finally:
        for host_id in list(_live.keys()):
            await _stop_host_instance(host_id)
        await api_runner.cleanup()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    if SUPER_BOT_TOKEN == "PUT_YOUR_SUPERADMIN_BOT_TOKEN_HERE":
        print("❌ እባክዎ SUPERADMIN_BOT_TOKEN ያስቀምጡ (environment variable ወይም ኮዱ ውስጥ)።")
        input("\nEnter ይጫኑ...")
        return
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n🛑 ቆሟል።")
    except Exception:
        import traceback
        print("\n❌ ስህተት ተፈጥሯል፦\n")
        traceback.print_exc()
    finally:
        input("\nEnter ይጫኑ...")


if __name__ == "__main__":
    main()
