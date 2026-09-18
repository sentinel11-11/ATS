import json
import os
import time
import uuid
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
root = os.path.abspath(os.path.join(BASE, "..", "shared"))
inbox = os.path.join(root, "inbox")
if not os.path.exists(inbox):
    os.makedirs(inbox)
item = {
    "id": uuid.uuid4().hex,
    "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "caller": "84950000000",
    "mode": "2",
    "speech": "Соедините с бюро пропусков"
}
path = os.path.join(inbox, "call_{}_{}.json".format(time.strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:8]))
with open(path, "wb") as f:
    f.write(json.dumps(item, ensure_ascii=False, indent=2).encode("utf-8"))
print(path)
