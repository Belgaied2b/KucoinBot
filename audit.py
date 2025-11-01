# audit.py
import json, time
def log_event(kind:str, payload:dict):
    payload = dict(payload)
    payload["ts"] = time.time()
    line = json.dumps({"kind":kind, **payload}, ensure_ascii=False)
    print(line)  # ou fichier rotatif
