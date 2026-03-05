# Data loading and saving
import json
import os


class DataManager:
    def __init__(self):
        self.member_data_file = "data/member_list_data.json"
        self.system_data_file = "data/newbies_system_data.json"

    def load_member_data(self):
        """Load member list data from file"""
        if os.path.exists(self.member_data_file):
            try:
                with open(self.member_data_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ERROR] Failed to load member data: {e}")
                return {}
        return {}

    def load_system_data(self):
        if os.path.exists(self.system_data_file):
            try:
                with open(self.system_data_file, "r") as f:
                    data = json.load(f)
                    data.setdefault("pairings", {})
                    data.setdefault("checkins", {})
                    data.setdefault("member_activity", {})
                    data.setdefault("priority_queue", [])
                    data.setdefault("messages_to_delete", {})
                    data.setdefault("ignored", [])  # new
                    data.setdefault("sticky_messages", {})  # new
                    return data
            except Exception as e:
                print(f"[ERROR] Failed to load system data: {e}")
        return {
            "pairings": {},
            "checkins": {},
            "member_activity": {},
            "priority_queue": [],
            "messages_to_delete": {},
            "ignored": [],  # new
            "sticky_messages": {},  # new
        }

    def save_member_data(self, data):
        """Save member list data to file"""
        os.makedirs(os.path.dirname(self.member_data_file), exist_ok=True)
        with open(self.member_data_file, "w") as f:
            json.dump(data, f, indent=4)

    def save_system_data(self, data):
        """Save system data to file"""
        os.makedirs(os.path.dirname(self.system_data_file), exist_ok=True)
        with open(self.system_data_file, "w") as f:
            json.dump(data, f, indent=4)

    def load_system_data(self):
        """Load system data (pairings, check-ins, activity)"""
        if os.path.exists(self.system_data_file):
            try:
                with open(self.system_data_file, "r") as f:
                    data = json.load(f)
                    # Ensure all required keys exist
                    data.setdefault("pairings", {})
                    data.setdefault("checkins", {})
                    data.setdefault("member_activity", {})
                    data.setdefault("priority_queue", [])
                    data.setdefault("messages_to_delete", {})
                    return data
            except Exception as e:
                print(f"[ERROR] Failed to load system data: {e}")
        return {
            "pairings": {},
            "checkins": {},
            "member_activity": {},
            "priority_queue": [],
            "messages_to_delete": {},
        }
