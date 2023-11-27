"""Anki addon to add words to a deck on JPDB.io when reviewing them"""

# pylint:disable-msg=E0401
import json
import time
import urllib
import csv
import os

from urllib.parse import quote
import requests

from aqt import reviewer
from anki.hooks import wrap, addHook

from anki import hooks, version as anki_version
from bs4 import BeautifulSoup
import aqt
from aqt.utils import showCritical
from aqt.qt import *
from aqt import mw

parts = anki_version.split(".")
major = int(parts[0])
minor = int(parts[1])
point_release = int(parts[2])


word_cache = {}


def setting(key):
    """Settings wrapper"""
    defaults = {
        "jpdb_api_key": None,
        "jpdb_session_token": None,
        "jpdb_mining_deck": 1,
        "word_fields": "Target",
    }
    try:
        return aqt.mw.addonManager.getConfig(__name__).get(key, defaults[key])
    except Exception as error:
        raise KeyError(f'setting {key} not found: {error}') from error


def get_word_id(word):
    """Get the word id from JPDB.io"""
    url = "https://jpdb.io/api/v1/parse"

    payload = json.dumps({
        "text": [
            word
        ],
        "position_length_encoding": "utf16",
        "token_fields": [],
        "vocabulary_fields": [
            "vid",
            "sid",
            "rid"
        ]
    })
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {setting("jpdb_api_key")}'
    }

    raw_res = requests.request(
        "POST", url, headers=headers, data=payload, timeout=5)

    response = raw_res.json()

    vid = response["vocabulary"][0][0]
    sid = response["vocabulary"][0][1]

    return vid, sid


def get_cached_word_info(word):
    """Get the word info from the cache or from JPDB.io"""
    if word in word_cache:
        return word_cache[word]
    vid, sid = get_word_id(word)
    state = get_word_state(vid, sid)
    word_cache[word] = {"vid": vid, "sid": sid, "state": state}
    return {"vid": vid, "sid": sid, "state": state}


def get_word_state(vid, sid):
    """Get the word state from JPDB.io"""
    url = "https://jpdb.io/api/v1/lookup-vocabulary"

    payload = json.dumps({
        "fields": [
            "card_state"
        ],
        "list": [
            [
                vid,
                sid
            ]
        ]
    })
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {setting("jpdb_api_key")}'
    }

    raw_res = requests.request(
        "POST", url, headers=headers, data=payload, timeout=5)

    state = raw_res.json()["vocabulary_info"][0][0]

    if state:
        return state[0]
    return "not_in_deck"


def add_word_to_deck(vid, sid):
    """Add the word to the deck"""
    url = "https://jpdb.io/api/v1/deck/add-vocabulary"
    deck_id = setting("jpdb_mining_deck")

    payload = json.dumps({
        "id": deck_id,
        "vocabulary": [
            [
                vid,
                sid
            ]
        ]
    })
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {setting("jpdb_api_key")}'
    }

    response = requests.request(
        "POST", url, headers=headers, data=payload, timeout=5)

    if "error" in response.json():
        raise ValueError(response.json()["error"])


def review_word(vid, sid, ease):
    """Review the word"""
    encoded_string = quote(f"vf,{vid},{sid}")
    session = setting("jpdb_session_token")

    payload = {}
    headers = {
        'Cookie': f'sid={session}'
    }

    pre_review_url = f"https://jpdb.io/review?c={encoded_string}"

    response = requests.get(
        pre_review_url, headers=headers, data=payload, timeout=10)
    soup = BeautifulSoup(response.text, 'html.parser')

    review_no_input = soup.select_one(
        'form[action^="/review"] input[type=hidden][name=r]')
    review_no = int(review_no_input['value'])

    review = 1

    if ease >= 2:
        review = 4

    review_url = f"https://jpdb.io/review?c={encoded_string}&r={review_no}&g={review}"

    requests.get(review_url, headers=headers, data=payload, timeout=10)

    return True


# Ease
# 1: Again
# 2: Hard
# 3: Good
# 4: Easy
def my_reviewer_answer_card(self, ease):
    """Override the answerCard function to add the word to the deck if it's not there"""
    note = self.card.note()

    # Si la carta no tiene el campo elegido, ignorarla
    word_field = setting("word_fields")
    word = None
    for field in word_field.split(","):
        if field.strip() in note:
            word = note[field.strip()]
            break

    if word is None:
        return

    try:
        cached_info = get_cached_word_info(word)
        vid, sid, state = cached_info["vid"], cached_info["sid"], cached_info["state"]
    # pylint:disable-msg=W0718
    except Exception:
        showCritical("Error 1: No se pudo conectar con JPDB.io")
        return

    if state == "not_in_deck":
        try:
            add_word_to_deck(vid, sid)
            # Update the cache after successfully adding to the deck
            # Replace "some_state_value" with the appropriate state
            word_cache[word]["state"] = "in_your_deck"
        # pylint:disable-msg=W0718
        except Exception:
            showCritical("Error 2: No se pudo añadir la palabra al deck")
            return

    try:
        review_word(vid, sid, ease)
    # pylint:disable-msg=W0718
    except Exception:
        showCritical("Error 3: No se pudo enviar la review a JPDB.io")


# pylint:disable-msg=W0212
reviewer.Reviewer._answerCard = wrap(
    reviewer.Reviewer._answerCard, my_reviewer_answer_card, "before")

addon_path = os.path.dirname(__file__)
csv_folder = os.path.join(mw.pm.addonFolder(), addon_path, 'word_cache.csv')


def save_cache_to_csv():
    """Save the cache to a CSV file"""
    with open(csv_folder, "w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["word", "vid", "sid", "state"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

        writer.writeheader()
        for word, info in word_cache.items():
            writer.writerow(
                {"word": word if word else "", "vid": info["vid"] if "vid" in info else "",
                 "sid": info["sid"] if "sid" in info else "",
                 "state": info["state"] if "state" in info else ""})


def load_cache_from_csv():
    """Load the cache from a CSV file"""
    try:
        with open(csv_folder, "r", newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                word_cache[row["word"]] = {
                    "vid": row["vid"] if "vid" in row else "",
                    "sid": row["sid"] if "sid" in row else "",
                    "state": row["state"] if "state" in row else ""}
    except FileNotFoundError:
        with open(csv_folder, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = ["word", "vid", "sid", "state"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            writer.writeheader()


# Load the cache at the beginning
load_cache_from_csv()

# pylint:disable-msg=W0613


def on_note_will_be_added(col, note, deck_id):
    """Add the word to the deck if it's not there"""

    word_field = setting("word_fields")
    word = None
    for field in word_field.split(","):
        if field.strip() in note:
            word = note[field.strip()]
            break

    if word is None:
        return

    if word in word_cache:
        return

    try:
        cached_info = get_cached_word_info(word)
        vid, sid, state = cached_info["vid"], cached_info["sid"], cached_info["state"]
    # pylint:disable-msg=W0718
    except Exception:
        showCritical(f"Error 1: No se pudo conectar con JPDB.io {word}")

    if state == "not_in_deck":
        try:
            add_word_to_deck(vid, sid)
            # Update the cache after successfully adding to the deck
            # Replace "some_state_value" with the appropriate state
            word_cache[word]["state"] = "in_your_deck"
        # pylint:disable-msg=W0718
        except Exception:
            showCritical("Error 2: No se pudo añadir la palabra al deck")


# Add the hook
if major <= 2:
    addHook("add_cards_did_add_note", on_note_will_be_added)

    # Save the cache when exiting Anki (optional)
    addHook("unloadProfile", save_cache_to_csv)
else:
    hooks.note_will_be_added.append(on_note_will_be_added)
    hooks.profile_will_close.append(save_cache_to_csv)
