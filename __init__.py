from aqt import reviewer
from anki.hooks import wrap
from aqt import mw, progress

from aqt.utils import showInfo


import json
import requests
import urllib
from bs4 import BeautifulSoup
import aqt
from aqt.utils import showCritical
import csv
from anki.hooks import addHook
from aqt.qt import *
import time


word_cache = {}

def setting(key):
    defaults = {
        "jpdb_api_key": None,
        "jpdb_session_token": None,
        "jpdb_mining_deck": 1,
        "word_fields": "Target"
    }
    try:
        return aqt.mw.addonManager.getConfig(__name__).get(key, defaults[key])
    except Exception as e:
        raise Exception(f'setting {key} not found: {e}')
    
def get_word_id(word):
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

    rawRes = requests.request("POST", url, headers=headers, data=payload)

    response = rawRes.json()

    vid = response["vocabulary"][0][0]
    sid = response["vocabulary"][0][1]

    return vid,sid

def get_cached_word_info(word):
    if word in word_cache:
        return word_cache[word]
    else:
        vid, sid = get_word_id(word)
        state = get_word_state(vid, sid)
        word_cache[word] = {"vid": vid, "sid": sid, "state": state}
        return {"vid": vid, "sid": sid, "state": state}
    
def get_word_state(vid,sid):
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

    rawRes = requests.request("POST", url, headers=headers, data=payload)

    state = rawRes.json()["vocabulary_info"][0][0]

    if state:
        return state[0]
    return "not_in_deck"

def add_word_to_deck(vid,sid):
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

    response = requests.request("POST", url, headers=headers, data=payload)

    if "error" in response.json():
        raise Exception(response.json()["error"])

from urllib.parse import quote

def review_word(vid, sid, ease):
    encoded_string = quote(f"vf,{vid},{sid}")
    session = setting("jpdb_session_token")

    payload = {}
    headers = {
        'Cookie': f'sid={session}'
    }

    pre_review_url = f"https://jpdb.io/review?c={encoded_string}"

    response = requests.get(pre_review_url, headers=headers, data=payload)
    soup = BeautifulSoup(response.text, 'html.parser')

    review_no_input = soup.select_one('form[action^="/review"] input[type=hidden][name=r]')
    review_no = int(review_no_input['value'])

    review = 1
    
    if ease >= 2:
        review = 4

    review_url = f"https://jpdb.io/review?c={encoded_string}&r={review_no}&g={review}"

    requests.get(review_url, headers=headers, data=payload)

    return True


# Ease
# 1: Again
# 2: Hard
# 3: Good
# 4: Easy
def my_reviewer_answerCard(self, ease):
    n = self.card.note()

    # Si la carta no tiene el campo elegido, ignorarla
    word_field = setting("word_fields")
    word = None
    for field in word_field.split(","):
        if field.strip() in n:
            word = n[field.strip()]
            break

    if word is None:
        return

    try:
        cached_info = get_cached_word_info(word)
        vid, sid, state = cached_info["vid"], cached_info["sid"], cached_info["state"]
    except:
        showCritical("Error 1: No se pudo conectar con JPDB.io")
        return

    if state == "not_in_deck":
        try:
            add_word_to_deck(vid, sid)
            # Update the cache after successfully adding to the deck
            word_cache[word]["state"] = "in_your_deck"  # Replace "some_state_value" with the appropriate state
        except:
            showCritical("Error 2: No se pudo añadir la palabra al deck")
            return

    try:
        review_word(vid,sid,ease)
    except:
        showCritical("Error 3: No se pudo enviar la review a JPDB.io")
    

reviewer.Reviewer._answerCard = wrap(reviewer.Reviewer._answerCard, my_reviewer_answerCard, "before")

# Function to save the cache to a CSV file
def save_cache_to_csv():
    with open("word_cache.csv", "w", newline="") as csvfile:
        fieldnames = ["word", "vid", "sid", "state"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

        writer.writeheader()
        for word, info in word_cache.items():
            writer.writerow({"word": word, "vid": info["vid"], "sid": info["sid"], "state": info["state"]})

# Function to load the cache from a CSV file
def load_cache_from_csv():
    try:
        with open("word_cache.csv", newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                word_cache[row["word"]] = {"vid": row["vid"], "sid": row["sid"], "state": row["state"]}
    except FileNotFoundError:
        pass  # The file doesn't exist yet

# Load the cache at the beginning
load_cache_from_csv()

# Save the cache when exiting Anki (optional)
addHook("unloadProfile", save_cache_to_csv)

def addWordstoCacheWrapper():
    addWordstoCache()


def addWordstoCache():
    cards = mw.col.findCards("is:new")
    total_cards = len(cards)

    # Show the progress bar with the total number of cards
    mw.progress.start(label="Adding Words to Cache", max=total_cards)

    for index, id in enumerate(cards):
        # Update the progress bar with the current card index
        mw.progress.update(value=index, label="Adding Words to Cache - Card %d/%d" % (index + 1, total_cards))

        card = mw.col.getCard(id)
        note = card.note()


        word_field = setting("word_fields")
        word = None
        for field in word_field.split(","):
            if field.strip() in note:
                word = note[field.strip()]
                break

        if word is None:
            return
        
        if word in word_cache:
            break

        try:
            cached_info = get_cached_word_info(word)
            vid, sid, state = cached_info["vid"], cached_info["sid"], cached_info["state"]
        except:
            showCritical("Error 1: No se pudo conectar con JPDB.io")
            continue
        
        if state == "not_in_deck":
            try:
                add_word_to_deck(vid, sid)
                # Update the cache after successfully adding to the deck
                word_cache[word]["state"] = "in_your_deck"  # Replace "some_state_value" with the appropriate state
            except:
                showCritical("Error 2: No se pudo añadir la palabra al deck")
                continue
        time.sleep(3)

    # Close the progress bar when the loop is finished
    mw.progress.finish()
    showInfo(f"Finished {total_cards}")
    
action = QAction(f"Cache words for jpdb", mw)
mw.form.menuTools.addAction(action)
action.triggered.connect(addWordstoCache)