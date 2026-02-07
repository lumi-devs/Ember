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
                with open(self.member_data_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ERROR] Failed to load member data: {e}")
                return {}
        return {}
    
    def load_system_data(self):
        """Load system data (pairings, check-ins)"""
        if os.path.exists(self.system_data_file):
            try:
                with open(self.system_data_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ERROR] Failed to load system data: {e}")
                return {"pairings": {}, "checkins": {}, "activity": {}}
        return {"pairings": {}, "checkins": {}, "activity": {}}
    
    def save_member_data(self, data):
        """Save member list data to file"""
        os.makedirs(os.path.dirname(self.member_data_file), exist_ok=True)
        with open(self.member_data_file, 'w') as f:
            json.dump(data, f, indent=4)
    
    def save_system_data(self, data):
        """Save system data to file"""
        os.makedirs(os.path.dirname(self.system_data_file), exist_ok=True)
        with open(self.system_data_file, 'w') as f:
            json.dump(data, f, indent=4)