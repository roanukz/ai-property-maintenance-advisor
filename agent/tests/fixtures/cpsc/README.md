# CPSC product recall fixtures

Offline fixtures for `agent/product_recalls.py` (SC15). Tests never reach the
network; each fake fetch returns one of these files.

- `live_17057.json`: a real response from CPSC's Recall Retrieval web service
  (https://www.saferproducts.gov/RestWebServices/Recall, format=json, no key),
  recorded on 13 October 2026 at 15:47:45 GMT (the response's Date header).
  It is public CPSC data, kept byte for byte (sha256
  68f11440306425457b2b6e0ff070bf2103b497206d18f62d2bf6148569d16d99). It holds
  one recall, 17057, whose firm is listed under Importers, with Manufacturers
  empty and Products[].Model empty; its model numbers appear only in its
  Description.
- `synthetic_matches.json`: invented recalls, not CPSC data. SYN001 names the
  Trane registry model "XR16 4TTR6036" in Products[0].Model; SYN002 names it
  only in its Description, as "XR16-4TTR6036"; SYN003 names the Rheem
  registry model "PRO+E50 M2 RH92 CL" in its Title and has a link that is not
  a web address.
- `synthetic_near_misses.json`: invented recalls, not CPSC data, that must
  never match a model on record: a suffix ("4TTR6036A") and a prefix
  ("4TTR603") of the model number, the family "XR16" alone, "4TTR6036" alone,
  "XR16, 4TTR6036" as a list, a sentence break between the family and the
  model number, and the Rheem series "PRO+E50" alone. Since each appliance
  record gained an exact model number (13 October 2026), SYN102, which prints
  "4TTR6036" alone, matches the Trane unit by its model number "4TTR6036"; it
  is a near miss only for a unit with no model number on record. SYN101 and
  SYN103 match no unit either way.

The synthetic files are marked synthetic in their names and in every recall's
text, and their links point at example.com.
