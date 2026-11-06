import json, os, re, sys, requests

TERM = "2271"  # Spring 2027
BASE = "https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes"
HEADERS = {"Authorization": "Bearer null", "User-Agent": "Mozilla/5.0"}
STATE_FILE = "state.json"

# Tempe only: any section whose campus field doesn't say Tempe (ASU Online, iCourse,
# other campuses) is skipped.
CAMPUS_KEYWORD = "tempe"

# Class numbers you're already in (from your calendar). Seat changes in these are
# ignored, because a seat opening in your own section just means someone dropped.
ENROLLED = {"10908", "15644", "14606", "34346", "10904", "13482", "12912"}

# "say" holds the "what to do" line of each alert. Lookup order for an alert is:
#   <event>_pref (preferred instructor only), <event>_<kind>, <event>
# event = new | opened | filled     kind = lecture | recitation | lab
WATCH = [
    {"subject": "MAT", "catalog": "343", "name": "Linear Algebra",
     "sections": {"13480"}, "urgent": True,
     "say": {"opened": "Go now! Open My ASU and swap it with your current Linear Algebra "
                       "section (swap, don't drop first). Then see if your CSE 325 "
                       "recitation can move."}},

    {"subject": "DAT", "catalog": "300", "name": "DAT 300", "sections": None,
     "prefer": "Ali", "urgent": True, "warn_zero": True,
     "say": {"opened_pref": "Go enroll right now. You've already got a plan for this one.",
             "opened": "You're a little safer now. No action needed.",
             "filled_pref": "No action needed unless the other section fills too.",
             "filled": "Talk to your advisor today about a permission to add or a waitlist spot.",
             "new": "Check whether the time works and enroll if it does."}},

    {"subject": "CSE", "catalog": "320", "name": "CSE 320", "sections": None,
     "say": {"new_lecture": "That's another lecture time, so it may fit around your other "
                            "classes better. Check Class Search and swap if it does.",
             "opened_lecture": "Check Class Search and swap in if the time works better for you.",
             "new_recitation": "Only worth switching if the time beats your current one.",
             "opened_recitation": "Only worth switching if the time beats your current one.",
             "new": "Check Class Search and see if it fits.",
             "opened": "Check Class Search and see if it fits."}},

    {"subject": "CSE", "catalog": "325", "name": "CSE 325", "sections": None,
     "say": {"new_lecture": "That's a second lecture time, which could unblock your schedule. "
                            "Check Class Search and swap if it fits.",
             "opened_lecture": "Check Class Search and swap in if the time works for you.",
             "new_recitation": "Only matters if you've landed the Linear Algebra swap. If you "
                               "have and the time works, swap your recitation.",
             "opened_recitation": "Only matters if you've landed the Linear Algebra swap. If you "
                                  "have and the time works, swap your recitation.",
             "new": "Check Class Search and see if it fits.",
             "opened": "Check Class Search and see if it fits."}},
]


def notify(msg, title, priority="high", tags=""):
    print("ALERT:", title, "-", msg)
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    r = requests.post(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}",
                      data=msg.encode("utf-8"), headers=headers, timeout=15)
    r.raise_for_status()


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


def is_tempe(info):
    """True / False, or None if the campus field can't be found."""
    v = str(get(info, ("CAMPUS", "CAMPUSDESCR", "CAMPUSDESC", "CAMPUSLOC", "LOCATION")))
    if not v:
        return None
    return CAMPUS_KEYWORD in v.lower()


def component(info):
    """lecture / recitation / lab / unknown. Integrated lecture+lab counts as lecture."""
    v = str(get(info, ("SSR_COMPONENT", "COMPONENT", "SECTIONTYPE", "CLASSTYPE",
                       "COMPONENTDESCR", "INSTRUCTIONTYPE"))).lower()
    if not v:
        return "unknown"
    if "rec" in v:
        return "recitation"
    if "lec" in v or "integrated" in v:
        return "lecture"
    if "lab" in v:
        return "lab"
    return "unknown"


def prof(info):
    p = get(info, ("INSTRUCTORSLIST", "INSTRUCTOR", "FULLNAME", "INSTRUCTORS"))
    if isinstance(p, list):
        p = ", ".join(map(str, p))
    return str(p)


def is_pref(w, info):
    if not w.get("prefer"):
        return False
    return re.search(rf"\b{re.escape(w['prefer'])}\b", prof(info), re.I) is not None


def when(info):
    days = get(info, ("DAYSLIST", "DAYS", "MEETINGDAYS"))
    if isinstance(days, list):
        days = "".join(map(str, days))
    start = get(info, ("STARTTIME", "STIME", "START_TIME"))
    return f"{days} {start}".strip()


def describe(w, info, kind, pref):
    if w.get("prefer"):
        if not prof(info):
            return "a section"
        return f"{w['prefer']}'s section" if pref else "the other section"
    noun = {"lecture": "lecture", "recitation": "recitation", "lab": "lab"}.get(kind, "section")
    t = when(info)
    return f"the {t} {noun}" if t else f"a {noun}"


def say(w, event, kind, pref=False):
    s = w.get("say", {})
    keys = ([f"{event}_pref"] if pref else []) + [f"{event}_{kind}", event]
    for k in keys:
        if k in s:
            return s[k]
    return "Take a look in Class Search."


def prio(w, kind, event, pref):
    if w.get("urgent") and (pref or event == "filled" or not w.get("prefer")):
        return "urgent"
    return "default" if kind in ("recitation", "lab") else "high"


def fetch(subject, catalog):
    base = {"refine": "Y", "catalogNbr": catalog, "subject": subject, "term": TERM,
            "honors": "F", "promod": "F", "searchType": "all"}
    # First try asking ASU for in-person Tempe only; if that errors or comes back empty,
    # fall back to everything. The Tempe check in main() is what actually decides.
    attempts = [{"campusOrOnlineSelection": "C", "campus": "TEMPE"},
                {"campusOrOnlineSelection": "A"}]
    errors = 0
    for extra in attempts:
        try:
            r = requests.get(BASE, params={**base, **extra}, headers=HEADERS, timeout=20)
            r.raise_for_status()
            classes = r.json().get("classes", [])
            if classes:
                return classes
        except Exception as e:
            errors += 1
            print(f"Fetch attempt {extra} failed for {subject} {catalog}: {e}")
    if errors == len(attempts):
        raise RuntimeError("all fetch attempts failed")
    return []


def main():
    try:
        state = json.load(open(STATE_FILE))
    except FileNotFoundError:
        state = {}
    new_state, failures = dict(state), 0
    warned = set()
    dumped = False

    for w in WATCH:
        course = f'{w["subject"]} {w["catalog"]}'
        name = w["name"]
        try:
            classes = fetch(w["subject"], w["catalog"])
        except Exception as e:
            print(f"Fetch failed for {course}: {e}")
            failures += 1
            continue

        # On manual runs, print one raw record so field names can be checked in the log.
        if not dumped and classes and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
            print("SAMPLE RECORD:", json.dumps(classes[0].get("CLAS", {}), indent=1))
            dumped = True

        # Pass 1: collect the Tempe sections we care about.
        seen = {}
        for c in classes:
            info = c["CLAS"]
            nbr = str(info["CLASSNBR"])
            if w.get("sections") and nbr not in w["sections"]:
                continue
            tempe = is_tempe(info)
            if tempe is False:
                continue
            if tempe is None and "campus" not in warned:
                print("WARNING: no campus field found, so the Tempe filter can't be applied.")
                warned.add("campus")
            kind = component(info)
            if kind == "unknown" and "component" not in warned:
                print("WARNING: no component field found, so lecture vs recitation is unknown.")
                warned.add("component")
            seen[nbr] = (info, kind, seats_open(info))

        cur = {n: v[2] for n, v in seen.items()}
        first_run = course not in state
        prev = state.get(course, {})

        if not cur:
            if prev:
                print(f"{course}: no Tempe sections returned this time, keeping old data.")
                continue
            if first_run:
                notify(f"Setup check: I couldn't find any Tempe sections for {name} yet. "
                       "If that seems wrong, the term or filter may need a look.",
                       "Setup check", "default", "warning")
            new_state[course] = {}
            continue

        # Pass 2: compare against last run.
        all_zero = all(v == 0 for v in cur.values())
        for nbr, (info, kind, seats) in seen.items():
            p = prev.get(nbr)
            if first_run or nbr in ENROLLED:
                continue
            pref = is_pref(w, info)
            who = describe(w, info, kind, pref)
            plural = "seat" if seats == 1 else "seats"

            if p is None:
                notify(f"New option! {name}: {who} just appeared ({seats} {plural} open). "
                       f"{say(w, 'new', kind, pref)}",
                       "New section", prio(w, kind, "new", pref), "new")
            elif seats > 0 and p == 0:
                notify(f"Good news! {name}: {who} just got {seats} open {plural}. "
                       f"{say(w, 'opened', kind, pref)}",
                       "Seat opened", prio(w, kind, "opened", pref), "tada")
            elif w.get("warn_zero") and seats == 0 and p > 0:
                extra = " Every section is full now." if all_zero else ""
                action = say(w, "filled", kind, pref and not all_zero)
                notify(f"Uh oh. {name}: {who} just filled up.{extra} {action}",
                       "Heads up", prio(w, kind, "filled", pref), "warning")

        new_state[course] = {**prev, **cur}

    json.dump(new_state, open(STATE_FILE, "w"), indent=2, sort_keys=True)
    if failures == len(WATCH):
        sys.exit(1)


main()
