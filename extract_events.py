"""Turn a decoded Effiplan /activities/events protobuf tree into calendar events.

Field numbers were reverse-engineered from a real response (no official schema):
  root[3] (body)
    [4] (week data)
      [5]  "activities" wrapper (single message), which itself contains:
             [5]  repeated day-code entries (e.g. "RH" rest days): id, code, {start_date, end_date}
             [12] repeated shift entries: id, {date, start_dt, end_dt}, post_code, need_code, task_code
      [7]  repeated daily counters (only present when 'tot'/'zon'/'cpt' keywords requested)
    [7] (lookup tables, keyed by field number, each entry = {code -> label(s)})
      [5]  day-code labels   (e.g. "..RH" -> "(..RH) Repos hebdo")
      [11] post labels       (post_xxx -> short/long name, e.g. store name)
      [12] need labels       (need_xxx -> longer descriptive name)
      [13] task labels       (task_xxx -> short/long name, e.g. "Vente")
"""

from decode_proto import decode_message, get_all, get_first


def _lookup_table(lookup_fields, group_field_num, label_field_num=3, fallback_label_field_num=2):
    table = {}
    for entry in get_all(lookup_fields, group_field_num):
        code = get_first(entry, 1)
        details = get_first(entry, 2, [])
        label = get_first(details, label_field_num) or get_first(details, fallback_label_field_num)
        if code is not None:
            table[code] = label
    return table


def parse_events_response(raw_bytes, include_rest_days=False):
    root = decode_message(raw_bytes)
    body = get_first(root, 3, [])
    week_data = get_first(body, 4, [])
    activities = get_first(week_data, 5, [])
    lookup = get_first(body, 7, [])

    day_code_labels = _lookup_table(lookup, 5)
    post_labels = _lookup_table(lookup, 11)
    need_labels = _lookup_table(lookup, 12)
    task_labels = _lookup_table(lookup, 13)

    events = []

    for entry in get_all(activities, 12):
        entry_id = get_first(entry, 1)
        time_info = get_first(entry, 2, [])
        start_dt = get_first(time_info, 2)
        end_dt = get_first(time_info, 3)
        post_code = get_first(entry, 3)
        need_code = get_first(entry, 4)
        task_code = get_first(entry, 5)

        if not (start_dt and end_dt):
            continue

        task_label = task_labels.get(task_code, task_code or "")
        place_label = need_labels.get(need_code) or post_labels.get(post_code) or ""
        title = f"{task_label} - {place_label}".strip(" -")

        events.append({
            "id": entry_id,
            "title": title or "Travail",
            "start": start_dt,
            "end": end_dt,
            "all_day": False,
        })

    if include_rest_days:
        for entry in get_all(activities, 5):
            entry_id = get_first(entry, 1)
            code = get_first(entry, 2)
            date_info = get_first(entry, 4, [])
            start_date = get_first(date_info, 1)
            end_date = get_first(date_info, 2)
            if not start_date:
                continue
            label = day_code_labels.get(code, code or "Absence")
            events.append({
                "id": entry_id,
                "title": label,
                "start": start_date,
                "end": end_date or start_date,
                "all_day": True,
            })

    return events
