import json, os, sys, requests

TERM = "2271"
BASE = "https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes"
HEADERS = {"Authorization": "Bearer null", "User-Agent": "Mozilla/5.0"}
STATE_FILE = "state.json"

WATCH = [
    {"subject": "MAT", "catalog": "343", "name": "Linear Algebra",
     "sections": {"13480"},
     "opened": "Go enroll right now. Once you're in, see if your CSE 325 recitation can move to match."},
    {"subject": "DAT", "catalog": "300", "name": "DAT 300", "sections": None,
     "prefer": "Ali", "warn_zero": True,
     "opened_pref": "Go enroll right now, you've already got a plan for this one.",
     "opened": "You're safe for now, no action needed.",
     "filled": "Check with your advisor today about getting a spot before the other one goes too.",
     "new": "Check it out and enroll if the time works."},
    {"subject": "CSE", "catalog": "320", "name": "CSE 320", "sections": None,
     "opened": "Open Class Search and see if it fits your schedule.",
     "new": "Open Class Search and see if it fits your schedule."},
    {"subject": "CSE", "catalog": "325", "name": "CSE 325", "sections": None,
     "opened": "If this is the recitation that works with Linear Algebra, switch to it now.",
     "new": "If this is the recitation that works with Linear Algebra, switch to it now."},
]

def notify(msg, title="Class update"):
    print("ALERT:", msg)
    requests.post(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}",
                  data=msg.encode("utf-8"),
                  headers={"Title": title, "Priority": "high"}, timeout=15)

def get(info, keys, default=""):
    for k in keys:
        v = info.get(k)
        if v not in (None, "", []):
            return v
    return default

def seats_open(info):
    for k in ("SEATSOPEN", "SEATS_OPEN", "SEATSAVAILABLE", "AVAILSEATS"):
        if k in info and str(info[k]).strip() != "":
            return int(info[k])
    caps = [k for k in ("ENRLCAP", "ENRL_CAP", "CAPACITY", "CLASSCAP") if k in info]
    enrl = [k for k in ("ENRLTOT", "ENRL_TOT", "ENROLLED", "ENRLTOTAL") if k in info]
    if caps and enrl:
        return int(info[caps[0]]) - int(info[enrl[0]])
    print("UNKNOWN SEAT FIELDS:", json.dumps(info, indent=1))
    raise SystemExit("Could not find seat count field")

def label(w, info):
    prof = get(info, ("INSTRUCTORSLIST", "INSTRUCTOR", "FULLNAME"))
    if isinstance(prof, list):
        prof = ", ".join(map(str, prof))
    if w.get("prefer"):
        return "Ali's section" if w["prefer"].lower() in str(prof).lower() else "the other section"
    days = get(info, ("DAYSLIST", "DAYS", "MEETINGDAYS"))
    if isinstance(days, list):
        days = "".join(map(str, days))
    start = get(info, ("STARTTIME", "STIME", "START_TIME"))
    t = f"{days} {start}".strip()
    return f"the {t} section" if t else "a section"

def is_pref(w, info):
    return label(w, info) == "Ali's section"

def fetch(subject, catalog):
    params = {"refine": "Y", "campusOrOnlineSelection": "A", "catalogNbr": catalog,
              "subject": subject, "term": TERM, "honors": "F", "promod": "F",
              "searchType": "all"}
    r = requests.get(BASE, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json().get("classes", [])

def main():
    try:
        state = json.load(open(STATE_FILE))
    except FileNotFoundError:
        state = {}
    new_state, failures = dict(state), 0

    for w in WATCH:
        course = f'{w["subject"]} {w["catalog"]}'
        name = w["name"]
        try:
            classes = fetch(w["subject"], w["catalog"])
        except Exception as e:
            print(f"Fetch failed for {course}: {e}")
            failures += 1
            continue

        first_run = course not in state
        prev_course = state.get(course, {})
        cur_course = {}
        for c in classes:
            info = c["CLAS"]
            nbr = str(info["CLASSNBR"])
            if w["sections"] and nbr not in w["sections"]:
                continue
            seats = seats_open(info)
            cur_course[nbr] = seats
            prev = prev_course.get(nbr)
            who = label(w, info)
            plural = "seat" if seats == 1 else "seats"

            if first_run:
                continue
            if prev is None:
                notify(f"New option! A new {name} section just appeared ({who}, "
                       f"{seats} {plural} open). {w.get('new', 'Take a look.')}")
            elif seats > 0 and prev == 0:
                action = w.get("opened_pref") if is_pref(w, info) and w.get("opened_pref") \
                    else w.get("opened", "Take a look.")
                notify(f"Good news! {name}: {who} just opened up "
                       f"({seats} {plural}). {action}", "Seat opened")
            elif w.get("warn_zero") and seats == 0 and prev > 0:
                extra = " Every section is now full." if sum(cur_course.values()) == 0 \
                    and len(cur_course) == len(classes) else ""
                notify(f"Uh oh. {name}: {who} just filled up.{extra} "
                       f"{w.get('filled', '')}".strip(), "Heads up")
        new_state[course] = cur_course

    json.dump(new_state, open(STATE_FILE, "w"), indent=2, sort_keys=True)
    if failures == len(WATCH):
        sys.exit(1)

main()
