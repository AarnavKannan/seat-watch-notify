import json, os, sys, requests

TERM = "2271"  # Spring 2027 (verify in the URL on Class Search)
BASE = "https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes"
HEADERS = {"Authorization": "Bearer null", "User-Agent": "Mozilla/5.0"}
STATE_FILE = "state.json"

# sections=None means watch every section of the course
WATCH = [
    {"subject": "CSE", "catalog": "320", "sections": None},
    {"subject": "MAT", "catalog": "343", "sections": {"13480"}},
    {"subject": "DAT", "catalog": "300", "sections": None},
]

def notify(msg):
    print("ALERT:", msg)
    requests.post(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}",
                  data=msg.encode("utf-8"),
                  headers={"Title": "ASU seat alert", "Priority": "high"},
                  timeout=15)

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

            if prev is None and not first_run:
                notify(f"NEW section {course} #{nbr} appeared ({seats} seats open)")
            elif seats > 0 and (prev is None or prev == 0):
                notify(f"{course} #{nbr} now has {seats} open seat(s)")
        new_state[course] = cur_course

    json.dump(new_state, open(STATE_FILE, "w"), indent=2, sort_keys=True)
    if failures == len(WATCH):
        sys.exit(1)

def seats_open(info):
    # Try likely direct field names first
    for k in ("SEATSOPEN", "SEATS_OPEN", "SEATSAVAILABLE", "AVAILSEATS"):
        if k in info and str(info[k]).strip() != "":
            return int(info[k])
    # Otherwise compute from capacity minus enrolled
    caps = [k for k in ("ENRLCAP", "ENRL_CAP", "CAPACITY", "CLASSCAP") if k in info]
    enrl = [k for k in ("ENRLTOT", "ENRL_TOT", "ENROLLED", "ENRLTOTAL") if k in info]
    if caps and enrl:
        return int(info[caps[0]]) - int(info[enrl[0]])
    # Nothing matched: show every field so we can pick the right one
    print("UNKNOWN SEAT FIELDS. Available keys and values:")
    for k, v in sorted(info.items()):
        print(f"  {k}: {v}")
    raise SystemExit("Could not find seat count field")

main()
