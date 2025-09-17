def load_omegle():
    data, file = load_db()
    if "omegle" not in data:
        data["omegle"] = {"queue": [], "active_chats": {}}
    return data, file

def save_omegle(data, file=None):
    save_db(data, file)
