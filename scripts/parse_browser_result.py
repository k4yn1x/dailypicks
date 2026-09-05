"""Parse a saved Claude_Browser javascript_tool result (JSON-encoded string) into a JSON file."""
import json, sys
arr = json.load(open(sys.argv[1])); txt = arr[0]["text"]
if "\n\n(captured" in txt: txt = txt.split("\n\n(captured")[0]
if "\n\nTab Context" in txt: txt = txt.split("\n\nTab Context")[0]
inner = json.loads(txt)          # the JS result is a JSON string of a JSON document
d = json.loads(inner) if isinstance(inner, str) else inner
json.dump(d, open(sys.argv[2], "w"))
for k, v in d.items(): print(k, len(v), v[0], '...', v[-1])
