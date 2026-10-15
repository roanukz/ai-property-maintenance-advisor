# CPSC product recall fixtures

Offline fixtures for `agent/product_recalls.py` (SC15). Tests never reach the
network; each fake fetch returns one of these files, and the three stored live
checks are read back through `latest_check`.

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

- The three `live_check_20261014_*.json` files are the first live product
  recall check (PRD 2 success criterion 3): run record
  recalls-b37be1f2a56b49fd in `data/runs/`, build `ad11e6e15a55d30a`, run on
  13 October 2026 at 22:21 US Eastern (02:21 UTC on 14 October). Each is the
  check `store_check` wrote for one registry appliance, copied byte for byte
  from `data/recalls/<appliance_id>/`: the appliance ID, the check version
  (2), whether the check applies (true), the maker, the model on record, the
  model number, checked_at (UTC) and, for each of the three requests, its
  field, parameters, URL, retrieved_at, error (none) and raw response body
  with that body's sha256. It is public CPSC data. A body is the text the
  service sent, decoded as UTF-8, so the sha256 of the body encoded as UTF-8
  is the sha256 of the bytes received; the store names each file by its UTC
  second, a sequence number and the first 8 hex digits of the file's sha256.
  None of the three names its model exactly: each reads back as "No CPSC
  product recall names this exact model", checked 2026-10-14 (UTC).
- `live_check_20261014_trane.json`: appl-xr16, maker "Trane", model on record
  "XR16 4TTR6036", model number "4TTR6036", from
  `20261014T022149Z-001-d9caffc8.json` (file sha256
  d9caffc82625f1a14ba9396bed14239aac0d6ac89c2aee4af9cd3770d04f3e36). 12
  distinct recalls, none listed under two roles; none names model number
  4TTR6036 or model on record XR16 4TTR6036. Recall 14737 lists Trane models
  such as "4TTM3036A1000A*", another series.
  - Manufacturer: 8 recalls, body sha256
    789153a8f4f5e985a1219eb8827f6071c7d0ca52c98e3676ed5589d90109a02a
  - Importer: 1 recall, body sha256
    2044ae15baa5e9dde89312497bdc4d6982007275b66aa0fc3571fa8cd83cc6c8
  - Distributor: 3 recalls, body sha256
    f617609a2526a127b5f178ff5ab61ea734e128e68156e432606d9f9ecaaa84ad
- `live_check_20261014_rheem.json`: appl-waterheater, maker "Rheem", model on
  record and model number "PRO+E50 M2 RH92 CL", from
  `20261014T022151Z-001-b48526ee.json` (file sha256
  b48526ee69b7e30c4b1d15fddd8ba2467737f69e0f0555402284aa1ad546c94e). 6
  distinct recalls: recall 16177 is listed under Manufacturer and
  Distributor, so the three bodies hold 7 between them. None names PRO+E50 M2
  RH92 CL.
  - Manufacturer: 5 recalls, body sha256
    3d18ad2f036fd20768cf5a51970ab1538e5e650491cc4d8e96152a0bbb9590d9
  - Importer: 1 recall, body sha256
    48f6eae2fd3ea52829aed8b87a061a75e8f8e880fed070284c5bf6bd9a88cd4d
  - Distributor: 1 recall, body sha256
    6ab621a1b94c80399883ff6a369d612d3b3230648685e83551451d12e32ca04d
- `live_check_20261014_sundance.json`: appl-optima880, maker "Sundance
  Spas", model on record "Optima 880", no model number, from
  `20261014T022148Z-001-1959255c.json` (file sha256
  1959255cfd3c43435ecf1e4837d2d0acaeef71328d0da68f4a5ce232a5b8f0ad). 1
  distinct recall, 23068, listed under Importer only; it prints "880" in the
  series list "680, 780, 880 and 980", which is not the model Optima 880, so
  it does not match. The two empty bodies are the same two bytes, `[]`.
  - Manufacturer: 0 recalls, body sha256
    4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945
  - Importer: 1 recall, body sha256
    e9cacfbe7f7d912f1d549222863eafd67a90d0536eb0cc20318dc0ad9f994999
  - Distributor: 0 recalls, body sha256
    4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945

The synthetic files are marked synthetic in their names and in every recall's
text, and their links point at example.com.
