import json
import os
from collections import defaultdict
from datetime import datetime
import pytz

class DataManager:
    def __init__(self):
        self.data_file = "data/role_mentions.json"
        self.automod_file = "data/automod_rules.json"
        self.ist = pytz.timezone('Asia/Kolkata')
    
    def load_role_mentions(self):
        """Load role mention data from file"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                    
                    # Convert to defaultdict
                    guild_data = {}
                    for guild_id, roles in data.items():
                        guild_data[guild_id] = defaultdict(int, roles)
                    
                    return defaultdict(lambda: defaultdict(int), guild_data)
            except:
                return defaultdict(lambda: defaultdict(int))
        return defaultdict(lambda: defaultdict(int))
    
    def save_role_mentions(self, data):
        """Save role mention data to file"""
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        
        # Convert defaultdict to regular dict
        serializable_data = {}
        for guild_id, roles in dict(data).items():
            serializable_data[guild_id] = dict(roles)
        
        with open(self.data_file, 'w') as f:
            json.dump(serializable_data, f, indent=4)
    
    def load_automod_rules(self):
        """Load automod rule data from file"""
        if os.path.exists(self.automod_file):
            try:
                with open(self.automod_file, 'r') as f:
                    data = json.load(f)
                    
                    # Convert string timestamps back to datetime
                    for rule_id, rule_data in data.items():
                        if "expires_at" in rule_data:
                            rule_data["expires_at"] = datetime.fromisoformat(rule_data["expires_at"])
                        if "created_at" in rule_data:
                            rule_data["created_at"] = datetime.fromisoformat(rule_data["created_at"])
                    
                    return data
            except:
                return {}
        return {}
    
    def save_automod_rules(self, data):
        """Save automod rule data to file"""
        os.makedirs(os.path.dirname(self.automod_file), exist_ok=True)
        
        # Convert datetime objects to ISO format for JSON
        serializable_data = {}
        for rule_id, rule_data in data.items():
            serializable_data[rule_id] = rule_data.copy()
            if "expires_at" in serializable_data[rule_id]:
                serializable_data[rule_id]["expires_at"] = serializable_data[rule_id]["expires_at"].isoformat()
            if "created_at" in serializable_data[rule_id]:
                serializable_data[rule_id]["created_at"] = serializable_data[rule_id]["created_at"].isoformat()
        
        with open(self.automod_file, 'w') as f:
            json.dump(serializable_data, f, indent=4)