import json
from pathlib import Path
from datetime import datetime
from config import BASE_DIR

EMOTION_FILE = BASE_DIR / "data" / "emotion.json"

class EmotionSystem:
    def __init__(self):
        self.states = {}
        self.load()
    
    def load(self):
        if EMOTION_FILE.exists():
            try:
                self.states = json.loads(EMOTION_FILE.read_text(encoding="utf-8"))
            except:
                self.states = {}
    
    def save(self):
        EMOTION_FILE.parent.mkdir(exist_ok=True)
        EMOTION_FILE.write_text(json.dumps(self.states, ensure_ascii=False, indent=2), encoding="utf-8")
        
    def get_state(self, user_id: str) -> dict:
        """Get state for a user, initializing if new."""
        # Convert ID to string key
        key = str(user_id)
        
        # Default State
        if key not in self.states:
            self.states[key] = {
                "affection": 50,  # 0-100 (50 = Classmate/Friend, NOT Stranger)
                "mood": 0,        # -50 to +50 (Temporary mood)
                "energy": 80,     # 0-100 (Stamina)
                "last_update": datetime.now().isoformat()
            }
        
        # Apply time-based decay/recovery (Energy recovers, Mood neutralizes)
        self._apply_time_effects(key)
        
        return self.states[key]
    
    def update_state(self, user_id: str, affection_delta=0, mood_delta=0, energy_delta=0):
        key = str(user_id)
        state = self.get_state(key) # This also applies time effects first
        
        # Update values with clamping
        state["affection"] = max(0, min(100, state["affection"] + affection_delta))
        state["mood"] = max(-50, min(50, state["mood"] + mood_delta))
        state["energy"] = max(0, min(100, state["energy"] + energy_delta))
        
        state["last_update"] = datetime.now().isoformat()
        self.save()
        
        return state

    def _apply_time_effects(self, key: str):
        state = self.states[key]
        last_str = state.get("last_update")
        if not last_str:
            return
            
        last_time = datetime.fromisoformat(last_str)
        now = datetime.now()
        elapsed_hours = (now - last_time).total_seconds() / 3600
        
        if elapsed_hours < 1:
            return
            
        # Recovery Logic
        # Energy recovers: +10 per hour
        energy_rec = int(elapsed_hours * 10)
        state["energy"] = min(100, state["energy"] + energy_rec)
        
        # Mood neutralizes: moves towards 0 by 5 per hour
        if state["mood"] > 0:
            state["mood"] = max(0, state["mood"] - int(elapsed_hours * 5))
        elif state["mood"] < 0:
            state["mood"] = min(0, state["mood"] + int(elapsed_hours * 5))
            
        state["last_update"] = now.isoformat()

    def get_prompt_text(self, user_id: str) -> str:
        """Generate prompt context describing current emotion."""
        state = self.get_state(user_id)
        
        aff = state["affection"]
        mood = state["mood"]
        energy = state["energy"]
        
        # Generate Affection description
        if aff >= 90: aff_desc = "Love (Devoted)"
        elif aff >= 70: aff_desc = "High Trust (Close)"
        elif aff >= 40: aff_desc = "Neutral (Friend)"
        else: aff_desc = "Low (Stranger/Cold)"
        
        # Generate Mood description
        if mood >= 30: mood_desc = "Excellent (Happy/Playful)"
        elif mood >= 10: mood_desc = "Good (Positive)"
        elif mood >= -10: mood_desc = "Neutral (Calm)"
        elif mood >= -30: mood_desc = "Bad (Annoyed/Sarcastic)"
        else: mood_desc = "Terrible (Angry/Cold)"
        
        # Generate Energy description
        if energy >= 80: ene_desc = "High (Energetic)"
        elif energy >= 30: ene_desc = "Normal"
        else: ene_desc = "Low (Sleepy/Tired)"
        
        return f"""
[Emotional State]
- Affection: {aff} ({aff_desc})
- Mood: {mood} ({mood_desc})
- Energy: {energy} ({ene_desc})
(Instruction: Adjust your tone based on these. Low Mood = Cold/Sarcastic. High Affection = Sweet/Deredere. Low Energy = Short/Lazy.)
""".strip()
